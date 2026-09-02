# -*- coding: utf-8 -*-
"""Tests v1.35 — §0quinquies decies (arbitrages LSS du 25/08/2026) :

I1 : source unique Ym@ne (exterieure=True, source MZONEX, jamais d'écriture
     locale — C3/AM-5 §0nonies confirmés : les lignes locales ne s'affichent
     toujours pas) ;
I2 : filtrage des niveaux — « enregistrement » rejeté à la NORMALISATION
     (jamais en base) ; seuls ALERTE / ALARME passent ;
I3 : les 10 colonnes dictées (Date · Heure · Immatriculation · Chauffeur ·
     Nom · Niveau · Seuil · Coordonnées GPS · Validation · Observation),
     seuil libellé « 90 km/h » (vitesse) / « 04:30 » (conduite), filtres,
     exports = écran — lignes invalidées exclues ;
I4 : workflow NON_TRAITEE → VALIDE / INVALIDE (observation OBLIGATOIRE pour
     invalider), invalidée exclue des totaux/exports, jamais supprimée ni
     masquée, audit infraction.validation (re-décision tracée), droits
     ADMIN+TRACKING (CONSULTATION lecture seule), et SOUVERAINETÉ du
     workflow : une re-collecte ne modifie jamais la décision ;
I5 : upsert idempotent par ymane_id sinon clé naturelle (jour+heure+plaque+
     nom) ; jamais de doublon, jamais de suppression.

RÉALIGNEMENT v1.36 (26/08/2026 — déclaré en CONFORMITÉ) : les fixtures [A]
prennent la forme RÉELLE du portail Ym@ne vérifiée en direct (exceptionid,
levellabel « db_alarm/db_alert/db_recording », parameterlabel « menu_* »,
startdatetime local, threshold verbatim, startgps « [longitude,latitude] »)
— le collecteur est ACTIF par défaut depuis la v1.36, et ce test le
désactive explicitement pour conserver la preuve du no-op à l'arrêt.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v135.db" SIM_ENABLE=0 python3 test_infractions_ymane_v135.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
# .env livre YMANE_ACTIVE=1 depuis la v1.36 : forcer "0" AVANT tout import
# applicatif (load_dotenv n'écrase jamais une variable déjà posée) pour
# prouver le no-op à l'arrêt — cas [G].
os.environ["YMANE_ACTIVE"] = "0"
import sys
from datetime import datetime

from sqlalchemy import delete, func, inspect, select, text

from app.database import SessionLocal
from app import engine
from app.api_ymane import ApiYmane, actif
from app.models import (AuditLog, Conducteur, Infraction, StatutConducteur,
                        TypeInfraction, Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.serializers import s_infraction
from app.ymane_import import cycle_ymane, importer_infractions_ymane

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

if db_url.startswith("sqlite:///"):            # leçon « base /tmp périmée »
    try:                                       # SUPPRESSION AVANT seed_si_vide
        os.remove(db_url.replace("sqlite:///", "/", 1))
    except OSError:
        pass

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
migrer_schema()                                # I5 : migration idempotente
db = SessionLocal()

for modele in (Infraction, AuditLog):
    db.execute(delete(modele))
db.commit()

MAINTENANT = datetime(2026, 8, 25, 12, 0)

# ---------------------------------------------------------------- [A] I2
print("\n[A] I2 — filtrage des niveaux à la normalisation (jamais en base)")
# Forme RÉELLE constatée le 26/08/2026 (branchement gravé I5, v1.36) :
# levellabel db_alarm/db_alert/db_recording · parameterlabel « menu_* » ·
# startdatetime local · threshold verbatim · startgps « [longitude,latitude] »
BRUT_VITESSE = {"exceptionid": 6150001, "exceptiontype": 1,
                "vehiclename": "8076 TCB (LSS)", "drivername": "RABÉ Jean Dina",
                "startdatetime": "2026-08-25 10:22:35",
                "enddatetime": "2026-08-25 10:24:00",
                "parameter": "Speeding", "parameterlabel": "menu_speeding",
                "totalduration": 0.025, "level": "Alarm",
                "levellabel": "db_alarm", "threshold": "90.00",
                "maxvalue": 112.0, "distanceunderexception": 0.85,
                "startgps": "[47.5079,-18.8792]"}
BRUT_ENREG = {"exceptionid": 6150002, "exceptiontype": 1,
              "vehiclename": "8076 TCB (LSS)",
              "startdatetime": "2026-08-25 10:25:00",
              "parameter": "Speeding", "parameterlabel": "menu_speeding",
              "level": "Recording", "levellabel": "db_recording",
              "threshold": "90.00", "maxvalue": 95.0}
BRUT_CONDUITE = {"exceptionid": 6150003, "exceptiontype": 5,
                 "vehiclename": "0576 TCD (LSS)",
                 "drivername": "Conducteur Inconnu",
                 "startdatetime": "2026-08-25 14:05:12",
                 "enddatetime": "2026-08-25 18:47:12",
                 "parameter": "ContinuousDrive",
                 "parameterlabel": "menu_continuous_driving",
                 "totalduration": 4.7, "timeexceeded": 0.2,
                 "level": "Alert", "levellabel": "db_alert",
                 "threshold": "4.50", "maxvalue": None,
                 "startgps": "[47.1000,-18.9000]"}

norm_v = ApiYmane.normaliser(BRUT_VITESSE)
norm_e = ApiYmane.normaliser(BRUT_ENREG)
norm_c = ApiYmane.normaliser(BRUT_CONDUITE)
check("I2 : « enregistrement » rejeté par normaliser → None (jamais en base)",
      norm_e is None)
check("I2 : « alarme » → ALARME ; « alerte » → ALERTE",
      norm_v and norm_v["niveau"] == "ALARME" and norm_c
      and norm_c["niveau"] == "ALERTE")
check("I5/arrêt manuel : YMANE_ACTIVE=0 → collecteur inactif (coupure "
      "opposable à tout instant)", actif() is False)

# ------------------------------------------------------- préparation référentiel
conducteur = Conducteur(nom_prenom="RABE Jean Dina", prenom_usuel="Dina",
                        matricule="TST135", statut=StatutConducteur.ACTIF)
db.add(conducteur)
db.commit()
v_8076 = db.scalar(select(Vehicule).where(Vehicule.plaque == "8076TCB"))
v_0576 = db.scalar(select(Vehicule).where(Vehicule.plaque == "0576TCD"))
check("Référentiel : véhicules seed 8076TCB / 0576TCD présents",
      v_8076 is not None and v_0576 is not None)

# ---------------------------------------------------------------- [B] import I1
print("\n[B] I1 — import des lignes ALERTE/ALARME (externes, source MZONEX)")
avant_audit = db.scalar(select(func.count(AuditLog.id))) or 0
stats = importer_infractions_ymane(db, [norm_v, norm_c], maintenant=MAINTENANT)
db.commit()
lignes = db.scalars(select(Infraction).where(
    Infraction.exterieure.is_(True)).order_by(Infraction.heure)).all()
check("Import : 2 reçues → 2 nouvelles, 0 ignorée",
      stats["recus"] == 2 and stats["nouvelles"] == 2 and stats["ignores"] == 0,
      str(stats))
lig_v, lig_c = lignes[0], lignes[1]
check("I1 : lignes extérieures (exterieure=True), source MZONEX, niveaux "
      "ALARME/ALERTE stockés",
      len(lignes) == 2 and lig_v.source.value == "MZONEX"
      and lig_v.niveau == "ALARME" and lig_c.niveau == "ALERTE")
check("I1 : véhicule résolu via plaque publiée « 8076 TCB (LSS) » → 8076TCB",
      lig_v.vehicule_id == v_8076.id and lig_v.vehicule.plaque == "8076TCB")
check("Conducteur retrouvé par nom (insensible casse/accents) : RABÉ → RABE",
      lig_v.conducteur_id == conducteur.id)
check("Chauffeur inconnu → conducteur_id NULL + chauffeur_brut conservé",
      lig_c.conducteur_id is None and lig_c.chauffeur_brut == "Conducteur Inconnu")
check("I3 : vitesse → EXCES_VITESSE, seuil 90 km/h, libellé « 90 km/h »",
      lig_v.type == TypeInfraction.EXCES_VITESSE
      and lig_v.seuil_unite == "kmh"
      and s_infraction(lig_v)["seuil_libelle"] == "90 km/h")
check("I3 : conduite → DEPASSEMENT_TCC, seuil 16200 s, libellé « 04:30 »",
      lig_c.type == TypeInfraction.DEPASSEMENT_TCC
      and lig_c.seuil_unite == "s"
      and s_infraction(lig_c)["seuil_libelle"] == "04:30")
check("I3 : coordonnées GPS présentes pour la vitesse (format « lat, lng »)",
      s_infraction(lig_v)["coordonnees"] == "-18.87920, 47.50790")
check("I3 : nom publié = libellé FR OFFICIEL du portail (menu_speeding → "
      "« Excès de Vitesse » · menu_continuous_driving → « Conduite Continue »)",
      lig_v.nom_ymane == "Excès de Vitesse"
      and s_infraction(lig_c)["nom"] == "Conduite Continue")
check("I4 : les deux lignes arrivent NON TRAITÉES",
      lig_v.validation == "NON_TRAITEE" and lig_c.validation == "NON_TRAITEE")
apres_audit = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "infraction.import_ymane")) or 0
check("Audit : une trace « infraction.import_ymane » par passe avec items",
      apres_audit >= 1)

# ---------------------------------------------------- [C] idempotence I5
print("\n[C] I5 — upsert idempotent : jamais de doublon, maj ciblée")
stats2 = importer_infractions_ymane(db, [norm_v, norm_c], maintenant=MAINTENANT)
db.commit()
total = db.scalar(select(func.count(Infraction.id)).where(
    Infraction.exterieure.is_(True)))
check("I5 : rejeu à l'identique → 0 nouvelle, 0 maj, total inchangé (2)",
      stats2["nouvelles"] == 0 and stats2["maj"] == 0 and total == 2, str(stats2))
norm_v2 = dict(norm_v, valeur=118)
stats3 = importer_infractions_ymane(db, [norm_v2], maintenant=MAINTENANT)
db.commit()
lig_v2 = db.scalar(select(Infraction).where(Infraction.ymane_id == "6150001"))
check("I5 : même ymane_id, valeur changée → 1 maj, 0 nouvelle, valeur 118",
      stats3["maj"] == 1 and stats3["nouvelles"] == 0
      and lig_v2.valeur_mesuree == 118.0, str(stats3))
# clé naturelle : import d'abord SANS identifiant externe…
norm_nat = dict(norm_c, ymane_id=None, nom="Pause non prise (> 20 min)",
                heure_txt="16:40:00", valeur=None, seuil=1200)
stats4 = importer_infractions_ymane(db, [norm_nat], maintenant=MAINTENANT)
db.commit()
# …puis le MÊME événement avec un identifiant externe → rattachement, pas de doublon
norm_nat_id = dict(norm_nat, ymane_id="ym-4", valeur=1500)
stats5 = importer_infractions_ymane(db, [norm_nat_id], maintenant=MAINTENANT)
db.commit()
lig_nat = db.scalar(select(Infraction).where(
    Infraction.nom_ymane == "Pause non prise (> 20 min)"))
check("I5 : clé naturelle (jour+heure+plaque+nom) → rattachement du ymane_id, "
      "aucun doublon", stats4["nouvelles"] == 1 and stats5["nouvelles"] == 0
      and lig_nat.ymane_id == "ym-4" and lig_nat.valeur_mesuree == 1500.0)
# doublon DANS la même passe
stats6 = importer_infractions_ymane(
    db, [dict(norm_v, ymane_id="ym-9", heure_txt="18:01:00"),
         dict(norm_v, ymane_id="ym-9", heure_txt="18:01:00")],
    maintenant=MAINTENANT)
db.commit()
check("I5 : doublon dans la même passe → 1 nouvelle + 1 doublon compté",
      stats6["nouvelles"] == 1 and stats6["doublons"] == 1, str(stats6))
# véhicule inconnu → reporté au cycle suivant, jamais inventé
stats7 = importer_infractions_ymane(
    db, [dict(norm_v, ymane_id="ym-404", plaque_brute="9999ZZZ (LSS)",
              heure_txt="19:00:00")], maintenant=MAINTENANT)
db.commit()
check("I5/I1 : véhicule inconnu « 9999ZZZ » → 0 création, compté sans_vehicule",
      stats7["sans_vehicule"] == 1 and stats7["nouvelles"] == 0, str(stats7))
# item sans heure → écarté, jamais deviné
stats8 = importer_infractions_ymane(
    db, [dict(norm_v, ymane_id="ym-405", heure_txt=None, date_txt="illISIBLE")],
    maintenant=MAINTENANT)
db.commit()
check("I5 : date/heure illisible → écarté (ignores=1), jamais placé au hasard",
      stats8["ignores"] == 1 and stats8["nouvelles"] == 0, str(stats8))
total_apres_imports = db.scalar(select(func.count(Infraction.id)).where(
    Infraction.exterieure.is_(True)))
check("I5 : total externe après toutes les passes = 4 (aucun doublon)",
      total_apres_imports == 4, str(total_apres_imports))

# --------------------------------------------- [D] endpoints — workflow I4
print("\n[D] I3/I4 — API : vitre, filtres, compteurs, validation (droits)")
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)


def jeton(username, password):
    r = client.post("/api/auth/login",
                    json={"username": username, "password": password})
    return ({"Authorization": f"Bearer {r.json()['access_token']}"}
            if r.status_code == 200 else None)


h_admin = jeton("admin", "Admin@2026")
h_track = jeton("tracking", "Tracking@2026")
h_cons = jeton("consultation", "Consult@2026")
check("JWT : admin / tracking / consultation connectés",
      all((h_admin, h_track, h_cons)))

r = client.get("/api/infractions", headers=h_cons)
j = r.json() if r.status_code == 200 else {}
check("I3 : GET /api/infractions (CONSULTATION) → 200, 4 lignes, compteurs",
      r.status_code == 200 and j.get("total") == 4
      and j.get("compteurs", {}).get("non_traitees") == 4,
      f"{r.status_code} {j.get('total')}")
check("I3 : chaque item porte les clés I3/I4 (nom, niveau, seuil_libelle, "
      "coordonnees, validation, observation)",
      all(k in j["items"][0] for k in ("nom", "niveau", "seuil_libelle",
                                       "coordonnees", "validation",
                                       "observation", "ymane_id")) if j.get("items") else False)
r_f = client.get("/api/infractions?niveau=ALERTE", headers=h_cons)
check("I3 : filtre niveau=ALERTE → seules les 2 alertes",
      r_f.status_code == 200 and r_f.json()["total"] == 2,
      str(r_f.json().get("total")))
r_v = client.get(f"/api/infractions?vehicule_id={v_8076.id}", headers=h_cons)
check("I3 : filtre véhicule → les lignes 8076TCB seules (2)",
      r_v.status_code == 200 and r_v.json()["total"] == 2,
      str(r_v.json().get("total")))

iid_c = lig_c.id          # ligne « conduite » (alerte, chauffeur inconnu)
iid_v = lig_v.id          # ligne « vitesse » (alarme)
r403 = client.post(f"/api/infractions/{iid_c}/validation", headers=h_cons,
                   json={"decision": "VALIDE"})
check("I4 : CONSULTATION ne peut pas valider → 403",
      r403.status_code == 403, str(r403.status_code))
r400 = client.post(f"/api/infractions/{iid_c}/validation", headers=h_track,
                   json={"decision": "INVALIDE"})
check("I4 : INVALIDE sans observation → 400 (observation obligatoire)",
      r400.status_code == 400, str(r400.status_code))
rok = client.post(f"/api/infractions/{iid_c}/validation", headers=h_track,
                  json={"decision": "VALIDE"})
jj = rok.json() if rok.status_code == 200 else {}
check("I4 : VALIDE (TRACKING) → 200, validee_par=tracking, validee_le posé",
      rok.status_code == 200 and jj.get("validation") == "VALIDE"
      and jj.get("validee_par") == "tracking" and bool(jj.get("validee_le")),
      f"{rok.status_code} {jj.get('validation')}")
rinv = client.post(f"/api/infractions/{iid_v}/validation", headers=h_admin,
                   json={"decision": "INVALIDE",
                         "observation": "Camion en atelier, boîtier déclenché "
                                        "au démarrage"})
jinv = rinv.json() if rinv.status_code == 200 else {}
check("I4 : INVALIDE (ADMIN) + observation → 200, observation conservée",
      rinv.status_code == 200 and jinv.get("validation") == "INVALIDE"
      and "atelier" in (jinv.get("observation") or ""),
      f"{rinv.status_code} {jinv.get('validation')}")
audits_v = db.scalars(select(AuditLog).where(
    AuditLog.action == "infraction.validation",
    AuditLog.entite_id == iid_v).order_by(AuditLog.date_heure)).all()
check("I4 : audit « infraction.validation » complet (avant NON_TRAITEE → "
      "après INVALIDE, observation, par)", len(audits_v) == 1
      and audits_v[0].details["avant"]["validation"] == "NON_TRAITEE"
      and audits_v[0].details["apres"]["validation"] == "INVALIDE"
      and "atelier" in (audits_v[0].details["apres"]["observation"] or ""),
      str(len(audits_v)))
r404 = client.post("/api/infractions/id-inconnu/validation", headers=h_admin,
                   json={"decision": "VALIDE"})
check("I4 : ligne inconnue → 404", r404.status_code == 404,
      str(r404.status_code))
# re-décision : INVALIDE → VALIDE, tracée
r_re = client.post(f"/api/infractions/{iid_v}/validation", headers=h_admin,
                   json={"decision": "VALIDE"})
audits_v2 = db.scalars(select(AuditLog).where(
    AuditLog.action == "infraction.validation",
    AuditLog.entite_id == iid_v)).all()
# l'API écrit via SA PROPRE session : rafraîchir la session de test
lig_re = db.get(Infraction, iid_v)
db.refresh(lig_re)
check("I4 : re-décision INVALIDE → VALIDE possible et tracée (2 audits, "
      "observation effacée)", r_re.status_code == 200 and len(audits_v2) == 2
      and lig_re.validation == "VALIDE" and lig_re.observation is None,
      f"audits={len(audits_v2)} validation={lig_re.validation} "
      f"obs={lig_re.observation!r}")
lig_re.validation = "INVALIDE"           # on repasse en INVALIDE pour la suite
lig_re.observation = "Confirmée erronée (re-vérification)"
lig_re.validee_par = "admin"
db.commit()

r_c = client.get("/api/infractions", headers=h_cons).json()["compteurs"]
check("I4 : compteurs — 1 invalidée ; comptabilisées = total − invalidées (3)",
      r_c["invalidees"] == 1 and r_c["comptabilisees"] == 3
      and r_c["validees"] == 1, str(r_c))
r_fv = client.get("/api/infractions?validation=INVALIDE", headers=h_cons).json()
check("I4 : filtre état de validation (écran : l'invalidée N'EST PAS masquée)",
      r_fv["total"] == 1 and r_fv["items"][0]["validation"] == "INVALIDE")

# C3 intact : une ligne LOCALE (exterieure=False) ne s'affiche jamais
locale = Infraction(date_jour=MAINTENANT.date(), heure=MAINTENANT.time(),
                    vehicule_id=v_8076.id, type=TypeInfraction.EXCES_VITESSE,
                    gravite=lig_v.gravite, source=lig_v.source,
                    exterieure=False, niveau="ALARME",
                    nom_ymane="Locale historique (audit)")
db.add(locale)
db.commit()
tot_affiche = client.get("/api/infractions", headers=h_cons).json()["total"]
check("I1/C3 : ligne locale exterieure=False → JAMAIS affichée (total = 4)",
      tot_affiche == 4, str(tot_affiche))

# ---------------------------------------------------- [E] exports I3/I4
print("\n[E] I3/I4 — exports Excel/PDF = écran, invalidées exclues (§A.2)")
rx = client.get("/api/infractions/export.xlsx", headers=h_admin)
rp = client.get("/api/infractions/export.pdf", headers=h_admin)
check("Exports : xlsx et pdf → 200 (bytes non vides)",
      rx.status_code == 200 and len(rx.content) > 500
      and rp.status_code == 200 and len(rp.content) > 500)
from app.routers.surveillance import HEADERS_INF, _lignes_infractions
check("I3 : en-têtes d'export = les 12 colonnes dictées (I3 amendé §0octies "
      "decies L2/L3, 27/08/2026)",
      HEADERS_INF == ["Date", "Heure", "Immatriculation", "Chauffeur",
                      "Infraction", "Niveau", "Seuil", "Coordonnées GPS",
                      "Début de l'infraction", "Fin de l'infraction",
                      "Validation", "Observation"])
exportables = db.scalars(select(Infraction).where(
    Infraction.exterieure.is_(True),
    Infraction.validation != "INVALIDE")).all()
lignes_export = _lignes_infractions([s_infraction(i) for i in exportables])
check("I4 : périmètre exportable = 3 lignes (invalidée exclue) — 12 colonnes",
      len(exportables) == 3
      and all(len(l) == 12 for l in lignes_export))
cles_export = {(l[0], l[1], l[2]) for l in lignes_export}   # date, heure, plaque
check("I4 : l'infraction invalidée (vitesse 8076TCB à 10:22:35) absente de "
      "l'export — une ligne VALIDE de MÊME NOM (10:22→valide non, 18:01)"
      " reste présente",
      ("25/08/2026", "10:22:35", "8076TCB") not in cles_export
      and ("25/08/2026", "18:01:00", "8076TCB") in cles_export,
      str(sorted(cles_export)))

# ----------------------------------- [F] souveraineté du workflow (I4)
print("\n[F] I4 — une re-collecte ne touche JAMAIS la décision")
stats_re = importer_infractions_ymane(
    db, [dict(norm_v, valeur=121), norm_c], maintenant=MAINTENANT)
db.commit()
lig_v3 = db.get(Infraction, iid_v)
lig_c3 = db.get(Infraction, iid_c)
# expire_on_commit=False : rafraîchir explicitement les lectures de contrôle
db.refresh(lig_v3)
db.refresh(lig_c3)
check("I4 : re-collecte maj la donnée (121) mais INVALIDE + observation + "
      "validee_par INTACTS",
      lig_v3.valeur_mesuree == 121.0 and lig_v3.validation == "INVALIDE"
      and "erronée" in (lig_v3.observation or "")
      and lig_v3.validee_par == "admin")
check("I4 : re-collecte de la ligne VALIDÉE → décision (VALIDE) conservée",
      lig_c3.validation == "VALIDE" and lig_c3.validee_par == "tracking")

# ----------------------------------- [G] cycle I5 livré désactivé
print("\n[G] I5 — cycle_ymane() no-op tant que YMANE_ACTIVE=0")
avant = db.scalar(select(func.count(Infraction.id))) or 0
avant_tr = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "infraction.import_ymane")) or 0
res_cycle = cycle_ymane()
apres = db.scalar(select(func.count(Infraction.id))) or 0
apres_tr = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "infraction.import_ymane")) or 0
check("I5 : cycle_ymane() → {'actif': False}, zéro écriture, zéro audit",
      res_cycle == {"actif": False} and avant == apres
      and avant_tr == apres_tr, str(res_cycle))

# ----------------------------------- [H] schéma (migration v1.35)
print("\n[H] Migration v1.35/v1.36 — colonnes infractions présentes et "
      "idempotentes")
cols = {c["name"] for c in inspect(db.get_bind()).get_columns("infractions")}
attendues = {"niveau", "nom_ymane", "chauffeur_brut", "ymane_id", "seuil_unite",
             "seuil_texte", "validation", "observation", "validee_par",
             "validee_le"}
check("Les 10 colonnes §0quinquies decies existent après migrer_schema()",
      attendues <= cols, str(attendues - cols))

# [H2] chemin RÉEL de migration : une base « héritée v1.34 » (sans les 10
# colonnes — cas de l'exploitant) doit les RECEVOIR par ALTER TABLE au
# démarrage, sans perdre une ligne (jamais de suppression de données).
print("\n[H2] Migration sur base « héritée v1.34 » (ALTER TABLE réel)")
nb_avant = db.scalar(select(func.count(Infraction.id))) or 0
with db.get_bind().begin() as cx:                 # purge hors ORM (schéma seul)
    cx.execute(text("DROP INDEX IF EXISTS ix_infractions_ymane_id"))
    for col in sorted(attendues):
        cx.execute(text(f"ALTER TABLE infractions DROP COLUMN {col}"))
cols_heritee = {c["name"] for c in inspect(db.get_bind()).get_columns("infractions")}
check("Base héritée simulée : les 10 colonnes sont bien absentes",
      not (attendues & cols_heritee), str(attendues & cols_heritee))
migrer_schema()                                   # = ce que fait le démarrage
insp_migree = inspect(db.get_bind())
cols_migree = {c["name"] for c in insp_migree.get_columns("infractions")}
idx_migree = {i["name"] for i in insp_migree.get_indexes("infractions")}
check("Migration v1.35/v1.36 : les 10 colonnes sont RECRÉÉES sur la base "
      "héritée", attendues <= cols_migree, str(attendues - cols_migree))
check("Migration v1.35/v1.36 : l'index ymane_id est (re)posé — idempotent",
      "ix_infractions_ymane_id" in idx_migree, str(idx_migree))
nb_apres = db.scalar(select(func.count(Infraction.id))) or 0
nb_hors_defaut = db.scalar(select(func.count(Infraction.id)).where(
    Infraction.validation != "NON_TRAITEE")) or 0
check("Migration v1.35/v1.36 : AUCUNE ligne perdue (jamais de suppression) — "
      "toutes relues au défaut « NON_TRAITEE » (cas d'une vraie base v1.34)",
      nb_apres == nb_avant and nb_hors_defaut == 0,
      f"{nb_avant}→{nb_apres}, hors_défaut={nb_hors_defaut}")
# comportement post-migration : la vitre et le workflow refonctionnent de suite
stats_post = importer_infractions_ymane(
    db, [dict(norm_v, ymane_id="ym-post", heure_txt="20:15:00")],
    maintenant=MAINTENANT)
db.commit()
check("Migration v1.35/v1.36 : import Ym@ne opérationnel juste après l'ALTER",
      stats_post["nouvelles"] == 1, str(stats_post))

print(f"\n{'=' * 64}\n===== test_infractions_ymane_v135 : {R['ok']} OK / {R['ko']} KO =====\n{'=' * 64}")
db.close()
try:
    if db_url.startswith("sqlite:///"):
        os.remove(db_url.replace("sqlite:///", "/", 1))
        print("Base de test supprimée.")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
