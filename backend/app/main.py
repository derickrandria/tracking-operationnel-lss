"""Point d'entrée — Plateforme de Tracking Opérationnel LSS.

FastAPI : API REST (/api), WebSocket (/ws) pour le temps réel (§9),
documentation OpenAPI/Swagger (/docs), service du frontend React (SPA).
"""

APP_VERSION = "1.45"   # visible au démarrage (fenêtre noire) et dans le bandeau latéral
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import CORS_ORIGINS, FRONTEND_DIST, SIM_ENABLE, SIM_TICK_S, now_local
from .database import SessionLocal
from . import daily, engine, event_bus, seed
from .security import decode_token

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s")
log = logging.getLogger("lss.main")


# ------------------------------------------------------------------ tâches de fond
async def _boucle_chien_de_garde():
    """Chien de garde périodique (GPS hors ligne, immobilisations, retards)."""
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(engine.boucle_surveillance)
        except Exception:
            log.exception("Erreur chien de garde")


async def _boucle_simulateur():
    """Tick live du simulateur (nouveaux événements GPS toutes les SIM_TICK_S)."""
    from .simulator import avancer_tous
    while True:
        await asyncio.sleep(SIM_TICK_S)
        try:
            def tick():
                db = SessionLocal()
                try:
                    avancer_tous(now_local(), db)
                finally:
                    db.close()
            await asyncio.to_thread(tick)
        except Exception:
            log.exception("Erreur tick simulateur")


async def _rejeu_puis_rien():
    """Rejeu de la journée en tâche de fond au démarrage (sans bloquer l'API)."""
    if not SIM_ENABLE:
        return
    from .simulator import rejeu_journee
    try:
        await asyncio.to_thread(rejeu_journee)
        event_bus.publish("data.refresh", {"raison": "rejeu_termine"})
        log.info("Rejeu terminé — plateforme peuplée")
    except Exception:
        log.exception("Échec du rejeu simulateur")


async def _boucle_collecteur_reel():
    """Collecte réelle MZoneX / CamtrackPro (§10) — activée par COLLECTOR_SOURCE.
    Les points insérés transitent par le même `ingest_event()` (§7) : missions,
    temps réglementaires, infractions, alertes et temps réel s'enchaînent tels quels."""
    from .scrapers import SOURCES, boucle_collecte
    source = os.getenv("COLLECTOR_SOURCE", "SIMULATEUR").upper()
    if source not in SOURCES and source != "MIXTE":
        return
    if SIM_ENABLE:
        log.warning("SIMULATEUR et COLLECTEUR %s actifs ensemble — "
                    "mettez SIM_ENABLE=0 pour la production réelle", source)
    log.info("Collecteur réel %s activé (période %ss)",
             source, os.getenv("COLLECTOR_PERIODE_S", "10"))
    await asyncio.to_thread(boucle_collecte)


# ------------------------------------------------------------------ migrations légères
def migrer_schema():
    """Migrations additives sans Alembic (base démo SQLite / prod PostgreSQL) :
    colonnes ajoutées par les addendums sur une base EXISTANTE. `create_all`
    (seed) suffit pour une base neuve ; ici on complète les bases déjà livrées."""
    from sqlalchemy import inspect, text
    from .database import engine as _engine
    from .seed import VEHICULES_CAMTRACKPRO
    insp = inspect(_engine)
    tables = set(insp.get_table_names())
    required_tables = {"trajets", "vehicules", "suivi_journalier", "infractions", "conducteurs"}
    if not required_tables.issubset(tables):
        return
    cols_t = {c["name"] for c in insp.get_columns("trajets")}
    cols_v = {c["name"] for c in insp.get_columns("vehicules")}
    with _engine.begin() as cx:
        # Addendum v1.4 §3.1 (table trajets)
        if "statut_source" not in cols_t:
            cx.execute(text("ALTER TABLE trajets ADD COLUMN statut_source VARCHAR(40)"))
            log.info("Migration : trajets.statut_source ajouté")
        if "source_plateforme" not in cols_t:
            cx.execute(text("ALTER TABLE trajets ADD COLUMN source_plateforme VARCHAR(20)"))
            log.info("Migration : trajets.source_plateforme ajouté")
        if "distance_km" not in cols_t:
            cx.execute(text("ALTER TABLE trajets ADD COLUMN distance_km FLOAT"))
            log.info("Migration : trajets.distance_km ajouté")
        # Addendum v1.2 §7.1 (flotte mixte MZoneX / CamtrackPro)
        if "plateforme_gps" not in cols_v:
            cx.execute(text("ALTER TABLE vehicules ADD COLUMN plateforme_gps VARCHAR(20)"))
            log.info("Migration : vehicules.plateforme_gps ajouté")
        # Addendum v1.5 §7.1 (validité métier des trajets — règle absolue §2)
        if "statut_validation" not in cols_t:
            cx.execute(text("ALTER TABLE trajets ADD COLUMN statut_validation VARCHAR(20)"))
            log.info("Migration : trajets.statut_validation ajouté")
        # §0septies (20/08/2026) — badge chauffeur + carnet de conduite
        for col, typ in (("conducteur_badge", "VARCHAR(160)"),
                         ("conducteur_badge_id", "VARCHAR(36)"),
                         ("badge_ecarte", "VARCHAR(160)"),
                         ("v_max", "FLOAT"), ("ralenti_s", "INTEGER"),
                         ("exc_vitesse", "INTEGER"), ("exc_freinage", "INTEGER"),
                         ("exc_accel", "INTEGER"), ("exc_ralenti", "INTEGER"),
                         ("exc_surregime", "INTEGER"), ("exc_autres", "INTEGER")):
            if col not in cols_t:
                cx.execute(text(f"ALTER TABLE trajets ADD COLUMN {col} {typ}"))
                log.info("Migration : trajets.%s ajouté (§0septies)", col)
        cols_s = {c["name"] for c in insp.get_columns("suivi_journalier")}
        if "conducteur_origine" not in cols_s:
            cx.execute(text("ALTER TABLE suivi_journalier ADD COLUMN conducteur_origine VARCHAR(10)"))
            log.info("Migration : suivi_journalier.conducteur_origine ajouté (§0septies)")
        # §0vicies decies N2 (31/08/2026) : relevés automatiques du soir
        if "position_20h" not in cols_s:
            cx.execute(text("ALTER TABLE suivi_journalier ADD COLUMN position_20h VARCHAR(200)"))
            log.info("Migration v1.44 : suivi_journalier.position_20h ajouté (§0vicies decies N2)")
        if "position_22h" not in cols_s:
            cx.execute(text("ALTER TABLE suivi_journalier ADD COLUMN position_22h VARCHAR(200)"))
            log.info("Migration v1.44 : suivi_journalier.position_22h ajouté (§0vicies decies N2)")
        # v3 AM-5 / C3 (22/08/2026) : vitre Infractions = lecture externe seule
        cols_i = {c["name"] for c in insp.get_columns("infractions")}
        if "exterieure" not in cols_i:
            cx.execute(text(
                "ALTER TABLE infractions ADD COLUMN exterieure BOOLEAN DEFAULT 0"))
            log.info("Migration : infractions.exterieure ajouté (§0nonies AM-5)")
        cols_i = {c["name"] for c in insp.get_columns("infractions")}
        # §0quinquies decies I1→I4 (25/08/2026) — source Ym@ne + validation
        # + I5 exécution (26/08/2026, v1.36) : seuil_texte (verbatim du portail)
        for col, typ in (("niveau", "VARCHAR(20)"), ("nom_ymane", "VARCHAR(160)"),
                         ("chauffeur_brut", "VARCHAR(160)"), ("ymane_id", "VARCHAR(80)"),
                         ("seuil_unite", "VARCHAR(10)"), ("seuil_texte", "VARCHAR(80)"),
                         ("validation", "VARCHAR(12) DEFAULT 'NON_TRAITEE'"),
                         ("observation", "VARCHAR(500)"), ("validee_par", "VARCHAR(80)"),
                         ("validee_le", "DATETIME")):
            if col not in cols_i:
                cx.execute(text(f"ALTER TABLE infractions ADD COLUMN {col} {typ}"))
                log.info("Migration v1.35/1.36 : infractions.%s ajouté (§0quinquies decies)", col)
        # l'ALTER ci-dessus ne crée pas l'index du modèle : le poser aussi —
        # idempotent (utile sur les bases héritées v1.34)
        cx.execute(text("CREATE INDEX IF NOT EXISTS ix_infractions_ymane_id "
                        "ON infractions (ymane_id)"))
        # §0octies decies L2 (27/08/2026, v1.41) — période de l'infraction
        # (enddatetime verbatim Ym@ne) ; les lignes existantes sont
        # rattrapées seules par l'upsert I5 à la prochaine relecture J-8→J.
        cols_i = {c["name"] for c in insp.get_columns("infractions")}
        for col, typ in (("date_fin", "DATE"), ("heure_fin", "TIME")):
            if col not in cols_i:
                cx.execute(text(f"ALTER TABLE infractions ADD COLUMN {col} {typ}"))
                log.info("Migration v1.41 : infractions.%s ajouté (§0octies decies L2)", col)
        # §0sexies decies J1 (27/08/2026) : forme canonique anti-doublon
        # chauffeur. L'index UNIQUE est volontairement posé PLUS TARD, par la
        # réparation v1.38 (§J3), une fois les doublons hérités résorbés.
        cols_c_raw = {c["name"]: c for c in insp.get_columns("conducteurs")}
        cols_c = set(cols_c_raw.keys())
        if "nom_normalise" not in cols_c:
            cx.execute(text(
                "ALTER TABLE conducteurs ADD COLUMN nom_normalise VARCHAR(170)"))
            log.info("Migration v1.38 : conducteurs.nom_normalise ajouté "
                     "(§0sexies decies J1)")
        cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteurs_nom_normalise "
                        "ON conducteurs (nom_normalise)"))
        if "tokens_set" not in cols_c:
            cx.execute(text(
                "ALTER TABLE conducteurs ADD COLUMN tokens_set VARCHAR(170)"))
            log.info("Migration : conducteurs.tokens_set ajouté")
        cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteurs_tokens_set "
                        "ON conducteurs (tokens_set)"))
        if "code_badge_mzonex" not in cols_c:
            cx.execute(text(
                "ALTER TABLE conducteurs ADD COLUMN code_badge_mzonex INTEGER"))
            log.info("Migration : conducteurs.code_badge_mzonex ajouté")
        cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteurs_code_badge_mzonex "
                        "ON conducteurs (code_badge_mzonex)"))

        # Rendre conducteurs.matricule nullable sous SQLite si créé avec contrainte NOT NULL héritée
        if cols_c_raw.get("matricule", {}).get("nullable") is False:
            try:
                cx.execute(text("PRAGMA foreign_keys=OFF"))
                cx.execute(text("""
                    CREATE TABLE IF NOT EXISTS conducteurs_migr_tmp (
                        id VARCHAR(36) PRIMARY KEY,
                        nom_prenom VARCHAR(160) NOT NULL,
                        code_badge_mzonex INTEGER,
                        prenom_usuel VARCHAR(60) NOT NULL,
                        matricule VARCHAR(40),
                        telephone VARCHAR(40),
                        statut VARCHAR(30) DEFAULT 'ACTIF',
                        date_creation DATETIME,
                        nom_normalise VARCHAR(170),
                        tokens_set VARCHAR(170)
                    )
                """))
                champs_sel = [
                    "id", "nom_prenom",
                    "code_badge_mzonex" if "code_badge_mzonex" in cols_c else "NULL AS code_badge_mzonex",
                    "prenom_usuel",
                    "CASE WHEN matricule LIKE 'CH%' OR matricule LIKE 'AUTO-%' THEN NULL ELSE matricule END AS matricule",
                    "telephone" if "telephone" in cols_c else "NULL AS telephone",
                    "statut" if "statut" in cols_c else "'ACTIF' AS statut",
                    "date_creation" if "date_creation" in cols_c else "CURRENT_TIMESTAMP AS date_creation",
                    "nom_normalise" if "nom_normalise" in cols_c else "NULL AS nom_normalise",
                    "tokens_set" if "tokens_set" in cols_c else "NULL AS tokens_set"
                ]
                cx.execute(text(f"""
                    INSERT INTO conducteurs_migr_tmp (id, nom_prenom, code_badge_mzonex, prenom_usuel, matricule, telephone, statut, date_creation, nom_normalise, tokens_set)
                    SELECT {', '.join(champs_sel)} FROM conducteurs
                """))
                cx.execute(text("DROP TABLE conducteurs"))
                cx.execute(text("ALTER TABLE conducteurs_migr_tmp RENAME TO conducteurs"))
                cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteurs_nom_prenom ON conducteurs (nom_prenom)"))
                cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteurs_prenom_usuel ON conducteurs (prenom_usuel)"))
                cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteurs_matricule ON conducteurs (matricule)"))
                cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteurs_nom_normalise ON conducteurs (nom_normalise)"))
                cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteurs_tokens_set ON conducteurs (tokens_set)"))
                cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteurs_code_badge_mzonex ON conducteurs (code_badge_mzonex)"))
                cx.execute(text("PRAGMA foreign_keys=ON"))
                log.info("Migration SQLite : conducteurs.matricule converti en NULLABLE avec succès")
            except Exception as e:
                log.warning("Impossible de convertir conducteurs.matricule en nullable : %s", e)

        # Table conducteur_aliases
        cx.execute(text("""
            CREATE TABLE IF NOT EXISTS conducteur_aliases (
                id VARCHAR(36) PRIMARY KEY,
                conducteur_id VARCHAR(36) NOT NULL REFERENCES conducteurs(id) ON DELETE CASCADE,
                alias_brut VARCHAR(160) NOT NULL,
                alias_normalise VARCHAR(170) NOT NULL UNIQUE,
                source VARCHAR(30) DEFAULT 'MANUEL',
                date_creation DATETIME
            )
        """))
        cx.execute(text("CREATE INDEX IF NOT EXISTS ix_conducteur_aliases_conducteur_id "
                        "ON conducteur_aliases (conducteur_id)"))
        cx.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_conducteur_aliases_normalise "
                        "ON conducteur_aliases (alias_normalise)"))
        # Purge des préfixes hérités 'CH...' ou 'AUTO-...' : seuls les driverKeyCode MZoneX sont conservés
        try:
            cx.execute(text("UPDATE conducteurs SET matricule = NULL WHERE matricule LIKE 'CH%' OR matricule LIKE 'AUTO-%'"))
            cx.execute(text("UPDATE conducteurs SET matricule = CAST(code_badge_mzonex AS TEXT) WHERE code_badge_mzonex IS NOT NULL AND (matricule IS NULL OR matricule = '')"))
        except Exception as e:
            log.warning("Avertissement purge matricules: %s", e)
        # §0decies (24/08/2026) : marqueur segment B d'un trajet franchissant
        # minuit (revérification portail sans faux « sans source »)
        if "suite_minuit" not in cols_t:
            cx.execute(text(
                "ALTER TABLE trajets ADD COLUMN suite_minuit BOOLEAN DEFAULT 0"))
            log.info("Migration : trajets.suite_minuit ajouté (§0decies)")
        # --- rattrapage des valeurs (trajets déjà enregistrés = validés) ---
        # NB : les enums SQLAlchemy stockent le NOM du membre (« VALIDE »),
        # la valeur accentuée « VALIDÉ » n'existant qu'à l'affichage (API).
        cx.execute(text("UPDATE trajets SET statut_source='VALIDE' WHERE statut_source IS NULL"))
        cx.execute(text("UPDATE vehicules SET plateforme_gps='MZONEX' WHERE plateforme_gps IS NULL"))
        # --- rattrapage Addendum v1.5 : l'existant (déjà comptabilisé) reste
        # VALIDE ; les provisoires en vol passent EN_ATTENTE
        cx.execute(text("UPDATE trajets SET statut_validation='EN_ATTENTE' "
                        "WHERE statut_validation IS NULL AND statut_source='PROVISOIRE'"))
        cx.execute(text("UPDATE trajets SET statut_validation='VALIDE' "
                        "WHERE statut_validation IS NULL"))
        # seuil pause validante : 15 min (ancien défaut v1.0-v1.6) → 20 min
        # (Addendum v1.5 §6) — seulement si la valeur n'a jamais été retouchée
        cx.execute(text("UPDATE parametrage_seuils SET valeur=1200 "
                        "WHERE cle='DUREE_MIN_PAUSE_VALIDE' AND valeur=900"))
        # v1.19 / arbitrage LSS du 14/08/2026 : vitesse d'arrêt 5 → 3 km/h
        # (embouteillages : un camion avançant à 5-11 km/h reste « en route »).
        # Appliqué seulement si la valeur est encore l'ancien défaut (5).
        cur = cx.execute(text("UPDATE parametrage_seuils SET valeur=3 "
                              "WHERE cle='SEUIL_VITESSE_ARRET' AND valeur=5"))
        if cur.rowcount:
            log.info("Migration v1.19 : SEUIL_VITESSE_ARRET 5 → 3 km/h "
                     "(arbitrage LSS du 14/08/2026)")
        cx.execute(text(
            "UPDATE trajets SET source_plateforme = ("
            "  SELECT v.plateforme_gps FROM vehicules v"
            "  JOIN suivi_journalier s ON s.vehicule_id = v.id"
            "  WHERE s.id = trajets.suivi_id) "
            "WHERE source_plateforme IS NULL"))
        if VEHICULES_CAMTRACKPRO:
            plaques = ",".join(f"'{p}'" for p in sorted(VEHICULES_CAMTRACKPRO))
            cx.execute(text(
                f"UPDATE vehicules SET plateforme_gps='CAMTRACKPRO' WHERE plaque IN ({plaques})"))
            cx.execute(text(
                "UPDATE trajets SET source_plateforme='CAMTRACKPRO' "
                "WHERE source_plateforme IS NULL AND suivi_id IN ("
                "  SELECT id FROM suivi_journalier WHERE vehicule_id IN ("
                f"    SELECT id FROM vehicules WHERE plaque IN ({plaques})))"))


def reparer_identifiants():
    """§0quater D0 (arbitrage 14/08/2026) — normalisation des identifiants en
    base : « 8076 TCB » / « 8076 tcb » → « 8076TCB » pour plaque ET boîtier
    (sinon la fiche ne correspond jamais aux relevés). En cas de collision
    (deux fiches tendraient vers la même plaque), la seconde est conservée
    telle quelle et une alerte ⓘ prévient l'administrateur."""
    from .config import normaliser_saisie
    from .database import SessionLocal
    from .engine import creer_alerte
    from .models import GraviteAlerte, TypeAlerte, Vehicule
    from sqlalchemy import select as _sel
    db = SessionLocal()
    try:
        vehicules = db.scalars(_sel(Vehicule)).all()
        prises = {v.plaque for v in vehicules}
        corrigees = 0
        for v in vehicules:
            cible = normaliser_saisie(v.plaque)
            if cible and cible != v.plaque:
                if cible in prises:
                    creer_alerte(
                        db, TypeAlerte.NOUVEAU_VEHICULE,
                        GraviteAlerte.INFORMATION,
                        f"Normalisation D0 : véhicule « {v.plaque} » "
                        f"deviendrait « {cible} » qui existe déjà — fiche "
                        f"non touchée, à régler à la main.",
                        vehicule_id=v.id, lien_module="/vehicules")
                    log.warning("§0quater D0 : collision %r → %r — fiche "
                                "non normalisée (doublon)", v.plaque, cible)
                    continue
                log.info("§0quater D0 : plaque %r → %r", v.plaque, cible)
                prises.discard(v.plaque)
                prises.add(cible)
                v.plaque = cible
                corrigees += 1
            if v.gps_associe:
                # D0 : espaces + casse seulement — le tiret du boîtier est
                # conservé (« OBC-8076 TCB » → « OBC-8076TCB »)
                gps = normaliser_saisie(v.gps_associe)
                if gps and gps != v.gps_associe:
                    log.info("§0quater D0 : boîtier %r → %r (%s)",
                             v.gps_associe, gps, v.plaque)
                    v.gps_associe = gps
                    corrigees += 1
        if corrigees:
            db.commit()
            log.info("§0quater D0 : %d identifiant(s) véhicule normalisé(s)",
                     corrigees)
    except Exception:
        db.rollback()
        log.exception("§0quater D0 — échec normalisation identifiants")
    finally:
        db.close()


def reparer_conducteurs_non_personnes():
    """§0quinquies D5 (arbitrage 14/08/2026) — une fois au démarrage :
    conducteurs déjà créés (ex. « Garage LSS 2 » par D2 v1.21) dont un mot
    est désormais bloqué → fiche passée INACTIVE + alerte ⓘ explicative.
    Réactivation manuelle possible dans l'onglet Chauffeurs si c'était bien
    une personne. Jamais de suppression (traçabilité)."""
    from .config import mots_ignores_conducteur
    from .database import SessionLocal
    from .engine import creer_alerte
    from .models import (AuditLog, Conducteur, GraviteAlerte,
                         StatutConducteur, TypeAlerte)
    from sqlalchemy import select as _sel
    mots = set(mots_ignores_conducteur())
    db = SessionLocal()
    try:
        corriges = 0
        for c in db.scalars(_sel(Conducteur).where(
                Conducteur.statut == StatutConducteur.ACTIF)).all():
            if not (set((c.nom_prenom or "").lower().split()) & mots):
                continue
            c.statut = StatutConducteur.INACTIF
            creer_alerte(
                db, TypeAlerte.NOUVEAU_CONDUCTEUR,
                GraviteAlerte.INFORMATION,
                f"Fiche chauffeur « {c.nom_prenom} » désactivée : le portail "
                "avait écrit un lieu (garage, dépôt…) dans la colonne "
                "chauffeur. Réactivez-la dans l'onglet Chauffeurs si c'est "
                "bien une personne.",
                conducteur_id=c.id, lien_module="/conducteurs")
            db.add(AuditLog(username="systeme",
                            action="conducteur.desactive_non_personne",
                            entite="conducteur", entite_id=c.id,
                            details={
                                "nom_prenom": c.nom_prenom,
                                "regle": "§0quinquies D5 (14/08/2026) : mots "
                                         "non-personnes ignorés"}))
            corriges += 1
            log.info("§0quinquies D5 : chauffeur non-personne « %s » "
                     "désactivé", c.nom_prenom)
        if corriges:
            db.commit()
    except Exception:
        db.rollback()
        log.exception("§0quinquies D5 — échec désactivation non-personnes")
    finally:
        db.close()



async def _boucle_reconciliation_reelle():
    """Addendum v1.4 §2.3 — synchronisation Niveau 2 (onglet Trajets MZoneX /
    rapport CamtrackPro) en production réelle, à la fréquence paramétrable
    FREQUENCE_SYNC_TRAJETS_VALIDES (éditable dans Paramètres, §7.4)."""
    from . import scrapers
    source = os.getenv("COLLECTOR_SOURCE", "SIMULATEUR").upper()
    if SIM_ENABLE or (source not in scrapers.VALIDATEURS_TRAJETS and source != "MIXTE"):
        return
    log.info("Synchronisation Niveau 2 activée pour %s", source)
    while True:
        try:
            # synchronisation immédiate au démarrage (la journée du portail
            # est rechargée telle quelle — idempotent grâce à l'anti-doublon),
            # puis à la fréquence FREQUENCE_SYNC_TRAJETS_VALIDES (Paramètres)
            await asyncio.to_thread(scrapers.synchroniser_trajets_valides, source)
        except Exception:
            log.exception("Échec synchronisation Niveau 2 (%s)", source)
        frequence = 900.0
        try:
            db = SessionLocal()
            try:
                frequence = float(engine.get_seuils(db).get(
                    "FREQUENCE_SYNC_TRAJETS_VALIDES", 900))
            finally:
                db.close()
        except Exception:
            pass
        await asyncio.sleep(max(60.0, frequence))


async def _am4_puis_reparation_v130():
    """Chaîne de fond du démarrage (dans cette order) :
    1. v3 AM-4 — catch-up des jours non consolidés (données réelles portails) ;
    2. §0decies D1/D3 (24/08/2026) — réparation embarquée des journées abîmées
       par l'ancienne bascule 01h00 : contrôle 19/08→veille, réparation des
       seules journées à écart, une fois, avec reprise au boot suivant ;
    3. §0undecies E5 (24/08/2026) — rattrapage UNIQUE de la colonne J-1 du
       jour courant (libellé de la dernière position GPS de la veille)."""
    from . import reparation
    try:
        await asyncio.to_thread(daily.rattraper_consolidation)
    except Exception:
        log.exception("AM-4 : échec (la réparation v1.30 tente quand même)")
    try:
        await asyncio.to_thread(reparation.executer_reparation_v130)
    except Exception:
        log.exception("§0decies : réparation v1.30 en échec — "
                      "reprise au prochain démarrage")
    try:
        await asyncio.to_thread(_rattrapage_j1)
    except Exception:
        log.exception("§0undecies E5 : rattrapage colonne J-1 en échec — "
                      "reprise au prochain démarrage")
    try:
        await asyncio.to_thread(reparation.executer_reparation_v132)
    except Exception:
        log.exception("§0duodecies F3 : réparation anti-doublons v1.32 en "
                      "échec — reprise au prochain démarrage")
    try:
        await asyncio.to_thread(reparation.reparer_conducteurs_v138)
    except Exception:
        log.exception("§0sexies decies J1-J4 : réparation dédoublonnage "
                      "chauffeurs v1.38 en échec — reprise au prochain "
                      "démarrage")
    try:
        await asyncio.to_thread(reparation.reparer_historique_conducteurs_passes)
    except Exception:
        log.exception("Réparation historique conducteurs passés en échec")
    try:
        # v146 — garde d'intégrité des heures de fin (fin < début → « en
        # cours » / jumeau REJETÉ). Idempotente, exécutée à CHAQUE démarrage.
        await asyncio.to_thread(reparation.reparer_fins_incoherentes)
    except Exception:
        log.exception("v146 : réparation fins incohérentes en échec — reprise "
                      "au prochain démarrage")


def _rattrapage_j1():
    db = SessionLocal()
    try:
        engine.rattrapage_position_j1_v131(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Démarrage — initialisation base + seed")
    seed.seed_si_vide()
    migrer_schema()
    try:    # §0octies C1 (20/08/2026) — correctif UNIQUE +3h des trajets N2
        db_c = SessionLocal()   # CamtrackPro stockés en UTC par la v1.26
        try:
            engine.corriger_fuseau_camtrackpro(db_c)
        finally:
            db_c.close()
    except Exception:
        log.exception("§0octies C1 : correctif fuseau CamtrackPro en échec — "
                      "aucune ligne touchée, nouvelle tentative au prochain "
                      "démarrage")
    reparer_identifiants()
    reparer_conducteurs_non_personnes()    # §0quinquies D5 (14/08/2026)
    try:
        from . import reparation
        reparation.migrer_schema_missions()
        reparation.reparer_historique_conducteurs_passes()
    except Exception:
        log.exception("Réparation initiale historique / missions en échec")
    daily.rattraper_au_demarrage()
    event_bus.attacher_boucle(asyncio.get_running_loop())

    taches = [
        asyncio.create_task(daily.boucle_cycle_quotidien()),
        asyncio.create_task(_boucle_chien_de_garde()),
        asyncio.create_task(_rejeu_puis_rien()),
        asyncio.create_task(_boucle_collecteur_reel()),  # §10 — si COLLECTOR_SOURCE actif
        asyncio.create_task(_boucle_reconciliation_reelle()),  # Addendum v1.4 §2.3
        # v3 AM-4 (22/08/2026) — catch-up consolidation de TOUS les jours
        # manqués au démarrage (données réelles portails, idempotent), PUIS
        # §0decies D1/D3 (24/08/2026) — réparation embarquée des journées
        # abîmées par l'ancienne bascule 01h00 (v1.30, une seule fois)
        asyncio.create_task(_am4_puis_reparation_v130()),
    ]
    if SIM_ENABLE:
        # Addendum v1.4 (démo) : la « validation retardée » du simulateur imite
        # l'onglet Trajets de MZoneX — branchée ici pour laisser le moteur neutre.
        from .simulator import planifier_validation_simulee
        if planifier_validation_simulee not in engine.HOOKS_TRAJET_CLOTURE:
            engine.HOOKS_TRAJET_CLOTURE.append(planifier_validation_simulee)
        taches.append(asyncio.create_task(_boucle_simulateur()))
        log.warning("★★ MODE DÉMONSTRATION : le SIMULATEUR fabrique les données "
                    "(fictives). Pour les données RÉELLES des portails : mettre "
                    "SIM_ENABLE=0 + COLLECTOR_SOURCE=MIXTE dans backend\\.env ★★")
    else:
        log.info("★★ MODE DONNÉES RÉELLES (SIM_ENABLE=0) — collecteur %s ★★",
                 os.getenv("COLLECTOR_SOURCE", "SIMULATEUR").upper())
    log.info("LSS Tracking v%s — Plateforme prête — %s", APP_VERSION, now_local())
    yield
    for t in taches:
        t.cancel()


app = FastAPI(title="Tracking Opérationnel LSS",
              description="API interne — suivi de flotte pétrolière (§4). "
                          "Documentation interactive : /docs",
              version="1.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ------------------------------------------------------------------ routeurs API
from .routers import admin, auth, conduite, dashboard, historique, operations, referentiels, surveillance, temps_conduite

for r in (auth.router, referentiels.router, operations.router, surveillance.router,
          dashboard.router, historique.router, admin.router, conduite.router,
          temps_conduite.router):
    app.include_router(r)


@app.get("/api/sante")
def sante():
    return {"statut": "OK", "heure_serveur": now_local().isoformat(),
            "version": APP_VERSION}


# ------------------------------------------------------------------ WebSocket (§9)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if not decode_token(token):
        await websocket.close(code=4401)
        return
    await event_bus.manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive (pings clients ignorés)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        event_bus.manager.disconnect(websocket)


# ------------------------------------------------------------------ SPA frontend
class SPAStaticFiles(StaticFiles):
    """Sert le build React et renvoie index.html pour les routes inconnues hors API."""
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404 and not path.startswith("api/") and not path.startswith("api"):
                return await super().get_response("index.html", scope)
            raise


if os.path.isdir(FRONTEND_DIST):
    app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
else:
    @app.get("/")
    def racine():
        return JSONResponse({"message": "Frontend non compilé — voir frontend/ (npm run build)",
                             "api": "/docs"})
