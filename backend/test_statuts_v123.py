"""Tests v1.23 — COMPLÉMENT §0quinquies D4 (14/08/2026 après-midi).

Preuve exploitant : l'onglet « Statuts » de CamtrackPro publie TOUTES les
unités du compte (6256/7136/7206TCE, 7766TBL) alors que le filtre « objet »
du rapport en omet. Le recensement CamtrackPro lit désormais les DEUX sources
en UNION. Ici on verrouille la plomberie côté serveur :

  · parser : libellés « Statuts » (« 7766 TBL-SINOTRUK HOWO-LPSA(LSS) ») ;
  · déduplication : la même plaque arrivant par DEUX sources (combo +
    Statuts, formes différentes) n'est comptée/créée/basculée qu'UNE fois ;
  · bascule sur plaque vue uniquement via la forme « Statuts ».

⚠️  La lecture Playwright réelle (clic « Statuts », scan texte, défilement)
    n'est PAS testée ici : pas de navigateur dans l'environnement de test —
    elle est volontairement défensive et tracée (repli combo en cas d'échec).

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v123.db" python3 test_statuts_v123.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys

from sqlalchemy import delete, func, select

from app.config import plaque_depuis_libelle_portail
from app.database import SessionLocal
from app import engine
from app.engine import recenser_flotte
from app.models import (Alerte, AuditLog, EvenementGPS, StatutVehicule,
                        SuiviJournalier, Trajet, TypeAlerte, Vehicule)
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

print("\n[D4+ · 14/08 PM] Libellés de la page « Statuts » CamtrackPro")
check("« 6256 TCE-CNHTC-LPSA(LSS) » → « 6256TCE »",
      plaque_depuis_libelle_portail("6256 TCE-CNHTC-LPSA(LSS)") == "6256TCE")
check("« 7766 TBL-SINOTRUK HOWO-LPSA(LSS) » → « 7766TBL »",
      plaque_depuis_libelle_portail("7766 TBL-SINOTRUK HOWO-LPSA(LSS)")
      == "7766TBL")
check("forme déjà normalisée « 7136TCE » (scan texte) → « 7136TCE »",
      plaque_depuis_libelle_portail("7136TCE") == "7136TCE")

print("\n[D4+ · 14/08 PM] Union doublonnée : comptée et traitée UNE fois")
v = Vehicule(plaque="7136TCE", gps_associe="OBC-7136TCE",
             plateforme_gps="MZONEX", statut=StatutVehicule.ACTIF)
db.add(v)
db.flush()
# la MÊME plaque vue sous DEUX formes (combo du rapport + page Statuts)
stats = recenser_flotte(db, {"CAMTRACKPRO": [
    "7136 TCE-CNHTC-LPSA(LSS)", "7136TCE",
    "6256 TCE-CNHTC-LPSA(LSS)"]}, username="collecteur-test")
check("bascule unique : 7136TCE MZONEX → CAMTRACKPRO (vus = 2, pas 3)",
      stats["vus"] == 2 and stats["bascules"] == 1
      and db.get(Vehicule, v.id).plateforme_gps == "CAMTRACKPRO")
check("création unique pour 6256TCE (CAMTRACKPRO) + alerte ⓘ unique pour elle",
      stats["crees"] == 1
      and db.scalar(select(func.count(Vehicule.id)).where(
          Vehicule.plaque == "6256TCE")) == 1
      and db.scalar(select(func.count(Alerte.id)).where(
          Alerte.type == TypeAlerte.NOUVEAU_VEHICULE,
          Alerte.message.like("%6256TCE%"))) == 1)
check("rejeu immédiat : 0 création, 0 bascule (idempotent sur l'union)",
      recenser_flotte(db, {"CAMTRACKPRO": [
          "7136 TCE-CNHTC-LPSA(LSS)", "7136TCE",
          "6256 TCE-CNHTC-LPSA(LSS)"]},
          username="collecteur-test")["bascules"] == 0)

print("\n[D4+ · 14/08 PM] Absents après union des deux sources")
check("7136TCE/6256TCE ne sont PAS signalés absents (vus via Statuts)",
      stats["absents"] == [])

db.close()
url = os.environ.get("DATABASE_URL", "")
if url.startswith("sqlite:////tmp/"):
    try:
        os.remove(url.replace("sqlite:///", ""))
    except OSError:
        pass
print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===\n")
sys.exit(1 if R["ko"] else 0)
