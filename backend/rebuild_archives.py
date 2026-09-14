#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Script CLI dédié — Reconstruction et re-calcul des archives journalières (CamTrackPro & MZoneX).
Exécute la purge, l'intégration Wialon et la consolidation complète des archives sans bloquer le serveur web.
Usage :
  python backend/rebuild_archives.py [YYYY-MM-DD ...]
"""
import sys
import os
import site
from datetime import date

# Configuration universelle des chemins de dépendances
pypaths = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")),
    os.path.abspath(os.path.dirname(__file__)),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pylib")),
    site.getusersitepackages(),
    "/tmp/pylib"
]
if hasattr(site, "getsitepackages"):
    pypaths.extend(site.getsitepackages())
for p in pypaths:
    if p and p not in sys.path:
        sys.path.insert(0, p)

from app.database import SessionLocal, engine as db_engine
from app.daily import recalculer_archives_journee
from app.seed import seed_si_vide
from app.main import migrer_schema

def main():
    dates_cibles = []
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            try:
                dates_cibles.append(date.fromisoformat(arg))
            except ValueError:
                print(f"Format de date invalide : {arg} (attendu: YYYY-MM-DD)")
                sys.exit(1)
    else:
        dates_cibles = [date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13), date(2026, 9, 14)]

    print("=" * 70)
    print("  LSS TRACKING — RECONSTRUCTION ET CONSOLIDATION DES ARCHIVES")
    print(f"  Base de données : {db_engine.url}")
    print(f"  Dates cibles : {[d.isoformat() for d in dates_cibles]}")
    print("=" * 70)

    seed_si_vide()
    migrer_schema()

    db = SessionLocal()
    try:
        for d in dates_cibles:
            print(f"\n[+] Re-moulinage et consolidation pour le {d.isoformat()}...")
            res = recalculer_archives_journee(d, db=db)
            print(f"    -> Statut: {res.get('statut')} | Archives réinsérées: {res.get('archives_mises_a_jour')} | Trajets consolidés: {res.get('trajets_consolides')}")
        db.commit()
        print("\n✅ Reconstruction des archives terminée avec succès !")
    except Exception as e:
        db.rollback()
        print(f"\n❌ Erreur lors de la reconstruction des archives : {e}")
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    main()
