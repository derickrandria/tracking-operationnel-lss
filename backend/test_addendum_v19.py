"""Tests v1.15 — ADDENDUM v1.9 : TCC coupé à 30 min, TCJ/TTJ (définitions
exactes), structure colonnes (TCC/TCJ/TTJ d'abord + « Lieu Arrêt »).

Interprétations appliquées (validées par la règle écrite + pseudocode §2.4 +
critère CA-3 du document — l'exemple chiffré « TCC = 08:10 » du §1.1 est
incohérent avec ces trois sources) :
  TCC = conduite de la SESSION courante, remise à zéro après pause ≥ 30 min ;
  TCJ = depuis le 1er trajet − pauses ≥ 20 min ;  TTJ = TCJ + tous les arrêts.

RÉALIGNÉ v1.34 (§0quaterdecies H1/H2, arbitrages LSS du 25/08/2026) : TCC =
CHRONO de session — les arrêts < 30 min sont INCLUS (texte §1.1 « pause courte
incluse ») et le chrono continue pendant un arrêt court EN COURS. Attendus CA-2
/ CA-2 bis / CA-3 recalculés dans la foulée : 05:00→11:05 = 6:05 (pause 20/29
min incluse), session 2 09:30→11:05 = 1:35, arrêt en cours 29 min → 4:29.
TCJ/TTJ inchangés.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v19.db" python3 test_addendum_v19.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.config import now_local
from app.database import SessionLocal
from app import engine
from app.models import (StatutSourceTrajet, StatutValidationTrajet, Trajet,
                        Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.serializers import s_suivi
from app.exporters import _entetes_suivi, _valeurs_suivi, _blocs_groupes

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
jour = now_local().date()


def h(hh, mm, ss=0):
    return datetime(jour.year, jour.month, jour.day, hh, mm, ss)


veh = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF").order_by(
        Vehicule.plaque)).first()
print(f"Véhicule test : {veh.plaque} · jour={jour}")

s = engine.ensure_suivi(db, veh, jour)
db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
db.commit()


def ajoute(debut, fin, km):
    db.add(Trajet(suivi_id=s.id, numero=0, heure_debut=debut, heure_fin=fin,
                  statut_source=StatutSourceTrajet.VALIDE,
                  source_plateforme="MZONEX", distance_km=km,
                  statut_validation=StatutValidationTrajet.VALIDE))
    db.commit()


def reset():
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
    db.commit()


def temps(maintenant):
    db.expire(s, ["trajets"])
    engine.recalculer_temps(db, s, maintenant)
    db.commit()
    return s.tcc_s, s.tcj_s, s.ttj_s


try:
    # ================================================================
    print("\n[CA-1/CA-2] Pause 29 min : NE COUPE PAS le TCC ; TCC > 4h30 si longue session")
    reset()
    # §1.1 — 05:00→09:00 (240 min) + pause 20 min + 09:20→11:00 (100 min)
    ajoute(h(5, 0), h(9, 0), 160.0)
    ajoute(h(9, 20), h(11, 0), 70.0)
    tcc, tcj, ttj = temps(h(11, 5))
    check("CA-2 : pause 20 min ≤ 30 → TCC continu = chrono 05:00→11:05 = 6:05 "
          "(§0quaterdecies H1/H2 : pause 20 min ET arrêt en cours 5 min INCLUS)",
          tcc == 6 * 3600 + 5 * 60, f"tcc={tcc}s attendu 21900s")
    check("CA-1 : TCC dépasse bien 4h30 dans ce cas (alerte déclenchable)",
          tcc > 16200)

    # ================================================================
    print("\n[CA-2 bis] Pause de 29 min exactement : TCC NON coupé")
    reset()
    ajoute(h(5, 0), h(9, 0), 160.0)      # 4:00
    ajoute(h(9, 29), h(11, 0), 70.0)     # pause 29 min — valide (≥20) mais < 30
    tcc, tcj, ttj = temps(h(11, 5))
    check("CA-2 : pause 29 min → TCC = chrono 05:00→11:05 = 6:05 "
          "(§0quaterdecies H1/H2 : pause incluse, arrêt en cours inclus)",
          tcc == 6 * 3600 + 5 * 60, f"tcc={tcc}s attendu 21900s")

    # ================================================================
    print("\n[CA-3] Pause de 30 min exactement : TCC COUPÉ (nouvelle session)")
    reset()
    ajoute(h(5, 0), h(9, 0), 160.0)      # 4:00
    ajoute(h(9, 30), h(11, 0), 70.0)     # pause 30 min ≥ 30 → coupe
    tcc, tcj, ttj = temps(h(11, 5))
    check("CA-3 : pause 30 min → TCC = chrono session 2 : 09:30→11:05 = 1:35 "
          "(§0quaterdecies H2 : arrêt en cours 5 min inclus)",
          tcc == 95 * 60, f"tcc={tcc}s attendu 5700s")
    # camion ACTUELLEMENT arrêté depuis ≥ 30 min → TCC affiché = 0
    reset()
    ajoute(h(5, 0), h(9, 0), 160.0)
    tcc, _, _ = temps(h(9, 30))          # arrêt en cours depuis 30 min pile
    check("CA-3 : pause en cours atteint 30 min → TCC remis à zéro",
          tcc == 0, f"tcc={tcc}s")
    tcc, _, _ = temps(h(9, 29))          # 29 min d'arrêt : pas encore coupé
    check("CA-3 : à 29 min d'arrêt → TCC = chrono 05:00→09:29 = 4:29 "
          "(§0quaterdecies H2 : le chrono continue pendant l'arrêt court)",
          tcc == 4 * 3600 + 29 * 60, f"tcc={tcc}s")

    # ================================================================
    print("\n[CA-4] TCJ = depuis le 1er trajet − pauses ≥ 20 min (§1.2)")
    reset()
    # exemple du document §1.2 : 05:00→09:00, 09:30→11:00, 11:20→14:00
    ajoute(h(5, 0), h(9, 0), 160.0)
    ajoute(h(9, 30), h(11, 0), 60.0)
    ajoute(h(11, 20), h(14, 0), 100.0)
    tcc, tcj, ttj = temps(h(14, 5))
    # fenêtre 05:00→14:05 … mesurée à la fin du dernier trajet = 9:00 + 5 min
    # de « queue » non comptée : le moteur mesure à fin_ref = 14:00
    # → fenêtre 9:00 − pauses (30 + 20 min) = 8:10 (valeur exacte du document)
    check("CA-4 : TCJ = 9:00 de fenêtre − 0:50 de pauses = 8:10",
          tcj == 8 * 3600 + 10 * 60, f"tcj={tcj}s attendu 29400s")

    # ================================================================
    print("\n[CA-5] TTJ = TCJ + tous les arrêts (§1.3) — fenêtre complète")
    check("CA-5 : TTJ = TCJ + pauses ≥ 20 min (8:10 + 0:50 = 9:00)",
          ttj == 9 * 3600, f"ttj={ttj}s attendu 32400s")
    check("CA-5 : TTJ ≥ TCJ (toujours)", ttj >= tcj)

    # ================================================================
    print("\n[CA-5 bis] Manœuvre exclue des trois compteurs (règle absolue §2)")
    reset()
    ajoute(h(5, 0), h(9, 0), 160.0)
    ajoute(h(9, 2), h(9, 6), 0.15)       # manœuvre < 0,3 km, REJETÉE
    from app.models import StatutValidationTrajet as SVT
    t_man = db.scalars(select(Trajet).where(
        Trajet.suivi_id == s.id).order_by(Trajet.heure_debut)).all()[-1]
    t_man.statut_validation = SVT.REJETE
    db.commit()
    ajoute(h(9, 30), h(11, 0), 60.0)
    _, tcj, ttj = temps(h(11, 5))
    check("manœuvre 4 min IGNORÉE (§7, supersède la soudure) : TCJ = conduite "
          "pure 4:00 + 1:30 = 5:30 — lignes 05:00→09:00 et 09:30→11:00",
          tcj == 5 * 3600 + 30 * 60, f"tcj={tcj}s")
    # v3 (AM-1/T1 + AM-6, arbitrages 22/08/2026) : TTJ = amplitude BRUTE 6:00
    # (plus aucune exclusion) ; arrêts comptables = TOUS les arrêts, manœuvre
    # de 4 min comprise (un mouvement < 0,3 km est un arrêt)
    check("TTJ = amplitude BRUTE 6:00 (v3 T1) ; arrêts comptables = arrêt "
          "26 min + manœuvre 4 min = 30 min (AM-1/AM-6)",
          ttj == 6 * 3600 and s.total_pause_s == 30 * 60
          and ttj == tcj + s.total_pause_s,
          f"ttj={ttj}s pause={s.total_pause_s}s")

    # ================================================================
    print("\n[CA-6/CA-7] Structure colonnes : TCC/TCJ/TTJ d'abord, « Lieu Arrêt »")
    for detail in (False, True):
        h_cols = _entetes_suivi(detail)
        # v1.44 (§0vicies decies N2 — réalignement déclaré) : indices relatifs à
        # « Heure départ » (Pos. 20h/22h ont décalé le bloc de +2 colonnes).
        i_hd = h_cols.index("Heure départ")
        check(f"CA-6 ({'détail' if detail else 'compact'}) : TCC,TCJ,TTJ juste "
              f"avant « Heure départ »",
              h_cols[i_hd - 3:i_hd] == ["TCC", "TCJ", "TTJ"],
              f"{h_cols[i_hd - 4:i_hd + 1]}")
        check(f"CA-7 ({'détail' if detail else 'compact'}) : « Lieu Arrêt » "
              f"après « Arrêt final »",
              h_cols[-3] == "Lieu Arrêt" and h_cols[-4] == "Arrêt final",
              f"{h_cols[-4:]}")
        spans = sum(sp for _, sp, _ in _blocs_groupes(detail))
        check(f"({('détail' if detail else 'compact')}) : blocs d'en-tête "
              f"cohérents ({spans} colonnes)", spans == len(h_cols))
    veh.last_adresse = "Dépôt Ambodivoanjo, Antananarivo"
    db.commit()
    db.expire(s, ["trajets"])
    sv = s_suivi(engine.ensure_suivi(db, veh, jour))
    check("CA-7 : s_suivi expose lieu_arret",
          sv.get("lieu_arret") == "Dépôt Ambodivoanjo, Antananarivo",
          f"{sv.get('lieu_arret')}")
    vals = _valeurs_suivi(sv, True, excel=False)
    check("CA-7 : valeur « Lieu Arrêt » présente dans l'export",
          "Dépôt Ambodivoanjo, Antananarivo" in vals,
          f"{vals[-4:]}")
    # v1.44 (réalignement déclaré) : colonnes 18-20 → 20-22 (Pos. 20h/22h).
    i_tcc = _entetes_suivi(True).index("TCC")
    check("CA-6 : valeurs TCC/TCJ/TTJ juste avant « Heure départ » dans l'export",
          ":" in str(vals[i_tcc]), f"{vals[i_tcc:i_tcc + 4]}")

    # ================================================================
    print("\n[CA-8] Trajet < 0,3 km : JAMAIS affiché (règle absolue §2)")
    from app.serializers import journee_suivi
    from app.chaines import ETAT_OFFICIEL
    jn = journee_suivi(s)
    debuts = [(lg.debut.hour, lg.debut.minute) for lg in jn.lignes]
    check("la manœuvre 09:02 ne forme aucune ligne propre",
          (9, 2) not in debuts, f"{debuts}")
    check("les lignes visibles restent 05:00 → 09:0x et 09:30 → 11:00",
          len(jn.lignes) == 2, f"{len(jn.lignes)} lignes : {debuts}")

    # ================================================================
    print("\n[CA-9] Durée temps réel du trajet en cours (confirmé v1.8)")
    ajoute(h(14, 30), None, None)
    t_en_cours = db.scalars(select(Trajet).where(
        Trajet.suivi_id == s.id, Trajet.heure_fin.is_(None))).first()
    t_en_cours.statut_source = StatutSourceTrajet.PROVISOIRE
    t_en_cours.statut_validation = SVT.EN_ATTENTE
    db.commit()
    veh.last_event_at = now_local() - timedelta(seconds=30)
    veh.last_vitesse = 40.0
    db.commit()
    db.expire(s, ["trajets"])
    sv = s_suivi(engine.ensure_suivi(db, veh, jour))
    t_dernier = sv["trajets"][-1]
    check("CA-9 : trajet en cours → Fin vide côté API (durée affichée à l'écran)",
          t_dernier["heure_fin"] is None
          and t_dernier["statut_source"] == "PROVISOIRE",
          f"{(t_dernier['heure_debut'], t_dernier['heure_fin'], t_dernier['statut_source'])}")
    vals = _valeurs_suivi(sv, True, excel=False)
    check("CA-9 : l'export porte la durée H:MM (jamais le texte « en cours »)",
          "en cours" not in [str(v) for v in vals])

finally:
    db.close()
    fichier = db_url.split("///")[-1]
    if fichier and os.path.exists(fichier):
        os.remove(fichier)
    print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
    sys.exit(1 if R["ko"] else 0)
