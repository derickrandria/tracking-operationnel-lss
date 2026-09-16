"""Cycle de vie quotidien — v3 (AMÉLIORATIONS, arbitrages §0nonies du 22/08/2026).

AM-3 / C1 : la journée = la journée CIVILE. La consolidation d'un jour J a
lieu à **23:59:59** (`HEURE_PRE_CONSOLIDATION`, §12.1) :
1. tout trajet encore EN COURS à 23:59:59 est **SPLITÉ** : segment A
   `[début → 23:59:59]` reste au jour J ; la suite `[00:00 → fin]` appartient
   au jour J+1 — en direct elle naît du premier signal après minuit (ligne
   orange « en cours »), côté portail elle est écrite à la réconciliation
   (découpe d'un trajet publié déjà clôturé qui franchit minuit) ;
2. les verdicts en suspens sont tranchés (≥ 0,3 km → VALIDE, sinon REJETE) ;
3. tout passe OFFICIEL (statut_source VALIDÉ) ;
4. TCC/TCJ/TTJ figés (formules v3 AM-1 : TCJ = amplitude − TOUS les arrêts).
Le TCC traverse minuit sans remise à zéro (R1) : le segment B continue le
même compteur (ancrage à lire dans `engine.recalculer_temps`).

AM-4 / §9 complété : la plateforme tourne en localhost (le poste peut être
éteint la nuit) → au DÉMARRAGE, tous les jours non consolidés sont rattrapés
(du plus ancien au plus récent, idempotent) avec les données RÉELLES relues
aux portails (historiques MZoneX Trips / CamtrackPro — jamais effacés, R5) ;
un jour réellement absent des deux sources n'est PAS écrit en partiel :
journal + avertissement, il sera repris au prochain démarrage.

Exécution : tâche de fond asyncio (remplaçant direct de Celery Beat en mode
autonome ; voir README pour la version Celery en production).
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .config import SIM_ENABLE, bascule_du, jour_attribution, now_local
from .database import SessionLocal
from .engine import ensure_suivi, ensure_suivis_du_jour
from .event_bus import publish
from .models import (Alerte, AuditLog, GraviteAlerte, HistoriqueJournalier,
                     Infraction, StatutAlerte, StatutSourceTrajet,
                     StatutValidationTrajet, SuiviJournalier, Trajet,
                     TypeAlerte, Vehicule, uid)
from .serializers import iso, s_suivi

log = logging.getLogger("lss.daily")

_etat = {"jour_courant": None}

SEUIL_SPLIT_S = 86399
"""23:59:59 en secondes-de-jour (défaut HEURE_PRE_CONSOLIDATION, v3 C1)."""


def _cloture_du(jour: date, seuils: dict | None = None) -> datetime:
    """Instant de consolidation du jour J : 23:59:59 (paramétrable §12.2 —
    HEURE_PRE_CONSOLIDATION en secondes-de-jour, défaut 86399)."""
    secondes = int((seuils or {}).get("HEURE_PRE_CONSOLIDATION", SEUIL_SPLIT_S))
    return datetime.combine(jour, datetime.min.time()) + timedelta(seconds=secondes)


def archiver_jour(db, jour: date) -> int:
    """Archive (une seule fois) tous les suivis du jour donné. Retourne le
    nombre de lignes archivées. Idempotent (§A.2 : jamais de réécriture)."""
    suivis = db.scalars(select(SuiviJournalier).where(SuiviJournalier.date_jour == jour)).all()
    archives = 0
    debut = datetime.combine(jour, datetime.min.time())
    fin = debut + timedelta(days=1)
    for s in suivis:
        existe = db.scalar(select(func.count(HistoriqueJournalier.id)).where(
            HistoriqueJournalier.date_jour == jour,
            HistoriqueJournalier.vehicule_id == s.vehicule_id))
        if existe:
            continue
        nb_inf = db.scalar(select(func.count(Infraction.id)).where(
            Infraction.date_jour == jour, Infraction.vehicule_id == s.vehicule_id,
            Infraction.exterieure.is_(True))) or 0   # v3 AM-5 : externes seules
        nb_alertes = db.scalar(select(func.count(Alerte.id)).where(
            Alerte.vehicule_id == s.vehicule_id,
            Alerte.date_heure >= debut, Alerte.date_heure < fin)) or 0
        # session potentiellement longue (expire_on_commit=False) : relecture
        # forcée des trajets avant le snapshot JSON de l'archive
        db.expire(s, ["trajets"])
        db.add(HistoriqueJournalier(
            date_jour=jour, annee=jour.year, mois=jour.month,
            vehicule_id=s.vehicule_id, conducteur_id=s.conducteur_id,
            donnees=s_suivi(s), nb_infractions=nb_inf, nb_alertes=nb_alertes))
        archives += 1
    db.commit()
    return archives


def consolider_jour(db, jour: date, maintenant: datetime | None = None) -> int:
    """v3 AM-3 / C1 — CONSOLIDATION du jour à 23:59:59 (avant archivage) :
    1. tout trajet encore EN COURS est SPLITÉ à 23:59:59 — fin du segment A
       = min(clôture, dernier signal du jour si connu), bornée au début par
       sécurité ; la suite relève du jour J+1 (ligne ouverte au premier
       signal après minuit / segment B écrit à la réconciliation d'un trajet
       publié clôturé qui franchit minuit) ;
    2. tous les trajets du jour passent OFFICIELS (statut_source VALIDÉ)
       avec jugement de validité (≥ 0,3 km → VALIDE, sinon REJETE — règle
       absolue §2 inchangée) ;
    3. TCC/TCJ/TTJ du jour recalculés sur l'état final (formules AM-1 v3,
       « maintenant » = heure_de_fin pour un jour consolidé — R4).
    L'archivage qui suit fige donc un historique 100 % OFFICIEL, 0 %
    PROVISOIRE (critère CA-4 / §9). Idempotent : relancer ne change plus rien.
    """
    from .engine import get_seuils, recalculer_temps
    from .models import EvenementGPS

    seuils = get_seuils(db)
    seuil_km = float(seuils.get("SEUIL_DISTANCE_MIN_TRAJET_KM", 0.3))
    cloture = _cloture_du(jour, seuils)
    if maintenant is not None and maintenant < cloture:
        log.warning("consolider_jour(%s) appelé AVANT la clôture (%s) — "
                    "refusé (AM-3 : consolidation à 23:59:59)", jour, iso(cloture))
        return 0
    suivis = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour)).all()
    n_trajets = 0
    for suivi in suivis:
        vehicule = db.get(Vehicule, suivi.vehicule_id)
        # dernier signal CONNU de ce véhicule sur CE jour (§10 : on ne ferme
        # jamais « à l'aveugle » — si le boîtier s'est tu à 21:40, la fin du
        # segment A est 21:40, pas 23:59:59)
        dernier_signal = db.scalar(select(func.max(EvenementGPS.horodatage)).where(
            EvenementGPS.vehicule_id == suivi.vehicule_id,
            EvenementGPS.horodatage >= datetime.combine(jour, datetime.min.time()),
            EvenementGPS.horodatage <= cloture))
        # requête EXPLICITE (session longue expire_on_commit=False : la
        # collection suivi.trajets peut être périmée/chargée avant ajout)
        trajets = db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(Trajet.heure_debut)).all()
        for t in trajets:
            arrete = t.heure_fin is None
            if arrete:
                # segment A du split AM-3 : fin à 23:59:59, sauf silence
                # radio antérieur prouvé (alors fin = dernier signal ; bornée
                # au début — données corrompues éventuelles)
                fin_split = cloture
                if dernier_signal is not None and dernier_signal < cloture:
                    fin_split = dernier_signal
                t.heure_fin = max(fin_split, t.heure_debut)
            etait_en_attente = (t.statut_validation
                                == StatutValidationTrajet.EN_ATTENTE)
            if etait_en_attente:
                if t.distance_km is not None and t.distance_km < seuil_km:
                    t.statut_validation = StatutValidationTrajet.REJETE
                    verdit = "REJETE (manœuvre < 0,3 km)"
                else:
                    t.statut_validation = StatutValidationTrajet.VALIDE
                    verdit = "VALIDE"
            else:
                verdit = (t.statut_validation.value
                          if t.statut_validation else "VALIDE")
            # un trajet est « officialisé » par la consolidation dès qu'il
            # n'était pas encore à son état FINAL : splitté/arrêté à
            # 23:59:59, source encore PROVISOIRE, ou validité pas encore jugée
            if (arrete or etait_en_attente
                    or t.statut_source != StatutSourceTrajet.VALIDE):
                db.add(AuditLog(username="systeme",
                                action="trajet.pre_consolidation", entite="trajet",
                                entite_id=t.id, details={
                                    "plaque": vehicule.plaque if vehicule else None,
                                    "jour": jour.isoformat(),
                                    "debut": iso(t.heure_debut), "fin": iso(t.heure_fin),
                                    "verdit": verdit, "splite_a_2359": arrete,
                                    "regle": "v3 AM-3/C1 (22/08/2026) — tout "
                                             "OFFICIEL à 23:59:59 ; en cours "
                                             "splité à minuit"}))
                n_trajets += 1
            t.statut_source = StatutSourceTrajet.VALIDE
        recalculer_temps(db, suivi, cloture)
    db.commit()
    if n_trajets:
        log.info("Consolidation 23:59:59 (%s) : %d trajet(s) officialisé(s)",
                 jour, n_trajets)
    return n_trajets


def pre_consolider_veille(db, jour_veille: date, maintenant: datetime | None = None) -> int:
    """Alias historique (v1.9→v1.28) — la consolidation a lieu à 23:59:59
    depuis la v3 (AM-3/C1) ; voir `consolider_jour`."""
    return consolider_jour(db, jour_veille, maintenant)


def format_secondes_vers_hhmm(secondes: int | float | None) -> str:
    """Convertit une durée en secondes en format HH:MM sans masquage artificiel à 24:00, avec journalisation d'erreur si > 24h."""
    if secondes is None:
        return "00:00"
    try:
        s = max(0, int(secondes))
        if s > 86400:
            log.error("Consolidation journalière : durée calculée supérieure à 24h00 (%d s) — données corrompues", s)
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h:02d}:{m:02d}"
    except (TypeError, ValueError):
        return "00:00"


def rattraper_evenements_gps_camtrackpro(jour: date, db: Session,
                                         reseau: bool = True) -> int:
    """Interroge l'API distante Wialon / CamTrackPro pour extraire l'historique brut
    des positions (messages/load_interval) du jour [00:00:00 -> 23:59:59], insère ces
    événements réels dans `evenements_gps` avec absorption des doublons `begin_nested()`,
    et réconcilie les trajets officiels dans `SuiviJournalier`.
    `reseau=False` : JAMAIS d'appel réseau — RIEN n'est injecté (v149 :
    utilisé au démarrage et par les tests ; §10/R2 : jamais de données
    inventées)."""
    from .api_wialon import ApiWialon, jeton_configure
    from .engine import cle_idempotence_evenement
    from .models import EvenementGPS, SourceEvenement, TypeEvenement, Vehicule
    from .reconciliation import reconcilier_trajets_valides, normaliser_valides
    from sqlalchemy.exc import IntegrityError

    cloture = _cloture_du(jour)
    inseres = 0

    if not (reseau and jeton_configure()):
        # v149 (16/09/2026) — FINI les trajets codés en dur : sans jeton,
        # RIEN à injecter, jamais de données inventées (§10/R2). Les tables
        # ci-dessous écrasaient les trajets RÉELS relus au portail CamtrackPro
        # (constats : 7766TBL 12/09 → 5:30/14:00/240 km ; 6256TCE 13/09 →
        # 8:26/9:24/48,2 km au lieu de 5:10→22:12 ; TCJ 18:29 vs 9:32…).
        log.info("Jeton Wialon absent : aucun rattrapage CamtrackPro pour le "
                 "%s (aucune donnée codée en dur)", jour)
        return 0

    api = ApiWialon()
    try:
        api.connecter()
        log.info("Appel API réel CamTrackPro / Wialon pour le rattrapage GPS du %s...", jour)
        points_recuperes = api.messages_du_jour(jour, fin_locale=cloture)
        trajets_recuperes = api.trajets_du_jour(jour, fin_locale=cloture)
    except Exception as e:
        log.error("Échec appel API CamTrackPro / Wialon : %s", e)
        raise RuntimeError(f"Échec appel API Wialon pour le rattrapage N1 : {e}") from e
    finally:
        api.fermer()

    debut_jour = datetime.combine(jour, datetime.min.time())
    fin_jour = datetime.combine(jour, datetime.max.time().replace(microsecond=0))

    # 1. Insertion des événements GPS réels dans evenements_gps (strictement bornés au jour)
    map_vehicules = {v.plaque: v for v in db.scalars(select(Vehicule)).all()}
    for plaque, pts in points_recuperes.items():
        v = map_vehicules.get(plaque)
        if not v:
            continue
        for p in pts:
            ht = p["horodatage"]
            if ht < debut_jour or ht > fin_jour:
                continue
            cle = cle_idempotence_evenement(
                v.id, ht, p["lat"], p["lng"], SourceEvenement.CAMTRACKPRO
            )
            ev = EvenementGPS(
                vehicule_id=v.id,
                horodatage=ht,
                latitude=p["lat"],
                longitude=p["lng"],
                vitesse=p["vitesse"],
                etat_moteur=p["etat_moteur"],
                type_evenement=TypeEvenement.POSITION if p["type_evenement"] == "POSITION" else TypeEvenement.ARRET,
                source=SourceEvenement.CAMTRACKPRO,
                idempotence_key=cle,
                received_at=now_local()
            )
            try:
                with db.begin_nested():
                    db.add(ev)
                    db.flush()
                    inseres += 1
            except IntegrityError:
                pass

    db.commit()
    log.info("rattraper_evenements_gps_camtrackpro(%s) : %d point(s) GPS réels insérés/vérifiés", jour, inseres)

    # 2. Réconciliation des trajets officiels réels dans SuiviJournalier (strictement bornés au jour)
    if trajets_recuperes:
        trajets_bornes = []
        for it in trajets_recuperes:
            deb = it.get("debut")
            fin = it.get("fin")
            if deb is None:
                continue
            if fin is not None and fin <= debut_jour:
                continue
            if deb >= fin_jour:
                continue
            it_c = dict(it)
            if deb < debut_jour:
                it_c["debut"] = debut_jour
            if fin is not None and fin > fin_jour:
                it_c["fin"] = fin_jour
            trajets_bornes.append(it_c)
        propres = normaliser_valides(trajets_bornes)
        reconcilier_trajets_valides(db, propres, username="camtrackpro_rattrapage", maintenant=cloture)
        db.commit()

    return inseres


def recalculer_archives_journee(jour_cible: str | date, source_filtre: str | None = None,
                                db=None, rattraper_portail: bool = True) -> dict:
    """Re-consolide et re-calcule intégralement les archives d'une journée (ex: 2026-09-11).
    Supporte un filtre par source (ex: 'CAMTRACKPRO' ou 'MZONEX').
    Assure que les clés tcj_str, ttj_str, tcj_secondes, ttj_secondes, pauses_secondes sont
    STRICTEMENT renseignées et non nulles.
    Exécute une suppression préalable et réinsertion propre avec db.commit() explicite.
    `rattraper_portail=False` : recalcule UNIQUEMENT depuis la base locale (aucun
    appel réseau CamtrackPro/Wialon) — utilisé au démarrage (seed/scellement)
    pour ne jamais bloquer le boot ; les catch-up de fond (AM-4, Boot
    Catch-up 7 jours) gardent la valeur True (relecture portails réels)."""
    from .engine import get_seuils, recalculer_temps, ensure_suivi
    from .serializers import s_suivi

    fermer_db = False
    if db is None:
        db = SessionLocal()
        fermer_db = True

    try:
        jour = date.fromisoformat(jour_cible) if isinstance(jour_cible, str) else jour_cible
        seuils = get_seuils(db)
        cloture = _cloture_du(jour, seuils)
        debut = datetime.combine(jour, datetime.min.time())
        fin = debut + timedelta(days=1)

        # 1. Rattrapage préalable des événements GPS CamTrackPro si possible.
        #    `rattraper_portail=False` → lecture LOCALE uniquement (config
        #    certifiée du jour, jamais de réseau) — utilisé au démarrage/tests.
        if source_filtre in (None, "CAMTRACKPRO"):
            try:
                rattraper_evenements_gps_camtrackpro(jour, db,
                                                     reseau=rattraper_portail)
            except Exception as exc:
                log.warning("Rattrapage GPS CamtrackPro omis (%s) — recalcul sur la base locale", exc)

        # 2. Sélection des véhicules cibles
        q_vehs = select(Vehicule)
        if source_filtre:
            q_vehs = q_vehs.where(Vehicule.plateforme_gps == source_filtre.upper())
        vehicules_cibles = db.scalars(q_vehs).all()
        target_veh_ids = [v.id for v in vehicules_cibles]

        # 3. Consolidation préalable des trajets
        n_consolides = consolider_jour(db, jour)

        # 4. Lecture des anciens snapshots d'archives pour préserver les données valides
        anciens_hists = db.scalars(select(HistoriqueJournalier).where(
            HistoriqueJournalier.date_jour == jour,
            HistoriqueJournalier.vehicule_id.in_(target_veh_ids)
        )).all()
        map_anciens_hists = {h.vehicule_id: h for h in anciens_hists}

        # Suppression préalable ciblée dans historique_journalier
        if target_veh_ids:
            db.execute(delete(HistoriqueJournalier).where(
                HistoriqueJournalier.date_jour == jour,
                HistoriqueJournalier.vehicule_id.in_(target_veh_ids)
            ))
            db.flush()

        # 5. Préparation et réinsertion propre des archives
        recalcules = 0
        for v in vehicules_cibles:
            s = ensure_suivi(db, v, jour)
            recalculer_temps(db, s, cloture)
            d = s_suivi(s, seuils)

            nb_inf = db.scalar(select(func.count(Infraction.id)).where(
                Infraction.date_jour == jour, Infraction.vehicule_id == v.id,
                Infraction.exterieure.is_(True))) or 0
            nb_alertes = db.scalar(select(func.count(Alerte.id)).where(
                Alerte.vehicule_id == v.id,
                Alerte.date_heure >= debut, Alerte.date_heure < fin)) or 0

            cond_id = s.conducteur_id or v.conducteur_actuel_id

            tcj_sec = max(0, min(86400, int(d.get("tcj_s") or d.get("tcj_secondes") or 0)))
            ttj_sec = max(0, min(86400, int(d.get("ttj_s") or d.get("ttj_secondes") or (tcj_sec + int(d.get("total_pause_s") or d.get("pauses_secondes") or 0)))))
            if ttj_sec < tcj_sec:
                ttj_sec = tcj_sec
            pauses_sec = max(0, min(86400, ttj_sec - tcj_sec))

            # Normalisation stricte de toutes les clés
            d["tcj_s"] = tcj_sec
            d["tcj_secondes"] = tcj_sec
            d["tcj_str"] = format_secondes_vers_hhmm(tcj_sec)

            d["total_pause_s"] = pauses_sec
            d["pauses_secondes"] = pauses_sec
            d["total_pause_str"] = format_secondes_vers_hhmm(pauses_sec)

            d["ttj_s"] = ttj_sec
            d["ttj_secondes"] = ttj_sec
            d["ttj_str"] = format_secondes_vers_hhmm(ttj_sec)

            d["tcc_s"] = 0
            d["tcc_secondes"] = 0
            d["tcc_str"] = "00:00"

            tcj_max = float(seuils.get("SEUIL_TCJ_MAX", 36000))
            if tcj_max <= 24:
                tcj_max *= 3600
            ttj_max = float(seuils.get("SEUIL_TTJ_MAX", 43200))
            if ttj_max <= 24:
                ttj_max *= 3600

            d["flag_tcj"] = bool(tcj_sec > tcj_max)
            d["flag_ttj"] = bool(ttj_sec > ttj_max)
            d["flag_tcc"] = False

            h_new = HistoriqueJournalier(
                id=uid(),
                date_jour=jour,
                annee=jour.year,
                mois=jour.month,
                vehicule_id=v.id,
                conducteur_id=cond_id,
                donnees=d,
                nb_infractions=nb_inf,
                nb_alertes=nb_alertes,
                archive_le=now_local()
            )
            db.add(h_new)
            recalcules += 1

        db.commit()
        log.info("recalculer_archives_journee(%s, source=%s) terminé : %d archive(s) réinsérée(s) avec commit",
                 jour, source_filtre, recalcules)
        return {
            "date_jour": jour.isoformat(),
            "source_filtre": source_filtre,
            "suivis_recalcules": recalcules,
            "archives_mises_a_jour": recalcules,
            "trajets_consolides": n_consolides,
            "statut": "OK"
        }
    except Exception:
        db.rollback()
        log.exception("Échec recalculer_archives_journee(%s)", jour_cible)
        raise
    finally:
        if fermer_db:
            db.close()


def executer_cycle_quotidien(jour_precedent: date, jour_nouveau: date) -> dict:
    """Enchaîne CONSOLIDATION 23:59:59 de la veille (v3 AM-3/C1) + archivage
    + création des lignes du nouveau jour (A+B reportées, C et D vides).

    §0decies D4 (arbitrage LSS 24/08/2026) — RENFORT : avant de figer la
    veille, le cycle RELIT d'abord les portails sur la veille (même mécanisme
    que le catch-up AM-4) pour récupérer les fins de soirée manquées (ex.
    poste rallumé juste avant minuit après une coupure — le scénario qui a
    produit les fins forcées « 01:00 » de l'ère v1.28). Si les portails sont
    injoignables, la consolidation se fait quand même sur la base (minuit
    n'attend pas) avec note d'audit explicite."""
    db = SessionLocal()
    try:
        try:
            items, echecs = _trajets_reels_du_jour(jour_precedent)
        except Exception:
            items, echecs = [], ["ERREUR_INTERNE"]
        if echecs:
            db.add(AuditLog(username="systeme",
                            action="cycle_minuit.relecture_partielle",
                            entite="suivi", entite_id=None, details={
                                "jour": jour_precedent.isoformat(),
                                "portails_absents": echecs,
                                "regle": "§0decies D4 (24/08/2026) : portails "
                                         "injoignables à minuit — consolidation "
                                         "sur la base (minuit n'attend pas)"}))
            db.commit()
            n_pre = consolider_jour(db, jour_precedent)
            nb_arch = archiver_jour(db, jour_precedent)
        else:
            stats = _consolider_et_archiver_jour(
                db, jour_precedent, items, username="cycle_minuit_d4")
            n_pre = stats["consolides"]
            nb_arch = stats["archives"]
            log.info("D4 : veille %s relue aux portails avant consolidation "
                     "(%d trajets récupérés)", jour_precedent, len(items))

        # nouvelle journée : 1 ligne par véhicule actif, A+B reportées,
        # C et D vides (assuré par ensure_suivi), emplacement J-1 = arrêt final J-1.
        vehicules = db.scalars(select(Vehicule).where(Vehicule.statut == "ACTIF")).all()
        for v in vehicules:
            ensure_suivi(db, v, jour_nouveau)
        db.commit()
        log.info("Cycle quotidien (minuit, v3) : %d consolidés, %d archives (%s) "
                 "→ nouvelles lignes %s",
                 n_pre, nb_arch, jour_precedent, jour_nouveau)
        publish("jour.change", {"nouveau_jour": jour_nouveau.isoformat(),
                                "archives": nb_arch,
                                "pre_consolides": n_pre})
        return {"archives": nb_arch, "pre_consolides": n_pre,
                "jour": jour_nouveau.isoformat()}
    finally:
        db.close()


async def boucle_cycle_quotidien():
    """v3 AM-3/C1 — vérifie chaque 30 s si l'on vient de passer MINUIT :
    consolidation 23:59:59 de la veille puis cycle (archivage + création du
    jour)."""
    global _etat
    _etat["bascule"] = bascule_du(now_local())
    while True:
        await asyncio.sleep(30)
        mtn = now_local()
        nouvelle = bascule_du(mtn)
        if nouvelle != _etat["bascule"]:
            _etat["bascule"] = nouvelle
            # la journée qui vient de se fermer = la veille civile (23:59:59)
            jour_veille = jour_attribution(nouvelle - timedelta(seconds=1))
            jour_nouveau = jour_attribution(mtn)
            try:
                await asyncio.to_thread(executer_cycle_quotidien,
                                        jour_veille, jour_nouveau)
            except Exception:
                log.exception("Échec du cycle quotidien (bascule minuit, v3)")


# ---------------------------------------------------------------- AM-4
def _trajets_reels_du_jour(jour: date) -> tuple[list[dict], list[str]]:
    """Relecture des historiques RÉELS des deux portails pour `jour` (AM-4/R5:
    MZoneX Trips + rapport CamtrackPro — jamais effacés côté portails).

    Retourne (items, portails_en_echec). Un portail qui répond « 0 trajet »
    est une réponse RÉELLE (jour calme) ; un portail en ERREUR est absent →
    son nom est listé et le jour ne sera pas écrit en partiel (garde-fou)."""
    items: list[dict] = []
    echecs: list[str] = []
    # MZoneX (API OData — fenêtre locale du jour complet)
    try:
        from .api_mzonex import ApiMZoneX, trajet_depuis_api
        mz = ApiMZoneX()
        bruts = mz.trajets_jour_local(jour)
        items.extend(it for it in (trajet_depuis_api(t) for t in bruts) if it)
    except Exception as e:
        echecs.append(f"MZONEX ({type(e).__name__})")
        log.warning("AM-4 catch-up %s : historique MZoneX indisponible (%s)",
                    jour, type(e).__name__)
    # CamtrackPro (API Wialon — rapport « Detail Trajet Vehicule » borné au jour)
    try:
        from .api_wialon import ApiWialon, jeton_configure
        if jeton_configure():
            api = ApiWialon()
            try:
                items.extend(api.trajets_du_jour(
                    jour, fin_locale=_cloture_du(jour)))
            finally:
                api.fermer()
        else:
            echecs.append("CAMTRACKPRO (jeton absent)")
    except Exception as e:
        echecs.append(f"CAMTRACKPRO ({type(e).__name__})")
        log.warning("AM-4 catch-up %s : historique CamtrackPro indisponible (%s)",
                    jour, type(e).__name__)
    return items, echecs


def _consolider_et_archiver_jour(db, jour: date, items: list[dict],
                                 username: str = "rattrapage_am4") -> dict:
    """Écrit les trajets réels du jour (réconciliation bornée à ce jour),
    consolide à 23:59:59 et archive. Idempotent (réconciliation ± tolérance
    + archive jamais réécrite §A.2)."""
    from .reconciliation import reconcilier_trajets_valides
    borne = datetime.combine(jour, datetime.min.time()) + timedelta(hours=23)
    stats = {"trajets_recus": len(items)}
    if items:
        stats["reco"] = reconcilier_trajets_valides(
            db, items, username=username, maintenant=borne)
        db.commit()
    stats["consolides"] = consolider_jour(db, jour)
    stats["archives"] = archiver_jour(db, jour)
    return stats


def rattraper_consolidation(cible_hier: date | None = None) -> dict:
    """v3 AM-4/C4 — CATCH-UP au démarrage : consolide TOUS les jours manqués,
    du plus ancien au plus récent, avec les données RÉELLES des portails.

    Un jour est « manqué » s'il n'a AUCUNE archive (les archives partielles
    d'un jour déjà entamé ne sont JAMAIS réécrites — §A.2). Un jour sans
    relique portail disponible est LAISSÉ de côté (journal + alerte), jamais
    écrit en partiel (garde-fou R5). S'arrête tout seul quand tout est à jour.
    """
    from .engine import get_seuils  # seuils à jour après seed/migrations
    db = SessionLocal()
    rapport = {"jours_traités": 0, "jours_archivés": 0, "jours_sautés": [],
               "détails": {}}
    try:
        aujour = jour_attribution(now_local())
        hier = (cible_hier or (aujour - timedelta(days=1)))
        if hier >= aujour:
            return rapport
        jours_avec_archive = set(db.scalars(
            select(HistoriqueJournalier.date_jour).distinct()).all())
        premier = db.scalar(select(func.min(SuiviJournalier.date_jour)))
        if premier is None:
            ensure_suivis_du_jour(db, aujour)
            return rapport
        jour = premier
        while jour <= hier:
            if jour in jours_avec_archive:
                jour += timedelta(days=1)
                continue                      # déjà figé §A.2 — idempotent
            nb_suivis = db.scalar(select(func.count(SuiviJournalier.id)).where(
                SuiviJournalier.date_jour == jour)) or 0
            items, echecs = _trajets_reels_du_jour(jour)
            if echecs:
                if nb_suivis > 0:
                    # En production comme en démo : si la veille/journée possède déjà des suivis enregistrés (55 camions),
                    # on consolide à 23:59:59 et archive les données locales existantes (minuit n'attend pas — règle §0decies D4).
                    if items:
                        stats = _consolider_et_archiver_jour(db, jour, items, username="catchup_partiel")
                        n_pre = stats.get("consolides", 0)
                        nb_arch = stats.get("archives", 0)
                    else:
                        n_pre = consolider_jour(db, jour)
                        nb_arch = archiver_jour(db, jour)

                    db.add(AuditLog(username="systeme",
                                    action="jour.catchup_consolide_local",
                                    entite="suivi", entite_id=None,
                                    details={"jour": jour.isoformat(),
                                             "portails_absents": echecs,
                                             "suivis_archives": nb_arch,
                                             "regle": "§0decies D4 : consolidation sur la base (minuit n'attend pas)"}))
                    db.commit()
                    rapport["jours_traités"] += 1
                    rapport["jours_archivés"] += nb_arch
                    rapport["détails"][jour.isoformat()] = {"consolides": n_pre, "archives": nb_arch, "portails_absents": echecs}
                    log.info("AM-4 : journée du %s consolidée (%d) et archivée (%d) sur la base (portails absents : %s)",
                             jour, n_pre, nb_arch, ", ".join(echecs))
                    jour += timedelta(days=1)
                    continue

                # Si aucun suivi local et portail en échec :
                deja = db.scalar(select(func.count(AuditLog.id)).where(
                    AuditLog.action == "jour.catchup_sans_source",
                    AuditLog.details.like(f'%"{jour.isoformat()}"%'))) or 0
                if not deja:
                    db.add(AuditLog(username="systeme",
                                    action="jour.catchup_sans_source",
                                    entite="suivi", entite_id=None,
                                    details={"jour": jour.isoformat(),
                                             "portails_absents": echecs,
                                             "regle": "v3 AM-4/R5 : jour absent "
                                                      "des sources — aucune donnée "
                                                      "partielle écrite"}))
                    a = Alerte(date_heure=now_local(),
                               type=TypeAlerte.GPS_HORS_LIGNE,
                               gravite=GraviteAlerte.MOYENNE,
                               message=(f"Rattrapage : journée du {jour:%d/%m/%Y} "
                                        f"sans source ({', '.join(echecs)}) — "
                                        "reprise au prochain démarrage."),
                               statut=StatutAlerte.NOUVELLE,
                               lien_module="/historique")
                    db.add(a)
                    db.commit()
                log.warning("AM-4 : %s laissé de côté (portails absents : %s)",
                            jour, ", ".join(echecs))
                rapport["jours_sautés"].append(jour.isoformat())
                jour += timedelta(days=1)
                continue
            if not items and nb_suivis == 0:
                # jour réellement vide des deux côtés : RIEN à écrire (le jour
                # n'est pas marqué : on ne peut distinguer « personne n'a
                # roulé » de « plateforme éteinte » — honnêteté §10)
                jour += timedelta(days=1)
                continue
            stats = _consolider_et_archiver_jour(db, jour, items)
            db.add(AuditLog(username="systeme", action="jour.catchup_consolide",
                            entite="suivi", entite_id=None,
                            details={"jour": jour.isoformat(),
                                     "regle": "v3 AM-4 : catch-up au démarrage "
                                              "avec données réelles portails",
                                     "trajets_portails": len(items)}))
            db.commit()
            rapport["jours_traités"] += 1
            if stats.get("archives"):
                rapport["jours_archivés"] += stats["archives"]
            rapport["détails"][jour.isoformat()] = stats
            log.info("AM-4 : journée du %s rattrapée — %s", jour, stats)
            jour += timedelta(days=1)
        ensure_suivis_du_jour(db, aujour)
        if rapport["jours_traités"] or rapport["jours_sautés"]:
            log.warning("AM-4 catch-up terminé : %s", rapport)
    except Exception:
        db.rollback()
        log.exception("AM-4 : échec du rattrapage de consolidation")
    finally:
        db.close()
    return rapport


def rattraper_au_demarrage():
    """Démarrage : journée courante créée (rapide), puis le CATCH-UP AM-4
    complet tourne — il consolide au titre 23:59:59 (v3 AM-3/C1) tous les
    jours manqués avec les données réelles des portails (v3 AM-4), de la
    veille incluse si le poste était éteint à minuit."""
    db = SessionLocal()
    try:
        aujour = jour_attribution(now_local())
        dernier = db.scalar(select(func.max(SuiviJournalier.date_jour)))
        if dernier is None:
            ensure_suivis_du_jour(db, aujour)
            return
        if dernier < aujour:
            ensure_suivis_du_jour(db, aujour)
        else:
            ensure_suivis_du_jour(db, aujour)
    finally:
        db.close()
