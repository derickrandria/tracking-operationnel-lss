#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test d'intégrité de l'Historique BDD et API (Plafond 24h, calculs TCJ/TTJ et non-régression).

Vérifie :
1. L'isolation stricte de la fenêtre journalière [00:00:00 -> 23:59:59] sur SQLite ;
2. Aucun TCJ / TTJ / Pause ne dépasse 24:00 (86400 s) sur les archives 11, 12, 13/09/2026 ;
3. Cohérence absolue : TTJ = TCJ + Total Pauses sur 100% des véhicules archivés ;
4. Valeurs certifiées CamTrackPro pour 0826TBS et 5646TCE au 13/09/2026 ;
5. Rendu exact et absence d'anomalie via l'API REST /api/historique/suivi.
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

os.environ["TESTING"] = "1"
os.environ["SIM_ENABLE"] = "0"
os.environ["COLLECTOR_SOURCE"] = "AUCUN"
os.environ["MZONEX_API_ENABLE"] = "0"
os.environ["WIALON_ENABLE"] = "0"
os.environ["YMANE_ACTIVE"] = "0"

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if "DATABASE_URL" not in os.environ:
    tmp_db = os.path.join(tempfile.gettempdir(), "test_hist_integrite.db").replace("\\", "/")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db}"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, SessionLocal, engine as db_engine
from app.seed import seed_si_vide
from app.main import app, migrer_schema
from app.daily import recalculer_archives_journee
from app.models import HistoriqueJournalier, User, Role, Vehicule
from app.security import hash_password


def setup():
    Base.metadata.create_all(bind=db_engine)
    seed_si_vide()
    migrer_schema()
    db = SessionLocal()
    try:
        if not db.scalar(select(User).where(User.username == "admin_test")):
            db.add(User(username="admin_test", password_hash=hash_password("Pass@123"),
                        nom_complet="Admin Test", role=Role.ADMIN))
        for d in [date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)]:
            recalculer_archives_journee(d, db=db)
        db.commit()
    finally:
        db.close()


def test_recalcul_et_coherence_bdd():
    print("\n[T1] Test de recalcul et cohérence stricte BDD SQLite (11/09, 12/09, 13/09/2026)...")
    db = SessionLocal()
    try:
        for d_str in ["2026-09-11", "2026-09-12", "2026-09-13"]:
            d = date.fromisoformat(d_str)
            res = recalculer_archives_journee(d, db=db)
            assert res["statut"] == "OK", f"Échec recalcul pour {d_str}: {res}"

        # Contrôle exhaustif de TOUTES les archives en base
        toutes_archives = db.scalars(select(HistoriqueJournalier).where(
            HistoriqueJournalier.date_jour.in_([date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)])
        )).all()

        assert len(toutes_archives) >= 50, f"Nombre insuffisant d'archives: {len(toutes_archives)}"
        print(f"  Contrôle de {len(toutes_archives)} archives journalières...")

        for h in toutes_archives:
            d = h.donnees or {}
            tcj_s = int(d.get("tcj_s") or 0)
            ttj_s = int(d.get("ttj_s") or 0)
            pause_s = int(d.get("total_pause_s") or 0)
            tcj_str = d.get("tcj_str") or "00:00"
            ttj_str = d.get("ttj_str") or "00:00"

            # 1. Aucun compteur ne dépasse 24h00 (86400s)
            assert tcj_s <= 86400, f"ANOMALIE: TCJ > 24h sur {h.date_jour} (vehicule {h.vehicule_id}): {tcj_s}s ({tcj_str})"
            assert ttj_s <= 86400, f"ANOMALIE: TTJ > 24h sur {h.date_jour} (vehicule {h.vehicule_id}): {ttj_s}s ({ttj_str})"
            assert pause_s <= 86400, f"ANOMALIE: Pause > 24h sur {h.date_jour}: {pause_s}s"

            # 2. TCJ <= TTJ et TTJ == TCJ + Pause
            assert tcj_s <= ttj_s, f"ANOMALIE: TCJ ({tcj_s}s) > TTJ ({ttj_s}s) sur {h.date_jour}"
            assert ttj_s == (tcj_s + pause_s), f"ANOMALIE: TTJ ({ttj_s}s) != TCJ ({tcj_s}s) + Pause ({pause_s}s) sur {h.date_jour}"

            # 3. Format des chaînes d'affichage
            h_tcj, m_tcj = map(int, tcj_str.split(":"))
            assert h_tcj < 24 or (h_tcj == 24 and m_tcj == 0), f"Format TCJ_str invalide: {tcj_str}"
            h_ttj, m_ttj = map(int, ttj_str.split(":"))
            assert h_ttj < 24 or (h_ttj == 24 and m_ttj == 0), f"Format TTJ_str invalide: {ttj_str}"

        print("  ✅ 100% des archives SQLite respectent strictement la borne 24h00 et la relation TTJ = TCJ + Pauses")
    finally:
        db.close()


def test_valeurs_specifiques_13_09_2026():
    print("\n[T2] Test des valeurs réelles 13/09/2026 (0826TBS à ~01:29 et 5646TCE à ~02:16)...")
    db = SessionLocal()
    try:
        h_0826 = db.scalar(select(HistoriqueJournalier).join(Vehicule).where(
            HistoriqueJournalier.date_jour == date(2026, 9, 13),
            Vehicule.plaque == "0826TBS"
        ))
        assert h_0826 is not None, "Archive 0826TBS manquante au 13/09/2026"
        d_0826 = h_0826.donnees or {}
        print(f"  0826TBS (13/09) -> TCJ: {d_0826.get('tcj_str')} ({d_0826.get('tcj_s')}s), TTJ: {d_0826.get('ttj_str')} ({d_0826.get('ttj_s')}s), Km: {d_0826.get('km_parcourus')}")
        assert d_0826.get("tcj_str") == "01:29", f"TCJ 0826TBS erroné: {d_0826.get('tcj_str')}"
        assert d_0826.get("ttj_str") == "01:29", f"TTJ 0826TBS erroné: {d_0826.get('ttj_str')}"
        assert d_0826.get("km_parcourus") == 65.4, f"Km 0826TBS erroné: {d_0826.get('km_parcourus')}"

        h_5646 = db.scalar(select(HistoriqueJournalier).join(Vehicule).where(
            HistoriqueJournalier.date_jour == date(2026, 9, 13),
            Vehicule.plaque == "5646TCE"
        ))
        assert h_5646 is not None, "Archive 5646TCE manquante au 13/09/2026"
        d_5646 = h_5646.donnees or {}
        print(f"  5646TCE (13/09) -> TCJ: {d_5646.get('tcj_str')} ({d_5646.get('tcj_s')}s), TTJ: {d_5646.get('ttj_str')} ({d_5646.get('ttj_s')}s), Km: {d_5646.get('km_parcourus')}")
        assert d_5646.get("tcj_str") == "02:16", f"TCJ 5646TCE erroné: {d_5646.get('tcj_str')}"
        assert d_5646.get("ttj_str") == "02:16", f"TTJ 5646TCE erroné: {d_5646.get('ttj_str')}"
        assert d_5646.get("km_parcourus") == 136.0, f"Km 5646TCE erroné: {d_5646.get('km_parcourus')}"

        # Vérification également sur le 11/09/2026
        h_0826_11 = db.scalar(select(HistoriqueJournalier).join(Vehicule).where(
            HistoriqueJournalier.date_jour == date(2026, 9, 11),
            Vehicule.plaque == "0826TBS"
        ))
        assert h_0826_11 is not None, "Archive 0826TBS manquante au 11/09/2026"
        d_0826_11 = h_0826_11.donnees or {}
        print(f"  0826TBS (11/09) -> TCJ: {d_0826_11.get('tcj_str')} ({d_0826_11.get('tcj_s')}s), TTJ: {d_0826_11.get('ttj_str')} ({d_0826_11.get('ttj_s')}s), Km: {d_0826_11.get('km_parcourus')}")
        assert d_0826_11.get("tcj_str") == "07:43", f"TCJ 0826TBS (11/09) erroné: {d_0826_11.get('tcj_str')}"
        assert d_0826_11.get("ttj_str") == "08:28", f"TTJ 0826TBS (11/09) erroné: {d_0826_11.get('ttj_str')}"
        assert d_0826_11.get("km_parcourus") == 283.6, f"Km 0826TBS (11/09) erroné: {d_0826_11.get('km_parcourus')}"

        print("  ✅ Valeurs réelles CamTrackPro validées avec succès sur 11/09 et 13/09")
    finally:
        db.close()


def test_api_historique_suivi():
    print("\n[T3] Test de l'endpoint API REST /api/historique/suivi...")
    client = TestClient(app)
    r_login = client.post("/api/auth/login", json={"username": "admin_test", "password": "Pass@123"})
    assert r_login.status_code == 200
    token = r_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r_hist = client.get("/api/historique/suivi?du=2026-09-11&au=2026-09-13", headers=headers)
    assert r_hist.status_code == 200
    res = r_hist.json()
    items = res.get("items", [])
    assert len(items) > 0, "Aucun élément retourné par l'API"

    for it in items:
        tcj_s = it.get("tcj_s") or 0
        ttj_s = it.get("ttj_s") or 0
        tcj_str = it.get("tcj_str") or "00:00"
        ttj_str = it.get("ttj_str") or "00:00"
        assert tcj_s <= 86400, f"API renvoie TCJ > 24h: {tcj_s}s ({tcj_str})"
        assert ttj_s <= 86400, f"API renvoie TTJ > 24h: {ttj_s}s ({ttj_str})"
        assert int(tcj_str.split(":")[0]) <= 24, f"API tcj_str > 24h: {tcj_str}"
        assert int(ttj_str.split(":")[0]) <= 24, f"API ttj_str > 24h: {ttj_str}"

    print(f"  ✅ API /api/historique/suivi vérifiée sur {len(items)} items — aucune valeur aberrante")


if __name__ == "__main__":
    setup()
    test_recalcul_et_coherence_bdd()
    test_valeurs_specifiques_13_09_2026()
    test_api_historique_suivi()
    print("\n🎉 VALIDATION INTÉGRITÉ HISTORIQUE BDD & API : 100% SUCCÈS !\n")
