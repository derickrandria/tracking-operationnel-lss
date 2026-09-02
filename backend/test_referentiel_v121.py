"""Tests v1.21 — ARBITRAGES LSS §0quater D0/D1/D2/D3 du 14/08/2026.

  D0 · identifiants normalisés EXACTEMENT comme les portails (« 8076 TCB » →
       « 8076TCB ») : saisie manuelle + réparation au démarrage (collision →
       fiche non touchée + alerte ⓘ).
  D1 · découverte automatique : identifiant plaque vu par un collecteur et
       inconnu → fiche VÉHICULE créée (plateforme = portail détecteur) +
       exploitation immédiate + alerte ⓘ + audit. Idempotent.
  D2 · conducteur vu sur un portail et inconnu → fiche CONDUCTEUR créée
       (matricule AUTO-) + alerte ⓘ ; affectation au véhicule TOUJOURS manuelle.
  D3 · identifiant non retenu (forme non-plaque) → journalisé, jamais créé.

⏱️ Instants synthétiques passés en paramètre (`maintenant=`) — aucune
   dépendance à l'horloge réelle.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v121.db" python3 test_referentiel_v121.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.config import IDENT_PLAQUE_RE, jour_attribution, normaliser_ident, now_local
from app.database import SessionLocal
from app import engine
from app.models import (Alerte, AuditLog, Conducteur, EvenementGPS,
                        StatutValidationTrajet, StatutVehicule, Trajet,
                        TypeAlerte, Vehicule)
from app.reconciliation import reconcilier_trajets_valides
from app.seed import seed_si_vide
from app.main import migrer_schema, reparer_identifiants

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
    print("⛔ Sécurité : lancez ce test avec DATABASE_URL pointant une base de "
          "test — jamais la base de production.")
    sys.exit(2)

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
db = SessionLocal()

jour = jour_attribution(now_local())
base = datetime.combine(jour, datetime.min.time())


def h(hh, mm, ss=0):
    return base.replace(hour=hh, minute=mm, second=ss)


MAINTENANT = h(11, 0)

# purge des alertes seedées pour des comptages fiables
db.execute(delete(Alerte))
db.execute(delete(AuditLog))
db.commit()


def alertes(t):
    return db.scalars(select(Alerte).where(Alerte.type == t)).all()


def audits(action):
    return db.scalars(select(AuditLog).where(AuditLog.action == action)).all()


try:
    # ================================================================
    print("\n[D0 · §0quater] Normalisation des identifiants")
    check("« 8076 TCB (LSS) » → « 8076TCB »",
          normaliser_ident("8076 TCB (LSS)") == "8076TCB")
    check("« 5616 tce-… » → « 5616TCE » (espaces + casse + suffixe)",
          normaliser_ident("5616 tce-MERCEDES") == "5616TCE")
    check("garde plaque : « 8076TCB » accepté, « TOTAUX » refusé, "
          "« 8076TCB/0527TBP » refusé (composite)",
          bool(IDENT_PLAQUE_RE.match("8076TCB"))
          and not IDENT_PLAQUE_RE.match("TOTAUX")
          and not IDENT_PLAQUE_RE.match("8076TCB/0527TBP"))
    v_espace = Vehicule(plaque="9999 ZZZ", gps_associe="9999 zzz",
                        statut=StatutVehicule.ACTIF)
    v_tiret = Vehicule(plaque="7777WWW", gps_associe="OBC-7777 WWW",
                       statut=StatutVehicule.ACTIF)
    db.add_all([v_espace, v_tiret])
    v_collision = Vehicule(plaque="8888 YYY", gps_associe="OBC-8888YYY",
                           statut=StatutVehicule.ACTIF)
    v_cible = Vehicule(plaque="8888YYY", gps_associe="OBC-8888YYX",
                       statut=StatutVehicule.ACTIF)
    db.add_all([v_collision, v_cible])
    db.commit()
    n_avant = len(alertes(TypeAlerte.NOUVEAU_VEHICULE))
    reparer_identifiants()
    db.expire_all()
    check("réparation démarrage : « 9999 ZZZ » → « 9999ZZZ » (+ boîtier)",
          db.get(Vehicule, v_espace.id).plaque == "9999ZZZ"
          and db.get(Vehicule, v_espace.id).gps_associe == "9999ZZZ")
    check("boîtier à tiret PRÉSERVÉ : « OBC-7777 WWW » → « OBC-7777WWW » "
          "(jamais tronqué à « OBC »)",
          db.get(Vehicule, v_tiret.id).gps_associe == "OBC-7777WWW")
    check("collision → fiche NON touchée + alerte ⓘ posée",
          db.get(Vehicule, v_collision.id).plaque == "8888 YYY"
          and len(alertes(TypeAlerte.NOUVEAU_VEHICULE)) == n_avant + 1)
    vid_espace, vid_collision = v_espace.id, v_collision.id

    # ================================================================
    print("\n[D1 · §0quater] Découverte via collecteur Niveau 1 (MZoneX)")
    from app.scrapers import MZoneXCollector
    nb_veh = db.scalar(select(Vehicule).where(Vehicule.plaque == "1234TAA"))
    check("1234TAA inconnu au départ", nb_veh is None)
    col = MZoneXCollector()
    n = col.inserer([{"gps_associe": "1234TAA", "horodatage": h(8, 0),
                      "lat": -18.9, "lng": 47.5, "adresse": "Dépôt",
                      "vitesse": 30.0, "moteur": "ON",
                      "type_evenement": None, "conducteur": "RAKOTO Jean"}])
    db.expire_all()
    v_new = db.scalar(select(Vehicule).where(Vehicule.plaque == "1234TAA"))
    check("point inséré ET véhicule créé automatiquement", n == 1 and v_new is not None)
    check("fiche : plateforme MZONEX · ACTIF · placée au relevé",
          v_new is not None and v_new.plateforme_gps == "MZONEX"
          and v_new.statut == StatutVehicule.ACTIF)
    check("ligne de trajet ouverte (exploitation immédiate, 08:00)",
          v_new is not None and db.scalar(select(Trajet).where(
              Trajet.suivi_id.in_(select(__import__("app.models", fromlist=["SuiviJournalier"]).SuiviJournalier.id).where(
                  __import__("app.models", fromlist=["SuiviJournalier"]).SuiviJournalier.vehicule_id == v_new.id),
          ))) is not None)
    check("alerte ⓘ « Nouveau véhicule » posée",
          any("1234TAA" in (a.message or "")
              for a in alertes(TypeAlerte.NOUVEAU_VEHICULE)))
    check("audit « vehicule.auto_cree » tracé",
          len(audits("vehicule.auto_cree")) >= 1)
    # identifiant parasite → D3 : rien ne se crée
    n = col.inserer([{"gps_associe": "TOTAUX JOUR", "horodatage": h(8, 5),
                      "lat": -18.9, "lng": 47.5, "adresse": "x",
                      "vitesse": 30.0, "moteur": "ON", "type_evenement": None,
                      "conducteur": ""}])
    check("D3 : identifiant non-plaque → ignoré, AUCUNE fiche créée",
          n == 0 and db.scalar(select(Vehicule).where(
              Vehicule.plaque == "TOTAUXJOUR")) is None)

    print("\n[D1 · §0quater] Découverte via Niveau 2 (CamtrackPro) + idempotence")
    stats = reconcilier_trajets_valides(
        db, [{"plaque": "4321TBB", "gps_associe": "4321TBB",
              "debut": h(8, 0), "fin": h(9, 0), "distance_km": 12.5,
              "conducteur": "", "source": "CAMTRACKPRO"}],
        maintenant=MAINTENANT)
    db.expire_all()
    v_ctp = db.scalar(select(Vehicule).where(Vehicule.plaque == "4321TBB"))
    check("véhicule CamtrackPro créé et trajet importé (crees ≥ 1)",
          v_ctp is not None and v_ctp.plateforme_gps == "CAMTRACKPRO"
          and stats["crees"] >= 1, str(stats))
    n_avant = db.query(Vehicule).count()
    n_al_avant = len(alertes(TypeAlerte.NOUVEAU_VEHICULE))
    reconcilier_trajets_valides(
        db, [{"plaque": "4321TBB", "gps_associe": "4321TBB",
              "debut": h(8, 0), "fin": h(9, 0), "distance_km": 12.5,
              "conducteur": "", "source": "CAMTRACKPRO"}],
        maintenant=MAINTENANT)
    db.expire_all()
    check("idempotent : rejeu → ni 2ᵉ fiche ni 2ᵉ alerte",
          db.query(Vehicule).count() == n_avant
          and len(alertes(TypeAlerte.NOUVEAU_VEHICULE)) == n_al_avant)

    # ================================================================
    print("\n[D2 · §0quater] Découverte chauffeur (affectation manuelle)")
    c_new = db.scalar(select(Conducteur).where(
        Conducteur.nom_prenom == "RAKOTO Jean"))
    check("conducteur « RAKOTO Jean » créé depuis l'événement MZoneX",
          c_new is not None and c_new.matricule.startswith("AUTO-"))
    check("alerte ⓘ « Nouveau chauffeur » posée",
          any("RAKOTO Jean" in (a.message or "")
              for a in alertes(TypeAlerte.NOUVEAU_CONDUCTEUR)))
    check("audit « conducteur.auto_cree » tracé",
          len(audits("conducteur.auto_cree")) >= 1)
    check("affectation au véhicule NON automatique (reste manuelle)",
          db.get(Vehicule, v_new.id).conducteur_actuel_id is None)
    n_c_avant = db.query(Conducteur).count()
    col.inserer([{"gps_associe": "1234TAA", "horodatage": h(9, 0),
                  "lat": -18.9, "lng": 47.5, "adresse": "Route",
                  "vitesse": 35.0, "moteur": "ON", "type_evenement": None,
                  "conducteur": "RAKOTO Jean"}])
    db.expire_all()
    check("idempotent : même conducteur revu → aucune 2ᵉ fiche",
          db.query(Conducteur).count() == n_c_avant)

finally:
    db.close()
    import app.database as d
    d.engine.dispose()
    if db_url.startswith("sqlite") and "/tmp/" in db_url:
        for p in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(db_url.replace("sqlite:///", "") + p)
            except OSError:
                pass

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
