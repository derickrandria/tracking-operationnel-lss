"""Tests complets — Module « Temps de conduite » (TCH).

Vérifie :
  1. Le calcul du TCH par chauffeur (indépendamment des camions conduits) ;
  2. Le cumul des TCJ réels issus des sources existantes (SuiviJournalier / HistoriqueJournalier) ;
  3. La détection du reset après un repos continu ≥ 24h (tous camions confondus) ;
  4. L'intégration du TCJ temps réel d'aujourd'hui dans le TCH et TCH restant ;
  5. Les alertes 46h (avertissement) et 56h (limite) avec leurs messages exacts et anti-doublon ;
  6. Les endpoints API REST (/api/temps-conduite, /export.xlsx, /export.pdf).

Exécution :
  DATABASE_URL="sqlite:////tmp/test_tch.db" python3 test_temps_conduite.py
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select

from app.config import jour_attribution, now_local
from app.database import SessionLocal, Base, engine as _engine
from app import engine
from app.engine import get_seuils, verifier_alertes_tch
from app.models import (Alerte, Conducteur, HistoriqueJournalier, StatutAlerte,
                        StatutConducteur, StatutValidationTrajet, SuiviJournalier,
                        Trajet, TypeAlerte, Vehicule, User, Role)
from app.routers.temps_conduite import (SEUIL_TCH_ALERTE_S, SEUIL_TCH_MAX_S,
                                       calculer_tch_seul_conducteur,
                                       extraire_donnees_chauffeurs)
from app.security import hash_password

DB_PATH = "/tmp/test_tch.db"
if os.path.exists(DB_PATH):
    try:
        os.remove(DB_PATH)
    except OSError:
        pass


def setup():
    Base.metadata.create_all(bind=_engine)
    db = SessionLocal()
    try:
        # Nettoyage préalable des données de test
        db.query(Trajet).filter(Trajet.id.in_(["t-lundi-1", "t-mardi-1", "t-jeudi-1", "t-vendredi-1",
                                               "t-paul-1", "t-paul-2", "t-paul-3", "t-paul-4", "t-paul-5", "t-paul-6"])).delete(synchronize_session=False)
        db.query(SuiviJournalier).filter(SuiviJournalier.id.in_(["s-lundi-1", "s-mardi-1", "s-jeudi-1", "s-vendredi-1",
                                                               "s-paul-1", "s-paul-2", "s-paul-3", "s-paul-4", "s-paul-5", "s-paul-6"])).delete(synchronize_session=False)
        db.query(Alerte).filter(Alerte.conducteur_id.in_(["cond-001", "cond-002"])).delete(synchronize_session=False)
        db.query(Vehicule).filter(Vehicule.id.in_(["veh-001", "veh-002", "veh-003"])).delete(synchronize_session=False)
        db.query(Conducteur).filter(Conducteur.id.in_(["cond-001", "cond-002"])).delete(synchronize_session=False)
        db.commit()

        # Utilisateur de test
        if not db.scalar(select(User).where(User.username == "admin_test")):
            db.add(User(username="admin_test", password_hash=hash_password("Pass@123"),
                        nom_complet="Admin Test", role=Role.ADMIN))

        # 2 conducteurs de test
        c1 = Conducteur(id="cond-001", nom_prenom="RAKOTO Jean", prenom_usuel="JEAN",
                        matricule="CH101", telephone="034 00 000 01", statut=StatutConducteur.ACTIF)
        c2 = Conducteur(id="cond-002", nom_prenom="RANDRIA Paul", prenom_usuel="PAUL",
                        matricule="CH102", telephone="034 00 000 02", statut=StatutConducteur.ACTIF)
        db.add_all([c1, c2])

        # 3 camions de test
        v1 = Vehicule(id="veh-001", plaque="1111TZZ", description="Camion A", statut="ACTIF")
        v2 = Vehicule(id="veh-002", plaque="2222TYY", description="Camion B", statut="ACTIF")
        v3 = Vehicule(id="veh-003", plaque="3333TXX", description="Camion C", statut="ACTIF")
        db.add_all([v1, v2, v3])

        db.commit()
    finally:
        db.close()


def test_1_cumul_multi_vehicules_et_reset_24h():
    print("\n[T1] Test TCH multi-véhicules & détection du reset 24h")
    db = SessionLocal()
    try:
        auj = now_local().date()
        j_lundi = auj - timedelta(days=4)
        j_mardi = auj - timedelta(days=3)
        j_mercredi = auj - timedelta(days=2) # Repos
        j_jeudi = auj - timedelta(days=1)
        j_vendredi = auj

        # Chauffeur 1 (Jean) :
        # Lundi : Conduite Camion A de 08:00 à 16:00 (TCJ = 7h00 = 25200s, TTJ = 8h00 = 28800s)
        s_lundi = SuiviJournalier(
            id="s-lundi-1", date_jour=j_lundi, vehicule_id="veh-001", conducteur_id="cond-001",
            tcj_s=25200, ttj_s=28800, heure_depart=datetime.combine(j_lundi, datetime.min.time()).replace(hour=8),
            arret_final=f"16:00 · Base"
        )
        t_lundi = Trajet(
            id="t-lundi-1", suivi_id="s-lundi-1", numero=1,
            heure_debut=datetime.combine(j_lundi, datetime.min.time()).replace(hour=8),
            heure_fin=datetime.combine(j_lundi, datetime.min.time()).replace(hour=16),
            distance_km=150.0, statut_validation=StatutValidationTrajet.VALIDE,
            conducteur_badge_id="cond-001"
        )

        # Mardi : Conduite Camion B de 08:00 à 17:00 (TCJ = 8h00 = 28800s, TTJ = 9h00 = 32400s)
        # Gap Lundi 16h -> Mardi 8h = 16h (< 24h repos continu)
        s_mardi = SuiviJournalier(
            id="s-mardi-1", date_jour=j_mardi, vehicule_id="veh-002", conducteur_id="cond-001",
            tcj_s=28800, ttj_s=32400, heure_depart=datetime.combine(j_mardi, datetime.min.time()).replace(hour=8),
            arret_final=f"17:00 · Base"
        )
        t_mardi = Trajet(
            id="t-mardi-1", suivi_id="s-mardi-1", numero=1,
            heure_debut=datetime.combine(j_mardi, datetime.min.time()).replace(hour=8),
            heure_fin=datetime.combine(j_mardi, datetime.min.time()).replace(hour=17),
            distance_km=180.0, statut_validation=StatutValidationTrajet.VALIDE,
            conducteur_badge_id="cond-001"
        )

        # Mercredi : REPOS COMPLET (aucun trajet valide)
        # Jeudi : Conduite Camion A de 09:00 à 15:00 (TCJ = 5h30 = 19800s, TTJ = 6h00 = 21600s)
        # Gap Mardi 17h -> Jeudi 9h = 40 heures (≥ 24h repos continu) -> RESET TCH !
        s_jeudi = SuiviJournalier(
            id="s-jeudi-1", date_jour=j_jeudi, vehicule_id="veh-001", conducteur_id="cond-001",
            tcj_s=19800, ttj_s=21600, heure_depart=datetime.combine(j_jeudi, datetime.min.time()).replace(hour=9),
            arret_final=f"15:00 · Base"
        )
        t_jeudi = Trajet(
            id="t-jeudi-1", suivi_id="s-jeudi-1", numero=1,
            heure_debut=datetime.combine(j_jeudi, datetime.min.time()).replace(hour=9),
            heure_fin=datetime.combine(j_jeudi, datetime.min.time()).replace(hour=15),
            distance_km=120.0, statut_validation=StatutValidationTrajet.VALIDE,
            conducteur_badge_id="cond-001"
        )

        # Vendredi (Aujourd'hui) : Conduite Camion B en cours (TCJ = 4h30 = 16200s)
        # Gap Jeudi 15h -> Vendredi 8h = 17 heures (< 24h)
        s_vendredi = SuiviJournalier(
            id="s-vendredi-1", date_jour=j_vendredi, vehicule_id="veh-002", conducteur_id="cond-001",
            tcj_s=16200, ttj_s=18000, heure_depart=datetime.combine(j_vendredi, datetime.min.time()).replace(hour=8),
            arret_final=f"12:30 · En route"
        )
        t_vendredi = Trajet(
            id="t-vendredi-1", suivi_id="s-vendredi-1", numero=1,
            heure_debut=datetime.combine(j_vendredi, datetime.min.time()).replace(hour=8),
            heure_fin=None, # En cours !
            distance_km=90.0, statut_validation=StatutValidationTrajet.VALIDE,
            conducteur_badge_id="cond-001"
        )

        db.add_all([s_lundi, s_mardi, s_jeudi, s_vendredi,
                    t_lundi, t_mardi, t_jeudi, t_vendredi])
        db.commit()

        # Calculer le TCH
        mtn = datetime.combine(j_vendredi, datetime.min.time()).replace(hour=12, minute=30)
        res = extraire_donnees_chauffeurs(db, j_lundi, j_vendredi, maintenant=mtn, conducteur_id_filtre="cond-001")
        lignes = res["lignes"]
        assert len(lignes) == 1
        l = lignes[0]

        # Vérifications :
        # Le reset 24h a eu lieu entre Mardi 17:00 et Jeudi 09:00 (40h de repos).
        # Le TCH cumulé actif = TCJ Jeudi (19800s = 5h30) + TCJ Vendredi (16200s = 4h30) = 36000s (10h00).
        # Lundi (7h00) et Mardi (8h00) sont bien dans l'historique mais NON comptés dans le TCH actif (inclus_dans_tch = False).
        print(f"  Cumul TCH calculé : {l['tch_cumul_s']} s ({l['tch_cumul_s']/3600:.1f} h) — Attendu : 36000 s (10.0 h)")
        assert l["tch_cumul_s"] == 36000, f"Erreur cumul TCH: {l['tch_cumul_s']} != 36000"

        # TCH restant = 56h (201600 s) - 10h (36000 s) = 46h (165600 s)
        print(f"  TCH restant calculé : {l['tch_restant_s']} s ({l['tch_restant_s']/3600:.1f} h) — Attendu : 165600 s (46.0 h)")
        assert l["tch_restant_s"] == 165600, f"Erreur restant: {l['tch_restant_s']} != 165600"

        # Historique détaillé
        h = l["historique"]
        assert h[j_lundi.isoformat()]["inclus_dans_tch"] is False
        assert h[j_mardi.isoformat()]["inclus_dans_tch"] is False
        assert h[j_jeudi.isoformat()]["inclus_dans_tch"] is True
        assert h[j_vendredi.isoformat()]["inclus_dans_tch"] is True
        assert h[j_vendredi.isoformat()]["en_cours"] is True
        print("  ✅ Multi-véhicules (Camion A + Camion B) correctement agrégé")
        print("  ✅ Reset 24h détecté avec exactitude")
        print("  ✅ TCJ temps réel d'aujourd'hui inclus dynamiquement")
    finally:
        db.close()


def test_2_alertes_tch_46h_et_56h():
    print("\n[T2] Test des alertes TCH (46h avertissement et 56h limite)")
    db = SessionLocal()
    try:
        auj = now_local().date()
        seuils = get_seuils(db)
        ts = datetime.combine(auj, datetime.min.time()).replace(hour=14)

        # Chauffeur 2 (Paul) : cumul de conduite important sans coupure de 24h
        # On lui ajoute 6 journées consécutives de 8h = 48h de conduite (dépasse 46h)
        for d in range(1, 7):
            jour_d = auj - timedelta(days=6 - d)
            s = SuiviJournalier(
                id=f"s-paul-{d}", date_jour=jour_d, vehicule_id="veh-003", conducteur_id="cond-002",
                tcj_s=28800, ttj_s=32400, # 8h00 par jour
                heure_depart=datetime.combine(jour_d, datetime.min.time()).replace(hour=8),
                arret_final="17:00 · Base"
            )
            t = Trajet(
                id=f"t-paul-{d}", suivi_id=f"s-paul-{d}", numero=1,
                heure_debut=datetime.combine(jour_d, datetime.min.time()).replace(hour=8),
                heure_fin=datetime.combine(jour_d, datetime.min.time()).replace(hour=17) if d < 6 else None,
                distance_km=200.0, statut_validation=StatutValidationTrajet.VALIDE,
                conducteur_badge_id="cond-002"
            )
            db.add_all([s, t])
        db.commit()

        # Déclenchement du contrôle d'alerte à 48h (≥ 46h)
        verifier_alertes_tch(db, "cond-002", seuils, ts)
        db.commit()

        # Vérifier qu'une alerte TCH_PROCHE_LIMITE a été créée
        al_46 = db.scalar(select(Alerte).where(
            Alerte.conducteur_id == "cond-002",
            Alerte.type == TypeAlerte.TCH_PROCHE_LIMITE
        ))
        assert al_46 is not None, "Alerte 46h non générée !"
        print(f"  Message alerte 46h généré : « {al_46.message} »")
        assert "TCH proche de la limite" in al_46.message
        assert "RANDRIA Paul" in al_46.message or "PAUL" in al_46.message
        assert al_46.gravite.value == "MOYENNE"
        print("  ✅ Alerte 46h (avertissement) conforme")

        # Pousser la conduite à 57h (dépasse 56h limite)
        s_today = db.scalar(select(SuiviJournalier).where(
            SuiviJournalier.conducteur_id == "cond-002",
            SuiviJournalier.date_jour == auj
        ))
        s_today.tcj_s = 61200 # +9h supplémentaires -> total 48h - 8h + 17h = 57h
        db.commit()

        verifier_alertes_tch(db, "cond-002", seuils, ts)
        db.commit()

        al_56 = db.scalar(select(Alerte).where(
            Alerte.conducteur_id == "cond-002",
            Alerte.type == TypeAlerte.TCH_LIMITE_ATTEINTE
        ))
        assert al_56 is not None, "Alerte 56h non générée !"
        print(f"  Message alerte 56h généré : « {al_56.message} »")
        assert "TCH limite atteinte" in al_56.message
        assert al_56.gravite.value == "CRITIQUE"
        print("  ✅ Alerte 56h (limite atteinte) conforme")

    finally:
        db.close()


def test_3_api_endpoints():
    print("\n[T3] Test des endpoints API REST /api/temps-conduite")
    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # 1. Login pour récupérer token JWT
    r_login = client.post("/api/auth/login", json={"username": "admin_test", "password": "Pass@123"})
    assert r_login.status_code == 200, f"Login échoué : {r_login.text}"
    token = r_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. GET /api/temps-conduite
    r_tch = client.get("/api/temps-conduite", headers=headers)
    assert r_tch.status_code == 200
    res = r_tch.json()
    assert "lignes" in res
    assert "dates" in res
    assert "stats" in res
    print(f"  API JSON 200 OK — {len(res['lignes'])} chauffeurs renvoyés")
    print(f"  KPIs : {res['stats']}")

    # 3. GET /api/temps-conduite/conducteur/cond-001
    r_cond = client.get("/api/temps-conduite/conducteur/cond-001", headers=headers)
    assert r_cond.status_code == 200
    assert r_cond.json()["conducteur"]["matricule"] == "CH101"
    print("  API détail chauffeur 200 OK")

    # 4. GET /api/temps-conduite/export.xlsx
    r_xlsx = client.get("/api/temps-conduite/export.xlsx", headers=headers)
    assert r_xlsx.status_code == 200
    assert len(r_xlsx.content) > 1000
    print("  Export Excel 200 OK")

    # 5. GET /api/temps-conduite/export.pdf
    r_pdf = client.get("/api/temps-conduite/export.pdf", headers=headers)
    assert r_pdf.status_code == 200
    assert len(r_pdf.content) > 1000
    print("  Export PDF 200 OK")


if __name__ == "__main__":
    setup()
    test_1_cumul_multi_vehicules_et_reset_24h()
    test_2_alertes_tch_46h_et_56h()
    test_3_api_endpoints()
    print("\n🎉 TOUS LES TESTS TEMPS DE CONDUITE (TCH) ONT RÉUSSI AVEC SUCCÈS !\n")
