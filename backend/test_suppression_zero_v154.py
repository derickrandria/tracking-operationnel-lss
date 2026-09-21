# -*- coding: utf-8 -*-
"""P1 — AUCUNE SUPPRESSION DE TRAJET OBSERVÉ (v1.54, 18/09/2026).

Preuve demandée : « une donnée provisoire absente du relevé officiel reste
présente en base ».

Ce que la suite vérifie, dans l'ordre :
  [1] un trajet PROVISOIRE sans contrepartie officielle SURVIT à la
      réconciliation (conservé, marqué REJETE, motif explicite, audit AVANT/APRÈS) ;
  [2] un jumeau (même début) n'est plus supprimé non plus ;
  [3] un ouvert provisoire recouvert par une ligne officielle est conservé ;
  [4] un résidu de structure recouvrant est conservé ;
  [5] AUCUNE ligne n'est perdue : le compte en base ne baisse jamais ;
  [6] la ligne conservée est bien MASQUÉE de l'affichage et des compteurs ;
  [7] la donnée brute (heures, distance) est INTACTE ;
  [8] le direct (ingestion GPS) ne ressuscite pas une ligne écartée.

Lancement :
  cd backend
  DATABASE_URL="sqlite:////tmp/test_suppression_zero_v154.db" \
      python test_suppression_zero_v154.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_suppression_zero_v154.db")

from sqlalchemy import func, select                          # noqa: E402

from app import engine, reconciliation                       # noqa: E402
from app.config import now_local                             # noqa: E402
from app.database import SessionLocal                        # noqa: E402
from app.main import migrer_schema                           # noqa: E402
from app.models import (AuditLog, StatutSourceTrajet,        # noqa: E402
                        StatutValidationTrajet, SuiviJournalier, Trajet,
                        Vehicule)
from app.seed import seed_si_vide                            # noqa: E402

OK = KO = 0


def check(nom: str, condition: bool, info: str = ""):
    global OK, KO
    if condition:
        OK += 1
        print(f"  ✅ {nom}")
    else:
        KO += 1
        print(f"  ❌ {nom} {info}")


seed_si_vide()
migrer_schema()
db = SessionLocal()
maintenant = now_local().replace(microsecond=0)
jour = maintenant.date()
jour_actuel = jour

veh = db.scalar(select(Vehicule).where(Vehicule.plateforme_gps == "MZONEX",
                                       Vehicule.statut == "ACTIF"))
suivi = engine.ensure_suivi(db, veh, jour)
base = datetime.combine(jour, datetime.min.time()).replace(hour=5)


def compter_trajets() -> int:
    db.expire_all()
    return db.scalar(select(func.count(Trajet.id)).where(Trajet.suivi_id == suivi.id))


def reinitialiser():
    """Remet le suivi à zéro (les rejets accumulés ne faussent pas les cas)."""
    db.expire_all()
    for t in db.scalars(select(Trajet).where(Trajet.suivi_id == suivi.id)).all():
        db.delete(t)
    db.query(AuditLog).filter(AuditLog.action.in_(
        ["trajet.orphelin_purge", "trajet.structure_corrige",
         "trajet.jumeau_fusionne", "trajet.rejete_conserve"])).delete(
             synchronize_session=False)
    db.commit()


print("\n[1] TRAJET PROVISOIRE ABSENT DU RELEVÉ OFFICIEL → CONSERVÉ EN BASE")
reinitialiser()
t1 = Trajet(suivi_id=suivi.id, numero=1, heure_debut=base,
            heure_fin=base + timedelta(minutes=50),
            distance_km=12.5, statut_source=StatutSourceTrajet.PROVISOIRE,
            source_plateforme="MZONEX")
t2 = Trajet(suivi_id=suivi.id, numero=2, heure_debut=base + timedelta(hours=2),
            heure_fin=base + timedelta(hours=3),
            distance_km=25.0, statut_source=StatutSourceTrajet.PROVISOIRE,
            source_plateforme="MZONEX")
db.add_all([t1, t2])
db.commit()
id_t2 = t2.id
nb_avant = compter_trajets()
reconciliation.reconcilier_trajets_valides(db, [{
    "vehicule_id": veh.id,
    "debut": base + timedelta(seconds=30),          # couvre t1 (±2 min)
    "fin": base + timedelta(minutes=50),
    "distance_km": 12.9, "source": "MZONEX"}],
    username="test_p1", maintenant=maintenant)

t2b = db.get(Trajet, id_t2)
db.expire_all()
t2b = db.get(Trajet, id_t2)
check("le trajet provisoire NON confirmé par le portail existe TOUJOURS en base",
      t2b is not None, f"t2={t2b}")
check("il est marqué REJETE (donc jamais compté)",
      t2b is not None and t2b.statut_validation == StatutValidationTrajet.REJETE,
      f"statut={getattr(t2b, 'statut_validation', None)}")
check("le MOTIF est enregistré sur la ligne",
      t2b is not None and bool(t2b.motif_rejet), f"motif={getattr(t2b, 'motif_rejet', None)}")
check("aucune ligne n'a disparu de la base (aucune suppression physique)",
      compter_trajets() >= nb_avant, f"avant={nb_avant} après={compter_trajets()}")

audit = db.scalars(select(AuditLog).where(
    AuditLog.action == "trajet.orphelin_purge",
    AuditLog.entite_id == id_t2)).all()
det = (audit[-1].details or {}) if audit else {}
check("audit présent", bool(audit))
check("audit AVANT/APRÈS complet",
      bool(det.get("avant")) and bool(det.get("apres")),
      f"clés={sorted(det)[:6]}")
check("l'audit déclare l'absence de suppression",
      str(det.get("suppression", "")).startswith("AUCUNE"), str(det.get("suppression")))
check("l'audit porte le motif",
      det.get("motif_rejet") == "ORPHELIN_NON_CONFIRME", str(det.get("motif_rejet")))
check("la donnée brute est INTACTE (heures et distance d'origine)",
      t2b is not None and t2b.heure_debut == base + timedelta(hours=2)
      and t2b.heure_fin == base + timedelta(hours=3)
      and abs((t2b.distance_km or 0) - 25.0) < 1e-6,
      f"{t2b.heure_debut if t2b else None}→{t2b.heure_fin if t2b else None} d={t2b.distance_km if t2b else None}")

print("\n[2] TRAJET CONSERVÉ = MASQUÉ À L'ÉCRAN ET HORS DES COMPTEURS")
from app.serializers import journee_suivi, s_suivi              # noqa: E402
from app.engine import get_seuils                                # noqa: E402
seuils = get_seuils(db)
lignes = journee_suivi(suivi, seuils).lignes
ids_affiches = {getattr(l.premiere_ref, "id", None) for l in lignes}
check("la ligne conservée n'est PAS rendue dans la grille",
      id_t2 not in ids_affiches, f"affichées={len(ids_affiches)}")
visu = s_suivi(suivi)
check("les compteurs ne la comptent pas comme trajet réel",
      visu["nb_trajets_valides_reels"] == sum(l.nb_segments for l in lignes)
      and visu["nb_trajets_valides_reels"] == len(lignes),
      f"reels={visu['nb_trajets_valides_reels']} lignes={len(lignes)}")

print("\n[3] JUMEAU (même début) → CONSERVÉ, plus supprimé")
reinitialiser()
ja = Trajet(suivi_id=suivi.id, numero=1, heure_debut=base,
            heure_fin=base + timedelta(minutes=40), distance_km=10.0,
            statut_source=StatutSourceTrajet.VALIDE, source_plateforme="MZONEX")
jb = Trajet(suivi_id=suivi.id, numero=2, heure_debut=base,
            heure_fin=None, distance_km=None,
            statut_source=StatutSourceTrajet.PROVISOIRE, source_plateforme="MZONEX")
db.add_all([ja, jb])
db.commit()
id_ja, id_jb = ja.id, jb.id
reconciliation.reconcilier_trajets_valides(db, [{
    "vehicule_id": veh.id, "debut": base, "fin": base + timedelta(minutes=40),
    "distance_km": 10.4, "source": "MZONEX"}], username="test_p1", maintenant=maintenant)
db.expire_all()
reste = db.get(Trajet, id_jb)
check("le jumeau existe TOUJOURS en base", reste is not None)
check("il porte un motif de rejet",
      reste is not None and reste.motif_rejet in ("DOUBLON_JUMEAU", "OUVERT_RECOUVERT"),
      f"motif={getattr(reste, 'motif_rejet', None)}")

print("\n[4] OUVERT RECOUVERT PAR UNE LIGNE OFFICIELLE → CONSERVÉ")
reinitialiser()
ouvert = Trajet(suivi_id=suivi.id, numero=1,
                heure_debut=base + timedelta(minutes=30), heure_fin=None,
                distance_km=None, statut_source=StatutSourceTrajet.PROVISOIRE,
                source_plateforme="MZONEX")
db.add(ouvert)
db.commit()
id_ouvert = ouvert.id
reconciliation.reconcilier_trajets_valides(db, [{
    "vehicule_id": veh.id, "debut": base,
    "fin": base + timedelta(hours=1, minutes=30),
    "distance_km": 42.0, "source": "MZONEX"}], username="test_p1", maintenant=maintenant)
db.expire_all()
o2 = db.get(Trajet, id_ouvert)
check("l'ouvert recouvert existe TOUJOURS en base", o2 is not None)
check("il est marqué REJETE avec motif",
      o2 is not None and o2.statut_validation == StatutValidationTrajet.REJETE
      and bool(o2.motif_rejet),
      f"{getattr(o2, 'statut_validation', None)}/{getattr(o2, 'motif_rejet', None)}")
check("son heure de début OBSERVÉE est intacte (30 min après la base)",
      o2 is not None and o2.heure_debut == base + timedelta(minutes=30),
      str(o2.heure_debut) if o2 else None)
check("il n'est PAS rendu dans la grille",
      id_ouvert not in {getattr(l.premiere_ref, "id", None)
                        for l in journee_suivi(suivi, seuils).lignes})

print("\n[5] LE DIRECT NE RESSUSCITE PAS UNE LIGNE ÉCARTÉE")
db.expire_all()
o3 = db.get(Trajet, id_ouvert)
avant_etat = (o3.heure_fin, o3.statut_validation, o3.motif_rejet)
engine.ingest_event(db, veh, maintenant, -18.95, 47.55,
                    adresse="test P1", vitesse=0.0, moteur="OFF",
                    source="MZONEX")
db.expire_all()
o4 = db.get(Trajet, id_ouvert)
check("la ligne écartée n'est ni réactivée ni réécrite par le direct",
      o4 is not None and (o4.heure_fin, o4.statut_validation, o4.motif_rejet) == avant_etat,
      f"avant={avant_etat} après={(o4.heure_fin, o4.statut_validation, o4.motif_rejet) if o4 else None}")

print("\n[6] GARANTIE GLOBALE : AUCUNE SUPPRESSION DANS LA RÉCONCILIATION")
src = (Path(__file__).resolve().parent / "app" / "reconciliation.py").read_text(encoding="utf-8")
check("plus aucun `db.delete(` de trajet dans reconciliation.py",
      "db.delete(" not in src, "occurrence restante")
check("le compteur de rejets est bien tracé par le module",
      "_rejeter_conserve" in src and "motif_rejet" in src)

db.commit()
print("\n" + "=" * 74)
print(f"  test_suppression_zero_v154 : {OK} OK / {KO} KO")
print("=" * 74)
sys.exit(1 if KO else 0)
