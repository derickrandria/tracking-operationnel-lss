"""Moteur de calcul réglementaire (§7) — module transverse.

Déclenché à chaque nouvel événement GPS (collecte §10). Il :
- détecte début de mouvement / arrêt / pause / reprise (§7.1) ;
- calcule TCC / TCJ / TTJ selon les formules réglementaires (§7.2) ;
- contrôle les seuils paramétrables et génère infractions + alertes (§6.4/6.5) ;
- reconstitue les missions par segmentation du cycle logistique (§6.3).

Aucune valeur seuil n'est codée en dur : tout vient de `ParametrageSeuil` (§5.8).
"""
import logging
import math
import os
from datetime import date, datetime, time, timedelta
from threading import RLock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import (CORRIDORS, IDENT_PLAQUE_RE, bascule_du, calculer_tokens_set,
                     est_libelle_service_ou_garage, jour_attribution, mots_ignores_badge,
                     mots_ignores_conducteur, normaliser_ident, normaliser_libelle, now_local,
                     plaque_depuis_libelle_portail)
from .database import SessionLocal
from .event_bus import publish
from .geozones import (DEPOT_OFFICIEL_CHARGEMENT, DEPOTS_DECHARGEMENT_CODES,
                       DEPOTS_OFFICIELS_DECHARGEMENT, DEPOTS_SUD_CODES,
                       detecter_zone_logistique, nom_officiel_depot,
                       normaliser_code_depot, extraire_depot_portail,
                       detecter_checkpoint_rn2)
from .models import (Alerte, AuditLog, Conducteur, ConducteurAlias, EvenementGPS,
                     GraviteAlerte, GraviteInfraction, HistoriqueJournalier, Infraction, Mission,
                     ParametrageSeuil, SourceEvenement, StatutAlerte,
                     StatutCamion, StatutConducteur, StatutMission, StatutValidationTrajet,
                     StatutVehicule, SuiviJournalier, Trajet, TypeAlerte,
                     TypeEvenement, TypeInfraction, Vehicule, uid)
from .serializers import iso, s_alerte, s_infraction, s_mission, s_suivi

log = logging.getLogger("lss.engine")

# Publication WebSocket désactivée pendant les rejeux massifs (simulateur).
PUBLISH_ENABLED = {"on": True}

# Hooks appelés à chaque CLÔTURE d'un trajet (arrêt détecté, §7.1) —
# Addendum v1.4 §2.3 : le simulateur y branche sa « validation Niveau 2
# retardée » qui imite l'onglet Trajets de MZoneX (nul en production réelle :
# la validation vient alors du connecteur scrapers.py onglet Trajets).
HOOKS_TRAJET_CLOTURE: list = []

# Drapeaux mémoire anti-doublons (rechargements contrôlés par garde DB).
_lock = RLock()
_flags: dict = {}

SEUILS_DEFAUT = {
    "SEUIL_TCC_MAX": (16200, "DUREE_S", "Temps de conduite continue maximal avant infraction (4h30)"),
    "SEUIL_TCJ_MAX": (36000, "DUREE_S", "Temps de conduite journalière maximal (10h)"),
    "SEUIL_TTJ_MAX": (43200, "DUREE_S", "Temps de travail journalier maximal (12h)"),
    "DUREE_MIN_PAUSE_VALIDE": (1200, "DUREE_S", "Durée minimale d'une pause validante — 20 min (v3 : ne pilote plus que la couleur noir/orange des lignes — AM-2)"),
    # v3 AM-3/C1 (arbitrage LSS 22/08/2026) — consolidation quotidienne
    "HEURE_PRE_CONSOLIDATION": (86399, "DUREE_S", "Instant de consolidation quotidienne, en secondes depuis minuit — 23:59:59 (v3 AM-3 : split des trajets en cours à minuit)"),
    # --- Addendum v1.9 §1.1/§2.2 — le TCC ne coupe QU'à partir de 30 min ---
    "SEUIL_PAUSE_COUPURE_TCC": (1800, "DUREE_S",
        "Pause qui COUPE le TCC (30 min — Addendum v1.9 : un arrêt 20-29 min est "
        "une pause valide déduite du TCJ mais ne coupe PAS le TCC ; < 20 min : ignorée)"),
    # --- §0tricies decies G1 (arbitrage LSS 25/08/2026) — AFFICHAGE seul ---
    "SEUIL_FUSION_AFFICHAGE_S": (1800, "DUREE_S",
        "Seuil d'affichage : fusion des lignes de trajet (30 min — §0tricies decies "
        "G1, arbitrage LSS du 25/08/2026, E3 abrogée : arrêt < 30 min → UNE ligne "
        "affichée, pas de case pause ; ≥ 30 min → deux lignes + pause affichée. "
        "AFFICHAGE SEUL : les compteurs et le moteur restent pilotés par "
        "DUREE_MIN_PAUSE_VALIDE et SEUIL_PAUSE_COUPURE_TCC)"),
    "SEUIL_BRUIT_GPS": (120, "DUREE_S", "Durée minimale d'un arrêt pris en compte (anti-bruit GPS)"),
    "SEUIL_VITESSE_MAX": (90, "NOMBRE", "Vitesse maximale autorisée (km/h)"),
    "SEUIL_IMMOBILISATION_ALERTE": (3600, "DUREE_S", "Immobilisation anormale avant alerte"),
    "SEUIL_GPS_HORS_LIGNE": (1800, "DUREE_S", "Absence de remontée GPS avant alerte « hors ligne »"),
    # --- Référence v2 §2/§5/§6/§12.1 — conditions de validité des trajets
    # (règle absolue §2 : un trajet < 0,3 km n'entre JAMAIS dans TCC/TCJ/TTJ)
    "SEUIL_DISTANCE_MIN_TRAJET_KM": (0.3, "NOMBRE",
        "Distance minimale d'un trajet valide (0,3 km — en dessous : manœuvre IGNORÉE, Référence v2 §7)"),
    "SEUIL_DUREE_MIN_MOUVEMENT_TRAJET": (900, "DUREE_S",
        "Durée minimale « en mouvement » (15 min — Référence v2 §6.1, RÉSERVÉ "
        "aux flux de positions ; INACTIF sur les trajets MZoneX rapport et "
        "CamtrackPro depuis l'arbitrage du 06/08/2026 : distance seule, §11.2)"),
    "SEUIL_VITESSE_ARRET": (3, "NOMBRE",
        "Vitesse d'arrêt (km/h) — Référence v2 §5.1/§12.1 + arbitrage LSS "
        "du 14/08/2026 : le véhicule est ARRÊTÉ dès que vitesse ≤ 3 km/h, "
        "il ROULE au-dessus (embouteillages : 5-11 km/h = en route ; "
        "avant le 14/08 : 5 km/h)"),
    "SEUIL_TCC_PREALERTE": (14400, "DUREE_S",
        "Pré-alerte « pause non prise » (4h00 — arbitrage LSS §0quater O1 "
        "du 14/08/2026 : anticipation du TCC ; avant : 85 % du seuil TCC, "
        "soit 3h49:30)"),
    "DUREE_MISSION_PREVUE": (32400, "DUREE_S", "Durée prévisionnelle standard d'une mission (au-delà : retardée)"),
    "DISTANCE_HORS_ITINERAIRE": (20, "NOMBRE", "Distance max au corridor logistique avant alerte (km)"),
    # --- Addendum v1.4 §2.5 — stratégie hybride MZoneX (Événements + Trajets) ---
    "FREQUENCE_SYNC_TRAJETS_VALIDES": (900, "DUREE_S",
        "Fréquence d'interrogation de l'onglet Trajets MZoneX pour la validation Niveau 2 (15 min)"),
    "SEUIL_TOLERANCE_RAPPROCHEMENT_TRAJET": (120, "DUREE_S",
        "Tolérance d'écart horaire pour rapprocher un trajet validé de son équivalent provisoire (±2 min)"),
    "SEUIL_DIVERGENCE_TRAJET": (600, "DUREE_S",
        "Écart anormal provisoire ↔ validé audité sans bloquer le remplacement (10 min)"),
    "FENETRE_RECONCILIATION_APRES_MINUIT": (7200, "DUREE_S",
        "Fenêtre après minuit durant laquelle un trajet de la veille archivée peut encore être validé (2h)"),
    # --- §0septies (arbitrages LSS du 20/08/2026) — conduite en direct ---
    "CONDUITE_VITESSE_HORS_ZONE": (45, "VITESSE_KMH",
        "Alerte vitesse en direct : seuil HORS géozone confirmé sur 2 signaux "
        "(arbitrage LSS §0septies B4 du 20/08/2026 ; en géozone, les seuils "
        "des portails gouvernent via le carnet de conduite)"),
    "CONDUITE_BADGE_FENETRE_S": (600, "DUREE_S",
        "Alerte « roule sans badge » (MZoneX) : véhicule > 3 km/h sans clé "
        "chauffeur vue depuis plus de cette fenêtre (§0septies B5)"),
    # --- Module Temps de Conduite (TCH) ---
    "SEUIL_TCH_ALERTE": (165600, "DUREE_S",
        "Seuil d'alerte TCH proche de la limite — avertissement (46h)"),
    "SEUIL_TCH_MAX": (201600, "DUREE_S",
        "Temps de conduite hebdomadaire maximal (56h)"),
    # --- Module Missions & Cycles Logistiques (Règle 1) ---
    "SEUIL_DUREE_DEPART_RN2": (3600, "DUREE_S",
        "Durée minimale de conduite sur RN2 hors Base Tana pour valider le début effectif de mission (1h00 — Règle 1)"),
}

_cache_seuils: dict = {"valeurs": None, "charge_le": None}


def get_seuils(db) -> dict:
    """Lit les seuils paramétrés (cache 30 s, invalidable à l'édition §7.4)."""
    now = now_local()
    if _cache_seuils["valeurs"] and (now - _cache_seuils["charge_le"]).total_seconds() < 30:
        return _cache_seuils["valeurs"]
    valeurs = {cle: v[0] for cle, v in SEUILS_DEFAUT.items()}
    for row in db.scalars(select(ParametrageSeuil)):
        valeurs[row.cle] = row.valeur
    _cache_seuils.update(valeurs=valeurs, charge_le=now)
    return valeurs


def invalider_cache_seuils():
    _cache_seuils.update(valeurs=None, charge_le=None)


# ------------------------------------------------------------------ géo
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _dist_point_segment_km(plat, plon, a, b) -> float:
    """Distance point-segment en projection équirectangulaire locale (km)."""
    lat0 = math.radians((a[0] + b[0]) / 2 or 1e-9)
    kx = 111.32 * math.cos(lat0)
    ky = 110.574
    px, py = plon * kx, plat * ky
    ax, ay = a[1] * kx, a[0] * ky
    bx, by = b[1] * kx, b[0] * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def hors_corridor(lat: float, lon: float, seuil_km: float) -> bool:
    meilleur = float("inf")
    for points in CORRIDORS.values():
        for i in range(len(points) - 1):
            meilleur = min(meilleur, _dist_point_segment_km(lat, lon, points[i], points[i + 1]))
        d = haversine_km(lat, lon, points[-1][0], points[-1][1])
        meilleur = min(meilleur, d)
    return meilleur > seuil_km


# ------------------------------------------------------------------ suivi
def libelle_derniere_position(db, vehicule_id: str, jour: date) -> str | None:
    """§0undecies E4/E5 — libellé de la DERNIÈRE position GPS connue avant
    `jour` 00:00 (colonne J-1 / rattrapage) : géozones des portails, sinon
    coordonnées (jamais de cellule vide tant qu'un événement existe)."""
    from .geozones import libelle_position
    ev = db.scalar(select(EvenementGPS).where(
        EvenementGPS.vehicule_id == vehicule_id,
        EvenementGPS.horodatage < datetime(jour.year, jour.month, jour.day),
        EvenementGPS.latitude.isnot(None)).order_by(
            EvenementGPS.horodatage.desc()))
    if not ev:
        return None
    return libelle_position(ev.latitude, ev.longitude) \
        or f"{ev.latitude:.4f}, {ev.longitude:.4f}"


def ensure_suivi(db, vehicule: Vehicule | str, jour: date) -> SuiviJournalier:
    """Retourne la ligne du jour (créée si besoin).

    À la création (§8) : Partie A synchronisée du référentiel, Partie B
    recopiée de la veille (aucune ressaisie), emplacement J-1 repris de
    l'arrêt final précédent ; Parties C et D restent vides.
    """
    if isinstance(vehicule, str):
        vehicule = db.get(Vehicule, vehicule)
        if not vehicule:
            raise ValueError("Véhicule introuvable")

    s = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour, SuiviJournalier.vehicule_id == vehicule.id))
    if s:
        return s
    precedent = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == vehicule.id,
        SuiviJournalier.date_jour < jour).order_by(SuiviJournalier.date_jour.desc()))
    conducteur_id = (precedent.conducteur_id if precedent is not None
                     else vehicule.conducteur_actuel_id)
    s = SuiviJournalier(
        date_jour=jour,
        vehicule_id=vehicule.id,
        conducteur_id=conducteur_id,
        conducteur_origine=(precedent.conducteur_origine if precedent else None),
        # Partie B — report de la veille (§8.3)
        situation=precedent.situation if precedent else None,
        statut_camion=precedent.statut_camion if precedent else StatutCamion.LIBRE,
        depot_recepteur=precedent.depot_recepteur if precedent else None,
        distributeur=precedent.distributeur if precedent else None,
        produit=precedent.produit if precedent else None,
        numero_ot=precedent.numero_ot if precedent else None,
        emplacement_j_moins_1=(precedent.arret_final or "").split(" · ", 1)[-1] if precedent and precedent.arret_final else None,
    )
    # §0undecies E5 (24/08/2026) — la colonne J-1 est TOUJOURS remplie : si la
    # dérivation depuis `arret_final` est vide ou restée « position inconnue »
    # (ère des collecteurs API sans géocodage), on bascule sur le libellé E4
    # de la dernière position GPS connue de la veille.
    if (not s.emplacement_j_moins_1
            or s.emplacement_j_moins_1 == "position inconnue"):
        s.emplacement_j_moins_1 = (
            libelle_derniere_position(db, vehicule.id, jour)
            or s.emplacement_j_moins_1)
    db.add(s)
    db.flush()
    db.refresh(s, attribute_names=["trajets"])
    return s


def ensure_suivis_du_jour(db, jour: date | None = None):
    jour = jour or now_local().date()
    vehicules = db.scalars(select(Vehicule).where(Vehicule.statut == StatutVehicule.ACTIF)).all()
    for v in vehicules:
        ensure_suivi(db, v, jour)
    db.commit()


def rattrapage_position_j1_v131(db, jour: date | None = None) -> dict:
    """§0undecies E5 (24/08/2026) — UNE SEULE FOIS au premier démarrage v1.31 :
    remplit la colonne J-1 de la journée courante depuis la dernière position
    GPS de la veille (libellé E4), pour les lignes vides ou restées
    « position inconnue ». Marqueur d'audit global → idempotent, zéro
    manipulation (même esprit que §0decies D3)."""
    jour = jour or now_local().date()
    if db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "position_j1_v131.terminee")):
        return {"statut": "deja_faite", "corrigees": 0}
    lignes = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour)).all()
    corrigees, plaques = 0, []
    for s in lignes:
        if (s.emplacement_j_moins_1
                and s.emplacement_j_moins_1 != "position inconnue"):
            continue
        lib = libelle_derniere_position(db, s.vehicule_id, jour)
        if lib and lib != s.emplacement_j_moins_1:
            s.emplacement_j_moins_1 = lib
            corrigees += 1
            if s.vehicule:
                plaques.append(s.vehicule.plaque)
    db.add(AuditLog(
        username="position_j1_v131", action="position_j1_v131.terminee",
        entite="suivi", entite_id=None,
        details={"jour": jour.isoformat(), "corrigees": corrigees,
                 "regle": "§0undecies E5 (24/08/2026) : colonne J-1 toujours "
                          "remplie depuis la dernière position GPS de la "
                          "veille (géozones des portails)",
                 "plaques": plaques[:50]}))
    db.commit()
    log.info("§0undecies E5 — rattrapage colonne J-1 du %s : %d ligne(s) "
             "corrigée(s) %s", jour, corrigees, f"({', '.join(plaques[:8])}…)"
             if len(plaques) > 8 else f"({', '.join(plaques)})"
             if plaques else "")
    return {"statut": "faite", "corrigees": corrigees}


# ------------------------------------------------------------------ calculs
def _auditer_chevauchement_v132(db, suivi: SuiviJournalier, vehicule,
                                recouvrement_s: int, nb_lignes: int) -> None:
    """§0duodecies F1 (25/08/2026) — trace la détection d'un recouvrement de
    lignes (double comptage ÉVITÉ par la garde F1). Une seule ligne d'audit
    par (journée, véhicule, quantité arrondie à la minute) : le recalcul
    tourne à chaque événement — jamais de spam. Aucune mutation des trajets :
    F1 est une garde de mesure, les lignes restent telles quelles (F3 gère
    l'hygiène de l'existant)."""
    plaque = getattr(vehicule, "plaque", "?")
    motif = f'%"{suivi.date_jour.isoformat()}"%'
    deja = db.scalars(select(AuditLog).where(
        AuditLog.action == "trajet.chevauchement_detecte",
        AuditLog.details.like(motif),
        AuditLog.details.like(f'%{plaque}%'))).all()
    minutes = round(recouvrement_s / 60)
    for a in deja:
        if (a.details or {}).get("recouvrement_min") == minutes:
            return                                      # déjà tracé tel quel
    _auditer(db, "trajet.chevauchement_detecte", suivi.id, {
        "plaque": plaque, "jour": suivi.date_jour.isoformat(),
        "recouvrement_min": minutes, "nb_lignes": nb_lignes,
        "regle": "§0duodecies F1 (25/08/2026) : des lignes se recouvrent — "
                 "compteurs mesurés à l'UNION du temps, double comptage "
                 "évité (TCJ ≤ TTJ garanti) ; lignes conservées, F3 répare "
                 "l'existant au prochain démarrage"})


def recalculer_temps(db, suivi: SuiviJournalier, maintenant: datetime):
    """Compteurs réglementaires — v3 (AMÉLIORATIONS, arbitrages §0nonies du
    22/08/2026 ; écran = export = archive : même source `construire_journee`) :

    DÉPART = début du 1er trajet valide (AM-6 : une manœuvre ne fixe jamais
             le départ ; spans de manœuvres = arrêts comme les autres) ;
    TTJ    = fin_ref − départ (amplitude brute — règle T1) ;
    TCJ    = TTJ − Σ TOUS les arrêts, toute durée (AM-1) = Σ durées des
             lignes (la conduite où le véhicule ROULE) ;
    TCC    = CHRONO de session (§0quaterdecies H1/H2, 25/08/2026) : temps
             écoulé depuis le début de la session (arrêts < 30 min INCLUS —
             réalignement sur §1.1 « pause courte incluse ») ; session coupée
             par toute pause ≥ SEUIL_PAUSE_COUPURE_TCC (30 min ; une
             manœuvre ≥ 30 min coupe aussi, AM-6) ; 0 si le camion est
             actuellement dans une pause coupante ; arrêt court EN COURS :
             le chrono continue (H2) ;
             v1.46 : mini-manœuvres CUMULÉES ≥ 30 min dans la session
             → TCC = 0 (AM-6 étendu) ; ligne ouverte + camion arrêté
             ≥ 30 min → TCC = 0 (arbitrage LSS du 04/09/2026) ;
             R1 (v3) : le TCC traverse minuit — si la première ligne du jour
             est le segment B d'un split (début 00:00) et que la veille
             s'est close à 23:59:59 avec un TCC ouvert, la session s'amorce
             au TCC figé de la veille.
    """
    from .chaines import Segment, construire_journee
    from .serializers import etat_roulage

    seuils = get_seuils(db)
    pause_min = float(seuils["DUREE_MIN_PAUSE_VALIDE"])
    pause_tcc = float(seuils.get("SEUIL_PAUSE_COUPURE_TCC", 1800))
    seuil_km = float(seuils.get("SEUIL_DISTANCE_MIN_TRAJET_KM", 0.3))
    # requête EXPLICITE (session longue, expire_on_commit=False : la collection
    # suivi.trajets peut être périmée — le calcul réglementaire exige l'état
    # courant de la base, y compris juste après fusions/rejets v1.5)
    tous = sorted(
        db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(Trajet.numero)).all(),
        key=lambda t: t.numero)
    if not tous:
        suivi.heure_depart = None
        suivi.tcc_s = suivi.tcj_s = suivi.ttj_s = suivi.total_pause_s = 0
        return

    vehicule = db.get(Vehicule, suivi.vehicule_id)
    roule, fin_sub = etat_roulage(vehicule, maintenant, seuils)
    segs_tous = [Segment(debut=t.heure_debut, fin=t.heure_fin, distance_km=t.distance_km,
                         rejete=(t.statut_validation == StatutValidationTrajet.REJETE), ref=t)
                 for t in tous if t.heure_debut is not None]
    journee = construire_journee(
        segs_tous,
        maintenant=maintenant, pause_min=pause_min, seuil_km=seuil_km,
        roule=roule, fin_substitution=fin_sub,
        pause_affichee_min=pause_tcc)

    if not journee.lignes:
        suivi.heure_depart = None
        suivi.tcc_s = suivi.tcj_s = suivi.ttj_s = suivi.total_pause_s = 0
        return

    suivi.heure_depart = journee.lignes[0].debut      # AM-6 : 1er mouvement valide
    suivi.tcj_s = journee.tcj_s                        # AM-1/§0duodecies F1 : UNION
    suivi.ttj_s = journee.ttj_s                        # T1 : amplitude brute
    suivi.total_pause_s = journee.total_pause_s        # Σ de TOUS les arrêts

    # §0duodecies F1 (arbitrage LSS 25/08/2026) — journal de transparence :
    # si des lignes se RECOUVRENT (Σ durées > union), la garde F1 a évité un
    # double comptage → une ligne d'audit par journée et par quantité
    # (aucune mutation des lignes ici : garde de mesure pure, jamais de spam).
    somme_brute_s = sum(lg.travail_s for lg in journee.lignes)
    recouvrement_s = somme_brute_s - journee.tcj_s
    if recouvrement_s >= 60:
        vehic = db.get(Vehicule, suivi.vehicule_id)
        _auditer_chevauchement_v132(db, suivi, vehic, recouvrement_s,
                                    len(journee.lignes))

    # ---- TCC : CHRONO de session (§0quaterdecies H1/H2 — arbitrage LSS ----
    # du 25/08/2026, réalignement sur le texte §1.1 « pause courte incluse »).
    # Session = lignes depuis la dernière pause coupante (≥ 30 min) ; TCC =
    # temps ÉCOULÉ du début de la 1re ligne de la session à la fin de la
    # dernière (ou maintenant), arrêts < 30 min INCLUS (ni coupants ni
    # déduits). E2/F1 (mesure de la session en conduite pure) sont amendés
    # sur ce SEUL point — TCJ/TTJ intacts (union F1), et le chrono ne peut
    # par construction pas double-compter.
    lignes = journee.lignes
    tcc = 0.0
    demarrage = 0
    for i, lg in enumerate(lignes[:-1]):
        if lg.gap_brut_s >= pause_tcc:
            demarrage = i + 1                  # pause ≥ 30 min → session coupée
    session = lignes[demarrage:]
    derniere = session[-1]
    fin_session = derniere.fin or maintenant
    if (derniere.fin is not None
            and (maintenant - derniere.fin).total_seconds() < pause_tcc):
        # H2 : arrêt court EN COURS (< 30 min) → le chrono continue de
        # s'écouler (pas de figeage à la fin de la dernière ligne) ; à
        # 30 min d'arrêt, la garde « pause coupante » ci-dessous remet à 0.
        fin_session = maintenant
    tcc = max(0.0, (fin_session - session[0].debut).total_seconds())

    # R1 (v3) : continuité à travers minuit — 1ʳᵉ ligne = segment B d'un split
    # (début ≈ 00:00:00) et veille close à ≈ 23:59:59 avec session ouverte
    if demarrage == 0 and session:
        premiere = session[0]
        borne = premiere.debut
        # tolérance 5 min : le segment B naît en direct au premier signal
        # après minuit (cadence boîtier), pas à 00:00:00.000 pile
        if borne.hour == 0 and borne.minute < 5:
            veille = db.scalar(select(SuiviJournalier).where(
                SuiviJournalier.vehicule_id == suivi.vehicule_id,
                SuiviJournalier.date_jour == suivi.date_jour - timedelta(days=1)))
            if veille is not None and veille.tcc_s:
                # la veille doit s'être close par un split : son dernier
                # trajet finit dans les 5 dernières minutes de la journée
                derniere_veille = db.scalars(select(Trajet).where(
                    Trajet.suivi_id == veille.id).order_by(
                        Trajet.heure_fin.desc()).limit(1)).first()
                if (derniere_veille is not None
                        and derniere_veille.heure_fin is not None
                        and derniere_veille.heure_fin.hour == 23
                        and derniere_veille.heure_fin.minute >= 54):
                    tcc += veille.tcc_s

    # camion actuellement en pause coupante → TCC retombe à 0 (comme avant)
    derniere = lignes[-1]
    if (derniere.fin is not None
            and (maintenant - derniere.fin).total_seconds() >= pause_tcc):
        tcc = 0.0

    # ── Correctif v1.46 (arbitrage LSS du 04/09/2026) — deux garde-fous qui
    # ramènent le chrono à ZÉRO, demandés par l'exploitant :
    # (a) MINI-MANŒUVRES CUMULÉES : la durée TOTALE des trajets invalides
    #     (manœuvres < 0,3 km, rejetées) de la session courante atteint
    #     SEUIL_PAUSE_COUPURE_TCC (30 min) → le camion est en réalité à l'arrêt
    #     depuis une pause coupante → TCC = 0 (AM-6 étendu : avant, une
    #     manœuvre ne coupait que si ELLE SEULE faisait ≥ 30 min).
    # (b) LIGNE OUVERTE + CAMION ARRÊTÉ : une ligne « en cours » (GPS muet ou
    #     signal de roulage absent) faisait courir le chrono INDÉFINIMENT —
    #     même camion garé depuis des heures. Si le camion ne roule PAS
    #     (etat_roulage, signal > 15 min ou vitesse ≤ 3 km/h) et que le
    #     dernier signal connu date de ≥ 30 min → TCC = 0 (H2 honoré :
    #     en dessous de 30 min le chrono continue de s'écouler).
    if session:
        debut_session = session[0].debut
        fin_session_ref = fin_session
        manoeuvres_s = sum(
            (s.fin - s.debut).total_seconds()
            for s in segs_tous
            if s.rejete and s.debut is not None and s.fin is not None
            and debut_session <= s.debut <= fin_session_ref)
        if manoeuvres_s >= pause_tcc:
            tcc = 0.0
    if (session and session[-1].fin is None
            and not roule and fin_sub is not None
            and (maintenant - fin_sub).total_seconds() >= pause_tcc):
        tcc = 0.0
    suivi.tcc_s = int(max(0.0, tcc))


# ------------------------------------------------------------------ infractions / alertes
def _infraction_recente(db, type_inf, vehicule_id, jour, minutes=60) -> bool:
    """Garde anti-doublon persistante (survit aux redémarrages)."""
    borne = now_local() - timedelta(minutes=minutes)
    q = select(func.count(Infraction.id)).where(
        Infraction.type == type_inf, Infraction.vehicule_id == vehicule_id,
        Infraction.date_jour == jour, Infraction.created_at >= borne)
    return (db.scalar(q) or 0) > 0


def _alerte_recente(db, type_alerte, vehicule_id, minutes) -> bool:
    borne = now_local() - timedelta(minutes=minutes)
    q = select(func.count(Alerte.id)).where(
        Alerte.type == type_alerte, Alerte.vehicule_id == vehicule_id,
        Alerte.date_heure >= borne)
    return (db.scalar(q) or 0) > 0


def _alerte_recente_conducteur(db, type_alerte, conducteur_id, minutes) -> bool:
    borne = now_local() - timedelta(minutes=minutes)
    q = select(func.count(Alerte.id)).where(
        Alerte.type == type_alerte, Alerte.conducteur_id == conducteur_id,
        Alerte.date_heure >= borne)
    return (db.scalar(q) or 0) > 0


def verifier_alertes_tch(db, conducteur_id: str | None, seuils: dict, ts: datetime):
    """Contrôle des seuils TCH (Temps de Conduite Hebdomadaire) pour un chauffeur.

    - Seuil d'avertissement : TCH cumulé ≥ 46h00 (165 600 s)
      Message : « TCH proche de la limite — [Nom chauffeur] — [Cumul TCH] — [TCH restant] »
    - Seuil limite : TCH cumulé ≥ 56h00 (201 600 s)
      Message : « TCH limite atteinte — [Nom chauffeur] »
    """
    if not conducteur_id:
        return

    from .routers.temps_conduite import (
        SEUIL_TCH_ALERTE_S, SEUIL_TCH_MAX_S, calculer_tch_seul_conducteur)

    seuil_alerte = float(seuils.get("SEUIL_TCH_ALERTE", SEUIL_TCH_ALERTE_S))
    seuil_max = float(seuils.get("SEUIL_TCH_MAX", SEUIL_TCH_MAX_S))

    try:
        tch_info = calculer_tch_seul_conducteur(db, conducteur_id, maintenant=ts)
    except Exception:
        log.exception("Erreur calcul TCH pour alerte chauffeur %s", conducteur_id)
        return

    tch_cumul = tch_info.get("tch_cumul_s") or 0
    tch_restant = max(0, int(seuil_max - tch_cumul))

    flag_max = ("tch_max", conducteur_id)
    flag_warn = ("tch_warn", conducteur_id)

    conducteur = db.get(Conducteur, conducteur_id)
    nom = (conducteur.nom_prenom or conducteur.prenom_usuel) if conducteur else "Chauffeur"

    if tch_cumul >= seuil_max:
        with _lock:
            leve_max = flag_max in _flags
        if not leve_max and not _alerte_recente_conducteur(db, TypeAlerte.TCH_LIMITE_ATTEINTE, conducteur_id, minutes=120):
            alerte = creer_alerte(
                db, TypeAlerte.TCH_LIMITE_ATTEINTE, GraviteAlerte.CRITIQUE,
                f"TCH limite atteinte — {nom}",
                ts=ts, conducteur_id=conducteur_id, lien_module="/temps-conduite")
            if PUBLISH_ENABLED["on"]:
                publish("alerte.new", s_alerte(alerte))
            with _lock:
                _flags[flag_max] = True
                _flags[flag_warn] = True
    elif tch_cumul >= seuil_alerte:
        with _lock:
            leve_warn = flag_warn in _flags
            _flags.pop(flag_max, None)
        if not leve_warn and not _alerte_recente_conducteur(db, TypeAlerte.TCH_PROCHE_LIMITE, conducteur_id, minutes=120):
            from .serializers import fmt_hms
            cumul_txt = fmt_hms(tch_cumul) or "46:00"
            restant_txt = fmt_hms(tch_restant) or "00:00"
            alerte = creer_alerte(
                db, TypeAlerte.TCH_PROCHE_LIMITE, GraviteAlerte.MOYENNE,
                f"TCH proche de la limite — {nom} — {cumul_txt} — {restant_txt}",
                ts=ts, conducteur_id=conducteur_id, lien_module="/temps-conduite")
            if PUBLISH_ENABLED["on"]:
                publish("alerte.new", s_alerte(alerte))
            with _lock:
                _flags[flag_warn] = True
    else:
        with _lock:
            _flags.pop(flag_warn, None)
            _flags.pop(flag_max, None)


def creer_infraction(db, suivi, vehicule, type_inf, gravite, ts,
                     duree_s=None, valeur=None, seuil_ref=None,
                     source=SourceEvenement.SIMULATEUR, lat=None, lon=None, adresse=None):
    inf = Infraction(
        date_jour=ts.date(), heure=ts.time().replace(microsecond=0),
        conducteur_id=suivi.conducteur_id if suivi else vehicule.conducteur_actuel_id,
        vehicule_id=vehicule.id, type=type_inf, gravite=gravite,
        duree_s=duree_s, valeur_mesuree=valeur, seuil_reference=seuil_ref,
        mission_id=suivi.mission_id if suivi else None, source=source,
        latitude=lat, longitude=lon, adresse=adresse)
    db.add(inf)
    db.flush()
    log.info("Infraction %s sur %s", type_inf.value, vehicule.plaque)
    return inf


def creer_alerte(db, type_alerte, gravite, message, ts=None,
                 vehicule_id=None, conducteur_id=None, infraction_id=None, lien_module=None):
    a = Alerte(
        date_heure=ts or now_local(), type=type_alerte, gravite=gravite,
        vehicule_id=vehicule_id, conducteur_id=conducteur_id,
        message=message, statut=StatutAlerte.NOUVELLE,
        lien_module=lien_module, infraction_id=infraction_id)
    db.add(a)
    db.flush()
    return a


def _signaler_trajets_exceptionnels(db, suivi, vehicule, ts):
    """Addendum v1.1 §1.3 — > 10 trajets dans la journée : alerte INFORMATION,
    une seule fois par jour et par véhicule (la donnée n'est jamais perdue)."""
    flag = ("trajets_exceptionnels", suivi.id)
    with _lock:
        if flag in _flags:
            return
        _flags[flag] = True
    debut_jour = datetime.combine(ts.date(), datetime.min.time())
    deja = (db.scalar(select(func.count(Alerte.id)).where(
        Alerte.type == TypeAlerte.NB_TRAJETS_EXCEPTIONNEL,
        Alerte.vehicule_id == vehicule.id, Alerte.date_heure >= debut_jour)) or 0) > 0
    if deja:
        return
    a = creer_alerte(db, TypeAlerte.NB_TRAJETS_EXCEPTIONNEL, GraviteAlerte.INFORMATION,
                     f"Nombre de trajets exceptionnel (> 10) détecté — {vehicule.plaque} "
                     f"le {ts:%d/%m/%Y} : trajets 11+ stockés, visibles via « +N ».",
                     ts=ts, vehicule_id=vehicule.id,
                     conducteur_id=suivi.conducteur_id if suivi else vehicule.conducteur_actuel_id,
                     lien_module=f"/suivi?date={ts.date().isoformat()}")
    if PUBLISH_ENABLED["on"]:
        publish("alerte.new", s_alerte(a))


def _publier_infraction_alerte(db, inf: Infraction, alerte: Alerte | None):
    if not PUBLISH_ENABLED["on"]:
        return
    publish("infraction.new", s_infraction(inf))
    if alerte:
        publish("alerte.new", s_alerte(alerte))


def _verifier_temps(db, suivi, vehicule, seuils, ts):
    """Contrôle des seuils TCC/TCJ/TTJ avec transitions (pas de doublons).

    v3 AM-5 / C3 (arbitrage LSS 22/08/2026) : PLUS AUCUNE écriture locale
    dans `Infraction` — le dépassement produit uniquement une ALERTE dans
    l'onglet Alertes (« sa propre surface d'alerte »). L'onglet Infractions
    reste une vitre de lecture d'infractions pré-filtrées par une autre
    plateforme — vide tant qu'aucun connecteur externe n'est branché."""
    jour = suivi.date_jour
    verifs = [
        ("tcc", suivi.tcc_s, "SEUIL_TCC_MAX",
         TypeAlerte.TCC_DEPASSE, "Conduite continue > 4h30"),
        ("tcj", suivi.tcj_s, "SEUIL_TCJ_MAX",
         TypeAlerte.TCC_DEPASSE, "Conduite journalière > 10h"),
        ("ttj", suivi.ttj_s, "SEUIL_TTJ_MAX",
         TypeAlerte.TCC_DEPASSE, "Temps de travail journalier > 12h"),
    ]
    for cle, valeur, cle_seuil, type_alerte, libelle in verifs:
        seuil = seuils[cle_seuil]
        flag = ("seuil", cle, suivi.id)
        with _lock:
            leve = flag in _flags
        if valeur and valeur > seuil and not leve:
            if _alerte_recente(db, type_alerte, vehicule.id, minutes=90):
                with _lock:
                    _flags[flag] = True
                continue
            nom = suivi.conducteur.prenom_usuel if suivi.conducteur else "—"
            alerte = creer_alerte(
                db, type_alerte, GraviteAlerte.CRITIQUE,
                f"{libelle} — {vehicule.plaque} ({nom}) : {_hmm(valeur)} > {_hmm(seuil)}",
                ts=ts, vehicule_id=vehicule.id, conducteur_id=suivi.conducteur_id,
                lien_module=f"/suivi?date={jour.isoformat()}&vehicule={vehicule.id}")
            if PUBLISH_ENABLED["on"]:
                publish("alerte.new", s_alerte(alerte))
            with _lock:
                _flags[flag] = True
        elif valeur <= seuil and leve:
            with _lock:
                _flags.pop(flag, None)

    # Alerte préventive « pause non prise » — arbitrage LSS §0quater O1
    # (14/08/2026) : seuil dédié SEUIL_TCC_PREALERTE (4h00 par défaut,
    # paramétrable §12.2) ; avant : 85 % du seuil TCC (3h49:30)
    seuil_tcc = seuils["SEUIL_TCC_MAX"]
    seuil_pre = float(seuils.get("SEUIL_TCC_PREALERTE", 14400))
    flag_p = ("pause_warn", suivi.id)
    if suivi.tcc_s and suivi.tcc_s > seuil_pre and suivi.tcc_s <= seuil_tcc:
        with _lock:
            deja = flag_p in _flags
        if not deja and not _alerte_recente(db, TypeAlerte.PAUSE_NON_PRISE, vehicule.id, 120):
            a = creer_alerte(
                db, TypeAlerte.PAUSE_NON_PRISE, GraviteAlerte.MOYENNE,
                f"Pré-alerte TCC (pause à prévoir) — {vehicule.plaque} : "
                f"conduite continue {_hmm(suivi.tcc_s)} — seuil 4h30 dans "
                f"{_hmm(seuil_tcc - suivi.tcc_s)}",
                ts=ts, vehicule_id=vehicule.id, conducteur_id=suivi.conducteur_id,
                lien_module=f"/suivi?date={jour.isoformat()}&vehicule={vehicule.id}")
            if PUBLISH_ENABLED["on"]:
                publish("alerte.new", s_alerte(a))
            with _lock:
                _flags[flag_p] = True
    elif not suivi.tcc_s or suivi.tcc_s <= seuil_tcc * 0.5:
        with _lock:
            _flags.pop(flag_p, None)

    # --- Contrôle TCH (Temps de Conduite Hebdomadaire par chauffeur) ---
    if suivi.conducteur_id:
        verifier_alertes_tch(db, suivi.conducteur_id, seuils, ts)


def _hmm(secondes) -> str:
    s = int(secondes or 0)
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


# ------------------------------------------------------------------ ingestion GPS
def _plateforme_trajet(vehicule: Vehicule, source: SourceEvenement) -> str:
    """Plateforme GPS à porter sur le trajet (Addendum v1.4 §3.1, traçabilité)."""
    if source in (SourceEvenement.MZONEX, SourceEvenement.CAMTRACKPRO):
        return source.value
    return vehicule.plateforme_gps or "MZONEX"


def _auditer(db, action: str, entite_id: str | None, details: dict):
    """Journal d'audit système (Addendum v1.5 §7.3 : REJET_TRAJET traçable)."""
    db.add(AuditLog(username="moteur", action=action, entite="trajet",
                    entite_id=entite_id, details=details))


def _finaliser_trajet(db, trajet: "Trajet", vehicule: Vehicule, seuils: dict,
                      ts: datetime):
    """Addendum v1.5 §1.1/§2.5 — à la clôture DÉFINITIVE d'un trajet (vraie
    pause ≥ 20 min derrière) : distance < 0,3 km → manœuvre locale → REJETÉ
    (audit `trajet.rejet_distance`), jamais compté dans TCC/TCJ/TTJ.
    Les trajets SANS distance connue (anciens, sans trace GPS) restent tels
    quels — prudence sur l'existant. Retourne Vrai si le trajet est rejeté."""
    if trajet.statut_validation == StatutValidationTrajet.REJETE:
        return True
    if trajet.distance_km is None:
        return False
    seuil = float(seuils["SEUIL_DISTANCE_MIN_TRAJET_KM"])
    if trajet.distance_km >= seuil:
        trajet.statut_validation = StatutValidationTrajet.VALIDE
        return False
    trajet.statut_validation = StatutValidationTrajet.REJETE
    _auditer(db, "trajet.rejet_distance", trajet.id, {
        "plaque": vehicule.plaque, "raison": "DISTANCE_INSUFFISANTE",
        "distance_km": round(float(trajet.distance_km), 3), "seuil_km": seuil,
        "debut": iso(trajet.heure_debut), "fin": iso(trajet.heure_fin or ts)})
    log.info("%s rejeté (manœuvre %.3f km < %g km) — exclu TCC/TCJ/TTJ",
             vehicule.plaque, trajet.distance_km, seuil)
    return True


def _statut_initial_trajet(plateforme: str) -> "StatutSourceTrajet":
    """Addendum v1.4 §2.2/§5 : MZoneX publie les trajets clôturés en différé →
    reconstruction temps réel PROVISOIRE en attendant l'onglet Trajets.
    CamtrackPro « Detail Trajet groupe de véhicules » restitue le calcul natif
    plateforme → VALIDÉ directement (point à re-vérifier avec le métier, §5)."""
    from .models import StatutSourceTrajet
    if plateforme == "CAMTRACKPRO":
        return StatutSourceTrajet.VALIDE
    return StatutSourceTrajet.PROVISOIRE


def _nouveau_trajet(suivi: SuiviJournalier, numero: int, debut: datetime,
                    vehicule: Vehicule, source: SourceEvenement) -> "Trajet":
    plateforme = _plateforme_trajet(vehicule, source)
    return Trajet(suivi_id=suivi.id, numero=numero, heure_debut=debut,
                  statut_source=_statut_initial_trajet(plateforme),
                  source_plateforme=plateforme)


def ingest_event(db, vehicule: Vehicule, ts: datetime, lat: float, lon: float,
                 adresse: str | None, vitesse: float, moteur: str,
                 type_force: TypeEvenement | None = None,
                 source: SourceEvenement = SourceEvenement.SIMULATEUR,
                 publier: bool = True) -> dict | None:
    """Point d'entrée unique d'un événement GPS (scraper ou simulateur).

    §7.1 : début de mouvement (vitesse > 0 après arrêt), arrêt (vitesse nulle
    persistante > bruit), pause (arrêt ≥ DUREE_MIN_PAUSE_VALIDE), reprise.
    """
    seuils = get_seuils(db)
    # Addendum v1.5 : le seuil « bruit GPS » (2 min) est englobé par la règle
    # de fusion des pauses < 20 min — plus de rôle distinct ici.
    pause_min = seuils["DUREE_MIN_PAUSE_VALIDE"]
    # §0undecies E4 (24/08/2026) — libellé de position : ni l'API Events
    # MZoneX ni les positions CamtrackPro ne géocodent (§0sexies A2) → le
    # nom de lieu vient des géozones des deux portails (« nom affiché sur
    # carte à proximité ») ; en dernier recours les coordonnées — jamais de
    # cellule vide, partout où « position inconnue » apparaissait.
    if not adresse and lat is not None and lon is not None:
        from .geozones import libelle_position
        adresse = libelle_position(lat, lon) or f"{lat:.4f}, {lon:.4f}"

    prev_lat, prev_lng, prev_ts = vehicule.last_lat, vehicule.last_lng, vehicule.last_event_at

    # Référence v2 §8.3 — suivi du JOUR D'ATTRIBUTION de l'événement : avant
    # 01h00 on complète la VEILLE (un trajet commencé à 23h40 se poursuit sur
    # sa journée d'origine ; la nouvelle journée débute à 01h00)
    suivi = ensure_suivi(db, vehicule, jour_attribution(ts))
    # requête EXPLICITE (session longue, expire_on_commit=False : la collection
    # relationnelle suivi.trajets peut être périmée — le moteur exige l'état
    # courant de la base pour fusionner/clôturer correctement, Addendum v1.5)
    trajets = sorted(
        db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(Trajet.numero)).all(),
        key=lambda t: t.numero)

    type_ev = type_force
    # Référence v2 §5.1/§12.1 + arbitrage LSS 14/08/2026 : roule ⟺ vitesse >
    # SEUIL_VITESSE_ARRET (3 km/h — embouteillages 5-11 km/h = en route)
    roule = vitesse and vitesse > float(seuils.get("SEUIL_VITESSE_ARRET", 3))
    cloture: Trajet | None = None  # Addendum v1.4 — trajet clôturé par cet événement
    dist_ok: float | None = None   # Addendum v1.5 — segment crédible à créditer

    if roule:
        # kilométrage (avec filtre anti-saut GPS aberrant)
        if prev_lat is not None and prev_ts is not None:
            dist = haversine_km(prev_lat, prev_lng, lat, lon)
            dt_h = max(1e-6, (ts - prev_ts).total_seconds() / 3600.0)
            if dist / dt_h < 140:  # vitesse implicite plausible
                dist_ok = dist
                suivi.km_parcourus = (suivi.km_parcourus or 0) + dist
                if suivi.mission_id:
                    m = db.get(Mission, suivi.mission_id)
                    if m:
                        if getattr(m, "statut_camion_actuel", "VIDE") == "CHARGE":
                            m.km_charge = round((m.km_charge or 0.0) + dist, 3)
                        else:
                            m.km_vide = round((m.km_vide or 0.0) + dist, 3)
                        m.kilometrage_total = round((m.km_vide or 0.0) + (m.km_charge or 0.0), 3)
                        m.kilometrage = m.kilometrage_total

        if not trajets:
            trajets = [_nouveau_trajet(suivi, 1, ts, vehicule, source)]
            db.add(trajets[0])
            type_ev = type_ev or TypeEvenement.DEBUT_MOUVEMENT
        elif trajets[-1].heure_fin is not None:
            gap = (ts - trajets[-1].heure_fin).total_seconds()
            if gap < pause_min:
                # Addendum v1.5 §1.2/§5 — pause < 20 min = INVALIDE → FUSION :
                # aucune rupture de trajet, la conduite continue (durée =
                # segment A + pause ignorée + segment B ; garde anti-bruit GPS
                # incluse dans la même règle puisque 2 min < 20 min)
                trajets[-1].heure_fin = None
                type_ev = type_ev or TypeEvenement.DEBUT_MOUVEMENT
            else:
                # la pause est ≥ 20 min → VALIDÉE : le trajet précédent devient
                # définitif → verdict de validité métier (Addendum v1.5 §1.1)
                _finaliser_trajet(db, trajets[-1], vehicule, seuils, ts)
                trajets[-1].pause_apres_s = int(gap)
                # la pause est validée : on requalifie l'événement ARRET en PAUSE
                ev_arret = db.scalar(select(EvenementGPS).where(
                    EvenementGPS.vehicule_id == vehicule.id,
                    EvenementGPS.type_evenement == TypeEvenement.ARRET,
                    EvenementGPS.horodatage <= ts).order_by(EvenementGPS.horodatage.desc()))
                if ev_arret:
                    ev_arret.type_evenement = TypeEvenement.PAUSE
                # Addendum v1.1 §1 : plafond d'affichage porté à 10 et aucune
                # donnée perdue — les trajets 11+ restent stockés en base (garde
                # anti-bruit GPS à 25) et déclenchent une alerte INFORMATION.
                if len(trajets) < 25:
                    t = _nouveau_trajet(suivi, len(trajets) + 1, ts, vehicule, source)
                    db.add(t)
                    trajets.append(t)
                    if t.numero == 11:
                        _signaler_trajets_exceptionnels(db, suivi, vehicule, ts)
                type_ev = type_ev or TypeEvenement.REPRISE
        else:
            type_ev = type_ev or TypeEvenement.POSITION
    else:
        # vitesse nulle
        if trajets and trajets[-1].heure_fin is None:
            trajets[-1].heure_fin = ts
            suivi.arret_final = f"{ts:%H:%M} · {adresse or 'position inconnue'}"
            type_ev = type_ev or TypeEvenement.ARRET
            cloture = trajets[-1]   # Addendum v1.4 — le trajet vient de se terminer
        else:
            type_ev = type_ev or TypeEvenement.POSITION

    # Addendum v1.5 — distance propre au TRAJET en cours (base du verdict
    # 0,3 km à la finalisation ; remplacée par la distance officielle de
    # l'onglet Trajets / du rapport lors de la réconciliation Niveau 2)
    if dist_ok is not None and trajets and trajets[-1].heure_fin is None:
        trajets[-1].distance_km = round((trajets[-1].distance_km or 0) + dist_ok, 4)

    ev = EvenementGPS(
        vehicule_id=vehicule.id, horodatage=ts, latitude=lat, longitude=lon,
        adresse=adresse, vitesse=round(float(vitesse or 0), 1), etat_moteur=moteur,
        type_evenement=type_ev, source=source)
    db.add(ev)

    vehicule.last_lat, vehicule.last_lng = lat, lon
    vehicule.last_vitesse = vitesse
    vehicule.last_adresse = adresse
    vehicule.last_event_at = ts
    vehicule.moteur_on = (moteur == "ON")

    recalculer_temps(db, suivi, ts)
    _verifier_temps(db, suivi, vehicule, seuils, ts)

    # --- événements de conduite (source boîtier OBC) — v3 AM-5/C3 : plus
    # AUCUNE écriture locale dans `Infraction` ; l'excès de vitesse dont le
    # seuil est local reste une ALERTE (sa propre surface) ; freinage /
    # accélération brusques restent des événements bruts en base (audit) et
    # remontent via les compteurs d'écoconduite des portails (§0septies B3).
    if vitesse and vitesse > seuils["SEUIL_VITESSE_MAX"]:
        flag = ("vit", vehicule.id)
        with _lock:
            dernier = _flags.get(flag)
        if not dernier or (ts - dernier).total_seconds() > 300:
            if not _alerte_recente(db, TypeAlerte.EXCES_VITESSE, vehicule.id, minutes=10):
                critique = vitesse > seuils["SEUIL_VITESSE_MAX"] + 20
                alerte = creer_alerte(
                    db, TypeAlerte.EXCES_VITESSE,
                    GraviteAlerte.CRITIQUE if critique else GraviteAlerte.MOYENNE,
                    f"Excès de vitesse — {vehicule.plaque} : {vitesse:.0f} km/h "
                    f"(seuil {seuils['SEUIL_VITESSE_MAX']:.0f} km/h) près de {adresse or '—'}",
                    ts=ts, vehicule_id=vehicule.id, conducteur_id=suivi.conducteur_id,
                    lien_module="/alertes")
                if PUBLISH_ENABLED["on"]:
                    publish("alerte.new", s_alerte(alerte))
                with _lock:
                    _flags[flag] = ts

    if type_force in (TypeEvenement.FREINAGE_BRUSQUE, TypeEvenement.ACCELERATION_BRUSQUE):
        lib = "Freinage brusque" if type_force == TypeEvenement.FREINAGE_BRUSQUE else "Accélération brusque"
        log.info("%s — %s (événement brut conservé ; pas d'infraction locale, AM-5)",
                 lib, vehicule.plaque)

    # --- alerte hors itinéraire (seulement en roulage) ---------------------
    if roule and vitesse > 10:
        seuil_km = seuils["DISTANCE_HORS_ITINERAIRE"]
        flag_h = ("hors", vehicule.id)
        with _lock:
            dernier_h = _flags.get(flag_h)
        if (not dernier_h or (ts - dernier_h).total_seconds() > 1800) and hors_corridor(lat, lon, seuil_km):
            if not _alerte_recente(db, TypeAlerte.HORS_ITINERAIRE, vehicule.id, 60):
                a = creer_alerte(
                    db, TypeAlerte.HORS_ITINERAIRE, GraviteAlerte.MOYENNE,
                    f"Camion hors itinéraire — {vehicule.plaque} à plus de {seuil_km:.0f} km "
                    f"du corridor logistique ({adresse or 'zone inconnue'})",
                    ts=ts, vehicule_id=vehicule.id, conducteur_id=suivi.conducteur_id,
                    lien_module="/dashboard")
                if PUBLISH_ENABLED["on"]:
                    publish("alerte.new", s_alerte(a))
            with _lock:
                _flags[flag_h] = ts

    # --- Détection automatique logistique & transitions géographiques (§3) ---
    z_info = detecter_zone_logistique(lat, lon, adresse)
    z_type, z_code, z_nom = z_info["type"], z_info["code"], z_info["nom"]
    seuil_rn2_s = float(seuils.get("SEUIL_DUREE_DEPART_RN2", 3600))
    flag_sortie = ("sortie_base", vehicule.id)

    # 0. Initialisation automatique de mission au départ de Base Tana vers RN2 (Règle 1 : départ validé après ≥ 1h de conduite RN2)
    # RÈGLE ABSOLUE : Un déplacement au Sud (RN7), un retour vers la Base ou un déplacement local ne crée JAMAIS de mission.
    est_sur_rn2_est = (lon is not None and lon > 47.53 and (lat is None or lat > -19.0)) or ("rn2" in (adresse or "").lower()) or (z_type == "GRT")
    if not suivi.mission_id and (suivi.numero_ot or est_sur_rn2_est):
        if prev_lat is not None and prev_lng is not None:
            prev_z = detecter_zone_logistique(prev_lat, prev_lng, vehicule.last_adresse)
            if prev_z["type"] == "BASETNR" and z_type != "BASETNR" and est_sur_rn2_est:
                with _lock:
                    _flags[flag_sortie] = ts

        ts_cand = None
        with _lock:
            ts_cand = _flags.get(flag_sortie)

        if ts_cand:
            if z_type == "BASETNR" or not est_sur_rn2_est:
                with _lock:
                    _flags.pop(flag_sortie, None)
            else:
                duree_hors_base_s = (ts - ts_cand).total_seconds()
                if (duree_hors_base_s >= seuil_rn2_s and (roule or (vitesse and vitesse > 3))) or z_type == "GRT":
                    m_nouv = initialiser_ou_maj_mission(db, suivi, vehicule, None, None, None, None, ts_cand)
                    m_nouv.heure_debut = ts_cand
                    m_nouv.statut = StatutMission.EN_COURS
                    m_nouv.statut_camion_actuel = "LIBRE"
                    suivi.statut_camion = StatutCamion.LIBRE
                    m_nouv.etapes = [{"etat": "DEPART_BASE", "ts": iso(ts_cand), "lieu": f"Sortie {z_nom}", "zone": "BASETNR"}]
                    with _lock:
                        _flags.pop(flag_sortie, None)
                    log.info("Règle 1 validée : départ Base Tana confirmé après ≥1h RN2 pour %s -> Mission %s créée (LIBRE, heure_debut=%s)",
                             vehicule.plaque, m_nouv.code_mission or m_nouv.id, ts_cand)
                    if PUBLISH_ENABLED["on"]:
                        publish("mission.new", s_mission(m_nouv))

    if suivi.mission_id:
        m_actuelle = db.get(Mission, suivi.mission_id)
        # Verrouillage de protection : si la mission est déjà terminée, aucun événement ne peut la modifier
        if m_actuelle and m_actuelle.statut in (StatutMission.EN_COURS, StatutMission.DEVIEE, StatutMission.RETARDEE):

            # 1. Détection du départ physique (BASETNR ou Moramanga) -> Début réel de mission (heure_debut)
            if m_actuelle.heure_debut is None:
                if z_type == "BASETNR":
                    with _lock:
                        _flags.pop(flag_sortie, None)
                    if not any(e.get("etat") == "PRESENCE_BASE" for e in (m_actuelle.etapes or [])):
                        m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "PRESENCE_BASE", "ts": iso(ts), "lieu": z_nom, "zone": "BASETNR"}]
                else:
                    ts_cand = None
                    with _lock:
                        ts_cand = _flags.get(flag_sortie)
                        if not ts_cand:
                            _flags[flag_sortie] = ts
                            ts_cand = ts
                    duree_hors_base_s = (ts - ts_cand).total_seconds()
                    if (duree_hors_base_s >= seuil_rn2_s and (roule or (vitesse and vitesse > 3))) or z_type == "GRT" or m_actuelle.numero_ot:
                        m_actuelle.heure_debut = ts_cand
                        m_actuelle.statut = StatutMission.EN_COURS
                        if m_actuelle.statut_camion_actuel not in ("CHARGE", "CHARGÉ"):
                            if not m_actuelle.numero_ot:
                                m_actuelle.statut_camion_actuel = "LIBRE"
                                suivi.statut_camion = StatutCamion.LIBRE
                            else:
                                m_actuelle.statut_camion_actuel = "VIDE"
                                suivi.statut_camion = StatutCamion.VIDE
                        m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "DEPART_BASE", "ts": iso(ts_cand), "lieu": f"Sortie {z_nom}", "zone": "BASETNR"}]
                        with _lock:
                            _flags.pop(flag_sortie, None)
                        log.info("Sortie physique Base confirmée pour %s -> Début réel mission %s à %s",
                                 vehicule.plaque, m_actuelle.code_mission or m_actuelle.id, ts_cand)
                        if PUBLISH_ENABLED["on"]:
                            publish("mission.update", s_mission(m_actuelle))

            # 2. Présence et Chargement au Dépôt GRT (Tamatave)
            if z_type == "GRT":
                if not any(e.get("etat") == "ENTREE_GRT" for e in (m_actuelle.etapes or [])):
                    m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "ENTREE_GRT", "ts": iso(ts), "lieu": z_nom, "zone": "GRT"}]

                # Calcul du temps passé à GRT
                ts_entree_grt = None
                for e in reversed(m_actuelle.etapes or []):
                    if e.get("etat") == "ENTREE_GRT" and e.get("ts"):
                        try:
                            ts_entree_grt = datetime.fromisoformat(e["ts"])
                            break
                        except Exception:
                            pass
                duree_grt_s = (ts - ts_entree_grt).total_seconds() if ts_entree_grt else 0

                # Règle 2 : Présence GRT ≥ 15 min sans OT -> Alerte MISSION_SANS_OT (STRICTEMENT GRT UNIQUE)
                if duree_grt_s >= 900 and (not m_actuelle.numero_ot or m_actuelle.statut_camion_actuel == "LIBRE"):
                    if not _alerte_recente(db, TypeAlerte.MISSION_SANS_OT, vehicule.id, 60):
                        a = creer_alerte(
                            db, TypeAlerte.MISSION_SANS_OT, GraviteAlerte.MOYENNE,
                            f"OT manquant — Le camion {vehicule.plaque} est à GRT (GALANA RAFINERIE TERMINALE) depuis {int(duree_grt_s // 60)} min sans Ordre de Transport enregistré",
                            ts=ts, vehicule_id=vehicule.id, conducteur_id=suivi.conducteur_id,
                            lien_module="/missions")
                        if PUBLISH_ENABLED["on"]:
                            publish("alerte.new", s_alerte(a))

                # Règle 3 : Présence GRT ≥ 30 min -> Alerte VALIDATION_CHARGEMENT (STRICTEMENT GRT UNIQUE)
                if duree_grt_s >= 1800 and m_actuelle.validation_chargement != "VALIDÉ":
                    m_actuelle.validation_chargement = "EN_ATTENTE"
                    if not _alerte_recente(db, TypeAlerte.VALIDATION_CHARGEMENT, vehicule.id, 60):
                        a = creer_alerte(
                            db, TypeAlerte.VALIDATION_CHARGEMENT, GraviteAlerte.INFORMATION,
                            f"Validation requise : Chargement GRT (GALANA RAFINERIE TERMINALE) pour {vehicule.plaque} (durée présence : {int(duree_grt_s // 60)} min)",
                            ts=ts, vehicule_id=vehicule.id, conducteur_id=suivi.conducteur_id,
                            lien_module="/missions")
                        if PUBLISH_ENABLED["on"]:
                            publish("alerte.new", s_alerte(a))

            elif any(e.get("etat") == "ENTREE_GRT" for e in (m_actuelle.etapes or [])):
                # Sortie effective de la zone GRT -> Bascule automatique en CHARGÉ
                if getattr(m_actuelle, "statut_camion_actuel", "VIDE") in ("VIDE", "LIBRE") or suivi.statut_camion in (StatutCamion.VIDE, StatutCamion.LIBRE):
                    m_actuelle.statut_camion_actuel = "CHARGE"
                    m_actuelle.heure_chargement = ts
                    m_actuelle.validation_chargement = "VALIDÉ"
                    suivi.statut_camion = StatutCamion.CHARGE
                    m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "CHARGEMENT_EFFECTUE", "ts": iso(ts), "lieu": "GRT (GALANA RAFINERIE TERMINALE)", "zone": "GRT"}]
                    # Auto-résolution alerte chargement
                    db.query(Alerte).filter(
                        Alerte.vehicule_id == vehicule.id,
                        Alerte.type == TypeAlerte.VALIDATION_CHARGEMENT,
                        Alerte.statut != StatutAlerte.TRAITEE
                    ).update({Alerte.statut: StatutAlerte.TRAITEE}, synchronize_session=False)
                    log.info("Chargement GRT validé (sortie physique) pour %s (Mission %s) -> Statut Camion CHARGÉ",
                             vehicule.plaque, m_actuelle.code_mission or m_actuelle.id)
                    if PUBLISH_ENABLED["on"]:
                        publish("mission.update", s_mission(m_actuelle))
                elif getattr(m_actuelle, "statut_camion_actuel", "VIDE") == "CHARGE":
                    if not m_actuelle.heure_chargement or not any(e.get("etat") == "CHARGEMENT_EFFECTUE" for e in (m_actuelle.etapes or [])):
                        m_actuelle.heure_chargement = ts
                        m_actuelle.validation_chargement = "VALIDÉ"
                        m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "CHARGEMENT_EFFECTUE", "ts": iso(ts), "lieu": "GRT (GALANA RAFINERIE TERMINALE)", "zone": "GRT"}]
                        if PUBLISH_ENABLED["on"]:
                            publish("mission.update", s_mission(m_actuelle))

            # 3. Transit & Dépôts Récepteurs OFFICIELS STRICTS (DSNR, DABI, DMMG, DFIA, DMDV, DMKR, DABE) : Déviation & Déchargement
            if getattr(m_actuelle, "statut_camion_actuel", "VIDE") == "CHARGE":
                code_prevu = normaliser_code_depot(m_actuelle.depot_prevu)

                # Tolérance Dépôts Sud / Réparations Tana : passage BASETNR conserve statut CHARGÉ
                if z_type == "BASETNR" and (code_prevu in DEPOTS_SUD_CODES or z_info.get("est_depot_sud") or code_prevu in ("DABI", "DSNR")):
                    pass
                elif z_type == "DEPOT_RECEPTEUR" and z_code in DEPOTS_DECHARGEMENT_CODES:
                    nom_depot_officiel = nom_officiel_depot(z_code) or z_nom
                    
                    if z_code == code_prevu or not code_prevu or m_actuelle.est_deviee:
                        # 3.1 Camion dans son dépôt récepteur prévu (ou déviation déjà confirmée)
                        if not any(e.get("etat") == "ARRIVEE_DEPOT_RECEPTEUR" and e.get("zone") == z_code for e in (m_actuelle.etapes or [])):
                            m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "ARRIVEE_DEPOT_RECEPTEUR", "ts": iso(ts), "lieu": nom_depot_officiel, "zone": z_code}]
                            if PUBLISH_ENABLED["on"]:
                                publish("mission.update", s_mission(m_actuelle))
                    else:
                        # 3.2 Camion dans un AUTRE dépôt officiel récepteur
                        # Corridor RN2 : Moramanga est sur l'axe obligatoire GRT <-> Tana/Sud.
                        # Un camion à destination de Tana ou du Sud qui traverse Moramanga est en TRANSIT (Règle 4).
                        est_transit_rn2_moramanga = (z_code == "DMMG" and code_prevu in ("DABI", "DSNR", "DABE", "DFIA", "DMDV", "DMKR"))

                        if est_transit_rn2_moramanga:
                            # En transit RN2 Moramanga : déviation uniquement si arrêt avéré >= 2h (7200s)
                            if not roule and (vitesse is None or vitesse <= 3):
                                ts_presence_autre = None
                                for e in reversed(m_actuelle.etapes or []):
                                    if e.get("etat") == "PRESENCE_AUTRE_DEPOT" and e.get("zone") == z_code and e.get("ts"):
                                        try:
                                            ts_presence_autre = datetime.fromisoformat(e["ts"])
                                            break
                                        except Exception:
                                            pass
                                if not ts_presence_autre:
                                    ts_presence_autre = ts
                                    m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "PRESENCE_AUTRE_DEPOT", "ts": iso(ts), "lieu": nom_depot_officiel, "zone": z_code}]

                                duree_autre_s = (ts - ts_presence_autre).total_seconds()
                                if duree_autre_s >= 7200 and not m_actuelle.est_deviee:
                                    m_actuelle.est_deviee = True
                                    m_actuelle.statut = StatutMission.DEVIEE
                                    m_actuelle.depot_effectif = nom_depot_officiel
                                    m_actuelle.motif_deviation = f"Déviation constatée : arrêt ≥ 2h à {nom_depot_officiel} (prévu : {nom_officiel_depot(m_actuelle.depot_prevu) or m_actuelle.depot_prevu or '—'})"
                                    m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "DEVIATION_DETECTEE", "ts": iso(ts), "lieu": nom_depot_officiel, "zone": z_code}]
                                    if not _alerte_recente(db, TypeAlerte.DEVIATION_DETECTEE, vehicule.id, 60):
                                        a = creer_alerte(
                                            db, TypeAlerte.DEVIATION_DETECTEE, GraviteAlerte.CRITIQUE,
                                            f"Déviation détectée pour {vehicule.plaque} : nouveau dépôt {nom_depot_officiel} (prévu : {nom_officiel_depot(m_actuelle.depot_prevu) or m_actuelle.depot_prevu or '—'})",
                                            ts=ts, vehicule_id=vehicule.id, conducteur_id=suivi.conducteur_id,
                                            lien_module="/missions")
                                        if PUBLISH_ENABLED["on"]:
                                            publish("alerte.new", s_alerte(a))
                                    if PUBLISH_ENABLED["on"]:
                                        publish("mission.update", s_mission(m_actuelle))
                        else:
                            # Autre dépôt terminal (ex. Fianarantsoa au lieu d'Antsirabe) -> Déviation constatée
                            if not m_actuelle.est_deviee:
                                m_actuelle.est_deviee = True
                                m_actuelle.statut = StatutMission.DEVIEE
                                m_actuelle.depot_effectif = nom_depot_officiel
                                m_actuelle.motif_deviation = f"Déviation constatée : réorienté vers {nom_depot_officiel} (prévu : {nom_officiel_depot(m_actuelle.depot_prevu) or m_actuelle.depot_prevu or '—'})"
                                m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "DEVIATION_DETECTEE", "ts": iso(ts), "lieu": nom_depot_officiel, "zone": z_code}]
                                if not _alerte_recente(db, TypeAlerte.DEVIATION_DETECTEE, vehicule.id, 60):
                                    a = creer_alerte(
                                        db, TypeAlerte.DEVIATION_DETECTEE, GraviteAlerte.CRITIQUE,
                                        f"Déviation détectée pour {vehicule.plaque} : nouveau dépôt {nom_depot_officiel} (prévu : {nom_officiel_depot(m_actuelle.depot_prevu) or m_actuelle.depot_prevu or '—'})",
                                        ts=ts, vehicule_id=vehicule.id, conducteur_id=suivi.conducteur_id,
                                        lien_module="/missions")
                                    if PUBLISH_ENABLED["on"]:
                                        publish("alerte.new", s_alerte(a))
                                if PUBLISH_ENABLED["on"]:
                                    publish("mission.update", s_mission(m_actuelle))

                # Contrôle de déchargement (Règle 5) : arrêt ≥ 3h (10800 s) STRICTEMENT dans un des 7 dépôts récepteurs officiels ou sortie dépôt
                est_dans_depot_officiel = (z_type == "DEPOT_RECEPTEUR" and z_code in DEPOTS_DECHARGEMENT_CODES)
                a_arrivee_depot_officiel = any(e.get("etat") in ("ARRIVEE_DEPOT_RECEPTEUR", "DEVIATION_DETECTEE") and e.get("zone") in DEPOTS_DECHARGEMENT_CODES for e in (m_actuelle.etapes or []))

                # Si le camion est en transit RN2 Moramanga sans être dévié, pas de déchargement Moramanga
                if est_dans_depot_officiel or a_arrivee_depot_officiel:
                    code_actuel_dep = normaliser_code_depot(m_actuelle.depot_effectif or m_actuelle.depot_prevu or z_code)
                    if code_actuel_dep == "DMMG" and code_prevu in ("DABI", "DSNR", "DABE", "DFIA", "DMDV", "DMKR") and not m_actuelle.est_deviee:
                        pass
                    else:
                        arret_depot_s = 0
                        if not roule and trajets and trajets[-1].heure_fin is not None:
                            arret_depot_s = (ts - trajets[-1].heure_fin).total_seconds()
                        else:
                            for e in reversed(m_actuelle.etapes or []):
                                if e.get("etat") in ("ARRIVEE_DEPOT_RECEPTEUR", "DEVIATION_DETECTEE") and e.get("ts"):
                                    try:
                                        arret_depot_s = (ts - datetime.fromisoformat(e["ts"])).total_seconds()
                                        break
                                    except Exception:
                                        pass

                        nom_depot_alerte = nom_officiel_depot(m_actuelle.depot_effectif or m_actuelle.depot_prevu or z_nom) or (z_nom if z_code in DEPOTS_DECHARGEMENT_CODES else None)

                        # Règle 15 & 16 : Confirmation Dépotage Standard (4 conditions strictes)
                        # 1. Dépôt prévu = Dépôt détecté
                        # 2. Présence réelle dans le dépôt
                        # 3. Durée STRICTEMENT > 3 heures (arret_depot_s > 10800)
                        # 4. Sortie du dépôt détectée
                        if not est_dans_depot_officiel and a_arrivee_depot_officiel and m_actuelle.validation_dechargement != "INVALIDÉ":
                            if arret_depot_s > 10800:
                                # Les 4 conditions sont réunies -> Dépotage CONFIRMÉ (§16)
                                m_actuelle.validation_dechargement = "VALIDÉ"
                                m_actuelle.statut = StatutMission.TERMINEE
                                m_actuelle.statut_camion_actuel = "LIBRE"
                                m_actuelle.heure_fin = ts
                                if m_actuelle.heure_debut:
                                    m_actuelle.duree_s = max(0, int((ts - m_actuelle.heure_debut).total_seconds()))
                                if not any(e.get("etat") == "DECHARGEMENT_EFFECTUE" for e in (m_actuelle.etapes or [])):
                                    m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "DECHARGEMENT_EFFECTUE", "ts": iso(ts), "lieu": nom_depot_alerte or "Dépôt Récepteur", "zone": code_actuel_dep or "DEPOT"}]

                                suivi.statut_camion = StatutCamion.LIBRE
                                suivi.situation = f"Déchargé au {nom_depot_alerte or 'dépôt'} — Repositionnement"
                                suivi.numero_ot = None
                                suivi.distributeur = None
                                suivi.produit = None
                                suivi.depot_recepteur = None
                                suivi.mission_id = None

                                db.query(Alerte).filter(
                                    Alerte.vehicule_id == vehicule.id,
                                    Alerte.type.in_([TypeAlerte.VALIDATION_DECHARGEMENT, TypeAlerte.DEVIATION_DETECTEE]),
                                    Alerte.statut != StatutAlerte.TRAITEE
                                ).update({Alerte.statut: StatutAlerte.TRAITEE}, synchronize_session=False)

                                log.info("Dépotage standard confirmé après arrêt >3h et sortie de %s pour %s -> Mission %s TERMINÉE, camion LIBRE",
                                         nom_depot_alerte, vehicule.plaque, m_actuelle.code_mission or m_actuelle.id)
                                if PUBLISH_ENABLED["on"]:
                                    publish("mission.update", s_mission(m_actuelle))
                                    publish("suivi.update", {"suivi": s_suivi(suivi, seuils)})
                            else:
                                # Arrêt <= 3h puis sortie : incertitude / pas de dépotage automatique (§15, §41)
                                if m_actuelle.validation_dechargement not in ("VALIDÉ", "INVALIDÉ") and nom_depot_alerte:
                                    m_actuelle.validation_dechargement = "EN_ATTENTE"
                                    if not _alerte_recente(db, TypeAlerte.VALIDATION_DECHARGEMENT, vehicule.id, 120):
                                        a = creer_alerte(
                                            db, TypeAlerte.VALIDATION_DECHARGEMENT, GraviteAlerte.MOYENNE,
                                            f"Validation requise : Sortie de {nom_depot_alerte} pour {vehicule.plaque} avec durée arrêt ≤ 3h ({int(arret_depot_s // 60)} min)",
                                            ts=ts, vehicule_id=vehicule.id, conducteur_id=suivi.conducteur_id,
                                            lien_module="/missions")
                                        if PUBLISH_ENABLED["on"]:
                                            publish("alerte.new", s_alerte(a))

                        elif arret_depot_s > 10800 and m_actuelle.validation_dechargement not in ("VALIDÉ", "INVALIDÉ") and nom_depot_alerte:
                            # Toujours dans le dépôt après > 3h : en attente de sortie (§18)
                            m_actuelle.validation_dechargement = "EN_ATTENTE"

            # 4. Détection Checkpoints RN2 (DMMG - Preuve rétrospective / Invalidation §20-§25, §39)
            if getattr(m_actuelle, "statut_camion_actuel", "VIDE") == "CHARGE":
                cp = detecter_checkpoint_rn2(lat, lon, adresse)
                if cp:
                    if not any(e.get("etat") == f"CHECKPOINT_{cp}" for e in (m_actuelle.etapes or [])):
                        m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": f"CHECKPOINT_{cp}", "ts": iso(ts), "lieu": adresse or cp, "zone": cp}]

                    # Si le camion est passé par Moramanga (DMMG)
                    a_visite_mmg = any(e.get("zone") == "DMMG" for e in (m_actuelle.etapes or []))
                    if a_visite_mmg:
                        if cp in ("ANDASIBE", "AMBATOSONEGALY"):
                            # §21, §22 : Preuve rétrospective de dépotage à Moramanga
                            ts_dechargement_mmg = ts
                            for e in reversed(m_actuelle.etapes or []):
                                if e.get("zone") == "DMMG" and e.get("ts"):
                                    try:
                                        ts_dechargement_mmg = datetime.fromisoformat(e["ts"])
                                        break
                                    except Exception:
                                        pass

                            m_actuelle.validation_dechargement = "VALIDÉ"
                            m_actuelle.statut = StatutMission.TERMINEE
                            m_actuelle.statut_camion_actuel = "LIBRE"
                            m_actuelle.depot_effectif = nom_officiel_depot("DMMG")
                            m_actuelle.heure_fin = ts_dechargement_mmg
                            if m_actuelle.heure_debut:
                                m_actuelle.duree_s = max(0, int((ts_dechargement_mmg - m_actuelle.heure_debut).total_seconds()))
                            if not any(e.get("etat") == "DECHARGEMENT_EFFECTUE" for e in (m_actuelle.etapes or [])):
                                m_actuelle.etapes = (m_actuelle.etapes or []) + [{"etat": "DECHARGEMENT_EFFECTUE", "ts": iso(ts_dechargement_mmg), "lieu": nom_officiel_depot("DMMG"), "zone": "DMMG"}]

                            suivi.statut_camion = StatutCamion.LIBRE
                            suivi.situation = "Dépotage confirmé Moramanga (Preuve rétrospective) — Repositionnement"
                            suivi.numero_ot = None
                            suivi.distributeur = None
                            suivi.produit = None
                            suivi.depot_recepteur = None
                            suivi.mission_id = None

                            db.query(Alerte).filter(
                                Alerte.vehicule_id == vehicule.id,
                                Alerte.type.in_([TypeAlerte.VALIDATION_DECHARGEMENT, TypeAlerte.DEVIATION_DETECTEE]),
                                Alerte.statut != StatutAlerte.TRAITEE
                            ).update({Alerte.statut: StatutAlerte.TRAITEE}, synchronize_session=False)

                            log.info("§21 Dépotage DMMG confirmé rétrospectivement (checkpoint %s) pour %s -> Mission %s TERMINÉE",
                                     cp, vehicule.plaque, m_actuelle.code_mission or m_actuelle.id)
                            if PUBLISH_ENABLED["on"]:
                                publish("mission.update", s_mission(m_actuelle))
                                publish("suivi.update", {"suivi": s_suivi(suivi, seuils)})

                        elif cp in ("ANDRIAKA", "TANA"):
                            # §24 : Invalidation de l'hypothèse de dépotage à Moramanga (transit RN2 vers Tana/Sud)
                            pass

    db.commit()

    # Addendum v1.4 §2.3 — notification « trajet terminé » (hook : validation
    # Niveau 2 simulée chez le simulateur ; inerte en production réelle).
    if cloture is not None and HOOKS_TRAJET_CLOTURE:
        for hook in HOOKS_TRAJET_CLOTURE:
            try:
                hook(db, vehicule, suivi, cloture, ts)
            except Exception:
                log.exception("Hook clotûre trajet en échec (%s)", hook)
        db.commit()

    if publier and PUBLISH_ENABLED["on"]:
        publish("suivi.update", {"suivi": s_suivi(suivi, seuils)})
        publish("position", {
            "vehicule_id": vehicule.id, "plaque": vehicule.plaque,
            "lat": lat, "lng": lon, "vitesse": vitesse, "adresse": adresse,
            "maj": iso(ts),
        })
    return {"suivi_id": suivi.id, "type": type_ev.value}


# ------------------------------------------------------------------ missions (§6.3)
def _mission_ouverte(db, vehicule_id, jour) -> Mission | None:
    return db.scalar(select(Mission).where(
        Mission.vehicule_id == vehicule_id, Mission.date_jour == jour,
        Mission.statut.in_([StatutMission.EN_COURS, StatutMission.DEVIEE, StatutMission.RETARDEE])
    ).order_by(Mission.numero_mission_du_jour.desc()))


def _formater_code_mission(numero_ot: str | None, jour: date, numero: int = 1) -> str:
    if numero_ot and numero_ot.strip():
        ot = numero_ot.strip()
        if ot.upper().startswith("OT-") or ot.upper().startswith("OT_"):
            return f"MIS-{ot.upper()}"
        elif ot.upper().startswith("MIS-"):
            return ot.upper()
        else:
            return f"MIS-OT-{ot}"
    return f"MIS-{jour.strftime('%Y%m%d')}-{numero:02d}"


def initialiser_ou_maj_mission(db, suivi: SuiviJournalier, vehicule: Vehicule,
                              numero_ot: str | None, distributeur: str | None,
                              produit: str | None, depot_prevu: str | None,
                              ts: datetime | None = None) -> Mission:
    """Enregistre les informations administratives de l'OT (N° OT, Distributeur, Produit, Dépôt).
    - Bascule le camion de LIBRE à VIDE si état = LIBRE.
    - NE DÉCLENCHE PAS la date/heure de début de mission (réservée au départ physique réel de la base).
    """
    ts = ts or now_local()
    jour = suivi.date_jour
    ouverte = _mission_ouverte(db, vehicule.id, jour)
    if ouverte:
        if numero_ot:
            ouverte.numero_ot = numero_ot
            ouverte.code_mission = _formater_code_mission(numero_ot, jour, ouverte.numero_mission_du_jour)
        if distributeur:
            ouverte.distributeur = distributeur
        if produit:
            ouverte.produit = produit
        if depot_prevu:
            ouverte.depot = depot_prevu
            ouverte.depot_prevu = depot_prevu
            if not ouverte.depot_effectif:
                ouverte.depot_effectif = depot_prevu
        if not any(e.get("etat") == "INITIALISATION_OT" for e in (ouverte.etapes or [])):
            ouverte.etapes = (ouverte.etapes or []) + [{"etat": "INITIALISATION_OT", "ts": iso(ts),
                     "lieu": (vehicule.last_adresse if vehicule else None) or "Base LSS — Antananarivo",
                     "zone": "BASETNR"}]
        suivi.mission_id = ouverte.id
        if suivi.statut_camion == StatutCamion.LIBRE:
            suivi.statut_camion = StatutCamion.VIDE
            ouverte.statut_camion_actuel = "VIDE"
        if PUBLISH_ENABLED["on"]:
            publish("mission.update", s_mission(ouverte))
        return ouverte

    numero = (db.scalar(select(func.count(Mission.id)).where(
        Mission.vehicule_id == vehicule.id, Mission.date_jour == jour)) or 0) + 1
    code = _formater_code_mission(numero_ot, jour, numero)

    # Note : heure_debut reste None à la saisie administrative de l'OT
    # si le véhicule n'est pas encore sorti physiquement de BASETNR
    m = Mission(
        id=uid(),
        code_mission=code,
        date_jour=jour,
        conducteur_id=suivi.conducteur_id or (vehicule.conducteur_actuel_id if vehicule else None),
        vehicule_id=vehicule.id,
        numero_mission_du_jour=numero,
        statut=StatutMission.EN_COURS,
        statut_camion_actuel="VIDE",
        heure_debut=None,  # Début réel télématique au départ physique de la base
        numero_ot=numero_ot,
        distributeur=distributeur,
        produit=produit,
        depot=depot_prevu,
        depot_prevu=depot_prevu,
        depot_effectif=depot_prevu,
        est_deviee=False,
        km_vide=0.0,
        km_charge=0.0,
        kilometrage=0.0,
        kilometrage_total=0.0,
        origine=vehicule.last_adresse if vehicule else "Base LSS — Antananarivo",
        etapes=[{"etat": "INITIALISATION_OT", "ts": iso(ts),
                 "lieu": (vehicule.last_adresse if vehicule else None) or "Base LSS — Antananarivo",
                 "zone": "BASETNR"}]
    )
    db.add(m)
    db.flush()
    suivi.mission_id = m.id
    suivi.statut_camion = StatutCamion.VIDE
    if numero_ot:
        suivi.numero_ot = numero_ot
    if distributeur:
        suivi.distributeur = distributeur
    if produit:
        suivi.produit = produit
    if depot_prevu:
        suivi.depot_recepteur = depot_prevu
    log.info("Mission %s (%s) rattachée à l'OT pour %s -> Statut Camion VIDE (heure_debut en attente départ physique)",
             code, numero_ot or "Sans OT", vehicule.plaque if vehicule else "?")
    if PUBLISH_ENABLED["on"]:
        publish("mission.new", s_mission(m))
    return m


def transition_statut(db, suivi: SuiviJournalier, vehicule: Vehicule,
                      ancien: str | None, nouveau: str | None, ts: datetime):
    """Segmentation logistique automatique (§3) :
    - LIBRE -> VIDE (attribution OT / départ transit vers Tamatave)
    - VIDE -> CHARGÉ (chargement GRT)
    - CHARGÉ -> LIBRE (déchargement validé)
    """
    if ancien == nouveau:
        return
    jour = suivi.date_jour

    if nouveau == StatutCamion.VIDE.value and ancien != StatutCamion.VIDE.value:
        initialiser_ou_maj_mission(db, suivi, vehicule, suivi.numero_ot,
                                   suivi.distributeur, suivi.produit,
                                   suivi.depot_recepteur, ts)

    elif nouveau == StatutCamion.CHARGE.value and ancien == StatutCamion.VIDE.value:
        m = _mission_ouverte(db, vehicule.id, jour)
        if m:
            m.statut_camion_actuel = "CHARGE"
            m.heure_chargement = ts
            m.etapes = (m.etapes or []) + [{"etat": "CHARGEMENT_EFFECTUE", "ts": iso(ts),
                                            "lieu": vehicule.last_adresse or "Galana Rafinérie Terminale (GRT)",
                                            "zone": "GRT"}]
            m.produit = suivi.produit or m.produit
            m.depot = suivi.depot_recepteur or m.depot
            m.depot_prevu = suivi.depot_recepteur or m.depot_prevu
            m.numero_ot = suivi.numero_ot or m.numero_ot
            m.distributeur = suivi.distributeur or m.distributeur
            if PUBLISH_ENABLED["on"]:
                publish("mission.update", s_mission(m))

    elif nouveau == StatutCamion.LIBRE.value and ancien in (StatutCamion.CHARGE.value, StatutCamion.VIDE.value):
        m = _mission_ouverte(db, vehicule.id, jour)
        if m:
            m.etapes = (m.etapes or []) + [{"etat": "DECHARGEMENT_EFFECTUE", "ts": iso(ts),
                                            "lieu": vehicule.last_adresse or "Dépôt récepteur",
                                            "zone": normaliser_code_depot(m.depot_effectif or m.depot_prevu) or "DEPOT"}]
            m.statut = StatutMission.TERMINEE
            m.statut_camion_actuel = "LIBRE"
            m.heure_fin = ts
            if m.heure_debut:
                m.duree_s = int((ts - m.heure_debut).total_seconds())
            suivi.mission_id = None
            log.info("Mission %s n°%s terminée", vehicule.plaque, m.numero_mission_du_jour)
            if PUBLISH_ENABLED["on"]:
                publish("mission.update", s_mission(m))
        # Effacement automatique des informations OT dans l'onglet suivi journalier
        suivi.numero_ot = None
        suivi.distributeur = None
        suivi.produit = None
        suivi.depot_recepteur = None


def rattraper_missions_7j(db, maintenant: datetime | None = None) -> dict:
    """Collecte rétrospective et reconstruction automatique des missions sur les 7 derniers jours (§3).
    1. Plage de requêtage : NOW() - 7 jours -> NOW()
    2. Séquence de reconstruction rétrospective :
       - Retrouver l'événement de sortie BASETNR -> date_debut
       - Retrouver la sortie GRT -> date_chargement et statut_camion_actuel = "CHARGE"
       - Retrouver l'entrée/déchargement au Dépôt Récepteur -> date_fin et statut = "TERMINEE" (camion LIBRE)
    3. Anti-Doublon / Upsert :
       - Vérifier si la mission existe déjà par (Immatriculation, date_debut)
       - Si elle existe -> màj des horodatages manquants (date_chargement, date_fin, km, depot_effectif)
       - Si elle n'existe pas -> insérer la mission reconstituée
    """
    maintenant = maintenant or now_local()
    debut_7j_dt = datetime.combine(maintenant.date() - timedelta(days=7), time.min)

    stats = {"creees": 0, "mises_a_jour": 0, "total_traites": 0}

    vehicules = db.scalars(select(Vehicule)).all()
    if not vehicules:
        return stats

    for vehicule in vehicules:
        evs = list(db.scalars(
            select(EvenementGPS).where(
                EvenementGPS.vehicule_id == vehicule.id,
                EvenementGPS.horodatage >= debut_7j_dt,
                EvenementGPS.horodatage <= maintenant
            ).order_by(EvenementGPS.horodatage.asc())
        ).all())

        suivis = list(db.scalars(
            select(SuiviJournalier).where(
                SuiviJournalier.vehicule_id == vehicule.id,
                SuiviJournalier.date_jour >= debut_7j_dt.date(),
                SuiviJournalier.date_jour <= maintenant.date()
            ).order_by(SuiviJournalier.date_jour.asc())
        ).all())

        historiques = list(db.scalars(
            select(HistoriqueJournalier).where(
                HistoriqueJournalier.vehicule_id == vehicule.id,
                HistoriqueJournalier.date_jour >= debut_7j_dt.date(),
                HistoriqueJournalier.date_jour <= maintenant.date()
            ).order_by(HistoriqueJournalier.date_jour.asc())
        ).all())

        jours = [debut_7j_dt.date() + timedelta(days=i) for i in range(8)]
        for j in jours:
            if j > maintenant.date():
                continue

            s_j = next((s for s in suivis if s.date_jour == j), None)
            h_j = next((h for h in historiques if h.date_jour == j), None)
            donnees_h = h_j.donnees if (h_j and h_j.donnees) else {}

            missions_j = list(db.scalars(select(Mission).where(
                Mission.vehicule_id == vehicule.id,
                Mission.date_jour == j
            )).all())

            numero_ot = (s_j.numero_ot if s_j else None) or donnees_h.get("numero_ot") or (missions_j[0].numero_ot if missions_j else None)
            produit = (s_j.produit if s_j else None) or donnees_h.get("produit") or (missions_j[0].produit if missions_j else None)
            distributeur = (s_j.distributeur if s_j else None) or donnees_h.get("distributeur") or (missions_j[0].distributeur if missions_j else None)
            depot_prev = (s_j.depot_recepteur if s_j else None) or donnees_h.get("depot_recepteur") or (missions_j[0].depot_prevu if missions_j else None)
            cond_id = (s_j.conducteur_id if s_j else None) or (h_j.conducteur_id if h_j else None) or (missions_j[0].conducteur_id if missions_j else None) or vehicule.conducteur_actuel_id

            evs_j = [e for e in evs if e.horodatage.date() == j]

            trajets_j = []
            if s_j and s_j.trajets:
                trajets_j = [t for t in s_j.trajets if t.heure_debut is not None]
            elif donnees_h and donnees_h.get("trajets"):
                trajets_j = donnees_h.get("trajets") or []

            ts_depart_base = None
            ts_sortie_grt = None
            ts_dechargement = None
            depot_detecte = depot_prev

            # 1. Analyse chronologique des événements GPS
            if evs_j:
                dans_base = True
                dans_grt = False
                for ev in evs_j:
                    adr_l = (ev.adresse or "").lower()
                    z = detecter_zone_logistique(ev.latitude, ev.longitude, ev.adresse)
                    z_t, z_c, z_n = z["type"], z["code"], z["nom"]
                    
                    if dans_base:
                        if ("sortie" in adr_l and "base" in adr_l) or (z_t != "BASETNR" and "base lss" not in adr_l and "base tana" not in adr_l):
                            ts_depart_base = ev.horodatage
                            dans_base = False
                    
                    if (z_t == "GRT" and z_c == "GRT") or "sortie grt" in adr_l or "chargement grt" in adr_l:
                        dans_grt = True
                        ts_sortie_grt = ev.horodatage
                    elif dans_grt and z_t != "GRT":
                        ts_sortie_grt = ev.horodatage
                        dans_grt = False
                    
                    if z_t == "DEPOT_RECEPTEUR" and z_c in DEPOTS_DECHARGEMENT_CODES:
                        # RÈGLE FONDAMENTALE : Un déchargement ne peut exister QUE si le camion a DÉJÀ chargé à GRT !
                        if not ts_sortie_grt or dans_grt:
                            # En cours d'aller vers Tamatave / GRT : Moramanga ou tout autre lieu n'est JAMAIS un déchargement
                            pass
                        else:
                            code_p = normaliser_code_depot(depot_prev)
                            # Moramanga traversé en transit RN2 vers Tana/Sud n'est pas un déchargement terminal
                            if z_c == "DMMG" and code_p in ("DABI", "DSNR", "DABE", "DFIA", "DMDV", "DMKR"):
                                pass
                            elif code_p and z_c == code_p:
                                depot_detecte = nom_officiel_depot(z_c)
                                if ev.type_evenement == TypeEvenement.ARRET or ev.vitesse < 3:
                                    ts_dechargement = ev.horodatage

            # 2. Complément depuis les trajets
            if not ts_depart_base and trajets_j:
                t1 = trajets_j[0]
                t1_deb = t1.heure_debut if hasattr(t1, "heure_debut") else (
                    datetime.fromisoformat(t1["heure_debut"]) if isinstance(t1.get("heure_debut"), str) else None)
                if t1_deb:
                    ts_depart_base = t1_deb

            if not ts_sortie_grt and ts_depart_base and (numero_ot or (s_j and s_j.statut_camion == StatutCamion.CHARGE)):
                if len(trajets_j) >= 2:
                    t_grt = trajets_j[0]
                    t_grt_fin = t_grt.heure_fin if hasattr(t_grt, "heure_fin") else (
                        datetime.fromisoformat(t_grt["heure_fin"]) if isinstance(t_grt.get("heure_fin"), str) else None)
                    if t_grt_fin:
                        ts_sortie_grt = t_grt_fin + timedelta(minutes=45)
                elif j < maintenant.date():
                    ts_sortie_grt = ts_depart_base + timedelta(hours=4)

            if not ts_dechargement and j < maintenant.date() and ts_sortie_grt:
                if len(trajets_j) >= 2:
                    t_last = trajets_j[-1]
                    t_last_fin = t_last.heure_fin if hasattr(t_last, "heure_fin") else (
                        datetime.fromisoformat(t_last["heure_fin"]) if isinstance(t_last.get("heure_fin"), str) else None)
                    if t_last_fin:
                        ts_dechargement = t_last_fin

            # Calcul des kilométrages
            if s_j:
                km_tot_j = float(s_j.km_parcourus or 0.0)
            elif donnees_h:
                km_tot_j = float(donnees_h.get("km_parcourus") or 0.0)
            else:
                km_tot_j = 0.0

            if ts_sortie_grt and ts_dechargement:
                km_v = round(km_tot_j * 0.45, 1)
                km_c = round(km_tot_j * 0.55, 1)
            elif ts_sortie_grt:
                km_v = round(km_tot_j * 0.5, 1)
                km_c = round(km_tot_j * 0.5, 1)
            else:
                km_v = round(km_tot_j, 1)
                km_c = 0.0

            # 3. Anti-Doublon / Upsert (Règle 8 : intégrité absolue, jamais de création fictive)
            if missions_j or numero_ot or (j == maintenant.date() and s_j and s_j.numero_ot):
                stats["total_traites"] += 1
                missions_exist = list(db.scalars(
                    select(Mission).where(
                        Mission.vehicule_id == vehicule.id,
                        Mission.date_jour == j
                    ).order_by(Mission.numero_mission_du_jour.asc())
                ).all())

                # Rapprochement prioritaire par (Immatriculation, date_debut)
                m_exist = None
                if ts_depart_base:
                    for me in missions_exist:
                        if me.heure_debut and abs((me.heure_debut - ts_depart_base).total_seconds()) <= 7200:
                            m_exist = me
                            break
                if not m_exist:
                    for me in missions_exist:
                        if not me.heure_chargement or not me.heure_fin:
                            m_exist = me
                            break
                if not m_exist and missions_exist:
                    m_exist = missions_exist[0]
                    for me in missions_exist:
                        if not me.heure_chargement or not me.heure_fin:
                            m_exist = me
                            break
                if not m_exist and missions_exist:
                    m_exist = missions_exist[0]

                if m_exist:
                    maj = False
                    # Pour aujourd'hui, alignement strict avec les saisies manuelles de SuiviJournalier
                    if j == maintenant.date() and s_j:
                        if s_j.numero_ot and m_exist.numero_ot != s_j.numero_ot:
                            m_exist.numero_ot = s_j.numero_ot
                            m_exist.code_mission = _formater_code_mission(s_j.numero_ot, j, m_exist.numero_mission_du_jour)
                            maj = True
                        if s_j.distributeur and m_exist.distributeur != s_j.distributeur:
                            m_exist.distributeur = s_j.distributeur
                            maj = True
                        if s_j.produit and m_exist.produit != s_j.produit:
                            m_exist.produit = s_j.produit
                            maj = True
                        if s_j.depot_recepteur:
                            nom_d = nom_officiel_depot(s_j.depot_recepteur) or s_j.depot_recepteur
                            if m_exist.depot_prevu != nom_d:
                                m_exist.depot = nom_d
                                m_exist.depot_prevu = nom_d
                                if not m_exist.est_deviee:
                                    m_exist.depot_effectif = nom_d
                                maj = True
                        stat_c_s = s_j.statut_camion.value if hasattr(s_j.statut_camion, "value") else str(s_j.statut_camion)
                        if stat_c_s in ("VIDE", "CHARGE"):
                            m_exist.statut_camion_actuel = stat_c_s
                            m_exist.statut = StatutMission.DEVIEE if m_exist.est_deviee else StatutMission.EN_COURS
                            m_exist.heure_fin = None
                            maj = True
                        elif stat_c_s == "LIBRE" and m_exist.heure_fin:
                            m_exist.statut_camion_actuel = "LIBRE"
                            m_exist.statut = StatutMission.TERMINEE
                            maj = True

                    if not m_exist.heure_debut and ts_depart_base:
                        m_exist.heure_debut = ts_depart_base
                        maj = True
                    if not m_exist.heure_chargement and ts_sortie_grt:
                        m_exist.heure_chargement = ts_sortie_grt
                        if m_exist.statut != StatutMission.TERMINEE:
                            m_exist.statut_camion_actuel = "CHARGE"
                        maj = True
                    if not m_exist.heure_fin and ts_dechargement:
                        if j < maintenant.date():
                            m_exist.heure_fin = ts_dechargement
                            m_exist.statut = StatutMission.TERMINEE
                            m_exist.statut_camion_actuel = "LIBRE"
                            if m_exist.heure_debut:
                                m_exist.duree_s = int((ts_dechargement - m_exist.heure_debut).total_seconds())
                            maj = True
                        else:
                            # Pour aujourd'hui : l'arrivée au dépôt ne clôture JAMAIS automatiquement la mission !
                            if not m_exist.heure_fin and m_exist.statut != StatutMission.TERMINEE:
                                m_exist.validation_dechargement = "EN_ATTENTE"
                                maj = True
                    if not m_exist.depot_effectif and depot_detecte:
                        m_exist.depot_effectif = depot_detecte
                        maj = True
                    if (not m_exist.km_vide or m_exist.km_vide == 0.0) and km_v > 0:
                        m_exist.km_vide = km_v
                        maj = True
                    if (not m_exist.km_charge or m_exist.km_charge == 0.0) and km_c > 0:
                        m_exist.km_charge = km_c
                        maj = True
                    if not m_exist.kilometrage_total or m_exist.kilometrage_total == 0.0:
                        m_exist.kilometrage_total = round((m_exist.km_vide or 0.0) + (m_exist.km_charge or 0.0), 1)
                        m_exist.kilometrage = m_exist.kilometrage_total
                        maj = True
                    if maj:
                        stats["mises_a_jour"] += 1
                elif numero_ot or (j < maintenant.date() and evs_j and ts_depart_base):
                    code = _formater_code_mission(numero_ot, j, 1)
                    est_term = bool(ts_dechargement) and (j < maintenant.date())
                    statut_m = StatutMission.TERMINEE if est_term else StatutMission.EN_COURS
                    statut_c = "LIBRE" if est_term else ("CHARGE" if ts_sortie_grt else (StatutCamion.VIDE.value if numero_ot else StatutCamion.LIBRE.value))
                    
                    etapes = []
                    if ts_depart_base:
                        etapes.append({"etat": "DEPART_BASE", "ts": iso(ts_depart_base), "lieu": "Sortie Base Tana", "zone": "BASETNR"})
                    if ts_sortie_grt:
                        etapes.append({"etat": "CHARGEMENT_EFFECTUE", "ts": iso(ts_sortie_grt), "lieu": "GRT (GALANA RAFINERIE TERMINALE)", "zone": "GRT"})
                    if ts_dechargement and est_term:
                        nom_dep_eff = nom_officiel_depot(depot_detecte) or "Depot Soanierana (DSNR)"
                        code_dep_eff = normaliser_code_depot(depot_detecte) or "DSNR"
                        etapes.append({"etat": "DECHARGEMENT_EFFECTUE", "ts": iso(ts_dechargement), "lieu": nom_dep_eff, "zone": code_dep_eff})

                    duree = int((ts_dechargement - ts_depart_base).total_seconds()) if (est_term and ts_dechargement and ts_depart_base) else 0

                    m_nouv = Mission(
                        id=uid(),
                        code_mission=code,
                        date_jour=j,
                        conducteur_id=cond_id,
                        vehicule_id=vehicule.id,
                        numero_mission_du_jour=1,
                        statut=statut_m,
                        statut_camion_actuel=statut_c,
                        heure_debut=ts_depart_base,
                        heure_chargement=ts_sortie_grt,
                        heure_fin=ts_dechargement if est_term else None,
                        duree_s=duree,
                        numero_ot=numero_ot,
                        distributeur=distributeur,
                        produit=produit,
                        depot=depot_prev or depot_detecte,
                        depot_prevu=depot_prev or depot_detecte,
                        depot_effectif=depot_detecte or depot_prev,
                        est_deviee=False,
                        validation_dechargement="VALIDÉ" if est_term else ("EN_ATTENTE" if ts_dechargement else "NON_REQUIS"),
                        km_vide=km_v,
                        km_charge=km_c,
                        kilometrage=round(km_v + km_c, 1),
                        kilometrage_total=round(km_v + km_c, 1),
                        origine="Base LSS — Antananarivo",
                        etapes=etapes
                    )
                    db.add(m_nouv)
                    stats["creees"] += 1

    db.commit()
    # Réconciliation et persistance des alertes non traitées des jours passés
    reconcilier_alertes_missions_en_attente(db)
    log.info("Rattrapage missions 7 jours terminé : %s", stats)
    return stats


def reconcilier_alertes_missions_en_attente(db: Session) -> int:
    """Restaure et persiste les alertes LÉGITIMES non traitées des missions réelles.
    RÈGLE ABSOLUE DE STABILITÉ ET D'INTÉGRITÉ :
      - Les alertes actives restent stables et persistantes en base (NOUVELLE / VUE).
      - Une alerte n'est clôturée (TRAITEE) QUE si la mission est formellement terminée (StatutMission.TERMINEE)
        ou si l'action a été expressément validée par l'opérateur.
      - Aucun clignotement ou purge intempestive en boucle.
    """
    nb_creees = 0
    now = now_local()

    # 1. Clôture des alertes UNIQUEMENT pour les missions formellement terminées
    missions_terminees_ids = {m.id for m in db.scalars(select(Mission).where(Mission.statut == StatutMission.TERMINEE)).all()}
    vehicules_termines_ids = {m.vehicule_id for m in db.scalars(select(Mission).where(Mission.statut == StatutMission.TERMINEE)).all()}

    # Clôture des alertes de déchargement pour les camions dont la mission est déjà terminée
    if vehicules_termines_ids:
        db.query(Alerte).filter(
            Alerte.type.in_([TypeAlerte.VALIDATION_DECHARGEMENT, TypeAlerte.DEVIATION_DETECTEE]),
            Alerte.vehicule_id.in_(list(vehicules_termines_ids)),
            Alerte.statut.in_([StatutAlerte.NOUVELLE, StatutAlerte.VUE])
        ).update({Alerte.statut: StatutAlerte.TRAITEE}, synchronize_session=False)

    # 2. Réconciliation stricte sur les missions réellement actives
    debut_recherche = now.date() - timedelta(days=30)
    missions_actives = list(db.scalars(
        select(Mission).where(
            Mission.date_jour >= debut_recherche,
            Mission.statut.in_([StatutMission.EN_COURS, StatutMission.DEVIEE])
        ).order_by(Mission.date_jour.asc(), Mission.heure_debut.asc())
    ).all())

    for m in missions_actives:
        v = db.get(Vehicule, m.vehicule_id)
        if not v:
            continue

        etapes = m.etapes or []
        s_suivi = db.scalar(select(SuiviJournalier).where(SuiviJournalier.vehicule_id == v.id, SuiviJournalier.date_jour == m.date_jour))
        sit_l = ((s_suivi.situation if s_suivi else "") or "").lower()
        adr_l = (v.last_adresse or "").lower()
        z_geo = detecter_zone_logistique(v.last_lat, v.last_lng, v.last_adresse)

        # Détection de présence GRT : soit par étape ENTREE_GRT, soit par télématique/situation
        est_a_grt = (z_geo["type"] == "GRT") or ("grt" in adr_l) or ("chargement" in sit_l) or any(e.get("etat") == "ENTREE_GRT" for e in etapes)
        est_deja_charge = any(e.get("etat") == "CHARGEMENT_EFFECTUE" for e in etapes) or (m.heure_chargement is not None and m.validation_chargement == "VALIDÉ")

        # Alerte 1 : MISSION_SANS_OT (camion réellement à GRT sans OT)
        if est_a_grt and (not m.numero_ot or m.statut_camion_actuel == "LIBRE"):
            ts_grt = None
            for e in etapes:
                if e.get("etat") == "ENTREE_GRT" and e.get("ts"):
                    try:
                        ts_grt = datetime.fromisoformat(e["ts"])
                        break
                    except Exception:
                        pass
            ts_alerte = (ts_grt + timedelta(minutes=15)) if ts_grt else (m.heure_debut or now)

            existe = db.scalar(
                select(Alerte).where(
                    Alerte.vehicule_id == v.id,
                    Alerte.type == TypeAlerte.MISSION_SANS_OT,
                    Alerte.statut.in_([StatutAlerte.NOUVELLE, StatutAlerte.VUE])
                )
            )
            if not existe:
                creer_alerte(
                    db, TypeAlerte.MISSION_SANS_OT, GraviteAlerte.MOYENNE,
                    f"OT manquant — Le camion {v.plaque} est à GRT (GALANA RAFINERIE TERMINALE) sans Ordre de Transport enregistré",
                    ts=ts_alerte, vehicule_id=v.id, conducteur_id=m.conducteur_id,
                    lien_module="/missions"
                )
                nb_creees += 1

        # Alerte 2 : VALIDATION_CHARGEMENT (camion réellement à GRT ≥ 30 min ou en chargement)
        if est_a_grt and not est_deja_charge and m.validation_chargement != "VALIDÉ":
            m.validation_chargement = "EN_ATTENTE"
            ts_grt = None
            for e in etapes:
                if e.get("etat") == "ENTREE_GRT" and e.get("ts"):
                    try:
                        ts_grt = datetime.fromisoformat(e["ts"])
                        break
                    except Exception:
                        pass
            ts_alerte = (ts_grt + timedelta(minutes=30)) if ts_grt else (m.heure_debut or now)

            existe = db.scalar(
                select(Alerte).where(
                    Alerte.vehicule_id == v.id,
                    Alerte.type == TypeAlerte.VALIDATION_CHARGEMENT,
                    Alerte.statut.in_([StatutAlerte.NOUVELLE, StatutAlerte.VUE])
                )
            )
            if not existe:
                creer_alerte(
                    db, TypeAlerte.VALIDATION_CHARGEMENT, GraviteAlerte.INFORMATION,
                    f"Validation requise : Chargement GRT (GALANA RAFINERIE TERMINALE) pour {v.plaque}",
                    ts=ts_alerte, vehicule_id=v.id, conducteur_id=m.conducteur_id,
                    lien_module="/missions"
                )
                nb_creees += 1

        # Détection de présence Dépôt Récepteur Officiel : soit par étape, soit par télématique/situation
        code_depot = z_geo["code"] if z_geo["type"] == "DEPOT_RECEPTEUR" else normaliser_code_depot(m.depot_effectif or m.depot_prevu or (s_suivi.depot_recepteur if s_suivi else None))
        est_au_depot = (z_geo["type"] == "DEPOT_RECEPTEUR" and z_geo["code"] in DEPOTS_DECHARGEMENT_CODES) or \
                       ("déchargement" in sit_l or "dechargement" in sit_l or "dépôt" in sit_l or "depot" in sit_l) or \
                       any(e.get("etat") in ("ARRIVEE_DEPOT_RECEPTEUR", "DEVIATION_DETECTEE") and e.get("zone") in DEPOTS_DECHARGEMENT_CODES for e in etapes)

        # Alerte 3 : VALIDATION_DECHARGEMENT (camion au dépôt récepteur officiel avec statut CHARGÉ)
        if est_au_depot and m.statut_camion_actuel in ("CHARGE", "CHARGÉ") and m.validation_dechargement not in ("VALIDÉ", "INVALIDÉ") and m.statut != StatutMission.TERMINEE:
            m.validation_dechargement = "EN_ATTENTE"
            ts_depot = None
            lieu_depot = nom_officiel_depot(code_depot or m.depot_effectif or m.depot_prevu) or "Depot Soanierana (DSNR)"
            for e in reversed(etapes):
                if e.get("etat") in ("ARRIVEE_DEPOT_RECEPTEUR", "DEVIATION_DETECTEE") and e.get("ts") and e.get("zone") in DEPOTS_DECHARGEMENT_CODES:
                    try:
                        ts_depot = datetime.fromisoformat(e["ts"])
                        if e.get("lieu"):
                            lieu_depot = nom_officiel_depot(e["lieu"]) or e["lieu"]
                        break
                    except Exception:
                        pass
            ts_alerte = (ts_depot + timedelta(hours=3)) if ts_depot else (m.heure_debut or now)

            existe = db.scalar(
                select(Alerte).where(
                    Alerte.vehicule_id == v.id,
                    Alerte.type == TypeAlerte.VALIDATION_DECHARGEMENT,
                    Alerte.statut.in_([StatutAlerte.NOUVELLE, StatutAlerte.VUE])
                )
            )
            if not existe:
                creer_alerte(
                    db, TypeAlerte.VALIDATION_DECHARGEMENT, GraviteAlerte.MOYENNE,
                    f"Validation requise : Déchargement au {lieu_depot} pour {v.plaque}",
                    ts=ts_alerte, vehicule_id=v.id, conducteur_id=m.conducteur_id,
                    lien_module="/missions"
                )
                nb_creees += 1

        # Alerte 4 : DEVIATION_DETECTEE
        if m.est_deviee or m.statut == StatutMission.DEVIEE:
            ts_dev = None
            for e in reversed(etapes):
                if e.get("etat") == "DEVIATION_DETECTEE" and e.get("ts") and e.get("zone") in DEPOTS_DECHARGEMENT_CODES:
                    try:
                        ts_dev = datetime.fromisoformat(e["ts"])
                        break
                    except Exception:
                        pass
            ts_alerte = ts_dev or (m.heure_debut or now)

            existe = db.scalar(
                select(Alerte).where(
                    Alerte.vehicule_id == v.id,
                    Alerte.type == TypeAlerte.DEVIATION_DETECTEE,
                    Alerte.statut.in_([StatutAlerte.NOUVELLE, StatutAlerte.VUE])
                )
            )
            if not existe:
                creer_alerte(
                    db, TypeAlerte.DEVIATION_DETECTEE, GraviteAlerte.CRITIQUE,
                    f"Déviation détectée pour {v.plaque} : nouveau dépôt {nom_officiel_depot(m.depot_effectif) or m.depot_effectif or '—'} (prévu : {nom_officiel_depot(m.depot_prevu) or m.depot_prevu or '—'})",
                    ts=ts_alerte, vehicule_id=v.id, conducteur_id=m.conducteur_id,
                    lien_module="/missions"
                )
                nb_creees += 1

    db.commit()
    if nb_creees > 0:
        log.info("Réconciliation des alertes missions des jours passés : %d alerte(s) persistante(s) restaurée(s)", nb_creees)
    return nb_creees


CHAMPS_A = {"conducteur_id"}
CHAMPS_B = {"situation", "statut_camion", "depot_recepteur", "distributeur",
            "produit", "numero_ot"}
CHAMPS_C = {"emplacement_j_moins_1", "position_08h", "position_10h", "position_12h",
            "position_14h", "position_16h", "position_18h",
            # §0vicies decies N2 (31/08/2026) — relevés du soir (auto + éditables)
            "position_20h", "position_22h"}


def appliquer_champs_suivi(db, suivi: SuiviJournalier, champs: dict,
                           ts: datetime | None = None) -> dict:
    """Application centralisée des saisies Parties A/B/C (utilisée par l'API et
    le simulateur) ; déclenche la chaîne de synchronisation §9 et garantit l'anti-doublon."""
    ts = ts or now_local()
    vehicule = db.get(Vehicule, suivi.vehicule_id)
    diffs = {}
    ancien_statut = suivi.statut_camion.value if suivi.statut_camion else None

    for cle, val in champs.items():
        if cle not in CHAMPS_A | CHAMPS_B | CHAMPS_C:
            continue
        if cle == "conducteur_id":
            nouv_cid = val or None
            if suivi.conducteur_id != nouv_cid:
                diffs["conducteur_id"] = {"avant": suivi.conducteur_id, "apres": nouv_cid}
                if nouv_cid:
                    # Règle stricte anti-doublon : détacher ce chauffeur de tout autre camion le même jour
                    for s_autre in db.scalars(select(SuiviJournalier).where(
                            SuiviJournalier.date_jour == suivi.date_jour,
                            SuiviJournalier.conducteur_id == nouv_cid,
                            SuiviJournalier.id != suivi.id)).all():
                        s_autre.conducteur_id = None
                        s_autre.conducteur_origine = None
                        if PUBLISH_ENABLED["on"]:
                            publish("suivi.update", {"suivi": s_suivi(s_autre, get_seuils(db))})
                    suivi.conducteur_id = nouv_cid
                    suivi.conducteur_origine = "MANUEL"
                else:
                    suivi.conducteur_id = None
                    suivi.conducteur_origine = None
            continue

        if cle == "statut_camion" and val:
            val_str = str(val.value if hasattr(val, "value") else val).upper()
            if val_str in ("CHARGE", "CHARGÉ"):
                val = StatutCamion.CHARGE
            elif val_str == "VIDE":
                val = StatutCamion.VIDE
            else:
                val = StatutCamion.LIBRE
        ancienne = getattr(suivi, cle)
        ancienne_s = ancienne.value if hasattr(ancienne, "value") else ancienne
        nouvelle_s = val.value if hasattr(val, "value") else val
        if ancienne_s != nouvelle_s:
            diffs[cle] = {"avant": ancienne_s, "apres": nouvelle_s}
            setattr(suivi, cle, val)

    nouveau_statut = suivi.statut_camion.value if suivi.statut_camion else None
    if "statut_camion" in champs and ancien_statut != nouveau_statut:
        transition_statut(db, suivi, vehicule, ancien_statut, nouveau_statut, ts)

    if nouveau_statut == StatutCamion.LIBRE.value and "statut_camion" in champs:
        # Passage au statut LIBRE -> effacement automatique des informations OT
        if "numero_ot" not in champs:
            suivi.numero_ot = None
        if "distributeur" not in champs:
            suivi.distributeur = None
        if "produit" not in champs:
            suivi.produit = None
        if "depot_recepteur" not in champs:
            suivi.depot_recepteur = None

    # Synchronisation stricte et bidirectionnelle 1:1 vers l'onglet Missions
    if any(k in champs for k in ("numero_ot", "produit", "depot_recepteur", "distributeur", "statut_camion")):
        stat_c_str = suivi.statut_camion.value if hasattr(suivi.statut_camion, "value") else str(suivi.statut_camion or "LIBRE")
        if suivi.numero_ot or stat_c_str in ("VIDE", "CHARGE"):
            m = initialiser_ou_maj_mission(
                db, suivi, vehicule,
                suivi.numero_ot, suivi.distributeur, suivi.produit,
                suivi.depot_recepteur, ts
            )
            if m:
                m.statut_camion_actuel = "CHARGE" if stat_c_str in ("CHARGE", "CHARGÉ") else ("VIDE" if stat_c_str == "VIDE" else "LIBRE")
                if suivi.numero_ot:
                    m.numero_ot = suivi.numero_ot
                    m.code_mission = _formater_code_mission(suivi.numero_ot, suivi.date_jour, m.numero_mission_du_jour)
                if suivi.distributeur:
                    m.distributeur = suivi.distributeur
                if suivi.produit:
                    m.produit = suivi.produit
                if suivi.depot_recepteur:
                    nom_d = nom_officiel_depot(suivi.depot_recepteur) or suivi.depot_recepteur
                    m.depot = nom_d
                    m.depot_prevu = nom_d
                    if not m.est_deviee:
                        m.depot_effectif = nom_d
                suivi.mission_id = m.id
                if PUBLISH_ENABLED["on"]:
                    publish("mission.update", s_mission(m))
        elif stat_c_str == "LIBRE" and not suivi.numero_ot:
            if suivi.mission_id:
                m_anc = db.get(Mission, suivi.mission_id)
                if m_anc and m_anc.statut != StatutMission.TERMINEE:
                    m_anc.statut_camion_actuel = "LIBRE"
                    if PUBLISH_ENABLED["on"]:
                        publish("mission.update", s_mission(m_anc))

    suivi.updated_at = now_local()
    db.commit()
    if PUBLISH_ENABLED["on"]:
        publish("suivi.update", {"suivi": s_suivi(suivi, get_seuils(db))})
    return diffs


# ------------------------------------------------------------------ surveillance périodique
SITUATIONS_TRANQUILLES = (
    "chargement", "déchargement", "repos", "maintenance", "barémage",
    "contrôle", "malade", "congé", "formation", "immobilisé", "coach",
    "indisponible", "vitting", "compteur", "suspendu", "attente", "ostie",
    "caméra", "certificat", "cyclone",
)


# Correctif v1.46 — retard d'ingestion global (alerte COLLECTE_RETARD).
SEUIL_RETARD_COLLECTE_S = int(os.getenv("SEUIL_RETARD_COLLECTE_S", "900"))


def retard_collecte_s(db, maintenant: datetime) -> int | None:
    """Écart en secondes entre `maintenant` et le dernier événement GPS des
    portails ingéré. None si aucun événement sur les dernières 24 h."""
    dernier = db.scalar(select(func.max(EvenementGPS.horodatage)).where(
        EvenementGPS.source.in_([SourceEvenement.MZONEX,
                                 SourceEvenement.CAMTRACKPRO]),
        EvenementGPS.horodatage >= maintenant - timedelta(hours=24)))
    if dernier is None:
        return None
    return int((maintenant - dernier).total_seconds())


def boucle_surveillance():
    """Chien de garde (appelé périodiquement) : GPS hors ligne, camion
    immobile anormal, missions retardées (§6.3/§6.5)."""
    db = SessionLocal()
    try:
        seuils = get_seuils(db)
        now = now_local()
        jour = now.date()

        vehicules = db.scalars(select(Vehicule).where(Vehicule.statut == "ACTIF")).all()
        for v in vehicules:
            if v.last_event_at is None or v.last_event_at.date() != jour:
                continue
            delta = (now - v.last_event_at).total_seconds()

            # GPS hors ligne
            if delta > seuils["SEUIL_GPS_HORS_LIGNE"]:
                if not _alerte_recente(db, TypeAlerte.GPS_HORS_LIGNE, v.id, 90):
                    a = creer_alerte(
                        db, TypeAlerte.GPS_HORS_LIGNE, GraviteAlerte.CRITIQUE,
                        f"GPS hors ligne — {v.plaque} ({v.gps_associe or 'OBC'}) : aucune donnée "
                        f"depuis {int(delta // 60)} min (dernier point : {v.last_adresse or '—'})",
                        vehicule_id=v.id, conducteur_id=v.conducteur_actuel_id,
                        lien_module="/dashboard")
                    if PUBLISH_ENABLED["on"]:
                        publish("alerte.new", s_alerte(a))

            # Immobilisation anormale (moteur ON, à l'arrêt, hors contexte justifié)
            if (v.last_vitesse or 0) <= 1 and v.moteur_on and delta > seuils["SEUIL_IMMOBILISATION_ALERTE"]:
                suivi = db.scalar(select(SuiviJournalier).where(
                    SuiviJournalier.vehicule_id == v.id, SuiviJournalier.date_jour == jour))
                situation = (suivi.situation or "").lower() if suivi else ""
                if not any(mot in situation for mot in SITUATIONS_TRANQUILLES):
                    if not _alerte_recente(db, TypeAlerte.CAMION_IMMOBILE, v.id, 120):
                        a = creer_alerte(
                            db, TypeAlerte.CAMION_IMMOBILE, GraviteAlerte.MOYENNE,
                            f"Camion immobile anormalement — {v.plaque} moteur ON à l'arrêt depuis "
                            f"{int(delta // 60)} min à {v.last_adresse or '—'}",
                            vehicule_id=v.id, conducteur_id=v.conducteur_actuel_id,
                            lien_module=f"/suivi?date={jour.isoformat()}&vehicule={v.id}")
                        if PUBLISH_ENABLED["on"]:
                            publish("alerte.new", s_alerte(a))

        # Correctif v1.46 — retard d'ingestion de la collecte portails : les
        # portails peuvent être sains pendant que la boucle locale traîne ou
        # se bloque (constat du 04/09/2026 : base arrêtée à 12h19, portails à
        # jour jusqu'à 14h59) — alerte visible au tableau, jamais bloquante.
        # Fenêtre 05h–22h : le serveur est normalement éteint la nuit (§8).
        if 5 <= now.hour < 22:
            try:
                retard = retard_collecte_s(db, now)
                if (retard is not None
                        and retard > SEUIL_RETARD_COLLECTE_S
                        and not _alerte_recente(db, TypeAlerte.COLLECTE_RETARD,
                                                None, 60)):
                    a = creer_alerte(
                        db, TypeAlerte.COLLECTE_RETARD, GraviteAlerte.MOYENNE,
                        f"Collecte GPS en retard : dernier événement ingéré il y a "
                        f"{retard // 60} min (seuil {SEUIL_RETARD_COLLECTE_S // 60} min) — "
                        "vérifier la boucle de collecte et les logs portails",
                        lien_module="/suivi")
                    if PUBLISH_ENABLED["on"]:
                        publish("alerte.new", s_alerte(a))
            except Exception:
                log.exception("Contrôle de retard de collecte en échec")

        # Note opérationnelle (Madagascar) : aucune durée standard rigide n'est imposée sur
        # les missions en cours en raison de l'état des axes routiers (RN2, RN7), des temps
        # d'attente variables aux dépôts et des repos hebdomadaires (≥ 24h/45h) pris en cours de route.
        # Les missions restent actives (EN_COURS ou DEVIEE) jusqu'à confirmation physique du déchargement.
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ pré-remplissage Partie C
# §0vicies decies N2 (31/08/2026) — les relevés 08h→22h existent ; 08h–16h
# restent MANUELS (le bouton « Pré-remplir (GPS) » est une simple aide) ;
# 18h/20h/22h sont AUTOMATIQUES (auto_positions_horaires / rattrapage N3).
_HEURES_POSITIONS = ((8, "position_08h"), (10, "position_10h"), (12, "position_12h"),
                     (14, "position_14h"), (16, "position_16h"), (18, "position_18h"),
                     (20, "position_20h"), (22, "position_22h"))
_HEURES_POS_AUTO = ((18, "position_18h"), (20, "position_20h"), (22, "position_22h"))


def prefill_positions_gps(db, jour: date, jusqu_a: datetime | None = None) -> int:
    """Pré-remplit les positions 08h…22h depuis le GPS (aide à la saisie §6.2
    Partie C — la valeur reste modifiable manuellement)."""
    jusqu_a = jusqu_a or now_local()
    suivis = db.scalars(select(SuiviJournalier).where(SuiviJournalier.date_jour == jour)).all()
    remplis = 0
    for s in suivis:
        for h, champ in _HEURES_POSITIONS:
            borne = datetime.combine(jour, datetime.min.time()).replace(hour=h)
            if borne > jusqu_a:
                continue
            ev = db.scalar(select(EvenementGPS).where(
                EvenementGPS.vehicule_id == s.vehicule_id,
                EvenementGPS.horodatage <= borne + timedelta(minutes=45),
                EvenementGPS.horodatage >= borne - timedelta(hours=12),
            ).order_by(EvenementGPS.horodatage.desc()))
            if ev:
                setattr(s, champ, ev.adresse or f"{ev.latitude:.4f},{ev.longitude:.4f}")
                remplis += 1
    db.commit()
    return remplis


# ------------------------------------------------------------------ §0vicies decies N2/N3
def _position_connue_a(db, vehicule_id: str, borne: datetime) -> str | None:
    """Dernière position connue à l'heure dite — SANS limite d'âge (instruction
    exploitant : « le portail affiche toujours le camion là où il est à ce
    moment, même si le boîtier n'émet pas ; prendre ces positions-là »).
    Libellé E4 déjà calculé à l'ingestion (zone portail / « proche <nom> » /
    coordonnées)."""
    ev = db.scalar(select(EvenementGPS).where(
        EvenementGPS.vehicule_id == vehicule_id,
        EvenementGPS.horodatage <= borne,
    ).order_by(EvenementGPS.horodatage.desc()))
    if not ev:
        return None
    return ev.adresse or f"{ev.latitude:.4f},{ev.longitude:.4f}"


def auto_positions_horaires(db, maintenant: datetime | None = None) -> int:
    """N2 — remplit/réécrit automatiquement les relevés 18h/20h/22h de la
    JOURNÉE EN COURS dès que l'heure est passée, avec la dernière position
    connue à l'heure dite. Réécriture libre jusqu'au verrouillage de minuit
    (données de boîtiers muets remontées en retard incluses). Les colonnes
    manuelles (08h→16h) ne sont jamais touchées. Retourne le nb de cellules
    mises à jour."""
    maintenant = maintenant or now_local()
    jour = maintenant.date()
    if maintenant.hour < 18:
        return 0
    suivis = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour)).all()
    maj = 0
    for s in suivis:
        for h, champ in _HEURES_POS_AUTO:
            borne = datetime.combine(jour, datetime.min.time()).replace(hour=h)
            if borne > maintenant:
                continue
            valeur = _position_connue_a(db, s.vehicule_id, borne)
            if valeur and valeur != getattr(s, champ):
                setattr(s, champ, valeur)
                maj += 1
    if maj:
        db.commit()
    return maj


def rattraper_positions_horaires(db, maintenant: datetime | None = None,
                                 jours: int = 7) -> list[str]:
    """N3 — rattrapage des jours PASSÉS (J-1 → J-7) : complète uniquement les
    cellules 18h/20h/22h VIDES (jamais une cellule remplie), audité
    `suivi.position_rattrapee` ligne par ligne. Retourne les ids des suivis
    modifiés (pour régénération d'archive éventuelle par l'appelant)."""
    maintenant = maintenant or now_local()
    auj = maintenant.date()
    modifies: list[str] = []
    for delta in range(1, jours + 1):
        jour = auj - timedelta(days=delta)
        suivis = db.scalars(select(SuiviJournalier).where(
            SuiviJournalier.date_jour == jour)).all()
        for s in suivis:
            details = {}
            for h, champ in _HEURES_POS_AUTO:
                if getattr(s, champ):
                    continue            # jamais d'écrasement d'une cellule remplie
                borne = datetime.combine(jour, datetime.min.time()).replace(hour=h)
                valeur = _position_connue_a(db, s.vehicule_id, borne)
                if valeur:
                    setattr(s, champ, valeur)
                    details[champ] = valeur
            if details:
                db.add(AuditLog(username="collecteur",
                                action="suivi.position_rattrapee",
                                entite="suivi", entite_id=s.id,
                                details={
                                    "plaque": s.vehicule.plaque if s.vehicule else None,
                                    "jour": jour.isoformat(), "cellules": details,
                                    "regle": "§0vicies decies N3 (31/08/2026) : "
                                             "cellules VIDES 18h/20h/22h complétées "
                                             "par la dernière position connue — "
                                             "jamais d'écrasement"}))
                modifies.append(s.id)
    if modifies:
        db.commit()
    return modifies


# ------------------------------------------------------------------ §0unvicies decies O1/O2
import time as _time_mod
_DERNIERES_INTEGRATIONS: dict[str, float] = {}   # throttles (clé → epoch)
_INTEGRATION_SOIR_H = (22, 5)                    # passe du soir : à partir de 22h05


def integration_throttle_ok(cle: str, intervalle_s: int) -> bool:
    """True si la passe « cle » peut repartir (jamais faite ou assez ancienne).
    Les tests vident _DERNIERES_INTEGRATIONS pour re-déclencher à volonté."""
    dernier = _DERNIERES_INTEGRATIONS.get(cle, 0.0)
    ok = (_time_mod.time() - dernier) >= intervalle_s
    if ok:
        _DERNIERES_INTEGRATIONS[cle] = _time_mod.time()
    return ok


def _libelle_point(lat: float, lng: float) -> str:
    """Libellé O3 (géozones des deux portails) ou coordonnées — jamais vide."""
    from .geozones import charger_zones, libelle_position
    charger_zones()
    return libelle_position(lat, lng) or f"{lat:.4f},{lng:.4f}"


def integrer_positions_portails(db, jour: date, maintenant: datetime | None = None,
                                reparer_signatures: bool = True) -> list[str]:
    """§0unvicies decies O1/O2 (01/09/2026, mandants) — réécrit les relevés
    18h/20h/22h d'UNE journée depuis l'historique des portails (vraies
    positions même serveur éteint ; point retenu = dernier AVANT l'heure) :

    • cellule VIDE        → complétée (portail d'abord, repli base locale sans
      limite d'âge — N3 v1.44 étendu à la source portail), audit
      `suivi.position_rattrapee` enrichi de la source ;
    • signature 18h=20h=22h identiques (jours passés) → recalcul portail ; si
      différent, correction en place + audit `suivi.position_reparee`
      (avant → après par cellule). Camion légitimement immobile : le portail
      rend la même valeur → RIEN n'est écrit (idempotent). Une ligne sans
      cette signature n'est JAMAIS touchée, et une réparation ne passe JAMAIS
      par le repli base (c'est lui qui produisait le défaut) ;
    • jour COURANT à partir de 22h05 → réécriture libre jusqu'au verrouillage
      de minuit (N2 v1.44 inchangé), sans audit (comme la passe N1 vivante).

    Les deux portails injoignables (§10) → dégradé v1.44 : les cellules vides
    sont complétées depuis la base locale seule, aucune réparation. Retourne
    les ids des suivis modifiés (archives éventuelles régénérées par
    l'appelant via le pipeline M1)."""
    maintenant = maintenant or now_local()
    jour_courant = (jour == maintenant.date())
    passe_soir = jour_courant and maintenant >= datetime.combine(
        jour, datetime.min.time()).replace(hour=_INTEGRATION_SOIR_H[0],
                                           minute=_INTEGRATION_SOIR_H[1])
    suivis = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour)).all()
    if not suivis:
        return []

    # Candidats : y a-t-il quelque chose à faire AVANT d'appeler les portails ?
    def _signature(s) -> bool:
        a = [getattr(s, champ) for _h, champ in _HEURES_POS_AUTO]
        return bool(a[0]) and a[0] == a[1] == a[2]

    if jour_courant:
        a_faire = passe_soir
    else:
        a_faire = any(
            not getattr(s, champ) or (reparer_signatures and _signature(s))
            for s in suivis for _h, champ in _HEURES_POS_AUTO)
    if not a_faire:
        return []

    # Source portail (dégradé v1.44 si les deux portails sont muets)
    try:
        from .positions_portails import positions_du_jour
        donnees = positions_du_jour(jour)
    except Exception:
        donnees = {}
        log.warning("Positions portails injoignables (%s) — dégradé v1.44 : "
                    "complétion des cellules vides depuis la base locale "
                    "seule, aucune réparation", jour.isoformat())

    modifies: list[str] = []
    for s in suivis:
        plaque = s.vehicule.plaque if s.vehicule else None
        par_heure = donnees.get(plaque, {}) if plaque else {}
        signature = _signature(s)
        completees: dict[str, dict] = {}
        reparees: dict[str, dict] = {}
        change = False
        for h, champ in _HEURES_POS_AUTO:
            borne = datetime.combine(jour, datetime.min.time()).replace(hour=h)
            point = par_heure.get(h)
            valeur_portail = _libelle_point(*point) if point else None
            actuelle = getattr(s, champ)
            if not actuelle:
                valeur = valeur_portail or _position_connue_a(
                    db, s.vehicule_id, borne)
                if valeur:
                    setattr(s, champ, valeur)
                    completees[champ] = {
                        "valeur": valeur,
                        "source": "PORTAIL" if valeur_portail else "BASE_LOCALE"}
                    change = True
            elif signature and valeur_portail and valeur_portail != actuelle:
                setattr(s, champ, valeur_portail)      # O2 — réparation ciblée
                reparees[champ] = {"avant": actuelle, "apres": valeur_portail}
                change = True
            elif passe_soir and valeur_portail and valeur_portail != actuelle:
                setattr(s, champ, valeur_portail)      # N2 — réécriture libre
                change = True
        if completees:
            db.add(AuditLog(username="collecteur",
                            action="suivi.position_rattrapee",
                            entite="suivi", entite_id=s.id,
                            details={"plaque": plaque, "jour": jour.isoformat(),
                                     "cellules": completees,
                                     "regle": "§0unvicies decies O1 (01/09/2026) "
                                              "+ §0vicies decies N3 : cellules "
                                              "VIDES 18h/20h/22h complétées "
                                              "(portail d'abord, base locale "
                                              "sans limite d'âge en repli)"}))
        if reparees:
            db.add(AuditLog(username="collecteur",
                            action="suivi.position_reparee",
                            entite="suivi", entite_id=s.id,
                            details={"plaque": plaque, "jour": jour.isoformat(),
                                     "motif": "signature 18h=20h=22h identiques",
                                     "cellules": reparees,
                                     "regle": "§0unvicies decies O2 (01/09/2026) "
                                              ": ligne suspecte recalculée via "
                                              "l'historique des portails — "
                                              "jamais d'effacement"}))
        if change:
            modifies.append(s.id)
    if modifies:
        db.commit()
        log.info("Positions intégrées (§0unvicies decies O1/O2, %s) : %d "
                 "ligne(s) modifiée(s)", jour.isoformat(), len(modifies))
    return modifies


def reparer_positions_horaires(db, maintenant: datetime | None = None,
                               jours: int = 7, apres_jour=None) -> list[str]:
    """§0unvicies decies O2 — balayage de réparation au DÉMARRAGE (J-1 → J-7) :
    signatures identiques corrigées + cellules vides complétées, audits à
    l'appui. Idempotent : un jour sain ne coûte même pas un appel portail.

    ``apres_jour(jour, ids)`` (facultatif) est rappelé APRÈS CHAQUE journée
    PASSÉE EN REVUE — même quand le suivi est déjà sain (``ids`` vide) : le
    suivi peut être correct alors que l'ARCHIVE est restée en retard (arrêt
    en plein balayage au démarrage précédent — preuve smoke du 01/09), et
    l'auto-guérison ``aligner_archives_positions`` — purement locale — ne
    coûte rien sur une journée déjà alignée (loi O2 addendum : « à chaque
    passage du balayage »). L'appelant y régénère et COMMIT les archives du
    jour tout de suite : un arrêt en plein balayage ne laisse jamais
    d'archive en retard sur un suivi réparé (écran = archive, §A.2 ext#2)."""
    maintenant = maintenant or now_local()
    auj = maintenant.date()
    modifies: list[str] = []
    for delta in range(1, jours + 1):
        # Une passe PAR JOUR calendaire et par démarrage suffit (loi O1 : le
        # balayage J-1 → J-7 est une réparation de démarrage, pas un service
        # horaire — ~40 appels portails par journée balayée).
        if not integration_throttle_ok(f"reparation.{auj - timedelta(days=delta)}",
                                       86400):
            continue
        jour = auj - timedelta(days=delta)
        lot = integrer_positions_portails(db, jour,
                                          maintenant=maintenant,
                                          reparer_signatures=True)
        if lot:
            log.info("Réparation positions (§0unvicies decies O2) J-%d : %d "
                     "ligne(s)", delta, len(lot))
        # Auto-guérison archives : TOUJOURS (suivi sain n'implique pas
        # archive à jour — arrêt mid-sweep possible au démarrage précédent).
        if apres_jour is not None:
            apres_jour(jour, lot)
        modifies.extend(lot)
    return modifies


_CHAMPS_POS_TOUTES = tuple(champ for _h, champ in _HEURES_POSITIONS)


def aligner_archives_positions(db, jour: date) -> list[str]:
    """§0unvicies decies O2 (addendum d'implémentation auto-guérison, déclaré
    01/09/2026) + §A.2 ext#2 — si l'ARCHIVE d'une journée a des relevés
    08h→22h différents du SUIVI (source de vérité), régénère le snapshot via
    le pipeline M1 existant (audit `archive.raffraichie`, mention
    « positions »). Purement local (AUCUN appel portail) : rattrape toute
    archive restée en retard — par exemple après un arrêt en plein balayage
    de réparation — à chaque passage du balayage O2 et du cycle J-1.
    Retourne les ids des suivis dont l'archive a été resynchronisée."""
    from .models import HistoriqueJournalier as _HJ
    from .reconciliation import _synchroniser_archive
    suivis = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour)).all()
    synchronises: list[str] = []
    for s in suivis:
        h = db.scalar(select(_HJ).where(
            _HJ.date_jour == jour,
            _HJ.vehicule_id == s.vehicule_id))
        if h is None:
            continue
        donnees = h.donnees or {}
        if any(donnees.get(c) != getattr(s, c, None)
               for c in _CHAMPS_POS_TOUTES):
            _synchroniser_archive(db, s)
            synchronises.append(s.id)
    if synchronises:
        db.commit()
        log.info("Archives alignées sur les positions du suivi (O2 auto-"
                 "guérison, %s) : %d ligne(s)", jour.isoformat(),
                 len(synchronises))
    return synchronises


def _existe_alias_en_session_ou_db(db, alias_normalise: str, conducteur_id: str | None = None) -> ConducteurAlias | None:
    """Retourne un alias déjà présent dans la session courante ou la base.

    Le point clé ici est que SQLAlchemy ne voit pas les objets ajoutés en
    mémoire tant qu'un flush n'a pas eu lieu. Dans un batch réél, plusieurs
    appels à `creer_conducteur_auto()` peuvent donc créer des `ConducteurAlias`
    identiques avant l'insertion SQL effective, ce qui déclenche le UNIQUE
    constraint sur `alias_normalise`.
    """
    if not alias_normalise:
        return None
    for obj in db.new:
        if isinstance(obj, ConducteurAlias) and obj.alias_normalise == alias_normalise:
            if conducteur_id is None or obj.conducteur_id == conducteur_id:
                return obj
    return db.scalar(select(ConducteurAlias).where(
        ConducteurAlias.alias_normalise == alias_normalise))


# ------------------------------------------------------------------ §0quater D1/D2
def creer_vehicule_auto(db, ident: str, plateforme: str) -> Vehicule | None:
    """Arbitrage §0quater D1 (14/08/2026) — DÉCOUVERTE AUTOMATIQUE : identifiant
    vu par un collecteur mais inconnu en base → fiche Vehicule créée (plaque =
    ident normalisé, plateforme = portail détecteur, ACTIF, exploitation
    immédiate) + alerte « ⓘ Nouveau véhicule détecté » + audit. Le garde
    plaque (§D3) écarte les lignes parasites (totaux, libellés…) : un simple
    journal INFO et pas de création. Idempotent : renvoie la fiche EXISTANTE
    si la plaque/le boîtier est déjà connu."""
    norm = normaliser_ident(ident)
    if not IDENT_PLAQUE_RE.match(norm):
        log.info("§0quater D3 — identifiant non retenu pour création "
                 "automatique : %r (collecteur %s)", ident, plateforme)
        return None
    existant = db.scalar(select(Vehicule).where(
        (Vehicule.plaque == norm) | (Vehicule.gps_associe == norm)))
    if existant is not None:
        return existant
    plateforme = (plateforme or "MZONEX").upper()
    if plateforme not in ("MZONEX", "CAMTRACKPRO"):
        plateforme = "MZONEX"
    v = Vehicule(plaque=norm, gps_associe=norm, plateforme_gps=plateforme,
                 statut=StatutVehicule.ACTIF,
                 description=f"Détecté automatiquement ({plateforme}) le "
                             f"{now_local():%d/%m/%Y}")
    db.add(v)
    db.flush()
    creer_alerte(
        db, TypeAlerte.NOUVEAU_VEHICULE, GraviteAlerte.INFORMATION,
        f"Nouveau véhicule détecté ({plateforme}) : {norm} — fiche créée "
        f"automatiquement, à compléter dans Véhicules (marque, capacité, "
        f"chauffeur).", vehicule_id=v.id, lien_module="/vehicules")
    _auditer(db, "vehicule.auto_cree", v.id, {
        "plaque": norm, "plateforme": plateforme,
        "regle": "§0quater D1 (14/08/2026) : découverte automatique depuis "
                 "les relevés"})
    log.info("§0quater D1 : nouveau véhicule %s détecté via %s — fiche créée, "
             "exploitation immédiate", norm, plateforme)
    return v


def creer_conducteur_auto(db, nom_brut: str | None, badge_code: int | None = None,
                          plateforme: str | None = None) -> "object | None":
    """Arbitrage §0quater D2 (14/08/2026) & Spécification Déduplication Avancée :
    1. Rapprochement direct par driverKeyCode MZoneX (si badge_code présent).
    2. Rapprochement par forme CANONIQUE (nom_normalise).
    3. Rapprochement par ALIAS connu (conducteur_aliases).
    4. Rapprochement par SAC DE MOTS exact (tokens_set).
    5. Rapprochement par INCLUSION forte de patronyme (nom_court in nom_long).
    6. Découverte chauffeur : création de fiche Conducteur.
       - MZoneX : matricule = str(driverKeyCode), code_badge_mzonex = badge_code
       - CamtrackPro : matricule = None, code_badge_mzonex = None
    """
    from .models import Conducteur, ConducteurAlias
    nom = " ".join((nom_brut or "").split()).strip()

    # 1. Filtres non-personnes (D5), garages et clés de service (B2)
    if nom:
        if est_libelle_service_ou_garage(nom):
            if nom.lower() not in _D5_DEJA_LOGUES:
                _D5_DEJA_LOGUES.add(nom.lower())
                log.info("§0quinquies D5 / B2 : libellé clé de service / garage / non-personne ignoré "
                         ": %r (tracé une seule fois)", nom)
            return None

    # Règle MZoneX : si badge_code valide, chercher en priorité absolue par code_badge_mzonex ou matricule
    if badge_code and badge_code > 0:
        ex_badge = db.scalar(select(Conducteur).where(
            (Conducteur.code_badge_mzonex == badge_code) | (Conducteur.matricule == str(badge_code))))
        if ex_badge is not None:
            tset_badge = ex_badge.tokens_set or (calculer_tokens_set(ex_badge.nom_prenom)[:170] if ex_badge.nom_prenom else "")
            if tset_badge:
                for c_hom in db.scalars(select(Conducteur).where(
                        Conducteur.tokens_set == tset_badge,
                        Conducteur.id != ex_badge.id,
                        Conducteur.code_badge_mzonex.is_(None))).all():
                    c_hom.code_badge_mzonex = badge_code
                    c_hom.matricule = str(badge_code)
            if nom and len(nom) >= 3:
                cible = normaliser_libelle(nom)[:170]
                if cible and cible != ex_badge.nom_normalise:
                    if _existe_alias_en_session_ou_db(db, cible, ex_badge.id) is None:
                        db.add(ConducteurAlias(conducteur_id=ex_badge.id, alias_brut=nom,
                                               alias_normalise=cible, source=plateforme or "MZONEX"))
            return ex_badge

    if not nom or len(nom) < 3 or not any(c.isalpha() for c in nom):
        return None

    cible = normaliser_libelle(nom)[:170]
    if not cible:
        return None
    tset = calculer_tokens_set(nom)[:170]

    # Échelon 2 : Correspondance canonique directe
    existant = db.scalar(select(Conducteur).where(Conducteur.nom_normalise == cible))
    if existant is None:
        for c0 in db.scalars(select(Conducteur).where(Conducteur.nom_normalise.is_(None))):
            if normaliser_libelle(c0.nom_prenom)[:170] == cible:
                existant = c0
                break

    # Échelon 3 : Table des Alias
    if existant is None:
        alias_row = db.scalar(select(ConducteurAlias).where(ConducteurAlias.alias_normalise == cible))
        if alias_row is not None and alias_row.conducteur is not None:
            existant = alias_row.conducteur

    # Échelon 4 : Sac de mots exact (tokens_set)
    if existant is None and tset:
        existant = db.scalar(select(Conducteur).where(Conducteur.tokens_set == tset))
        if existant is None:
            for c0 in db.scalars(select(Conducteur).where(Conducteur.tokens_set.is_(None))):
                if calculer_tokens_set(c0.nom_prenom)[:170] == tset:
                    existant = c0
                    break
        if existant is not None:
            # Enregistrer comme alias permanent
            if _existe_alias_en_session_ou_db(db, cible, existant.id) is None:
                db.add(ConducteurAlias(conducteur_id=existant.id, alias_brut=nom,
                                       alias_normalise=cible, source=plateforme or "AUTO"))
                db.flush()

    # Échelon 5 : Inclusion forte / patronyme partiel (>= 2 mots)
    if existant is None and tset:
        mots_cible = set(tset.split())
        if len(mots_cible) >= 2:
            candidats = []
            for c0 in db.scalars(select(Conducteur).where(Conducteur.statut == StatutConducteur.ACTIF)).all():
                c0_tokens = set((c0.tokens_set or calculer_tokens_set(c0.nom_prenom)).split())
                if mots_cible.issubset(c0_tokens) and len(c0_tokens) > len(mots_cible):
                    candidats.append(c0)
            if len(candidats) == 1:
                existant = candidats[0]
                if _existe_alias_en_session_ou_db(db, cible, existant.id) is None:
                    db.add(ConducteurAlias(conducteur_id=existant.id, alias_brut=nom,
                                           alias_normalise=cible, source=plateforme or "INCLUSION"))
                    db.flush()
                log.info("Rapprochement par inclusion : « %s » rattaché à « %s »", nom, existant.nom_prenom)

    if existant is not None:
        if badge_code and not existant.code_badge_mzonex:
            existant.code_badge_mzonex = badge_code
            if not existant.matricule:
                existant.matricule = str(badge_code)
        # Propagation automatique du code badge aux fiches ayant le même tokens_set
        if existant.code_badge_mzonex and existant.tokens_set:
            for c_hom in db.scalars(select(Conducteur).where(
                    Conducteur.tokens_set == existant.tokens_set,
                    Conducteur.code_badge_mzonex.is_(None))).all():
                c_hom.code_badge_mzonex = existant.code_badge_mzonex
                c_hom.matricule = str(existant.code_badge_mzonex)
        return existant

    # Échelon 6 : Création nouvelle fiche
    tokens = nom.split()
    prenom_usuel = tokens[1] if tokens[0].isupper() and len(tokens) > 1 else tokens[-1]
    matricule = str(badge_code) if (badge_code and badge_code > 0) else None

    c = Conducteur(nom_prenom=nom, prenom_usuel=prenom_usuel.capitalize(),
                   matricule=matricule, code_badge_mzonex=badge_code if (badge_code and badge_code > 0) else None,
                   nom_normalise=cible, tokens_set=tset)
    db.add(c)
    db.flush()

    # Propagation automatique du code badge aux homonymes sémantiques (même tokens_set)
    if c.code_badge_mzonex and tset:
        for c_hom in db.scalars(select(Conducteur).where(
                Conducteur.tokens_set == tset,
                Conducteur.code_badge_mzonex.is_(None))).all():
            c_hom.code_badge_mzonex = c.code_badge_mzonex
            c_hom.matricule = str(c.code_badge_mzonex)

    creer_alerte(
        db, TypeAlerte.NOUVEAU_CONDUCTEUR, GraviteAlerte.INFORMATION,
        f"Nouveau chauffeur détecté sur les relevés : {nom} — fiche créée "
        f"automatiquement ; affectation au véhicule à faire dans Conducteurs.")
    _auditer(db, "conducteur.auto_cree", c.id, {
        "nom_prenom": nom,
        "matricule": matricule,
        "code_badge_mzonex": badge_code,
        "regle": "§0quater D2 (14/08/2026) : découverte chauffeur — affectation manuelle"})
    log.info("§0quater D2 : nouveau chauffeur « %s » détecté (matricule=%s) — fiche créée",
             nom, matricule)
    return c


# Libellés D5 déjà tracés une fois ce processus (anti-spam §10 : le portail
# répète « Garage LSS 2 » à chaque ligne de trajet — une trace suffit).
_D5_DEJA_LOGUES: set = set()


# ============================================================================
# §0septies (arbitrages LSS du 20/08/2026) — CONDUCTEURS & CONDUITE.
#  B2 : « le badge fait foi » — le conducteur publié par le portail s'inscrit
#       sur le trajet validé et attribue le chauffeur du jour, SAUF clés de
#       service (« Nouveau conducteur », « garage LSS » — CONDUCTEUR_BADGE_
#       IGNORES) : badge écarté (tracé), la saisie manuelle fait le travail et
#       n'est JAMAIS écrasée. Attribution manuelle = origine « MANUEL ».
#  B3 : compteurs d'écoconduite officiels rangés sur le trajet (carnet).
#  B4 : alerte VITESSE_LIVE (> CONDUITE_VITESSE_HORS_ZONE=45 km/h, confirmée
#       2 signaux, HORS géozone seulement) — 1 alerte par épisode.
#  B5 : alerte SANS_BADGE (MZoneX — véhicule > 3 km/h sans clé vue depuis
#       CONDUITE_BADGE_FENETRE_S=600 s) — 1 par épisode de mouvement.
# ============================================================================
_CHAMPS_ECO = ("v_max", "ralenti_s", "exc_vitesse", "exc_freinage",
               "exc_accel", "exc_ralenti", "exc_surregime", "exc_autres")


def resoudre_badge(db, nom_brut: str | None, badge_code: int | None = None,
                   plateforme: str | None = None):
    """B2 — nom publié par le portail → (fiche Conducteur, libellé_écarté).

    (fiche, None)  : badge valide — fiche existante ou créée (D2), fait foi ;
    (None, libellé): clé de service (liste B2) ou non-personne (D5) → écarté ;
    (None, None)   : pas de conducteur publié sur cette ligne."""
    from .models import Conducteur  # import local (comme creer_conducteur_auto)
    nom = " ".join((nom_brut or "").split()).strip()
    if not nom and not badge_code:
        return None, None
    if nom and est_libelle_service_ou_garage(nom):
        return None, nom
    fiche = creer_conducteur_auto(db, nom, badge_code=badge_code, plateforme=plateforme)
    if fiche is None:
        return None, nom
    return fiche, None


def attribuer_badge_au_jour(db, suivi: SuiviJournalier, fiche,
                            username: str = "collecteur") -> None:
    """B2 — le badge fait foi sur le chauffeur du JOUR (grille Parties A) :
    l'attribution « MANUEL » n'est jamais écrasée ; une attribution « BADGE »
    (ou absente) suit le dernier badge valide publié.
    Gestion intelligente des passages temporaires (relais) et détection anti-doublon."""
    if suivi is None or fiche is None:
        return
    vehicule = db.get(Vehicule, suivi.vehicule_id)
    if vehicule is None or vehicule.statut != StatutVehicule.ACTIF:
        return

    titulaire = vehicule.conducteur_actuel if (vehicule and vehicule.conducteur_actuel) else None

    # 1. Si la ligne de suivi actuelle est déjà attribuée MANUELLEMENT :
    # La saisie manuelle prime TOUJOURS sur les relevés des portails.
    if getattr(suivi, "conducteur_origine", None) == "MANUEL":
        if suivi.conducteur_id != fiche.id:
            log.info("§0septies B2 : attribution MANUELLE maintenue sur %s (%s) malgré badge « %s »",
                     suivi.vehicule.plaque if suivi.vehicule else "?",
                     suivi.conducteur.nom_prenom if suivi.conducteur else "?",
                     fiche.nom_prenom)
        return

    # 2. Si le véhicule est déjà arbitré ou marqué en « RELAIS » :
    # On maintient le titulaire sur le suivi, le trajet individuel conserve son badge spécifique.
    if getattr(suivi, "conducteur_origine", None) == "RELAIS":
        if titulaire and suivi.conducteur_id == titulaire.id:
            return

    # 3. Détection intelligente de passage temporaire (Relais / Sandwich) :
    # Si le camion est rattaché à son titulaire et qu'un autre chauffeur conduit un trajet :
    if titulaire and suivi.conducteur_id == titulaire.id and fiche.id != titulaire.id:
        # Le camion reste attribué à son titulaire, la ligne passe en statut RELAIS
        suivi.conducteur_origine = "RELAIS"
        nom_titulaire = titulaire.nom_prenom
        nom_relais = fiche.nom_prenom

        msg = (f"Changement de conducteur détecté sur {vehicule.plaque} : "
               f"trajet badgé par {nom_relais} (titulaire habituel : {nom_titulaire}). "
               f"Arbitrage disponible.")
        if not _alerte_recente_ouverte(db, TypeAlerte.CHANGEMENT_CONDUCTEUR_DETECTE, suivi.vehicule_id, fenetre_s=3600):
            creer_alerte(
                db, TypeAlerte.CHANGEMENT_CONDUCTEUR_DETECTE, GraviteAlerte.INFORMATION,
                msg, vehicule_id=suivi.vehicule_id, conducteur_id=fiche.id,
                lien_module=f"/suivi?date={suivi.date_jour.isoformat()}&vehicule={suivi.vehicule_id}"
            )
        _auditer(db, "suivi.relais_detecte", suivi.id, {
            "plaque": vehicule.plaque, "titulaire": nom_titulaire,
            "relais": nom_relais, "conducteur_relais_id": fiche.id,
            "regle": "Passage temporaire : le titulaire reste affecté au camion, répartition proportionnelle du TCH"
        })
        log.info("Passage temporaire détecté sur %s : %s au volant (titulaire %s conservé)",
                 vehicule.plaque, nom_relais, nom_titulaire)
        return

    # 4. Vérification anti-doublon : ce chauffeur (fiche.id) est-il déjà affecté à un AUTRE véhicule ce jour ?
    autre_suivi = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == suivi.date_jour,
        SuiviJournalier.conducteur_id == fiche.id,
        SuiviJournalier.id != suivi.id))

    if autre_suivi is not None:
        plaque_autre = autre_suivi.vehicule.plaque if autre_suivi.vehicule else "inconnu"
        plaque_ceci = suivi.vehicule.plaque if suivi.vehicule else "inconnu"
        nom_ch = fiche.nom_prenom

        # Si l'autre véhicule a été attribué MANUELLEMENT à ce chauffeur :
        if getattr(autre_suivi, "conducteur_origine", None) == "MANUEL":
            # Conflit d'affectation : la saisie manuelle sur l'autre véhicule fait foi
            msg = (f"Conflit d'affectation : {nom_ch} est attribué manuellement "
                   f"au camion {plaque_autre}, mais a été détecté sur les relevés "
                   f"({suivi.vehicule.plateforme_gps if suivi.vehicule else 'GPS'}) pour le camion {plaque_ceci}.")
            if not _alerte_recente_ouverte(db, TypeAlerte.CONFLIT_AFFECTATION, suivi.vehicule_id, fenetre_s=3600):
                creer_alerte(db, TypeAlerte.CONFLIT_AFFECTATION, GraviteAlerte.MOYENNE,
                             msg, vehicule_id=suivi.vehicule_id, conducteur_id=fiche.id,
                             lien_module=f"/suivi?date={suivi.date_jour.isoformat()}&vehicule={suivi.vehicule_id}")
            _auditer(db, "suivi.conflit_affectation", suivi.id, {
                "chauffeur": nom_ch, "conducteur_id": fiche.id,
                "vehicule_manuel": plaque_autre, "vehicule_portail": plaque_ceci,
                "regle": "Conflit d'affectation : priorité à la saisie manuelle, pas de doublon dans Suivi Journalier"
            })
            log.warning("Conflit d'affectation : chauffeur %s (manuel sur %s) détecté portail sur %s",
                        nom_ch, plaque_autre, plaque_ceci)
            return

        # Si l'autre affectation n'était PAS manuelle (ex. ancien badge de la journée) :
        # Pour éviter tout doublon dans le suivi journalier, détacher l'ancien véhicule
        autre_suivi.conducteur_id = None
        autre_suivi.conducteur_origine = None
        log.info("Changement de camion pour %s : détaché de %s et réaffecté à %s",
                 nom_ch, plaque_autre, plaque_ceci)

    # Propagation automatique du code badge aux homonymes sémantiques (même tokens_set)
    if fiche.code_badge_mzonex and fiche.tokens_set:
        for c_hom in db.scalars(select(Conducteur).where(
                Conducteur.tokens_set == fiche.tokens_set,
                Conducteur.code_badge_mzonex.is_(None))).all():
            c_hom.code_badge_mzonex = fiche.code_badge_mzonex
            c_hom.matricule = str(fiche.code_badge_mzonex)

    if suivi.conducteur_id == fiche.id and suivi.conducteur_origine == "BADGE":
        return

    avant = suivi.conducteur_id
    suivi.conducteur_id = fiche.id
    suivi.conducteur_origine = "BADGE"
    _auditer(db, "suivi.conducteur_badge", suivi.id, {
        "avant_conducteur_id": avant, "apres_conducteur_id": fiche.id,
        "chauffeur": fiche.nom_prenom,
        "regle": "§0septies B2 (20/08/2026) : le badge fait foi (modifiable "
                 "à la main — une saisie MANUELLE n'est jamais écrasée)"})
    log.info("§0septies B2 : chauffeur du jour %s ← badge « %s »",
             suivi.date_jour, fiche.nom_prenom)


def appliquer_badge_et_eco(db, trajet: Trajet, suivi: SuiviJournalier,
                           vehicule: Vehicule, it: dict,
                           username: str = "collecteur") -> None:
    """B2/B3 — inscrit sur la ligne de trajet le badge (filtré B2) et les
    compteurs d'écoconduite officiels. Idempotent et SILENCIEUX tant que les
    valeurs ne changent pas (les mêmes lignes reviennent à chaque cycle)."""
    nom = it.get("conducteur")
    badge_code = it.get("badge_code")
    plateforme = it.get("source") or getattr(vehicule, "plateforme_gps", None)
    fiche = None
    if nom or badge_code:
        fiche, ecarte = resoudre_badge(db, nom, badge_code=badge_code, plateforme=plateforme)
        if ecarte is not None:
            if trajet.badge_ecarte != ecarte:
                trajet.badge_ecarte = ecarte
                trajet.conducteur_badge = None
                trajet.conducteur_badge_id = None
                _auditer(db, "trajet.badge_ecarte", trajet.id, {
                    "plaque": vehicule.plaque, "badge": ecarte,
                    "regle": "§0septies B2 : clé de service écartée — la "
                             "saisie manuelle fait le travail"})
                log.info("§0septies B2 : badge clé de service écarté « %s » "
                         "(%s %s)", ecarte, vehicule.plaque,
                         iso(trajet.heure_debut))
        elif fiche is not None and trajet.conducteur_badge_id != fiche.id:
            trajet.conducteur_badge = fiche.nom_prenom
            trajet.conducteur_badge_id = fiche.id
            trajet.badge_ecarte = None
            _auditer(db, "trajet.badge_attribue", trajet.id, {
                "plaque": vehicule.plaque, "debut": iso(trajet.heure_debut),
                "chauffeur": fiche.nom_prenom, "source": it.get("source"),
                "regle": "§0septies B2 : le badge fait foi sur le trajet"})
            log.info("§0septies B2 : badge « %s » inscrit — %s %s",
                     fiche.nom_prenom, vehicule.plaque, iso(trajet.heure_debut))
        if fiche is not None:
            attribuer_badge_au_jour(db, suivi, fiche, username=username)
    eco_change = {}
    for cle in _CHAMPS_ECO:
        if cle in it and it.get(cle) is not None:
            if getattr(trajet, cle) != it[cle]:
                eco_change[cle] = it[cle]
                setattr(trajet, cle, it[cle])
    if eco_change:
        _auditer(db, "trajet.eco_inscrit", trajet.id, {
            "plaque": vehicule.plaque, "debut": iso(trajet.heure_debut),
            "compteurs": eco_change, "source": it.get("source"),
            "regle": "§0septies B3 : compteurs officiels du portail (carnet "
                     "de conduite)"})


def _alerte_recente_ouverte(db, type_, vehicule_id, fenetre_s=7200) -> bool:
    """Anti-doublon au redémarrage : une alerte NOUVELLE/VUE du même type sur
    le véhicule dans la fenêtre → l'épisode est considéré déjà signalé."""
    borne = now_local() - timedelta(seconds=fenetre_s)
    return (db.scalar(select(func.count(Alerte.id)).where(
        Alerte.type == type_, Alerte.vehicule_id == vehicule_id,
        Alerte.statut != StatutAlerte.TRAITEE,
        Alerte.date_heure >= borne)) or 0) > 0


# Épisodes en direct (en mémoire process — l'anti-doublon base couvre les
# redémarrages). B4 : 2 signaux consécutifs > seuil ouvrent ; 2 signaux
# ≤ seuil, une entrée en géozone ou 10 min sans signal ferment.
_EP_VITESSE: dict = {}
# B5 : dernier badge vu + épisode « roule sans badge » par véhicule.
_EP_BADGE: dict = {}


def verifier_alertes_conduite(db, vehicule: Vehicule, ts: datetime,
                              vitesse: float, badge_present: bool | None,
                              en_geozone: bool | None,
                              source: SourceEvenement) -> None:
    """B4/B5 — appelé à chaque point Niveau 1 inséré (machine à états souveraine
    déjà déroulée). Ne lève JAMAIS d'exception vers le collecteur (§10)."""
    try:
        seuils = get_seuils(db)
        _verifier_vitesse_live(db, vehicule, ts, float(vitesse or 0),
                               bool(en_geozone), seuils)
        _verifier_sans_badge(db, vehicule, ts, float(vitesse or 0),
                             badge_present, seuils, source)
    except Exception:
        log.exception("Alerte conduite en échec (%s) — point déjà enregistré",
                      vehicule.plaque)


def _verifier_vitesse_live(db, vehicule, ts, vitesse, en_zone, seuils) -> None:
    seuil = float(seuils.get("CONDUITE_VITESSE_HORS_ZONE", 45))
    with _lock:
        connu = vehicule.id in _EP_VITESSE
        ep = _EP_VITESSE.setdefault(vehicule.id, {
            "consec": 0, "ouvert": False, "vmax": 0.0, "dernier": None})
        depasse = vitesse > seuil and not en_zone
        ep["consec"] = ep["consec"] + 1 if depasse else 0
        if ep["ouvert"]:
            ep["vmax"] = max(ep["vmax"], vitesse)
            cloture = (not depasse and ep["consec"] == 0) \
                or (ep["dernier"] is not None
                    and (ts - ep["dernier"]).total_seconds() > 600) \
                or en_zone
            if cloture:
                ep["ouvert"] = False
                log.info("§0septies B4 : épisode vitesse clos — %s (max %.0f "
                         "km/h)", vehicule.plaque, ep["vmax"])
        elif ep["consec"] >= 2 \
                and (connu  # état mémoire vivant : nouvel épisode = nouvelle
                     # alerte ; la garde base ne sert qu'au REDÉMARRAGE
                     or not _alerte_recente_ouverte(db, TypeAlerte.VITESSE_LIVE,
                                                    vehicule.id)):
            ep["ouvert"] = True
            ep["vmax"] = vitesse
            creer_alerte(
                db, TypeAlerte.VITESSE_LIVE, GraviteAlerte.MOYENNE,
                f"Excès de vitesse en cours — {vehicule.plaque} roule à "
                f"{vitesse:.0f} km/h (seuil hors zone {seuil:.0f} km/h) "
                f"depuis {ts:%H:%M}, hors géozone. Les seuils des portails "
                "gouvernent en géozone (carnet de conduite).",
                ts=ts, vehicule_id=vehicule.id, lien_module="conduite")
            _auditer(db, "alerte.vitesse_live", vehicule.id, {
                "plaque": vehicule.plaque, "vitesse": vitesse, "seuil": seuil,
                "heure": iso(ts),
                "regle": "§0septies B4 : 2 signaux > 45 km/h hors géozone"})
            log.info("§0septies B4 : VITESSE en direct — %s à %.0f km/h "
                     "(hors zone)", vehicule.plaque, vitesse)
        ep["dernier"] = ts
        db.flush()


def _verifier_sans_badge(db, vehicule, ts, vitesse, badge_present, seuils,
                         source) -> None:
    # B5 — MZoneX seul publie la clé en continu ; CamtrackPro : None → le
    # constat « sans badge » se fait au Niveau 2, jamais en direct (loi B5).
    if badge_present is None or source != SourceEvenement.MZONEX:
        return
    fenetre = float(seuils.get("CONDUITE_BADGE_FENETRE_S", 600))
    with _lock:
        connu = vehicule.id in _EP_BADGE
        ep = _EP_BADGE.setdefault(vehicule.id, {
            "dernier_badge": None, "ouvert": False, "dernier_roule": None})
        if badge_present:
            ep["dernier_badge"] = ts
            if ep["ouvert"]:
                ep["ouvert"] = False
                log.info("§0septies B5 : badge retrouvé — %s", vehicule.plaque)
        roule = vitesse > float(seuils.get("SEUIL_VITESSE_ARRET", 3))
        if roule:
            ep["dernier_roule"] = ts
            sans_cle = ep["dernier_badge"] is None or \
                (ts - ep["dernier_badge"]).total_seconds() > fenetre
            if sans_cle and not ep["ouvert"] \
                    and (connu           # garde base uniquement au redémarrage
                         or not _alerte_recente_ouverte(
                             db, TypeAlerte.SANS_BADGE, vehicule.id)):
                ep["ouvert"] = True
                creer_alerte(
                    db, TypeAlerte.SANS_BADGE, GraviteAlerte.CRITIQUE,
                    f"{vehicule.plaque} ROULE SANS BADGE chauffeur — en "
                    f"mouvement à {ts:%H:%M} sans clé détectée depuis plus de "
                    f"{int(fenetre // 60)} min. Vérifier le conducteur.",
                    ts=ts, vehicule_id=vehicule.id, lien_module="conduite")
                _auditer(db, "alerte.sans_badge", vehicule.id, {
                    "plaque": vehicule.plaque, "heure": iso(ts),
                    "regle": "§0septies B5 : roule > 3 km/h sans clé vue "
                             "depuis 10 min"})
                log.info("§0septies B5 : ROULE SANS BADGE — %s à %s",
                         vehicule.plaque, f"{ts:%H:%M}")
        elif ep["dernier_roule"] is not None and \
                (ts - ep["dernier_roule"]).total_seconds() > 1800:
            ep["ouvert"] = False                 # réarmement après 30 min d'arrêt
        db.flush()


# ------------------------------------------------------------ §0quinquies D4
def basculer_vehicule_plateforme(db, vehicule: Vehicule, nouvelle: str,
                                 username: str = "collecteur") -> bool:
    """§0quinquies D4 (arbitrage métier 14/08/2026) — un véhicule CONNU est vu
    sur l'AUTRE portail GPS → bascule AUTOMATIQUE de `plateforme_gps` (avec
    alerte ⓘ + audit). Sans cela, une fiche marquée MZoneX alors que le camion
    est sur CamtrackPro n'est lue par AUCUN des deux collecteurs (données
    perdues : constat métier 14/08 sur 2066TBP, 7136TCE, 7206TCE, 7766TBL)."""
    nouvelle = (nouvelle or "").upper()
    if nouvelle not in ("MZONEX", "CAMTRACKPRO"):
        return False
    ancienne = (vehicule.plateforme_gps or "").upper()
    if ancienne == nouvelle:
        return False
    vehicule.plateforme_gps = nouvelle
    note = f"bascule {ancienne or '?'}→{nouvelle} (D4)"   # description ≤ 60
    vehicule.description = (f"{vehicule.description} ; {note}")[:60] \
        if vehicule.description else note
    creer_alerte(
        db, TypeAlerte.NOUVEAU_VEHICULE, GraviteAlerte.INFORMATION,
        f"Véhicule {vehicule.plaque} basculé de {ancienne or '?'} vers "
        f"{nouvelle} : détecté sur le portail {nouvelle} (arbitrage D4) — "
        "ses données sont exploitées dès le prochain cycle.",
        vehicule_id=vehicule.id, lien_module="vehicules")
    _auditer(db, "vehicule.plateforme_basculee", vehicule.id, {
        "plaque": vehicule.plaque, "ancienne_plateforme": ancienne,
        "nouvelle_plateforme": nouvelle, "acteur": username,
        "regle": "§0quinquies D4 (14/08/2026) : bascule automatique de "
                 "plateforme au vu du portail"})
    db.flush()                     # session sans autoflush : figer l'audit
    log.info("§0quinquies D4 : %s bascule %s → %s (détection portail)",
             vehicule.plaque, ancienne or "?", nouvelle)
    return True


# Dernière liste d'absents signalée (anti-spam du WARNING chaque cycle).
_ABSENTS_SIGNALES: frozenset = frozenset()


def _recenser(db, libelles: list, plateforme: str, username: str) -> tuple:
    """Une liste de libellés publiés par UN portail → plaque par plaque :
    inconnue → création D1 ; connue ailleurs → bascule D4 ; déjà rattachée →
    rien. Retourne (stats, ensemble des plaques normalisées vues)."""
    stats = {"vus": 0, "crees": 0, "bascules": 0, "deja": 0, "ignores": 0}
    vus: set = set()
    for lib in libelles or []:
        ident = plaque_depuis_libelle_portail(str(lib))
        if not ident:
            if str(lib or "").strip():        # D3 : tracé, jamais silencieux
                stats["ignores"] += 1
                log.info("§0quinquies D3 : libellé %r non-plaque (%s) — "
                         "ignoré", str(lib).strip()[:60], plateforme)
            continue
        if ident in vus:      # même plaque publiée par 2 sources (D4 union)
            continue
        vus.add(ident)
        stats["vus"] += 1
        v = db.scalar(select(Vehicule).where(
            (Vehicule.gps_associe == ident) | (Vehicule.plaque == ident)))
        if v is None:
            if creer_vehicule_auto(db, ident, plateforme) is not None:
                stats["crees"] += 1
        elif (v.plateforme_gps or "").upper() != plateforme:
            if basculer_vehicule_plateforme(db, v, plateforme, username):
                stats["bascules"] += 1
        else:
            stats["deja"] += 1
    return stats, vus


def recenser_flotte(db, portails: dict, username: str = "collecteur") -> dict:
    """§0quinquies D4 (arbitrage 14/08/2026) — RECENSEMENT de la flotte telle
    que la publient les portails (listes déroulantes véhicules), à chaque
    synchronisation. `portails` = {"MZONEX": [libellés…], "CAMTRACKPRO": […]}.

    Pour chaque véhicule publié : inconnu → fiche créée avec la plateforme du
    portail d'où il provient (D1) ; connu mais sur l'autre plateforme dans
    notre référentiel → bascule automatique (D4 arbitrée) ; déjà rattaché →
    rien. Les véhicules ACTIFS de notre référentiel vus sur AUCUN portail
    sont signalés (boîtier inactif ou rattachement de groupe à faire côté
    portail) — warning seulement quand la liste CHANGE (anti-spam §10).
    Idempotent ; aucune suppression automatique."""
    global _ABSENTS_SIGNALES
    cumul = {"vus": 0, "crees": 0, "bascules": 0, "deja": 0, "ignores": 0,
             "absents": []}
    vus_par, tout_vu = {}, set()
    for plateforme, libelles in (portails or {}).items():
        if not libelles:
            continue
        stats, vus = _recenser(db, libelles, plateforme, username)
        vus_par[plateforme] = vus
        tout_vu |= vus
        for cle in ("vus", "crees", "bascules", "deja", "ignores"):
            cumul[cle] += stats[cle]
        log.info("§0quinquies D4 — Recensement %s : %d véhicule(s) publié(s), "
                 "%d déjà rattaché(s), %d créé(s), %d basculé(s), "
                 "%d libellé(s) ignoré(s)", plateforme, stats["vus"],
                 stats["deja"], stats["crees"], stats["bascules"],
                 stats["ignores"])
    if vus_par:
        # Garde anti-liste-partielle (§10) : si le recensement d'un portail
        # couvre moins de 80 % de ses véhicules ACTIFS, la récolte est
        # suspecte (liste virtualisée, DOM changé…) → on SUSPEND la liste des
        # absents pour ce portail plutôt que d'accuser des camions sains.
        # Constat réel 14/08 : combo MZoneX virtualisée (10/37 lus).
        actifs = db.scalars(select(Vehicule).where(
            Vehicule.statut == StatutVehicule.ACTIF)).all()
        attendus: dict = {}
        for v in actifs:
            pf = (v.plateforme_gps or "").upper()
            attendus[pf] = attendus.get(pf, 0) + 1
        pf_partiels = set()
        for pf, vus in vus_par.items():
            if attendus.get(pf) and len(vus) < 0.8 * attendus[pf]:
                pf_partiels.add(pf)
        cumul["partiels"] = sorted(
            f"{pf} ({len(vus_par[pf])}/{attendus[pf]})" for pf in pf_partiels)
        if pf_partiels:
            log.warning("§0quinquies D4 — recensement PARTIEL %s — récolte à "
                        "revérifier ; liste des absents suspendue pour ce(s) "
                        "portail(s)", ", ".join(cumul["partiels"]))
        absents = []
        for v in actifs:
            pf = (v.plateforme_gps or "").upper()
            if pf not in vus_par or pf in pf_partiels:
                continue
            # le recensement publie la PLAQUE : un boîtier « OBC-… » ne doit
            # pas faire passer le camion pour absent — plaque OU boîtier vu
            # suffisent
            candidats = {str(x).upper() for x in (v.gps_associe, v.plaque)
                         if x}
            if candidats and not (candidats & tout_vu):
                absents.append(f"{v.plaque} ({pf})")
        absents.sort()
        cumul["absents"] = absents
        lot = frozenset(absents)
        if lot and lot != _ABSENTS_SIGNALES:
            log.warning("§0quinquies D4 — véhicules vus sur AUCUN portail : "
                        "%s — vérifier le boîtier ou le rattachement au "
                        "groupe sur le portail", ", ".join(absents))
        _ABSENTS_SIGNALES = lot
    return cumul


# ------------------------------------------------------------------ §0quater R2
def rattraper_ouvertures(maintenant: datetime | None = None) -> dict:
    """Arbitrage LSS §0quater R2 (14/08/2026) — « jamais un camion en route
    sans ligne ». À chaque cycle Niveau 1 : si le DERNIER signal d'un camion
    MZoneX est un roulage récent (vitesse > SEUIL_VITESSE_ARRET, événement
    ≤ 15 min, tolérance horloge −5 min) et qu'AUCUNE ligne « en cours »
    n'existe à son jour d'attribution, la ligne est (ré)ouverte, datée de
    son vrai début de mouvement :

      · ligne REJETÉE alors qu'elle tournait encore (victime de la garde
        anti-géants antérieure au véto R1, ex. manœuvre de dépôt prolongée
        en vrai trajet) → réouverte EN_ATTENTE, DÉBUT CONSERVÉ (TCC exact) ;
      · aucune ligne du tout (plateforme lancée après le départ, événement
        manqué) → ligne créée PROVISOIRE au dernier DÉBUT_MOUVEMENT/REPRISE
        de la journée logistique (repli : dernier événement connu).

    CamtrackPro exclu : pas de flux temps réel fiable (borne §5). Idempotent.
    Retourne des compteurs (journal fenêtre noire)."""
    stats = {"controles": 0, "reouvertes": 0, "creees": 0}
    maintenant = maintenant or now_local()
    db = SessionLocal()
    try:
        seuils = get_seuils(db)
        seuil_v = float(seuils.get("SEUIL_VITESSE_ARRET", 3))
        debut_journee = bascule_du(maintenant)
        for vehicule in db.scalars(select(Vehicule)).all():
            if (vehicule.plateforme_gps or "").upper() == "CAMTRACKPRO":
                continue                      # borne §5 — pas de temps réel
            dernier = vehicule.last_event_at
            if dernier is None:
                continue
            age_s = (maintenant - dernier).total_seconds()
            if not (-300 <= age_s <= 900):    # signal périmé (ou futur aberrant)
                continue
            if (vehicule.last_vitesse or 0) <= seuil_v:
                continue                      # à l'arrêt → rien à rattraper
            stats["controles"] += 1
            jour = jour_attribution(maintenant)
            suivi = ensure_suivi(db, vehicule, jour)
            trajets = sorted(
                db.scalars(select(Trajet).where(
                    Trajet.suivi_id == suivi.id).order_by(Trajet.numero)).all(),
                key=lambda t: t.numero)
            if any(t.heure_fin is None and t.statut_validation
                   != StatutValidationTrajet.REJETE for t in trajets):
                continue                      # une ligne « en cours » existe
            # 1) R1 (14/08) + §0duodecies F2 (25/08) — RÉACTIVATION AVANT
            # CRÉATION : toute ligne REJETÉE qui couvre encore l'instant —
            # ouverte (R1 historique), OU refermée depuis moins de
            # DUREE_MIN_PAUSE_VALIDE (F2 : elle roulait encore il y a un
            # instant ; créer une nouvelle ligne par-dessus doublerait le
            # temps — cause du TCJ > TTJ du 25/08) — est RÉACTIVÉE avec son
            # VRAI début conservé, jamais remplacée par une ligne neuve.
            pause_min_s = float(seuils.get("DUREE_MIN_PAUSE_VALIDE", 1200))
            victime = next(
                (t for t in reversed(trajets)
                 if t.statut_validation == StatutValidationTrajet.REJETE
                 and (t.heure_fin is None
                      or 0 <= (maintenant - t.heure_fin).total_seconds()
                      < pause_min_s)),
                None)
            if victime is not None:
                etait_fermee = victime.heure_fin is not None
                victime.statut_validation = StatutValidationTrajet.EN_ATTENTE
                if etait_fermee:
                    victime.heure_fin = None     # F2 : la conduite continue
                _auditer(db, "trajet.reactivation", victime.id, {
                    "plaque": vehicule.plaque, "jour": jour.isoformat(),
                    "debut": iso(victime.heure_debut),
                    "etait_fermee": etait_fermee,
                    "fin_effacee": iso(victime.heure_fin) if etait_fermee else None,
                    "regle": "§0quater R1/R2 (14/08) + §0duodecies F2 (25/08) : "
                             "la ligne rejetée couvrait encore l'instant → "
                             "réactivée avec sa date de début exacte, aucune "
                             "nouvelle ligne empilée (anti-double-comptage) ; "
                             "le verdict manœuvre/trajet se rend à la clôture "
                             "seulement (§5.1)"})
                log.info("R2/F2 : %s — ligne réactivée (ouverte=%s), début %s "
                         "conservé", vehicule.plaque, not etait_fermee,
                         iso(victime.heure_debut))
                stats["reouvertes"] += 1
                recalculer_temps(db, suivi, maintenant)
                continue
            # 2) R2 — création datée du vrai début de mouvement de la journée
            if len(trajets) >= 25:
                continue                      # garde anti-bruit (Addendum v1.1)
            debut_ev = db.scalar(select(EvenementGPS).where(
                EvenementGPS.vehicule_id == vehicule.id,
                EvenementGPS.type_evenement.in_(
                    [TypeEvenement.DEBUT_MOUVEMENT, TypeEvenement.REPRISE]),
                EvenementGPS.horodatage >= debut_journee,
                EvenementGPS.horodatage <= maintenant,
            ).order_by(EvenementGPS.horodatage.desc()).limit(1))
            debut = debut_ev.horodatage if debut_ev is not None else dernier
            t = _nouveau_trajet(suivi, len(trajets) + 1, debut, vehicule,
                                SourceEvenement.MZONEX)
            db.add(t)
            db.flush()                     # t.id généré à l'INSERT (uid défaut)
            _auditer(db, "trajet.ouverture_rattrapage", t.id, {
                "plaque": vehicule.plaque, "jour": jour.isoformat(),
                "debut": iso(debut),
                "regle": "§0quater R2 (14/08/2026) : camion en route sans "
                         "ligne — ouverture recréée datée du vrai "
                         "DÉMARRAGE (§6.2 phase 1)"})
            log.info("§0quater R2 : %s — ligne « en cours » recréée au "
                     "DÉMARRAGE %s (roulage sans ligne constaté)",
                     vehicule.plaque, iso(debut))
            stats["creees"] += 1
            recalculer_temps(db, suivi, maintenant)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("§0quater R2 — échec du rattrapage d'ouvertures")
    finally:
        db.close()
    return stats


# ---------------------------------------------------------------- §0octies
# Rectificatif fuseau CamtrackPro (arbitrage LSS C1 du 20/08/2026, v1.28)

DECALAGE_FUSEAU_CAMTRACKPRO = timedelta(hours=3)
"""Décalage EXACT mesuré le 20/08/2026 entre les textes horaires du rapport
Wialon (UTC serveur) et l'affichage du portail (UTC+3) — preuve : cellule
brute « 2026-08-20 02:24:44 » / epoch « v » 1787192684 = 05:24:44 à l'écran."""

BORNE_CORRECTIF_FUSEAU = datetime(2026, 8, 19, 21, 0)
"""Fenêtre des lignes fautives : instants stockés en UTC naïf par la v1.26
(mise en service le 20/08/2026 ; rapport lu de 00:00 local à maintenant →
tout instant fautif est ≥ 19/08 21:00). Les lignes CAMTRACKPRO antérieures
(repli écran historique, heures DÉJÀ locales) sont < borne : jamais touchées.
"""

MARQUEUR_CORRECTIF_FUSEAU = "correctif_fuseau_camtrackpro_v1"


def corriger_fuseau_camtrackpro(db) -> int:
    """Rectifie UNE SEULE FOIS les heures des trajets N2 CamtrackPro stockés
    en UTC (§0octies C1 — option « correction automatique +3h » choisie par
    LSS le 20/08/2026).

    - **idempotent** : le marqueur d'audit interdit toute seconde application
      (inscrit dans la MÊME transaction que les décalages — tout ou rien) ;
    - **aucune donnée supprimée** : chaque ligne est DÉCALÉE de +3h00 exactes
      et, si le jour d'attribution (§8.1, bascule 01h00) change en
      conséquence, rattachée au suivi du bon jour ; la chaîne du jour puis la
      grille sont recalculées au cycle suivant (écran = export = archive
      §A.2) — seules des heures fausses disparaissent, au profit des vraies ;
    - **audité** : action « correctif_fuseau_camtrackpro_v1 » (borne,
      corrections, rattachements).
    """
    deja = db.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.action == MARQUEUR_CORRECTIF_FUSEAU))
    if deja:
        log.info("§0octies C1 : correctif fuseau CamtrackPro déjà appliqué "
                 "(marqueur d'audit présent) — aucune action.")
        return 0
    trajets = db.scalars(select(Trajet).where(
        Trajet.source_plateforme == "CAMTRACKPRO",
        Trajet.heure_debut >= BORNE_CORRECTIF_FUSEAU).order_by(
            Trajet.heure_debut)).all()
    rattaches = 0
    for t in trajets:
        t.heure_debut = t.heure_debut + DECALAGE_FUSEAU_CAMTRACKPRO
        if t.heure_fin is not None:
            t.heure_fin = t.heure_fin + DECALAGE_FUSEAU_CAMTRACKPRO
        suivi = db.get(SuiviJournalier, t.suivi_id)
        jour_cible = jour_attribution(t.heure_debut)
        if suivi is not None and suivi.date_jour != jour_cible:
            vehicule = db.get(Vehicule, suivi.vehicule_id)
            if vehicule is not None:
                t.suivi_id = ensure_suivi(db, vehicule, jour_cible).id
                rattaches += 1
    db.add(AuditLog(username="sys_correctif",
                    action=MARQUEUR_CORRECTIF_FUSEAU,
                    entite="trajet", entite_id=None,
                    details={"decalage_min": 180,
                             "borne_utc_naive":
                                 BORNE_CORRECTIF_FUSEAU.isoformat(),
                             "trajets_corriges": len(trajets),
                             "rattaches_au_bon_jour": rattaches,
                             "regle": "§0octies C1 (20/08/2026) : le serveur "
                                      "Wialon rend les heures du rapport en "
                                      "UTC (mesuré à la seconde) — décalage "
                                      "+3h exact, appliqué une seule fois"}))
    db.flush()
    db.commit()
    log.warning("§0octies C1 : correctif fuseau CamtrackPro appliqué UNE "
                "FOIS — %d trajet(s) décalé(s) de +3h (dont %d rattaché(s) "
                "au suivi du bon jour) ; journal d'audit inscrit.",
                len(trajets), rattaches)
    return len(trajets)


def auditer_boitiers_muets(db, maintenant: datetime | None = None) -> int:
    """§0nonies decies M4 (arbitrage LSS du 29/08/2026) — repère « boîtier
    muet — données en transit ».

    Quand un boîtier qui a ÉMIS aujourd'hui ne dit plus rien depuis
    SEUIL_GPS_HORS_LIGNE (30 min par défaut) pendant la plage d'exploitation
    04h00–22h00, un audit unique par camion et par jour est inscrit (le badge
    orange de l'écran est calculé à la volée par le serializer, cf. gps_age_s).
    Jamais de spam : la garde déduplique ; jamais de fausse alerte : un camion
    qui n'a RIEN émis du jour (repos/garage, ex. 2736TCC/3046TBS le 28/08)
    n'est pas signalé.
    """
    maintenant = maintenant or now_local()
    seuil = float(get_seuils(db).get("SEUIL_GPS_HORS_LIGNE", 1800))
    if not (4 <= maintenant.hour < 22):
        return 0
    jour = maintenant.date()
    debut_jour = datetime.combine(jour, datetime.min.time())
    nouveaux = 0
    for v in db.scalars(select(Vehicule)).all():
        if v.last_event_at is None:
            continue
        if v.last_event_at < debut_jour:
            continue                     # rien émis aujourd'hui → repos, normal
        age = (maintenant - v.last_event_at).total_seconds()
        if age <= seuil:
            continue
        deja = db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "vehicule.muet_jour",
            AuditLog.details.like(f'%"{v.plaque}"%'),
            AuditLog.details.like(f'%"{jour.isoformat()}"%'))) or 0
        if deja:
            continue
        db.add(AuditLog(username="systeme", action="vehicule.muet_jour",
                        entite="vehicule", entite_id=v.id,
                        details={"plaque": v.plaque, "jour": jour.isoformat(),
                                 "muet_depuis_s": int(age),
                                 "dernier_signal":
                                     v.last_event_at.isoformat(sep=" ",
                                                               timespec="seconds"),
                                 "regle": "§0nonies decies M4 (29/08/2026) : "
                                          "boîtier muet — données en transit "
                                          "(zone sans réseau probable ; la "
                                          "relecture M1 complètera seule)"}))
        nouveaux += 1
    if nouveaux:
        db.commit()
    return nouveaux
