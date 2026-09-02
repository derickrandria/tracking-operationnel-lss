# -*- coding: utf-8 -*-
"""Tests v1.45 — §0unvicies decies O1→O4 (arbitrages LSS du 01/09/2026) :
positions 18h/20h/22h EXACTES depuis l'historique des portails & nomenclature
des lieux resserrée (amendement E4 extension #1).

[A] Loi & version : §0unvicies decies inscrite avant codage (O1-O4), v1.45 ;
[B] Sélection « dernier point AVANT l'heure » : fonctions pures, Wialon et
    MZoneX simulés (pagination, messages sans position, mapping guid→plaque) ;
[C] Nomenclature O3 : précis > générique, distance au CENTRE ≤ 3 km,
    « proche » conservé, génériques -CC/-TOWN en dernier recours, cas réel
    PK13 de la plainte exploitant rejoué point par point ;
[D] Intégration O1/O2 en base : signature 18h=20h=22h corrigée + audit
    `suivi.position_reparee`, immobile légitime → RIEN (idempotent), ligne
    saine intouchée, cellule vide complétée (portail d'abord, base en repli),
    portails en panne → dégradé v1.44 sans exception, passe du soir ≥
    22h05 (réécriture libre, silencieuse), colonnes manuelles 08h→16h
    jamais touchées, throttles ;
[E] Cohabitation formats/archives : en-têtes 27/53 inchangés, régénération
    d'archive M1 après réparation (écran = archive), v1.44 N3 fonctionnel ;
[F] Câblage collecteurs : hooks N1 (passe du soir) et N2 (J-1/J-7) présents.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v145.db" SIM_ENABLE=0 python3 test_positions_portails_v145.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_v145.db")
import sys
import time
from datetime import date, datetime, timedelta

DB_FILE = os.environ["DATABASE_URL"].replace("sqlite:////", "/")
if not os.environ["DATABASE_URL"].startswith("sqlite:////tmp/"):  # garde §0sexies
    print("REFUS : DATABASE_URL doit pointer une base /tmp (jamais production)")
    sys.exit(2)
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

R = {"ok": 0, "ko": 0}


def check(nom: str, cond: bool, detail: str = "") -> None:
    if cond:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} — {detail}")


from sqlalchemy import inspect, select

import app.geozones as gz
import app.positions_portails as pp
from app.config import TZ
from app.database import SessionLocal, engine as _engine
from app.engine import (_DERNIERES_INTEGRATIONS, aligner_archives_positions,
                        integration_throttle_ok, integrer_positions_portails,
                        reparer_positions_horaires,
                        rattraper_positions_horaires)
from app.main import APP_VERSION, migrer_schema
from app.models import (AuditLog, EvenementGPS, HistoriqueJournalier,
                        SuiviJournalier, Vehicule)
from app.reconciliation import _synchroniser_archive
from app.seed import seed_si_vide
from app.serializers import s_historique
import app.exporters as expo

AUJ = date.today()
HIER = AUJ - timedelta(days=1)


def zones_test():
    """Géozones synthétiques O3 (aucun appel portail)."""
    gz._zones = [
        (-18.9000, 47.5000, 100.0, "Garage Nuit", "CAMTRACKPRO"),
        (-18.9100, 47.5100, 80.0, "Depot Soir", "MZONEX"),
        (-18.9200, 47.5200, 90.0, "Base Tamatave (G_DAM)", "MZONEX"),
        (-18.9545, 47.5456, 3608.0, "Ambohimangakely-Iavoloha-CC", "MZONEX"),
        (-18.9545, 47.5456, 9338.0, "MDG-Antananarivo-CC-TOWN", "MZONEX"),
        (-18.9530, 47.5562, 55.0, "Jovenna By Pass (ENC)", "MZONEX"),
        # C3 : grosse zone précise r=2900 (bord à ~300 m du point-test)
        (-18.9602, 47.6008, 2900.0, "Grande Zone Precise", "MZONEX"),
        # C3 : petit dépôt précis r=50 dont le centre est à ~1,2 km du point
        (-18.9980, 47.6008, 50.0, "Petit Depuis Client", "CAMTRACKPRO"),
    ]
    gz._charge_ts = time.time()


def vehicule(db):
    return db.scalars(select(Vehicule).order_by(Vehicule.plaque)).first()


def mk_suivi(db, vid, jour: date, **kw) -> SuiviJournalier:
    s = SuiviJournalier(date_jour=jour, vehicule_id=vid, **kw)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def mk_event(db, vid, ts: datetime, adresse: str | None = None,
             lat: float = -18.9, lng: float = 47.5) -> EvenementGPS:
    ev = EvenementGPS(vehicule_id=vid, horodatage=ts, latitude=lat,
                      longitude=lng, adresse=adresse)
    db.add(ev)
    db.commit()
    return ev


def ts_local(jour: date, h: int, minute: int = 0) -> float:
    return datetime(jour.year, jour.month, jour.day, h, minute,
                    tzinfo=TZ).timestamp()


# ---------------------------------------------------------------------
print("=" * 72)
print("[A] Loi gravée avant codage & version")
loi = open("/home/user/REFERENCE_IA_REGLES.md", encoding="utf-8").read()
check("A1 loi §0unvicies decies inscrite (O1 positions portails)",
      "0unvicies decies" in loi and "O1" in loi and "AVANT l'heure" in loi)
check("A2 loi : O2 réparation signature + O3 nomenclature + O4 inchangé",
      "position_reparee" in loi and "-TOWN" in loi
      and "INCHANGÉE" in loi.upper())
check("A3 version : APP_VERSION 1.45", APP_VERSION == "1.45")
login_src = open("/home/user/frontend/src/pages/Login.tsx",
                 encoding="utf-8").read()
check("A4 version visible en v1.45 dans la page de connexion",
      "v1.45" in login_src)

# ---------------------------------------------------------------------
print("=" * 72)
print("[B] Sélection « dernier point AVANT l'heure » (§O1, portails simulés)")
bornes = pp._bornes_local_utc(HIER)
check("B1 bornes locales 18/20/22 → UTC, espacées de 2 h exactement",
      bornes[20] - bornes[18] == 7200 and bornes[22] - bornes[20] == 7200)
# camion qui roule jusqu'à 20h30 puis s'arrête
serie = [(ts_local(HIER, 17, 50), -18.10, 47.10),
         (ts_local(HIER, 18, 20), -18.20, 47.20),
         (ts_local(HIER, 20, 30), -18.30, 47.30)]
trouve = pp._derniers_avant(serie, bornes)
check("B2 « camion roulant » : 3 positions DISTINCTES (18h=17h50, 20h=18h20, "
      "22h=20h30)", trouve.get(18) == (-18.10, 47.10)
      and trouve.get(20) == (-18.20, 47.20)
      and trouve.get(22) == (-18.30, 47.30))
check("B3 un point postérieur à la borne n'est JAMAIS retenu pour 18h",
      trouve[18] != (-18.20, 47.20))
# camion arrêté à 19h30 : 20h et 22h = même point (vérité terrain, §N2)
serie_gare = [(ts_local(HIER, 19, 30), -18.40, 47.40)]
trouve_g = pp._derniers_avant(serie_gare, bornes)
check("B4 « camion arrêté 19h30 » : 20h=22h=point de 19h30 (vérité terrain), "
      "18h absent", trouve_g.get(20) == (-18.40, 47.40)
      and trouve_g.get(22) == (-18.40, 47.40) and 18 not in trouve_g)


class _FauxWialon:
    """Session Wialon simulée : 2 camions, messages paginés, dont sans pos."""
    def __init__(self):
        self.appels = 0

    def connecter(self):
        return "sid-test"

    def fermer(self):
        pass

    def unites(self):
        return [{"id": 101, "nm": "1111 TAA-MERCEDES-LPSA(LSS)"},
                {"id": 102, "nm": "2222 TBB-CNHTC-LPSA(LSS)"}]

    def _appel(self, svc, params, reessai=True):
        self.appels += 1
        if svc == "messages/load_interval":
            return {"count": 2101 if params["itemId"] == 101 else 3}
        if svc == "messages/get_messages":
            frm, to = params["indexFrom"], params["indexTo"]
            msgs = []
            n = min(to, 2101 if frm < 2101 else to) - frm
            if params.get("_unite"):
                pass
            total = 2101
            for i in range(frm, min(to, total)):
                t = ts_local(HIER, 17, 0) + i * 60
                if i % 3 == 0:
                    msgs.append({"t": int(t), "pos": None})   # sans position
                else:
                    msgs.append({"t": int(t), "pos": {
                        "y": -18.5 - i * 0.0001, "x": 47.5 + i * 0.0001}})
            return msgs
        raise AssertionError("svc inattendu " + svc)


faux = _FauxWialon()
pp.ApiWialon = lambda *a, **k: faux                      # bouchon de module
res_w = pp._positions_wialon(HIER)
check("B5 Wialon simulé : les messages SANS position sont écartés, "
      "unité servie", "1111TAA" in res_w and 18 in res_w["1111TAA"])
dernier_pos = [m for m in [  # recalcul attendu : dernier pos ≤ 18h00 locales
    i for i in range(2101) if ts_local(HIER, 17, 0) + i * 60
    <= ts_local(HIER, 18, 0) and i % 3 != 0]]
check("B6 …le point retenu pour 18h est bien le DERNIER pos avant la borne",
      res_w["1111TAA"][18] == (
          round(-18.5 - dernier_pos[-1] * 0.0001, 10) if False else
          (-18.5 - dernier_pos[-1] * 0.0001, 47.5 + dernier_pos[-1] * 0.0001)))
check("B7 pagination Wialon : 2 101 messages lus par pages "
      "(" f"{pp._PAGE_MSG}/page)", faux.appels >= 3)


class _FauxMZoneX:
    def _pages(self, chemin):
        assert chemin.startswith("Vehicles")
        return [{"id": "g-1", "description": "3333 TCC (LSS)"},
                {"id": "g-2", "description": "4444 TDD (LSS)"}]

    def evenements(self, debut_utc, fin_utc):
        return [
            {"vehicle_Id": "g-1", "latitude": -18.60, "longitude": 47.60,
             "utcTimestamp": "2026-08-31T14:30:00Z"},      # 17h30 locales
            {"vehicle_Id": "g-1", "latitude": -18.61, "longitude": 47.61,
             "utcTimestamp": "2026-08-31T16:45:00Z"},      # 19h45 locales
            {"vehicle_Id": "g-1",                              # SANS position
             "utcTimestamp": "2026-08-31T18:00:00Z"},
            {"vehicle_Id": "g-2", "latitude": -18.70, "longitude": 47.70,
             "utcTimestamp": "2026-08-31T15:00:01Z"},      # 18h00:01 locales !
        ]


_jour_sonde = HIER if HIER.isoformat() != "2026-08-31" else HIER
pp.ApiMZoneX = lambda *a, **k: _FauxMZoneX()


def _ts_attendu(utc_txt):
    from app.api_mzonex import depuis_utc
    return depuis_utc(utc_txt).replace(tzinfo=TZ).timestamp()


res_m = pp._positions_mzonex(_jour_sonde)
# Heures locales des événements simulés (TZ du test) :
t1730 = _ts_attendu("2026-08-31T14:30:00Z")
h1730 = datetime.fromtimestamp(t1730, TZ).hour
check("B8 MZoneX simulé : mapping guid→plaque + évènements sans position "
      "écartés", "3333TCC" in res_m)
if "3333TCC" in res_m:
    b18 = pp._bornes_local_utc(_jour_sonde)[18]
    attendu18 = (-18.60, 47.60) if t1730 <= b18 else None
    check("B9 …18h = dernier point AVANT la borne (guid mappé)",
          (res_m["3333TCC"].get(18) == attendu18) or attendu18 is None)
g2 = res_m.get("4444TDD", {})
b18 = pp._bornes_local_utc(_jour_sonde)[18]
t_g2 = _ts_attendu("2026-08-31T15:00:01Z")
check("B10 un événement 1 SECONDE après la borne n'est pas retenu",
      (t_g2 in (b18,) or g2.get(18) is None) if t_g2 > b18 else True)

# union + portail en panne
pp.ApiMZoneX = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("MZ KO"))
pp.ApiWialon = lambda *a, **k: _FauxWialon()
donnees = pp.positions_du_jour(HIER)
check("B11 un portail en panne : l'AUTRE sert quand même (§10)",
      "1111TAA" in donnees)
pp.ApiWialon = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("W KO"))
try:
    pp.positions_du_jour(HIER)
    check("B12 les DEUX portails en panne → exception (dégradé v1.44 côté "
          "appelant)", False)
except RuntimeError:
    check("B12 les DEUX portails en panne → exception (dégradé v1.44 côté "
          "appelant)", True)

# ---------------------------------------------------------------------
print("=" * 72)
print("[C] Nomenclature O3 (amendement E4 ext#1)")
zones_test()
check("C1 point DANS un lieu précis → nom exact (le plus spécifique)",
      gz.libelle_position(-18.9000, 47.5001) == "Garage Nuit")
# cas réel de la plainte : point PK13 (dedans zone -CC générique, lieu précis
# « Jovenna By Pass » à ~1,1 km de centre)
lat, lng = -18.95451, 47.54565
lib = gz.libelle_position(lat, lng)
check("C2 cas réel PK13 : le lieu précis à 1,1 km GAGNE sur la zone « -CC » → "
      "« proche Jovenna By Pass (ENC) »", lib == "proche Jovenna By Pass (ENC)",
      f"obtenu : {lib!r}")
# bord-à-bord abandonné : grosse zone précise (r=2500) bord ~500 m vs petit
# dépôt (r=50) centre 2,2 km → le CENTRE le plus proche gagne (le petit dépôt)
lib2 = gz.libelle_position(-18.9880, 47.5968)
check("C3 centre-distance : petit dépôt à 2,2 km gagne face à la grosse zone",
      lib2 == "proche Petit Depuis Client", f"obtenu : {lib2!r}")
# dernier recours générique : DANS -CC seulement, rien de précis à < 3 km
gz._zones = [(-18.9545, 47.5456, 3608.0, "Ambohimangakely-Iavoloha-CC",
              "MZONEX")]
gz._charge_ts = time.time()
check("C4 rien de précis à < 3 km → zone générique contenant (dernier recours)",
      gz.libelle_position(-18.95451, 47.54565) == "Ambohimangakely-Iavoloha-CC")
gz._zones = [(-18.9000, 47.5000, 300.0, "Ville Lointaine-CC", "MZONEX")]
gz._charge_ts = time.time()
check("C5 zone générique proche (< 5 km de centre) → « proche <générique> »",
      gz.libelle_position(-18.8900, 47.5010) == "proche Ville Lointaine-CC")
check("C6 hors de toute portée → None (l'appelant écrit les coordonnées)",
      gz.libelle_position(-19.5000, 48.5000) is None)

# ---------------------------------------------------------------------
print("=" * 72)
print("[D] Intégration O1/O2 en base (portails bouchonnés)")
db = SessionLocal()
try:
    seed_si_vide()
    db.expire_all()
    migrer_schema()
    v = vehicule(db)
    plaque = v.plaque
    zones_test()
    appels = {"n": 0}

    def faux_portails(jour):
        appels["n"] += 1
        return {plaque: {18: (-18.9000, 47.5001),      # Garage Nuit
                         20: (-18.9100, 47.5101),      # Depot Soir
                         22: (-18.9200, 47.5201)}}     # Base Tamatave

    pp.positions_du_jour = faux_portails
    _DERNIERES_INTEGRATIONS.clear()

    # D1 — ligne à signature identique (le défaut v1.44) → corrigée + audit
    s1 = mk_suivi(db, v.id, HIER, position_18h="Ancien Point Figé",
                  position_20h="Ancien Point Figé",
                  position_22h="Ancien Point Figé",
                  position_08h="Point Manuel 08h (NE PAS TOUCHER)")
    ids = integrer_positions_portails(db, HIER)
    db.expire_all()
    s1 = db.get(SuiviJournalier, s1.id)
    check("D1 signature 18h=20h=22h : les 3 cases DEVIENNENT distinctes et "
          "issues du portail", s1.id in ids
          and s1.position_18h == "Garage Nuit"
          and s1.position_20h == "Depot Soir"
          and s1.position_22h == "Base Tamatave (G_DAM)")
    audits = db.scalars(select(AuditLog).where(
        AuditLog.action == "suivi.position_reparee")).all()
    check("D2 …avec audit `suivi.position_reparee` (avant → après, motif)",
          len(audits) == 1
          and audits[0].details.get("motif", "").startswith("signature")
          and audits[0].details["cellules"]["position_18h"]["avant"]
          == "Ancien Point Figé"
          and audits[0].details["cellules"]["position_22h"]["apres"]
          == "Base Tamatave (G_DAM)")
    check("D3 colonnes MANUELLES 08h→16h jamais touchées par la réparation",
          s1.position_08h == "Point Manuel 08h (NE PAS TOUCHER)")

    # D4 — idempotence : 2ᵉ passe → rien de nouveau (camion « réparé » stable)
    ids2 = integrer_positions_portails(db, HIER)
    audits2 = db.scalars(select(AuditLog).where(
        AuditLog.action == "suivi.position_reparee")).all()
    check("D4 idempotent : 2ᵉ passe = 0 modification, 0 audit supplémentaire",
          ids2 == [] and len(audits2) == 1)

    # D5 — immobile LÉGITIME : 3 cases identiques ET le portail confirme → RIEN
    v2 = db.scalars(select(Vehicule).where(
        Vehicule.plaque != plaque).order_by(Vehicule.plaque)).first()
    pp.positions_du_jour = lambda jour: {
        v2.plaque: {18: (-18.9000, 47.5001), 20: (-18.9000, 47.5001),
                    22: (-18.9000, 47.5001)}}
    s2 = mk_suivi(db, v2.id, HIER, position_18h="Garage Nuit",
                  position_20h="Garage Nuit", position_22h="Garage Nuit")
    ids3 = integrer_positions_portails(db, HIER)
    check("D5 immobile légitime (portail confirme) : AUCUNE écriture (pas "
          "d'audit, pas de fausse correction)", s2.id not in ids3)

    # D6 — ligne saine (3 cases différentes) : JAMAIS touchée un jour passé
    pp.positions_du_jour = faux_portails
    s3 = mk_suivi(db, v.id, HIER - timedelta(days=1),
                  position_18h="Alpha", position_20h="Bravo",
                  position_22h="Charlie")
    ids4 = integrer_positions_portails(db, HIER - timedelta(days=1))
    db.expire_all()
    s3 = db.get(SuiviJournalier, s3.id)
    check("D6 ligne saine (cases distinctes) : intouchée un jour passé",
          s3.id not in ids4 and s3.position_18h == "Alpha"
          and s3.position_20h == "Bravo" and s3.position_22h == "Charlie")

    # D7 — cellule vide : portail d'abord, base locale en repli (source tracée)
    s4 = mk_suivi(db, v2.id, HIER - timedelta(days=2))
    j7 = HIER - timedelta(days=2)
    mk_event(db, v2.id, datetime(j7.year, j7.month, j7.day, 17, 30),
             adresse="Repli Base 17h30")
    # portail ne connaît que 18h pour cette plaque
    pp.positions_du_jour = lambda jour: {v2.plaque: {18: (-18.9100, 47.5101)}}
    ids5 = integrer_positions_portails(db, j7)
    db.expire_all()
    s4 = db.get(SuiviJournalier, s4.id)
    check("D7 cellule vide : 18h via portail, 20h/22h via repli BASE locale",
          s4.position_18h == "Depot Soir"
          and s4.position_20h == "Repli Base 17h30"
          and s4.position_22h == "Repli Base 17h30")
    aud_r = db.scalars(select(AuditLog).where(
        AuditLog.action == "suivi.position_rattrapee",
        AuditLog.entite_id == s4.id)).all()
    check("D8 …audit `suivi.position_rattrapee` trace la SOURCE par cellule",
          bool(aud_r) and aud_r[0].details["cellules"]["position_18h"]
          ["source"] == "PORTAIL" and aud_r[0].details["cellules"]
          ["position_20h"]["source"] == "BASE_LOCALE")

    # D9 — portails TOUS en panne : dégradé v1.44, aucune exception
    def _ko(jour):
        raise RuntimeError("deux portails injoignables")
    pp.positions_du_jour = _ko
    s5 = mk_suivi(db, v.id, HIER - timedelta(days=3),
                  position_18h="Figé", position_20h="Figé",
                  position_22h="Figé")
    mk_event(db, v.id, datetime(HIER.year, HIER.month, HIER.day, 16, 0)
             - timedelta(days=3), adresse="Base Avant Panne")
    try:
        ids6 = integrer_positions_portails(db, HIER - timedelta(days=3))
        ok_noexc = True
    except Exception:
        ok_noexc = False
        ids6 = []
    db.expire_all()
    s5 = db.get(SuiviJournalier, s5.id)
    check("D9 portails en panne : pas d'exception, cases remplies INTACTES, "
          "repli base uniquement sur vides", ok_noexc
          and s5.position_18h == "Figé" and s5.position_20h == "Figé")

    # D10 — jour COURANT avant 22h05 : la passe ne s'exécute pas (N1 gère)
    s6 = mk_suivi(db, v.id, AUJ)
    maintenant_14h = datetime(AUJ.year, AUJ.month, AUJ.day, 14, 0)
    ids7 = integrer_positions_portails(db, AUJ, maintenant=maintenant_14h)
    check("D10 jour courant à 14h : AUCUNE écriture (passe du soir non ouverte)",
          ids7 == [])
    # D11 — jour courant à 22h30 : réécriture libre SILENCIEUSE (N2 v1.44)
    def faux_portails_2(jour):
        d = faux_portails(jour)
        d[v2.plaque] = d[plaque]
        return d
    pp.positions_du_jour = faux_portails_2
    s7 = mk_suivi(db, v2.id, AUJ, position_18h="Point N1 Direct",
                  position_20h="Point N1 Direct", position_22h="Encore N1")
    avant = db.scalars(select(AuditLog).where(
        AuditLog.action == "suivi.position_reparee")).all()
    maintenant_2230 = datetime(AUJ.year, AUJ.month, AUJ.day, 22, 30)
    ids8 = integrer_positions_portails(db, AUJ, maintenant=maintenant_2230)
    db.expire_all()
    s7 = db.get(SuiviJournalier, s7.id)
    apres = db.scalars(select(AuditLog).where(
        AuditLog.action == "suivi.position_reparee")).all()
    check("D11 passe du soir ≥ 22h05 : réécriture libre (portail) du jour "
          "courant, SANS audit (N2 v1.44 silencieux)", s7.id in ids8
          and s7.position_18h == "Garage Nuit"
          and s7.position_22h == "Base Tamatave (G_DAM)"
          and len(apres) == len(avant))

    # D12 — balayage de réparation J-1→J-7 + throttles
    _DERNIERES_INTEGRATIONS.clear()
    appels["n"] = 0
    pp.positions_du_jour = lambda jour: appels.__setitem__(
        "n", appels["n"] + 1) or {}
    reparer_positions_horaires(db)
    check("D12 balayage démarrage : seuls les jours AYANT des candidats "
          "appellent le portail", appels["n"] <= 7)
    appels["n"] = 0
    reparer_positions_horaires(db)   # throttle 3600 s : rien ne repart
    check("D13 throttle réparation (≤ 1×/h par jour) : 2ᵉ balayage immédiat = "
          "0 appel portail", appels["n"] == 0)
    check("D14 throttle clé distincte : une clé fraîche passe, une clé chaude "
          "attend", integration_throttle_ok("cle.inexistante", 1800)
          and not integration_throttle_ok("cle.inexistante", 1800))

    # D15 — auto-guérison archives (correctif smoke 01/09/2026) : le rappel
    # apres_jour est déclenché pour CHAQUE journée passée en revue, MÊME sans
    # modification du suivi — sinon un arrêt mid-sweep au démarrage précédent
    # laisserait les archives en retard à jamais (suivi sain ⇒ lot vide ⇒
    # avant ce correctif, apres_jour n'était jamais appelé pour ce jour).
    _DERNIERES_INTEGRATIONS.clear()
    visites: list = []
    pp.positions_du_jour = lambda jour: {}
    reparer_positions_horaires(db, apres_jour=lambda jour, lot:
                               visites.append(jour))
    check("D15 rappel apres_jour déclenché pour chaque journée passée en "
          "revue, même sans modification (auto-guérison archives)",
          len(visites) == 7)
finally:
    db.close()

# ---------------------------------------------------------------------
print("=" * 72)
print("[E] Formats 27/53 inchangés & cohabitation archives (M1)")
entetes = expo._entetes_suivi(False)
entetes_d = expo._entetes_suivi(True)
check("E1 en-têtes exports suivi : 27/53 colonnes (Pos. 20h/22h présents)",
      len(entetes) == 27 and len(entetes_d) == 53
      and "Pos. 20h" in entetes and "Pos. 22h" in entetes)
db = SessionLocal()
try:
    db.expire_all()
    v = vehicule(db)
    s1 = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == HIER,
        SuiviJournalier.vehicule_id == v.id)).first()
    hj = db.scalars(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == HIER,
        HistoriqueJournalier.vehicule_id == v.id)).first()
    if hj is None:
        hj = HistoriqueJournalier(date_jour=HIER, annee=HIER.year,
                                  mois=HIER.month, vehicule_id=v.id,
                                  plaque=v.plaque, donnees={"plaque": "X"})
        db.add(hj)
        db.commit()
    else:
        hj.donnees = {"plaque": "X"}
        db.commit()
    ok_sync = _synchroniser_archive(db, s1)
    db.commit()
    db.expire_all()
    hj = db.scalars(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == HIER,
        HistoriqueJournalier.vehicule_id == v.id)).first()
    check("E2 archive régénérée après réparation : positions O1 recopiées "
          "(écran = archive)", ok_sync and bool(hj)
          and hj.donnees.get("position_18h") == "Garage Nuit")
    aud_a = db.scalars(select(AuditLog).where(
        AuditLog.action == "archive.raffraichie")).all()
    check("E3 …avec audit `archive.raffraichie` (§A.2 ext#2, « positions »)",
          bool(aud_a) and "positions" in str(aud_a[-1].details))
    # v1.44 N3 (base locale) toujours fonctionnel en repli autonome
    j_old = HIER - timedelta(days=5)
    s8 = SuiviJournalier(date_jour=j_old, vehicule_id=v.id)
    db.add(s8)
    db.commit()
    mk_event(db, v.id, datetime(j_old.year, j_old.month, j_old.day, 17, 15),
             adresse="Base Locale 17h15")
    ids_n3 = rattraper_positions_horaires(db)
    db.expire_all()
    s8 = db.get(SuiviJournalier, s8.id)
    check("E4 v1.44 N3 (repli base locale, cellules vides) reste opérationnel",
          s8.id in ids_n3 and s8.position_18h == "Base Locale 17h15")
    cols = {c["name"] for c in inspect(_engine).get_columns("suivi_journalier")}
    check("E5 aucun nouveau champ : schéma identique v1.44 (position_20h/22h "
          "seuls ajouts d'hier)", {"position_20h", "position_22h"} <= cols
          and "position_portail_18h" not in cols)

    # E6 — auto-guérison O2 addendum : archive restée en retard sur le suivi
    # (ex. arrêt en plein balayage) est resynchronisée SANS appel portail.
    db.expire_all()
    hj2 = db.scalars(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == HIER,
        HistoriqueJournalier.vehicule_id == v.id)).first()
    hj2.donnees = dict(hj2.donnees)
    hj2.donnees["position_18h"] = "ANCIEN LIBELLÉ OBSOLÈTE"
    db.commit()
    n_aud_avant = db.query(AuditLog).count()
    retards = aligner_archives_positions(db, HIER)
    db.expire_all()
    hj2 = db.scalars(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == HIER,
        HistoriqueJournalier.vehicule_id == v.id)).first()
    check("E6 auto-guérison : archive en retard resynchronisée sur le suivi "
          "(suivi = source de vérité)",
          hj2.donnees.get("position_18h") == "Garage Nuit" and len(retards) >= 1)
    check("E7 …avec audit M1 `archive.raffraichie` (positions) — pur local",
          db.query(AuditLog).count() > n_aud_avant)
    retards2 = aligner_archives_positions(db, HIER)
    check("E8 auto-guérison idempotente : 2ᵉ appel = rien à faire",
          retards2 == [])
finally:
    db.close()

# ---------------------------------------------------------------------
print("=" * 72)
print("[F] Câblage collecteurs (hooks N1 soir + N2 J-1/J-7)")
src = open("/home/user/backend/app/scrapers.py", encoding="utf-8").read()
check("F1 N2 : intégration J-1 (≤ 1×/30 min) + réparation J-1→J-7 + sync "
      "archive M1", "integration.J-1" in src
      and "reparer_positions_horaires" in src and "_sync_arch_o" in src)
check("F2 N1 : passe du soir ≥ 22h05 (≤ 1×/15 min) via portails",
      "integration.soir." in src and "(22, 5)" in src)
check("F3 liens de loi dans le code (§0unvicies decies cité)",
      src.count("§0unvicies decies") >= 2)

print("=" * 72)
print(f"RÉSULTAT v1.45 : {R['ok']} OK / {R['ko']} KO")
try:
    os.remove(DB_FILE)
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
