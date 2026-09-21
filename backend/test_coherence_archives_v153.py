# -*- coding: utf-8 -*-
"""Tests v1.53 — COHÉRENCE DES ARCHIVES : les quatre chemins doivent produire
le MÊME snapshot sur les mêmes données (exigence du 18/09/2026, point 3).

Chemins comparés :
  [C] RATTRAPAGE   `rattrapage.traiter_jour` (ex. `rattraper_consolidation`) —
                   jour reconstruit depuis les portails, puis archivé ;
  [B] RECALCUL     `daily.recalculer_archives_journee` — reconstruction depuis
                   les trajets en base ;
  [A] RESYNC       `reconciliation._synchroniser_archive` — archive existante
                   resynchronisée avec l'état validé ;
  [D] LECTURE      `serializers.s_historique` — ce que voit l'utilisateur.
  [E] RÉFÉRENCE    `chaines.construire_journee` — la source unique du calcul.

Le test vérifie aussi que les compteurs v1.53 (`nb_trajets_valides_reels`,
`nb_sequences_affichees`, `nb_trajets_fusionnes`) sont DISTINCTS et cohérents
sur tous les chemins — et qu'aucun compteur de séquences n'est présenté comme
un compteur de trajets réels.

Exécution :
  cd backend
  DATABASE_URL="sqlite:////tmp/test_coherence_v153.db" python test_coherence_archives_v153.py
"""
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

os.environ.setdefault("SIM_ENABLE", "0")

_URL = os.environ.get("DATABASE_URL", "")
if not _URL.startswith("sqlite:////tmp/"):
    print("⛔ Sécurité : DATABASE_URL doit être fourni et viser /tmp "
          f"(reçu : {_URL or 'non défini'})")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import func, select                                  # noqa: E402

from app.chaines import Segment, construire_journee                  # noqa: E402
from app.database import SessionLocal                                # noqa: E402
from app.engine import ensure_suivi, get_seuils                      # noqa: E402
from app.main import migrer_schema                                   # noqa: E402
from app.models import (HistoriqueJournalier, Trajet, Vehicule)      # noqa: E402
from app.seed import seed_si_vide                                    # noqa: E402
from app.serializers import s_historique, journee_suivi              # noqa: E402

OK = KO = 0


def check(label, cond, detail=""):
    global OK, KO
    if cond:
        OK += 1
        print(f"  ✅ {label}")
    else:
        KO += 1
        print(f"  ❌ {label}   {detail}")


print("=" * 74)
print("  LSS v1.53 — COHÉRENCE DES QUATRE CHEMINS D'ARCHIVAGE")
print(f"  Base : {_URL}")
print("=" * 74)

seed_si_vide()
migrer_schema()
db = SessionLocal()
try:
    for modele in (Trajet, HistoriqueJournalier):
        db.query(modele).delete()
    db.commit()

    v = db.scalars(select(Vehicule).order_by(Vehicule.plaque).limit(1)).first()
    J = date(2026, 9, 4)

    def dt(h, m=0, s=0):
        return datetime(J.year, J.month, J.day, h, m, s)

    # Données de référence — 3 trajets valides :
    #   06:00 → 08:00 (115 km) · 08:05 → 08:15 (0,520 km) · 08:50 → 23:55 (75 km)
    #   ruptures : 5 min (fusionnée) puis 35 min (clôture) → 2 séquences
    REF = [(dt(6, 0), dt(8, 0), 115.0),
           (dt(8, 5), dt(8, 15), 0.520),
           (dt(8, 50), dt(23, 55), 75.0)]

    # -------------------------------------------------------------------------
    print("\n[C] RATTRAPAGE — `rattrapage.traiter_jour` (lecture portail simulée)")
    import app.rattrapage as rat
    from app.rattrapage import LectureJour, LectureSource

    def lecteur_fixe(jour):
        items = [{"plaque": v.plaque, "debut": d0, "fin": d1,
                  "distance_km": km, "conducteur": "TEST Coherence",
                  "source": "CAMTRACKPRO"} for d0, d1, km in REF]
        return LectureJour(jour, {
            "MZONEX": LectureSource("MZONEX", rat.DISPONIBLE, items=items),
            "CAMTRACKPRO": LectureSource("CAMTRACKPRO", rat.DISPONIBLE, items=[]),
            "YMANE": LectureSource("YMANE", rat.DISPONIBLE)})

    sauve = rat.lecture_reelle
    rat.lecture_reelle = lecteur_fixe
    try:
        res = rat.traiter_jour(J, lecteur=lecteur_fixe, motif="test_coherence_v153")
    finally:
        rat.lecture_reelle = sauve
    print(f"     statut rattrapage : {res.get('statut')}")
    db.expire_all()
    h = db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == J,
        HistoriqueJournalier.vehicule_id == v.id))
    check("[C] le rattrapage a produit UNE archive pour la journée", h is not None,
          f"statut={res.get('statut')}")
    check("[C] le rattrapage a écrit les 3 trajets valides en base",
          db.scalar(select(func.count(Trajet.id))) == 3,
          f"{db.scalar(select(func.count(Trajet.id)))}")
    snap_C = dict(h.donnees or {})

    # -------------------------------------------------------------------------
    print("\n[B] RECALCUL — `daily.recalculer_archives_journee`")
    from app.daily import recalculer_archives_journee
    recalculer_archives_journee(J, db=db, rattraper_portail=False,
                                autoriser_reecriture=True, motif="test_coherence_v153")
    db.expire_all()
    h = db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == J,
        HistoriqueJournalier.vehicule_id == v.id))
    snap_B = dict(h.donnees or {})

    # -------------------------------------------------------------------------
    print("\n[A] RESYNC — `reconciliation._synchroniser_archive`")
    from app.reconciliation import _synchroniser_archive
    suivi = ensure_suivi(db, v, J)
    _synchroniser_archive(db, suivi)
    db.expire_all()
    h = db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == J,
        HistoriqueJournalier.vehicule_id == v.id))
    db.refresh(h)
    snap_A = dict(h.donnees or {})

    # -------------------------------------------------------------------------
    print("\n[E] RÉFÉRENCE — `chaines.construire_journee` (source unique du calcul)")
    segs = [Segment(debut=t.heure_debut, fin=t.heure_fin,
                    distance_km=t.distance_km, rejete=False)
            for t in db.scalars(select(Trajet).order_by(Trajet.numero)).all()]
    ref_journee = construire_journee(segs, maintenant=dt(23, 59, 59), date_jour=J,
                                     pause_min=1200, seuil_km=0.3,
                                     pause_affichee_min=1800)
    print(f"     référence : TCJ={ref_journee.tcj_s} TTJ={ref_journee.ttj_s} "
          f"lignes={len(ref_journee.lignes)}")

    # -------------------------------------------------------------------------
    print("\n[1] LES QUATRE CHEMINS PRODUISENT LE MÊME SNAPSHOT (données identiques)")

    def payload(donnees: dict) -> dict:
        tr = donnees.get("trajets") or []
        return {
            "tcj_s": int(donnees.get("tcj_s") or 0),
            "ttj_s": int(donnees.get("ttj_s") or 0),
            "tcc_s": int(donnees.get("tcc_s") or 0),
            "nb_trajets_valides_reels": int(donnees.get("nb_trajets_valides_reels") or 0),
            "nb_sequences_affichees": int(donnees.get("nb_sequences_affichees") or 0),
            "nb_trajets": int(donnees.get("nb_trajets") or 0),
            "nb_trajets_fusionnes": int(donnees.get("nb_trajets_fusionnes") or 0),
            "lignes_brutes": len(tr),
            "bornes": [(t.get("heure_debut"), t.get("heure_fin")) for t in tr],
        }

    pA, pB, pC = payload(snap_A), payload(snap_B), payload(snap_C)
    check("[C] = [B] (rattrapage = recalcul) : mêmes compteurs et mêmes trajets bruts",
          pC == pB, f"\n     C={pC}\n     B={pB}" if pC != pB else "")
    check("[B] = [A] (recalcul = resync) : mêmes compteurs et mêmes trajets bruts",
          pB == pA, f"\n     B={pB}\n     A={pA}" if pB != pA else "")
    check("[C] = [A] : les trois chemins d'écriture sont convergents", pC == pA)

    check("les 3 trajets valides sont conservés INDIVIDUELLEMENT en base "
          "(aucune fusion stockée) sur les trois chemins",
          pA["lignes_brutes"] == pB["lignes_brutes"] == pC["lignes_brutes"] == 3,
          f"{pA['lignes_brutes']}/{pB['lignes_brutes']}/{pC['lignes_brutes']}")

    check("[E] la référence `construire_journee` donne les mêmes TCJ / TTJ "
          "que les archives",
          ref_journee.tcj_s == pA["tcj_s"] == pB["tcj_s"] == pC["tcj_s"]
          and ref_journee.ttj_s == pA["ttj_s"] == pB["ttj_s"] == pC["ttj_s"],
          f"ref={ref_journee.tcj_s}/{ref_journee.ttj_s} "
          f"archives={pA['tcj_s']}/{pA['ttj_s']}")

    # -------------------------------------------------------------------------
    print("\n[2] COMPTEURS DISTINCTS (exigence « nb_trajets n'a qu'un seul sens »)")
    for nom, p in (("A", pA), ("B", pB), ("C", pC)):
        check(f"[{nom}] 3 trajets valides réels / 2 séquences / 1 regroupé",
              p["nb_trajets_valides_reels"] == 3 and p["nb_sequences_affichees"] == 2
              and p["nb_trajets_fusionnes"] == 1 and p["nb_trajets"] == 2,
              f"{p['nb_trajets_valides_reels']}/{p['nb_sequences_affichees']}"
              f"/{p['nb_trajets_fusionnes']}/{p['nb_trajets']}")
        check(f"[{nom}] `nb_trajets` == `nb_sequences_affichees` (un seul sens) "
              f"et réel = séquences + regroupés",
              p["nb_trajets"] == p["nb_sequences_affichees"]
              and p["nb_trajets_valides_reels"]
              == p["nb_sequences_affichees"] + p["nb_trajets_fusionnes"])
    check("aucune archive ne porte un compteur de séquences dans le champ "
          "« trajets réels » : les deux valeurs sont présentes ET différentes",
          all(p["nb_trajets"] != p["nb_trajets_valides_reels"] for p in (pA, pB, pC)))

    # -------------------------------------------------------------------------
    print("\n[3] LECTURE — `s_historique` expose la même vérité (chemin [D])")
    seuils = get_seuils(db)
    lu = s_historique(h, detail=True, seuils=seuils)
    check("[D] les compteurs lus égalent ceux des trois écritures",
          int(lu["nb_trajets_valides_reels"]) == 3
          and int(lu["nb_sequences_affichees"]) == 2
          and int(lu["nb_trajets_fusionnes"]) == 1
          and int(lu["nb_trajets"]) == 2,
          f"{lu['nb_trajets']}/{lu['nb_sequences_affichees']}"
          f"/{lu['nb_trajets_valides_reels']}/{lu['nb_trajets_fusionnes']}")
    check("[D] TCJ / TTJ lus égaux aux valeurs archivées",
          int(lu["tcj_s"]) == pA["tcj_s"] and int(lu["ttj_s"]) == pA["ttj_s"],
          f"{lu['tcj_s']}/{lu['ttj_s']} vs {pA['tcj_s']}/{pA['ttj_s']}")
    check("[D] TCC conservé (jamais écrasé par 0) + drapeau d'affichage levé",
          int(lu["tcc_s"]) == pA["tcc_s"] and pA["tcc_s"] > 0
          and lu.get("tcc_masque") is True,
          f"tcc={lu['tcc_s']} masque={lu.get('tcc_masque')}")
    check("[D] les trajets sont présentés fusionnés à la lecture (2 séquences) "
          "sans que la base soit modifiée (3 bruts)",
          len(lu["donnees"]["trajets"]) == 2 and pA["lignes_brutes"] == 3,
          f"lus={len(lu['donnees']['trajets'])} bruts={pA['lignes_brutes']}")

    print("\n" + "=" * 74)
    print(f"  RÉSULTAT : {OK} OK / {KO} KO")
    print("=" * 74)
finally:
    db.close()

for suffixe in ("", "-wal", "-shm"):
    chemin = _URL.replace("sqlite:///", "") + suffixe
    if os.path.exists(chemin):
        shutil.rmtree(chemin, ignore_errors=True) if os.path.isdir(chemin) else os.remove(chemin)

sys.exit(1 if KO else 0)
