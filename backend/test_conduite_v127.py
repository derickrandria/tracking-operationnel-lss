"""Tests v1.27 — Conducteurs & conduite (§0septies, arbitrages LSS 20/08/2026).

HORS RÉSEAU (les faits ont été mesurés en direct le 20/08/2026 : 77 % des
trajets MZoneX badgés, Events porte driverKeyCode en continu, 749+450
géozones, rapport CamtrackPro avec vitesse max + ralenti + conducteur) :

  E1  Mapping MZoneX Trips enrichi : conducteur, code clé, vitesse max,
      compteurs d'écoconduite détaillés ; « autres » = total officiel −
      détail (borne 0) ; champs absents → None (jamais inventé, §10).
  E2  Mapping Wialon : vitesse max « 45 km/h » → 45.0 ; ralenti « 0:03:12 »
      → 192 s ; conducteur toujours propagé.
  E3  B2 — résolution du badge : « Nouveau conducteur » / « GARAGE LSS »
      (casse/accents) écartés SANS fiche ; vrai nom → fiche D2 (idempotent).
  E4  Réconciliation de bout en bout : badge valide inscrit sur la ligne +
      chauffeur du jour (origine BADGE) ; clé de service → badge écarté,
      saisie manuelle conservée ; attribution MANUELLE jamais écrasée ;
      seconde synchronisation identique = zéro nouvel audit (idempotence).
  E5  Fusion §2.2 (< 20 min) : compteurs SOMMÉS, vitesse MAX retenue, DERNIER
      badge publié fait foi — distance et horaires inchangés (règles §7/§5.1).
  E6  Alertes en direct : VITESSE_LIVE (2 signaux > 45 hors zone, 1 par
      épisode, silence en géozone) ; SANS_BADGE (MZoneX > 3 km/h sans clé
      10 min, 1 par épisode, réarmement 30 min) ; CamtrackPro jamais en
      direct (B5).
  E7  API /api/conduite : 401 sans jeton ; vue chauffeurs (totaux exacts,
      « /100 km ») ; vue trajets noirs (filtre min_total).

Exécution (base de test isolée, SUPPRIMÉE à la fin) :
  DATABASE_URL="sqlite:////tmp/test_v127.db" python3 test_conduite_v127.py
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app.config import jour_attribution, now_local
from app.database import SessionLocal
from app import engine
from app.engine import (appliquer_badge_et_eco, resoudre_badge,
                        verifier_alertes_conduite)
from app.models import (Alerte, AuditLog, Conducteur, SourceEvenement,
                        StatutValidationTrajet, SuiviJournalier, Trajet,
                        TypeAlerte, Vehicule)
from app.reconciliation import (normaliser_valides,
                                reconcilier_trajets_valides)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.api_mzonex import trajet_depuis_api
from app.api_wialon import item_depuis_ligne_rapport

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


def vehicule_test(plaque):
    v = db.scalar(select(Vehicule).where(Vehicule.plaque == plaque))
    if v is None:
        v = Vehicule(plaque=plaque, gps_associe=plaque)
        db.add(v)
        db.commit()
    return v


def reel(st):
    return st if st != StatutValidationTrajet.REJETE else st


# ------------------------------------------------------------------ E1
print("\n[E1] Mapping MZoneX Trips enrichi (badge + compteurs officiels)")
brut = {"vehicle_Registration": "8086 TCB",
        "startUtcTimestamp": "2026-08-20T03:30:00Z",
        "endUtcTimestamp": "2026-08-20T04:10:00Z", "distance": 12.34,
        "driver_Description": "JOMA ALEXANDRE", "driverKeyCode": 39020,
        "maxSpeed": 62.5, "numberOfExceptions": 11,
        "numberOfSpeedingExceptions": 4,
        "numberOfHarshBrakingExceptions": 2,
        "numberOfExcessiveAccelerationExceptions": 1,
        "numberOfExcessiveIdleExceptions": 3,
        "numberOfExcessiveRPMExceptions": 0}
it = trajet_depuis_api(brut)
check("conducteur + code clé + vitesse max transmis", it["conducteur"] ==
      "JOMA ALEXANDRE" and it["badge_code"] == 39020 and it["v_max"] == 62.5)
check("compteurs détaillés transmis (vitesse 4, freinage 2, accél 1, ralenti "
      "3, régime 0)", it["exc_vitesse"] == 4 and it["exc_freinage"] == 2
      and it["exc_accel"] == 1 and it["exc_ralenti"] == 3
      and it["exc_surregime"] == 0)
check("« autres » = total officiel (11) − détail (10) = 1",
      it["exc_autres"] == 1)
brut2 = dict(brut, numberOfExceptions=None, maxSpeed=None,
             driver_Description=None, driverKeyCode=None,
             numberOfSpeedingExceptions=None)
it2 = trajet_depuis_api(brut2)
check("champs absents → None (jamais inventés) ; « autres » recalculé sans "
      "total → None", it2["conducteur"] is None and it2["v_max"] is None
      and it2["exc_vitesse"] is None and it2["exc_autres"] is None
      and it2["badge_code"] is None)

# ------------------------------------------------------------------ E2
print("\n[E2] Mapping Wialon (vitesse max + ralenti moteur officiels)")
cellules = ["2026-08-20 02:24:44", {"t": "GFC-Brickaville-RN2"},
            {"t": "2026-08-20 04:39:08"}, {"t": "Antsampanana"},
            "2:14:24", "0:03:12", "2:11:12", "23.04 km",
            {"t": "31 km/h"}, {"t": "45 km/h"}, "0:05:01",
            "RAKOTORAHALAHY HERINIAINA CLEMENT"]
it = item_depuis_ligne_rapport("0826 TBS-MERCEDES -LPSA(LSS)", cellules)
check("vitesse max « 45 km/h » → 45.0 ; ralenti « 0:03:12 » → 192 s ; "
      "conducteur propagé", it["v_max"] == 45.0 and it["ralenti_s"] == 192
      and it["conducteur"].startswith("RAKOTORAHALAHY"))
it = item_depuis_ligne_rapport("0826 TBS-MERCEDES -LPSA(LSS)",
                               [cellules[0], "", "", "", "", "", "", "", "",
                                "", "", ""])
check("cellules vides → v_max/ralenti None (jamais 0 par défaut)",
      it["v_max"] is None and it["ralenti_s"] is None)

# ------------------------------------------------------------------ E3
print("\n[E3] B2 — le badge fait foi SAUF clés de service")
avant = db.scalar(select(func.count(Conducteur.id))) or 0
fiche, ecarte = resoudre_badge(db, "Nouveau conducteur")
check("« Nouveau conducteur » → écarté, aucune fiche",
      fiche is None and ecarte == "Nouveau conducteur")
fiche2, ecarte2 = resoudre_badge(db, "GARAGE LSS")
check("« GARAGE LSS » (casse) → écarté, aucune fiche",
      fiche2 is None and ecarte2 == "GARAGE LSS")
apres = db.scalar(select(func.count(Conducteur.id))) or 0
check("aucune fiche créée pour les clés de service", apres == avant)
fiche3, ecarte3 = resoudre_badge(db, "RABENIAINA Andry Jean Michael")
check("vrai nom → fiche créée (D2) ; le rappel retourne la MÊME fiche",
      fiche3 is not None and ecarte3 is None
      and resoudre_badge(db, "rabeniaina andry jean michael")[0].id
      == fiche3.id)

# ------------------------------------------------------------------ E4
print("\n[E4] Réconciliation de bout en bout (badge + éco + attribution jour)")
v1 = vehicule_test("9999TZZ")
maintenant = now_local()
fin1 = maintenant - timedelta(hours=2)
deb1 = fin1 - timedelta(minutes=40)
if jour_attribution(deb1) != jour_attribution(maintenant):
    # garde bascule 01h00 : recaler dans la journée logistique courante
    deb1 = maintenant.replace(hour=1, minute=5, second=0, microsecond=0)
    fin1 = deb1 + timedelta(minutes=40)
jour = jour_attribution(deb1)
items = normaliser_valides([{
    "plaque": "9999TZZ", "debut": deb1, "fin": fin1, "distance_km": 21.5,
    "conducteur": "JOMA ALEXANDRE", "badge_code": 39020, "v_max": 62.5,
    "exc_vitesse": 4, "exc_freinage": 2, "exc_accel": 1, "exc_ralenti": 3,
    "exc_surregime": 0, "exc_autres": 1, "source": "MZONEX"}])
stats = reconcilier_trajets_valides(db, items, username="test",
                                    maintenant=maintenant)
suivi = db.scalar(select(SuiviJournalier).where(
    SuiviJournalier.vehicule_id == v1.id, SuiviJournalier.date_jour == jour))
t = db.scalar(select(Trajet).where(Trajet.suivi_id == suivi.id))
f_joma = db.scalar(select(Conducteur).where(
    func.lower(Conducteur.nom_prenom) == "joma alexandre"))
check("ligne créée VALIDE (hors « en cours ») avec badge inscrit + fiche "
      "liée (rapprochement insensible à la casse — pas de doublon de fiche)",
      t is not None and t.statut_validation == StatutValidationTrajet.VALIDE
      and (t.conducteur_badge or "").lower() == "joma alexandre"
      and t.conducteur_badge_id == f_joma.id, str(stats))
check("compteurs d'écoconduite rangés sur la ligne (vitesse 4, autres 1, "
      "vmax 62.5)", t.exc_vitesse == 4 and t.exc_autres == 1
      and t.v_max == 62.5)
check("chauffeur du JOUR attribué par le badge (origine BADGE)",
      suivi.conducteur_id == f_joma.id
      and suivi.conducteur_origine == "BADGE")
n_audit = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action.in_(["trajet.badge_attribue", "trajet.eco_inscrit",
                         "suivi.conducteur_badge"]))) or 0
stats2 = reconcilier_trajets_valides(db, items, username="test",
                                     maintenant=maintenant + timedelta(minutes=15))
n_audit2 = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action.in_(["trajet.badge_attribue", "trajet.eco_inscrit",
                         "suivi.conducteur_badge"]))) or 0
check("2ᵉ synchronisation identique : 0 nouvel audit badge/éco (idempotent)",
      n_audit2 == n_audit, f"{n_audit} → {n_audit2}")

# clé de service : badge écarté, saisie manuelle conservée
v2 = vehicule_test("8888TYY")
items2 = normaliser_valides([{
    "plaque": "8888TYY", "debut": deb1, "fin": fin1, "distance_km": 9.2,
    "conducteur": "Nouveau conducteur", "source": "CAMTRACKPRO",
    "v_max": 38.0, "ralenti_s": 480}])
reconcilier_trajets_valides(db, items2, username="test", maintenant=maintenant)
suivi2 = db.scalar(select(SuiviJournalier).where(
    SuiviJournalier.vehicule_id == v2.id, SuiviJournalier.date_jour == jour))
t2 = db.scalar(select(Trajet).where(Trajet.suivi_id == suivi2.id))
check("clé de service : badge écarté sur la ligne, aucune fiche liée",
      t2.badge_ecarte == "Nouveau conducteur"
      and t2.conducteur_badge_id is None and t2.conducteur_badge is None)
check("chauffeur du jour NON attribué (la saisie manuelle fera le travail) ; "
      "ralenti/vmax officiels CamtrackPro rangés",
      suivi2.conducteur_id is None and t2.ralenti_s == 480
      and t2.v_max == 38.0)

# attribution MANUELLE jamais écrasée par un badge (même valide)
suivi2.conducteur_id = f_joma.id
suivi2.conducteur_origine = "MANUEL"
db.commit()
items3 = normaliser_valides([{
    "plaque": "8888TYY", "debut": fin1 + timedelta(minutes=30),
    "fin": fin1 + timedelta(minutes=90), "distance_km": 12.0,
    "conducteur": "RABENIAINA Andry Jean Michael", "source": "MZONEX"}])
reconcilier_trajets_valides(db, items3, username="test",
                            maintenant=maintenant + timedelta(minutes=30))
db.refresh(suivi2)
check("attribution MANUELLE conservée face à un badge valide (B2)",
      suivi2.conducteur_id == f_joma.id
      and suivi2.conducteur_origine == "MANUEL")

# ------------------------------------------------------------------ E5
print("\n[E5] v3 AM-2 : plus de fusion — chaque segment garde SA ligne,\n     SES compteurs et SON badge")
v3 = vehicule_test("7777TXX")
f1 = maintenant - timedelta(hours=3)
d1 = f1 - timedelta(minutes=40)             # segment 1 : 40 min
d2 = f1 + timedelta(minutes=15)             # reprise 15 min après → même ligne
f2 = d2 + timedelta(minutes=30)
if jour_attribution(d1) != jour_attribution(maintenant):
    d1 = maintenant.replace(hour=1, minute=5, second=0, microsecond=0)
    f1 = d1 + timedelta(minutes=40)
    d2, f2 = f1 + timedelta(minutes=15), f1 + timedelta(minutes=45)
items = normaliser_valides([
    {"plaque": "7777TXX", "debut": d1, "fin": f1, "distance_km": 10.0,
     "conducteur": "JOMA ALEXANDRE", "exc_vitesse": 1, "exc_freinage": 0,
     "v_max": 44.0, "source": "MZONEX"},
    {"plaque": "7777TXX", "debut": d2, "fin": f2, "distance_km": 6.0,
     "conducteur": "RABENIAINA Andry Jean Michael", "exc_vitesse": 2,
     "exc_freinage": 1, "v_max": 62.0, "source": "MZONEX"}])
reconcilier_trajets_valides(db, items, username="test", maintenant=maintenant)
suivi3 = db.scalar(select(SuiviJournalier).where(
    SuiviJournalier.vehicule_id == v3.id,
    SuiviJournalier.date_jour == jour_attribution(d1)))
lignes = db.scalars(select(Trajet).where(
    Trajet.suivi_id == suivi3.id,
    Trajet.statut_validation != StatutValidationTrajet.REJETE)).all()
f_rabe = db.scalar(select(Conducteur).where(
    func.lower(Conducteur.nom_prenom) == "rabeniaina andry jean michael"))
f_joma3 = db.scalar(select(Conducteur).where(
    func.lower(Conducteur.nom_prenom) == "joma alexandre"))
check("v3 : DEUX lignes, chacune avec SA distance (10 km / 6 km) — la pause "
      "de 15 min n'est plus fusionnée (AM-2)",
      len(lignes) == 2
      and abs((lignes[0].distance_km or 0) - 10.0) < 1e-6
      and abs((lignes[1].distance_km or 0) - 6.0) < 1e-6)
check("v3 : compteurs NON sommés — ligne 1 = JOMA (vitesse 1, freinage 0, "
      "vmax 44, badge JOMA) ; ligne 2 = RABENIAINA (vitesse 2, freinage 1, "
      "vmax 62, badge RABENIAINA)",
      lignes[0].exc_vitesse == 1 and lignes[0].exc_freinage == 0
      and lignes[0].v_max == 44.0
      and lignes[0].conducteur_badge_id == f_joma3.id
      and lignes[1].exc_vitesse == 2 and lignes[1].exc_freinage == 1
      and lignes[1].v_max == 62.0
      and lignes[1].conducteur_badge_id == f_rabe.id)

# ------------------------------------------------------------------ E6
print("\n[E6] Alertes en direct (B4 vitesse hors zone ; B5 roule sans badge)")
engine._EP_VITESSE.clear()
engine._EP_BADGE.clear()
v4 = vehicule_test("6666TWW")
t0 = now_local().replace(microsecond=0)
MZ, CP = SourceEvenement.MZONEX, SourceEvenement.CAMTRACKPRO

def n_alertes(type_, vid):
    return db.scalar(select(func.count(Alerte.id)).where(
        Alerte.type == type_, Alerte.vehicule_id == vid)) or 0

verifier_alertes_conduite(db, v4, t0, 50.0, False, False, MZ)
check("1ᵉʳ signal > 45 km/h : confirmation exigée — pas encore d'alerte",
      n_alertes(TypeAlerte.VITESSE_LIVE, v4.id) == 0)
verifier_alertes_conduite(db, v4, t0 + timedelta(seconds=60), 52.0, False,
                          False, MZ)
check("2ᵉ signal confirmé → alerte VITESSE_LIVE ouverte (1 par épisode)",
      n_alertes(TypeAlerte.VITESSE_LIVE, v4.id) == 1)
verifier_alertes_conduite(db, v4, t0 + timedelta(seconds=120), 70.0, True,
                          False, MZ)
check("3ᵉ signal (même épisode) : aucune alerte supplémentaire",
      n_alertes(TypeAlerte.VITESSE_LIVE, v4.id) == 1)
for k in range(2):
    verifier_alertes_conduite(db, v4, t0 + timedelta(seconds=180 + 60 * k),
                              0.0, False, False, MZ)
check("retour ≤ seuil sur 2 signaux : épisode clos SANS nouvelle alerte",
      n_alertes(TypeAlerte.VITESSE_LIVE, v4.id) == 1)
for k in range(2):
    verifier_alertes_conduite(db, v4, t0 + timedelta(seconds=400 + 60 * k),
                              55.0, True, False, MZ)
check("nouvel épisode (après clôture) → nouvelle alerte",
      n_alertes(TypeAlerte.VITESSE_LIVE, v4.id) == 2)
n_av = n_alertes(TypeAlerte.VITESSE_LIVE, v4.id)
engine._EP_VITESSE.clear()
for k in range(3):
    verifier_alertes_conduite(db, v4, t0 + timedelta(seconds=900 + 60 * k),
                              80.0, True, True, MZ)     # EN géozone → silence
check("EN GÉOZONE : jamais d'alerte 45 (les seuils portail gouvernent, B4)",
      n_alertes(TypeAlerte.VITESSE_LIVE, v4.id) == n_av)

# B5 — roule sans badge (MZoneX), épisodes et réarmement
v5 = vehicule_test("5555TVV")
engine._EP_BADGE.clear()
t1 = now_local().replace(microsecond=0)
for k in range(2):
    verifier_alertes_conduite(db, v5, t1 + timedelta(seconds=60 * k),
                              20.0, False, False, MZ)
check("roule > 3 km/h sans clé MZoneX → alerte SANS_BADGE (CRITIQUE)",
      n_alertes(TypeAlerte.SANS_BADGE, v5.id) == 1)
verifier_alertes_conduite(db, v5, t1 + timedelta(seconds=150), 25.0, False,
                          False, MZ)
check("même épisode de mouvement : pas de seconde alerte",
      n_alertes(TypeAlerte.SANS_BADGE, v5.id) == 1)
verifier_alertes_conduite(db, v5, t1 + timedelta(seconds=200), 22.0, True,
                          False, MZ)
check("badge retrouvé → épisode refermé (et aucune alerte nouvelle)",
      n_alertes(TypeAlerte.SANS_BADGE, v5.id) == 1)
# arrêt > 30 min puis remouvement sans badge → réarmement : nouvelle alerte
verifier_alertes_conduite(db, v5, t1 + timedelta(minutes=45), 0.0, False,
                          False, MZ)
engine._EP_BADGE[v5.id]["dernier_badge"] = None    # clé partie avec l'arrêt
verifier_alertes_conduite(db, v5, t1 + timedelta(minutes=46), 18.0, False,
                          False, MZ)
check("réarmement après 30 min d'arrêt : nouvel épisode → nouvelle alerte",
      n_alertes(TypeAlerte.SANS_BADGE, v5.id) == 2)
n_sb = n_alertes(TypeAlerte.SANS_BADGE, v5.id)
for k in range(3):
    verifier_alertes_conduite(db, v5, t1 + timedelta(minutes=60 + k), 30.0,
                              None, False, SourceEvenement.CAMTRACKPRO)
check("CamtrackPro en direct : JAMAIS d'alerte sans-badge (loi B5 — le "
      "constat se fait au Niveau 2)", n_alertes(TypeAlerte.SANS_BADGE, v5.id)
      == n_sb)

# ------------------------------------------------------------------ E7
print("\n[E7] API /api/conduite (401 sans jeton ; deux vues exactes)")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routers import auth as r_auth, conduite as r_conduite

apptest = FastAPI()
apptest.include_router(r_auth.router)
apptest.include_router(r_conduite.router)
client = TestClient(apptest)
check("401 sans jeton (les deux vues)",
      client.get("/api/conduite/chauffeurs").status_code == 401
      and client.get("/api/conduite/trajets").status_code == 401)
tok = client.post("/api/auth/login",
                  json={"username": "admin", "password": "Admin@2026"}
                  ).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
r = client.get(f"/api/conduite/chauffeurs?du={jour.isoformat()}", headers=H)
check("vue chauffeurs : 200, liste bâtie", r.status_code == 200
      and "chauffeurs" in r.json())
cj = [c for c in r.json()["chauffeurs"]
      if c["chauffeur"].lower() == "joma alexandre"]
check("JOMA : 2 lignes publiées à son nom (9999TZZ + son segment de "
      "7777TXX, v3 AM-2) ; vitesse=5 (4+1) ; autres=1 ; vmax 62.5",
      len(cj) == 1 and cj[0]["trajets"] == 2 and cj[0]["exc_vitesse"] == 5
      and cj[0]["exc_autres"] == 1 and cj[0]["v_max"] == 62.5,
      str(cj))
cr = [c for c in r.json()["chauffeurs"]
      if c["chauffeur"].lower() == "rabeniaina andry jean michael"]
check("RABENIAINA : son segment de 7777TXX + sa ligne de 8888TYY = trajets "
      "=2 ; compteurs de SES lignes seules : vitesse=2, freinage=1",
      len(cr) == 1 and cr[0]["trajets"] == 2 and cr[0]["exc_vitesse"] == 2
      and cr[0]["exc_freinage"] == 1, str(cr))
cs = [c for c in r.json()["chauffeurs"] if "Clé de service" in c["chauffeur"]]
check("« Clé de service » regroupée en ligne dédiée (CamtrackPro ralenti "
      "480 s conservé)", len(cs) == 1 and cs[0]["ralenti_s"] == 480)
check("« /100 km » = infractions ramenées à 100 km (JOMA : 12 points sur "
      "31,5 km → 38,1)",
      cj and abs(cj[0]["pour_100km"] - round(100 * 12 / 31.5, 1)) < 0.2,
      str(cj and cj[0]["pour_100km"]))
r = client.get(f"/api/conduite/trajets?du={jour.isoformat()}&min_total=3",
               headers=H)
tj = r.json()["trajets"] if r.status_code == 200 else []
check("vue trajets noirs : filtre min_total=3 → uniquement lignes chargées "
      "(JOMA 11 / segment RABENIAINA 3)", r.status_code == 200
      and all(x["total"] >= 3 for x in tj)
      and any(x["plaque"] == "9999TZZ" for x in tj)
      and any(x["plaque"] == "7777TXX" for x in tj))

# ------------------------------------------------------------------ fin
db.close()
print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
cible = db_url.replace("sqlite:///", "")
if "/tmp/" in cible and os.path.exists(cible):
    os.remove(cible)
sys.exit(1 if R["ko"] else 0)
