# -*- coding: utf-8 -*-
"""Tests v1.31 — §0undecies (arbitrages LSS du 24/08/2026) :

E1 : fusion d'AFFICHAGE des ruptures < 20 min (amendement partiel d'AM-2) —
     « les colonnes sans être bondées » : exemple réel 0936TBV du 24/08
     (9:54 → 13:06 | 13:13 → 13:55 | 14:36 → 16:00) affiché en DEUX lignes
     [9:54→13:55 pause 0:41] [14:36→16:00] ;
E2 : compteurs TCC/TCJ/TTJ INTACTS (tous les arrêts déduits, AM-1) ;
E3 : seuil d'affichage des pauses inchangé (30 min) — ABROGÉE le 25/08/2026 ;
E4 : positions nommées par les géozones des portails (« nom affiché sur carte
     à proximité ») + correctif d'unité du buffer MZoneX (degrés → mètres) ;
E5 : colonne J-1 toujours remplie + rattrapage unique au premier démarrage.

RÉALIGNÉ v1.33 (§0tricies decies G1/G2, arbitrage LSS direct du 25/08/2026) :
E3 est abrogée et le seuil de fusion d'affichage passe de 20 à 30 min. Les
sections « cas réel 0936TBV » (ruptures 7 et 41 min) et « archives » restent
VALIDES : ces ruptures sont de part et d'autre des deux seuils (20 et 30) ;
seule la section « E1/E3 — seuils » change d'attendu (rupture 21 min :
désormais FUSIONNÉE). La matrice complète des seuils v1.33 (dont les trois
cas réels du 25/08) est couverte par test_fusion30_v133.py.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v131.db" SIM_ENABLE=0 python3 test_affichage_position_v131.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import copy
import sys
from datetime import date, datetime

from sqlalchemy import delete, func, select

from app.database import SessionLocal
from app import engine, geozones
from app.models import (AuditLog, EvenementGPS, HistoriqueJournalier,
                        Infraction, Mission, Alerte,
                        StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet,
                        SourceEvenement, Vehicule)
from app.serializers import (fusionner_snapshot, fusionner_trajets_affichage,
                             s_historique, s_suivi)
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

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
db = SessionLocal()

# ardoise vierge (déterminisme) : l'historique de démonstration du seed est purgé
for modele in (Trajet, EvenementGPS, HistoriqueJournalier, SuiviJournalier,
               Alerte, AuditLog, Infraction, Mission):
    db.execute(delete(modele))
db.commit()

JPASS = date(2026, 8, 21)          # vendredi passé → lignes OFFICIELLES
JVEILLE = date(2026, 8, 22)
JJOUR = date(2026, 8, 23)
P0936 = "0936TBV"


def dt(jour, hh, mm, ss=0):
    return datetime(jour.year, jour.month, jour.day, hh, mm, ss)


def veh(plaque):
    return db.scalar(select(Vehicule).where(Vehicule.plaque == plaque))


def suivi_de(v, jour):
    s = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v.id,
        SuiviJournalier.date_jour == jour))
    if s is None:
        s = engine.ensure_suivi(db, v, jour)
    return s


def ligne(v, jour, hh1, mm1, hh2, mm2, dist,
          validation=StatutValidationTrajet.VALIDE,
          source=StatutSourceTrajet.VALIDE, plateforme="CAMTRACKPRO"):
    s = suivi_de(v, jour)
    n = db.scalar(select(func.count(Trajet.id)).where(
        Trajet.suivi_id == s.id)) or 0
    t = Trajet(suivi_id=s.id, numero=n + 1,
               heure_debut=dt(jour, hh1, mm1), heure_fin=dt(jour, hh2, mm2),
               distance_km=dist, statut_source=source,
               statut_validation=validation, source_plateforme=plateforme)
    db.add(t)
    db.flush()
    return t


def zones_de_test(zones):
    with geozones._lock:
        geozones._zones = list(zones)


# Centre d'essai : Antananarivo (-18.8792, 47.5079)
LAT, LNG = -18.8792, 47.5079


def evenement(v, ts, lat=LAT, lng=LNG, vitesse=0.0):
    ev = EvenementGPS(vehicule_id=v.id, horodatage=ts, latitude=lat,
                      longitude=lng, vitesse=vitesse, source="MZONEX")
    db.add(ev)
    db.flush()
    return ev


print("\n=== E1 — fusion d'affichage : le cas RÉEL 0936TBV (24/08) ===")
v1 = veh(P0936)
ligne(v1, JPASS, 9, 54, 13, 6, 31.0)     # arrêt de 7 min (13:06→13:13)
ligne(v1, JPASS, 13, 13, 13, 55, 6.0)    # arrêt de 41 min (13:55→14:36)
ligne(v1, JPASS, 14, 36, 16, 0, 20.0)
db.commit()
s = suivi_de(v1, JPASS)
engine.recalculer_temps(db, s, dt(JPASS, 16, 40))
db.commit()
out = s_suivi(s, engine.get_seuils(db))
tr = out["trajets"]
check("2 lignes affichées (pas 3)", len(tr) == 2, f"got {len(tr)}")
check("ligne 1 : début 09:54", tr[0]["heure_debut"][11:16] == "09:54",
      f"got {tr[0]['heure_debut']}")
check("ligne 1 : FIN T1 = 13:55 (micro-arrêt absorbé)",
      tr[0]["heure_fin"][11:16] == "13:55", f"got {tr[0]['heure_fin']}")
check("ligne 1 : PAUSE 1 = 0:41 (2460 s)",
      tr[0]["pause_apres_s"] == 2460, f"got {tr[0]['pause_apres_s']}")
check("ligne 2 : DÉBUT T2 = 14:36", tr[1]["heure_debut"][11:16] == "14:36",
      f"got {tr[1]['heure_debut']}")
check("ligne 2 : FIN T2 = 16:00", tr[1]["heure_fin"][11:16] == "16:00")
check("distances SOMMÉES sur la ligne fusionnée (31 + 6)",
      abs((tr[0]["distance_km"] or 0) - 37.0) < 1e-9,
      f"got {tr[0]['distance_km']}")
check("segments cumulés (2 composantes)", tr[0]["segments"] == 2,
      f"got {tr[0]['segments']}")
check("numérotation re-séquencée 1, 2",
      [t["numero"] for t in tr] == [1, 2])
check("« Nb trajets » = lignes fusionnées (2)", out["nb_trajets"] == 2,
      f"got {out['nb_trajets']}")

print("\n=== E2 — compteurs INTACTS : tous les arrêts déduits (AM-1) ===")
db.refresh(s)
att_tcj = (dt(JPASS, 13, 6) - dt(JPASS, 9, 54)).total_seconds() \
    + (dt(JPASS, 13, 55) - dt(JPASS, 13, 13)).total_seconds() \
    + (dt(JPASS, 16, 0) - dt(JPASS, 14, 36)).total_seconds()
check("TCJ = Σ conduite réelle (7 min ET 41 min déduits)",
      s.tcj_s == int(att_tcj), f"got {s.tcj_s} attendu {int(att_tcj)}")
check("TTJ = amplitude brute 09:54→16:00",
      s.ttj_s == int((dt(JPASS, 16, 0) - dt(JPASS, 9, 54)).total_seconds()),
      f"got {s.ttj_s}")

print("\n=== E1/E3 — seuils : RÉALIGNÉ v1.33 sur §0tricies decies G1/G2 ===")
print("    (E3 abrogée le 25/08/2026 : plus de bande 20-29 min à pause")
print("     masquée — un arrêt < 30 min FUSIONNE, ≥ 30 min deux lignes + pause)")
v2 = veh("0916TBV")
ligne(v2, JPASS, 8, 0, 10, 0, 12.0)    # rupture 21 min
ligne(v2, JPASS, 10, 21, 12, 0, 8.0)
db.commit()
tr2 = s_suivi(suivi_de(v2, JPASS), engine.get_seuils(db))["trajets"]
check("G1 — rupture 21 min (< 30 min) : UNE SEULE ligne 08:00→12:00",
      len(tr2) == 1 and tr2[0]["heure_debut"][11:16] == "08:00"
      and tr2[0]["heure_fin"][11:16] == "12:00",
      f"got {[(t['heure_debut'][11:16], t['heure_fin'][11:16]) for t in tr2]}")
check("G1 — ligne fusionnée : distances sommées (12 + 8), segments cumulés",
      len(tr2) == 1 and abs((tr2[0]["distance_km"] or 0) - 20.0) < 1e-9
      and tr2[0]["segments"] == 2)
check("G1 — aucune case pause sur une ligne unique (fin de journée)",
      len(tr2) == 1 and tr2[0]["pause_apres_s"] in (0, None))

v3 = veh("3076TBS")
ligne(v3, JPASS, 8, 0, 10, 0, 12.0)    # rupture 40 min
ligne(v3, JPASS, 10, 40, 12, 0, 8.0)
db.commit()
tr3 = s_suivi(suivi_de(v3, JPASS), engine.get_seuils(db))["trajets"]
check("rupture 40 min : DEUX lignes + pause affichée 2400 s (G2)",
      len(tr3) == 2 and tr3[0]["pause_apres_s"] == 2400,
      f"got {len(tr3)} / {tr3[0]['pause_apres_s']}")

print("\n=== E1 — fonction pure : statut de la DERNIÈRE composante, idempotence, zéro mutation ===")
brut = [
    {"id": "a", "numero": 1, "heure_debut": "2026-08-21T09:54:00",
     "heure_fin": "2026-08-21T13:06:00", "pause_apres_s": 0,
     "statut_source": "VALIDÉ", "source_plateforme": "CAMTRACKPRO",
     "distance_km": 31.0, "statut_validation": "VALIDE", "segments": 1},
    {"id": "b", "numero": 2, "heure_debut": "2026-08-21T13:13:00",
     "heure_fin": "2026-08-21T13:55:00", "pause_apres_s": 2460,
     "statut_source": "PROVISOIRE", "source_plateforme": "MZONEX",
     "distance_km": None, "statut_validation": "EN_ATTENTE", "segments": 2},
    {"id": "c", "numero": 3, "heure_debut": "2026-08-21T14:36:00",
     "heure_fin": "2026-08-21T16:00:00", "pause_apres_s": 0,
     "statut_source": "VALIDÉ", "source_plateforme": "CAMTRACKPRO",
     "distance_km": 20.0, "statut_validation": "VALIDE", "segments": 1},
]
avant = copy.deepcopy(brut)
fus = fusionner_trajets_affichage(brut)
check("entrée JAMAIS mutée", brut == avant)
check("2 lignes fusionnées", len(fus) == 2)
check("statut/couleur = DERNIÈRE composante (PROVISOIRE/EN_ATTENTE)",
      fus[0]["statut_source"] == "PROVISOIRE"
      and fus[0]["statut_validation"] == "EN_ATTENTE")
check("distance partielle : 31.0 + None → 31.0",
      fus[0]["distance_km"] == 31.0)
check("segments sommés (1 + 2)", fus[0]["segments"] == 3)
check("idempotente : fusionner(fusionner(X)) == fusionner(X)",
      fusionner_trajets_affichage(fus) == fus)
d_none = fusionner_trajets_affichage([
    {"numero": 1, "heure_debut": "2026-08-21T08:00:00",
     "heure_fin": "2026-08-21T09:00:00", "pause_apres_s": 0,
     "statut_source": "VALIDÉ", "distance_km": None,
     "statut_validation": "VALIDE", "segments": 1},
    {"numero": 2, "heure_debut": "2026-08-21T09:05:00",
     "heure_fin": "2026-08-21T10:00:00", "pause_apres_s": 0,
     "statut_source": "VALIDÉ", "distance_km": None,
     "statut_validation": "VALIDE", "segments": 1}])
check("deux composantes sans distance → distance None (pas de 0 inventé)",
      len(d_none) == 1 and d_none[0]["distance_km"] is None)

print("\n=== E1 — snapshots d'archive : MÊME règle, zéro réécriture (§A.2) ===")
v4 = veh("4876TBU")
s4 = suivi_de(v4, JPASS)
snap = {"plaque": "4876TBU", "nb_trajets": 2, "trajets": copy.deepcopy(brut)}
h = HistoriqueJournalier(date_jour=JPASS, annee=2026, mois=8,
                         vehicule_id=v4.id, conducteur_id=None,
                         donnees=copy.deepcopy(snap),
                         nb_infractions=0, nb_alertes=0)
db.add(h)
db.commit()
snap_avant = copy.deepcopy(h.donnees)
out_h = s_historique(h, detail=True)
check("archive : ligne fusionnée à la lecture (2 lignes)",
      len(out_h["donnees"]["trajets"]) == 2)
check("archive : nb_trajets relu = 2", out_h["donnees"]["nb_trajets"] == 2)
check("archive : snapshot en base JAMAIS muté (§A.2)",
      h.donnees == snap_avant)
out_h2 = s_historique(h, detail=False)
check("archive (mode compact) : nb_trajets aussi fusionné (2)",
      out_h2["nb_trajets"] == 2, f"got {out_h2['nb_trajets']}")
fuse_twice = fusionner_snapshot(out_h["donnees"])
check("snapshot déjà fusionné → relecture sans effet",
      fuse_twice["trajets"] == out_h["donnees"]["trajets"])

print("\n=== E4 — correctif d'unité : buffer MZoneX en DEGRÉS ===")
r1 = geozones._rayon_mzonex_m({"buffer": 0.0005})
check("buffer 0.0005° ≈ 55,7 m (degrés → mètres)", 54 < r1 < 58,
      f"got {r1}")
r2 = geozones._rayon_mzonex_m({"buffer": 1})
check("buffer 1° ≈ 111,3 km", abs(r2 - 111320) < 5, f"got {r2}")
r3 = geozones._rayon_mzonex_m({
    "buffer": 0, "bottomLatitude": -18.881, "topLatitude": -18.877,
    "leftLongitude": 47.505, "rightLongitude": 47.510})
check("buffer 0 → repli emprise (mètres, plausible)", 100 < r3 < 500,
      f"got {r3}")

print("\n=== E4 — nommage : zone la plus SPÉCIFIQUE, « proche » < 5 km, sinon None ===")
ZBASE = (LAT, LNG, 150.0, "Base LSS — Antananarivo", "MZONEX")
ZGARE = (LAT + 0.027, LNG, 400.0, "Gare Soarano", "CAMTRACKPRO")  # ~3 km au nord
ZREG = (LAT, LNG, 60000.0, "Analamanga (région)", "MZONEX")       # géante
zones_de_test([ZBASE, ZGARE, ZREG])
check("point dans Base LSS (malgré la région géante) → « Base LSS… »",
      geozones.libelle_position(LAT, LNG) == "Base LSS — Antananarivo",
      f"got {geozones.libelle_position(LAT, LNG)}")
check("point hors de toute zone mais à ~1 km de Gare Soarano → « proche … »",
      geozones.libelle_position(LAT + 0.027, LNG + 0.01) == "proche Gare Soarano",
      f"got {geozones.libelle_position(LAT + 0.027, LNG + 0.01)}")
check("point très loin → None (l'appelant écrira les coordonnées)",
      geozones.libelle_position(-12.0, 49.0) is None)
zones_de_test([ZREG])
check("zone géante (> 5 km de rayon) JAMAIS utilisée pour nommer",
      geozones.libelle_position(LAT, LNG) is None)
zones_de_test([])
check("cache vide → None (pas de nom inventé, §10)",
      geozones.libelle_position(LAT, LNG) is None)

print("\n=== E4 — ingestion : adresses nommées à l'arrivée des points GPS ===")
zones_de_test([ZBASE, ZGARE, ZREG])
v5 = veh("0826TBS")
engine.ingest_event(db, v5, dt(JPASS, 9, 0), LAT, LNG, None, 24.0, "ON",
                    source=SourceEvenement.MZONEX)
engine.ingest_event(db, v5, dt(JPASS, 9, 20), LAT, LNG, None, 0.0, "ON",
                    source=SourceEvenement.MZONEX)
db.commit()
ev5 = db.scalar(select(EvenementGPS).where(
    EvenementGPS.vehicule_id == v5.id).order_by(EvenementGPS.horodatage.desc()))
arret5 = db.scalar(select(SuiviJournalier).where(
    SuiviJournalier.vehicule_id == v5.id,
    SuiviJournalier.date_jour == JPASS)).arret_final
check("événement GPS nommé « Base LSS — Antananarivo »",
      ev5.adresse == "Base LSS — Antananarivo", f"got {ev5.adresse!r}")
check("vehicule.last_adresse = nom de zone",
      veh("0826TBS").last_adresse == "Base LSS — Antananarivo")
check("« Arrêt final » porte le nom (plus de « position inconnue »)",
      arret5 is not None and "Base LSS" in arret5
      and "position inconnue" not in arret5, f"got {arret5!r}")
zones_de_test([])
engine.ingest_event(db, v5, dt(JPASS, 9, 21), LAT, LNG, None, 15.0, "ON",
                    source=SourceEvenement.MZONEX)
db.commit()
db.refresh(v5)
check("hors zones : secours coordonnées « -18.8792, 47.5079 »",
      v5.last_adresse == f"{LAT:.4f}, {LNG:.4f}", f"got {v5.last_adresse!r}")

print("\n=== E5 — colonne J-1 : dérivation existante conservée, repli géozone ===")
zones_de_test([ZBASE, ZGARE, ZREG])
v6 = veh("5716TBS")
s6 = suivi_de(v6, JVEILLE)
s6.arret_final = "18:42 · Base LSS — Antananarivo"
db.commit()
s6j = engine.ensure_suivi(db, v6, JJOUR)
check("J-1 dérivé de l'arrêt final de la veille (« Base LSS… »)",
      s6j.emplacement_j_moins_1 == "Base LSS — Antananarivo",
      f"got {s6j.emplacement_j_moins_1!r}")

v7 = veh("5506TBS")
evenement(v7, dt(JVEILLE, 19, 5))
db.commit()
s7j = engine.ensure_suivi(db, v7, JJOUR)
check("sans arrêt final : repli sur la dernière position GPS de la veille",
      s7j.emplacement_j_moins_1 == "Base LSS — Antananarivo",
      f"got {s7j.emplacement_j_moins_1!r}")

v8 = veh("6546TCE")
s8 = suivi_de(v8, JVEILLE)
s8.arret_final = "01:00 · position inconnue"     # héritage de l'ère 01h00
db.commit()
evenement(v8, dt(JVEILLE, 18, 30))
db.commit()
s8j = engine.ensure_suivi(db, v8, JJOUR)
check("« position inconnue » de la veille remplacée par la géozone",
      s8j.emplacement_j_moins_1 == "Base LSS — Antananarivo",
      f"got {s8j.emplacement_j_moins_1!r}")

print("\n=== E5 — rattrapage UNIQUE au premier démarrage v1.31 ===")
v9 = veh("7306TCE")
suivi_de(v9, JJOUR)                      # ligne du jour créée vide (J-1 None)
db.commit()
evenement(v9, dt(JVEILLE, 19, 55))       # position d'hier soir arrivée après
db.commit()
n_av = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "position_j1_v131.terminee")) or 0
rap1 = engine.rattrapage_position_j1_v131(db, JJOUR)
db.commit()
s9j = db.scalar(select(SuiviJournalier).where(
    SuiviJournalier.vehicule_id == v9.id,
    SuiviJournalier.date_jour == JJOUR))
check("rattrapage effectué", rap1["statut"] == "faite",
      f"got {rap1}")
check("colonne J-1 du jour remplie « Base LSS — Antananarivo »",
      s9j.emplacement_j_moins_1 == "Base LSS — Antananarivo",
      f"got {s9j.emplacement_j_moins_1!r}")
check("marqueur d'audit posé", (db.scalar(select(func.count(AuditLog.id))
      .where(AuditLog.action == "position_j1_v131.terminee")) or 0) == n_av + 1)
lignes_remplies = {s.vehicule_id: s.emplacement_j_moins_1 for s in db.scalars(
    select(SuiviJournalier).where(SuiviJournalier.date_jour == JJOUR))}
rap2 = engine.rattrapage_position_j1_v131(db, JJOUR)
db.commit()
lignes_apres = {s.vehicule_id: s.emplacement_j_moins_1 for s in db.scalars(
    select(SuiviJournalier).where(SuiviJournalier.date_jour == JJOUR))}
check("2e exécution : « deja_faite » (une seule fois)",
      rap2["statut"] == "deja_faite")
check("2e exécution : aucune donnée touchée (idempotent)",
      lignes_apres == lignes_remplies)
s6j_bis = db.scalar(select(SuiviJournalier).where(
    SuiviJournalier.vehicule_id == v6.id,
    SuiviJournalier.date_jour == JJOUR))
check("les lignes déjà correctement remplies ne bougent pas",
      s6j_bis.emplacement_j_moins_1 == "Base LSS — Antananarivo")

print(f"\n==== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ====")
db.close()
try:
    if db_url.startswith("sqlite:///"):
        os.remove(db_url.replace("sqlite:///", "/", 1))
        print("Base de test supprimée.")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
