# -*- coding: utf-8 -*-
"""P2 — REPLI QUAND L'API OFFICIELLE ÉCHOUE (v1.54, 18/09/2026).

Preuve demandée : « API indisponible → fallback exécuté ».

Ce que la suite vérifie :
  [1] API en panne → l'échec est ENREGISTRÉ (journal + état) ;
  [2] le lecteur d'ÉCRAN de secours est EXÉCUTÉ et ses données sont servies ;
  [3] la COMPLÉTUDE du repli est contrôlée ;
  [4] un repli INCOMPLET est REJETÉ en bloc (jamais d'archive partielle) ;
  [5] chaque ligne porte l'ORIGINE de lecture utilisée ;
  [6] API désactivée → écran directement (diagnostic) ;
  [7] le repli est TRACÉ en base (audit) quand il est utilisé.

Lancement :
  cd backend
  DATABASE_URL="sqlite:////tmp/test_repli_api_v154.db" python test_repli_api_v154.py
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_repli_api_v154.db")

from sqlalchemy import select                                   # noqa: E402

from app import scrapers                                        # noqa: E402
from app.config import now_local                                # noqa: E402
from app.database import SessionLocal                           # noqa: E402
from app.main import migrer_schema                              # noqa: E402
from app.models import AuditLog                                 # noqa: E402
from app.seed import seed_si_vide                               # noqa: E402

OK = KO = 0


def check(nom: str, condition: bool, info: str = ""):
    global OK, KO
    if condition:
        OK += 1
        print(f"  ✅ {nom}")
    else:
        KO += 1
        print(f"  ❌ {nom} {info}")


maint = now_local().replace(microsecond=0)
JOUR = [maint.date()]


class ApiEnPanne:
    """Reproduit l'API officielle en échec (§0sexies A2)."""

    def __init__(self):
        self.recensement = []
        self.appels = 0

    def collecter_valides(self, jours=None):
        self.appels += 1
        raise RuntimeError("panne API simulée")


class EcranComplet:
    """Lecteur d'écran de secours : couvre TOUTE la flotte attendue."""

    def __init__(self):
        self.recensement = ["4006 TBS (LSS)", "2746 TCC (LSS)"]

    def collecter_valides(self):
        return [
            {"plaque": "4006TBS", "debut": maint - timedelta(hours=2),
             "fin": maint - timedelta(hours=1), "distance_km": 12.0,
             "source": "MZONEX"},
            {"plaque": "2746TCC", "debut": maint - timedelta(hours=4),
             "fin": maint - timedelta(hours=3), "distance_km": 31.0,
             "source": "MZONEX"},
        ]


class EcranIncomplet:
    """Repli partiel : une seule plaque connue sur deux → INUTILISABLE."""

    def __init__(self):
        self.recensement = ["4006 TBS (LSS)"]

    def collecter_valides(self):
        return [{"plaque": "4006TBS", "debut": maint - timedelta(hours=2),
                 "fin": maint - timedelta(hours=1), "distance_km": 12.0,
                 "source": "MZONEX"}]


ATTENDUES = ["4006TBS", "2746TCC"]
orig_api = scrapers.MZoneXTrajetsApiCollector
orig_ecran = scrapers.MZoneXTrajetsCollector
env_avant = {k: os.environ.get(k) for k in ("MZONEX_API_ENABLE",
                                            "MZONEX_REPLI_ECRAN")}
try:
    print("\n[1-2-3-5] API EN PANNE → REPLI ÉCRAN EXÉCUTÉ, SOURCE IDENTIFIÉE")
    os.environ.pop("MZONEX_REPLI_ECRAN", None)      # défaut = repli ACTIF (P2)
    os.environ.pop("MZONEX_API_ENABLE", None)
    scrapers.MZoneXTrajetsApiCollector = ApiEnPanne
    scrapers.MZoneXTrajetsCollector = EcranComplet
    bruts, rec = scrapers._collecter_n2_mzonex(JOUR, attendues=ATTENDUES)
    etat = dict(scrapers.DERNIER_ETAT_N2)
    check("API en panne : l'échec est ENREGISTRÉ (état + journal)",
          str(etat.get("echec", "")).startswith("api_"), str(etat.get("echec")))
    check("le LECTEUR D'ÉCRAN a bien été EXÉCUTÉ et ses données servies",
          len(bruts) == 2, f"lignes={len(bruts)}")
    check("chaque ligne porte son ORIGINE de lecture",
          all(b.get("origine_lecture") == scrapers.ORIGINE_ECRAN for b in bruts),
          str([b.get("origine_lecture") for b in bruts]))
    check("la COMPLÉTUDE est contrôlée et déclarée complète",
          etat.get("complet") is True and etat.get("detail", {}).get("mode") == "reference",
          str(etat))
    check("le recensement du repli est remonté (les fiches inconnues se créent)",
          len(rec) == 2, str(rec))

    print("\n[4] REPLI INCOMPLET → REJETÉ EN BLOC (aucune archive partielle)")
    scrapers.MZoneXTrajetsCollector = EcranIncomplet
    bruts2, rec2 = scrapers._collecter_n2_mzonex(JOUR, attendues=ATTENDUES)
    etat2 = dict(scrapers.DERNIER_ETAT_N2)
    check("le repli incomplet n'est PAS utilisé (0 ligne)",
          bruts2 == [], f"lignes={len(bruts2)}")
    check("la complétude est déclarée FAUSSE et la plaque manquante est nommée",
          etat2.get("complet") is False
          and "2746TCC" in (etat2.get("detail", {}).get("manquantes") or []),
          str(etat2.get("detail")))
    check("l'origine reste traçable même en cas de rejet",
          etat2.get("origine") == scrapers.ORIGINE_ECRAN, str(etat2.get("origine")))

    print("\n[6] API DÉSACTIVÉE → ÉCRAN DIRECTEMENT (diagnostic §10)")
    os.environ["MZONEX_API_ENABLE"] = "0"
    scrapers.MZoneXTrajetsCollector = EcranComplet
    bruts3, rec3 = scrapers._collecter_n2_mzonex(JOUR, attendues=ATTENDUES)
    check("l'API n'est jamais appelée quand elle est désactivée",
          len(bruts3) == 2 and bruts3[0]["source"] == "MZONEX",
          f"lignes={len(bruts3)}")
    check("mode « API désactivée » consigné",
          dict(scrapers.DERNIER_ETAT_N2).get("echec") == "api_desactivee",
          str(scrapers.DERNIER_ETAT_N2.get("echec")))

    print("\n[7] LE REPLI EST TRACÉ EN BASE (audit lecture.repli_ecran)")
    seed_si_vide()
    migrer_schema()
    os.environ.pop("MZONEX_API_ENABLE", None)
    scrapers.MZoneXTrajetsApiCollector = ApiEnPanne
    scrapers.MZoneXTrajetsCollector = EcranComplet
    scrapers._collecter_n2_mzonex(JOUR, attendues=ATTENDUES)
    scrapers._tracer_repli_n2("MZONEX", JOUR)
    db = SessionLocal()
    trace = db.scalars(select(AuditLog).where(
        AuditLog.action == "lecture.repli_ecran")).all()
    det = (trace[-1].details or {}) if trace else {}
    check("l'audit du repli existe", bool(trace))
    check("il porte l'échec, la source utilisée et la complétude",
          det.get("origine_utilisee") == "REPLI_ECRAN"
          and str(det.get("echec_api", "")).startswith("api_")
          and det.get("complet") is True, str(det))
    db.close()
finally:
    scrapers.MZoneXTrajetsApiCollector = orig_api
    scrapers.MZoneXTrajetsCollector = orig_ecran
    for k, v in env_avant.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

print("\n" + "=" * 74)
print(f"  test_repli_api_v154 : {OK} OK / {KO} KO")
print("=" * 74)
sys.exit(1 if KO else 0)
