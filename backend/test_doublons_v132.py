# -*- coding: utf-8 -*-
"""Tests v1.32 — §0duodecies (arbitrages LSS du 25/08/2026).

Réalignement v1.37 (27/08/2026, déclaré en CONFORMITÉ) : la vérification F4
(« actions automatiques » du rapport de diagnostic) ré-horodate à la
journée J les lignes d'audit créées par le test — le moteur les horodate
à l'horloge réelle, ce qui rendait la suite dépendante du calendrier
(detect KO le 27/08). Aucun autre sens de vérification ne change.

F1 : GARDE ABSOLUE anti-double-comptage — compteurs mesurés à l'UNION des
     intervalles (TCJ ≤ TTJ structurel, audit `trajet.chevauchement_detecte`,
     résultat STRICTEMENT identique à Σ durées quand rien ne se recouvre) ;
F2 : rattrapage « camion roulant sans ligne » — RÉACTIVATION de la ligne
     rejetée qui couvre encore l'instant (ouverte ou refermée < 20 min) au
     lieu d'empiler une ligne nouvelle ;
F3 : réparation unique du 25/08 — départage des lignes recouvrantes (VALIDÉ
     prime, puis début le plus tôt), rejet sans suppression, transfert de
     l'état « en cours » au gardien, marqueur `duplication_v132.terminee`,
     idempotent ;
F4 : rapport de diagnostic exportable (fichier texte, lecture seule).

Cas fil conducteur : la journée RÉELLE de 0926TBV du 25/08/2026 (capture
10:02 de l'exploitant : TCC=TCJ=5:13 > TTJ=4:26, DÉPART 05:34, cases « — »).

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v132.db" SIM_ENABLE=0 python3 test_doublons_v132.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.database import SessionLocal
from app import engine
from app.chaines import Segment, construire_journee, union_duree_s
from app.diagnostic import construire_rapport_diagnostic
from app.models import (AuditLog, EvenementGPS, HistoriqueJournalier,
                        StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, TypeEvenement, Vehicule)
from app.reparation import (JOUR_V132, MARQUEUR_V132,
                            executer_reparation_v132,
                            resoudre_chevauchements_jour)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.serializers import s_suivi

R = {"ok": 0, "ko": 0}


def check(nom, cond, info=""):
    if cond:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} {info}")


def check_eq(nom, got, want, info=""):
    check(nom, got == want, f"(obtenu={got!r} attendu={want!r}) {info}")


db_url = os.environ.get("DATABASE_URL", "")
if "/tmp/" not in db_url and "test" not in db_url:
    print("⛔ Sécurité : base de test uniquement (DATABASE_URL /tmp).")
    sys.exit(2)

engine.PUBLISH_ENABLED["on"] = False
# idempotence des RELANCES : un échec antérieur pouvait laisser la base /tmp
# derrière lui (résidus de véhicules) → on repart toujours d'un fichier neuf
_fichier = db_url.replace("sqlite:///", "")
if _fichier.startswith("/tmp/") and os.path.exists(_fichier):
    os.remove(_fichier)
seed_si_vide()
migrer_schema()
db = SessionLocal()

# ardoise vierge (déterminisme) : l'historique de démonstration du seed est purgé
from sqlalchemy import delete as _delete
from app.models import Alerte, Infraction, Mission
for modele in (Trajet, EvenementGPS, HistoriqueJournalier, SuiviJournalier,
               Alerte, AuditLog, Infraction, Mission):
    db.execute(_delete(modele))
db.commit()

PROV = StatutSourceTrajet.PROVISOIRE
VALI = StatutSourceTrajet.VALIDE
EN_AT = StatutValidationTrajet.EN_ATTENTE
REJ = StatutValidationTrajet.REJETE
V_OK = StatutValidationTrajet.VALIDE

J = date(2026, 8, 25)
REC = datetime(2026, 8, 25, 10, 0, 40)     # dernier événement de la capture


def _veh(plaque):
    v = db.scalar(select(Vehicule).where(Vehicule.plaque == plaque))
    if v is None:
        v = Vehicule(plaque=plaque, description=f"{plaque} (test)",
                     plateforme_gps="MZONEX")
        db.add(v)
        db.flush()
    return v


def _suivi(v, jour=J):
    s = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v.id,
        SuiviJournalier.date_jour == jour))
    if s is None:
        s = SuiviJournalier(date_jour=jour, vehicule_id=v.id)
        db.add(s)
        db.flush()
    return s


def _hj(h, m, sec=0):
    return datetime(2026, 8, 25, h, m, sec)


def _trajet(suivi, numero, debut, fin, src=PROV, val=EN_AT, dist=None):
    t = Trajet(suivi_id=suivi.id, numero=numero, heure_debut=debut,
               heure_fin=fin, statut_source=src, statut_validation=val,
               source_plateforme="MZONEX", distance_km=dist)
    db.add(t)
    db.flush()
    return t


# ===========================================================================
print("\n== A · union_duree_s — primitive F1 (pure) ==")
# ===========================================================================
check_eq("A1 union vide = 0", union_duree_s([]), 0)
check_eq("A2 un intervalle", union_duree_s([(_hj(5, 0), _hj(6, 0))]), 3600)
check_eq("A3 disjoints = somme",
         union_duree_s([(_hj(5, 0), _hj(6, 0)), (_hj(7, 0), _hj(8, 30))]), 9000)
check_eq("A4 recouvrants = union",
         union_duree_s([(_hj(5, 0), _hj(7, 0)), (_hj(6, 0), _hj(9, 0))]),
         4 * 3600)
check_eq("A5 contenu absorbé",
         union_duree_s([(_hj(5, 0), _hj(10, 0)), (_hj(6, 0), _hj(7, 0))]),
         5 * 3600)
check_eq("A6 triple recouvrement chaîné",
         union_duree_s([(_hj(5, 0), _hj(6, 0)), (_hj(5, 30), _hj(7, 0)),
                        (_hj(6, 45), _hj(8, 15))]),
         int((_hj(8, 15) - _hj(5, 0)).total_seconds()))
check_eq("A7 dégénérés ignorés",
         union_duree_s([(_hj(5, 0), _hj(5, 0)), (_hj(6, 0), _hj(5, 0)),
                        (None, None), (_hj(5, 0), _hj(6, 0))]), 3600)
check_eq("A8 idempotente",
         union_duree_s([(_hj(6, 0), _hj(9, 0)), (_hj(5, 0), _hj(7, 0))]),
         union_duree_s([(_hj(5, 0), _hj(7, 0)), (_hj(6, 0), _hj(9, 0))]))

# ===========================================================================
print("\n== B · F1 — construire_journee : TCJ ≤ TTJ structurel ==")
# ===========================================================================
# B1 : journée saine → identique à Σ durées (E2 §0undecies préservé)
segs_sains = [
    Segment(debut=_hj(5, 34), fin=_hj(9, 35), distance_km=100.0, rejete=False),
    Segment(debut=_hj(10, 5), fin=_hj(12, 0), distance_km=50.0, rejete=False),
]
jd = construire_journee(segs_sains, maintenant=_hj(12, 30))
sigma = int((_hj(9, 35) - _hj(5, 34)).total_seconds()
            + (_hj(12, 0) - _hj(10, 5)).total_seconds())
check_eq("B1 sain : TCJ = Σ durées (E2 intact)", jd.tcj_s, sigma)
check_eq("B1 sain : TTJ amplitude",
         jd.ttj_s, int((_hj(12, 0) - _hj(5, 34)).total_seconds()))
check("B1 sain : TCJ ≤ TTJ", jd.tcj_s <= jd.ttj_s)
check_eq("B1 sain : pauses = trou",
         jd.total_pause_s, int((_hj(10, 5) - _hj(9, 35)).total_seconds()))

# B2 : REPRODUCTION EXACTE de la capture 0926TBV (25/08 10:02) — la garde
segs_0926 = [
    Segment(debut=_hj(5, 34, 8), fin=_hj(9, 35, 48), distance_km=None,
            rejete=False),          # officielle MZoneX (validée)
    Segment(debut=_hj(8, 49, 0), fin=None, distance_km=None,
            rejete=False),          # rattrapée ouverte (chevauchante)
]
jd2 = construire_journee(segs_0926, maintenant=REC)
tcj_sans_garde = (int((_hj(9, 35, 48) - _hj(5, 34, 8)).total_seconds())
                  + int((REC - _hj(8, 49, 0)).total_seconds()))
check("B2 preuve : SANS garde on retombe sur son 5:13 (bug reproduit)",
      tcj_sans_garde // 60 == 313, f"Σ={tcj_sans_garde//60} min")
check_eq("B2 F1 : TCJ = union (= amplitude réelle 4:26)",
         jd2.tcj_s, jd2.ttj_s)
check_eq("B2 F1 : TCJ 5:13 → 4:26", jd2.tcj_s // 60, 266)
check("B2 F1 : TCJ ≤ TTJ", jd2.tcj_s <= jd2.ttj_s)
check_eq("B2 F1 : pauses = 0 (aucun trou réel)", jd2.total_pause_s, 0)

# B3 : recouvrement + vrai trou : la pause réelle survit
segs_mix = [
    Segment(debut=_hj(6, 0), fin=_hj(9, 0), distance_km=50.0, rejete=False),
    Segment(debut=_hj(8, 0), fin=_hj(10, 0), distance_km=30.0, rejete=False),
    Segment(debut=_hj(11, 0), fin=_hj(12, 0), distance_km=20.0, rejete=False),
]
jd3 = construire_journee(segs_mix, maintenant=_hj(12, 30))
sigma3 = sum(lg.travail_s for lg in jd3.lignes)
check_eq("B3 union (5h) < Σ (6h)", (jd3.tcj_s, sigma3),
         (5 * 3600, 6 * 3600))
check_eq("B3 TTJ 6h", jd3.ttj_s, 6 * 3600)
check_eq("B3 pause réelle 10:00→11:00 = 1h", jd3.total_pause_s, 3600)

# B4 : ligne rejetée ignorée ET garde
segs_rej = [
    Segment(debut=_hj(5, 34, 8), fin=_hj(9, 35, 48), distance_km=None,
            rejete=False),
    Segment(debut=_hj(8, 49, 0), fin=None, distance_km=None, rejete=True),
]
jd4 = construire_journee(segs_rej, maintenant=REC)
check_eq("B4 rejetée exclue : TCJ = ligne officielle seule",
         jd4.tcj_s, int((_hj(9, 35, 48) - _hj(5, 34, 8)).total_seconds()))

# ===========================================================================
print("\n== C · F1 — recalculer_temps : compteurs + audit chevauchement ==")
# ===========================================================================
v1 = _veh("9001TST")
s1 = _suivi(v1)
_trajet(s1, 1, _hj(5, 34, 8), _hj(9, 35, 48), src=VALI, val=V_OK, dist=None)
_trajet(s1, 2, _hj(8, 49, 0), None, src=PROV, val=EN_AT, dist=None)
engine.recalculer_temps(db, s1, REC)
db.commit()
check_eq("C1 stocké : TCJ = TTJ (union)", (s1.tcj_s, s1.ttj_s) ,
         (jd2.tcj_s, jd2.ttj_s))
check("C1 stocké : TCJ ≤ TTJ", s1.tcj_s <= s1.ttj_s)
check_eq("C1 DÉPART = 05:34:08", s1.heure_depart, _hj(5, 34, 8))
auds = db.scalars(select(AuditLog).where(
    AuditLog.action == "trajet.chevauchement_detecte")).all()
check_eq("C2 audit chevauchement écrit", len(auds), 1)
if auds:
    d0 = auds[0].details
    check("C2 audit : plaque/jour/quantité",
          d0.get("plaque") == "9001TST" and d0.get("jour") == J.isoformat()
          and d0.get("recouvrement_min") == 47,
          f"details={d0}")
# idempotence de l'audit (pas de spam au recalcul suivant)
engine.recalculer_temps(db, s1, REC + timedelta(minutes=5))
db.commit()
auds2 = db.scalars(select(AuditLog).where(
    AuditLog.action == "trajet.chevauchement_detecte")).all()
check_eq("C2 audit idempotent (même quantité → pas de doublon)",
         len(auds2), 1)

# TCC : session mesurée à l'union — après le recalcul à REC+5 min, la valeur
# union attendue = (REC+5min − 05:34:08) = 271 (plus de 5:13)
attendu_c3 = (REC + timedelta(minutes=5) - _hj(5, 34, 8)).seconds // 60
check_eq("C3 TCC = valeur union (≈ 4:26 + 5 min)", s1.tcc_s // 60,
         attendu_c3)

# ===========================================================================
print("\n== D · F2 — rattrapage : réactivation au lieu de duplication ==")
# ===========================================================================
v2 = _veh("9002TST")
s2 = _suivi(v2)
# ligne rejetée refermée à 08:49:30 (micro-arrêt), camion roule à 08:56
tB = _trajet(s2, 1, _hj(5, 47, 1), _hj(8, 49, 30), src=PROV, val=REJ,
             dist=61.2)
v2.last_event_at = _hj(8, 56, 0)
v2.last_vitesse = 55.0
db.commit()
engine.rattraper_ouvertures(maintenant=_hj(8, 56, 0))
db.commit()
db.refresh(tB)
trajets2 = db.scalars(select(Trajet).where(
    Trajet.suivi_id == s2.id).order_by(Trajet.numero)).all()
check_eq("D1 F2 : AUCUNE nouvelle ligne créée", len(trajets2), 1)
check("D1 F2 : ligne rejetée RÉACTIVÉE", tB.statut_validation == EN_AT)
check("D1 F2 : fin effacée (roulage continue)", tB.heure_fin is None)
check_eq("D1 F2 : VRAI début conservé (05:47:01)", tB.heure_debut,
         _hj(5, 47, 1))
aud_r = db.scalars(select(AuditLog).where(
    AuditLog.action == "trajet.reactivation")).all()
check_eq("D2 audit `trajet.reactivation` écrit", len(aud_r), 1)

# D3 : ligne rejetée fermée ANCIENNE (> 20 min) → création normale
v3 = _veh("9003TST")
s3 = _suivi(v3)
tB3 = _trajet(s3, 1, _hj(5, 47, 1), _hj(7, 0, 0), src=PROV, val=REJ, dist=12.0)
v3.last_event_at = _hj(8, 56, 0)
v3.last_vitesse = 55.0
db.commit()
engine.rattraper_ouvertures(maintenant=_hj(8, 56, 0))
db.commit()
trajets3 = db.scalars(select(Trajet).where(
    Trajet.suivi_id == s3.id).order_by(Trajet.numero)).all()
check_eq("D3 rejetée ancienne : nouvelle ligne ouverte créée (R2 historique)",
         len(trajets3), 2)
check("D3 l'ancienne reste rejetée/fermée",
      tB3.statut_validation == REJ and tB3.heure_fin is not None)

# D4 : ligne OUVERTE rejetée → réactivation (R1 historique conservé)
v4 = _veh("9004TST")
s4 = _suivi(v4)
tB4 = _trajet(s4, 1, _hj(5, 47, 1), None, src=PROV, val=REJ, dist=22.0)
v4.last_event_at = _hj(8, 56, 0)
v4.last_vitesse = 55.0
db.commit()
engine.rattraper_ouvertures(maintenant=_hj(8, 56, 0))
db.commit()
db.refresh(tB4)
db.expire_all()
trajets4 = db.scalars(select(Trajet).where(
    Trajet.suivi_id == s4.id).order_by(Trajet.numero)).all()
check_eq("D4 R1 : ouverte rejetée réactivée, aucune nouvelle",
         (len(trajets4), tB4.statut_validation), (1, EN_AT))

# ===========================================================================
print("\n== E · F3 — réparation unique du 25/08 ==")
# ===========================================================================
v5 = _veh("0926TBV")
s5 = _suivi(v5)
tB5 = _trajet(s5, 1, _hj(5, 47, 1), None, src=PROV, val=REJ, dist=98.4)
tC5 = _trajet(s5, 2, _hj(8, 49, 0), None, src=PROV, val=EN_AT, dist=None)
tA5 = _trajet(s5, 3, _hj(5, 34, 8), _hj(9, 35, 48), src=VALI, val=V_OK,
              dist=None)
engine.recalculer_temps(db, s5, REC)
db.commit()

stats = resoudre_chevauchements_jour(db, J, maintenant=REC)
db.commit()
check("E1 chevauchements arbitrés (0926TBV compris)",
      stats["chevauchements"] >= 1 and "0926TBV" in stats["plaques"],
      f"stats={stats}")
db.refresh(tC5); db.refresh(tA5)
check("E1 la PROVISOIRE chevauchante est rejetée",
      tC5.statut_validation == REJ)
check("E1 la VALIDÉE est conservée", tA5.statut_validation == V_OK)
check("E1 état « en cours » TRANSFÉRÉ au gardien (extension déclarée)",
      tA5.heure_fin is None)
check("E1 JAMAIS de suppression : la perdante existe toujours en base",
      db.get(Trajet, tC5.id) is not None)
db.refresh(s5)
check("E2 compteurs recalculés : TCJ ≤ TTJ", s5.tcj_s <= s5.ttj_s)
check_eq("E2 DÉPART 05:34 conservé", s5.heure_depart, _hj(5, 34, 8))
aud_d = db.scalars(select(AuditLog).where(
    AuditLog.action == "trajet.doublon_rejete")).all()
check_eq("E3 audit doublon_rejete écrit (2 horaires)",
         len(auds_c := [a for a in aud_d
                        if a.details.get("plaque") == "0926TBV"]) , 1)

# E4 : idempotence — un 2e passage ne change rien
avant = (tC5.statut_validation, tA5.heure_fin)
stats2 = resoudre_chevauchements_jour(db, J, maintenant=REC)
db.commit()
check("E4 idempotent : plus rien à recouvrer pour 0926TBV",
      stats2["rejetes"] == 0
      or "0926TBV" not in stats2["plaques"], f"stats2={stats2}")
db.refresh(tC5); db.refresh(tA5)
check_eq("E4 état stable", (tC5.statut_validation, tA5.heure_fin), avant)

# E5 : tie-break « à statut égal, début le plus tôt »
v6 = _veh("9006TST")
s6 = _suivi(v6)
tX1 = _trajet(s6, 1, _hj(8, 0, 0), _hj(10, 0, 0), src=PROV, val=EN_AT)
tX2 = _trajet(s6, 2, _hj(7, 0, 0), _hj(9, 0, 0), src=PROV, val=EN_AT)
db.commit()
st6 = resoudre_chevauchements_jour(db, J, maintenant=_hj(10, 30, 0))
db.commit()
db.refresh(tX1); db.refresh(tX2)
check("E5 à statut égal : la plus tardive est rejetée",
      tX1.statut_validation == REJ and tX2.statut_validation == EN_AT)
check_eq("E5 un rejet pour 9006TST", st6["plaques"].count("9006TST"), 1)

# E6 : journée ARCHIVÉE jamais touchée (§A.2)
v7 = _veh("9007TST")
s7 = _suivi(v7)
tY1 = _trajet(s7, 1, _hj(8, 0, 0), _hj(10, 0, 0), src=PROV, val=EN_AT)
tY2 = _trajet(s7, 2, _hj(7, 0, 0), _hj(9, 0, 0), src=PROV, val=EN_AT)
db.add(HistoriqueJournalier(date_jour=J, annee=2026, mois=8,
                            vehicule_id=v7.id, donnees={"trajets": []}))
db.commit()
resoudre_chevauchements_jour(db, J, maintenant=_hj(10, 30, 0))
db.commit()
db.refresh(tY1); db.refresh(tY2)
check("E6 journée archivée : AUCUNE mutation (§A.2)",
      tY1.statut_validation == EN_AT and tY2.statut_validation == EN_AT)

# E7 : exécution one-time + marqueur + idempotence globale
r1 = executer_reparation_v132()
r2 = executer_reparation_v132()
check("E7 marqueur one-time posé puis respecté",
      r1.get("deja_fait") is False and r2.get("deja_fait") is True)
mk = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == MARQUEUR_V132))
check_eq("E7 un seul marqueur global", mk, 1)

# ===========================================================================
print("\n== F · F4 — rapport de diagnostic (texte, lecture seule) ==")
# ===========================================================================
avant_count = db.scalar(select(func.count(Trajet.id)))
# RÉALIGNEMENT v1.37 (déclaré en CONFORMITÉ) : le scénario fige la journée
# RÉELLE 0926TBV du 25/08/2026, mais le moteur horodate ses audits à
# l'horloge réelle du jour d'exécution — la fenêtre du rapport F4
# ([J, J+2 jours[) ne les couvrait donc que si la suite tournait les
# 25-26/08 (constaté KO le 27/08, batterie v1.37). On ré-horodate les
# lignes d'audit CRÉÉES PAR CE TEST (données de test uniquement, jamais
# de production) à la journée J — même sens de vérification, désormais
# indépendant du calendrier.
for _a in db.scalars(select(AuditLog).where(
        AuditLog.action.in_(("trajet.doublon_rejete",
                             "trajet.chevauchement_detecte")))).all():
    if "0926TBV" in str(_a.details or {}):
        _a.date_heure = datetime(2026, 8, 25, 10, 1, 0)
db.commit()
txt = construire_rapport_diagnostic(db, "0926TBV", J, maintenant=REC)
check("F1 en-tête horodaté", "RAPPORT DE DIAGNOSTIC" in txt
      and "Généré le" in txt)
check("F1 plaque + journée", "0926TBV" in txt and "25/08/2026" in txt)
check("F2 lignes TOUTES présentes (rejetées comprises)",
      "REJETÉE (masquée, non comptée)" in txt and "(en cours)" in txt)
check("F2 horaires des lignes", "05:34:08" in txt and "08:49:00" in txt)
check("F3 compteurs", "TCC" in txt and "TCJ" in txt and "TTJ" in txt)
check("F4 actions automatiques (audit du jour)",
      "trajet.doublon_rejete" in txt or "chevauchement" in txt)
check("F5 seuils", "DUREE_MIN_PAUSE_VALIDE" in txt)
check("F6 lecture seule : aucune ligne créée",
      db.scalar(select(func.count(Trajet.id))) == avant_count)
txt2 = construire_rapport_diagnostic(db, "XXXXXXX", J, maintenant=REC)
check("F7 plaque inconnue → message clair", "INCONNU" in txt2)

# ===========================================================================
print("\n== G · régression affichage : la ligne reproduite devient saine ==")
# ===========================================================================
# après F3 sur 0926TBV : la grille montre UNE ligne en cours 05:34 → « en
# cours » (vérité du moment), compteurs 4:26 → plus de « 5:13 »
row = s_suivi(s5, engine.get_seuils(db))
check_eq("G1 grille : une seule ligne affichée (fusion E1 intacte)",
         row["nb_trajets"], 1, f"trajets={row['trajets']}")
check("G1 ligne 05:34:08 → en cours",
      row["trajets"][0]["heure_debut"].startswith("2026-08-25T05:34:08")
      and row["trajets"][0]["heure_fin"] is None)
lg1 = jd2  # rappel : union 266 min
check("G2 compteurs sains : TCJ = TTJ ≈ 4:26 (plus de 5:13)",
      s5.tcj_s == s5.ttj_s and s5.tcj_s // 60 >= 260)

# ===========================================================================
print(f"\n{'=' * 64}\nRÉSULTAT : {R['ok']} OK / {R['ko']} KO\n{'=' * 64}")
db.close()
cible = db_url.replace("sqlite:///", "")
if cible.startswith("/tmp/"):
    try:
        os.remove(cible)
    except OSError:
        pass
sys.exit(1 if R["ko"] else 0)
