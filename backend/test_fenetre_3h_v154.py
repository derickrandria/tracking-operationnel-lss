# -*- coding: utf-8 -*-
"""ÉTAPE 1 — FENÊTRE DE RATTRAPAGE DES BOÎTIERS MUETS : 3 HEURES (v1.54, 18/09/2026).

Régression corrigée : le commit `d0c901c` (12/09/2026) avait ramené
`MZONEX_API_FENETRE_MAX_S` de **10 800 s (3 h)** à **900 s (15 min)**, en
confondant DEUX réglages indépendants :

    · PROFONDEUR historique relue   → `FENETRE_MAX_S`   = 10 800 s (3 h)  ← arbitrage M3
    · TAILLE d'une tranche de requête → `TRANCHE_S`      = 900 s (15 min) ← payload léger

Ce que cette suite PROUVE (aucun accès réseau : les appels sont simulés) :

  [1] fenêtre de 15 minutes      : la tranche de payload est de 15 min, la
                                   profondeur reste de 3 h (12 tranches) ;
  [2] fenêtre de 16 minutes      : un silence de 16 min est COUVERT par N1 ;
  [3] fenêtre de 3 heures        : un silence de 3 h est COUVERT (bord inclus) ;
  [4] fenêtre de 3 h et 1 seconde: hors N1, DÉTECTÉ comme trou et relu par M1 ;
  [5] relance deux fois          : idempotence, ZÉRO doublon ;
  [6] donnée arrivée tardivement : boîtier muet → ingérée, horodatée à l'heure
                                   GPS réelle (pas à l'heure de réception) ;
  [7] source partielle           : pagination plafonnée → REFUSÉE (exception),
                                   jamais de lot tronqué transmis ;
  [8] échec de collecte          : l'échec est ENREGISTRÉ (checkpoint + audit)
                                   et la période relue est conservée ;
  [9] archive non créée          : source incomplète → AUCUNE archive écrite,
                                   AUCUN trajet officiel créé ;
  [10] audit : la SOURCE et la PÉRIODE relue sont en base (règle 8).

Lancement :
  cd backend
  DATABASE_URL="sqlite:////tmp/test_fenetre_3h_v154.db" python test_fenetre_3h_v154.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_fenetre_3h_v154.db")

from sqlalchemy import func, select                            # noqa: E402

from app import scrapers                                       # noqa: E402
from app.api_mzonex import (ErreurApiMZoneX, TRANCHE_S,        # noqa: E402
                            ApiMZoneX, FENETRE_MAX_S)
from app.config import now_local, TZ                           # noqa: E402
from app.database import SessionLocal                          # noqa: E402
from app.main import migrer_schema                             # noqa: E402
from app.models import (AuditLog, CollecteCheckpoint,          # noqa: E402
                        EvenementGPS, HistoriqueJournalier,
                        StatutSourceTrajet, Trajet, Vehicule)
from app.seed import seed_si_vide                              # noqa: E402

OK = KO = 0
PROFONDEUR_ATTENDUE = 10800         # 3 h — arbitrage §0nonies decies M3 (29/08/2026)
TRANCHE_ATTENDUE = 900              # 15 min — payload OData léger


def check(nom: str, condition: bool, info: str = ""):
    global OK, KO
    if condition:
        OK += 1
        print(f"  ✅ {nom}")
    else:
        KO += 1
        print(f"  ❌ {nom} {info}")


MAINT = now_local().replace(microsecond=0)
api = ApiMZoneX(jetons=None)
# Aucun accès réseau : la résolution du groupe de flotte (qui interroge le
# portail) est neutralisée — seul le DÉCOUPAGE des requêtes est observé ici.
api.groupe_flotte = lambda: "GROUPE-TEST"


class _JetonFactice:
    """Aucun réseau : jeton figé pour les appels simulés."""

    def jeton(self):
        return "jeton-de-test"


api._jetons = _JetonFactice()

print("\n[1] PROFONDEUR 3 H vs TRANCHES DE 15 MIN (deux réglages INDÉPENDANTS)")
check(f"FENETRE_MAX_S = {PROFONDEUR_ATTENDUE} s (3 h) — valeur de l'arbitrage M3",
      FENETRE_MAX_S == PROFONDEUR_ATTENDUE, f"{FENETRE_MAX_S}")
check(f"TRANCHE_S = {TRANCHE_ATTENDUE} s (15 min) — payload découpé, profondeur intacte",
      TRANCHE_S == TRANCHE_ATTENDUE, f"{TRANCHE_S}")
check("la profondeur N'EST PAS la tranche (c'est la régression du 12/09)",
      FENETRE_MAX_S > TRANCHE_S, f"{FENETRE_MAX_S} vs {TRANCHE_S}")
d15, f15 = api.fenetre_incrementale(None, MAINT)
check("première passe : la fenêtre N1 couvre 3 h, pas 15 min",
      abs((f15 - d15).total_seconds() - PROFONDEUR_ATTENDUE) < 2,
      f"{(f15 - d15).total_seconds()} s")

# une fenêtre de 3 h est servie en tranches de 15 min (12 requêtes), jamais tronquée
bornes: list[tuple] = []
orig_pages = api._pages


def _pages_espionnes(chemin_requete):
    bornes.append(chemin_requete)
    return []


api._pages = _pages_espionnes
api.evenements(d15, f15)
api._pages = orig_pages
check("une fenêtre de 3 h est découpée en 12 tranches de 15 min (aucune troncature)",
      len(bornes) == 12, f"{len(bornes)} tranche(s)")

print("\n[2-3-4] SILENCES DE 15 MIN / 16 MIN / 3 H / 3 H + 1 S (couverture N1)")


def couvert(silence_s: int) -> tuple[bool, float]:
    """Le silence est-il DANS la fenêtre relue par N1 ? (bornes UTC naïves)"""
    fin_silence = MAINT - timedelta(seconds=20)          # décalage publication
    debut_silence = fin_silence - timedelta(seconds=silence_s)
    d_utc, f_utc = api.fenetre_incrementale(None, MAINT)
    d_loc = d_utc.replace(tzinfo=TZ).astimezone(TZ)      # (comparaison en UTC)
    return (d_utc <= api._utc_naive(debut_silence)
            and api._utc_naive(fin_silence) <= f_utc), (f_utc - d_utc).total_seconds()


ok15, prof = couvert(15 * 60)
check("silence de 15 minutes : DANS la fenêtre N1 → récupérable",
      ok15 and prof == PROFONDEUR_ATTENDUE, f"couvert={ok15} profondeur={prof}")
ok16, _ = couvert(16 * 60)
check("silence de 16 minutes : DANS la fenêtre N1 → récupérable "
      "(c'était LE cas cassé à 900 s)",
      ok16)
ok3h, _ = couvert(3 * 3600)
check("silence de 3 heures : COUVERT (bord gauche inclus, 10800 ≥ 10800)",
      ok3h)
ok3h1, _ = couvert(3 * 3600 + 1)
check("silence de 3 h + 1 s : HORS de N1 (comportement attendu) — "
      "c'est M1 qui prend le relais",
      not ok3h1)

print("\n[4 bis] UN TROU DE 3 H + 1 S EST DÉTECTÉ ET RELU (M1, tranches de 15 min)")
seed_si_vide()
migrer_schema()
db = SessionLocal()
veh = db.scalar(select(Vehicule).where(Vehicule.statut == "ACTIF").limit(1))
JOUR = MAINT.date()
debut_trou = datetime.combine(JOUR, datetime.min.time()).replace(hour=4)
trou_debut = MAINT - timedelta(hours=3, seconds=1)
for h in (debut_trou, MAINT - timedelta(minutes=30), MAINT):
    db.add(EvenementGPS(vehicule_id=veh.id, horodatage=h, latitude=-18.9,
                        longitude=47.5, vitesse=40.0, etat_moteur="ON",
                        source=scrapers.SourceEvenement.MZONEX))
db.commit()
fenetres = scrapers._fenetres_manquantes_mzonex(db, 0, MAINT)
trous = [f for f in fenetres if f[1] > trou_debut + timedelta(minutes=1)]
check("un silence > 3 h est identifié comme TROU à relire",
      bool(trous), f"fenetres={[(str(a), str(b)) for a, b in fenetres]}")
if trous:
    a, b = trous[0]
    lignes: list[str] = []
    orig = api._pages
    api._pages = lambda c: (lignes.append(c) or [])
    api.evenements(a, b)
    api._pages = orig
    attendu = max(1, int((b - a).total_seconds() // TRANCHE_S))
    check("le trou est relu en ENTIER (tranches de 15 min, aucune troncature)",
          len(lignes) >= attendu, f"{len(lignes)} requête(s) pour {attendu} tranche(s)")

print("\n[5-6] RELANCE SANS DOUBLON ET DONNÉE ARRIVÉE TARDIVEMENT")
coll = scrapers.MZoneXApiCollector()
coll.api = api
db.close()

# point « boîtier muet » : horodatage GPS vieux de 2 h, reçu MAINTENANT
tardif = {
    "gps_associe": veh.plaque, "horodatage": MAINT - timedelta(hours=2),
    "lat": -18.91, "lng": 47.51, "vitesse": 35.0, "moteur": "ON",
}
db = SessionLocal()
n1 = coll.inserer([dict(tardif)], historique=True)
db.expire_all()
total1 = db.scalar(select(func.count(EvenementGPS.id))) or 0
ev = db.scalars(select(EvenementGPS).where(
    EvenementGPS.vehicule_id == veh.id).order_by(
        EvenementGPS.horodatage.desc())).all()
check("donnée tardive (2 h de retard) INGÉRÉE", n1 >= 1, f"insérés={n1}")
check("elle est horodatée à l'heure GPS RÉELLE (pas à l'heure de réception)",
      any(abs((e.horodatage - (MAINT - timedelta(hours=2))).total_seconds()) < 2
          for e in ev), str([str(e.horodatage) for e in ev[:3]]))
n2 = coll.inserer([dict(tardif)], historique=True)
db.expire_all()
total2 = db.scalar(select(func.count(EvenementGPS.id))) or 0
check("RELANCE IDENTIQUE : aucun doublon (idempotence)", total2 == total1,
      f"avant={total1} après={total2}")
n3 = coll.inserer([dict(tardif), dict(tardif)], historique=True)
db.expire_all()
total3 = db.scalar(select(func.count(EvenementGPS.id))) or 0
check("relance en double dans le MÊME lot : toujours aucun doublon",
      total3 == total1 and n3 == 0, f"total={total3} insérés={n3}")

print("\n[7] SOURCE PARTIELLE : PAGINATION PLAFONNÉE → REFUSÉE, JAMAIS TRONQUÉE")


class _ReponsePleine:
    """Répond TOUJOURS une page pleine → le plafond de pagination est atteint."""

    status_code = 200

    def __init__(self, n: int):
        self._n = n

    def json(self):
        return {"value": [{"i": i} for i in range(self._n)]}


class _ClientFactice:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        return _ReponsePleine(scrapers.TAILLE_PAGE if hasattr(scrapers, "TAILLE_PAGE")
                              else 200)


import app.api_mzonex as apim                                # noqa: E402
orig_httpx_client = apim.httpx.Client
apim.httpx.Client = _ClientFactice
try:
    complete = True
    try:
        api._pages("Events?test=1")
    except ErreurApiMZoneX:
        complete = False
finally:
    apim.httpx.Client = orig_httpx_client
check("une réponse PARTIELLE (plafond de pagination) est REFUSÉE par exception",
      complete is False, "aucune exception levée → lot tronqué transmis !")

print("\n[8] ÉCHEC DE COLLECTE : ENREGISTRÉ, PÉRIODE CONSERVÉE, RIEN D'INGÉRÉ")


class ApiEnPanne:
    def __init__(self, *a, **k):
        self.recensement = []

    def collecter_valides(self, jours=None):
        raise RuntimeError("panne API simulée")

    def collecter(self):
        raise RuntimeError("panne API simulée")


class EcranIncomplet:
    def __init__(self, *a, **k):
        self.recensement = ["4006 TBS (LSS)"]

    def collecter_valides(self):
        return [{"plaque": "4006TBS", "debut": MAINT - timedelta(hours=2),
                 "fin": MAINT - timedelta(hours=1), "distance_km": 12.0,
                 "source": "MZONEX"}]


orig_api_c, orig_ecran_c = (scrapers.MZoneXTrajetsApiCollector,
                            scrapers.MZoneXTrajetsCollector)
env_av = {k: os.environ.get(k) for k in ("MZONEX_API_ENABLE", "MZONEX_REPLI_ECRAN")}
try:
    scrapers.MZoneXTrajetsApiCollector = ApiEnPanne
    scrapers.MZoneXTrajetsCollector = EcranIncomplet
    os.environ.pop("MZONEX_REPLI_ECRAN", None)
    os.environ.pop("MZONEX_API_ENABLE", None)

    db = SessionLocal()
    nb_arch_av = db.scalar(select(func.count(HistoriqueJournalier.id))) or 0
    nb_val_av = db.scalar(select(func.count(Trajet.id)).where(
        Trajet.statut_source == StatutSourceTrajet.VALIDE)) or 0
    db.close()

    attendues = ["4006TBS", "2746TCC"]
    bruts, rec = scrapers._collecter_n2_mzonex([JOUR], attendues=attendues)
    etat = dict(scrapers.DERNIER_ETAT_N2)
    check("API en panne : l'échec est ENREGISTRÉ",
          str(etat.get("echec", "")).startswith("api_"), str(etat.get("echec")))
    check("le repli est exécuté mais INCOMPLET → aucune ligne servie",
          bruts == [], f"lignes={len(bruts)}")
    check("la complétude est FAUSSE et la plaque manquante est nommée",
          etat.get("complet") is False
          and "2746TCC" in (etat.get("detail", {}).get("manquantes") or []),
          str(etat.get("detail")))

    print("\n[9] SOURCE INCOMPLÈTE → AUCUNE ARCHIVE OFFICIELLE, AUCUN TRAJET OFFICIEL")
    db = SessionLocal()
    nb_arch_ap = db.scalar(select(func.count(HistoriqueJournalier.id))) or 0
    nb_val_ap = db.scalar(select(func.count(Trajet.id)).where(
        Trajet.statut_source == StatutSourceTrajet.VALIDE)) or 0
    db.close()
    check("AUCUNE archive créée par une collecte incomplète",
          nb_arch_ap == nb_arch_av, f"avant={nb_arch_av} après={nb_arch_ap}")
    check("AUCUN trajet officiel (VALIDÉ) créé",
          nb_val_ap == nb_val_av, f"avant={nb_val_av} après={nb_val_ap}")

    print("\n[10] AUDIT : LA SOURCE ET LA PÉRIODE RELUE SONT EN BASE (règle 8)")
    # (a) échec de la fenêtre N1 temps réel : checkpoint + audit
    db = SessionLocal()
    nb_aud_av = db.scalar(select(func.count(AuditLog.id))) or 0
    db.close()


    class ApiQuiEchoue:
        def __init__(self, *a, **k):
            pass

        def fenetre_incrementale(self, derniere, maintenant):
            return (MAINT - timedelta(hours=3), MAINT)

        def evenements(self, d, f):
            raise ErreurApiMZoneX("panne simulée de la fenêtre N1")

    orig_collecter = apim.ApiMZoneX
    coll_n1 = scrapers.MZoneXApiCollector()
    coll_n1.api = ApiQuiEchoue()
    try:
        coll_n1.collecter()
        leve = False
    except Exception:
        leve = True
    check("l'échec de lecture N1 est bien propagé (rien n'est ingéré)", leve)
    db = SessionLocal()
    cp = db.scalars(select(CollecteCheckpoint).where(
        CollecteCheckpoint.source == "MZONEX_N1").order_by(
            CollecteCheckpoint.updated_at.desc())).all()
    aud = db.scalars(select(AuditLog).where(
        AuditLog.action == "collecte.echec_n1")).all()
    db.close()
    check("checkpoint N1 : la SOURCE et la PÉRIODE sont conservées",
          bool(cp) and (cp[0].fenetre_fin - cp[0].fenetre_debut
                        ).total_seconds() == PROFONDEUR_ATTENDUE,
          str([(c.source, str(c.fenetre_debut), str(c.fenetre_fin), c.statut)
               for c in cp[:2]]))
    check("checkpoint N1 marqué ECHEC avec son erreur",
          bool(cp) and cp[0].statut == "ECHEC" and bool(cp[0].derniere_erreur),
          str(cp[0].statut if cp else None))
    check("audit « collecte.echec_n1 » : source + période + profondeur",
          bool(aud) and aud[-1].details.get("source") == "MZONEX"
          and len(aud[-1].details.get("periode_utc") or []) == 2
          and aud[-1].details.get("profondeur_s") == PROFONDEUR_ATTENDUE,
          str(aud[-1].details)[:160] if aud else "aucun audit")

    # (b) un rattrapage réussi est audité avec ses périodes
    db = SessionLocal()
    nb_ev_av = db.scalar(select(func.count(EvenementGPS.id))) or 0
    db.close()
    scrapers._audit_collecte("collecte.relecture_n1", {
        "source": "MZONEX", "niveau": "N1_RELECTURE_TROUS", "jours": [str(JOUR)],
        "points_inseres": 2,
        "fenetres_relues": [{"debut": "2026-09-18T05:00:00",
                             "fin": "2026-09-18T05:15:00", "lus": 2, "points": 2}],
        "fenetres_total": 1, "issue": "RATTRAPAGE", "donnees_ingerees": 2})
    db = SessionLocal()
    aud2 = db.scalars(select(AuditLog).where(
        AuditLog.action == "collecte.relecture_n1")).all()
    db.close()
    check("audit « collecte.relecture_n1 » : source + périodes relues + volume",
          bool(aud2) and aud2[-1].details.get("source") == "MZONEX"
          and aud2[-1].details["fenetres_relues"][0]["debut"]
          and aud2[-1].details.get("points_inseres") == 2,
          str(aud2[-1].details)[:160] if aud2 else "aucun audit")
finally:
    scrapers.MZoneXTrajetsApiCollector = orig_api_c
    scrapers.MZoneXTrajetsCollector = orig_ecran_c
    for k, v in env_av.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

print("\n" + "=" * 74)
print(f"  test_fenetre_3h_v154 : {OK} OK / {KO} KO")
print("=" * 74)
sys.exit(1 if KO else 0)
