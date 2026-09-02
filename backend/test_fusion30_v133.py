# -*- coding: utf-8 -*-
"""Tests v1.33 — §0tricies decies (arbitrage LSS DIRECT du 25/08/2026) :

G1 : un arrêt STRICTEMENT < 30 min → les deux lignes sont FUSIONNÉES en UNE
     seule, pas de case pause (seuil d'affichage porté par le nouveau réglage
     SEUIL_FUSION_AFFICHAGE_S = 1800 ; E1 amendée : 20 → 30 min) ;
G2 : un arrêt ≥ 30 min → deux lignes + pause AFFICHÉE (E3 abrogée : plus de
     bande « 20-29 min : deux lignes + case « — » masquée) ;
Périmètre strict : compteurs E2/F1 INTACTS (tous les arrêts déduits du TCJ ;
coupure du TCC à 30 min) et seuil MOTEUR DUREE_MIN_PAUSE_VALIDE = 20 min
INTACT (couleur orange/noir, réouverture R1/F2, ingestion) ;
Archives (§A.2) : relecture des snapshots déjà fusionnés à 20 min (v1.31/1.32)
= même résultat que fusionner à 30 min l'original — transitivité, jamais de
réécriture.

Cas réels du 25/08/2026 (portails vérifiés à la seconde) rejoués :
 - 0916TBV : rupture 20 min 06 s (07:48:24 → 08:08:30)   → fusionnée ;
 - 0576TCD : rupture 22 min 59 s (11:13:15 → 11:36:14)   → fusionnée ;
 - 8076TCB : rupture 25 min 50 s (07:46:27 → 08:12:17)   → fusionnée ;
   puis journée 8076TCB complète en intégration s_suivi + compteurs.

RÉALIGNÉ v1.34 (§0quaterdecies H1/H2, 25/08/2026) : le contrôle du TCC
8076TCB attend désormais le CHRONO de session (arrêt en cours de 25 min
inclus) au lieu de la conduite pure de la dernière trajectoire.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v133.db" SIM_ENABLE=0 python3 test_fusion30_v133.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import copy
import sys
from datetime import date, datetime

from sqlalchemy import delete, func, select

from app.database import SessionLocal
from app import engine
from app.models import (HistoriqueJournalier, ParametrageSeuil,
                        StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, Vehicule,
                        EvenementGPS, Alerte, AuditLog, Infraction, Mission)
from app.serializers import (FUSION_AFFICHAGE_S, fusionner_snapshot,
                             fusionner_trajets_affichage, s_historique, s_suivi)
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

# Leçon « base /tmp périmée » : suppression AVANT le seed (un crash antérieur
# laisserait des véhicules fantômes aux uid déterministes).
if db_url.startswith("sqlite:///"):
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

JPASS = date(2026, 8, 25)          # journée de l'incident (passée → OFFICIEL)


def dt(hh, mm, ss=0):
    return datetime(JPASS.year, JPASS.month, JPASS.day, hh, mm, ss)


def veh(plaque):
    return db.scalar(select(Vehicule).where(Vehicule.plaque == plaque))


def suivi_de(v, jour):
    s = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v.id,
        SuiviJournalier.date_jour == jour))
    if s is None:
        s = engine.ensure_suivi(db, v, jour)
    return s


def ligne(v, d1, d2, dist,
          validation=StatutValidationTrajet.VALIDE,
          source=StatutSourceTrajet.VALIDE, plateforme="MZONEX"):
    s = suivi_de(v, JPASS)
    n = db.scalar(select(func.count(Trajet.id)).where(
        Trajet.suivi_id == s.id)) or 0
    t = Trajet(suivi_id=s.id, numero=n + 1, heure_debut=d1, heure_fin=d2,
               distance_km=dist, statut_source=source,
               statut_validation=validation, source_plateforme=plateforme)
    db.add(t)
    db.flush()
    return t


def L(deb, fin, dist=None, segments=1, statut="VALIDÉ", validation="VALIDE"):
    return {"id": f"{deb}/{fin}", "numero": 0,
            "heure_debut": deb.isoformat(), "heure_fin": fin.isoformat() if fin else None,
            "pause_apres_s": 0, "statut_source": statut,
            "source_plateforme": "MZONEX", "distance_km": dist,
            "statut_validation": validation, "segments": segments}


def bornes(lignes):
    return [(t["heure_debut"][11:19],
             (t["heure_fin"][11:19] if t["heure_fin"] else None),
             t["pause_apres_s"]) for t in lignes]


# ==================================================================== réglage
print("\n=== Réglage — nouveau seuil inscrit, seuils MOTEUR intacts (périmètre strict) ===")
seuils = engine.get_seuils(db)
check("SEUIL_FUSION_AFFICHAGE_S présent et = 1800 (30 min)",
      float(seuils.get("SEUIL_FUSION_AFFICHAGE_S", -1)) == 1800.0)
check("SEUIL_FUSION_AFFICHAGE_S inséré en base (visible dans Paramètres)",
      db.scalar(select(func.count(ParametrageSeuil.id)).where(
          ParametrageSeuil.cle == "SEUIL_FUSION_AFFICHAGE_S")) == 1)
check("constante de repli FUSION_AFFICHAGE_S = 1800 (relecture archives)",
      FUSION_AFFICHAGE_S == 1800.0)
check("DUREE_MIN_PAUSE_VALIDE TOUJOURS = 1200 (rôles moteur intacts)",
      float(seuils.get("DUREE_MIN_PAUSE_VALIDE", -1)) == 1200.0)
check("SEUIL_PAUSE_COUPURE_TCC TOUJOURS = 1800 (coupure TCC intacte)",
      float(seuils.get("SEUIL_PAUSE_COUPURE_TCC", -1)) == 1800.0)

# ===================================================== cas réels du 25/08 (G1)
print("\n=== G1 — les TROIS cas réels du 25/08/2026, rejoués à la seconde ===")
cas_reels = [
    ("0916TBV : 20 min 06 s (07:48:24 → 08:08:30)",
     (dt(7, 48, 24), dt(8, 8, 30))),
    ("0576TCD : 22 min 59 s (11:13:15 → 11:36:14)",
     (dt(11, 13, 15), dt(11, 36, 14))),
    ("8076TCB : 25 min 50 s (07:46:27 → 08:12:17)",
     (dt(7, 46, 27), dt(8, 12, 17))),
]
for nom, (fin1, deb2) in cas_reels:
    brut = [L(dt(fin1.hour - 1, 0, 0), fin1, 20.0),
            L(deb2, dt(deb2.hour + 1, 0, 0), 10.0)]
    avant = copy.deepcopy(brut)
    fus = fusionner_trajets_affichage(brut)   # seuil par défaut = 1800 (G1)
    check(f"{nom} → UNE ligne fusionnée, aucune case pause",
          len(fus) == 1 and fus[0]["pause_apres_s"] == 0
          and fus[0]["heure_fin"] == dt(deb2.hour + 1, 0, 0).isoformat())
    check(f"{nom} → entrée jamais mutée", brut == avant)

# ================================================================= bornes (G1/G2)
print("\n=== G1/G2 — bornes exactes du seuil 30 min (STRICTEMENT <) ===")
b29 = fusionner_trajets_affichage(
    [L(dt(8, 0, 0), dt(9, 0, 0), 5.0), L(dt(9, 29, 59), dt(10, 0, 0), 5.0)])
check("rupture 29 min 59 s (< 1800) → fusionnée",
      len(b29) == 1 and b29[0]["pause_apres_s"] == 0, f"got {bornes(b29)}")
b30 = fusionner_trajets_affichage(
    [L(dt(8, 0, 0), dt(9, 0, 0), 5.0), L(dt(9, 30, 0), dt(10, 0, 0), 5.0)])
check("rupture EXACTEMENT 30 min 00 s (= 1800) → 2 lignes + pause 0:30",
      len(b30) == 2 and b30[0]["pause_apres_s"] == 1800, f"got {bornes(b30)}")
b31 = fusionner_trajets_affichage(
    [L(dt(8, 0, 0), dt(9, 0, 0), 5.0), L(dt(9, 30, 1), dt(10, 0, 0), 5.0)])
check("rupture 30 min 01 s → 2 lignes + pause affichée",
      len(b31) == 2 and b31[0]["pause_apres_s"] == 1801, f"got {bornes(b31)}")
chaine = fusionner_trajets_affichage(
    [L(dt(8, 0), dt(9, 0), 5.0), L(dt(9, 20), dt(10, 0), 5.0),
     L(dt(10, 20), dt(11, 0), 5.0), L(dt(11, 45), dt(12, 0), 5.0)])
check("chaîne de ruptures 20/20 min → tout fusionne en UNE ligne",
      len(chaine) == 2 and chaine[0]["heure_fin"] == dt(11, 0).isoformat()
      and chaine[0]["pause_apres_s"] == 2700, f"got {bornes(chaine)}")
check("idempotente au nouveau seuil : f(f(X)) == f(X)",
      fusionner_trajets_affichage(chaine) == chaine)

# ============================================= journée portail 0916TBV complète
print("\n=== G1/G2 — journée OFFICIELLE 0916TBV du 25/08 (portail MZoneX) ===")
jour_mz = [L(dt(5, 44, 18), dt(7, 48, 24), 37.526),
           L(dt(8, 8, 30), dt(10, 0, 51), 35.055),
           L(dt(10, 36, 36), dt(13, 52, 20), 53.465)]
fus_mz = fusionner_trajets_affichage(jour_mz)
check("2 lignes affichées : [05:44:18→10:00:51] puis [10:36:36→13:52:20]",
      bornes(fus_mz)[0][:2] == ("05:44:18", "10:00:51")
      and bornes(fus_mz)[1][:2] == ("10:36:36", "13:52:20"),
      f"got {bornes(fus_mz)}")
check("pause visible = 35 min 45 s (2145 s, ≥ 30 min)",
      fus_mz[0]["pause_apres_s"] == 2145)
check("distances sommées sur la ligne fusionnée (37,526 + 35,055)",
      abs((fus_mz[0]["distance_km"] or 0) - 72.581) < 1e-9)
check("numérotation re-séquencée 1, 2 — « Nb trajets » suit",
      [t["numero"] for t in fus_mz] == [1, 2])

# ============================================================== archives (§A.2)
print("\n=== Archives (§A.2) — relecture transitive, jamais de réécriture ===")
brut4 = [L(dt(8, 0), dt(9, 0), 5.0), L(dt(9, 7), dt(10, 0), 5.0),
         L(dt(10, 25), dt(11, 0), 5.0), L(dt(11, 41), dt(12, 0), 5.0)]
f20 = fusionner_trajets_affichage(copy.deepcopy(brut4), seuil_fusion_s=1200.0)
check("forme stockée « v1.31/1.32 » (fusion 20 min) : 3 lignes",
      len(f20) == 3, f"got {len(f20)}")
f30_direct = fusionner_trajets_affichage(copy.deepcopy(brut4))
f30_relut = fusionner_trajets_affichage(copy.deepcopy(f20))
check("transitivité : relire à 30 min une archive fusionnée à 20 min "
      "= fusionner l'original à 30 min", f30_relut == f30_direct)
check("→ les ruptures 7 et 25 min disparaissent de l'archive relue (2 lignes)",
      len(f30_relut) == 2 and f30_relut[0]["heure_fin"] == dt(11, 0).isoformat())

v_arch = veh("4876TBU")
s_arch = suivi_de(v_arch, JPASS)
db.commit()
snap_stocke = {"plaque": "4876TBU", "nb_trajets": len(f20),
               "trajets": copy.deepcopy(f20)}
h = HistoriqueJournalier(date_jour=JPASS, annee=2026, mois=8,
                         vehicule_id=v_arch.id, conducteur_id=None,
                         donnees=copy.deepcopy(snap_stocke),
                         nb_infractions=0, nb_alertes=0)
db.add(h)
db.commit()
snap_avant = copy.deepcopy(h.donnees)
out_h = s_historique(h, detail=True)
check("s_historique : archive relue à 30 min (2 lignes)",
      len(out_h["donnees"]["trajets"]) == 2
      and out_h["donnees"]["nb_trajets"] == 2)
check("snapshot en base JAMAIS muté (§A.2, aucune archive réécrite)",
      h.donnees == snap_avant)

# ============================================== intégration s_suivi : 8076TCB
print("\n=== Intégration s_suivi — journée COMPLÈTE 8076TCB du 25/08 ===")
v = veh("8076TCB")
ligne(v, dt(7, 33, 7), dt(7, 46, 27), 1.08)      # rupture 25 min 50 s (G1)
ligne(v, dt(8, 12, 17), dt(8, 28, 48), 1.624)    # rupture 1 h 43 (G2)
ligne(v, dt(10, 11, 50), dt(12, 35, 13), 63.036)  # rupture 39 min 47 s (G2)
ligne(v, dt(13, 15, 3), dt(14, 0, 0), 5.0)
db.commit()
s = suivi_de(v, JPASS)
db.commit()
engine.recalculer_temps(db, s, dt(14, 25))   # stop de 25 min < coupure TCC
db.commit()
db.refresh(s)
out = s_suivi(s, engine.get_seuils(db))
tr = out["trajets"]
check("3 lignes affichées (la rupture de 25 min 50 s est absorbée)",
      len(tr) == 3 and out["nb_trajets"] == 3, f"got {bornes(tr)}")
check("ligne 1 fusionnée : 07:33:07 → 08:28:48, pause 1:43:02 affichée",
      bornes(tr)[0] == ("07:33:07", "08:28:48", 6182), f"got {bornes(tr)}")
check("ligne 2 : 10:11:50 → 12:35:13, pause 0:39:50 affichée",
      bornes(tr)[1] == ("10:11:50", "12:35:13", 2390), f"got {bornes(tr)}")
check("ligne 3 : 13:15:03 → 14:00:00 (pas de pause après la fin de journée)",
      bornes(tr)[2] == ("13:15:03", "14:00:00", 0), f"got {bornes(tr)}")
check("distance de la ligne fusionnée = 1,08 + 1,624 (rien d'inventé)",
      abs((tr[0]["distance_km"] or 0) - 2.704) < 1e-9,
      f"got {tr[0]['distance_km']}")
d1 = (dt(7, 46, 27) - dt(7, 33, 7)).total_seconds()
d2 = (dt(8, 28, 48) - dt(8, 12, 17)).total_seconds()
d3 = (dt(12, 35, 13) - dt(10, 11, 50)).total_seconds()
d4 = (dt(14, 0, 0) - dt(13, 15, 3)).total_seconds()
check("compteurs INTACTS (E2/F1) : TCJ = Σ des 4 durées de conduite "
      "(TOUS les arrêts déduits, même 25 min 50 s)",
      s.tcj_s == int(d1 + d2 + d3 + d4), f"got {s.tcj_s} attendu {int(d1+d2+d3+d4)}")
check("TTJ = amplitude brute 07:33:07 → 14:00:00",
      s.ttj_s == int((dt(14, 0, 0) - dt(7, 33, 7)).total_seconds()),
      f"got {s.ttj_s}")
check("TCC (§0quaterdecies H1/H2) : session = chrono depuis la reprise 13:15 "
      "(les pauses 1:43 ET 0:39 coupent) + l'arrêt en cours de 25 min INCLUS "
      "= 1:09:57", s.tcc_s == int((dt(14, 25) - dt(13, 15, 3)).total_seconds()),
      f"got {s.tcc_s} attendu {int((dt(14, 25) - dt(13, 15, 3)).total_seconds())}")

# =========================================================== ligne EN COURS
print("\n=== G1 — rupture < 30 min vers une ligne EN COURS : une seule ligne ouverte ===")
en_cours = fusionner_trajets_affichage([
    L(dt(10, 0, 0), dt(11, 13, 15), 20.859),
    L(dt(11, 36, 14), None, 5.0, statut="PROVISOIRE", validation="EN_ATTENTE")])
check("0576TCD fin de matinée : UNE ligne ouverte 10:00→ (fin vide, orange)",
      len(en_cours) == 1 and en_cours[0]["heure_fin"] is None
      and en_cours[0]["statut_source"] == "PROVISOIRE"
      and en_cours[0]["statut_validation"] == "EN_ATTENTE",
      f"got {bornes(en_cours)}")
check("ligne ouverte : pas de case pause",
      en_cours[0]["pause_apres_s"] == 0)

# ================================================== export = écran (source unique)
print("\n=== Écran = export = archive (§A.2) — une seule source fusionnée ===")
check("nb_trajets == nb de lignes affichées (cohérence grille/export)",
      out["nb_trajets"] == len(tr))
relance = s_suivi(s, engine.get_seuils(db))
check("s_suivi déterministe (deux appels, mêmes lignes)",
      bornes(relance["trajets"]) == bornes(tr))

print(f"\n{'=' * 64}\nRÉSULTAT : {R['ok']} OK / {R['ko']} KO\n{'=' * 64}")
db.close()
try:
    if db_url.startswith("sqlite:///"):
        os.remove(db_url.replace("sqlite:///", "/", 1))
        print("Base de test supprimée.")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
