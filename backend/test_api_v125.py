"""Tests v1.25 — Connecteur API OData MZoneX (§0sexies, arbitrages 20/08/2026).

Couvre, HORS RÉSEAU (stubs d'injection — la validation en direct a été faite
sur l'environnement réel au moment du développement) :

  C1  Mapping événement API → point collector : UTC → heure locale exacte,
      plaque « 8086 TCB » → 8086TCB (garde D3), vitesse bornée ∈ [0;+∞[,
      lignes sans plaque/GPS écartées. Aucun type forcé : la machine états
      §7.1 (v>3 km/h, §0ter) reste souveraine.
  C2  Mapping trajet officiel API → item réconciliation : début/fin à la
      seconde, distance km positive, fin absente tolérée (« en cours »).
  C3  Fenêtre incrémentale : reprise dernière−3 min ; plafond 30 min à la
      première passe ; fin = maintenant−20 s (décalage de publication).
  C4  Jetons OAuth2 : cache disque servi ; actualisation avec ROTATION du
      refresh ; actualisation refusée → reconnexion complète ; sans fichier
      → connexion complète. Secrets jamais en log ni en sortie.
  C5  Contrat N1 de bout en bout : points API injectés dans
      CollectorBase.inserer → ligne orange « en cours » si v>3, D1 crée la
      fiche inconnue, seconde passe idempotente (anti-rejeu + chevauchement).
  C6  Replis §0sexies A2 : API N2 en échec → lecteur d'écran appelé pour ce
      cycle ; MZONEX_API_ENABLE=0 → écran directement.

Exécution (base de test isolée, SUPPRIMÉE à la fin) :
  DATABASE_URL="sqlite:////tmp/test_v125.db" python3 test_api_v125.py
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
import time
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.config import jour_attribution, now_local
from app.database import SessionLocal
from app import engine, scrapers
from app.models import (AuditLog, EvenementGPS, SourceEvenement,
                        StatutValidationTrajet, SuiviJournalier, Trajet,
                        Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.oauth_mzonex import ErreurAuthMZoneX, GestionnaireJetonsMZoneX
from app.api_mzonex import (ApiMZoneX, depuis_utc, plaque_depuis_ligne_api,
                            point_depuis_evenement_api, trajet_depuis_api)

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

# ---------------------------------------------------------------- C1
print("\n[C1] Mapping événement API → point (UTC→local, plaque, vitesse)")
ev = {"vehicle_Registration": "8086 TCB", "utcTimestamp": "2026-08-20T02:14:37Z",
      "latitude": -18.8792, "longitude": 47.5079, "speed": 41.7}
p = point_depuis_evenement_api(ev)
check("horodatage UTC converti en HEURE LOCALE (UTC+3) à la seconde",
      p is not None and p["horodatage"] == datetime(2026, 8, 20, 5, 14, 37),
      str(p and p["horodatage"]))
check("plaque « 8086 TCB » → 8086TCB ; vitesse conservée ; type NON forcé "
      "(machine états §7.1 souveraine)",
      p["gps_associe"] == "8086TCB" and abs(p["vitesse"] - 41.7) < 1e-9
      and p["type_evenement"] is None and p["moteur"] == "ON")
check("garde D3 : ident non-plaque écarté ; GPS manquant écarté ; vitesse "
      "négative/none bornée à 0",
      point_depuis_evenement_api({**ev, "vehicle_Registration": "8086"}) is None
      and point_depuis_evenement_api({**ev, "latitude": None}) is None
      and point_depuis_evenement_api({**ev, "speed": None})["vitesse"] == 0.0)
check("libellé complet « 8086 TCB (LSS) » accepté aussi",
      plaque_depuis_ligne_api("8086 TCB (LSS)") == "8086TCB")

# ---------------------------------------------------------------- C2
print("\n[C2] Mapping trajet officiel API → item réconciliation (§2.4)")
t = {"vehicle_Registration": "0926 TBV",
     "startUtcTimestamp": "2026-08-19T20:41:20Z",
     "endUtcTimestamp": "2026-08-19T21:16:02Z", "distance": 15.673}
it = trajet_depuis_api(t)
check("début/fin locaux à la seconde + distance portail conservée",
      it["plaque"] == "0926TBV" and it["debut"] == datetime(2026, 8, 19,
                                                           23, 41, 20)
      and it["fin"] == datetime(2026, 8, 20, 0, 16, 2)
      and it["distance_km"] == 15.673,
      f"{it}")
check("fin absente tolérée (« en cours ») ; source MZONEX ; ligne sans "
      "plaque écartée",
      trajet_depuis_api({**t, "endUtcTimestamp": None})["fin"] is None
      and it["source"] == "MZONEX"
      and trajet_depuis_api({**t, "vehicle_Registration": "aucun"}) is None)

# ---------------------------------------------------------------- C3
print("\n[C3] Fenêtre incrémentale N1 (§0sexies A1 — cadence 60 s)")
api = ApiMZoneX(jetons=None)
maint = now_local().replace(microsecond=0)
derniere_fraiche = maint - timedelta(minutes=5)
d_utc, f_utc = api.fenetre_incrementale(derniere_fraiche, maint)
check("reprise (UTC naïf) : début ≈ (dernière−3 min)→UTC, "
      "fin ≈ (maintenant−20 s)→UTC",
      abs((api._utc_naive(derniere_fraiche) - timedelta(seconds=180)) - d_utc)
      < timedelta(seconds=2)
      and abs((api._utc_naive(maint) - timedelta(seconds=20)) - f_utc)
      < timedelta(seconds=2), f"{d_utc} / {f_utc}")
d_utc2, f_utc2 = api.fenetre_incrementale(None, maint)
check("première passe : plafond 3 h sous la FIN de fenêtre (§0nonies decies "
      "M3 du 29/08/2026 — boîtiers muets ≤ 3 h rattrapés en direct)",
      abs((f_utc2 - timedelta(seconds=10800)) - d_utc2) < timedelta(seconds=2))
ancienne = maint - timedelta(hours=5)
d_utc3, f_utc3 = api.fenetre_incrementale(ancienne, maint)
check("reprise après panne : TOUT de même plafonné à 3 h sous la fin "
      "(au-delà, la relecture J-1→J-7 — M1 — fait foi)",
      abs((f_utc3 - timedelta(seconds=10800)) - d_utc3) < timedelta(seconds=2))

# ---------------------------------------------------------------- C4
print("\n[C4] Jetons OAuth2 — cache, rotation, replis (sans réseau)")
fic = "/tmp/test_mz_oauth.json"
for cand in (fic,):
    try:
        os.unlink(cand)
    except OSError:
        pass
appels = {"login": 0, "refresh": 0}


def faux_login():
    appels["login"] += 1
    return {"access_token": "AT-A", "refresh_token": "RT-A", "expires_in": 3600}


def faux_refresh_ok(rt):
    appels["refresh"] += 1
    assert rt in ("RT-A", "RT-B")
    return {"access_token": "AT-B", "refresh_token": "RT-B", "expires_in": 3600}


def faux_refresh_refuse(rt):
    appels["refresh"] += 1
    raise ErreurAuthMZoneX("invalid_grant")


g = GestionnaireJetonsMZoneX(fichier=fic, fournir_login=faux_login,
                             fournir_refresh=faux_refresh_ok)
t1 = g.jeton()
check("sans cache : connexion complète, jeton servi",
      t1 == "AT-A" and appels["login"] == 1 and appels["refresh"] == 0)
t2 = g.jeton()
check("cache disque valide : AUCUN rappel réseau",
      t2 == "AT-A" and appels["login"] == 1 and appels["refresh"] == 0)
# force l'expiration → refresh
import json as _json
etat = _json.load(open(fic))
etat["expire"] = time.time() - 5
_json.dump(etat, open(fic, "w"))
t3 = g.jeton()
check("expiré : actualisation (refresh) avec ROTATION du refresh stockée",
      t3 == "AT-B" and appels["refresh"] == 1
      and _json.load(open(fic))["refresh_token"] == "RT-B")
etat = _json.load(open(fic)); etat["expire"] = time.time() - 5
_json.dump(etat, open(fic, "w"))
g2 = GestionnaireJetonsMZoneX(fichier=fic, fournir_login=faux_login,
                              fournir_refresh=faux_refresh_refuse)
t4 = g2.jeton()
check("refresh refusé → RECONNEXION COMPLÈTE automatique (aucun secret en "
      "log, l'erreur technique ne remonte pas l'identifiant)",
      t4 == "AT-A" and appels["login"] == 2)
g2.invalider()
check("invalider() purge le fichier (ré-authentification au prochain appel)",
      not os.path.exists(fic))
try:
    os.unlink(fic)
except OSError:
    pass

# ---------------------------------------------------------------- C5
print("\n[C5] Contrat N1 de bout en bout (API → inserer → ligne orange, D1)")


class ApiFactice(ApiMZoneX):
    def __init__(self, lignes):
        self._lignes = lignes

    def evenements(self, debut_utc, fin_utc):
        return list(self._lignes)


maint = now_local().replace(microsecond=0)
plaque_test = "4006TBS"
s1 = db.scalars(select(SuiviJournalier).join(Vehicule).where(
    Vehicule.plaque == plaque_test,
    SuiviJournalier.date_jour == jour_attribution(maint))).first()
if s1 is not None:
    db.execute(delete(Trajet).where(Trajet.suivi_id == s1.id))
    db.commit()
db.execute(delete(EvenementGPS).where(
    EvenementGPS.source == SourceEvenement.MZONEX))
db.execute(delete(AuditLog).where(AuditLog.username.like("test-v125%")))
db.commit()
db.expire_all()

from app.config import TZ as _TZ
from datetime import timezone as _tz_utc


def umer(d_local_naive):
    """datetime naïf LOCAL → chaîne « …Z » UTC (construction honnête, sans
    dépendre du fuseau du système de test)."""
    return d_local_naive.replace(tzinfo=_TZ).astimezone(_tz_utc.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
lignes = [
    {"vehicle_Registration": "4006 TBS", "latitude": -18.9, "longitude": 47.5,
     "utcTimestamp": umer(maint - timedelta(seconds=90)), "speed": 36.0},
    {"vehicle_Registration": "4006 TBS", "latitude": -18.9, "longitude": 47.5,
     "utcTimestamp": umer(maint - timedelta(seconds=60)), "speed": 38.0},
    {"vehicle_Registration": "XYZ Inconnu", "latitude": -18.9,
     "longitude": 47.5, "utcTimestamp": umer(maint - timedelta(seconds=30)),
     "speed": 0.0},
    # 9999NXX : plaque au motif valide volontairement ABSENTE du référentiel
    {"vehicle_Registration": "9999 NXX", "latitude": -18.9,
     "longitude": 47.5, "utcTimestamp": umer(maint - timedelta(seconds=10)),
     "speed": 12.0},
]
coll = scrapers.MZoneXApiCollector(api=ApiFactice(lignes))
# fenêtre indépendante du filtrage interne : normaliser directement les lignes
points = coll.normaliser(lignes)
check("normaliser : lignes API → points exploitables (ident sans motif écarté)",
      len(points) == 3, f"n={len(points)}")
n1 = coll.inserer(points)
db.expire_all()
tr = [t for t in db.scalars(select(Trajet).join(SuiviJournalier).join(Vehicule)
      .where(Vehicule.plaque == plaque_test,
             SuiviJournalier.date_jour == jour_attribution(maint))).all()
      if t.statut_validation != StatutValidationTrajet.REJETE]
check("v>3 km/h (§0ter) → ligne « en cours » orange ouverte par le MÊME moteur "
      "que le flux écran (§7.1)",
      n1 == 3 and len(tr) == 1 and tr[0].heure_fin is None
      and tr[0].heure_debut <= maint - timedelta(seconds=80),
      f"ins={n1} lignes={len(tr)}")
v_auto = db.scalars(select(Vehicule).where(
    Vehicule.plaque == "9999NXX")).first()
check("D1 (§0quater) : plaque inconnue vue par l'API → fiche CRÉÉE "
      "automatiquement (plateforme MZONEX)",
      v_auto is not None and v_auto.plateforme_gps == "MZONEX")
n2 = coll.inserer(coll.normaliser(lignes))
db.expire_all()
check("2ᵉ passe idempotente (anti-rejeu + chevauchement 3 min) : 0 doublon",
      n2 == 0, f"n2={n2}")

# ---------------------------------------------------------------- C6
print("\n[C6] Replis §0sexies A2 — écran en secours automatique")


class N2ApiCasse:
    def __init__(self):
        self.recensement = []

    def collecter_valides(self):
        raise RuntimeError("panne API simulée")


class N2EcranTemoin:
    def __init__(self):
        self.recensement = ["4006 TBS (LSS)"]

    def collecter_valides(self):
        return [{"plaque": "4006TBS", "debut": maint - timedelta(hours=2),
                 "fin": maint - timedelta(hours=1), "distance_km": 12.0,
                 "source": "MZONEX"}]


orig_api, orig_ecran = (scrapers.MZoneXTrajetsApiCollector,
                        scrapers.MZoneXTrajetsCollector)
try:
    scrapers.MZoneXTrajetsApiCollector = N2ApiCasse
    scrapers.MZoneXTrajetsCollector = N2EcranTemoin
    bruts, rec = scrapers._collecter_n2_mzonex()
    check("API N2 en échec → lecteur d'écran SOLICITÉ pour ce cycle "
          "(données servies tout de même)",
          len(bruts) == 1 and bruts[0]["plaque"] == "4006TBS"
          and rec == ["4006 TBS (LSS)"])
    os.environ["MZONEX_API_ENABLE"] = "0"
    bruts2, rec2 = scrapers._collecter_n2_mzonex()
    check("MZONEX_API_ENABLE=0 → écran directement (diagnostic §10), API "
          "jamais appelée",
          bruts2[0]["source"] == "MZONEX" and rec2 == ["4006 TBS (LSS)"])
finally:
    scrapers.MZoneXTrajetsApiCollector = orig_api
    scrapers.MZoneXTrajetsCollector = orig_ecran
    os.environ.pop("MZONEX_API_ENABLE", None)

# ---------------------------------------------------------------- nettoyage
db.execute(delete(EvenementGPS).where(
    EvenementGPS.source == SourceEvenement.MZONEX))
if s1 is not None:
    db.execute(delete(Trajet).where(Trajet.suivi_id == s1.id))
if v_auto is not None:
    s_auto = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v_auto.id)).all()
    for s in s_auto:
        db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
    db.execute(delete(Vehicule).where(Vehicule.id == v_auto.id))
db.commit()
db.close()
for cand in ("/tmp/test_v125.db",):
    try:
        os.unlink(cand)
    except OSError:
        pass

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
