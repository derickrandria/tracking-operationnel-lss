#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test suite pour le module Missions enrichi (v2026.1).

Vérifie :
1. Attribution d'OT (Option A) & Création de Mission (code MIS-OT-xxx, statut camion VIDE)
2. Ingestion GPS & calcul ventilé des distances (km_vide vs km_charge)
3. Détection GRT Tamatave (Entrée -> Sortie -> Passage automatique à CHARGÉ)
4. Règle Dépôts Sud (Tolérance passage BASETNR en restant CHARGÉ)
5. Détection de Déviation (Nouveau dépôt -> statut DÉVIÉE, même Mission_ID conservé)
6. Déchargement & Clôture (Arrêt >= 3h dans le dépôt récepteur -> TERMINÉE, camion LIBRE)
7. Synchronisation inter-onglets (statut opérationnel conducteurs & véhicules)
8. Endpoints API /api/missions (liste, stats, exports Excel/PDF)
"""
import os
import sys
import tempfile
from datetime import date, datetime, time, timedelta

os.environ["TESTING"] = "1"
os.environ["SIM_ENABLE"] = "0"
os.environ["COLLECTOR_SOURCE"] = "AUCUN"
os.environ["MZONEX_API_ENABLE"] = "0"
os.environ["WIALON_ENABLE"] = "0"
os.environ["YMANE_ACTIVE"] = "0"

# Configuration universelle UTF-8 pour Windows PowerShell / Linux
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if "DATABASE_URL" not in os.environ:
    tmp_db = os.path.join(tempfile.gettempdir(), "test_missions_lss.db").replace("\\", "/")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db}"

if "sqlite" not in os.environ.get("DATABASE_URL", ""):
    print("⛔ Sécurité : base de test uniquement (SQLite).")
    sys.exit(2)

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.engine import (appliquer_champs_suivi, ensure_suivi,
                        ingest_event, initialiser_ou_maj_mission)
from app.geozones import detecter_zone_logistique, normaliser_code_depot
from app.main import app
from app.models import (Conducteur, EvenementGPS, Mission, StatutCamion,
                        StatutConducteur, StatutMission,
                        StatutVehicule, SuiviJournalier, TypeEvenement, Vehicule)
from app.seed import seed_si_vide

# Initialisation base de test
engine_test = create_engine(os.environ["DATABASE_URL"])
Base.metadata.drop_all(bind=engine_test)
Base.metadata.create_all(bind=engine_test)
SessionTest = sessionmaker(bind=engine_test)
db = SessionTest()
seed_si_vide()

client = TestClient(app)

# Login admin pour tests API
login_res = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@2026"})
assert login_res.status_code == 200, f"Échec login: {login_res.text}"
token = login_res.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}


def test_missions_complet():
    print("============================================================")
    print("TESTS MISSIONS & CYCLES LOGISTIQUES (v2026.1)")
    print("============================================================")

    aujourd = date.today()
    maintenant = datetime.combine(aujourd, time(6, 0))

    # Réinitialisation des missions pour le test unitaire isolé
    db.query(Mission).delete()
    db.commit()

    # Récupération d'un véhicule et d'un chauffeur
    v = db.scalar(select(Vehicule).where(Vehicule.statut == StatutVehicule.ACTIF))
    c = db.scalar(select(Conducteur).where(Conducteur.statut == StatutConducteur.ACTIF))
    assert v is not None and c is not None

    suivi = ensure_suivi(db, v.id, aujourd)
    suivi.conducteur_id = c.id
    suivi.mission_id = None
    suivi.statut_camion = StatutCamion.LIBRE
    db.commit()

    # -------------------------------------------------------------------------
    # 1. Test Attribution d'OT (Option A) & Création Mission
    # -------------------------------------------------------------------------
    print("\n[T1] Attribution OT (Préparation administrative, heure_debut non déclenchée)")
    m = initialiser_ou_maj_mission(
        db, suivi, v,
        numero_ot="OT-99881",
        distributeur="TOTAL",
        produit="Gasoil",
        depot_prevu="Antsirabe (DABE)",
        ts=maintenant,
    )
    db.commit()

    assert m is not None
    assert m.code_mission == "MIS-OT-99881"
    assert m.statut == StatutMission.EN_COURS
    assert m.statut_camion_actuel == "VIDE"
    assert m.heure_debut is None, "La date_debut ne doit pas être définie administrativement lors de l'attribution OT"
    assert suivi.statut_camion == StatutCamion.VIDE
    assert suivi.mission_id == m.id
    assert suivi.numero_ot == "OT-99881"
    assert len(m.etapes) >= 1
    print("  ✅ Mission créée avec code MIS-OT-99881")
    print("  ✅ Statut camion basculé automatiquement à VIDE")
    print("  ✅ Heure de début de mission reste None (en attente départ physique base)")

    # -------------------------------------------------------------------------
    # 2. Test Départ Physique Base & Déclenchement Réel Heure Début
    # -------------------------------------------------------------------------
    print("\n[T2] Sortie physique Base Tana & Roulage à VIDE vers Tamatave (km_vide)")
    # Simulation départ physique Base Tana -> RN2 vers Tamatave
    t_ev1 = maintenant + timedelta(minutes=30)
    ingest_event(db, v, t_ev1, -18.9100, 47.7500, "RN2 · PK 45 (Sortie Base Tana)", 40.0, "ON", source="SIMULATEUR")
    
    db.refresh(m)
    assert m.heure_debut is not None, "L'heure de début doit être capturée à la sortie physique de la base"
    assert m.heure_debut == t_ev1
    print(f"  ✅ Début réel de mission capturé au franchissement base : {m.heure_debut:%H:%M}")

    t_ev2 = maintenant + timedelta(hours=2)
    ingest_event(db, v, t_ev2, -18.9489, 48.2257, "Moramanga", 45.0, "ON", source="SIMULATEUR")
    
    db.refresh(m)
    assert m.km_vide > 50.0, f"km_vide attendu > 50, obtenu: {m.km_vide}"
    assert m.km_charge == 0.0, f"km_charge attendu 0, obtenu: {m.km_charge}"
    assert m.kilometrage_total == m.km_vide
    print(f"  ✅ Distance à vide cumulée : {m.km_vide:.1f} km (km_charge: {m.km_charge:.1f} km)")

    # -------------------------------------------------------------------------
    # 3. Test Détection GRT Tamatave (Entrée -> Sortie -> Bascule à CHARGÉ)
    # -------------------------------------------------------------------------
    print("\n[T3] Détection Géofence GRT Tamatave (VIDE ➔ CHARGÉ)")
    t_grt_in = maintenant + timedelta(hours=5)
    # Entrée GRT Tamatave
    ingest_event(db, v, t_grt_in, -18.1492, 49.4023, "Galana Rafinérie Terminale (GRT)", 5.0, "ON", source="SIMULATEUR")
    db.refresh(m)
    assert any(e.get("etat") == "ENTREE_GRT" for e in m.etapes)
    print("  ✅ Étape ENTREE_GRT détectée")

    # Sortie de GRT (vitesse > 15 km/h ou hors zone)
    t_grt_out = maintenant + timedelta(hours=6)
    ingest_event(db, v, t_grt_out, -18.1820, 49.2680, "RN2 · Ranomainty", 42.0, "ON", source="SIMULATEUR")
    db.refresh(m)
    db.refresh(suivi)

    assert m.statut_camion_actuel == "CHARGE"
    assert suivi.statut_camion == StatutCamion.CHARGE
    assert m.heure_chargement is not None
    assert any(e.get("etat") == "CHARGEMENT_EFFECTUE" for e in m.etapes)
    print("  ✅ Sortie de GRT validée -> Statut Camion = CHARGÉ")
    print(f"  ✅ Heure de chargement : {m.heure_chargement:%H:%M}")

    # -------------------------------------------------------------------------
    # 4. Test Roulage en CHARGE & Tolérance Transit Sud par BASETNR
    # -------------------------------------------------------------------------
    print("\n[T4] Transit CHARGÉ & Règle Dépôts Sud (Passage Base Tana)")
    t_charge1 = maintenant + timedelta(hours=9)
    # Arrivée Base Tana en transit vers le Sud (Antsirabe)
    ingest_event(db, v, t_charge1, -18.9537, 47.5449, "Base LSS — Antananarivo", 30.0, "ON", source="SIMULATEUR")
    db.refresh(m)
    db.refresh(suivi)

    assert m.statut_camion_actuel == "CHARGE"
    assert suivi.statut_camion == StatutCamion.CHARGE
    assert m.km_charge > 50.0
    print(f"  ✅ Passage BASETNR pour Dépôt Sud toléré en restant CHARGÉ")
    print(f"  ✅ Km chargé accumulé : {m.km_charge:.1f} km (Km total: {m.kilometrage_total:.1f} km)")

    # -------------------------------------------------------------------------
    # 5. Test Détection de Déviation (Nouveau Dépôt)
    # -------------------------------------------------------------------------
    print("\n[T5] Détection de Déviation (Réorientation vers Fianarantsoa au lieu d'Antsirabe)")
    t_dev = maintenant + timedelta(hours=12)
    # Arrivée à Fianarantsoa (DFIA) alors que le dépôt prévu était Antsirabe (DABE)
    ingest_event(db, v, t_dev, -21.4536, 47.0857, "Dépôt DFIA — Fianarantsoa", 10.0, "ON", source="SIMULATEUR")
    db.refresh(m)

    assert m.est_deviee is True
    assert m.statut == StatutMission.DEVIEE
    assert "Fianarantsoa" in (m.depot_effectif or "")
    assert any(e.get("etat") == "DEVIATION_DETECTEE" for e in m.etapes)
    print("  ✅ Statut Mission basculé à DÉVIÉE")
    print(f"  ✅ Nouvelle destination enregistrée : {m.depot_effectif}")

    # -------------------------------------------------------------------------
    # 6. Test Déchargement Validé (Arrêt >= 3h au Dépôt Récepteur -> Alerte & Validation)
    # -------------------------------------------------------------------------
    print("\n[T6] Déchargement au Dépôt Récepteur (Arrêt >= 3h -> Alerte & Validation Clôture LIBRE)")
    # Arrêt dans le dépôt récepteur
    t_arret = maintenant + timedelta(hours=12, minutes=5)
    ingest_event(db, v, t_arret, -21.4536, 47.0857, "Dépôt DFIA — Fianarantsoa", 0.0, "OFF", source="SIMULATEUR")
    
    # Événement 3h15 plus tard (arrêt >= 3h confirmé) -> Alerte de validation générée
    t_fin_decharge = maintenant + timedelta(hours=15, minutes=20)
    ingest_event(db, v, t_fin_decharge, -21.4536, 47.0857, "Dépôt DFIA — Fianarantsoa", 0.0, "OFF", source="SIMULATEUR")

    db.refresh(m)
    assert m.validation_dechargement == "EN_ATTENTE"
    print("  ✅ Alerte VALIDATION_DECHARGEMENT générée après arrêt >= 3h")

    # Validation par l'opérateur
    from app.routers.operations import executer_action_rapide_mission, ActionMissionRapideIn
    res_val = executer_action_rapide_mission(ActionMissionRapideIn(
        action="VALIDER_DECHARGEMENT",
        mission_id=m.id,
        vehicule_id=v.id
    ), db=db, user=None)

    db.refresh(m)
    db.refresh(suivi)

    assert m.statut == StatutMission.TERMINEE, f"Statut attendu TERMINÉE, obtenu: {m.statut}"
    assert m.statut_camion_actuel == "LIBRE"
    assert suivi.statut_camion == StatutCamion.LIBRE
    assert suivi.mission_id is None
    assert m.heure_fin is not None
    assert m.duree_s > 0
    print("  ✅ Déchargement validé par l'opérateur")
    print(f"  ✅ Mission TERMINÉE, camion revenu à LIBRE, durée : {m.duree_s//3600}h{(m.duree_s%3600)//60:02d}")

    # -------------------------------------------------------------------------
    # 6b. Test Verrouillage de Protection Post-Déchargement
    # -------------------------------------------------------------------------
    print("\n[T6b] Verrouillage de Protection (Trame télématique rétrospective GRT)")
    # Envoi d'une trame retardée GRT postérieure à la clôture
    t_retard_grt = maintenant + timedelta(hours=16)
    ingest_event(db, v, t_retard_grt, -18.1492, 49.4023, "GRT Tamatave (trame rétrospective)", 20.0, "ON", source="SIMULATEUR")
    db.refresh(m)
    db.refresh(suivi)
    assert m.statut == StatutMission.TERMINEE, "La mission terminée ne doit pas être rouverte par une trame retardée"
    assert suivi.statut_camion == StatutCamion.LIBRE, "Le statut LIBRE post-déchargement ne doit pas être écrasé"
    assert suivi.mission_id is None
    print("  ✅ Verrouillage confirmé : la mission terminée reste clôturée et le camion reste LIBRE")

    # -------------------------------------------------------------------------
    # 7. Test Endpoints API REST /api/missions & Exports
    # -------------------------------------------------------------------------
    print("\n[T7] Test API REST /api/missions & Exports")
    # Liste des missions
    res = client.get("/api/missions", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["missions"]) >= 1
    assert "stats" in data
    assert data["stats"]["terminees"] >= 1
    print("  ✅ GET /api/missions renvoie 200 OK avec liste et stats")

    # Export Excel
    res_xlsx = client.get("/api/missions/export.xlsx", headers=headers)
    assert res_xlsx.status_code == 200
    assert len(res_xlsx.content) > 1000
    print("  ✅ GET /api/missions/export.xlsx renvoie 200 OK (fichier généré)")

    # Export PDF
    res_pdf = client.get("/api/missions/export.pdf", headers=headers)
    assert res_pdf.status_code == 200
    assert len(res_pdf.content) > 1000
    print("  ✅ GET /api/missions/export.pdf renvoie 200 OK (fichier généré)")

    # -------------------------------------------------------------------------
    # 8. Test Interconnexion Conducteurs & Véhicules
    # -------------------------------------------------------------------------
    print("\n[T8] Synchronisation relationnelle inter-onglets (Conducteurs / Véhicules)")
    res_cond = client.get("/api/conducteurs", headers=headers)
    assert res_cond.status_code == 200
    cond_list = res_cond.json()
    cond_c = next((item for item in cond_list if item["id"] == c.id), None)
    assert cond_c is not None
    assert "statut_operationnel" in cond_c
    print(f"  ✅ Statut opérationnel conducteur : {cond_c['statut_operationnel']}")

    res_veh = client.get("/api/vehicules", headers=headers)
    assert res_veh.status_code == 200
    veh_list = res_veh.json()
    veh_v = next((item for item in veh_list if item["id"] == v.id), None)
    assert veh_v is not None
    assert "statut_operationnel" in veh_v
    print(f"  ✅ Statut opérationnel véhicule : {veh_v['statut_operationnel']}")

    # -------------------------------------------------------------------------
    # 9. Test des 3 colonnes d'horodatages distinctes
    # -------------------------------------------------------------------------
    print("\n[T9] Test des 3 colonnes d'horodatages distinctes (Début, Chargement, Déchargement)")
    m_json = client.get("/api/missions", headers=headers).json()["missions"][0]
    assert "date_debut" in m_json and "heure_debut" in m_json
    assert "date_chargement" in m_json and "heure_chargement" in m_json
    assert "date_fin" in m_json and "heure_fin" in m_json
    assert m_json["date_debut"] is not None
    assert m_json["date_chargement"] is not None
    assert m_json["date_fin"] is not None
    print(f"  ✅ Début Mission : {m_json['date_debut']}")
    print(f"  ✅ Date Chargement : {m_json['date_chargement']}")
    print(f"  ✅ Date Déchargement : {m_json['date_fin']}")

    # -------------------------------------------------------------------------
    # 10. Test Collecte rétrospective & Rattrapage 7 jours
    # -------------------------------------------------------------------------
    print("\n[T10] Test Collecte rétrospective & Rattrapage 7 jours (API + Anti-doublon Upsert)")
    # Appel de l'endpoint de rattrapage
    res_rat = client.post("/api/missions/rattrapage", headers=headers)
    assert res_rat.status_code == 200
    res_rat_data = res_rat.json()
    assert res_rat_data["statut"] == "OK"
    assert "stats" in res_rat_data
    print(f"  ✅ Endpoint /api/missions/rattrapage 200 OK — stats: {res_rat_data['stats']}")

    # Création d'une mission incomplète pour tester l'upsert
    j_hier = aujourd - timedelta(days=1)
    m_incompl = Mission(
        id="test-miss-incompl",
        code_mission="MIS-TEST-INC",
        date_jour=j_hier,
        vehicule_id=v.id,
        conducteur_id=c.id,
        numero_mission_du_jour=2,
        statut=StatutMission.EN_COURS,
        statut_camion_actuel="VIDE",
        heure_debut=datetime.combine(j_hier, time(5, 30)),
        heure_chargement=None,
        heure_fin=None,
        numero_ot="OT-INC-1",
        depot_prevu="DABE"
    )
    db.add(m_incompl)
    db.commit()

    # Création d'événements GPS hier : départ base 05:30, sortie GRT 09:30, déchargement 19:00
    ev_dep_hier = EvenementGPS(
        vehicule_id=v.id,
        horodatage=datetime.combine(j_hier, time(5, 30)),
        latitude=-18.9100,
        longitude=47.7500,
        adresse="RN2 · Sortie Base Tana",
        vitesse=45.0,
        type_evenement=TypeEvenement.DEBUT_MOUVEMENT
    )
    ev_grt_hier = EvenementGPS(
        vehicule_id=v.id,
        horodatage=datetime.combine(j_hier, time(9, 30)),
        latitude=-18.1820,
        longitude=49.2680,
        adresse="RN2 · Ranomainty (Sortie GRT)",
        vitesse=40.0,
        type_evenement=TypeEvenement.POSITION
    )
    ev_dech_hier = EvenementGPS(
        vehicule_id=v.id,
        horodatage=datetime.combine(j_hier, time(19, 0)),
        latitude=-19.8659,
        longitude=47.0333,
        adresse="Dépôt DABE — Antsirabe",
        vitesse=0.0,
        type_evenement=TypeEvenement.ARRET
    )
    db.add_all([ev_dep_hier, ev_grt_hier, ev_dech_hier])
    db.commit()

    # Déclenchement du rattrapage
    from app.engine import rattraper_missions_7j
    stats_rep = rattraper_missions_7j(db, datetime.combine(aujourd, time(12, 0)))
    db.refresh(m_incompl)

    assert m_incompl.heure_chargement is not None, "heure_chargement doit être rattrapée par l'upsert"
    assert m_incompl.heure_fin is not None, "heure_fin doit être rattrapée par l'upsert"
    assert m_incompl.statut == StatutMission.TERMINEE, "statut doit être clôturé à TERMINÉE"
    assert m_incompl.statut_camion_actuel == "LIBRE", "camion doit repasser à LIBRE"
    print("  ✅ Anti-doublon / Upsert confirmé : mission incomplète mise à jour avec les horodatages manquants")

    print("\n============================================================")
    print("TOUS LES TESTS MISSIONS PASSENT AVEC SUCCÈS (10/10)")
    print("============================================================")


if __name__ == "__main__":
    test_missions_complet()
