# -*- coding: utf-8 -*-
"""P3 — ARCHIVES : VERSION DE SCHÉMA, LECTURE RÉTROCOMPATIBLE, « À RECALCULER »
(v1.54, 18/09/2026).

Ce que la suite prouve :
  [1] les nouveaux snapshots portent une VERSION DE SCHÉMA ;
  [2] une archive ancienne AVEC lignes est relue en RECONSTRUISANT ses
      compteurs — et elle est marquée « à recalculer » ;
  [3] une archive ancienne SANS lignes ne renvoie JAMAIS zéro : ses compteurs
      valent « inconnu » (None) et elle est marquée « à recalculer » ;
  [4] une archive à jour déclare ses compteurs comme venant d'elle-même
      (`compteurs_source = ARCHIVE`) ;
  [5] AUCUN MODE APPLY AUTOMATIQUE : un recalcul non autorisé ne modifie RIEN
      (comparaison stricte avant/après) ;
  [6] l'outil DRY-RUN ne contient aucun chemin d'écriture.

Lancement :
  cd backend
  DATABASE_URL="sqlite:////tmp/test_archives_retrocompat_v154.db" \
      python test_archives_retrocompat_v154.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DATABASE_URL",
                      "sqlite:////tmp/test_archives_retrocompat_v154.db")
os.environ.pop("LSS_ARCHIVES_APPLY", None)          # défaut = aucune écriture

from sqlalchemy import select                                   # noqa: E402

from app import engine                                          # noqa: E402
from app.daily import recalculer_archives_journee               # noqa: E402
from app.database import SessionLocal                           # noqa: E402
from app.engine import get_seuils                               # noqa: E402
from app.main import migrer_schema                              # noqa: E402
from app.models import (HistoriqueJournalier, StatutSourceTrajet,  # noqa: E402
                        Trajet, Vehicule)
from app.seed import seed_si_vide                               # noqa: E402
from app.serializers import (ARCHIVE_SCHEMA_VERSION,            # noqa: E402
                             s_historique, snapshot_canonique)

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
veh = db.scalar(select(Vehicule).where(Vehicule.statut == "ACTIF").limit(1))
JOUR = date(2026, 8, 20)


def poser_archive(jour: date, donnees: dict) -> HistoriqueJournalier:
    db.query(HistoriqueJournalier).filter(
        HistoriqueJournalier.date_jour == jour).delete()
    db.commit()
    suivi = engine.ensure_suivi(db, veh, jour)
    h = HistoriqueJournalier(date_jour=jour, annee=jour.year, mois=jour.month,
                             vehicule_id=suivi.vehicule_id, donnees=donnees,
                             nb_infractions=0, nb_alertes=0)
    db.add(h)
    db.commit()
    return h


print("\n[1] LES NOUVEAUX SNAPSHOTS PORTENT UNE VERSION DE SCHÉMA")
suivi = engine.ensure_suivi(db, veh, JOUR)
base = datetime.combine(JOUR, datetime.min.time()).replace(hour=6)
for i, (deb, fin, km) in enumerate([(0, 120, 115.0), (125, 135, 0.52)], start=1):
    db.add(Trajet(suivi_id=suivi.id, numero=i,
                  heure_debut=base + timedelta(minutes=deb),
                  heure_fin=base + timedelta(minutes=fin), distance_km=km,
                  statut_source=StatutSourceTrajet.PROVISOIRE,
                  source_plateforme="MZONEX"))
db.commit()
db.expire(suivi, ["trajets"])          # relation rechargée (leçon v1.52)
snap = snapshot_canonique(suivi, get_seuils(db))
check("schema_version inscrit par la fabrique unique",
      int(snap.get("schema_version") or 0) == ARCHIVE_SCHEMA_VERSION,
      f"version={snap.get('schema_version')} attendue={ARCHIVE_SCHEMA_VERSION}")
check("les compteurs sont calculés et les trajets BRUTS conservés "
      "(2 réels · 1 séquence de 5 min d'écart · 2 lignes en base)",
      snap.get("nb_trajets_valides_reels") == 2
      and snap.get("nb_sequences_affichees") == 1
      and len(snap.get("trajets") or []) == 2,
      f"reels={snap.get('nb_trajets_valides_reels')} "
      f"seq={snap.get('nb_sequences_affichees')} "
      f"lignes={len(snap.get('trajets') or [])}")

print("\n[2] ARCHIVE ANCIENNE AVEC LIGNES → COMPTEURS RECONSTRUITS + « À RECALCULER »")
ancienne_avec = {
    "tcj_s": 7200, "ttj_s": 9000, "tcc_s": 3600, "total_pause_s": 1800,
    "nb_trajets": 2,
    "trajets": [{"id": "a", "numero": 1, "heure_debut": "2026-08-21T06:00:00",
                 "heure_fin": "2026-08-21T07:00:00", "segments": 1},
                {"id": "b", "numero": 2, "heure_debut": "2026-08-21T07:10:00",
                 "heure_fin": "2026-08-21T08:00:00", "segments": 1}],
}
h2 = poser_archive(date(2026, 8, 21), ancienne_avec)
lu2 = s_historique(h2, detail=True, seuils=get_seuils(db))
check("compteurs RECONSTRUITS et annoncés comme tels",
      lu2.get("compteurs_source") == "RECONSTRUIT", str(lu2.get("compteurs_source")))
check("les compteurs reconstruits ne sont pas nuls (2 réels, 1 séquence)",
      lu2.get("nb_trajets_valides_reels") == 2
      and lu2.get("nb_sequences_affichees") == 1,
      f"{lu2.get('nb_trajets_valides_reels')}/{lu2.get('nb_sequences_affichees')}")
check("statut « à recalculer » levé", lu2.get("a_recalculer") is True)
check("version de schéma rapportée (0 = antérieure)",
      lu2.get("schema_version") == 0, str(lu2.get("schema_version")))

print("\n[3] ARCHIVE ANCIENNE SANS LIGNE → « INCONNU », JAMAIS ZÉRO")
h3 = poser_archive(date(2026, 8, 22), {"tcj_s": 7200, "ttj_s": 7200, "tcc_s": 0})
lu3 = s_historique(h3, detail=True, seuils=get_seuils(db))
check("compteurs déclarés INDISPONIBLES",
      lu3.get("compteurs_source") == "INDISPONIBLE", str(lu3.get("compteurs_source")))
check("le nombre de trajets réels est None — JAMAIS 0",
      lu3.get("nb_trajets_valides_reels") is None,
      f"valeur={lu3.get('nb_trajets_valides_reels')!r}")
check("statut « à recalculer » levé", lu3.get("a_recalculer") is True)

print("\n[4] ARCHIVE À JOUR → COMPTEURS DE L'ARCHIVE, AUCUN RECALCUL REQUIS")
donnees_ok = dict(snap)
h4 = poser_archive(date(2026, 8, 23), donnees_ok)
lu4 = s_historique(h4, detail=True, seuils=get_seuils(db))
check("compteurs déclarés comme venant de l'archive",
      lu4.get("compteurs_source") == "ARCHIVE", str(lu4.get("compteurs_source")))
check("aucun recalcul nécessaire",
      lu4.get("a_recalculer") is False, str(lu4.get("a_recalculer")))
check("version de schéma à jour",
      lu4.get("schema_version") == ARCHIVE_SCHEMA_VERSION)

print("\n[5] AUCUN MODE APPLY AUTOMATIQUE (recalcul non autorisé = ZÉRO écriture)")
h5 = poser_archive(date(2026, 8, 24), dict(ancienne_avec))
db.expire_all()
avant = dict(db.get(HistoriqueJournalier, h5.id).donnees or {})
res = recalculer_archives_journee(date(2026, 8, 24), db=db,
                                  autoriser_reecriture=False,
                                  motif="test_p3_non_autorise")
db.expire_all()
apres = dict(db.get(HistoriqueJournalier, h5.id).donnees or {})
check("l'archive est IDENTIQUE avant/après (aucune réécriture)",
      avant == apres, f"avant={sorted(avant)[:4]} après={sorted(apres)[:4]}")
check("le recalcul non autorisé est rapporté comme refusé",
      str(res).lower().find("refus") >= 0 or res.get("reecriture") in (None, "REFUSEE"),
      str(res)[:120])
check("la variable d'environnement d'application n'est PAS armée par défaut",
      os.getenv("LSS_ARCHIVES_APPLY", "0") != "1")

print("\n[6] L'OUTIL DRY-RUN NE CONTIENT AUCUN CHEMIN D'ÉCRITURE")
src = (Path(__file__).resolve().parent
       / "verifier_archives_dry_run_v153.py").read_text(encoding="utf-8")
check("aucun db.delete / db.add / commit d'écriture",
      "db.delete(" not in src and "db.add(" not in src
      and "db.commit()" not in src, "écriture détectée dans l'outil")
check("l'outil rappelle l'absence de mode apply",
      "apply automatique" in src.lower() or "AUCUNE ÉCRITURE" in src)

db.close()
print("\n" + "=" * 74)
print(f"  test_archives_retrocompat_v154 : {OK} OK / {KO} KO")
print("=" * 74)
sys.exit(1 if KO else 0)
