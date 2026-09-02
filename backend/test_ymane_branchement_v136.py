# -*- coding: utf-8 -*-
"""Tests v1.36/v1.37 — §0quinquies decies I5, EXÉCUTION du 26/08/2026 :
activation réelle du collecteur Ym@ne (branchement GRAVÉ après vérification
en direct avec le compte LSS), + AMENDEMENT A-I5 du 26/08 (v1.37, arbitrage
direct exploitant, inscrit dans la loi avant codage) : fenêtre de relecture
J-1→J **ABROGÉE → J-8→J à chaque cycle** — le traitement d'une infraction
par Ym@ne peut dépasser 12 h ; l'anti-doublon par exceptionid rend la
relecture sans effet.

Preuves visées :
[A] Normalisation des lignes RÉELLES du rapport « detailedexceptionreport »
    (relevées verbatim les 24-25/08/2026) : 7 familles, niveaux
    db_alarm/db_alert/db_recording, seuil VERBATIM (km/h · heures
    décimales → secondes · plage horaire brute « 18:00:00 to 05:30:00 »),
    GPS « [longitude,latitude] » → affichage « latitude, longitude »,
    type/gravité dérivés — Recording rejeté ICI (I2, jamais en base) ;
[B] « Seuil » affiché à l'écran/export (s_infraction) : « 25 km/h » ·
    « 04:30 » · « 09:00 » · « 10:00 » · « 6.00 » · « 18:00:00 to 05:30:00 »
    — jamais d'unité inventée ;
[C] Import des lignes réelles : véhicules résolus via « 4526 TCC (LSS) »…,
    date/heure = startdatetime local JAMAIS converti, idempotence par
    exceptionid, compteurs exacts, audit ;
[D] cycle_ymane ACTIF avec session Ym@ne factice : POST /login/ (errorCode
    200 + identité 2004), garde /isvalidaccess, UNE re-connexion sur HTTP
    500 puis nouvelle tentative, fenêtre J-8→J (A-I5 ; vehicleid=0,
    exceptiontype=0, exceptionlevel=0 — filtre I2 souverain à l'import),
    rattrapage d'une ligne tardive datée J-8, refus d'ouverture opposable.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v136.db" SIM_ENABLE=0 python3 test_ymane_branchement_v136.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
os.environ["YMANE_ACTIVE"] = "0"        # posée AVANT import (dotenv n'écrase
import sys                              # pas) ; réactivée cas par cas ci-dessous
from datetime import date, datetime, time, timedelta

import urllib.error
from sqlalchemy import delete, func, select

from app.database import SessionLocal
from app import engine
from app.api_ymane import ApiYmane, YmaneDesactive, actif
from app.config import now_local
from app.models import AuditLog, GraviteInfraction, Infraction, TypeInfraction
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
db = SessionLocal()
for modele in (Infraction, AuditLog):
    db.execute(delete(modele))
db.commit()

MAINTENANT = datetime(2026, 8, 25, 12, 0)

# ============================================================ lignes RÉELLES
# Relevées verbatim sur https://bi.camtrack.pro/detailedexceptionreport
# (compte LSS, clientid=1 affiliateid=1 transporterid=2004) les 24-25/08/2026.
BRUT_NUIT = {"exceptionid": 6152059, "exceptiontype": 4,
             "transportername": "LSS (LPSA)",
             "vehiclename": "4526 TCC (LSS)",
             "drivername": "ANDRIANASOLO Maminiaina eddy",
             "startdatetime": "2026-08-23 18:00:00",
             "enddatetime": "2026-08-23 19:01:56",
             "parameter": "NightDrive", "parameterlabel": "menu_night_driving",
             "totalduration": None, "level": "Alarm",
             "levellabel": "db_alarm",
             "threshold": "18:00:00 to 05:30:00", "maxvalue": None,
             "distanceunderexception": 32.267, "timeexceeded": 1.03222,
             "startgps": "[49.1165,-18.5497]",
             "endgps": "[49.0324,-18.8153]", "rollingdays": 0}
BRUT_VITESSE = {"exceptionid": 6153001, "exceptiontype": 1,
                "vehiclename": "8076 TCB (LSS)", "drivername": "RABÉ Jean Dina",
                "startdatetime": "2026-08-24 07:41:20",
                "enddatetime": "2026-08-24 07:42:05",
                "parameter": "Speeding", "parameterlabel": "menu_speeding",
                "totalduration": 0.0125, "level": "Alarm",
                "levellabel": "db_alarm", "threshold": "25.00",
                "maxvalue": 33.0, "distanceunderexception": 0.31,
                "startgps": "[47.5079,-18.8792]"}
BRUT_COND_CONTINUE = {"exceptionid": 6153002, "exceptiontype": 5,
                      "vehiclename": "0926 TBV (LSS)",
                      "drivername": "RAKOTO Nirina",
                      "startdatetime": "2026-08-24 05:12:00",
                      "enddatetime": "2026-08-24 17:27:02",
                      "parameter": "ContinuousDrive",
                      "parameterlabel": "menu_continuous_driving",
                      "totalduration": 12.250556, "timeexceeded": 7.75056,
                      "level": "Alarm", "levellabel": "db_alarm",
                      "threshold": "4.50", "maxvalue": None,
                      "startgps": "[47.2841,-18.9344]"}
BRUT_ACCEL = {"exceptionid": 6153003, "exceptiontype": 2,
              "vehiclename": "5156 TBV (LSS)",
              "startdatetime": "2026-08-24 09:03:11",
              "parameter": "Acceleration",
              "parameterlabel": "menu_harsh_acceleration",
              "totalduration": 0.001, "level": "Alert",
              "levellabel": "db_alert", "threshold": "6.00",
              "maxvalue": 15.0, "startgps": "[47.4102,-18.9011]"}
BRUT_FREIN = {"exceptionid": 6153004, "exceptiontype": 3,
              "vehiclename": "5186 TBV (LSS)",
              "startdatetime": "2026-08-24 15:36:44",
              "parameter": "HarshBrake",
              "parameterlabel": "menu_harsh_braking",
              "totalduration": 0.001, "level": "Alert",
              "levellabel": "db_alert", "threshold": "10.00",
              "maxvalue": 16.0, "startgps": "[47.1003,-18.7802]"}
BRUT_COND_JOUR = {"exceptionid": 6153005, "exceptiontype": 6,
                  "vehiclename": "3056 TBS (LSS)",
                  "startdatetime": "2026-08-24 06:00:00",
                  "enddatetime": "2026-08-24 17:02:05",
                  "parameter": "DailyDrive",
                  "parameterlabel": "menu_daily_driving",
                  "totalduration": 11.034722, "timeexceeded": 1.034722,
                  "level": "Alarm", "levellabel": "db_alarm",
                  "threshold": "10.00", "maxvalue": None,
                  "startgps": "[47.0512,-18.8123]"}
BRUT_REPOS_JOUR = {"exceptionid": 6153006, "exceptiontype": 7,
                   "vehiclename": "4006 TBS (LSS)",
                   "startdatetime": "2026-08-24 20:00:00",
                   "enddatetime": "2026-08-25 03:45:36",
                   "parameter": "DailyRest",
                   "parameterlabel": "menu_daily_rest_time",
                   "totalduration": 7.76, "timeexceeded": 0,
                   "level": "Alarm", "levellabel": "db_alarm",
                   "threshold": "9.00", "maxvalue": None,
                   "startgps": "[47.2210,-18.6507]"}
BRUT_ENREG_NUIT = {"exceptionid": 6153007, "exceptiontype": 4,
                   "vehiclename": "0926 TBV (LSS)",
                   "startdatetime": "2026-08-24 22:10:00",
                   "parameter": "NightDrive",
                   "parameterlabel": "menu_night_driving",
                   "level": "Recording", "levellabel": "db_recording",
                   "threshold": "18:00:00 to 05:30:00"}
BRUT_ENREG_VITESSE = {"exceptionid": 6153008, "exceptiontype": 1,
                      "vehiclename": "5156 TBV (LSS)",
                      "startdatetime": "2026-08-24 11:00:00",
                      "parameter": "Speeding",
                      "parameterlabel": "menu_speeding",
                      "level": "Recording", "levellabel": "db_recording",
                      "threshold": "90.00", "maxvalue": 94.0}
BRUTS_REELS = [BRUT_NUIT, BRUT_VITESSE, BRUT_COND_CONTINUE, BRUT_ACCEL,
               BRUT_FREIN, BRUT_COND_JOUR, BRUT_REPOS_JOUR,
               BRUT_ENREG_NUIT, BRUT_ENREG_VITESSE]

# ------------------------------------------------------------ [A] normaliser
print("\n[A] Normalisation des familles RÉELLES (verbatim 24-25/08/2026)")
n_nuit = ApiYmane.normaliser(BRUT_NUIT)
n_vit = ApiYmane.normaliser(BRUT_VITESSE)
n_cc = ApiYmane.normaliser(BRUT_COND_CONTINUE)
n_acc = ApiYmane.normaliser(BRUT_ACCEL)
n_frein = ApiYmane.normaliser(BRUT_FREIN)
n_cj = ApiYmane.normaliser(BRUT_COND_JOUR)
n_rj = ApiYmane.normaliser(BRUT_REPOS_JOUR)

check("I2 : les DEUX lignes « Recording » (db_recording) rejetées → None, "
      "jamais en base",
      ApiYmane.normaliser(BRUT_ENREG_NUIT) is None
      and ApiYmane.normaliser(BRUT_ENREG_VITESSE) is None)
check("Niveaux : db_alarm → ALARME ; db_alert → ALERTE (levellabel LU "
      "d'abord, repli sur level)",
      n_nuit["niveau"] == "ALARME" and n_acc["niveau"] == "ALERTE")
check("Niveau inconnu (« db_info ») → rejet prudent (jamais deviné)",
      ApiYmane.normaliser(dict(BRUT_VITESSE, levellabel="db_info",
                               level="Info")) is None)
check("Noms FR OFFICIELS du portail (menu_*) : nuit/continue/journalière/"
      "repos/accélération/freinage",
      n_nuit["nom"] == "Conduite de Nuit"
      and n_cc["nom"] == "Conduite Continue"
      and n_cj["nom"] == "Conduite Journalière"
      and n_rj["nom"] == "Temps de Repos Journalier"
      and n_acc["nom"] == "Accélération Brusque"
      and n_frein["nom"] == "Freinage Brusque"
      and n_vit["nom"] == "Excès de Vitesse")
check("Vitesse : seuil « 25.00 » → 25.0 kmh (kmh : pas de texte verbatim), "
      "mesuré = maxvalue 33",
      n_vit["seuil"] == 25.0 and n_vit["seuil_unite"] == "kmh"
      and n_vit["seuil_texte"] is None and n_vit["valeur"] == 33.0)
check("Conduite de nuit : seuil = PLAGE VERBATIM « 18:00:00 to 05:30:00 » "
      "(brut, jamais d'unité inventée), référence numérique vide",
      n_nuit["seuil_unite"] == "brut"
      and n_nuit["seuil_texte"] == "18:00:00 to 05:30:00"
      and n_nuit["seuil"] is None)
check("Heures décimales → SECONDES : « 4.50 » h → 16200 s (continue) ; "
      "« 10.00 » → 36000 s (journalière) ; « 9.00 » → 32400 s (repos)",
      n_cc["seuil"] == 16200.0 and n_cc["seuil_unite"] == "s"
      and n_cj["seuil"] == 36000.0 and n_rj["seuil"] == 32400.0)
check("Seuils bruts VERBATIM : accélération « 6.00 » · freinage « 10.00 » "
      "(indices constructeur, affichés tels quels)",
      n_acc["seuil_texte"] == "6.00" and n_acc["seuil_unite"] == "brut"
      and n_frein["seuil_texte"] == "10.00")
check("Mesuré = totalduration×3600 pour les familles horaires (12.250556 h "
      "≈ 44102 s pour la continue) ; maxvalue sinon",
      n_cc["valeur"] and abs(n_cc["valeur"] - 44102.0) < 1.0
      and n_cc["duree_s"] == 44102 and n_acc["valeur"] == 15.0
      and abs(n_rj["valeur"] - 27936.0) < 1.0)
check("GPS Ym@ne = « [longitude,latitude] » → lat −18.5497 / lng 49.1165 "
      "(ORDRE INVERSÉ corrigé à l'affichage)",
      n_nuit["lat"] == -18.5497 and n_nuit["lng"] == 49.1165)
check("Distance sous exception conservée (32.267 km sur la nuit réelle)",
      n_nuit["distance_km"] == 32.267)
check("Date/heure = startdatetime LOCAL, jamais converti (23/08 18:00:00)",
      n_nuit["date_txt"] == "2026-08-23" and n_nuit["heure_txt"] == "18:00:00")
check("Type dérivé (icône/filtre) : vitesse / TCC (nuit, continue) / TCJ "
      "(journalière) / TTJ (repos) / accélération / freinage",
      n_vit["type_code"] == "EXCES_VITESSE"
      and n_nuit["type_code"] == "DEPASSEMENT_TCC"
      and n_cc["type_code"] == "DEPASSEMENT_TCC"
      and n_cj["type_code"] == "DEPASSEMENT_TCJ"
      and n_rj["type_code"] == "DEPASSEMENT_TTJ"
      and n_acc["type_code"] == "ACCELERATION_BRUSQUE"
      and n_frein["type_code"] == "FREINAGE_BRUSQUE")
check("exceptionid → ymane_id (chaîne)", n_nuit["ymane_id"] == "6152059")

# ------------------------------------------- [B/C] import + seuils affichés
print("\n[B/C] Import des lignes réelles + colonne « Seuil » (écran = export)")
normes = [n for n in (n_nuit, n_vit, n_cc, n_acc, n_frein, n_cj, n_rj)
          if n is not None]
stats = importer_infractions_ymane(db, normes, maintenant=MAINTENANT)
db.commit()
check("Import : 7 lignes admissibles → 7 nouvelles, 0 ignorée, 0 sans "
      "véhicule (plaques « 4526 TCC (LSS) »… résolues)",
      stats["recus"] == 7 and stats["nouvelles"] == 7
      and stats["ignores"] == 0 and stats["sans_vehicule"] == 0, str(stats))
par_plaque = {}
for inf in db.scalars(select(Infraction)).all():
    par_plaque.setdefault(inf.vehicule.plaque, []).append(inf)
check("Véhicules résolus : 4526TCC · 8076TCB · 0926TBV · 5156TBV · 5186TBV · "
      "3056TBS · 4006TBS",
      set(par_plaque) == {"4526TCC", "8076TCB", "0926TBV", "5156TBV",
                          "5186TBV", "3056TBS", "4006TBS"},
      str(sorted(par_plaque)))
lig_nuit = db.scalar(select(Infraction).where(
    Infraction.ymane_id == "6152059"))
check("Gravité dérivée du niveau : ALARME → CRITIQUE (nuit) ; "
      "chauffeur Ym@ne conservé en nom brut (jamais perdu)",
      lig_nuit.gravite == GraviteInfraction.CRITIQUE
      and lig_nuit.chauffeur_brut == "ANDRIANASOLO Maminiaina eddy")
check("Ligne réelle nuit : type TCC, seuil brut + verbatim, distance "
      "32.267 km, validation NON_TRAITEE (I4)",
      lig_nuit.type == TypeInfraction.DEPASSEMENT_TCC
      and lig_nuit.seuil_unite == "brut"
      and lig_nuit.seuil_texte == "18:00:00 to 05:30:00"
      and lig_nuit.seuil_reference is None
      and lig_nuit.validation == "NON_TRAITEE")
lib = {i.ymane_id: s_infraction(i)["seuil_libelle"]
       for i in db.scalars(select(Infraction)).all()}
check("Colonne « Seuil » (écran = export) : « 25 km/h » · « 04:30 » · "
      "« 10:00 » · « 09:00 » · « 6.00 » · « 18:00:00 to 05:30:00 »",
      lib["6153001"] == "25 km/h" and lib["6153002"] == "04:30"
      and lib["6153005"] == "10:00" and lib["6153006"] == "09:00"
      and lib["6153003"] == "6.00"
      and lib["6152059"] == "18:00:00 to 05:30:00", str(lib))
check("Coordonnées affichées « lat, lng » (ordre corrigé) sur la nuit "
      "réelle", s_infraction(lig_nuit)["coordonnees"] == "-18.54970, 49.11650",
      s_infraction(lig_nuit)["coordonnees"])

# idempotence par exceptionid (rattrapage veille : mêmes lignes relues)
stats2 = importer_infractions_ymane(db, normes, maintenant=MAINTENANT)
db.commit()
total = db.scalar(select(func.count(Infraction.id)).where(
    Infraction.exterieure.is_(True)))
check("I5 : même fenêtre relue (rattrapage J-1→J) → 0 nouvelle, 0 maj, 7 "
      "au total — jamais de doublon",
      stats2["nouvelles"] == 0 and stats2["maj"] == 0 and total == 7,
      f"{stats2} total={total}")
audits = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "infraction.import_ymane")) or 0
check("Audit : une trace par passe de collecte (2 passes → 2 traces)",
      audits == 2, f"audits={audits}")

# ------------------------------------------------------ [D] session factice
print("\n[D] Cycle ACTIF avec portail Ym@ne factice (branchement gravé I5)")


class ReponseFactice:
    def __init__(self, statut=200, texte="", donnees=None):
        self.status_code = statut
        self.ok = 200 <= statut < 400
        self.text = texte
        self._donnees = donnees

    def json(self):
        if self._donnees is None:
            raise ValueError("corps non JSON")
        return self._donnees

    def raise_for_status(self):
        if not self.ok:
            raise urllib.error.HTTPError(
                "", self.status_code, f"HTTP {self.status_code}", None, None)


class SessionFactice:
    """Imite le portail Pumex : POST /login/ (errorCode 200 + identité),
    /changelanguage, garde /isvalidaccess, rapport detailedexceptionreport."""

    def __init__(self, items, echecs_liste=0, code_login=200):
        self.items = items
        self.echecs_liste = echecs_liste     # HTTP 500 à servir avant réussite
        self.code_login = code_login
        self.connexions = 0
        self.appels = []

    def get(self, url, params=None, timeout=None, **_):
        self.appels.append(("GET", url, params))
        if "/changelanguage/" in url:
            return ReponseFactice(200, donnees={})
        if url.endswith("/isvalidaccess"):
            return ReponseFactice(200, texte="true")
        if url.endswith("/detailedexceptionreport"):
            if self.echecs_liste > 0:
                self.echecs_liste -= 1
                return ReponseFactice(500, texte="session expirée")
            return ReponseFactice(200, donnees=self.items)
        return ReponseFactice(404)

    def post(self, url, json=None, timeout=None, **_):
        self.appels.append(("POST", url, json))
        if url.endswith("/login/"):
            self.connexions += 1
            if self.code_login != 200:
                return ReponseFactice(
                    200, donnees={"errorCode": self.code_login,
                                  "errorMessage": "Identifiants refusés"})
            return ReponseFactice(200, donnees={
                "errorCode": 200, "errorMessage": "Connexion réussie",
                "data": {"customerid": "1", "affiliateid": "1",
                         "transporterid": "2004",
                         "transportername": "LSS (LPSA)",
                         "username": "Lss_tracking"}})
        return ReponseFactice(404)


db.execute(delete(Infraction))
db.execute(delete(AuditLog).where(AuditLog.action == "infraction.import_ymane"))
db.commit()
jour = now_local().date()
os.environ["YMANE_ACTIVE"] = "1"          # activation (état livré v1.36)
check("I5 : collecteur ACTIF dès YMANE_ACTIVE=1 (état livré v1.36)",
      actif() is True)

# fenêtre du jour VIDE au matin (cas réel fréquent) — items datés d'hier
items_veille = [dict(b, exceptionid=700000 + i) for i, b in enumerate(BRUTS_REELS)]
# + UNE ligne tardive datée d'il y a EXACTEMENT 8 jours (motif de A-I5 :
# Ym@ne peut mettre > 12 h, parfois plusieurs jours, à traiter) — elle doit
# être rattrapée par la fenêtre J-8→J.
jour_j8 = (jour - timedelta(days=8)).isoformat()
items_veille.append(dict(BRUT_NUIT, exceptionid=999101,
                         startdatetime=f"{jour_j8} 19:30:00",
                         enddatetime=f"{jour_j8} 23:12:00"))
http = SessionFactice(items_veille)
api = ApiYmane(base_url="https://bi.camtrack.pro", utilisateur="Lss_tracking",
               mot_de_passe="secret", session=http)
res = cycle_ymane(db, api=api)
total = db.scalar(select(func.count(Infraction.id))) or 0
check("Cycle : 10 lignes brutes → 2 filtrées I2 (« Recording ») + 8 "
      "importées (1 tardive J-8 incluse), compteurs renvoyés",
      res.get("actif") is True and res.get("recus") == 10
      and res.get("filtrees_i2") == 2 and res.get("nouvelles") == 8
      and total == 8, str(res))
# v1.44 (réalignement déclaré) : le comptage se fait sur la LIGNE TARDIVE
# elle-même (heure 19:30) — un comptage à la date entière collisionne avec les
# fixtures réelles datées du 23/08/2026 dès que J-8 = 23/08 (calendrier).
nb_j8 = db.scalar(select(func.count(Infraction.id)).where(
    Infraction.date_jour == date.fromisoformat(jour_j8),
    Infraction.heure == time(19, 30))) or 0
check("A-I5 : la ligne tardive datée J-8 (19:30) EST rattrapée et importée "
      "(1 ligne à cette date)", nb_j8 == 1, f"j8={jour_j8} n={nb_j8}")
appels_liste = [p for m, u, p in http.appels if u.endswith("/detailedexceptionreport")]
check("Requête gravée : vehicleid=0 · exceptiontype=0 · exceptionlevel=0 "
      "(filtre I2 souverain à l'import)",
      len(appels_liste) == 1 and appels_liste[0]["vehicleid"] == 0
      and appels_liste[0]["exceptiontype"] == 0
      and appels_liste[0]["exceptionlevel"] == 0, str(appels_liste))
check("Identité prise sur la RÉPONSE de connexion (jamais recopiée) : "
      "clientid=1 · affiliateid=1 · transporterid=2004 (noms EXACTS de "
      "l'URL réelle du portail)",
      appels_liste[0]["clientid"] == 1 and appels_liste[0]["affiliateid"] == 1
      and appels_liste[0]["transporterid"] == 2004
      and api._identite["transporterid"] == 2004, str(appels_liste[0]))
check("A-I5 : fenêtre J-8→J par cycle (ABROGE J-1→J — retard traitement "
      f"Ym@ne > 12 h) : startdate={ (jour - timedelta(days=8)).isoformat() } "
      f"enddate={ jour.isoformat() }",
      appels_liste[0]["startdate"] == (jour - timedelta(days=8)).isoformat()
      and appels_liste[0]["enddate"] == jour.isoformat(), str(appels_liste))
logins = [a for a in http.appels if a[0] == "POST" and a[1].endswith("/login/")]
check("Session : UNE ouverture POST /login/ + corps JSON username/password/"
      "language", len(logins) == 1 and logins[0][2]["username"] == "Lss_tracking"
      and logins[0][2]["language"] == "French", str(len(logins)))
gardes = [a for a in http.appels if a[0] == "GET"
          and a[1].endswith("/isvalidaccess")]
check("Garde /isvalidaccess consultée avant la lecture", len(gardes) >= 1)

# session expirée : HTTP 500 sur le rapport → UNE re-connexion, UNE relance
db.execute(delete(Infraction))
db.commit()
http2 = SessionFactice(items_veille, echecs_liste=1)
api2 = ApiYmane(base_url="https://bi.camtrack.pro", session=http2)
res2 = cycle_ymane(db, api=api2)
total2 = db.scalar(select(func.count(Infraction.id))) or 0
check("Session expirée (HTTP 500) : UNE re-connexion puis nouvelle tentative "
      "→ collecte aboutie quand même", res2.get("nouvelles") == 8
      and http2.connexions == 2 and total2 == 8,
      f"connexions={http2.connexions} res={res2}")

# refus applicatif de login → erreur opposable, jamais silencieuse
api3 = ApiYmane(base_url="https://bi.camtrack.pro",
                session=SessionFactice([], code_login=401))
refus = None
try:
    api3.exceptions_fenetre(jour - timedelta(days=1), jour)
except RuntimeError as exc:
    refus = str(exc)
check("Refus de connexion (errorCode≠200) → RuntimeError « authentification "
      "refusée » explicite",
      refus is not None and "authentification refusée" in refus, str(refus))

# collecteur coupé : JAMAIS de requête sortante
os.environ["YMANE_ACTIVE"] = "0"
http4 = SessionFactice(items_veille)
api4 = ApiYmane(session=http4)
bloque = None
vide = api4.exceptions_fenetre(jour - timedelta(days=1), jour)
try:                       # défense en profondeur sur l'ouverture directe
    api4._connecter()
except YmaneDesactive as exc:
    bloque = str(exc)
check("YMANE_ACTIVE=0 : aucune requête sortante — fenêtre → [] no-op et "
      "_connecter → YmaneDesactive (0 appel réseau)",
      vide == [] and bloque is not None and len(http4.appels) == 0,
      f"vide={vide!r} appels={len(http4.appels)}")

print(f"\n{'=' * 64}\n===== test_ymane_branchement_v136 : {R['ok']} OK / "
      f"{R['ko']} KO =====\n{'=' * 64}")
db.close()
os.environ.pop("YMANE_ACTIVE", None)      # ne jamais fuiter hors du test
try:
    if db_url.startswith("sqlite:///"):
        os.remove(db_url.replace("sqlite:///", "/", 1))
        print("Base de test supprimée.")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
