"""Test unitaire & d'intégration — Détection intelligente des passages temporaires (relais) et arbitrage des chauffeurs."""
import os, sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

os.environ["TESTING"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from datetime import date, datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base, get_db
from backend.app.engine import (
    attribuer_badge_au_jour, ensure_suivi, recalculer_temps,
    _nouveau_trajet, _finaliser_trajet, get_seuils
)
from backend.app.main import app
from backend.app.models import (
    Conducteur, Vehicule, SuiviJournalier, Trajet, Alerte,
    StatutVehicule, StatutValidationTrajet, StatutSourceTrajet,
    SourceEvenement, TypeAlerte, StatutAlerte, Role, User, uid
)
from backend.app.security import hash_password, create_token

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def test_arbitrage_conducteur_complet():
    db = TestingSessionLocal()
    aujourd = date.today()
    maintenant = datetime.combine(aujourd, datetime.min.time()).replace(hour=14, minute=0)

    # 1. Créer User Admin + Chauffeurs + Véhicule
    u_admin = User(id=uid(), username="admin_arbitrage", password_hash=hash_password("Pass123!"),
                   role=Role.ADMIN, actif=True, nom_complet="Admin")
    db.add(u_admin)

    c_titulaire = Conducteur(id=uid(), nom_prenom="TOLOFANAHARY Mitantsoa", prenom_usuel="Mitantsoa",
                             code_badge_mzonex=3924, statut="ACTIF")
    c_relais = Conducteur(id=uid(), nom_prenom="TODIVELO Eric", prenom_usuel="Eric",
                          code_badge_mzonex=3950, statut="ACTIF")
    db.add_all([c_titulaire, c_relais])
    db.commit()

    v = Vehicule(id=uid(), plaque="0576TCD", statut=StatutVehicule.ACTIF,
                 conducteur_actuel_id=c_titulaire.id)
    db.add(v)
    db.commit()

    token = create_token(u_admin)
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Initialiser Suivi pour 0576TCD (avec titulaire Mitantsoa)
    suivi = ensure_suivi(db, v, aujourd)
    suivi.conducteur_id = c_titulaire.id
    suivi.conducteur_origine = "TITULAIRE"

    # Trajet T1 par Mitantsoa (09:19 -> 13:39, 101.9 km)
    t1_deb = datetime.combine(aujourd, datetime.min.time()).replace(hour=9, minute=19)
    t1_fin = datetime.combine(aujourd, datetime.min.time()).replace(hour=13, minute=39)
    t1 = Trajet(
        id=uid(), suivi_id=suivi.id, numero=1, heure_debut=t1_deb, heure_fin=t1_fin,
        distance_km=101.9, statut_validation=StatutValidationTrajet.VALIDE,
        statut_source=StatutSourceTrajet.VALIDE, conducteur_badge=c_titulaire.nom_prenom,
        conducteur_badge_id=c_titulaire.id
    )

    # Trajet T2 badgé par Eric (14:16 -> 15:30, 39.3 km)
    t2_deb = datetime.combine(aujourd, datetime.min.time()).replace(hour=14, minute=16)
    t2_fin = datetime.combine(aujourd, datetime.min.time()).replace(hour=15, minute=30)
    t2 = Trajet(
        id=uid(), suivi_id=suivi.id, numero=2, heure_debut=t2_deb, heure_fin=t2_fin,
        distance_km=39.3, statut_validation=StatutValidationTrajet.VALIDE,
        statut_source=StatutSourceTrajet.VALIDE, conducteur_badge=c_relais.nom_prenom,
        conducteur_badge_id=c_relais.id
    )

    db.add_all([t1, t2])
    db.commit()

    # 3. Tester la détection de passage temporaire
    attribuer_badge_au_jour(db, suivi, c_relais, username="test")
    db.commit()

    # Vérification 1 : Le titulaire reste sur la ligne de suivi et le flag passe en RELAIS
    db.refresh(suivi)
    assert suivi.conducteur_id == c_titulaire.id, "Le titulaire doit être conservé sur le camion lors d'un relais"
    assert suivi.conducteur_origine == "RELAIS", "Le statut d'origine doit indiquer RELAIS"

    # Vérification 2 : Une alerte CHANGEMENT_CONDUCTEUR_DETECTE a été levée
    alt = db.query(Alerte).filter(
        Alerte.vehicule_id == v.id,
        Alerte.type == TypeAlerte.CHANGEMENT_CONDUCTEUR_DETECTE
    ).first()
    assert alt is not None, "Une alerte CHANGEMENT_CONDUCTEUR_DETECTE doit être émise"
    assert alt.statut == StatutAlerte.NOUVELLE

    print("  ✅ Détection intelligente de passage temporaire validée (Titulaire conservé, flag RELAIS posé)")

    # 4. Tester l'arbitrage : Option A (PASSAGE_TEMPORAIRE)
    res_a = client.post("/api/suivi/arbitrer-conducteur", headers=headers, json={
        "suivi_id": suivi.id,
        "choix": "PASSAGE_TEMPORAIRE",
        "conducteur_id": c_relais.id,
        "alerte_id": alt.id
    })
    assert res_a.status_code == 200, f"Erreur API arbitrage A: {res_a.text}"
    db.refresh(suivi)
    db.refresh(alt)
    assert suivi.conducteur_id == c_titulaire.id
    assert suivi.conducteur_origine == "RELAIS"
    assert alt.statut == StatutAlerte.TRAITEE, "L'alerte doit être clôturée après arbitrage"
    print("  ✅ Option A (Passage temporaire / Relais) confirmée et alerte clôturée")

    # 5. Tester l'arbitrage : Option B (REMPLACEMENT_JOURNEE)
    res_b = client.post("/api/suivi/arbitrer-conducteur", headers=headers, json={
        "suivi_id": suivi.id,
        "choix": "REMPLACEMENT_JOURNEE",
        "conducteur_id": c_relais.id
    })
    assert res_b.status_code == 200, f"Erreur API arbitrage B: {res_b.text}"
    db.refresh(suivi)
    db.refresh(t1)
    db.refresh(t2)
    assert suivi.conducteur_id == c_relais.id
    assert suivi.conducteur_origine == "MANUEL"
    assert t1.conducteur_badge_id == c_relais.id, "Tous les trajets doivent être réaffectés au nouveau conducteur"
    assert t2.conducteur_badge_id == c_relais.id
    print("  ✅ Option B (Remplacement journée) confirmée et trajets réassignés")

    # 6. Tester l'arbitrage : Option C (MAINTENIR_TITULAIRE)
    res_c = client.post("/api/suivi/arbitrer-conducteur", headers=headers, json={
        "suivi_id": suivi.id,
        "choix": "MAINTENIR_TITULAIRE"
    })
    assert res_c.status_code == 200, f"Erreur API arbitrage C: {res_c.text}"
    db.refresh(suivi)
    db.refresh(t1)
    db.refresh(t2)
    assert suivi.conducteur_id == c_titulaire.id
    assert suivi.conducteur_origine == "MANUEL"
    assert t1.conducteur_badge_id == c_titulaire.id
    assert t2.conducteur_badge_id == c_titulaire.id
    print("  ✅ Option C (Maintenir titulaire 100%) confirmée")

    db.close()


if __name__ == "__main__":
    test_arbitrage_conducteur_complet()
    print("\n🎉 TOUS LES TESTS D'ARBITRAGE ET RELAIS CONDUCTEURS SONT VALIDÉS AVEC SUCCÈS !")
