# -*- coding: utf-8 -*-
"""Tests v1.43 — §0nonies decies M1→M4 (arbitrages LSS du 29/08/2026) :
rattrapage des boîtiers muets (zone sans couverture GSM → tampon remonté tard).

[A] M2/M1 — REJOUAGE MÉCANIQUE du scénario 2746TCC du 28/08/2026 (géant
     provisoire 10:20→18:11 redécoupé en T2 10:20→13:06 / pause 1:11 /
     T3 14:17→18:11) ; variante géant NON rapprochable (début 10:26) →
     marqué REJETÉ (jamais supprimé) ; archive J-1 régénérée en place
     (audit « archive.raffraichie ») ; idempotence au cycle suivant ;
[B] M1 — garde de fenêtre : J-7 réconciliable, J-8 refusé ;
[C] M3 — fenêtre temps réel élargie à 3 h ;
[D] M1 — planification des relectures (J-1 à chaque cycle ; passe profonde
     J-7→J au démarrage puis horaire) ;
[E] M4 — audit « vehicule.muet_jour » unique par camion et par jour
     (jamais de faux positif pour un camion au repos) + âge GPS à l'écran ;
[F] garde-fou : loi gravée avant codage, version 1.43.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v143.db" SIM_ENABLE=0 python3 test_boitiers_muets_v143.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_v143.db")
import sys
from datetime import date, datetime, timedelta

DB_FILE = "/tmp/test_v143.db"
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


from sqlalchemy import func, select

from app.database import SessionLocal
from app.engine import auditer_boitiers_muets, ensure_suivi
from app.main import APP_VERSION
from app.models import (AuditLog, HistoriqueJournalier,
                        StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, Vehicule)
from app.reconciliation import (_dans_fenetre_minuit,
                                reconcilier_trajets_valides)
from app.seed import seed_si_vide
from app.serializers import s_suivi
import app.api_mzonex as apim
import app.scrapers as scrapers

seed_si_vide()

JOUR = date(2026, 8, 28)
MTN = datetime(2026, 8, 28, 19, 30, 0)

TRIPS_2746 = [   # verbatim portail (capture exploitant + sonde live 29/08)
    ("2026-08-28 06:08:20", "2026-08-28 06:15:56", 0.0),     # manœuvre (0 km)
    ("2026-08-28 06:17:05", "2026-08-28 08:36:47", 49.96),
    ("2026-08-28 08:46:20", "2026-08-28 09:44:53", 16.433),
    ("2026-08-28 10:20:37", "2026-08-28 12:07:26", 40.164),
    ("2026-08-28 12:22:04", "2026-08-28 13:06:39", 16.524),
    ("2026-08-28 14:17:29", "2026-08-28 18:11:16", 75.515),
]


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _items(plaque):
    return [{"gps_associe": plaque, "plaque": plaque,
             "debut": _dt(a), "fin": _dt(b), "distance_km": d,
             "conducteur": "RAMBINITSOA Jean Charles",
             "source": "MZONEX"} for a, b, d in TRIPS_2746]


def _vehicule(db, plaque):
    v = db.scalar(select(Vehicule).where(Vehicule.plaque == plaque))
    if v is None:
        v = Vehicule(plaque=plaque, description=f"{plaque}/TEST",
                     gps_associe=plaque, plateforme_gps="MZONEX")
        db.add(v)
        db.commit()
    return v


def _provisoire(db, suivi, numero, d, f, km=8.0, geant_debut_10_26=False):
    t = Trajet(suivi_id=suivi.id, numero=numero, heure_debut=d, heure_fin=f,
               statut_source=StatutSourceTrajet.PROVISOIRE,
               statut_validation=StatutValidationTrajet.EN_ATTENTE,
               source_plateforme="MZONEX", distance_km=km)
    db.add(t)
    db.commit()
    return t


def _affichables(db, suivi_id):
    return [t for t in db.scalars(select(Trajet).where(
        Trajet.suivi_id == suivi_id).order_by(Trajet.heure_debut)).all()
        if t.statut_validation != StatutValidationTrajet.REJETE]


def _nettoyer_demo(db, vehicule_id, jour):
    """La seed de démonstration archive 6 jours : purger les reliques du
    camion cible pour rejouer le scénario À ISOLATION (base de test seule)."""
    db.query(HistoriqueJournalier).filter(
        HistoriqueJournalier.date_jour == jour,
        HistoriqueJournalier.vehicule_id == vehicule_id).delete()
    for s in db.scalars(select(SuiviJournalier).where(
            SuiviJournalier.date_jour == jour,
            SuiviJournalier.vehicule_id == vehicule_id)).all():
        db.query(Trajet).filter(Trajet.suivi_id == s.id).delete()
        db.delete(s)
    db.commit()


print("== [A] M2/M1 — rejouage mécanique du 2746TCC (28/08/2026) ==")
db = SessionLocal()
v = _vehicule(db, "2746TCC")
_nettoyer_demo(db, v.id, JOUR)
suivi = ensure_suivi(db, v, JOUR)
# état du soir tel que constaté chez l'exploitant : T1 déjà VALIDÉ le matin,
# géant PROVISOIRE 10:20→18:11 (boîtier muet 10:20→18:11, refermé par le
# seul point remonté à 18:11)
t1 = _provisoire(db, suivi, 1, _dt("2026-08-28 06:17:05"),
                 _dt("2026-08-28 09:44:53"), 66.4)
geant = _provisoire(db, suivi, 2, _dt("2026-08-28 10:20:37"),
                    _dt("2026-08-28 18:11:16"), 132.2)
# archive du soir DÉJÀ figée, lignes vides (état avant relecture)
h = HistoriqueJournalier(date_jour=JOUR, annee=2026, mois=8, vehicule_id=v.id,
                         donnees={"trajets": [], "nb_trajets": 0})
db.add(h)
db.commit()
geant_id = geant.id

stats = reconcilier_trajets_valides(db, _items("2746TCC"),
                                    username="test-v143", maintenant=MTN)
db.expire_all()
aff = _affichables(db, suivi.id)
fins = [t.heure_fin.strftime("%H:%M:%S") for t in aff]
statuts = {t.statut_source for t in aff}
# loi v3 AM-2 : chaque trajet officiel publié garde SA ligne en base (5 vraies
# lignes) ; l'écran FUSIONNE pour l'affichage < 30 min (E1 §0undecies) →
# 3 lignes visuelles [06:17→09:44 | 10:20→13:06 | 14:17→18:11], pauses 0:35
# et 1:11 — la vérité portail EXACTE, le géant a disparu
check("A1 5 lignes officielles affichables (vérité portail) — géant disparu",
      len(aff) == 5, f"{len(aff)} lignes, fins {fins}")
check("A2 fins exactes du portail : 08:36:47 / 09:44:53 / 12:07:26 / 13:06:39 / 18:11:16",
      fins == ["08:36:47", "09:44:53", "12:07:26", "13:06:39", "18:11:16"],
      str(fins))
check("A2 toutes VALIDÉES (source portail souveraine)",
      statuts == {StatutSourceTrajet.VALIDE}, str(statuts))
check("A3 le géant est REMPLACÉ en place (même enregistrement, fin 12:07:26)",
      any(t.id == geant_id and t.statut_source == StatutSourceTrajet.VALIDE
          and t.heure_fin.strftime("%H:%M:%S") == "12:07:26" for t in aff),
      "remplacement introuvable")
check("A4 pauses réelles en base : 573 s / 2144 s / 878 s / 4250 s / 0 (fin)",
      [t.pause_apres_s for t in aff] == [573, 2144, 878, 4250, 0],
      str([t.pause_apres_s for t in aff]))
check("A5 manœuvre 06:08 (0,0 km) ignorée — aucune ligne posée",
      stats["rejets"] >= 1 and not any(
          t.heure_debut == _dt("2026-08-28 06:08:20") for t in aff))
h2 = db.scalar(select(HistoriqueJournalier).where(
    HistoriqueJournalier.date_jour == JOUR,
    HistoriqueJournalier.vehicule_id == v.id))
check("A6 archive J-1 régénérée EN PLACE : 5 lignes dans le snapshot",
      bool(h2) and h2.donnees.get("nb_trajets") == 5
      and len(h2.donnees.get("trajets") or []) == 5,
      str(h2.donnees.get("nb_trajets")) if h2 else "archive absente")
aud_raff = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "archive.raffraichie")) or 0
check("A7 audit « archive.raffraichie » inscrit (avant → après)",
      aud_raff >= 1, f"{aud_raff}")

print("== [A8] géant non rapproché par le DÉBUT mais même FIN → absorbé puis découpé ==")
# v1.10 (déjà adoptée) : fin commune ± tolérance → absorption puis la série
# officielle découpe — la guérison existe déjà et reste souveraine
v9 = _vehicule(db, "0576TCD")
_nettoyer_demo(db, v9.id, JOUR)
suivi9 = ensure_suivi(db, v9, JOUR)
g9 = _provisoire(db, suivi9, 1, _dt("2026-08-28 10:26:00"),
                 _dt("2026-08-28 18:11:16"), 132.2)
reconcilier_trajets_valides(db, _items("0576TCD"),
                            username="test-v143", maintenant=MTN)
db.expire_all()
aff9 = _affichables(db, suivi9.id)
aud_f = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "trajet.fusion_historique")) or 0
check("A8a géant 10:26→18:11 absorbé par la fin → VALIDÉ sur l'officiel (v1.10)",
      [t.heure_fin.strftime("%H:%M:%S") for t in aff9]
      == ["08:36:47", "09:44:53", "12:07:26", "13:06:39", "18:11:16"],
      str([t.heure_fin for t in aff9]))
check("A8a aucun doublon (5 lignes exactement) + audit d'absorption inscrit",
      len(aff9) == 5 and aud_f >= 1, f"{len(aff9)} lignes, {aud_f} audit(s)")

print("== [A8b] M2 PUR — géant sans début NI fin officiels (10:26→18:15) → REJETÉ, jamais supprimé ==")
# vraie « orpheline » : début à 5m23s et fin à 3m44s des bornes officielles
# (> tolérance 2 min des rapprochements §2.4/v1.10) — cas M2 propre
v10 = _vehicule(db, "2736TCC")
_nettoyer_demo(db, v10.id, JOUR)
suivi10 = ensure_suivi(db, v10, JOUR)
g10 = _provisoire(db, suivi10, 1, _dt("2026-08-28 10:26:00"),
                  _dt("2026-08-28 18:15:00"), 132.2)
stats10 = reconcilier_trajets_valides(db, _items("2736TCC"),
                                      username="test-v143", maintenant=MTN)
db.expire_all()
aff10 = _affichables(db, suivi10.id)
g10x = db.get(Trajet, g10.id)
aud_g = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "trajet.geant_rejete")) or 0
check("A8b géant marqué REJETÉ mais CONSERVÉ (flag §3.2, jamais de suppression)",
      g10x.statut_validation == StatutValidationTrajet.REJETE
      and stats10.get("geants", 0) == 1,
      f"{g10x.statut_validation}, geants={stats10.get('geants')}")
check("A8b la série officielle découpe à sa place (5 lignes, pause 1:11 en base)",
      [t.heure_fin.strftime("%H:%M:%S") for t in aff10]
      == ["08:36:47", "09:44:53", "12:07:26", "13:06:39", "18:11:16"]
      and aff10[3].pause_apres_s == 4250,
      str([t.heure_fin for t in aff10]))
check("A8b audit « trajet.geant_rejete » (avant → après) inscrit", aud_g >= 1,
      f"{aud_g}")

print("== [A9] idempotence — même relecture au cycle suivant ==")
stats_b = reconcilier_trajets_valides(db, _items("2746TCC"),
                                      username="test-v143",
                                      maintenant=MTN + timedelta(minutes=15))
db.expire_all()
aff_b = _affichables(db, suivi.id)
check("A9 ni doublon ni fluctuation (toujours 5 lignes, mêmes fins)",
      len(aff_b) == 5
      and [t.heure_fin.strftime("%H:%M:%S") for t in aff_b]
      == ["08:36:47", "09:44:53", "12:07:26", "13:06:39", "18:11:16"])
check("A9 aucun géant re-créé (stats géants nulles au 2ᵉ passage)",
      stats_b.get("geants", 0) == 0, str(stats_b.get("geants")))
db.close()

print("== [B] M1 — garde de fenêtre de réconciliation (7 jours) ==")
db = SessionLocal()
seuils = {}
now = datetime(2026, 8, 29, 7, 0, 0)
check("B1 jour courant réconciliable",
      _dans_fenetre_minuit(seuils, date(2026, 8, 29), now))
check("B2 J-3 réconciliable (relecture)",
      _dans_fenetre_minuit(seuils, date(2026, 8, 26), now))
check("B3 J-7 réconciliable (bord de fenêtre)",
      _dans_fenetre_minuit(seuils, date(2026, 8, 22), now))
check("B4 J-8 REFUSÉ (hors fenêtre arbitrée)",
      not _dans_fenetre_minuit(seuils, date(2026, 8, 21), now))

print("== [C] M3 — fenêtre temps réel Niveau 1 = 3 heures ==")
check("C1 FENETRE_MAX_S = 10 800 s", apim.FENETRE_MAX_S == 10800,
      str(apim.FENETRE_MAX_S))
du, fu = apim.ApiMZoneX(jetons=None).fenetre_incrementale(None, now)
check("C2 plancher incrémental = maintenant − 3 h (et non −30 min)",
      (fu - du).total_seconds() == 10800, f"{(fu - du).total_seconds()}")

print("== [D] M1 — planification des relectures ==")
scrapers._DERNIERE_PASSE_PROFONDE = 0.0
j1 = scrapers._jours_a_relire(now)
j2 = scrapers._jours_a_relire(now + timedelta(minutes=15))
check("D1 passe PROFONDE au démarrage : J-7 → J (8 jours, croissant)",
      len(j1) == 8 and j1[0] == date(2026, 8, 22)
      and j1[-1] == date(2026, 8, 29), str(j1))
check("D2 cycles ordinaires : J-1 et J uniquement",
      j2 == [date(2026, 8, 28), date(2026, 8, 29)], str(j2))

print("== [E] M4 — repère « boîtier muet — données en transit » ==")
from app.config import now_local as _now
v_muet = db.scalar(select(Vehicule).where(Vehicule.plaque == "2746TCC"))
v_muet.last_event_at = _now() - timedelta(hours=2)        # a émis ce matin puis muet
v_repos = _vehicule(db, "3046TBS")
v_repos.last_event_at = _now() - timedelta(days=1)        # rien émis aujourd'hui
db.commit()
mtn_e = _now()
n1 = auditer_boitiers_muets(db, maintenant=mtn_e)
n2 = auditer_boitiers_muets(db, maintenant=mtn_e + timedelta(minutes=15))
aud_muet = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "vehicule.muet_jour",
    AuditLog.details.like('%"2746TCC"%'))) or 0
aud_repos = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "vehicule.muet_jour",
    AuditLog.details.like('%"3046TBS"%'))) or 0
check("E1 boîtier muet (a émis à 07:00, silence > 30 min) → 1 audit",
      n1 >= 1 and aud_muet == 1, f"n1={n1} aud={aud_muet}")
check("E2 jamais de spam : 2ᵉ appel → aucune nouvelle écriture",
      n2 == 0 and aud_muet == 1, f"n2={n2}")
check("E3 camion au repos (rien émis du jour) → JAMAIS signalé",
      aud_repos == 0, f"{aud_repos}")
seuils_e = {}
s_e = ensure_suivi(db, v_muet, date(2026, 8, 29))
db.refresh(s_e)
js = s_suivi(s_e, seuils_e)
from app.config import now_local
age_reel = int((now_local() - v_muet.last_event_at).total_seconds())
check("E4 l'écran reçoit l'âge GPS réel (badge orange > 30 min)",
      isinstance(js.get("gps_age_s"), int)
      and abs(js["gps_age_s"] - age_reel) <= 10
      and js["gps_age_s"] > 1800,
      f"{js.get('gps_age_s')} vs {age_reel}")
db.close()

print("== [F] garde-fou version & loi ==")
# v1.44 (réalignement déclaré) : la version peut être ≥ 1.43 (suite de la
# plateforme) — la sévérité est inchangée, on vérifie le palier minimal.
def _v_tuple(ver: str):
    try:
        return tuple(int(p) for p in ver.split(".")[:2])
    except ValueError:
        return (0, 0)
check("F1 APP_VERSION ≥ 1.43", _v_tuple(APP_VERSION) >= (1, 43), APP_VERSION)
loi = open("/home/user/REFERENCE_IA_REGLES.md", encoding="utf-8").read()
check("F2 §0nonies decies gravé AVANT codage (§A.9)",
      "0nonies decies" in loi and "rattrapage des boîtiers muets" in loi)
check("F3 loi : audits et repères des chantiers figurés",
      "archive.raffraichie" in loi and "vehicule.muet_jour" in loi)
check("F3b loi : relecture J-1 chaque cycle + J-2→J-7 horaire + fenêtre 3 h + badge",
      "J-2 → J-7" in loi and "3 heures" in loi and "boîtier muet" in loi)
import re as _re
login = open("/home/user/frontend/src/pages/Login.tsx", encoding="utf-8").read()
_m = _re.search(r"v1\.(\d+)", login)
check("F4 bandeau de connexion ≥ v1.43", bool(_m) and int(_m.group(1)) >= 43,
      _m.group(0) if _m else "aucun")

print(f"\n==== {R['ok']} OK / {R['ko']} KO ====")
try:
    os.remove(DB_FILE)
except OSError:
    pass
sys.exit(0 if R["ko"] == 0 else 1)
