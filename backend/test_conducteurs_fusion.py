# -*- coding: utf-8 -*-
"""Test suite — Déduplication avancée, driverKeyCode MZoneX, CamtrackPro et Fusion de chauffeurs."""
import os
os.environ.setdefault("SIM_ENABLE", "0")
os.environ["YMANE_ACTIVE"] = "0"
import sys
from datetime import date, datetime, time

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

print(f"\n{'=' * 60}\nRESULTAT : {R['ok']} OK / {R['ko']} KO\n{'=' * 60}")
db.close()
sys.exit(1 if R["ko"] else 0)
