"""
Test Suite — Conformité Intégrale au RÈGLEMENT MÉTIER MODULE MISSIONS (Articles 1 à 45).
Vérifie :
1. Extraction exacte des noms de dépôts portail (§5, §6)
2. Durée de présence strictement > 3h et sortie physique pour dépotage standard (§15, §16, §38)
3. Séquence rétrospective DMMG (Moramanga -> Andasibe -> Ambatosonegaly) (§20-23)
4. Invalidation DMMG (Moramanga -> Andriaka -> Tana) (§24, §25)
5. Nettoyage SuiviJournalier lors du passage à LIBRE (§29)
6. Protection de l'état TERMINÉE (§36)

Exécution — AUCUNE configuration manuelle, AUCUN chemin absolu :
  python backend/test_reglement_metier_missions.py                (depuis la racine du dépôt)
  python -m unittest backend.test_reglement_metier_missions -v    (découverte unittest)
  python -m unittest discover -s backend -p "test_reglement_metier_missions.py"
Le test s'exécute depuis N'IMPORTE QUEL répertoire (racine, backend/, intégration
continue). La base de test est créée toute seule dans le dossier temporaire du
système ; DATABASE_URL peut la surcharger, à condition de viser une base de test
(la base applicative réelle est refusée, arrêt code 2).
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, date, timedelta
from pathlib import Path

os.environ["TESTING"] = "1"
os.environ["SIM_ENABLE"] = "0"
os.environ["COLLECTOR_SOURCE"] = "AUCUN"

# ══════════════════════════════════════════════════════════════════════════
# AMORÇAGE ROBUSTE (v1.54, 21/09/2026) — correctif d'exécution, AUCUNE
# assertion métier touchée.
#
# 1) RACINE DU DÉPÔT déduite de l'emplacement de CE fichier.
#    Avant : `sys.path.insert(0, os.path.abspath("."))` + `abspath("backend")`
#    → le test ne marchait QUE si le répertoire courant était la racine du
#    dépôt ; lancé depuis `backend/` (ce que fait la campagne de tests, et
#    ce que ferait un lanceur d'intégration continue), il mourait sur
#    `ModuleNotFoundError: No module named 'backend'`.
#    Désormais : aucun chemin absolu, aucun répertoire courant supposé.
#
# 2) BASE DE TEST SÛRE par défaut (dossier temporaire du système).
#    Avant : sans variable DATABASE_URL, le test utilisait la base
#    applicative par défaut (<dépôt>/data/lss.db) et écrivait dedans
#    — configuration manuelle cachée, et risque réel sur une installation
#    existante. Désormais : base temporaire automatique, et refus explicite
#    (code 2) de toute base qui ne serait pas une base de test.
# ══════════════════════════════════════════════════════════════════════════
RACINE_DEPOT = Path(__file__).resolve().parents[1]
if str(RACINE_DEPOT) not in sys.path:
    sys.path.insert(0, str(RACINE_DEPOT))

_base_auto = Path(tempfile.gettempdir()) / "test_reglement_metier_missions.db"
_url_base = os.environ.get("DATABASE_URL", "")
if not _url_base:
    os.environ["DATABASE_URL"] = f"sqlite:///{_base_auto}"
elif not _url_base.startswith("sqlite:///"):
    print("⛔ Sécurité : ce test n'accepte qu'une base SQLite de test.")
    sys.exit(2)
else:
    _fichier = Path(_url_base.replace("sqlite:///", "", 1))
    _est_base_de_test = (
        "/tmp/" in _url_base
        or "test" in _fichier.name.lower()
        or _fichier.parent.resolve() == Path(tempfile.gettempdir()).resolve())
    if not _est_base_de_test:
        print("⛔ Sécurité : base de test uniquement (fichier temporaire ou "
              "nom contenant « test »). Base refusée : "
              f"{_fichier.name}")
        sys.exit(2)

# Base repartie proprement (leçon « base /tmp périmée ») : une base de test
# laissée par une exécution précédente ferait échouer les insertions.
if os.environ["DATABASE_URL"].startswith("sqlite:///"):
    for _suffixe in ("", "-wal", "-shm"):
        try:
            Path(str(Path(os.environ["DATABASE_URL"].replace("sqlite:///", "", 1))
                     ) + _suffixe).unlink()
        except OSError:
            pass

from backend.app.database import SessionLocal, Base, engine as db_engine
from backend.app.models import (
    Vehicule, Conducteur, SuiviJournalier, Mission, Alerte,
    StatutCamion, StatutVehicule, StatutConducteur, StatutMission,
    StatutAlerte, TypeAlerte, TypeEvenement, uid
)
from backend.app.geozones import (
    DEPOTS_OFFICIELS_DECHARGEMENT, DEPOT_OFFICIEL_CHARGEMENT,
    extraire_depot_portail, normaliser_code_depot, nom_officiel_depot,
    detecter_checkpoint_rn2
)
from backend.app.engine import (
    ingest_event, appliquer_champs_suivi, initialiser_ou_maj_mission
)


class TestReglementMetierMissions(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=db_engine)

    def setUp(self):
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_01_referentiel_depots_et_extraction_exacte(self):
        """§5 & §6 : Table des 7 dépôts récepteurs officiels et extraction exacte."""
        self.assertEqual(len(DEPOTS_OFFICIELS_DECHARGEMENT), 7)
        self.assertEqual(DEPOTS_OFFICIELS_DECHARGEMENT["DABI"], "Depot Alarobia (DABI)")
        self.assertEqual(DEPOTS_OFFICIELS_DECHARGEMENT["DABE"], "Depot Antsirabe (DABE)")
        self.assertEqual(DEPOTS_OFFICIELS_DECHARGEMENT["DMDV"], "Depot Morondava (DMDV)")
        self.assertEqual(DEPOTS_OFFICIELS_DECHARGEMENT["DMKR"], "Depot Manakara (DMKR)")
        self.assertEqual(DEPOTS_OFFICIELS_DECHARGEMENT["DFIA"], "Depot Fianarantsoa (DFIA)")
        self.assertEqual(DEPOTS_OFFICIELS_DECHARGEMENT["DSNR"], "Depot Soanierana (DSNR)")
        self.assertEqual(DEPOTS_OFFICIELS_DECHARGEMENT["DMMG"], "Depot Moramanga (DMMG)et parking (devant depot + steel 1947)")

        # Extractions exactes portail
        self.assertEqual(extraire_depot_portail("Depot Antsirabe (DABE) - Parking Sud"), "DABE")
        self.assertEqual(extraire_depot_portail("Depot Fianarantsoa (DFIA)"), "DFIA")
        self.assertEqual(extraire_depot_portail("Depot Moramanga (DMMG)et parking (devant depot + steel 1947)"), "DMMG")
        self.assertEqual(extraire_depot_portail("Depot Soanierana (DSNR)"), "DSNR")
        self.assertEqual(extraire_depot_portail("Depot Alarobia (DABI)"), "DABI")
        self.assertEqual(extraire_depot_portail("Depot Morondava (DMDV)"), "DMDV")
        self.assertEqual(extraire_depot_portail("Depot Manakara (DMKR)"), "DMKR")
        self.assertEqual(extraire_depot_portail("GRT (GALANA RAFINERIE TERMINALE)"), "GRT")

        # Lieux non officiels -> None
        self.assertIsNone(extraire_depot_portail("Station Shell Ankorondrano"))
        self.assertIsNone(extraire_depot_portail("Garage Privé PK 12"))

    def test_02_depotage_standard_4_conditions(self):
        """§15, §16, §18 : Dépotage standard (DABE) : 4 conditions strictes (durée > 3h + sortie)."""
        plaque = f"TEST-DABE-{int(datetime.now().timestamp())}"
        v = Vehicule(id=uid(), plaque=plaque, statut=StatutVehicule.ACTIF)
        self.db.add(v)
        self.db.commit()

        j = date.today()
        s = SuiviJournalier(
            vehicule_id=v.id, date_jour=j,
            statut_camion=StatutCamion.CHARGE,
            numero_ot="OT-DABE-001", distributeur="TOTAL", produit="GASOIL",
            depot_recepteur="Depot Antsirabe (DABE)"
        )
        self.db.add(s)
        self.db.commit()

        # Initialiser mission en statut CHARGE
        t0 = datetime(j.year, j.month, j.day, 6, 0, 0)
        m = initialiser_ou_maj_mission(self.db, s, v, s.numero_ot, s.distributeur, s.produit, s.depot_recepteur, t0)
        m.statut_camion_actuel = "CHARGE"
        m.heure_chargement = t0
        self.db.commit()

        # 1. Arrivée à DABE à 10:00
        t_arr = datetime(j.year, j.month, j.day, 10, 0, 0)
        ingest_event(self.db, v, t_arr, -19.8659, 47.0333, "Depot Antsirabe (DABE)", 0.0, "OFF")
        self.db.refresh(m)
        self.assertEqual(m.statut, StatutMission.EN_COURS)
        self.assertEqual(m.statut_camion_actuel, "CHARGE")

        # 2. Après 2h (12:00) : toujours en cours, pas de dépotage (< 3h)
        t_2h = datetime(j.year, j.month, j.day, 12, 0, 0)
        ingest_event(self.db, v, t_2h, -19.8659, 47.0333, "Depot Antsirabe (DABE)", 0.0, "OFF")
        self.db.refresh(m)
        self.assertEqual(m.statut, StatutMission.EN_COURS)
        self.assertEqual(m.statut_camion_actuel, "CHARGE")

        # 3. Après 4h (14:00) : toujours dans le dépôt -> ATTENDRE SORTIE (§18)
        t_4h = datetime(j.year, j.month, j.day, 14, 0, 0)
        ingest_event(self.db, v, t_4h, -19.8659, 47.0333, "Depot Antsirabe (DABE)", 0.0, "OFF")
        self.db.refresh(m)
        self.assertEqual(m.statut, StatutMission.EN_COURS)
        self.assertEqual(m.statut_camion_actuel, "CHARGE")

        # 4. Sortie du dépôt à 14:15 sur RN7 (hors zone dépôt) -> Dépotage CONFIRMÉ (§16)
        t_sortie = datetime(j.year, j.month, j.day, 14, 15, 0)
        ingest_event(self.db, v, t_sortie, -19.7000, 47.0500, "RN7 Antsirabe Nord", 40.0, "ON")
        self.db.refresh(m)
        self.db.refresh(s)

        self.assertEqual(m.statut, StatutMission.TERMINEE)
        self.assertEqual(m.statut_camion_actuel, "LIBRE")
        self.assertEqual(s.statut_camion, StatutCamion.LIBRE)
        # Nettoyage automatique Suivi Journalier (§29)
        self.assertIsNone(s.numero_ot)
        self.assertIsNone(s.distributeur)
        self.assertIsNone(s.produit)
        self.assertIsNone(s.depot_recepteur)

    def test_03_preuve_retrospective_dmmg(self):
        """§20-§23 : Preuve rétrospective DMMG (Moramanga -> Andasibe -> Ambatosonegaly)."""
        plaque = f"TEST-DMMG-{int(datetime.now().timestamp())}"
        v = Vehicule(id=uid(), plaque=plaque, statut=StatutVehicule.ACTIF)
        self.db.add(v)
        self.db.commit()

        j = date.today()
        s = SuiviJournalier(
            vehicule_id=v.id, date_jour=j,
            statut_camion=StatutCamion.CHARGE,
            numero_ot="OT-MMG-002", distributeur="JOVENNA", produit="SUPER",
            depot_recepteur="Depot Moramanga (DMMG)"
        )
        self.db.add(s)
        self.db.commit()

        t0 = datetime(j.year, j.month, j.day, 7, 0, 0)
        m = initialiser_ou_maj_mission(self.db, s, v, s.numero_ot, s.distributeur, s.produit, s.depot_recepteur, t0)
        m.statut_camion_actuel = "CHARGE"
        m.heure_chargement = t0
        self.db.commit()

        # Arrivée Moramanga à 11:00
        t_mmg = datetime(j.year, j.month, j.day, 11, 0, 0)
        ingest_event(self.db, v, t_mmg, -18.9489, 48.2257, "Depot Moramanga (DMMG)et parking (devant depot + steel 1947)", 0.0, "OFF")

        # Passage Andasibe (route Est)
        t_and = datetime(j.year, j.month, j.day, 13, 30, 0)
        ingest_event(self.db, v, t_and, -18.9261, 48.4178, "Andasibe RN2", 35.0, "ON")
        self.db.refresh(m)
        self.db.refresh(s)

        # Confirmation rétrospective déclenchée dès passage vers l'Est
        self.assertEqual(m.statut, StatutMission.TERMINEE)
        self.assertEqual(m.statut_camion_actuel, "LIBRE")
        self.assertEqual(s.statut_camion, StatutCamion.LIBRE)
        self.assertEqual(m.heure_fin, t_mmg)  # Date fin calée sur Moramanga (§22)
        self.assertIsNone(s.numero_ot)

    def test_04_invalidation_dmmg_transit_tana(self):
        """§24 : Invalidation DMMG si trajectoire Moramanga -> Andriaka -> Tana avec camion CHARGÉ."""
        plaque = f"TEST-TRANSIT-{int(datetime.now().timestamp())}"
        v = Vehicule(id=uid(), plaque=plaque, statut=StatutVehicule.ACTIF)
        self.db.add(v)
        self.db.commit()

        j = date.today()
        s = SuiviJournalier(
            vehicule_id=v.id, date_jour=j,
            statut_camion=StatutCamion.CHARGE,
            numero_ot="OT-TANA-003", distributeur="SHELL", produit="GASOIL",
            depot_recepteur="Depot Soanierana (DSNR)"
        )
        self.db.add(s)
        self.db.commit()

        t0 = datetime(j.year, j.month, j.day, 6, 0, 0)
        m = initialiser_ou_maj_mission(self.db, s, v, s.numero_ot, s.distributeur, s.produit, s.depot_recepteur, t0)
        m.statut_camion_actuel = "CHARGE"
        m.heure_chargement = t0
        self.db.commit()

        # Transit Moramanga (court arrêt 30 min)
        t_mmg = datetime(j.year, j.month, j.day, 10, 0, 0)
        ingest_event(self.db, v, t_mmg, -18.9489, 48.2257, "Depot Moramanga (DMMG)", 0.0, "OFF")

        # Repart vers l'Ouest (Andriaka vers Tana)
        t_andriaka = datetime(j.year, j.month, j.day, 11, 30, 0)
        ingest_event(self.db, v, t_andriaka, -18.9167, 48.0500, "Andriaka RN2", 45.0, "ON")
        self.db.refresh(m)

        # La mission ne doit PAS avoir dépoté à Moramanga ! Elle reste EN_COURS et CHARGÉ vers Tana
        self.assertEqual(m.statut, StatutMission.EN_COURS)
        self.assertEqual(m.statut_camion_actuel, "CHARGE")


if __name__ == "__main__":
    unittest.main()
