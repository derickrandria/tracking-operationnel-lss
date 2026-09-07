# -*- coding: utf-8 -*-
"""Test suite — Déduplication avancée, driverKeyCode MZoneX, CamtrackPro et Fusion de chauffeurs."""
import os
import sys
os.environ.setdefault("SIM_ENABLE", "0")
os.environ["YMANE_ACTIVE"] = "0"

# Configuration universelle UTF-8 pour Windows PowerShell / Linux
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import engine
from app.config import calculer_tokens_set, normaliser_libelle
from app.models import (Alerte, AuditLog, Conducteur, ConducteurAlias,
                        HistoriqueJournalier, Infraction, Mission,
                        StatutAlerte, StatutConducteur, StatutSourceTrajet,
                        StatutValidationTrajet, SuiviJournalier, Trajet,
                        TypeAlerte, TypeInfraction, SourceEvenement, Vehicule)
from app.seed import seed_si_vide
from app.main import app, migrer_schema
from app.engine import creer_conducteur_auto, resoudre_badge
from app.serializers import s_conducteur

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
    print("⛔ Sécurité : base de test uniquement (DATABASE_URL /tmp).")
    sys.exit(2)

if db_url.startswith("sqlite:///"):
    try:
        os.remove(db_url.replace("sqlite:///", "/", 1))
    except OSError:
        pass

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
db = SessionLocal()

print("\n[T1] Test Sac de Mots (Inversion Nom / Prénom)")
# Création d'un conducteur "RAKOTO Jean Paul"
c_t1 = Conducteur(nom_prenom="RAKOTO Jean Paul", prenom_usuel="Jean Paul",
                  matricule="CH101",
                  nom_normalise=normaliser_libelle("RAKOTO Jean Paul"),
                  tokens_set=calculer_tokens_set("RAKOTO Jean Paul"),
                  statut=StatutConducteur.ACTIF)
db.add(c_t1)
db.commit()

# Ingestion de "Jean Paul RAKOTO" -> doit retrouver c_t1
c_trouve = creer_conducteur_auto(db, "Jean Paul RAKOTO")
check("Inversion exacte 'Jean Paul RAKOTO' reconnue comme 'RAKOTO Jean Paul'",
      c_trouve is not None and c_trouve.id == c_t1.id)

# Vérifier qu'un alias a été automatiquement créé pour cette variante
alias_cree = db.scalar(select(ConducteurAlias).where(
    ConducteurAlias.alias_normalise == normaliser_libelle("Jean Paul RAKOTO")))
check("Alias permanent automatique créé pour la variante inversée",
      alias_cree is not None and alias_cree.conducteur_id == c_t1.id)

print("\n[T2] Test Inclusion de patronyme (Nom court dans Nom long)")
# Dans seed_si_vide(), "RAKOTOMALALA Johnny horlando" est présent
# Ingestion de "RAKOTOMALALA Johnny" -> doit être rattaché par inclusion à "RAKOTOMALALA Johnny horlando"
c_johnny = db.scalar(select(Conducteur).where(Conducteur.nom_prenom.ilike("%RAKOTOMALALA Johnny horlando%")))
c_inc = creer_conducteur_auto(db, "RAKOTOMALALA Johnny")
check("Nom court 'RAKOTOMALALA Johnny' rattaché par inclusion à 'RAKOTOMALALA Johnny horlando'",
      c_inc is not None and c_johnny is not None and c_inc.id == c_johnny.id)

print("\n[T3] Test driverKeyCode MZoneX vs CamtrackPro")
# MZoneX avec badge_code 9988
c_mzone = creer_conducteur_auto(db, "ANDRIANAIVOHASINA Faneva", badge_code=9988, plateforme="MZONEX")
db.commit()
check("Création chauffeur MZoneX avec driverKeyCode",
      c_mzone is not None and c_mzone.code_badge_mzonex == 9988 and c_mzone.matricule == "9988")

# Re-détection MZoneX avec le même badge_code mais un nom légèrement différent
c_mzone_re = creer_conducteur_auto(db, "Faneva ANDRIANAIVO", badge_code=9988, plateforme="MZONEX")
check("Rapprochement par driverKeyCode prioritaire absolu",
      c_mzone_re is not None and c_mzone_re.id == c_mzone.id)

# CamtrackPro : sans badge_code
c_camtrack = creer_conducteur_auto(db, "RABENJANAHARY Patrick", badge_code=None, plateforme="CAMTRACKPRO")
db.commit()
check("Création chauffeur CamtrackPro sans code (matricule vide)",
      c_camtrack is not None and c_camtrack.code_badge_mzonex is None and c_camtrack.matricule is None)

# Test synchronisation automatique driverKeyCode par tokens_set
print("\n[T3b] Test synchronisation automatique driverKeyCode par tokens_set")
# On crée un chauffeur sans badge
c_homonyme = Conducteur(nom_prenom="Patrick RABENJANAHARY", prenom_usuel="PATRICK",
                        matricule=None, code_badge_mzonex=None,
                        nom_normalise=normaliser_libelle("Patrick RABENJANAHARY"),
                        tokens_set=calculer_tokens_set("Patrick RABENJANAHARY"),
                        statut=StatutConducteur.ACTIF)
db.add(c_homonyme)
db.commit()

# On attribue un driverKeyCode à la fiche c_camtrack
c_camtrack.code_badge_mzonex = 54321
c_camtrack.matricule = "54321"
db.commit()
# Appel de creer_conducteur_auto ou modification via referentiel
creer_conducteur_auto(db, "RABENJANAHARY Patrick", badge_code=54321, plateforme="MZONEX")
db.commit()

db.refresh(c_homonyme)
check("driverKeyCode 54321 synchronisé automatiquement sur l'homonyme 'Patrick RABENJANAHARY' via tokens_set",
      c_homonyme.code_badge_mzonex == 54321 and c_homonyme.matricule == "54321")

print("\n[T4] Test API REST Fusion de chauffeurs")
client = TestClient(app)
rz = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@2026"})
token = rz.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Création d'un doublon AUTO
c_auto = Conducteur(nom_prenom="RABENJANAHARY Patrick Auto", prenom_usuel="Patrick",
                    matricule="AUTO-9999",
                    nom_normalise=normaliser_libelle("RABENJANAHARY Patrick Auto"),
                    tokens_set=calculer_tokens_set("RABENJANAHARY Patrick Auto"),
                    statut=StatutConducteur.ACTIF)
db.add(c_auto)
db.commit()
c_auto_id = c_auto.id
c_camtrack_id = c_camtrack.id

v_test = db.scalar(select(Vehicule).limit(1))
suivi_test = SuiviJournalier(date_jour=date.today(), vehicule_id=v_test.id, conducteur_id=c_auto_id)
db.add(suivi_test)
db.commit()
suivi_test_id = suivi_test.id

# Fusionner c_auto vers c_camtrack
resp_fusion = client.post("/api/conducteurs/fusionner", headers=headers, json={
    "source_id": c_auto_id,
    "cible_id": c_camtrack_id,
    "creer_alias": True
})
check("Endpoint /api/conducteurs/fusionner renvoie 200 OK", resp_fusion.status_code == 200)

db.expire_all()
# Vérifier que c_auto est supprimé
check("Fiche source AUTO supprimée", db.get(Conducteur, c_auto_id) is None)
# Vérifier que le suivi a été réassigné
suivi_maj = db.get(SuiviJournalier, suivi_test_id)
check("Suivi journalier réassigné au chauffeur cible", suivi_maj.conducteur_id == c_camtrack_id)
# Vérifier que l'alias a été créé
alias_fusion = db.scalar(select(ConducteurAlias).where(
    ConducteurAlias.alias_normalise == normaliser_libelle("RABENJANAHARY Patrick Auto")))
check("Alias permanent créé pour l'ancien nom de la fiche fusionnée",
      alias_fusion is not None and alias_fusion.conducteur_id == c_camtrack_id)

print("\n[T5] Test gestion des Alias (Ajout / Suppression)")
resp_add_alias = client.post(f"/api/conducteurs/{c_camtrack_id}/aliases", headers=headers, json={
    "alias": "PATRICK Rabenjanahary"
})
check("Ajout manuel d'alias 200 OK", resp_add_alias.status_code == 200)
alias_id = resp_add_alias.json().get("id")

resp_del_alias = client.delete(f"/api/conducteurs/{c_camtrack_id}/aliases/{alias_id}", headers=headers)
check("Suppression d'alias 200 OK", resp_del_alias.status_code == 200)

print("\n[T6] Test Suppression directe d'un chauffeur")
c_del = Conducteur(nom_prenom="A_SUPPRIMER Test", prenom_usuel="Test",
                   matricule="TEST-DEL", statut=StatutConducteur.ACTIF)
db.add(c_del)
db.commit()
c_del_id = c_del.id

# Attacher un véhicule et un alias à ce chauffeur
v_del = db.scalar(select(Vehicule).limit(1))
v_del.conducteur_actuel_id = c_del_id
db.add(ConducteurAlias(conducteur_id=c_del_id, alias_brut="Test Alias", alias_normalise="test alias"))
db.commit()

resp_suppr = client.delete(f"/api/conducteurs/{c_del_id}", headers=headers)
check("Suppression directe chauffeur 200 OK", resp_suppr.status_code == 200)
db.expire_all()
check("Chauffeur supprimé en base", db.get(Conducteur, c_del_id) is None)
check("Véhicule détaché proprement (conducteur_actuel_id = None)", v_del.conducteur_actuel_id is None)
check("Alias associé supprimé", db.scalar(select(ConducteurAlias).where(ConducteurAlias.conducteur_id == c_del_id)) is None)

print("\n[T7] Test clés de service et garage (Garage LSS, Nouveau conducteur)")
from app.engine import resoudre_badge, attribuer_badge_au_jour, TypeAlerte
fiche_gar, ecarte_gar = resoudre_badge(db, "Garage LSS 2")
check("Libellé 'Garage LSS 2' écarté (fiche=None, ecarte='Garage LSS 2')",
      fiche_gar is None and ecarte_gar == "Garage LSS 2")

fiche_nouv, ecarte_nouv = resoudre_badge(db, "Nouveau conducteur")
check("Libellé 'Nouveau conducteur' écarté",
      fiche_nouv is None and ecarte_nouv == "Nouveau conducteur")

print("\n[T8] Test Détection de Conflit d'affectation (Chauffeur manuel vs détection portail)")
# Créer 2 véhicules et un chauffeur
v_conf_1 = Vehicule(plaque="TEST-CF1", statut="ACTIF")
v_conf_2 = Vehicule(plaque="TEST-CF2", statut="ACTIF")
c_manuel = Conducteur(nom_prenom="RABETAFIKA Michel", prenom_usuel="Michel",
                      matricule=None, nom_normalise=normaliser_libelle("RABETAFIKA Michel"),
                      tokens_set=calculer_tokens_set("RABETAFIKA Michel"), statut=StatutConducteur.ACTIF)
db.add_all([v_conf_1, v_conf_2, c_manuel])
db.commit()

# Affecter manuellement c_manuel au véhicule 1 pour aujourd'hui
suivi_cf1 = SuiviJournalier(date_jour=date.today(), vehicule_id=v_conf_1.id,
                            conducteur_id=c_manuel.id, conducteur_origine="MANUEL")
suivi_cf2 = SuiviJournalier(date_jour=date.today(), vehicule_id=v_conf_2.id,
                            conducteur_id=None, conducteur_origine=None)
db.add_all([suivi_cf1, suivi_cf2])
db.commit()

# Simuler une détection portail du même chauffeur c_manuel sur le véhicule 2
attribuer_badge_au_jour(db, suivi_cf2, c_manuel)
db.commit()

# Vérifier que le suivi_cf1 a conservé son chauffeur manuel
db.refresh(suivi_cf1)
db.refresh(suivi_cf2)
check("Suivi 1 conserve son chauffeur MANUEL (priorité absolue)",
      suivi_cf1.conducteur_id == c_manuel.id and suivi_cf1.conducteur_origine == "MANUEL")
check("Suivi 2 n'a pas dupliqué le chauffeur manuel",
      suivi_cf2.conducteur_id != c_manuel.id)

# Vérifier qu'une alerte CONFLIT_AFFECTATION a été créée
alerte_conflit = db.scalar(select(Alerte).where(
    Alerte.type == TypeAlerte.CONFLIT_AFFECTATION,
    Alerte.vehicule_id == v_conf_2.id,
    Alerte.conducteur_id == c_manuel.id))
check("Alerte CONFLIT_AFFECTATION créée pour le véhicule 2", alerte_conflit is not None)

print("\n[T9] Test Anti-doublon pour chauffeur BADGE mobile")
# Créer 2 nouveaux véhicules et un chauffeur BADGE
v_b1 = Vehicule(plaque="TEST-B1", statut="ACTIF")
v_b2 = Vehicule(plaque="TEST-B2", statut="ACTIF")
c_badge = Conducteur(nom_prenom="RAZAFY Hery", prenom_usuel="Hery",
                     matricule="77889", code_badge_mzonex=77889,
                     nom_normalise=normaliser_libelle("RAZAFY Hery"),
                     tokens_set=calculer_tokens_set("RAZAFY Hery"), statut=StatutConducteur.ACTIF)
db.add_all([v_b1, v_b2, c_badge])
db.commit()

suivi_b1 = SuiviJournalier(date_jour=date.today(), vehicule_id=v_b1.id,
                           conducteur_id=c_badge.id, conducteur_origine="BADGE")
suivi_b2 = SuiviJournalier(date_jour=date.today(), vehicule_id=v_b2.id,
                           conducteur_id=None, conducteur_origine=None)
db.add_all([suivi_b1, suivi_b2])
db.commit()

# Détection du chauffeur c_badge sur le véhicule 2
attribuer_badge_au_jour(db, suivi_b2, c_badge)
db.commit()

db.refresh(suivi_b1)
db.refresh(suivi_b2)
check("Chauffeur BADGE réaffecté au véhicule 2",
      suivi_b2.conducteur_id == c_badge.id and suivi_b2.conducteur_origine == "BADGE")
check("Chauffeur BADGE détaché du véhicule 1 pour éviter tout doublon",
      suivi_b1.conducteur_id is None)

print("\n[T10] Test Distinction des temps de conduite pour multi-chauffeurs sur un même camion")
from app.routers.temps_conduite import extraire_donnees_chauffeurs

v_partage = Vehicule(plaque="TEST-4926TBU", statut="ACTIF")
c_dom = Conducteur(nom_prenom="RAKOTONINDRINA Solofohery Alain", prenom_usuel="DOMINIQUE",
                   matricule="3968", code_badge_mzonex=3968,
                   nom_normalise=normaliser_libelle("RAKOTONINDRINA Solofohery Alain"),
                   tokens_set=calculer_tokens_set("RAKOTONINDRINA Solofohery Alain"), statut=StatutConducteur.ACTIF)
c_ando = Conducteur(nom_prenom="ANDRIAMAMONJY Ando Lovasoa", prenom_usuel="Ando",
                    matricule="39002", code_badge_mzonex=39002,
                    nom_normalise=normaliser_libelle("ANDRIAMAMONJY Ando Lovasoa"),
                    tokens_set=calculer_tokens_set("ANDRIAMAMONJY Ando Lovasoa"), statut=StatutConducteur.ACTIF)
db.add_all([v_partage, c_dom, c_ando])
db.flush()
v_partage.conducteur_actuel_id = c_dom.id
db.commit()

jour_hier = date.today() - timedelta(days=1)
suivi_partage = SuiviJournalier(date_jour=jour_hier, vehicule_id=v_partage.id,
                                conducteur_id=c_ando.id, conducteur_origine="BADGE")
db.add(suivi_partage)
db.flush()

# Trajets de Dominique (de 05:00 à 15:00 = 10h)
t1 = Trajet(suivi_id=suivi_partage.id, numero=1,
            heure_debut=datetime.combine(jour_hier, datetime.min.time()).replace(hour=5),
            heure_fin=datetime.combine(jour_hier, datetime.min.time()).replace(hour=15),
            distance_km=250.0, statut_validation=StatutValidationTrajet.VALIDE,
            conducteur_badge="RAKOTONINDRINA Solofohery Alain", conducteur_badge_id=c_dom.id)

# Trajet de Ando (de 16:00 à 18:00 = 2h)
t2 = Trajet(suivi_id=suivi_partage.id, numero=2,
            heure_debut=datetime.combine(jour_hier, datetime.min.time()).replace(hour=16),
            heure_fin=datetime.combine(jour_hier, datetime.min.time()).replace(hour=18),
            distance_km=80.0, statut_validation=StatutValidationTrajet.VALIDE,
            conducteur_badge="ANDRIAMAMONJY Ando Lovasoa", conducteur_badge_id=c_ando.id)

db.add_all([t1, t2])
db.commit()

# Archiver ce jour dans HistoriqueJournalier
from app.serializers import s_suivi
from app.models import HistoriqueJournalier
db.expire(suivi_partage, ["trajets"])
db.add(HistoriqueJournalier(
    date_jour=jour_hier, annee=jour_hier.year, mois=jour_hier.month,
    vehicule_id=v_partage.id, conducteur_id=c_ando.id,
    donnees=s_suivi(suivi_partage), nb_infractions=0, nb_alertes=0))
db.commit()

# Extraction des données TCH
res_tch = extraire_donnees_chauffeurs(db, debut_fenetre=jour_hier, fin_fenetre=date.today())
lignes_tch = {l["conducteur_id"]: l for l in res_tch["lignes"]}

dom_tch = lignes_tch.get(c_dom.id)
ando_tch = lignes_tch.get(c_ando.id)

check("Dominique présent dans le calcul TCH", dom_tch is not None)
check("Ando présent dans le calcul TCH", ando_tch is not None)

# Dominique doit avoir ~10h (36000s) et non 0s
dom_tcj_hier = dom_tch["historique"].get(jour_hier.isoformat(), {}).get("tcj_s", 0) if dom_tch else 0
# Ando doit avoir ~2h (7200s) et non 12h
ando_tcj_hier = ando_tch["historique"].get(jour_hier.isoformat(), {}).get("tcj_s", 0) if ando_tch else 0

check(f"Dominique crédité de ses trajets (10h00 = 36000s, obtenu: {dom_tcj_hier}s)",
      dom_tcj_hier == 36000)
check(f"Ando crédité uniquement de ses trajets (2h00 = 7200s, obtenu: {ando_tcj_hier}s)",
      ando_tcj_hier == 7200)

# ------------------------------------------------------------------
# Test 11 : Répartition exacte et affichage multi-chauffeurs sur archive passée
# ------------------------------------------------------------------
print("\n[T11] Test Répartition exacte Dominique / Ando sur archive passée")
from app.reparation import reparer_historique_conducteurs_passes

jour_avant_hier = date.today() - timedelta(days=2)
# Simuler une archive passée où Dominique a conduit 10h00 (36000s) et Ando 2h01 (7260s) = total 12h01
trajets_archive_partage = [
    {
        "id": "t-snap-1", "numero": 1,
        "heure_debut": datetime.combine(jour_avant_hier, datetime.min.time()).replace(hour=4, minute=0).isoformat(),
        "heure_fin": datetime.combine(jour_avant_hier, datetime.min.time()).replace(hour=14, minute=0).isoformat(),
        "distance_km": 300.0, "statut_validation": "VALIDE",
        "conducteur_badge": "RAKOTONINDRINA Solofohery Alain", "conducteur_badge_id": c_dom.id
    },
    {
        "id": "t-snap-2", "numero": 2,
        "heure_debut": datetime.combine(jour_avant_hier, datetime.min.time()).replace(hour=14, minute=30).isoformat(),
        "heure_fin": datetime.combine(jour_avant_hier, datetime.min.time()).replace(hour=16, minute=31).isoformat(),
        "distance_km": 60.0, "statut_validation": "VALIDE",
        "conducteur_badge": "ANDRIAMAMONJY Ando Lovasoa", "conducteur_badge_id": c_ando.id
    }
]

db.add(HistoriqueJournalier(
    date_jour=jour_avant_hier, annee=jour_avant_hier.year, mois=jour_avant_hier.month,
    vehicule_id=v_partage.id, conducteur_id=c_ando.id, # Était attribué par erreur à Ando seul
    donnees={
        "id": "s-err-1", "date_jour": jour_avant_hier.isoformat(),
        "vehicule_id": v_partage.id, "plaque": "TEST-4926TBU",
        "conducteur_id": c_ando.id, "chauffeur": "Ando Lovasoa",
        "tcj_s": 43260, "ttj_s": 50400, "trajets": trajets_archive_partage
    },
    nb_infractions=0, nb_alertes=0))
db.commit()

# Exécuter la réparation des archives passées
stats_rep = reparer_historique_conducteurs_passes(db)
check("Réparation archives passées exécutée sans erreur", not stats_rep.get("erreur"))

# Vérifier que l'archive a bien été réattribuée au chauffeur majoritaire Dominique (10h > 2h01)
hist_repare = db.scalar(select(HistoriqueJournalier).where(
    HistoriqueJournalier.date_jour == jour_avant_hier,
    HistoriqueJournalier.vehicule_id == v_partage.id
))
check("Archive réassignée au chauffeur majoritaire Dominique", hist_repare.conducteur_id == c_dom.id)

# Extraire les données TCH
res_tch2 = extraire_donnees_chauffeurs(db, debut_fenetre=jour_avant_hier, fin_fenetre=date.today())
lignes_tch2 = {l["conducteur_id"]: l for l in res_tch2["lignes"]}

dom_tch2 = lignes_tch2.get(c_dom.id)
ando_tch2 = lignes_tch2.get(c_ando.id)

dom_tcj_j2 = dom_tch2["historique"].get(jour_avant_hier.isoformat(), {}).get("tcj_s", 0) if dom_tch2 else 0
ando_tcj_j2 = ando_tch2["historique"].get(jour_avant_hier.isoformat(), {}).get("tcj_s", 0) if ando_tch2 else 0

check(f"Dominique crédité exactement de ses trajets (10h00 = 36000s, obtenu: {dom_tcj_j2}s)",
      dom_tcj_j2 == 36000)
check(f"Ando crédité exactement de ses trajets (2h01 = 7260s, obtenu: {ando_tcj_j2}s)",
      ando_tcj_j2 == 7260)
check(f"Somme des temps Dominique + Ando = 12h01 (43260s, somme obtenue: {dom_tcj_j2 + ando_tcj_j2}s)",
      dom_tcj_j2 + ando_tcj_j2 == 43260)

print(f"\n{'=' * 60}\nRESULTAT : {R['ok']} OK / {R['ko']} KO\n{'=' * 60}")
db.close()
sys.exit(1 if R["ko"] else 0)
