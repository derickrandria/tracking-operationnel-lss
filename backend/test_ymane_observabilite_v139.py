# -*- coding: utf-8 -*-
"""Tests v1.39 — §0septies decies K1→K3 (arbitrages LSS du 27/08/2026) :
observabilité de la collecte Ym@ne.

Origine : constat exploitant du 27/08 — onglet Infractions à zéro alors que
le portail Ym@ne est alimenté ; l'échec (ou la désactivation) de la collecte
était INVISIBLE dans l'application (journal console seul).

[A] K1 — échec : UNE alerte COLLECTE_YMANE créée (NOUVELLE, MOYENNE, raison
    en français, lien /infractions, audit) ; répétition = MÀJ du message
    (compteur/dernier essai), jamais de doublon, statut conservé ;
[B] K1 — guérison : cycle redevenu sain → l'alerte ouverte est refermée
    automatiquement (TRAITEE + audit « guérison »), jamais effacée ; succès
    sans alerte → aucune création ;
[C] classification des raisons en français + import réel d'une ligne ;
[D] K1/K3 — désactivé (YMANE_ACTIVE≠1) = silence souverain (ni écriture ni
    alerte) ; verrou de passe (K2) : deux cycles jamais concurremment ;
[E] K2 — endpoint « Relancer la collecte maintenant » : réservé aux rôles
    d'écriture (403 consultation), 409 sur conflit, audit
    infraction.relance_ymane, message prêt-à-afficher, cas désactivé.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v139.db" SIM_ENABLE=0 python3 test_ymane_observabilite_v139.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_v139.db")
os.environ["YMANE_ACTIVE"] = "1"
import sys
from datetime import date, datetime, time

import urllib.error
from sqlalchemy import delete, func, select

_db_url = os.environ.get("DATABASE_URL", "")
assert "/tmp/" in _db_url, f"REFUS — DATABASE_URL hors /tmp : {_db_url!r}"

from app.database import SessionLocal
from app.models import (Alerte, AuditLog, GraviteAlerte, Infraction,
                        StatutAlerte, TypeAlerte, Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app import ymane_import as yim
from app.api_ymane import ApiYmane

R = {"ok": 0, "ko": 0}


def check(nom, cond, info=""):
    if cond:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} {info}")


class ApiEchec:
    """API factice qui lève l'exception demandée à chaque lecture."""
    def __init__(self, exc):
        self.exc = exc
    def infractions_recentes(self, jour):
        raise self.exc


class ApiVide:
    def infractions_recentes(self, jour):
        return []


DB_FILE = "/tmp/test_v139.db"
try:                                                 # SUPPRESSION AVANT seed_si_vide
    os.remove(DB_FILE)                               # NE JAMAIS exploiter une base héritée
except OSError:
    pass
seed_si_vide()
migrer_schema()
db = SessionLocal()
plaque_seed = db.scalar(select(Vehicule.plaque).limit(1))
db.close()
print(f"base test prête — plaque témoin : {plaque_seed}")

BRUT = {
    "exceptionid": 99001122,
    "levellabel": "Alarm",
    "parameterlabel": "menu_speeding",
    "parameter": "Speeding",
    "startdatetime": "27/08/2026 06:30:00",
    "threshold": 90,
    "maxvalue": 112.0,
    "totalduration": None,
    "distanceunderexception": 1.2,
    "startgps": "[47.5000,-18.9000]",
    "vehiclename": plaque_seed,
    "drivername": "Chauffeur Test Inconnu",
}


class ApiUneLigne:
    def infractions_recentes(self, jour):
        return [dict(BRUT)]


def _alertes_ymane(db):
    return db.scalars(select(Alerte).where(
        Alerte.type == TypeAlerte.COLLECTE_YMANE)).all()


def _audits(db, action):
    return db.scalars(select(AuditLog).where(AuditLog.action == action)).all()


# -------------------------------------------------------------------- [A] K1 échec
print("\n[A] K1 — échec : alerte créée, anti-spam, statut conservé")
st = yim.cycle_ymane_avec_alerte(api=ApiEchec(urllib.error.URLError("DNS"))) 
check("A1 wrapper ok=False", st.get("actif") and st.get("ok") is False, repr(st))
check("A1 raison « injoignable »", "injoignable" in (st.get("raison") or ""), st.get("raison"))
db = SessionLocal()
als = _alertes_ymane(db)
check("A1 une seule alerte créée", len(als) == 1, f"trouvées={len(als)}")
a = als[0] if als else None
check("A1 type/gravité/statut/lien", bool(a) and a.type == TypeAlerte.COLLECTE_YMANE
      and a.gravite == GraviteAlerte.MOYENNE and a.statut == StatutAlerte.NOUVELLE
      and a.lien_module == "/infractions",
      repr((a.type, a.statut, a.gravite, a.lien_module)) if a else "aucune")
check("A1 message dit la raison en français", bool(a) and "injoignable" in a.message
      and "en échec" in a.message, a.message if a else "aucune")
check("A1 audit alerte.ymane_echec", len(_audits(db, "alerte.ymane_echec")) == 1)
db.close()

st2 = yim.cycle_ymane_avec_alerte(api=ApiEchec(TimeoutError("45s")))
db = SessionLocal()
als = _alertes_ymane(db)
a = als[0]
check("A2 toujours UNE seule alerte (anti-spam)", len(als) == 1, f"trouvées={len(als)}")
check("A2 message mis à jour (2e échec, dernier essai)",
      "2e échec" in a.message and "dernier essai" in a.message.lower(), a.message)
check("A2 statut conservé NOUVELLE", a.statut == StatutAlerte.NOUVELLE)
check("A2 audit ymane_echec_repete", len(_audits(db, "alerte.ymane_echec_repete")) == 1)
db.close()

db = SessionLocal()
a = _alertes_ymane(db)[0]
a.statut = StatutAlerte.VUE
db.commit()
db.close()
yim.cycle_ymane_avec_alerte(api=ApiEchec(urllib.error.URLError("io")))
db = SessionLocal()
a = _alertes_ymane(db)[0]
check("A3 échec sur alerte VUE → statut VUE conservé (jamais rétrogradé)",
      a.statut == StatutAlerte.VUE, a.statut.value)
db.close()

# -------------------------------------------------------------------- [B] K1 guérison
print("\n[B] K1 — guérison : fermeture automatique, jamais d'effacement")
st3 = yim.cycle_ymane_avec_alerte(api=ApiVide())
db = SessionLocal()
als = _alertes_ymane(db)
a = als[0]
check("B1 cycle sain ok=True", st3.get("ok") is True, repr(st3))
check("B1 alerte refermée automatiquement (TRAITEE)",
      a.statut == StatutAlerte.TRAITEE, a.statut.value)
check("B1 message porte le rétablissement", "Rétablie" in a.message, a.message)
check("B1 alerte JAMAIS effacée (toujours présente)", len(als) == 1)
check("B1 audit ymane_guerison", len(_audits(db, "alerte.ymane_guerison")) == 1)
db.close()

yim.cycle_ymane_avec_alerte(api=ApiVide())
db = SessionLocal()
check("B2 succès sans alerte ouverte → aucune nouvelle alerte",
      len(_alertes_ymane(db)) == 1)
db.close()

# ------------------------------------------------------------------ [C] raisons + import
print("\n[C] classifications en français + import d'une ligne")
def _raison(exc):
    return yim.cycle_ymane_avec_alerte(api=ApiEchec(exc)).get("raison") or ""

# chaque essai rouvre/met à jour l'alerte — nettoyage entre sous-cas
def _purge_alertes_ymane():
    db = SessionLocal()
    db.execute(delete(AuditLog).where(AuditLog.action.in_((
        "alerte.ymane_echec", "alerte.ymane_echec_repete"))))
    db.execute(delete(Alerte).where(Alerte.type == TypeAlerte.COLLECTE_YMANE))
    db.commit(); db.close()

check("C1 HTTP 403 → identifiants refusés",
      "identifiants refusés" in _raison(
          urllib.error.HTTPError("", 403, "", None, None)))
check("C1 HTTP 500 → erreur du site",
      "répond en erreur" in _raison(
          urllib.error.HTTPError("", 500, "", None, None)))
check("C1 Timeout → délai dépassé",
      "délai dépassé" in _raison(TimeoutError("t")))
check("C1 refus applicatif de connexion → identifiants refusés",
      "identifiants refusés" in _raison(RuntimeError(
          "Ym@ne : authentification refusée — 'Login incorrect'")))
check("C1 JSON illisible → réponse illisible",
      "illisible" in _raison(ValueError("Expecting value")))
_purge_alertes_ymane()       # les alertes de classification ne doivent pas rester

st4 = yim.cycle_ymane_avec_alerte(api=ApiUneLigne())
db = SessionLocal()
inf = db.scalar(select(Infraction).where(Infraction.ymane_id == "99001122"))
check("C2 import d'une ligne : ok=True, 1 nouvelle",
      st4.get("ok") and st4.get("nouvelles") == 1, repr(st4))
check("C2 infraction écrite (ALARME, vitesse, véhicule seed)",
      bool(inf) and inf.niveau == "ALARME" and inf.valeur_mesuree == 112.0
      and inf.seuil_reference == 90.0 and inf.seuil_unite == "kmh",
      repr(inf) if not inf else repr((inf.niveau, inf.valeur_mesuree,
                                      inf.seuil_reference, inf.seuil_unite)))
check("C2 aucune alerte ouverte après ce succès",
      len(_alertes_ymane(db)) == 0)
db.close()

# ------------------------------------------------------------------ [D] désactivé + verrou
print("\n[D] K1/K3 — désactivé = silence souverain ; verrou de passe")
os.environ["YMANE_ACTIVE"] = "0"
st5 = yim.cycle_ymane_avec_alerte(api=ApiEchec(RuntimeError("ne doit pas être appelé")))
db = SessionLocal()
check("D1 désactivé → actif=False, silence total",
      st5 == {"actif": False} and len(_alertes_ymane(db)) == 0, repr(st5))
db.close()

os.environ["YMANE_ACTIVE"] = "1"
acquis = yim._verrou_cycle.acquire(blocking=False)
st6 = yim.cycle_ymane_avec_alerte(api=ApiEchec(RuntimeError("x")))
check("D2 verrou tenu → conflit, aucune exécution",
      acquis and st6.get("conflit") is True and st6.get("ok") is False, repr(st6))
yim._verrou_cycle.release()
st6b = yim.cycle_ymane_avec_alerte(api=ApiVide())
check("D2 verrou libéré → cycle normal à nouveau", st6b.get("ok") is True, repr(st6b))

# ------------------------------------------------------------------ [E] endpoint K2
print("\n[E] K2 — endpoint « Relancer la collecte maintenant »")
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)


def _jeton(u, p):
    r = client.post("/api/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}

h_admin = _jeton("admin", "Admin@2026")
h_tracking = _jeton("tracking", "Tracking@2026")
h_consult = _jeton("consultation", "Consult@2026")

r = client.post("/api/infractions/ymane/rejouer", headers=h_consult)
check("E1 consultation → 403 (rôles d'écriture seuls)", r.status_code == 403,
      f"code={r.status_code}")

orig = yim.cycle_ymane_avec_alerte
yim.cycle_ymane_avec_alerte = lambda: {"actif": True, "ok": True, "recus": 7,
                                       "filtrees_i2": 2, "nouvelles": 3,
                                       "maj": 1, "doublons": 1,
                                       "sans_vehicule": 0}
try:
    r2 = client.post("/api/infractions/ymane/rejouer", headers=h_tracking)
    j = r2.json() if r2.status_code == 200 else {}
    check("E2 relance (tracking) → 200 ok=True", r2.status_code == 200 and j.get("ok"),
          f"code={r2.status_code} corps={j}")
    check("E2 message prêt-à-afficher (lignes/nouvelles)",
          "7 ligne(s)" in (j.get("message") or "") and "3 nouvelle(s)" in j["message"],
          j.get("message"))
    db = SessionLocal()
    aud = [a for a in _audits(db, "infraction.relance_ymane")
           if a.username == "tracking"]
    check("E2 audit infraction.relance_ymane au nom du TRACKING",
          len(aud) >= 1 and (aud[-1].details or {}).get("nouvelles") == 3,
          repr([a.username for a in aud]))
    db.close()
finally:
    yim.cycle_ymane_avec_alerte = orig

acquis = yim._verrou_cycle.acquire(blocking=False)
r3 = client.post("/api/infractions/ymane/rejouer", headers=h_admin)
yim._verrou_cycle.release()
check("E3 collecte déjà en cours → 409 « déjà en cours »",
      r3.status_code == 409 and "déjà en cours" in r3.json().get("detail", ""),
      f"code={r3.status_code} corps={r3.text}")

os.environ["YMANE_ACTIVE"] = "0"
r4 = client.post("/api/infractions/ymane/rejouer", headers=h_admin)
os.environ["YMANE_ACTIVE"] = "1"
j4 = r4.json() if r4.status_code == 200 else {}
check("E4 désactivé → 200 ok=False « désactivée », sans appel Ym@ne",
      r4.status_code == 200 and j4.get("ok") is False
      and "désactivée" in (j4.get("message") or ""),
      f"code={r4.status_code} corps={j4}")
db = SessionLocal()
check("E4 tentative consignée en audit (actif=False)",
      any((a.details or {}).get("raison") == "collecte désactivée"
          for a in _audits(db, "infraction.relance_ymane")))
db.close()

# ------------------------------------------------------------------ fin
db = SessionLocal()
try:
    total_a = len(_alertes_ymane(db))
    print(f"\nétat final : alertes COLLECTE_YMANE résiduelles (TRAITEE test C1 "
          f"purge) = {total_a}")
finally:
    db.close()

print(f"\n==== {R['ok']} OK / {R['ko']} KO ====")
try:
    os.remove(DB_FILE)
except OSError:
    pass
sys.exit(0 if R["ko"] == 0 else 1)
