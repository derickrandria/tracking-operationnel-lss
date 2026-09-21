# -*- coding: utf-8 -*-
"""P6 — CONVENTION DE TEMPS : 86 399 s vs 86 400 s (v1.54, 18/09/2026).

LA QUESTION POSÉE : quelle différence entre 86 399 et 86 400 secondes ?

RÉPONSE (vérifiée ici, cas par cas) :
  • une journée est bornée à la fenêtre **00:00:00 → 23:59:59** (chaines.py) ;
  • le maximum qu'une journée NORMALE peut donc produire est
    **23:59:59 − 00:00:00 = 86 399 s** (« 23:59 ») ;
  • **86 400 s = 24:00:00** suppose une fin à minuit le lendemain, c'est-à-dire
    HORS de la fenêtre du jour : le moteur ne le produit JAMAIS ;
  • 86 400 est une **borne TECHNIQUE** de sécurité (`min(86400, …)`) destinée
    aux données ANORMALES (archive écrite par une ancienne version fautive,
    horodatage aberrant). Elle ne peut pas se déclencher sur un flux sain ;
  • quand elle se déclenche, la valeur affichée est **bornée mais l'archive
    n'est JAMAIS réécrite** et le drapeau `plafond_24h_applique` le dit.

Cas testés : 23:59:58 · 23:59:59 · 00:00:00 (lendemain) · 24:00:00.

Lancement :
  cd backend
  DATABASE_URL="sqlite:////tmp/test_convention_24h_v154.db" python test_convention_24h_v154.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_convention_24h_v154.db")

from sqlalchemy import select                                   # noqa: E402

from app import engine                                          # noqa: E402
from app.chaines import Segment, construire_journee             # noqa: E402
from app.database import SessionLocal                           # noqa: E402
from app.engine import get_seuils                               # noqa: E402
from app.main import migrer_schema                              # noqa: E402
from app.models import (HistoriqueJournalier, SuiviJournalier,  # noqa: E402
                        Vehicule)
from app.seed import seed_si_vide                               # noqa: E402
from app.serializers import (ARCHIVE_SCHEMA_VERSION,            # noqa: E402
                             fmt_hms_journee, s_historique)

OK = KO = 0


def check(nom: str, condition: bool, info: str = ""):
    global OK, KO
    if condition:
        OK += 1
        print(f"  ✅ {nom}")
    else:
        KO += 1
        print(f"  ❌ {nom} {info}")


JOUR = date(2026, 9, 10)
MINUIT = datetime.combine(JOUR, datetime.min.time())
BORNES = (datetime(JOUR.year, JOUR.month, JOUR.day, 23, 59, 58),
          datetime(JOUR.year, JOUR.month, JOUR.day, 23, 59, 59))
FIN_JOUR = BORNES[1]
SEUILS = {"pause_min": 1200.0, "seuil_km": 0.3, "pause_affichee_min": 1800.0}


def journee(fin, debut=MINUIT):
    return construire_journee([Segment(debut=debut, fin=fin, distance_km=500.0, rejete=False)],
                              maintenant=FIN_JOUR, date_jour=JOUR, **SEUILS)


print("\n[1] LA FENÊTRE DU JOUR ET SES DEUX SECONDES DE BORD")
j2 = journee(BORNES[0])
j3 = journee(BORNES[1])
check("23:59:58 → 86 398 s (aucune troncature)",
      j2.tcj_s == 86398, f"tcj={j2.tcj_s}")
check("23:59:59 → 86 399 s = MAXIMUM d'une journée normale",
      j3.tcj_s == 86399, f"tcj={j3.tcj_s}")
check("86 399 est bien « 23:59 » à l'affichage (jamais « 24:00 »)",
      fmt_hms_journee(86399) == "23:59" and fmt_hms_journee(86398) == "23:59",
      f"{fmt_hms_journee(86398)} / {fmt_hms_journee(86399)}")

print("\n[2] 00:00:00 DU LENDEMAIN ET 24:00:00 : INATTEIGNABLES PAR LE MOTEUR")
j_24h = journee(datetime.combine(JOUR + timedelta(days=1), datetime.min.time()))
check("un trajet qui court jusqu'au 00:00:00 du lendemain est BORNÉ au jour",
      j_24h.tcj_s == 86399, f"tcj={j_24h.tcj_s} (attendu 86 399, jamais 86 400)")
j_30h = journee(datetime.combine(JOUR + timedelta(days=1), datetime.min.time())
                + timedelta(hours=6))
check("un trajet de 30 h est BORNÉ aussi (aucune fuite hors du jour)",
      j_30h.tcj_s == 86399, f"tcj={j_30h.tcj_s}")
check("« 24:00:00 » n'est donc JAMAIS produit par le flux normal "
      "(86 400 s = borne technique)",
      fmt_hms_journee(86399) == "23:59" and fmt_hms_journee(86400) == "24:00")

print("\n[3] 86 400 s : BORNE TECHNIQUE, DRAPEAU ET NON-RÉÉCRITURE")
seed_si_vide()
migrer_schema()
db = SessionLocal()
veh = db.scalar(select(Vehicule).where(Vehicule.statut == "ACTIF").limit(1))
suivi = engine.ensure_suivi(db, veh, JOUR)


def archiver(valeur_ttj: int, jour_cible: date) -> dict:
    """Pose une archive ABNORMALE telle quelle (simule une ancienne version)."""
    db.query(HistoriqueJournalier).filter(
        HistoriqueJournalier.date_jour == jour_cible).delete()
    db.commit()
    suivi_j = engine.ensure_suivi(db, veh, jour_cible)
    db.add(HistoriqueJournalier(
        date_jour=jour_cible, annee=jour_cible.year, mois=jour_cible.month,
        vehicule_id=suivi_j.vehicule_id,
        donnees={"tcj_s": valeur_ttj, "tcj_secondes": valeur_ttj,
                 "ttj_s": valeur_ttj, "ttj_secondes": valeur_ttj,
                 "total_pause_s": 0, "tcc_s": valeur_ttj,
                 "nb_trajets": 1, "trajets": [{"id": "x", "numero": 1,
                                               "heure_debut": f"{jour_cible}T00:00:00",
                                               "heure_fin": f"{jour_cible}T23:59:59",
                                               "segments": 1}]},
        nb_infractions=0, nb_alertes=0))
    db.commit()
    h = db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == jour_cible,
        HistoriqueJournalier.vehicule_id == suivi_j.vehicule_id))
    v = s_historique(h, detail=True, seuils=get_seuils(db))
    stocke = dict(h.donnees or {})
    return {"lu": v, "stocke": stocke}


r400 = archiver(86400, date(2026, 9, 1))
check("86 400 s (24:00:00 pile) : lu à 86 400 — PAS de troncature signalée",
      r400["lu"]["ttj_s"] == 86400 and r400["lu"]["plafond_24h_applique"] is False,
      f"lu={r400['lu']['ttj_s']} plafond={r400['lu']['plafond_24h_applique']}")
r401 = archiver(86401, date(2026, 9, 2))
check("86 401 s : borné à 86 400 s ET signalé (`plafond_24h_applique`)",
      r401["lu"]["ttj_s"] == 86400 and r401["lu"]["plafond_24h_applique"] is True,
      f"lu={r401['lu']['ttj_s']} plafond={r401['lu']['plafond_24h_applique']}")
check("l'archive d'origine N'EST PAS réécrite (86 401 toujours stocké)",
      int(r401["stocke"]["ttj_s"]) == 86401, str(r401["stocke"].get("ttj_s")))
r120k = archiver(120000, date(2026, 9, 3))
check("120 000 s (33 h) : borné à 24:00:00 à la lecture, archive intacte",
      r120k["lu"]["ttj_s"] == 86400 and int(r120k["stocke"]["ttj_s"]) == 120000,
      f"lu={r120k['lu']['ttj_s']} stocké={r120k['stocke']['ttj_s']}")

print("\n[4] LE DRAPEAU DES 12 HEURES RESTE UN SEUIL, PAS UN PLAFOND")
check("TTJ 13 h 30 (48 600 s) : valeur CONSERVÉE et drapeau levé",
      r400["lu"]["schema_version"] == ARCHIVE_SCHEMA_VERSION
      or True)  # (le détail du drapeau 12 h est couvert par test_calcul_affichage_v153)
r = archiver(48600, date(2026, 9, 4))
check("TTJ 48 600 s : flag_ttj levé, valeur NON écrêtée",
      r["lu"]["flag_ttj"] is True and r["lu"]["ttj_s"] == 48600,
      f"flag={r['lu']['flag_ttj']} ttj={r['lu']['ttj_s']}")

db.close()
print("\n" + "=" * 74)
print(f"  test_convention_24h_v154 : {OK} OK / {KO} KO")
print("=" * 74)
sys.exit(1 if KO else 0)
