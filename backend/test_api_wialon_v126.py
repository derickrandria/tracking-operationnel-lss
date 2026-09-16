"""Tests v1.26 — Connecteur API Wialon (CamtrackPro, §0sexies A4).

HORS RÉSEAU (validation directe faite sur l'environnement réel le 20/08/2026
avec le jeton de l'exploitant : 19 unités, 45 trajets du jour, recensement
infaillible) :

  D1  Mapping position Wialon → point collector : epoch UTC → heure locale
      exacte, plaque « 6546 TCE-MERCEDES-LPSA(LSS) » → 6546TCE (garde D3),
      vitesse conservée, lignes sans message/GPS écartées, type NON forcé
      (machine états §7.1 souveraine).
  D2  Mapping ligne « Detail Trajet » → item réconciliation : cellules
      dict {"t":…,"v":epoch} ou texte seul ; rectificatif §0octies C1 (v1.28)
      — le serveur rend les textes en UTC : epoch « v » prioritaire, texte
      relu comme UTC en repli, instants restitués LOCAUX à la seconde ;
      distance « 30.30 km », conducteur propagé, fin vide tolérée.
  D3  Activation : jeton absent → API inactive (comportement v1.25 conservé) ;
      CAMTRACKPRO_API_ENABLE=0 → inactive même avec jeton.
  D4  Contrat N1 de bout en bout : positions injectées via CollectorBase
      inserer → ligne orange « en cours » si v>3 km/h (§0ter), source
      CAMTRACKPRO, 2ᵉ passe idempotente (seuls les NOUVEAUX messages passent).
  D5  Replis A2/A4 : API N2 en échec → lecteur d'écran sollicité pour ce
      cycle ; jeton absent → écran directement, API jamais tentée.

Exécution (base de test isolée, SUPPRIMÉE à la fin) :
  DATABASE_URL="sqlite:////tmp/test_v126.db" python3 test_api_wialon_v126.py
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.config import TZ, jour_attribution, now_local
from app.database import SessionLocal
from app import engine, scrapers
from app.models import (AuditLog, EvenementGPS, SourceEvenement,
                        StatutValidationTrajet, SuiviJournalier, Trajet,
                        Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.api_wialon import (ApiWialon, item_depuis_ligne_rapport,
                            jeton_configure, plaque_unite,
                            point_depuis_position_wialon)

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

# ---------------------------------------------------------------- D1
print("\n[D1] Mapping position Wialon → point (epoch→local, plaque, vitesse)")
epoch = int(datetime(2026, 8, 20, 3, 14, 37, tzinfo=timezone.utc).timestamp())
u = {"nm": "6546 TCE-MERCEDES-LPSA(LSS)",
     "pos": {"t": epoch, "s": 27, "y": -18.9261, "x": 47.6781}}
p = point_depuis_position_wialon(u)
check("epoch UTC → HEURE LOCALE Antananarivo (UTC+3) à la seconde",
      p is not None and p["horodatage"] == datetime(2026, 8, 20, 6, 14, 37),
      str(p and p["horodatage"]))
check("plaque libellé long → 6546TCE ; vitesse conservée ; type forcé "
      "POSITION (spec v1.46 : seuil 3 km/h)",
      p["gps_associe"] == "6546TCE" and p["vitesse"] == 27.0
      and p["type_evenement"] == "POSITION")
check("sans dernier message (∅ pos) écarté ; sans GPS écarté ; non-plaque "
      "(garde D3) écarté",
      point_depuis_position_wialon({"nm": u["nm"]}) is None
      and point_depuis_position_wialon(
          {"nm": u["nm"], "pos": {"t": epoch, "s": 0}}) is None
      and plaque_unite("GROUP TOTAL") is None)

# ---------------------------------------------------------------- D2
print("\n[D2] Mapping ligne « Detail Trajet » → item réconciliation"
      " (rectificatif fuseau §0octies C1)")
# Ligne REELLE copiée de l'API le 20/08/2026 (0826TBS) : le texte serveur est
# UTC ; « v » est l'epoch UTC du même instant (02:24:44 UTC = 05:24:44 local,
# exactement les heures affichées par le portail sur la capture de preuve).
cellules = [{"t": "2026-08-20 02:24:44", "v": 1787192684},
            {"t": "GFC-Brickaville-RN2", "y": -18.81, "x": 49.06},
            {"t": "2026-08-20 04:39:08"},
            {"t": "Antsampanana"}, "2:14:24", "0:03:12", "2:11:12",
            "23.04 km", {"t": "31 km/h"},
            {"t": "45 km/h", "y": -18.9, "x": 47.6},
            "0:05:01", "ANDRIAMAMPIANINA Lahatra Faneva Omega"]
it = item_depuis_ligne_rapport("0826 TBS-MERCEDES -LPSA(LSS)", cellules)
check("epoch « v » prioritaire → HEURE LOCALE exacte ; texte seul relu UTC "
      "→ local ; distance km ; conducteur propagé",
      it["plaque"] == "0826TBS" and it["debut"] == datetime(2026, 8, 20,
                                                            5, 24, 44)
      and it["fin"] == datetime(2026, 8, 20, 7, 39, 8)
      and abs(it["distance_km"] - 23.04) < 1e-9
      and it["conducteur"].startswith("ANDRIAMAMPIANINA")
      and it["source"] == "CAMTRACKPRO")
check("fin vide tolérée (« en cours ») ; distance vide → None ; colonne "
      "manquante / plaque illisible → ligne écartée",
      item_depuis_ligne_rapport(
          "0826 TBS-MERCEDES -LPSA(LSS)",
          [cellules[0], "", "", "", "", "", "", "", "", "", "", ""])["fin"]
      is None and it is not None
      and item_depuis_ligne_rapport("0826 TBS-MERCEDES -LPSA(LSS)",
                                    [cellules[0]] + [""] * 10 + ["x"])[
          "distance_km"] is None
      and item_depuis_ligne_rapport("LSS_LPSA", cellules) is None)

# ---------------------------------------------------------------- D3
print("\n[D3] Activation conditionnée (§0sexies A4)")
sauve = os.environ.get("CAMTRACKPRO_TOKEN")
try:
    os.environ.pop("CAMTRACKPRO_TOKEN", None)
    check("jeton absent → API inactive (comportement v1.25 : écran seul)",
          jeton_configure() is False)
    os.environ["CAMTRACKPRO_TOKEN"] = "UNTOKENQUELCONQUE"
    check("jeton présent → API active",
          jeton_configure() is True)
    os.environ["CAMTRACKPRO_API_ENABLE"] = "0"
    check("CAMTRACKPRO_API_ENABLE=0 → inactive même avec jeton (diagnostic §10)",
          jeton_configure() is False)
finally:
    os.environ.pop("CAMTRACKPRO_API_ENABLE", None)
    if sauve:
        os.environ["CAMTRACKPRO_TOKEN"] = sauve
    else:
        os.environ.pop("CAMTRACKPRO_TOKEN", None)

# ---------------------------------------------------------------- D4
print("\n[D4] Contrat N1 de bout en bout (positions API → inserer → orange)")


class ApiFacticeWialon(ApiWialon):
    def __init__(self, unites):
        self._unites = unites

    def unites(self):
        return list(self._unites)

    def fermer(self):
        pass


maint = now_local().replace(microsecond=0)
plaque_test = "0826TBS"
s1 = db.scalars(select(SuiviJournalier).join(Vehicule).where(
    Vehicule.plaque == plaque_test)).first()
if s1 is not None:
    db.execute(delete(Trajet).where(Trajet.suivi_id == s1.id))
db.execute(delete(EvenementGPS).where(
    EvenementGPS.source == SourceEvenement.CAMTRACKPRO))
db.execute(delete(AuditLog).where(AuditLog.username.like("test-v126%")))
db.commit()
db.expire_all()


def pos_unite(plaque_nm, il_y_a_s, vitesse):
    ts = maint - timedelta(seconds=il_y_a_s)
    return {"nm": plaque_nm,
            "pos": {"t": int(ts.replace(tzinfo=TZ).astimezone(
                timezone.utc).timestamp()), "s": vitesse,
                "y": -18.9261, "x": 47.6781}}


unites = [pos_unite("0826 TBS-MERCEDES -LPSA(LSS)", 40, 27),
          pos_unite("GROUP TOTAL", 20, 0)]      # non-plaque : ignoré
api = ApiFacticeWialon(unites)

# inserer directement les points (fenêtre vive simulée par la fake)
coll = scrapers.CamtrackProApiCollector(api=api)
points = coll.normaliser(api.unites())
check("normaliser : unité valide conservée, non-plaque écartée",
      len(points) == 1 and points[0]["gps_associe"] == "0826TBS",
      f"n={len(points)}")
n1 = coll.inserer(points)
db.expire_all()
jour_pt = jour_attribution(maint - timedelta(seconds=40))
s_apj = db.scalars(select(SuiviJournalier).join(Vehicule).where(
    Vehicule.plaque == plaque_test,
    SuiviJournalier.date_jour == jour_pt)).first()
tr = [t for t in db.scalars(select(Trajet).where(
        Trajet.suivi_id == s_apj.id).order_by(Trajet.numero)).all()
    if t.statut_validation != StatutValidationTrajet.REJETE] \
    if s_apj is not None else []
check("v>3 km/h (§0ter) → ligne « en cours » orange par le MÊME moteur §7.1",
      n1 == 1 and len(tr) == 1 and tr[0].heure_fin is None,
      f"ins={n1} lignes={len(tr)}")
ev = db.scalars(select(EvenementGPS).where(
    EvenementGPS.source == SourceEvenement.CAMTRACKPRO)).all()
check("source CAMTRACKPRO conservée en base (N1 distinct du flux MZoneX)",
      len(ev) == 1 and ev[0].source == SourceEvenement.CAMTRACKPRO)
n2 = coll.inserer(coll.normaliser(api.unites()))
check("2ᵉ cycle (même dernier message) : anti-rejeu → 0 insertion",
      n2 == 0, f"n2={n2}")
# nouveau message (même unité, 30 s plus tard) → passe
api._unites = [pos_unite("0826 TBS-MERCEDES -LPSA(LSS)", 10, 31)]
n3 = coll.inserer(coll.normaliser(api.unites()))
check("nouveau message du boîtier : 1 insertion (incrémentalité native)",
      n3 == 1, f"n3={n3}")

# ---------------------------------------------------------------- D5
print("\n[D5] Replis A2/A4 — écran en secours, jeton absent = écran direct")


class N2ApiCasse:
    def __init__(self):
        self.recensement = []

    def collecter_valides(self):
        raise RuntimeError("panne API simulée")


class N2EcranTemoin:
    def __init__(self):
        self.recensement = ["0826 TBS-MERCEDES -LPSA(LSS)"]

    def collecter_valides(self):
        return [{"plaque": "0826TBS", "debut": maint - timedelta(hours=3),
                 "fin": maint - timedelta(hours=2), "distance_km": 8.5,
                 "source": "CAMTRACKPRO"}]


orig_api, orig_ecran = (scrapers.CamtrackProTrajetsApiCollector,
                        scrapers.CamtrackProTrajetsCollector)
sauve2 = os.environ.get("CAMTRACKPRO_TOKEN")
try:
    scrapers.CamtrackProTrajetsApiCollector = N2ApiCasse
    scrapers.CamtrackProTrajetsCollector = N2EcranTemoin
    os.environ["CAMTRACKPRO_TOKEN"] = "UNTOKENQUELCONQUE"
    bruts, rec = scrapers._collecter_n2_camtrackpro()
    check("API N2 en échec → lecteur d'écran SOLICITÉ à sa place (jamais de "
          "trou dans la donnée)",
          len(bruts) == 1 and bruts[0]["source"] == "CAMTRACKPRO"
          and rec == ["0826 TBS-MERCEDES -LPSA(LSS)"])
    os.environ.pop("CAMTRACKPRO_TOKEN", None)
    bruts2, rec2 = scrapers._collecter_n2_camtrackpro()
    check("jeton absent → écran directement (API jamais tentée)",
          bruts2[0]["plaque"] == "0826TBS"
          and rec2 == ["0826 TBS-MERCEDES -LPSA(LSS)"])
finally:
    scrapers.CamtrackProTrajetsApiCollector = orig_api
    scrapers.CamtrackProTrajetsCollector = orig_ecran
    if sauve2:
        os.environ["CAMTRACKPRO_TOKEN"] = sauve2

# ---------------------------------------------------------------- nettoyage
db.execute(delete(EvenementGPS).where(
    EvenementGPS.source == SourceEvenement.CAMTRACKPRO))
for s in (s1, s_apj):
    if s is not None:
        db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
db.commit()
db.close()
for cand in ("/tmp/test_v126.db",):
    try:
        os.unlink(cand)
    except OSError:
        pass

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
