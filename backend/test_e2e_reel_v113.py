"""E2E v1.13 sur la VRAIE base bac à sable (copie) + VRAIS trajets du portail
relus le 04/08 à ~10h47 — vérifie la guérison des lignes défectueuses vues
sur les captures métier (jumeaux orange/noir, noir prématuré, fausse pause).

Exécution : python3 test_e2e_reel_v113.py   (copie backend/data/lss.db→/tmp)
"""
import os, shutil, sys
os.environ.setdefault("SIM_ENABLE", "0")
os.environ["DATABASE_URL"] = "sqlite:////tmp/e2e_v113.db"
from datetime import datetime

src = "data/lss.db"
if not os.path.exists(src):
    # Un test qui n'a PAS tourné n'est pas un test vert : statut NON-EXÉCUTABLE
    # explicite et code de retour NON NUL (le mot « sauté » est conservé : la
    # campagne classe la suite en « NON EXÉCUTÉ », jamais en réussite).
    print("=== NON EXÉCUTABLE : base locale absente (data/lss.db) — "
          "test sauté ===")
    print("    Aucun contrôle n'a tourné : ce n'est pas un vert.")
    sys.exit(3)
shutil.copy(src, "/tmp/e2e_v113.db")

from sqlalchemy import delete, select
from app.database import SessionLocal
from app import engine
from app.models import (StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, Vehicule)
from app.reconciliation import reconcilier_trajets_valides
from app.main import migrer_schema
from app.serializers import s_suivi

R = {"ok": 0, "ko": 0}
def check(nom, cond, info=""):
    R["ok" if cond else "ko"] += 1
    print(f"  {'✅' if cond else '❌'} {nom}{'' if cond else ' ' + str(info)}")

migrer_schema()
db = SessionLocal()
jour = datetime.now().date()
base = datetime.combine(jour, datetime.min.time())
def h(hh, mm, ss=0): return base.replace(hour=hh, minute=mm, second=ss)

# heures UTC de la sonde (navigateur bac à sable en UTC) — la logique est
# indifférente au fuseau : ce sont les « heures de la base » ici.
# « rejetes » = manœuvres < 0,3 km détectées EN DIRECT par le Niveau 1 sur le
# PC métier. ATTENDUS Référence v2 / arbitrages du 06/08 (v1.18) : elles sont
# IGNORÉES PARTOUT (§7 — ne soudent plus, n'ancrent plus le départ, ne
# prolongent plus la fin) ; le Niveau 2 n'en pose AUCUN segment.
CAS = {
    "4526TCC": {
        "items": [(h(2, 42, 15), h(2, 52, 43), 0.0),
                  (h(2, 56, 18), h(5, 21, 15), 46.689)],
        "rejetes": [(h(2, 42, 15), h(2, 52, 43), 0.0)],
        "jumeau_debut": h(2, 56, 18),
        "roule": True,          # reparti à 05:25:31 UTC, encore en route à la sonde
        "attendu": dict(nb=1, debut=h(2, 56, 18), fin=None, noir=False),  # §7 : manœuvre de tête ignorée
    },
    "5316TBU": {
        "items": [(h(3, 5, 34), h(4, 28, 14), 37.64),
                  (h(4, 35, 59), h(4, 43, 26), 4.015),
                  (h(4, 45, 55), h(6, 27, 28), 48.68),
                  (h(6, 32, 43), h(6, 48, 14), 4.386),
                  (h(7, 31, 31), h(7, 43, 6), 1.786)],
        "rejetes": [],
        "jumeau_debut": h(3, 5, 34),
        "roule": False,
        "attendu": None,        # vérifié à la main ci-dessous (2 lignes)
    },
    "0916TBV": {
        "items": [(h(2, 39, 36), h(2, 49, 11), 0.0),
                  (h(2, 53, 47), h(3, 34, 54), 13.539),
                  (h(3, 42, 19), h(4, 52, 1), 27.689),
                  (h(5, 8, 13), h(7, 13, 23), 43.625)],
        "rejetes": [(h(2, 39, 36), h(2, 49, 11), 0.0)],
        "jumeau_debut": h(2, 53, 47),
        "roule": False,
        # Réalignement v1.31 (§0undecies E1, 24/08/2026) : ruptures 7:25 et
        # 16:12 < 20 min → UNE ligne affichée 02:53:47 → 07:13:23 ; manœuvre
        # de tête toujours ignorée, base inchangée
        "attendu": dict(nb=1, debut=h(2, 53, 47), fin=h(7, 13, 23), noir=True),
    },
    "9856TCD": {
        "items": [(h(3, 4, 53), h(3, 13, 54), 0.0),
                  (h(4, 41, 4), h(4, 51, 12), 1.119),
                  (h(5, 56, 16), h(5, 58, 36), 0.0),
                  (h(6, 8, 16), h(6, 15, 16), 1.467),
                  (h(6, 20, 45), h(6, 22, 47), 0.188),
                  (h(6, 31, 31), h(6, 37, 40), 0.0),
                  (h(6, 46, 11), h(6, 51, 54), 0.044),
                  (h(7, 20, 52), h(7, 50, 40), 0.071)],
        # manœuvres vues en direct par le Niveau 1 (PC métier)
        "rejetes": [(h(3, 4, 53), h(3, 13, 54), 0.0),
                    (h(5, 56, 16), h(5, 58, 36), 0.0),
                    (h(6, 20, 45), h(6, 22, 47), 0.188),
                    (h(6, 31, 31), h(6, 37, 40), 0.0),
                    (h(6, 46, 11), h(6, 51, 54), 0.044),
                    (h(7, 20, 52), h(7, 50, 40), 0.071)],
        "jumeau_debut": h(5, 56, 16),
        "roule": False,
        "attendu": None,
    },
}

try:
    for plaque, cas in CAS.items():
        v = db.scalar(select(Vehicule).where(Vehicule.plaque == plaque))
        if v is None:
            print(f"— {plaque} absent de la base bac à sable, sauté")
            continue
        print(f"\n===== {plaque} =====")
        s = engine.ensure_suivi(db, v, jour)
        db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
        # manœuvres vues en direct par le Niveau 1 (rejetées, jamais affichées
        # seules mais soudant les chaînes)
        for j, (d, f, km) in enumerate(cas["rejetes"]):
            db.add(Trajet(suivi_id=s.id, numero=100 + j, heure_debut=d,
                          heure_fin=f, statut_source=StatutSourceTrajet.PROVISOIRE,
                          source_plateforme="MZONEX", distance_km=km,
                          statut_validation=StatutValidationTrajet.REJETE))
        # débris « v1.12 » : jumeau ouvert + doublon officiel au même début
        if cas["jumeau_debut"]:
            p, f, km = cas["items"][1]
            db.add(Trajet(suivi_id=s.id, numero=1, heure_debut=cas["jumeau_debut"],
                          heure_fin=None, statut_source=StatutSourceTrajet.PROVISOIRE,
                          source_plateforme="MZONEX", distance_km=None,
                          statut_validation=StatutValidationTrajet.EN_ATTENTE))
            db.add(Trajet(suivi_id=s.id, numero=2, heure_debut=cas["jumeau_debut"],
                          heure_fin=f, statut_source=StatutSourceTrajet.VALIDE,
                          source_plateforme="MZONEX", distance_km=km,
                          statut_validation=StatutValidationTrajet.VALIDE))
        v.last_vitesse = 35.0 if cas["roule"] else 0.0
        from app.config import now_local
        v.last_event_at = now_local()  # signal frais
        db.commit()
        stats = reconcilier_trajets_valides(db, [
            {"vehicule_id": v.id, "gps_associe": v.gps_associe, "plaque": v.plaque,
             "debut": d, "fin": f, "distance_km": km, "source": "MZONEX"}
            for d, f, km in cas["items"]], maintenant=now_local())
        print(f"  stats : {stats}")
        g = s_suivi(db.get(SuiviJournalier, s.id))
        for t in g["trajets"]:
            print(f"  LIGNE | {t['heure_debut'][11:16]} → "
                  f"{t['heure_fin'][11:16] if t['heure_fin'] else 'en cours'}"
                  f" | pause {t['pause_apres_s']//60} min | {t['statut_source']}")
        a = cas["attendu"]
        if a:
            ts = g["trajets"]
            l = ts[0] if ts else None
            check("nombre de lignes", len(ts) == a["nb"], f"{len(ts)}")
            check("début", l and l["heure_debut"] == a["debut"].isoformat())
            check("fin", l and l["heure_fin"] == (a["fin"].isoformat() if a["fin"] else None),
                  f"{l and l['heure_fin']}")
            check("couleur attendue",
                  l and (l["statut_source"] == "VALIDÉ") == a["noir"])
        if plaque == "5316TBU":
            ts = g["trajets"]
            # Réalignement v1.31 (§0undecies E1, 24/08/2026 — prime sur AM-2
            # pour les arrêts < 20 min) : les 4 premiers fragments (ruptures
            # 7:45 / 2:29 / 5:15 < 20 min) affichent UNE ligne fusionnée ; la
            # vraie pause de 43 min reste affichée après elle (E3 : ≥ 30 min).
            check("DEUX lignes affichées (E1 v1.31 : 4 fragments fusionnés + "
                  "le trajet après la vraie pause)", len(ts) == 2, f"{len(ts)}")
            if len(ts) == 2:
                l1, l5 = ts[0], ts[1]
                check("L1 fusionnée noire 03:05:34 → 06:48:14 "
                      "(37,64+4,015+48,68+4,386 = 94,721 km, segments 4)",
                      l1["heure_debut"] == h(3, 5, 34).isoformat()
                      and l1["heure_fin"] == h(6, 48, 14).isoformat()
                      and l1["statut_source"] == "VALIDÉ"
                      and abs((l1["distance_km"] or 0) - 94.721) < 0.01
                      and l1["segments"] == 4,
                      f"{l1['heure_debut']}→{l1['heure_fin']} {l1['distance_km']}")
                check("pause réelle 43 min AFFICHÉE après la ligne fusionnée "
                      "(E3 : ≥ 30 min)",
                      l1["pause_apres_s"] == int(
                          (h(7, 31, 31) - h(6, 48, 14)).total_seconds()),
                      f"{l1['pause_apres_s']}")
                check("L2 noire ou orange selon l'âge de sa fin",
                      l5["statut_source"] in ("PROVISOIRE", "VALIDÉ"))
        if plaque == "9856TCD":
            ts = g["trajets"]
            debuts = [t["heure_debut"] for t in ts]
            check("pas de ligne « manœuvre 03:04 » (0 km, isolée)",
                  h(3, 4, 53).isoformat() not in debuts, debuts)
            l1 = ts[0] if ts else None
            check("L1 = 04:41 → 04:51 noire, pause RÉELLE 1h17:04 "
                  "(§7 : à travers les manœuvres ignorées)",
                  l1 and l1["heure_debut"] == h(4, 41, 4).isoformat()
                  and l1["heure_fin"] == h(4, 51, 12).isoformat()
                  and l1["pause_apres_s"] == int((h(6, 8, 16) - h(4, 51, 12)).total_seconds()),
                  f"{l1}")
            check("L2 = 06:08:16 → 06:15:16 (fin = PREMIER arrêt réel §5.1 — "
                  "les manœuvres de fin ne prolongent plus)",
                  len(ts) >= 2 and ts[1]["heure_debut"] == h(6, 8, 16).isoformat()
                  and ts[1]["heure_fin"] == h(6, 15, 16).isoformat(),
                  f"{ts[1] if len(ts) > 1 else None}")
            check("pas de ligne 07:20 (0,071 km isolée, manœuvre)",
                  all(t["heure_debut"] != h(7, 20, 52).isoformat() for t in ts))
except BaseException as _exc:   # AUCUNE exception n'est masquée : ni import,
    # ni exécution, ni assertion. Un test interrompu n'est PAS un test vert.
    print(f"\n=== ABANDON : {type(_exc).__name__}: {_exc} ===",
          file=sys.stderr)
    print("=== AUCUN verdict pour cette suite : contrôles non exécutés ===",
          file=sys.stderr)
    raise                        # traceback + code de sortie NON NUL
finally:
    db.close()
    if os.path.exists("/tmp/e2e_v113.db"):
        os.remove("/tmp/e2e_v113.db")

print(f"\n=== RÉSULTAT E2E RÉEL : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
