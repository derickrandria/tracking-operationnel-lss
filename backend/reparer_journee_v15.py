"""Réparation ponctuelle Addendum v1.5 — journée construite AVANT la v1.8.

Les versions ≤ 1.7 (sans filtre ni fusion) ont pu laisser dans la journée :
  • des micro-trajets < 0,3 km visibles dans la grille (règle absolue §2 violée) ;
  • des ruptures de trajet sur des pauses < 20 min (« pause 28 s », « 2 min »…).

Ce script, à lancer UNE FOIS serveur arrêté, remet la journée au propre avec
les règles v1.5 : fusion des pauses < 20 min → rejet des trajets < 0,3 km →
renumérotation → recalcul TCC/TCJ/TTJ. Idempotent : on peut le relancer sans
danger ; la prochaine synchronisation Niveau 2 (15 min) appliquera ensuite les
horaires et distances officiels du portail.

    cd E:\\projets\\LSS-Tracking-Plateforme\\backend
    python reparer_journee_v15.py                # journée d'aujourd'hui
    python reparer_journee_v15.py 2026-08-01     # journée précise
"""
import os
import sys

os.environ.setdefault("SIM_ENABLE", "0")
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select

from app.config import now_local
from app.database import SessionLocal
from app import engine as eng
from app.main import migrer_schema
from app.models import (AuditLog, StatutValidationTrajet, SuiviJournalier,
                        Trajet)
from app.serializers import iso


def auditer(db, action: str, details: dict):
    db.add(AuditLog(username="reparation-v15", action=action,
                    entite="trajet", entite_id=None, details=details))


def reparer(db, jour: date) -> dict:
    seuils = eng.get_seuils(db)
    pause_min = float(seuils["DUREE_MIN_PAUSE_VALIDE"])      # 20 min (v1.5)
    seuil_km = float(seuils["SEUIL_DISTANCE_MIN_TRAJET_KM"])  # 0,3 km
    maintenant = now_local()
    stats = {"suivis": 0, "fusions": 0, "rejets": 0, "suivis_recalcules": 0}

    suivis = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour)).all()
    for suivi in suivis:
        ts = list(db.scalars(select(Trajet).where(
            Trajet.suivi_id == suivi.id).order_by(Trajet.heure_debut)).all())
        if not ts:
            continue
        stats["suivis"] += 1

        # ---- PASSE 1 · fusion des ruptures sur pause < 20 min (§5) --------
        i = 0
        while i < len(ts) - 1:
            a, b = ts[i], ts[i + 1]
            if (a.heure_fin is not None and b.heure_debut is not None
                    and 0 <= (b.heure_debut - a.heure_fin).total_seconds() < pause_min):
                a.heure_fin = b.heure_fin or a.heure_fin
                a.pause_apres_s = b.pause_apres_s
                if a.distance_km is not None or b.distance_km is not None:
                    a.distance_km = round((a.distance_km or 0) + (b.distance_km or 0), 3)
                # on conserve le « meilleur » statut source des deux
                if str(getattr(b.statut_source, "value", b.statut_source)) == "VALIDÉ":
                    a.statut_source = b.statut_source
                    a.source_plateforme = b.source_plateforme
                db.delete(b)
                del ts[i + 1]
                stats["fusions"] += 1
            else:
                i += 1
        db.flush()

        # ---- PASSE 2 · rejet des trajets < 0,3 km (§1.1, règle absolue §2) -
        for t in ts:
            if (t.distance_km is not None and t.distance_km < seuil_km
                    and t.statut_validation != StatutValidationTrajet.REJETE):
                t.statut_validation = StatutValidationTrajet.REJETE
                stats["rejets"] += 1
            elif t.statut_validation != StatutValidationTrajet.REJETE:
                # les trajets sains finalisés (avec fin) deviennent VALIDE ;
                # ceux encore EN COURS restent EN_ATTENTE
                t.statut_validation = (StatutValidationTrajet.VALIDE
                                       if t.heure_fin is not None
                                       else StatutValidationTrajet.EN_ATTENTE)

        # ---- PASSE 3 · renumérotation + pauses brutes + recalcul §7.2 -----
        for n, t in enumerate(sorted(ts, key=lambda t: t.heure_debut), 1):
            t.numero = n
        for a, b in zip(ts, ts[1:]):
            if a.heure_fin and b.heure_debut:
                a.pause_apres_s = max(0, int((b.heure_debut - a.heure_fin).total_seconds()))
        eng.recalculer_temps(db, suivi, maintenant)
        stats["suivis_recalcules"] += 1

    auditer(db, "reparation.v15", {"jour": jour.isoformat(), **stats})
    db.commit()
    return stats


if __name__ == "__main__":
    migrer_schema()
    jour = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else now_local().date()
    db = SessionLocal()
    try:
        print(f"Réparation de la journée {jour} (règles v1.5 : pause ≥ 20 min, "
              f"distance ≥ 0,3 km)…")
        stats = reparer(db, jour)
        suivis = db.execute(select(
            SuiviJournalier.id).where(SuiviJournalier.date_jour == jour)).all()
        tot_v = db.scalar(select(__import__('sqlalchemy').func.count(Trajet.id))
                          .join(SuiviJournalier).where(
                              SuiviJournalier.date_jour == jour,
                              Trajet.statut_validation == StatutValidationTrajet.VALIDE)) or 0
        tot_r = db.scalar(select(__import__('sqlalchemy').func.count(Trajet.id))
                          .join(SuiviJournalier).where(
                              SuiviJournalier.date_jour == jour,
                              Trajet.statut_validation == StatutValidationTrajet.REJETE)) or 0
        print(f"  suivi(s) traités      : {stats['suivis']}")
        print(f"  fusions (pause < 20 min) : {stats['fusions']}")
        print(f"  rejets (trajet < 0,3 km) : {stats['rejets']}")
        print(f"  → à l'écran maintenant : {tot_v} trajet(s) affiché(s), "
              f"{tot_r} manœuvre(s) masquée(s)")
        print("Terminé — redémarrez le serveur ; la synchronisation Niveau 2 "
              "réajustera horaires et distances officiels au prochain cycle.")
    finally:
        db.close()
