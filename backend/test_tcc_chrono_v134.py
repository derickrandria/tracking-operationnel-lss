# -*- coding: utf-8 -*-
"""Tests v1.34 — §0quaterdecies (arbitrages LSS DIRECTS du 25/08/2026) :

H1 : TCC = CHRONO de session — temps ÉCOULÉ depuis le début de la session
     (1er départ valide ou reprise après pause ≥ 30 min), arrêts < 30 min
     INCLUS (réalignement de l'implémentation sur le texte §1.1 « pause
     courte incluse » ; E2/F1 amendés sur ce seul point) ;
H2 : arrêt court EN COURS (< 30 min) — le chrono TCC continue de s'écouler ;
     à 30 min d'arrêt → pause coupante → TCC = 0, nouvelle session ;
Inviolables (vérifiés ici aussi) : TCJ = Σ conduite réelle, TOUS les arrêts
     déduits toute durée (AM-1/F1) ; TTJ = amplitude (T1) ; manœuvre < 0,3 km
     totalement effacée, ni ligne ni durée (§A.5) ; pause ≥ 30 min coupe
     (« supérieure ou égale », borne exacte incluse) ; minuit R1 intact ;
     recouvrement : le chrono ne double-compte pas par construction.

Fil rouge : EXEMPLE EXACT DE L'EXPLOITANT (0576TCD, 25/08/2026) — départ
10:20, arrêt 11:13, manœuvre rejetée 11:15:15→11:15:49 (0,004 km), reprise
11:36, jusqu'à 14:30 → attendus : TCC 4:10 · TCJ 3:47:00 · TTJ 4:10.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v134.db" SIM_ENABLE=0 python3 test_tcc_chrono_v134.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import date, datetime

from sqlalchemy import delete, func, select

from app.database import SessionLocal
from app import engine
from app.models import (AuditLog, EvenementGPS, HistoriqueJournalier,
                        Alerte, Infraction, Mission,
                        StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema

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
    try:
        os.remove(db_url.replace("sqlite:///", "/", 1))
    except OSError:
        pass

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
db = SessionLocal()

for modele in (Trajet, EvenementGPS, HistoriqueJournalier, SuiviJournalier,
               Alerte, AuditLog, Infraction, Mission):
    db.execute(delete(modele))
db.commit()

J = date(2026, 8, 25)


def dt(hh, mm, ss=0):
    return datetime(J.year, J.month, J.day, hh, mm, ss)


def veh(plaque):
    return db.scalar(select(Vehicule).where(Vehicule.plaque == plaque))


def suivi_de(v):
    s = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v.id, SuiviJournalier.date_jour == J))
    return s if s is not None else engine.ensure_suivi(db, v, J)


def ligne(v, d1, d2, dist=10.0, validation=StatutValidationTrajet.VALIDE):
    s = suivi_de(v)
    n = db.scalar(select(func.count(Trajet.id)).where(
        Trajet.suivi_id == s.id)) or 0
    db.add(Trajet(suivi_id=s.id, numero=n + 1, heure_debut=d1, heure_fin=d2,
                  distance_km=dist, statut_source=StatutSourceTrajet.VALIDE,
                  statut_validation=validation, source_plateforme="MZONEX"))
    db.flush()


def recalc(v, maintenant):
    s = suivi_de(v)
    db.commit()
    engine.recalculer_temps(db, s, maintenant)
    db.commit()
    db.refresh(s)
    return s


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


# ============================================ fil rouge : exemple exploitant
print("\n=== H1 — EXEMPLE EXACT DE L'EXPLOITANT (0576TCD, 25/08) ===")
v = veh("0576TCD")
ligne(v, dt(10, 20, 42), dt(11, 13, 15), 20.859)                    # T1 réel
ligne(v, dt(11, 15, 15), dt(11, 15, 49), 0.004,
      StatutValidationTrajet.REJETE)                                # manœuvre
ligne(v, dt(11, 36, 14), None, 40.0)                                # T2 en cours
s = recalc(v, dt(14, 30, 0))
check("DÉPART = 10:20:42 (la manœuvre ne fixe jamais le départ, AM-6)",
      s.heure_depart == dt(10, 20, 42), f"got {s.heure_depart}")
check("TCC = 4:09:18 (chrono 10:20:42 → 14:30 : arrêt 23 min INCLUS) "
      "= « 4:10 » de l'exploitant", s.tcc_s == 4 * 3600 + 9 * 60 + 18,
      f"got {hms(s.tcc_s)}")
check("TCJ = 3:46:19 (conduite pure ; manœuvre effacée, arrêt plein "
      "11:13:15→11:36:14 déduit) = « 3:47:00 »", s.tcj_s == 3 * 3600 + 46 * 60 + 19,
      f"got {hms(s.tcj_s)}")
check("TTJ = 4:09:18 (amplitude brute) = « 4:10 »",
      s.ttj_s == 4 * 3600 + 9 * 60 + 18, f"got {hms(s.ttj_s)}")
check("hiérarchie saine : TCJ ≤ TCC ≤ TTJ", s.tcj_s <= s.tcc_s <= s.ttj_s)

# ================================================= H1 : ce qui coupe / inclus
print("\n=== H1 — arrêts < 30 min INCLUS ; ≥ 30 min coupe (borne exacte) ===")
v1 = veh("0916TBV")
ligne(v1, dt(8, 0), dt(9, 0), 15.0)          # arrêt 29 min (inclus)
ligne(v1, dt(9, 29), None, 15.0)             # EN COURS
s1 = recalc(v1, dt(11, 0))
check("arrêt 29 min INCLUS : TCC = chrono 08:00 → 11:00 = 3:00:00 (pas 2:31)",
      s1.tcc_s == 3 * 3600, f"got {hms(s1.tcc_s)}")
check("TCJ conduit pur = 1:00 + 1:31 = 2:31:00 (arrêt 29 min déduit, AM-1/F1)",
      s1.tcj_s == 2 * 3600 + 31 * 60, f"got {hms(s1.tcj_s)}")

v2 = veh("3076TBS")
ligne(v2, dt(8, 0), dt(9, 0), 15.0)          # arrêt 7 min (inclus)
ligne(v2, dt(9, 7), dt(9, 30), 8.0)          # arrêt pile 30 min (coupe)
ligne(v2, dt(10, 0), None, 20.0)             # session 2, EN COURS
s2 = recalc(v2, dt(12, 30))
check("borne EXACTE 30 min : pause coupante (« supérieure ou égale ») → "
      "TCC = session 2 uniquement 10:00 → 12:30 = 2:30:00",
      s2.tcc_s == 2 * 3600 + 1800, f"got {hms(s2.tcc_s)}")
check("TCJ journée = 1:00 + 0:23 + 2:30 = 3:53:00 (toutes pauses déduites)",
      s2.tcj_s == 3 * 3600 + 53 * 60, f"got {hms(s2.tcj_s)}")
check("TTJ amplitude = 08:00 → 12:30 = 4:30:00",
      s2.ttj_s == 4 * 3600 + 1800, f"got {hms(s2.ttj_s)}")

# ================================ H2 : arrêt court EN COURS, chrono vivant
print("\n=== H2 — arrêt court EN COURS : le chrono continue ; à 30 min → 0 ===")
v3 = veh("4876TBU")
ligne(v3, dt(14, 0), dt(15, 0), 12.0)        # ligne CLOSE, plus rien ensuite
s3a = recalc(v3, dt(15, 10))                 # arrêt en cours : 10 min
check("arrêt en cours 10 min : TCC continue = 14:00 → 15:10 = 1:10:00",
      s3a.tcc_s == 1 * 3600 + 600, f"got {hms(s3a.tcc_s)}")
s3b = recalc(v3, dt(15, 29, 59))             # arrêt en cours : 29:59
check("arrêt en cours 29:59 : chrono toujours vivant = 1:29:59",
      s3b.tcc_s == 1 * 3600 + 29 * 60 + 59, f"got {hms(s3b.tcc_s)}")
s3c = recalc(v3, dt(15, 30, 0))              # pause coupante atteinte
check("arrêt atteint 30:00 → pause coupante : TCC = 0 (règle existante)",
      s3c.tcc_s == 0, f"got {hms(s3c.tcc_s)}")
check("TCJ reste la conduite pure 1:00:00 quelle que soit l'heure de mesure",
      s3a.tcj_s == s3b.tcj_s == s3c.tcj_s == 3600)

# ==================================================== manœuvres (§A.5/AM-6)
print("\n=== Manœuvres effacées — jamais de ligne, jamais de durée ===")
v4 = veh("0576TCD")          # on réutilise le fil rouge, tronçon à part
v5 = veh("0926TBV")
ligne(v5, dt(5, 34, 8), dt(9, 35, 48), 105.617)   # trajet réel du 25/08
ligne(v5, dt(9, 36, 28), None, 8.2)               # T2 EN COURS → arrêt 40 s
s5 = recalc(v5, dt(11, 35))
check("arrêt de 40 secondes INCLUS : TCC = 05:34:08 → 11:35 = 6:00:52",
      s5.tcc_s == (dt(11, 35) - dt(5, 34, 8)).total_seconds(),
      f"got {hms(s5.tcc_s)}")
check("…mais TCJ conduit pur les déduit (union F1 : Σ trajectoires)",
      s5.tcj_s == int((dt(9, 35, 48) - dt(5, 34, 8)).total_seconds()
                      + (dt(11, 35) - dt(9, 36, 28)).total_seconds()),
      f"got {hms(s5.tcj_s)}")

# ============================ recouvrement : le chrono est sain par nature
print("\n=== H1 + F1 — recouvrement : pas de double comptage, audit conservé ===")
v6 = veh("5506TBS")
ligne(v6, dt(8, 0), dt(10, 0), 40.0)
ligne(v6, dt(9, 0), None, 30.0)              # ligne recouvrante, en cours
db.execute(delete(AuditLog))
db.commit()
s6 = recalc(v6, dt(11, 0))
check("lignes recouvrantes : TCC = chrono 08:00 → 11:00 = 3:00:00 "
      "(span, jamais de double comptage)", s6.tcc_s == 3 * 3600,
      f"got {hms(s6.tcc_s)}")
check("TCJ = union 3:00:00 (F1 intacte)", s6.tcj_s == 3 * 3600,
      f"got {hms(s6.tcj_s)}")
nb_audit = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "trajet.chevauchement_detecte")) or 0
check("garde F1 : audit trajet.chevauchement_detecte toujours émis",
      nb_audit >= 1)

# =========================== plusieurs sessions dans la journée (chaînage)
print("\n=== H1 — enchaînement de sessions ===")
v7 = veh("0826TBS")
ligne(v7, dt(6, 0), dt(7, 0), 30.0)          # session 1
ligne(v7, dt(7, 20), dt(8, 0), 25.0)         # arrêt 20 min (inclus s1)
ligne(v7, dt(9, 0), dt(10, 0), 30.0)         # pause 1 h (coupe)
ligne(v7, dt(10, 20), dt(12, 0), 28.0)       # arrêt 20 min de s2 (INCLUS)
s7 = recalc(v7, dt(12, 0))
check("session 2 = 09:00 → 12:00 (arrêt 20 min de la session INCLUS) = 3:00:00",
      s7.tcc_s == 3 * 3600, f"got {hms(s7.tcc_s)}")
check("TCJ = 1:00 + 0:40 + 1:00 + 1:40 = 4:20:00 (F1)",
      s7.tcj_s == 4 * 3600 + 1200, f"got {hms(s7.tcj_s)}")
check("TTJ = 06:00 → 12:00 = 6:00:00", s7.ttj_s == 6 * 3600)

# ================================ seuil légal franchi plus tôt (conséquence)
print("\n=== Conséquence assumée : le chrono atteint plus tôt le seuil 4:30 ===")
check("0916TBV (arrêt 29 min inclus) : TCC 3:00 > TCJ 2:00 → le drapeau "
      "peut précéder d'un arrêt court, c'est voulu (§0quaterdecies)",
      s1.tcc_s > s1.tcj_s)

print(f"\n{'=' * 64}\nRÉSULTAT : {R['ok']} OK / {R['ko']} KO\n{'=' * 64}")
db.close()
try:
    if db_url.startswith("sqlite:///"):
        os.remove(db_url.replace("sqlite:///", "/", 1))
        print("Base de test supprimée.")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
