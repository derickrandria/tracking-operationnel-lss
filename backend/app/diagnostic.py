"""§0duodecies F4 (arbitrage LSS du 25/08/2026, mandant) — RAPPORT DE
DIAGNOSTIC exportable.

Un fichier texte horodaté, lisible sans connaissance technique, qui rassemble
pour UN véhicule et UNE journée :
  1. toutes les lignes de trajet (y compris les rejetées, masquées à l'écran) ;
  2. les compteurs tels qu'enregistrés et tels que recalculés à l'instant ;
  3. les événements/positions GPS de la journée (heure, vitesse, lieu) ;
  4. les actions automatiques de la journée pour ce véhicule (journal d'audit :
     rejets, rattrapages, réconciliations, chevauchements détectés) ;
  5. les seuils de calcul utiles à la relecture.

Lecture seule : AUCUNE écriture en base (§10/R2 — on n'invente et on ne
modifie rien pour diagnostiquer). À joindre à toute question d'exploitation.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from .config import now_local
from .models import (AuditLog, EvenementGPS, SuiviJournalier, Trajet, Vehicule,
                     StatutValidationTrajet)

log = logging.getLogger("lss.diagnostic")

MAX_EVENEMENTS = 5000        # garde-fou lisibilité (jamais atteint en pratique)


def _hm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M:%S") if dt else "—"


def _fr(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def construire_rapport_diagnostic(db, plaque: str, jour: date,
                                  maintenant: datetime | None = None) -> str:
    """Texte complet du rapport (§0duodecies F4). Fonction pure côté base :
    lecture seule, aucun commit. `plaque` acceptée avec ou sans espaces."""
    maintenant = maintenant or now_local()
    plaque = (plaque or "").strip()
    lignes_txt: list[str] = []
    w = lignes_txt.append

    vehicule = db.scalar(select(Vehicule).where(
        Vehicule.plaque == plaque)) or db.scalar(select(Vehicule).where(
            Vehicule.plaque.ilike(plaque.replace(" ", "%"))))
    w("=" * 72)
    w("RAPPORT DE DIAGNOSTIC — Plateforme de Tracking Opérationnel LSS")
    w(f"Généré le {maintenant:%d/%m/%Y à %H:%M:%S} (heure locale)")
    w("=" * 72)
    if vehicule is None:
        w(f"\nVéhicule « {plaque} » : INCONNU dans la base.")
        w("Vérifier l'orthographe de la plaque (ex. 0926TBV).")
        return "\n".join(lignes_txt)

    w(f"Véhicule  : {vehicule.plaque} — {vehicule.description or ''} "
      f"(plateforme {vehicule.plateforme_gps or '?'})")
    w(f"Journée   : {_fr(jour)}")
    w("")

    # ---------------------------------------------------------------- suivi
    suivi = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour,
        SuiviJournalier.vehicule_id == vehicule.id))

    w("1) LIGNES DE TRAJET DE LA JOURNÉE")
    w("   (toutes, y compris les rejetées qui sont masquées à l'écran —")
    w("    une ligne rejetée n'est jamais supprimée ni comptée)")
    w("-" * 72)
    if suivi is None:
        w("   Aucune ligne de suivi pour cette journée.")
        trajets: list[Trajet] = []
    else:
        trajets = list(db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(
                Trajet.heure_debut, Trajet.heure_fin)).all())
        if not trajets:
            w("   Aucune ligne de trajet enregistrée.")
        w(f"   {'N°':>2} | {'début':<8} | {'fin':<8} | {'origine':<11} | "
          f"{'validité':<28} | distance")
        w("   " + "-" * 68)
        for t in trajets:
            rejetee = t.statut_validation == StatutValidationTrajet.REJETE
            valid = ("REJETÉE (masquée, non comptée)" if rejetee
                     else t.statut_validation.value)
            fin = _hm(t.heure_fin) if t.heure_fin else "(en cours)"
            dist = (f"{t.distance_km:.3f} km" if t.distance_km is not None
                    else "non fournie")
            w(f"   {t.numero:>2} | {_hm(t.heure_debut):<8} | {fin:<8} | "
              f"{t.statut_source.value:<11} | {valid:<28} | {dist}")
    w("")

    # ------------------------------------------------------------- compteurs
    w("2) COMPTEURS")
    w("-" * 72)

    def _hmm(s: float | int) -> str:
        s = int(s or 0)
        return f"{s // 3600}:{(s % 3600) // 60:02d}"

    if suivi is None:
        w("   (pas de compteurs — aucune journée)")
    else:
        w(f"   Enregistrés (dernier calcul) : DÉPART "
          f"{suivi.heure_depart:%H:%M}" if suivi.heure_depart else
          "   Enregistrés : DÉPART —")
        w(f"   TCC {_hmm(suivi.tcc_s)} · TCJ {_hmm(suivi.tcj_s)} · "
          f"TTJ {_hmm(suivi.ttj_s)} · pauses {_hmm(suivi.total_pause_s)}")
        # recalcul à l'instant (Lecture seule : construire_journee ne touche
        # rien ; la garde §0duodecies F1 mesure à l'union — le recalcul montre
        # la valeur PROTÉGÉE même si des lignes se recouvrent)
        from .engine import get_seuils
        from .serializers import journee_suivi
        seuils = get_seuils(db)
        jd = journee_suivi(suivi, seuils, maintenant=maintenant)
        brut = sum(lg.travail_s for lg in jd.lignes)
        recouvre = max(0, brut - jd.tcj_s)
        w(f"   Recalculés à l'instant     : TCJ {_hmm(jd.tcj_s)} · "
          f"TTJ {_hmm(jd.ttj_s)} · pauses {_hmm(jd.total_pause_s)}")
        if recouvre >= 60:
            w(f"   ⚠ Recouvrement de lignes détecté : {_hmm(recouvre)} "
              "comptées deux fois AVANT la garde anti-double-comptage —")
            w("     la garde (§0duodecies F1, 25/08/2026) a neutralisé le "
              "double comptage dans les valeurs ci-dessus.")
    w("")

    # ------------------------------------------------------------- événements
    w("3) ÉVÉNEMENTS / POSITIONS GPS DE LA JOURNÉE")
    w("-" * 72)
    debut_j = datetime.combine(jour, time.min)
    fin_j = debut_j + timedelta(days=1)
    evenements = list(db.scalars(select(EvenementGPS).where(
        EvenementGPS.vehicule_id == vehicule.id,
        EvenementGPS.horodatage >= debut_j,
        EvenementGPS.horodatage < fin_j).order_by(
            EvenementGPS.horodatage).limit(MAX_EVENEMENTS + 1)).all())
    tronque = len(evenements) > MAX_EVENEMENTS
    evenements = evenements[:MAX_EVENEMENTS]
    if not evenements:
        w("   Aucun événement GPS enregistré ce jour-là.")
    for ev in evenements:
        w(f"   {ev.horodatage:%H:%M:%S} | {ev.vitesse or 0:>5.1f} km/h | "
          f"{ev.type_evenement.value:<16} | {ev.adresse or '—'}")
    if tronque:
        w(f"   … (liste tronquée à {MAX_EVENEMENTS} événements)")
    w("")

    # ------------------------------------------------------------ journal
    w("4) ACTIONS AUTOMATIQUES DE LA JOURNÉE (journal d'audit)")
    w("-" * 72)
    audits = list(db.scalars(select(AuditLog).where(
        AuditLog.date_heure >= debut_j,
        AuditLog.date_heure < fin_j + timedelta(days=1),
    ).order_by(AuditLog.date_heure)).all())
    liees = [a for a in audits
             if vehicule.plaque in str((a.details or {}))]
    if not liees:
        w("   Aucune action automatique enregistrée pour ce véhicule ce "
          "jour-là.")
    for a in liees:
        details = "; ".join(f"{k}={v}" for k, v in (a.details or {}).items()
                            if k not in ("regle",))
        regle = (a.details or {}).get("regle")
        w(f"   {a.date_heure:%H:%M:%S} | {a.action}")
        if details:
            w(f"        {details}")
        if regle:
            w(f"        règle : {regle}")
    w("")

    # --------------------------------------------------------------- seuils
    w("5) SEUILS DE CALCUL EN VIGUEUR")
    w("-" * 72)
    try:
        from .engine import get_seuils
        seuils = get_seuils(db)
        for cle in ("DUREE_MIN_PAUSE_VALIDE", "SEUIL_PAUSE_COUPURE_TCC",
                    "SEUIL_FUSION_AFFICHAGE_S",
                    "SEUIL_DISTANCE_MIN_TRAJET_KM",
                    "SEUIL_TOLERANCE_RAPPROCHEMENT_TRAJET",
                    "SEUIL_TCC_MAX", "SEUIL_TCJ_MAX", "SEUIL_TTJ_MAX"):
            w(f"   {cle} = {seuils.get(cle, '—')}")
    except Exception:
        w("   (seuils indisponibles)")
    w("")
    w("=" * 72)
    w("FIN DU RAPPORT — à envoyer tel quel avec votre question.")
    w("=" * 72)
    texte = "\n".join(lignes_txt)
    log.info("Rapport diagnostic F4 généré — %s %s (%d lignes)",
             vehicule.plaque, jour.isoformat(), texte.count("\n"))
    return texte
