"""Tests v1.24 — correctifs de lecture D4 + veto « début à l'arrêt »
(constats réels du 14/08/2026 après-midi sur la v1.23 en production) :

  C1 · auto-réparation v1.17 : un « Début du trajet » publié À L'ARRÊT
       (vitesse ≤ SEUIL_VITESSE_ARRET — blip de contact, boîtier muet) ne
       doit JAMAIS être ré-ingéré (ligne fantôme rejetée manœuvre en boucle)
       ; un début FRAIS avec vitesse > seuil reste ré-ingéré.
  C2 · garde anti-liste-partielle : si le recensement d'un portail couvre
       < 80 % de ses véhicules ACTIFS → récolte « partielle » signalée et
       liste des absents SUSPENDUE pour ce portail (plus jamais le faux
       « vus nulle part » de 34 camions sains du 14/08).
  C3 · D5 trace « non-personne » une seule fois (le comportement fonctionnel
       — aucune fiche — est quant à lui stable aux appels répétés).

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v124.db" python3 test_correctifs_v124.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import timedelta

from sqlalchemy import delete, func, select

from app.config import now_local
from app.database import SessionLocal
from app import engine
from app.engine import creer_conducteur_auto, recenser_flotte
from app.models import (Alerte, AuditLog, EvenementGPS, SourceEvenement,
                        StatutVehicule, SuiviJournalier, Trajet,
                        TypeEvenement, Vehicule)
from app.scrapers import _reparer_debuts_sans_trajet
from app.seed import seed_si_vide
from app.main import migrer_schema

R = {"ok": 0, "ko": 0}


def check(nom, cond, info=""):
    if cond:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} {info}")


db_url = os.environ.get("DATABASE_URL", "")
if "/tmp/" not in db_url and "test" not in db_url:
    print("⛔ Sécurité : lancez ce test avec DATABASE_URL pointant une base de "
          "test — jamais la base de production.")
    sys.exit(2)

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
db = SessionLocal()
for modele in (EvenementGPS, Trajet, SuiviJournalier, Alerte, AuditLog,
               Vehicule):
    db.execute(delete(modele))
db.flush()

print("\n[C1 · v1.24] Veto « début à l'arrêt » dans l'auto-réparation")
maintenant = now_local()


def vehicule_event(il_y_a_s, immat, vitesse):
    v = Vehicule(plaque=immat, gps_associe=immat,
                 plateforme_gps="MZONEX", statut=StatutVehicule.ACTIF)
    db.add(v)
    db.flush()
    db.add(EvenementGPS(
        vehicule_id=v.id, horodatage=maintenant - timedelta(seconds=il_y_a_s),
        latitude=-18.94, longitude=48.21, vitesse=vitesse, etat_moteur="ON",
        type_evenement=TypeEvenement.DEBUT_MOUVEMENT,
        source=SourceEvenement.MZONEX))
    db.flush()
    return v


def nb_trajets(v):
    return db.scalar(select(func.count(Trajet.id)).join(
        SuiviJournalier, Trajet.suivi_id == SuiviJournalier.id).where(
        SuiviJournalier.vehicule_id == v.id)) or 0


v_blip = vehicule_event(10 * 60, "8076TCB", 0.0)   # frais mais À L'ARRÊT
v_roule = vehicule_event(10 * 60, "0916TBU", 8.0)  # frais et EN MOUVEMENT
rep = _reparer_debuts_sans_trajet(db, [v_blip, v_roule],
                                  SourceEvenement.MZONEX)
check("début frais à 0 km/h (blip de contact) : NON ré-ingéré, aucune ligne",
      rep == 1 and nb_trajets(v_blip) == 0)
check("début frais en mouvement (8 km/h) : ré-ingéré, ligne « en cours »",
      nb_trajets(v_roule) == 1)

print("\n[C2 · v1.24] Garde anti-liste-partielle (recensement < 80 %)")
for i in range(5):     # 5 actifs MZoneX, seul le 1er sera « publié » (20 %)
    db.add(Vehicule(plaque=f"20{16 + i:02d}TCB", gps_associe=f"20{16 + i:02d}TCB",
                    plateforme_gps="MZONEX", statut=StatutVehicule.ACTIF))
db.flush()
stats = recenser_flotte(db, {"MZONEX": ["2016 TCB (LSS)"]},
                        username="collecteur-test")
check("récolte 1/5 → « partielle » signalée",
      any(p.startswith("MZONEX (1/") for p in stats.get("partiels", [])))
check("liste des absents SUSPENDUE pour MZoneX (plus de faux positifs)",
      stats["absents"] == [])

print("\n[C3 · v1.24] D5 : trace unique, comportement stable")
check("« Garage LSS 2 » bloqué deux fois de suite, jamais de fiche",
      creer_conducteur_auto(db, "Garage LSS 2") is None
      and creer_conducteur_auto(db, "Garage LSS 2") is None)

db.close()
url = os.environ.get("DATABASE_URL", "")
if url.startswith("sqlite:////tmp/"):
    try:
        os.remove(url.replace("sqlite:///", ""))
    except OSError:
        pass
print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===\n")
sys.exit(1 if R["ko"] else 0)
