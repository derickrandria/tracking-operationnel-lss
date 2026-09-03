"""Réparation embarquée des journées abîmées — v1.30 (arbitrages §0decies du
24/08/2026, mandants).

CONTEXTE — l'ancienne bascule 01h00 (§0bis B, abrogée par §0nonies C1) a figé
des journées avec des fins FORCÉES à 01:00 : poste éteint le soir (ex. vendredi
21/08) → fins de trajets jamais reçues, journée archivée ainsi par la v1.28 au
redémarrage, et §A.2 interdit toute réécriture automatique. S'y ajoute la
journée du 20/08 (0906TBV : trajet du milieu perdu — §0nonies C4). L'exploitant
ne pouvant pas remettre sa base, la vérité = les PORTAILS (R5 : historiques
jamais effacés côté portails) → la réparation s'exécute CHEZ LUI, ici.

RÈGLES (§0decies) :
 D1 — contrôle complet du 19/08/2026 (BORNE_DEBUT) à la veille du premier
      démarrage v1.30 ; chaque journée est relue aux portails (mêmes mécanismes
      qu'AM-4) puis comparée trajet à trajet avec la base ; SEULES les journées
      À ÉCART sont réparées ; journée conforme → AUCUNE écriture.
 D2 — archives des journées réparées régénérées (exception ponctuelle §A.2,
      arbitrée), journal AVANT/APRÈS par véhicule, alerte récapitulative ;
      JAMAIS de suppression : lignes fautives marquées REJETÉES (conservées,
      masquées — extension AM-2/R2).
 D3 — lancement automatique, une seule fois, après le catch-up AM-4, avec
      marqueurs (global + par journée) : une journée sautée pour portail
      indisponible (garde-fou R5) est reprise au démarrage suivant.

MÉTHODE (déterministe, sans mutation tant que la journée n'est pas jugée) :
 1. relecture portails du jour (`daily._trajets_reels_du_jour`) ; segment B
    [00:00 → fin] des trajets franchissant minuit posé au jour J+1 (split d'
    écriture AM-3, marqueur `suite_minuit`) ;
 2. appariement par le DÉBUT (± tolérance § SEUIL_TOLERANCE_RAPPROCHEMENT_) —
    ligne sans correspondant au portail → REJETÉE (fantôme / clôture 01:00) ;
    correspondant de type manœuvre (< 0,3 km) → REJETÉE (verdict §2/§7) ;
    correspondant valide divergent (fin/distance) → GUÉRI en place (remplace
    les horaires par ceux du portail : guérit les fins forcées ET décompose
    les fusions d'affichage de l'ère ≤ v1.28 en lignes propres AM-2) ;
 3. trajets valides du portail SANS correspondant en base → INSÉRÉS (trajet
    perdu, ex. 0906TBV du 20/08) ;
 4. recalcul chaîne (pauses, TCC/TCJ/TTJ v3), consolidation 23:59:59,
    archives régénérées, journal AVANT/APRÈS, alerte.
"""
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from .config import calculer_tokens_set, jour_attribution, normaliser_libelle, now_local
from .daily import (_trajets_reels_du_jour as _relecture_portails,
                    archiver_jour, consolider_jour)
from .database import SessionLocal
from .engine import ensure_suivi, get_seuils
from .models import (Alerte, AuditLog, Conducteur, GraviteAlerte,
                     HistoriqueJournalier, Infraction, Mission,
                     StatutAlerte, StatutSourceTrajet, StatutValidationTrajet,
                     SuiviJournalier, Trajet, TypeAlerte, Vehicule)
from .reconciliation import (_badge_eco, _propager, _recalculer_pauses,
                             _renumeroter, _spliter_minuit,
                             _synchroniser_archive)
from .serializers import iso, s_conducteur

log = logging.getLogger("lss.reparation")

BORNE_DEBUT = date(2026, 8, 19)          # §0decies D1 — début de la fenêtre
MARQUEUR_GLOBAL = "reparation_v130.terminee"
ACT_REPARATION = "jour.repare_v130"
ACT_CONFORME = "jour.verifie_conforme_v130"
USERNAME = "reparation_v130"
REGLE = ("§0decies D1/D2/D3 (24/08/2026) : relecture portails, réparation "
         "sans suppression, archives régénérées (exception arbitrée §A.2)")


# ---------------------------------------------------------------- utilitaires
def _audit(db, action: str, entite_id, details: dict, entite: str = "trajet"):
    db.add(AuditLog(username=USERNAME, action=action, entite=entite,
                    entite_id=entite_id, details=details))


def _marque_existe(db, action: str, jour: date) -> bool:
    return bool(db.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.action == action,
        AuditLog.details.like(f'%"{jour.isoformat()}"%'))))


def _cle(it: dict) -> str:
    return str(it.get("vehicule_id") or it.get("gps_associe")
               or it.get("plaque") or "").strip().upper()


def _displayable(it: dict, seuil_km: float) -> bool:
    """Occupe une ligne à l'écran : distance inconnue ou ≥ seuil (§2/§7)."""
    d = it.get("distance_km")
    return d is None or d >= seuil_km


def _est_probable_segment_b(t: Trajet) -> bool:
    """Segment B hérité d'avant la colonne `suite_minuit` (v1.29) : début pile
    à 00:00:00, distance non répartie (AM-3). Exempté du masquage, comme un
    segment B marqué — le portail ne republie pas un trajet au jour de sa fin."""
    return (t.heure_debut is not None
            and t.heure_debut.time() == time(0, 0)
            and t.distance_km is None)


def _snap(t: Trajet) -> dict:
    return {"id": t.id, "debut": iso(t.heure_debut), "fin": iso(t.heure_fin),
            "distance_km": t.distance_km,
            "source": t.statut_source.value if t.statut_source else None,
            "validation": (t.statut_validation.value
                           if t.statut_validation else None)}


# ------------------------------------------------------ plan (aucune mutation)
def _plan_suivi(trajets: list[Trajet], items_v: list[dict],
                tolerance: float, seuil_km: float):
    """Compare lignes DB (affichables, hors segments B) / trajets du portail.

    Retourne (heals, masks, inserts) :
      heals   = [(trajet_db, item)]           lignes divergentes → guéries
      masks   = [(trajet_db, item|None, motif)] lignes à masquer (REJETÉES)
      inserts = [item]                        trajets portail absents → créés
    """
    lignes = [t for t in trajets
              if t.statut_validation != StatutValidationTrajet.REJETE
              and not t.suite_minuit and not _est_probable_segment_b(t)]
    tous = sorted(items_v, key=lambda it: (it["debut"],
                                           it.get("fin") or it["debut"]))
    heals, masks, inserts, pris = [], [], [], set()
    for t in sorted(lignes, key=lambda x: x.heure_debut):
        cand, dmin = None, None
        for it in tous:
            d = abs((it["debut"] - t.heure_debut).total_seconds())
            if dmin is None or d < dmin:
                cand, dmin = it, d
        if cand is None or dmin > tolerance:
            masks.append((t, None, "sans_source_portail"))
            continue
        if not _displayable(cand, seuil_km):
            masks.append((t, cand, "verdict_manoeuvre_portail"))
            continue
        if id(cand) in pris:
            masks.append((t, cand, "doublon_meme_debut"))
            continue
        pris.add(id(cand))
        fin_p = cand.get("fin")
        d_fin = (abs((t.heure_fin - fin_p).total_seconds())
                 if (t.heure_fin and fin_p) else (0.0 if t.heure_fin == fin_p
                                                  else float("inf")))
        d_dist = (abs((t.distance_km or 0.0) - cand["distance_km"])
                  if (t.distance_km is not None
                      and cand.get("distance_km") is not None) else 0.0)
        if d_fin > tolerance or d_dist > 0.05:
            heals.append((t, cand))
    inserts = [it for it in tous if _displayable(it, seuil_km)
               and id(it) not in pris]
    return heals, masks, inserts


# ---------------------------------------------------- segments B (minuit, AM-3)
def _poser_segments_b(db, jour: date, items_splite: list[dict],
                      mapping: dict, tolerance: float) -> int:
    """Poser au jour J+1 les segments B (00:00 → …) nés du split d'écriture
    des trajets du jour J franchissant minuit (v3 AM-3/C1). Idempotent : un
    segment B déjà présent (marqué, ou hérité non marqué) n'est pas doublé.
    L'archive du jour J+1, si elle existe déjà (journée vérifiée plus tôt), est
    resynchronisée aussitôt — sinon le segment posé après coup n'y entrerait
    jamais (§3.3, même mécanisme que la réconciliation)."""
    poses = 0
    suivis_touches: set[str] = set()
    for it in items_splite:
        if not it.get("suite_minuit"):
            continue
        if jour_attribution(it["debut"]) != jour + timedelta(days=1):
            continue
        vehicule = mapping.get(_cle(it))
        if vehicule is None:
            continue                       # inconnu : audité au niveau du jour
        jour_b = jour + timedelta(days=1)
        suivi_b = ensure_suivi(db, vehicule, jour_b)
        deja = db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi_b.id)).all()
        if any((t.suite_minuit or _est_probable_segment_b(t))
               and abs((t.heure_debut - it["debut"]).total_seconds()) <= tolerance
               for t in deja):
            continue
        numero = (max((t.numero for t in deja), default=0) + 1)
        nouveau = Trajet(suivi_id=suivi_b.id, numero=numero,
                         heure_debut=it["debut"], heure_fin=it.get("fin"),
                         statut_source=StatutSourceTrajet.VALIDE,
                         source_plateforme=it.get("source"),
                         distance_km=None,
                         statut_validation=StatutValidationTrajet.VALIDE,
                         suite_minuit=True)
        db.add(nouveau)
        db.flush()
        _audit(db, "trajet.repare_v130_segment_b", nouveau.id, {
            "plaque": vehicule.plaque, "jour": jour_b.isoformat(),
            "debut": iso(it["debut"]), "fin": iso(it.get("fin")),
            "regle": REGLE + " — segment B posé au lendemain (AM-3/C1)"})
        log.info("Réparation : segment B posé (%s %s→%s)", vehicule.plaque,
                 iso(it["debut"]), iso(it.get("fin")))
        poses += 1
        suivis_touches.add((suivi_b.id, jour_b))
    for sid, jour_b in suivis_touches:
        s_b = db.get(SuiviJournalier, sid)
        if s_b is not None:
            _synchroniser_archive(db, s_b)   # met à jour l'archive existante
        archiver_jour(db, jour_b)            # idempotent : ne crée que l'absent
    return poses


# ------------------------------------------------------------- archives (D2)
def _resume_archive(donnees: dict) -> dict:
    trajets = donnees.get("trajets") or []
    return {"nb_trajets": donnees.get("nb_trajets", len(trajets)),
            "heure_depart": donnees.get("heure_depart"),
            "tcc_s": donnees.get("tcc_s"), "tcj_s": donnees.get("tcj_s"),
            "ttj_s": donnees.get("ttj_s"),
            "lignes": [{"debut": t.get("heure_debut"), "fin": t.get("heure_fin"),
                        "distance_km": t.get("distance_km")}
                       for t in trajets]}


def _reecrire_archives_jour(db, jour: date, plaques: dict) -> dict:
    """Régénère les archives du jour depuis l'état réparé (EXCEPTION §A.2
    arbitrée §0decies D2) avec journal AVANT/APRÈS par véhicule."""
    existantes = db.scalars(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == jour)).all()
    avant = {plaques.get(h.vehicule_id, h.vehicule_id):
             _resume_archive(dict(h.donnees or {})) for h in existantes}
    for h in existantes:
        db.delete(h)
    db.flush()
    archiver_jour(db, jour)
    nouvelles = db.scalars(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == jour)).all()
    apres = {plaques.get(h.vehicule_id, h.vehicule_id):
             _resume_archive(dict(h.donnees or {})) for h in nouvelles}
    _audit(db, "jour.archive_reecrite_v130", None, {
        "jour": jour.isoformat(), "avant": avant, "apres": apres,
        "regle": REGLE}, entite="suivi")
    return {"avant": avant, "apres": apres}


# ----------------------------------------------------------------- une journée
def verifier_et_reparer_jour(db, jour: date, items: list[dict]) -> dict:
    """D1 : compare la journée à la relecture portails et la répare si écart.
    Retourne un rapport {conforme, guéries, masquées, insérés, segments_b}."""
    seuils = get_seuils(db)
    tolerance = float(seuils.get("SEUIL_TOLERANCE_RAPPROCHEMENT_TRAJET", 120))
    seuil_km = float(seuils.get("SEUIL_DISTANCE_MIN_TRAJET_KM", 0.3))

    # split d'écriture (AM-3) : A au jour J, B [00:00 → …] au jour J+1
    items = _spliter_minuit([dict(it) for it in items])
    par_vehicule: dict[str, list[dict]] = {}
    for it in items:
        if it.get("suite_minuit"):
            continue                       # traité par _poser_segments_b
        if jour_attribution(it["debut"]) != jour:
            continue
        par_vehicule.setdefault(_cle(it), []).append(it)

    mapping: dict[str, Vehicule] = {}
    plaques: dict[str, str] = {}
    for v in db.scalars(select(Vehicule)).all():
        mapping[v.plaque.strip().upper()] = v
        mapping[str(v.id).strip().upper()] = v
        if v.gps_associe:
            mapping[str(v.gps_associe).strip().upper()] = v
        plaques[v.id] = v.plaque

    suivis = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour)).all()
    nb_trajets_portails = sum(len(v) for v in par_vehicule.values())
    plans: list[tuple] = []
    inconnus: set[str] = set()
    actions = 0
    for suivi in suivis:
        vehicule = db.get(Vehicule, suivi.vehicule_id)
        trajets = list(db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(Trajet.heure_debut)).all())
        items_v = []
        for cle in (vehicule.plaque.strip().upper(),
                    str(vehicule.id).upper(),
                    str(vehicule.gps_associe or "").strip().upper()):
            items_v = par_vehicule.pop(cle, None) or items_v
        heals, masks, inserts = _plan_suivi(trajets, items_v, tolerance,
                                            seuil_km)
        plans.append((suivi, vehicule, heals, masks, inserts))
        actions += len(heals) + len(masks) + len(inserts)

    # véhicules du portail SANS suivi du jour : leur journée est à construire
    for cle, items_v in par_vehicule.items():
        vehicule = mapping.get(cle)
        if vehicule is None:
            inconnus.add(cle)
            continue
        suivi = ensure_suivi(db, vehicule, jour)
        trajets = list(db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id)).all())
        heals, masks, inserts = _plan_suivi(trajets, items_v, tolerance,
                                            seuil_km)
        plans.append((suivi, vehicule, heals, masks, inserts))
        actions += len(heals) + len(masks) + len(inserts)
    if inconnus:
        _audit(db, "jour.repare_v130_vehicules_inconnus", None, {
            "jour": jour.isoformat(), "identifiants": sorted(inconnus),
            "regle": REGLE + " — aucune fiche créée pour le passé "
                             "(découverte auto réservée au direct, §0quater)"},
            entite="suivi")

    poses_b = _poser_segments_b(db, jour, items, mapping, tolerance)

    nb_arch_jour = db.scalar(select(func.count(HistoriqueJournalier.id)).where(
        HistoriqueJournalier.date_jour == jour)) or 0
    nb_suivis = db.scalar(select(func.count(SuiviJournalier.id)).where(
        SuiviJournalier.date_jour == jour)) or 0
    arch_incomplete = bool(nb_suivis) and nb_arch_jour < nb_suivis

    if actions == 0 and poses_b == 0 and not arch_incomplete:
        _audit(db, ACT_CONFORME, None, {
            "jour": jour.isoformat(),
            "trajets_portails": nb_trajets_portails,
            "regle": REGLE + " — journée conforme, aucune écriture"},
            entite="suivi")
        db.commit()
        return {"conforme": True, "jour": jour.isoformat()}

    # ---------------- application (journée À ÉCART → réparation) ------------
    rapport = {"conforme": False, "jour": jour.isoformat(), "gueries": 0,
               "masquees": 0, "inserees": 0, "segments_b": poses_b,
               "archive_incomplete_reecrite": arch_incomplete}
    borne = datetime.combine(jour, datetime.min.time()) + timedelta(hours=23)
    for suivi, vehicule, heals, masks, inserts in plans:
        if not (heals or masks or inserts):
            continue
        trajets = list(db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(Trajet.heure_debut)).all())
        for t, it in heals:
            avant = _snap(t)
            if it.get("fin") is not None:
                t.heure_fin = it["fin"]
            if it.get("distance_km") is not None:
                t.distance_km = it["distance_km"]
            t.statut_source = StatutSourceTrajet.VALIDE
            t.statut_validation = StatutValidationTrajet.VALIDE
            t.source_plateforme = it.get("source")
            _badge_eco(db, t, suivi, vehicule, it, USERNAME)
            _audit(db, "trajet.repare_v130_corrige", t.id, {
                "plaque": vehicule.plaque, "jour": jour.isoformat(),
                "avant": avant, "apres": _snap(t), "regle": REGLE})
            rapport["gueries"] += 1
        for t, it, motif in masks:
            avant = _snap(t)
            t.statut_validation = StatutValidationTrajet.REJETE
            _audit(db, "trajet.repare_v130_masque", t.id, {
                "plaque": vehicule.plaque, "jour": jour.isoformat(),
                "motif": motif,
                "correspondant_portail": (iso(it["debut"]) if it else None),
                "ligne_conservee_en_base": avant, "regle": REGLE})
            rapport["masquees"] += 1
        for it in inserts:
            numero = max((t.numero for t in trajets), default=0) + 1
            nouveau = Trajet(suivi_id=suivi.id, numero=numero,
                             heure_debut=it["debut"], heure_fin=it.get("fin"),
                             statut_source=StatutSourceTrajet.VALIDE,
                             source_plateforme=it.get("source"),
                             distance_km=it.get("distance_km"),
                             statut_validation=StatutValidationTrajet.VALIDE,
                             suite_minuit=bool(it.get("suite_minuit")))
            db.add(nouveau)
            db.flush()
            trajets.append(nouveau)
            _badge_eco(db, nouveau, suivi, vehicule, it, USERNAME)
            _audit(db, "trajet.repare_v130_insere", nouveau.id, {
                "plaque": vehicule.plaque, "jour": jour.isoformat(),
                "debut": iso(it["debut"]), "fin": iso(it.get("fin")),
                "distance_km": it.get("distance_km"),
                "source": it.get("source"), "regle": REGLE})
            rapport["inserees"] += 1
        trajets = sorted(trajets, key=lambda t: t.heure_debut)
        _renumeroter(trajets)
        _recalculer_pauses(trajets)
        db.flush()
        _propager(db, suivi, vehicule, borne, jour_actif=False)
        _synchroniser_archive(db, suivi)
    db.commit()

    cons = consolider_jour(db, jour)          # officiel 23:59:59 (v3 AM-3)
    archives = _reecrire_archives_jour(db, jour, plaques)
    db.flush()
    _audit(db, ACT_REPARATION, None, {
        "jour": jour.isoformat(), "gueries": rapport["gueries"],
        "masquees": rapport["masquees"], "inserees": rapport["inserees"],
        "segments_b": poses_b, "trajets_consolides": cons,
        "archives_avant": archives["avant"], "archives_apres": archives["apres"],
        "regle": REGLE}, entite="suivi")
    a = Alerte(date_heure=now_local(), type=TypeAlerte.REPARATION_DONNEES,
               gravite=GraviteAlerte.INFORMATION,
               message=(f"Journée du {jour:%d/%m/%Y} réparée depuis les "
                        f"portails : {rapport['gueries']} ligne(s) corrigée(s), "
                        f"{rapport['masquees']} ligne(s) fautive(s) retirée(s) "
                        f"de l'affichage (conservées au journal), "
                        f"{rapport['inserees']} trajet(s) retrouvé(s). "
                        f"Détail avant/après au journal d'audit."),
               statut=StatutAlerte.NOUVELLE, lien_module="/historique")
    db.add(a)
    db.commit()
    log.warning("Réparation v1.30 : journée du %s RÉPARÉE — %s",
                jour.isoformat(), rapport)
    return rapport


# -------------------------------------------------------------------- global
def executer_reparation_v130() -> dict:
    """§0decies D1/D3 — contrôle complet 19/08/2026 → veille, une seule fois ;
    idempotent par journée ; reprise au boot suivant tant qu'un portail manque."""
    db = SessionLocal()
    rapport = {"verifiees": [], "reparees": [], "sautees": [], "conformes": 0}
    try:
        if db.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.action == MARQUEUR_GLOBAL)):
            return {"statut": "deja_faite"}
        aujour = jour_attribution(now_local())
        veille = aujour - timedelta(days=1)
        if veille < BORNE_DEBUT:
            return {"statut": "rien_a_verifier"}
        jours = set(db.scalars(select(SuiviJournalier.date_jour).distinct()).all())
        jours |= set(db.scalars(
            select(HistoriqueJournalier.date_jour).distinct()).all())
        cibles = sorted(j for j in jours if BORNE_DEBUT <= j <= veille)
        for jour in cibles:
            if _marque_existe(db, ACT_REPARATION, jour) \
                    or _marque_existe(db, ACT_CONFORME, jour):
                continue
            try:
                items, echecs = _relecture_portails(jour)
            except Exception:
                items, echecs = [], ["ERREUR_INTERNE"]
            if echecs:
                log.warning("Réparation v1.30 : %s sautée (portails absents : "
                            "%s) — reprise au prochain démarrage",
                            jour.isoformat(), ", ".join(echecs))
                rapport["sautees"].append(jour.isoformat())
                continue
            res = verifier_et_reparer_jour(db, jour, items)
            if res.get("conforme"):
                rapport["conformes"] += 1
            else:
                rapport["reparees"].append(jour.isoformat())
            rapport["verifiees"].append(jour.isoformat())
        if rapport["sautees"]:
            # D3 : la réparation « s'arrête quand tout est à jour » — pas de
            # marqueur global tant qu'une journée manque de source fiable (R5)
            log.warning("Réparation v1.30 PARTIELLE : %d sautée(s) — reprise "
                        "au prochain démarrage", len(rapport["sautees"]))
            return rapport
        _audit(db, MARQUEUR_GLOBAL, None, {
            "fenetre": [BORNE_DEBUT.isoformat(), veille.isoformat()],
            "verifiees": rapport["verifiees"], "reparees": rapport["reparees"],
            "conformes": rapport["conformes"], "regle": REGLE}, entite="suivi")
        nb_r = len(rapport["reparees"])
        msg = ("Réparation automatique des journées du 19/08 au "
               f"{veille:%d/%m/%Y} terminée : {len(rapport['verifiees'])} "
               f"journée(s) vérifiée(s), {nb_r} réparée(s)"
               + (f" ({', '.join(j[8:10] + '/' + j[5:7] for j in rapport['reparees'])})"
                  if nb_r else "") + ". Les grilles de l'Historique sont à jour.")
        db.add(Alerte(date_heure=now_local(), type=TypeAlerte.REPARATION_DONNEES,
                      gravite=GraviteAlerte.INFORMATION, message=msg,
                      statut=StatutAlerte.NOUVELLE, lien_module="/historique"))
        db.commit()
        log.warning("Réparation v1.30 terminée : %s", rapport)
    except Exception:
        db.rollback()
        log.exception("Réparation v1.30 : échec (reprise au prochain démarrage)")
    finally:
        db.close()
    return rapport


# ============================================================================
# §0duodecies F3 (arbitrage LSS du 25/08/2026, mandant) — RÉPARATION UNIQUE
# de l'anomalie « TCJ > TTJ » du 25/08/2026 : des lignes NON rejetées se
# RECOUVRAIENT (double comptage prouvé : 47 min chez 0926TBV). La garde F1
# (§0duodecies) protège désormais les compteurs partout et toujours ; F3 fait
# l'HYGIÈNE de l'existant : la ligne VALIDÉE (officielle portail) prime, à
# statut égal celle au début le plus tôt ; la perdante est marquée REJETÉE —
# JAMAIS supprimée (§3.2/R2, extension AM-2) — avec audit des deux horaires ;
# compteurs recalculés aussitôt.
# Extension de mise en œuvre DÉCLARÉE (CONFORMITÉ §A.9) : si la perdante
# rejetée était la ligne « en cours » et que le gardien était fermé, l'état
# « en cours » est TRANSFÉRÉ au gardien (fin effacée) — esprit « jamais un
# camion roulant sans ligne » (§0quater R2) ; sinon le rattrapage F2
# réactiverait la perdante rejetée à l'infini (ping-pong).
# ============================================================================
MARQUEUR_V132 = "duplication_v132.terminee"
JOUR_V132 = date(2026, 8, 25)


def resoudre_chevauchements_jour(db, jour: date, seuil_s: float = 60.0,
                                 maintenant: datetime | None = None) -> dict:
    """Départage les lignes NON rejetées qui se recouvrent dans chaque suivi
    de `jour` SANS archive (journée encore active). Réutilisable (tests, F3).

    Retourne {suivis_controles, chevauchements, rejetes, plaques}."""
    from .engine import recalculer_temps
    from .models import StatutSourceTrajet, StatutValidationTrajet
    maintenant = maintenant or now_local()
    stats = {"suivis_controles": 0, "chevauchements": 0, "rejetes": 0,
             "plaques": []}
    suivis = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour)).all()
    for suivi in suivis:
        archive = db.scalar(select(func.count(HistoriqueJournalier.id)).where(
            HistoriqueJournalier.date_jour == jour,
            HistoriqueJournalier.vehicule_id == suivi.vehicule_id)) or 0
        if archive:
            continue                       # §A.2 : jamais de réécriture d'archive
        stats["suivis_controles"] += 1
        vehicule = db.get(Vehicule, suivi.vehicule_id)
        lignes = [t for t in db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(
                Trajet.heure_debut, Trajet.heure_fin)).all()
            if t.statut_validation != StatutValidationTrajet.REJETE
            and t.heure_debut is not None]
        lignes.sort(key=lambda t: (t.heure_debut, t.heure_fin or datetime.max))
        i = 0
        modifie = False
        while i < len(lignes) - 1:
            a, b = lignes[i], lignes[i + 1]
            fin_a = a.heure_fin or maintenant
            fin_b = b.heure_fin or maintenant
            recouvre_s = ((min(fin_a, fin_b) - max(a.heure_debut, b.heure_debut))
                          .total_seconds())
            if recouvre_s <= seuil_s:
                i += 1
                continue
            stats["chevauchements"] += 1
            # --- arbitrage F3 : VALIDÉ (officiel) prime ; à statut égal, le
            # début le plus tôt (couvre le plus de réalité)
            def _score(t):
                return (1 if t.statut_source == StatutSourceTrajet.VALIDE else 0,
                        -t.heure_debut.timestamp())
            gardien, perdant = (a, b) if _score(a) >= _score(b) else (b, a)
            etait_ouvert = perdant.heure_fin is None
            _audit(db, "trajet.doublon_rejete", perdant.id, {
                "plaque": getattr(vehicule, "plaque", "?"),
                "jour": jour.isoformat(),
                "recouvrement_s": int(recouvre_s),
                "gardien": {"id": gardien.id, "debut": iso(gardien.heure_debut),
                            "fin": iso(gardien.heure_fin),
                            "statut_source": gardien.statut_source.value},
                "perdant": {"id": perdant.id, "debut": iso(perdant.heure_debut),
                            "fin": iso(perdant.heure_fin),
                            "statut_source": perdant.statut_source.value},
                "regle": "§0duodecies F3 (25/08/2026) : deux lignes non "
                         "rejetées se recouvrent — la VALIDÉE prime (à "
                         "statut égal, début le plus tôt) ; la perdante est "
                         "marquée REJETÉE, jamais supprimée (§3.2/R2)"})
            perdant.statut_validation = StatutValidationTrajet.REJETE
            stats["rejetes"] += 1
            lignes.remove(perdant)
            # Extension DÉCLARÉE : la perdante était la ligne « en cours » et
            # le gardien était fermé → l'état « en cours » passe au gardien
            # (la conduite continue réellement — §0quater R2 ; sinon F2
            # réactiverait la perdante rejetée au cycle suivant = ping-pong)
            if etait_ouvert and gardien.heure_fin is not None:
                gardien.heure_fin = None
                _audit(db, "trajet.en_cours_transfere", gardien.id, {
                    "plaque": getattr(vehicule, "plaque", "?"),
                    "jour": jour.isoformat(),
                    "debut_gardien": iso(gardien.heure_debut),
                    "regle": "§0duodecies F3 — extension déclarée : l'état "
                             "« en cours » de la ligne rejetée est transféré "
                             "au gardien (jamais un camion roulant sans "
                             "ligne, §0quater R2)"})
            if getattr(vehicule, "plaque", None):
                stats["plaques"].append(vehicule.plaque)
            modifie = True
            # paire réévaluée au même rang (une ligne peut en recouvrir 2+)
        if modifie:
            recalculer_temps(db, suivi, maintenant)
    return stats


def executer_reparation_v132() -> dict:
    """F3 — lancement automatique, UNE seule fois (marqueur d'audit global),
    idempotent, sans exception vers le démarrage : erreur tracée, reprise au
    boot suivant. Aucune relance des portails (résolution locale uniquement,
    contrairement à v1.30 : l'audit des lignes suffit ici)."""
    rapport = {"deja_fait": False, "jour": JOUR_V132.isoformat()}
    db = SessionLocal()
    try:
        deja = db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == MARQUEUR_V132)) or 0
        if deja:
            rapport["deja_fait"] = True
            return rapport
        stats = resoudre_chevauchements_jour(db, JOUR_V132)
        rapport.update(stats)
        _audit(db, MARQUEUR_V132, None, {
            "jour": JOUR_V132.isoformat(),
            "suivis_controles": stats["suivis_controles"],
            "chevauchements": stats["chevauchements"],
            "rejetes": stats["rejetes"], "plaques": sorted(stats["plaques"]),
            "regle": "§0duodecies F3 (25/08/2026) : réparation unique des "
                     "lignes recouvrantes du 25/08 (TCJ > TTJ 0926TBV) — "
                     "rejet sans suppression, compteurs recalculés"},
               entite="suivi")
        if stats["rejetes"]:
            msg = ("Réparation automatique des compteurs du "
                   f"{JOUR_V132:%d/%m/%Y} : {stats['rejetes']} ligne(s) en "
                   f"double marquée(s) rejetée(s) "
                   f"({', '.join(sorted(stats['plaques']))}) — TCC/TCJ/TTJ "
                   "recalculés, aucune donnée supprimée. Le détail est dans "
                   "le journal d'audit.")
            db.add(Alerte(date_heure=now_local(),
                          type=TypeAlerte.REPARATION_DONNEES,
                          gravite=GraviteAlerte.INFORMATION, message=msg,
                          statut=StatutAlerte.NOUVELLE,
                          lien_module="/suivi"))
        db.commit()
        log.warning("Réparation v1.32 (F3) terminée : %s", rapport)
    except Exception:
        db.rollback()
        log.exception("§0duodecies F3 : réparation v1.32 en échec — "
                      "reprise au prochain démarrage")
    finally:
        db.close()
    return rapport


# ============================================================================
# §0sexies decies J1-J4 (arbitrages LSS du 27/08/2026) — Réparation v1.38 :
# déduplication du référentiel chauffeurs abîmé par le bug lower() SQLite
# (noms accentués jamais reconnus → fiches créées en rafale, ex. MICHAËL
# Justin ×N, alertes « Nouveau chauffeur détecté » en série).
#
# J2 — gardien = fiche la plus complète (véhicule affecté > téléphone >
# matricule non-AUTO > plus de références liées), à égalité la plus ancienne ;
# rebanchage des références vers le gardien (véhicules, suivi, badges de
# trajets, missions, alertes, infractions) ; archives NON retouchées (§A.2).
# J3 — EXCEPTION au principe « jamais de suppression » (choix express de
# l'exploitant, gravé au §0sexies decies) : les fiches doublons sont
# SUPPRIMÉES après consignation INTÉGRALE dans AuditLog (snapshot JSON
# complet + gardien), à passage unique, groupes homonymes normalisés seuls.
# J4 — les alertes NOUVEAU_CONDUCTEUR de la rafale passent TRAITÉE en masse
# (motif audité) ; la cause étant corrigée (J1), elles ne se reproduiront pas.
# ============================================================================
MARQUEUR_V138 = "reparation.conducteurs_v138"


def _canon_nom(c: Conducteur) -> str:
    """Clé de groupe = forme canonique RECALCULÉE (jamais la colonne stockée
    — elle peut être vide ou dérivée d'une ancienne règle, ex. double espace
    interne conservé : dérive constatée au fumier v1.38)."""
    return normaliser_libelle(c.nom_prenom)[:170]


def _nb_refs_gardien(db, cid: str) -> int:
    n = db.scalar(select(func.count(Vehicule.id)).where(
        Vehicule.conducteur_actuel_id == cid)) or 0
    n += db.scalar(select(func.count(SuiviJournalier.id)).where(
        SuiviJournalier.conducteur_id == cid)) or 0
    n += db.scalar(select(func.count(Trajet.id)).where(
        Trajet.conducteur_badge_id == cid)) or 0
    n += db.scalar(select(func.count(Mission.id)).where(
        Mission.conducteur_id == cid)) or 0
    n += db.scalar(select(func.count(Alerte.id)).where(
        Alerte.conducteur_id == cid)) or 0
    n += db.scalar(select(func.count(Infraction.id)).where(
        Infraction.conducteur_id == cid)) or 0
    return n


def _choisir_gardien(db, fiches: list[Conducteur]) -> Conducteur:
    """J2 — la plus complète, à égalité la plus ancienne (dictionnaire de
    l'exploitant gravé au §0sexies decies)."""
    def score(c: Conducteur) -> tuple:
        s = 0
        s += 1000 * (db.scalar(select(func.count(Vehicule.id)).where(
            Vehicule.conducteur_actuel_id == c.id)) or 0)
        s += 100 if c.telephone else 0
        s += 100 if (c.matricule and not c.matricule.startswith("AUTO-")) else 0
        s += _nb_refs_gardien(db, c.id)
        return (-s, c.date_creation, c.id)
    return sorted(fiches, key=score)[0]


def _rebrancher_vers_gardien(db, perdant_id: str, gardien_id: str) -> dict:
    """Repointage des références VIVES (jamais les archives — §A.2)."""
    n = {}
    n["vehicules"] = db.execute(
        Vehicule.__table__.update()
        .where(Vehicule.conducteur_actuel_id == perdant_id)
        .values(conducteur_actuel_id=gardien_id)).rowcount
    n["suivis"] = db.execute(
        SuiviJournalier.__table__.update()
        .where(SuiviJournalier.conducteur_id == perdant_id)
        .values(conducteur_id=gardien_id)).rowcount
    n["badges_trajets"] = db.execute(
        Trajet.__table__.update()
        .where(Trajet.conducteur_badge_id == perdant_id)
        .values(conducteur_badge_id=gardien_id)).rowcount
    n["missions"] = db.execute(
        Mission.__table__.update()
        .where(Mission.conducteur_id == perdant_id)
        .values(conducteur_id=gardien_id)).rowcount
    n["alertes"] = db.execute(
        Alerte.__table__.update()
        .where(Alerte.conducteur_id == perdant_id)
        .values(conducteur_id=gardien_id)).rowcount
    n["infractions"] = db.execute(
        Infraction.__table__.update()
        .where(Infraction.conducteur_id == perdant_id)
        .values(conducteur_id=gardien_id)).rowcount
    return {k: v for k, v in n.items() if v}


def _assurer_index_unique(db) -> bool:
    """Pose l'index UNIQUE dès que plus aucun doublon normalisé n'existe
    (jamais bloquant : reporté au prochain démarrage sinon)."""
    from sqlalchemy import text
    doublons = db.execute(text(
        "SELECT count(*) FROM (SELECT nom_normalise FROM conducteurs "
        "WHERE nom_normalise IS NOT NULL GROUP BY nom_normalise "
        "HAVING count(*) > 1)")).scalar() or 0
    if doublons:
        log.warning("§0sexies decies J1 : index UNIQUE reporté — %d groupe(s) "
                    "homonyme(s) restant(s)", doublons)
        return False
    db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ux_conducteurs_nom_normalise "
                    "ON conducteurs (nom_normalise)"))
    db.commit()
    return True


def reparer_conducteurs_v138(db=None) -> dict:
    """J1→J4 — la passe de FUSION est rejouée à CHAQUE démarrage tant qu'un
    groupe homonyme canonique survit (idempotente : 0 groupe → 0 effet) ;
    le marqueur ne court-circuite que la clôture J4 des alertes fantômes et
    l'alerte récapitulative. Ainsi une clé dérivée d'une ancienne règle
    (double espace interne conservé, constaté au fumier) est rattrapée même
    après un premier passage imparfait ; la purge historique elle-même reste
    à passage unique (marqueur)."""
    stats = {"deja_fait": False, "backfill": 0, "groupes": 0,
             "gardiens": 0, "supprimees": 0, "rebranchees": 0,
             "alertes_closes": 0, "index_unique": False}
    propre = db is None
    db = db or SessionLocal()
    try:
        # J1 — forme canonique : remplie là où elle manque (idempotent) ;
        # les clés DÉRIVÉES (ancienne règle, ex. double espace) sont
        # recalculées APRÈS la fusion (passe « rekey » ci-dessous).
        for c in db.scalars(select(Conducteur).where(
                Conducteur.nom_normalise.is_(None))):
            c.nom_normalise = _canon_nom(c)
            stats["backfill"] += 1
        db.commit()

        deja = db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == MARQUEUR_V138)) or 0
        # J2/J3 — groupes homonymes canoniques (passee rejouée : poches
        # résiduelles d'une clé dérivée ; sans aucun groupe, coût nul)
        if True:
            # J2/J3 — groupes homonymes
            par_nom: dict[str, list[Conducteur]] = {}
            for c in db.scalars(select(Conducteur)).all():
                par_nom.setdefault(_canon_nom(c), []).append(c)
            for cle, fiches in par_nom.items():
                if not cle or len(fiches) < 2:
                    continue
                stats["groupes"] += 1
                gardien = _choisir_gardien(db, fiches)
                stats["gardiens"] += 1
                for f in fiches:
                    if f.id == gardien.id:
                        continue
                    liens = _rebrancher_vers_gardien(db, f.id, gardien.id)
                    stats["rebranchees"] += sum(liens.values())
                    # J3 — consignation INTÉGRALE avant suppression (jamais
                    # de perte d'information, exception gravée au §0sexies decies)
                    db.add(AuditLog(
                        username="reparation-v1.38",
                        action="conducteur.doublon_supprime",
                        entite="conducteur", entite_id=f.id,
                        details={
                            "fiche_supprimee": {
                                "id": f.id, "nom_prenom": f.nom_prenom,
                                "prenom_usuel": f.prenom_usuel,
                                "matricule": f.matricule,
                                "telephone": f.telephone,
                                "statut": f.statut.value if f.statut else None,
                                "date_creation": iso(f.date_creation)},
                            "gardien": {"id": gardien.id,
                                        "nom_prenom": gardien.nom_prenom,
                                        "matricule": gardien.matricule},
                            "references_rebranchees": liens,
                            "regle": "§0sexies decies J3 (27/08/2026) : "
                                     "exception à « jamais de suppression » — "
                                     "doublon du bug lower() SQLite, choix "
                                     "express de l'exploitant, à passage "
                                     "unique"}))
                    db.delete(f)
                    stats["supprimees"] += 1
            if stats["supprimees"]:
                log.warning("§0sexies decies J2/J3 : %s fiche(s) doublon(s) "
                            "fusionnée(s) à cette passe (rejouée ou "
                            "initiale)", stats["supprimees"])
            db.commit()
            if not deja:
                # J4 — alertes fantômes de la rafale → TRAITÉE (motif
                # audité) ; marqueur de purge initiale ; récapitulatif.
                fantomes = list(db.scalars(select(Alerte).where(
                    Alerte.type == TypeAlerte.NOUVEAU_CONDUCTEUR,
                    Alerte.statut != StatutAlerte.TRAITEE)).all())
                for a in fantomes:
                    a.statut = StatutAlerte.TRAITEE
                stats["alertes_closes"] = len(fantomes)
                db.add(AuditLog(
                    username="reparation-v1.38", action=MARQUEUR_V138,
                    entite="conducteur", entite_id=None,
                    details=dict(stats, regle=(
                        "§0sexies decies J2/J3/J4 (27/08/2026) : "
                        "fusion des doublons, alertes fantômes "
                        "clôturées, index UNIQUE — à passage unique"))))
                db.commit()
                if stats["supprimees"] or stats["alertes_closes"]:
                    db.add(Alerte(
                        date_heure=now_local(),
                        type=TypeAlerte.REPARATION_DONNEES,
                        gravite=GraviteAlerte.INFORMATION,
                        message=(f"Réparation automatique des fiches "
                                 f"chauffeurs (bug d'accents corrigé) : "
                                 f"{stats['supprimees']} fiche(s) en doublon "
                                 f"fusionnée(s), {stats['rebranchees']} "
                                 f"référence(s) rattachée(s) au bon "
                                 f"chauffeur, {stats['alertes_closes']} "
                                 f"alerte(s) fantôme(s) « Nouveau chauffeur "
                                 f"» clôturée(s). Le détail complet est dans "
                                 f"le journal d'audit."),
                        statut=StatutAlerte.NOUVELLE,
                        lien_module="/referentiels"))
                    db.commit()
        stats["deja_fait"] = bool(deja)
        # Passe « rekey » (toujours, idempotent) : aligne la colonne stockée
        # sur la forme canonique actuelle — ex. clés « double espace » des
        # anciennes fiches. Post-fusion, deux clés ne peuvent plus entrer en
        # collision ; si l'index UNIQUE existe déjà, un doublon récalcitrant
        # est signalé sans jamais bloquer le démarrage.
        for c in db.scalars(select(Conducteur)).all():
            canon = _canon_nom(c)
            if c.nom_normalise != canon:
                c.nom_normalise = canon
                stats["rekey"] = stats.get("rekey", 0) + 1
            tset = calculer_tokens_set(c.nom_prenom)[:170]
            if c.tokens_set != tset:
                c.tokens_set = tset
        db.commit()
        stats["index_unique"] = _assurer_index_unique(db)
        db.commit()
        log.warning("Réparation v1.38 (conducteurs, §0sexies decies) : %s",
                    stats)
    except Exception:
        db.rollback()
        log.exception("§0sexies decies : réparation v1.38 en échec — "
                      "reprise au prochain démarrage")
        stats["erreur"] = True
    finally:
        if propre:
            db.close()
    return stats


def _dt_iso_rep(texte) -> datetime | None:
    if not texte:
        return None
    try:
        return datetime.fromisoformat(str(texte))
    except (ValueError, TypeError):
        return None


def reparer_historique_conducteurs_passes(db=None) -> dict:
    """Correction des attributions de conducteurs sur les données passées et archives :
    1. Réalignement sur le chauffeur majoritaire / titulaire du véhicule.
    2. Propagation et enrichissement des badges de trajets dans les snapshots JSON d'archives.
    3. Élimination des attributions erronées dues aux badges de relais momentanés.
    """
    propre = db is None
    db = db or SessionLocal()
    stats = {"suivis_corriges": 0, "archives_corrigees": 0, "trajets_badges_enrichis": 0}
    try:
        # 1. Parcourir tous les SuiviJournalier existants
        suivis = db.scalars(select(SuiviJournalier).options(
            selectinload(SuiviJournalier.trajets),
            joinedload(SuiviJournalier.vehicule)
        )).all()

        for s in suivis:
            if getattr(s, "conducteur_origine", None) == "MANUEL":
                continue
            veh = s.vehicule
            titulaire_id = veh.conducteur_actuel_id if veh else None

            # Calcul des durées par conducteur sur les trajets réels
            duree_par_cond: dict[str, int] = {}
            for t in (s.trajets or []):
                if t.statut_validation == StatutValidationTrajet.REJETE:
                    continue
                cid = t.conducteur_badge_id or titulaire_id
                if cid:
                    duree = int((t.heure_fin - t.heure_debut).total_seconds()) if (t.heure_fin and t.heure_debut and t.heure_fin >= t.heure_debut) else 0
                    duree_par_cond[cid] = duree_par_cond.get(cid, 0) + duree

            if duree_par_cond:
                majoritaire_id = max(duree_par_cond.items(), key=lambda x: x[1])[0]
                if s.conducteur_id != majoritaire_id:
                    log.info("Réparation Suivi %s (%s) : conducteur %s -> majoritaire %s",
                             s.id, veh.plaque if veh else "?", s.conducteur_id, majoritaire_id)
                    s.conducteur_id = majoritaire_id
                    s.conducteur_origine = "BADGE"
                    stats["suivis_corriges"] += 1
            elif titulaire_id and s.conducteur_id != titulaire_id and not s.conducteur_id:
                s.conducteur_id = titulaire_id
                stats["suivis_corriges"] += 1

        db.commit()

        # 2. Parcourir tous les HistoriqueJournalier existants
        hists = db.scalars(select(HistoriqueJournalier).options(
            joinedload(HistoriqueJournalier.vehicule)
        )).all()

        for h in hists:
            veh = h.vehicule
            titulaire_id = veh.conducteur_actuel_id if veh else None
            d = dict(h.donnees or {})
            trajets_snap = list(d.get("trajets") or [])
            modifie = False

            # Enrichir les snapshots de trajets depuis la table `trajets` de la base
            db_trajets = db.scalars(select(Trajet).join(
                SuiviJournalier, Trajet.suivi_id == SuiviJournalier.id
            ).where(
                SuiviJournalier.vehicule_id == h.vehicule_id,
                SuiviJournalier.date_jour == h.date_jour
            )).all()

            if db_trajets and trajets_snap:
                map_db_t = {t_db.id: t_db for t_db in db_trajets if t_db.id}
                for t in trajets_snap:
                    if not isinstance(t, dict):
                        continue
                    tid = t.get("id")
                    if tid and tid in map_db_t:
                        db_t = map_db_t[tid]
                        if db_t.conducteur_badge and not t.get("conducteur_badge"):
                            t["conducteur_badge"] = db_t.conducteur_badge
                            t["conducteur_badge_id"] = db_t.conducteur_badge_id
                            modifie = True
                            stats["trajets_badges_enrichis"] += 1

            duree_par_cond_hist: dict[str, int] = {}
            for t in trajets_snap:
                if not isinstance(t, dict):
                    continue
                if t.get("statut_validation") == "REJETE":
                    continue
                cid = t.get("conducteur_badge_id") or titulaire_id
                if cid:
                    deb = _dt_iso_rep(t.get("heure_debut"))
                    fin = _dt_iso_rep(t.get("heure_fin"))
                    duree = int((fin - deb).total_seconds()) if (deb and fin and fin >= deb) else 0
                    duree_par_cond_hist[cid] = duree_par_cond_hist.get(cid, 0) + duree

            if duree_par_cond_hist:
                majoritaire_id = max(duree_par_cond_hist.items(), key=lambda x: x[1])[0]
                if h.conducteur_id != majoritaire_id:
                    log.info("Réparation Historique %s (%s - %s) : conducteur %s -> majoritaire %s",
                             h.id, h.date_jour, veh.plaque if veh else "?", h.conducteur_id, majoritaire_id)
                    h.conducteur_id = majoritaire_id
                    maj_cond = db.get(Conducteur, majoritaire_id)
                    if maj_cond:
                        d["conducteur_id"] = maj_cond.id
                        d["chauffeur"] = maj_cond.nom_prenom
                        d["conducteur"] = s_conducteur(maj_cond, court=True)
                    modifie = True
                    stats["archives_corrigees"] += 1
            elif titulaire_id and not h.conducteur_id:
                h.conducteur_id = titulaire_id
                maj_cond = db.get(Conducteur, titulaire_id)
                if maj_cond:
                    d["conducteur_id"] = maj_cond.id
                    d["chauffeur"] = maj_cond.nom_prenom
                    d["conducteur"] = s_conducteur(maj_cond, court=True)
                modifie = True
                stats["archives_corrigees"] += 1

            if modifie:
                d["trajets"] = trajets_snap
                h.donnees = d

        db.commit()
        log.info("Réparation historique conducteurs terminée : %s", stats)
    except Exception:
        db.rollback()
        log.exception("Erreur lors de la réparation de l'historique des conducteurs")
    finally:
        if propre:
            db.close()
    return stats
