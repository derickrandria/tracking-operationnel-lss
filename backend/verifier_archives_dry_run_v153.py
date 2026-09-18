# -*- coding: utf-8 -*-
"""DRY-RUN — AUDIT DES ARCHIVES EXISTANTES (v1.53, 18/09/2026).

Outil **STRICTEMENT EN LECTURE SEULE** : il ne réécrit AUCUNE archive, ne
modifie AUCUNE ligne, n'ouvre aucune transaction d'écriture. Il se contente de
DIRE ce qu'il faudrait réparer — la réparation elle-même reste une décision
humaine (validation explicite exigée avant toute réécriture d'archive).

Ce qu'il cherche (les « anciennes valeurs fausses ») :
  [1] COMPTEURS ABSENTS      archive écrite avant v1.53 (pas de
                             `nb_trajets_valides_reels` / `nb_sequences_affichees`
                             / `nb_trajets_fusionnes`) ;
  [2] TRAJETS FUSIONNÉS EN BASE  le snapshot ne contient que la vue d'affichage
                             (clé `segments` > 1) : les trajets valides ne sont
                             plus individuellement présents dans l'archive ;
  [3] NB_TRAJETS INCOHÉRENT  `nb_trajets` ne correspond pas au nombre de
                             séquences affichées (ancien double sens du champ) ;
  [4] TCC DÉTRUIT            `tcc_s == 0` alors que la journée a roulé
                             (`tcj_s > 0`) : valeur calculée écrasée par
                             l'ancien masquage N1 / le bug d'archivage v1.52 ;
  [5] VALEUR HORS JOURNÉE    TCJ ou TTJ > 86 400 s (donnée anormale) ;
  [6] TTJ < TCJ              incohérence arithmétique (TTJ = TCJ + arrêts).

Usage :
  cd backend
  DATABASE_URL="sqlite:////tmp/ma_base.db" python verifier_archives_dry_run_v153.py
  DATABASE_URL="sqlite:////tmp/ma_base.db" python verifier_archives_dry_run_v153.py \
      --json /tmp/rapport_archives.json --limite 50

⚠ Il n'existe VOLONTAIREMENT aucune option de réparation dans cet outil.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select                                    # noqa: E402

from app.database import SessionLocal                            # noqa: E402
from app.models import (HistoriqueJournalier, SuiviJournalier,   # noqa: E402
                        Vehicule)
from app.serializers import (ARCHIVE_SCHEMA_VERSION,            # noqa: E402
                             compter_trajets_reels, s_historique)

CLES_V153 = ("nb_trajets_valides_reels", "nb_sequences_affichees",
             "nb_trajets_fusionnes")


def diagnostiquer(donnees: dict) -> tuple[list[str], list[str]]:
    """(anomalies certaines, points à CONFIRMER) d'UNE archive.

    Aucune écriture, aucune mutation. Distinction essentielle :
    `tcc_s == 0` sur une journée CLOSE n'est PAS une anomalie — la nuit est une
    interruption ≥ 30 min, donc le TCC d'une journée terminée vaut normalement
    zéro (règle H1). Seule une comparaison avec un RECALCUL permet de dire si la
    valeur stockée a été détruite ; ces cas sont donc classés « à confirmer »."""
    d = donnees or {}
    trajets = d.get("trajets") or []
    anomalies = []
    a_confirmer = []

    if not all(c in d for c in CLES_V153):
        anomalies.append("COMPTEURS_ABSENTS")
    # v1.54/P3 — une archive sans VERSION de schéma est antérieure : elle doit
    # être signalée « à recalculer » (jamais lue comme si ses champs absents
    # valaient zéro, jamais réécrite sans validation explicite).
    if int(d.get("schema_version") or 0) < ARCHIVE_SCHEMA_VERSION:
        anomalies.append("SCHEMA_ANTERIEUR")
    if any(int(t.get("segments") or 1) > 1 for t in trajets):
        anomalies.append("TRAJETS_FUSIONNES_EN_BASE")

    nb = int(d.get("nb_trajets") or 0)
    seq = d.get("nb_sequences_affichees")
    if seq is not None and int(seq) != nb:
        anomalies.append("NB_TRAJETS_INCOHERENT")
    elif seq is None and trajets and nb != len(trajets):
        # archive ancienne : `nb_trajets` devrait valoir le nombre de lignes
        anomalies.append("NB_TRAJETS_INCOHERENT")
    if trajets and nb and seq is not None and compter_trajets_reels(trajets) != int(
            d.get("nb_trajets_valides_reels") or 0):
        anomalies.append("NB_TRAJETS_INCOHERENT")

    tcj = int(d.get("tcj_s") or d.get("tcj_secondes") or 0)
    ttj = int(d.get("ttj_s") or d.get("ttj_secondes") or 0)
    if "tcc_s" not in d and "tcc_secondes" not in d and tcj > 0:
        anomalies.append("TCC_ABSENT")                 # clé manquante = certain
    elif int(d.get("tcc_s") or d.get("tcc_secondes") or 0) == 0 and tcj > 0:
        a_confirmer.append("TCC_NUL_JOURNEE_ROULEE")  # normal SI journée close
    if tcj > 86400 or ttj > 86400:
        anomalies.append("VALEUR_HORS_JOURNEE")
    if ttj and tcj and ttj < tcj:
        anomalies.append("TTJ_INFERIEUR_A_TCJ")
    return anomalies, a_confirmer


def comparer_au_calcul(db, h: HistoriqueJournalier) -> list[str]:
    """Recalcule la journée depuis les TRAJETS EN BASE (fonctions pures :
    `construire_journee` / `fusionner_trajets_affichage`) et compare aux valeurs
    stockées. Ne modifie rien — c'est la seule façon de démontrer qu'une valeur
    a été détruite plutôt que d'être légitimement nulle."""
    from app.chaines import Segment, construire_journee
    from app.engine import get_seuils
    from app.models import Trajet, StatutValidationTrajet

    trajets = db.scalars(select(Trajet).where(
        Trajet.suivi_id.in_(select(SuiviJournalier.id).where(
            SuiviJournalier.vehicule_id == h.vehicule_id,
            SuiviJournalier.date_jour == h.date_jour)))).all()
    if not trajets:
        return ["NON_VERIFIABLE"]
    seuils = get_seuils(db)
    fin_jour = datetime.combine(h.date_jour, datetime.max.time().replace(microsecond=0))
    segs = [Segment(debut=t.heure_debut, fin=t.heure_fin, distance_km=t.distance_km,
                    rejete=(t.statut_validation == StatutValidationTrajet.REJETE))
            for t in trajets if t.heure_debut is not None]
    journee = construire_journee(
        segs, maintenant=fin_jour, date_jour=h.date_jour,
        pause_min=float(seuils.get("DUREE_MIN_PAUSE_VALIDE", 1200)),
        seuil_km=float(seuils.get("SEUIL_DISTANCE_MIN_TRAJET_KM", 0.3)),
        pause_affichee_min=float(seuils.get("SEUIL_AFFICHAGE_PAUSE_MIN", 1800)))
    d = h.donnees or {}
    ecarts = []
    if int(d.get("tcj_s") or 0) != int(journee.tcj_s):
        ecarts.append("ECART_CALCUL")
    if int(d.get("ttj_s") or 0) != int(journee.ttj_s):
        ecarts.append("ECART_CALCUL")
    return ecarts


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit DRY-RUN des archives (lecture seule).")
    ap.add_argument("--json", dest="chemin_json", default=None,
                    help="écrit le rapport JSON à ce chemin (hors base de données)")
    ap.add_argument("--limite", type=int, default=0,
                    help="ne détailler que N archives (0 = toutes)")
    ap.add_argument("--annee", type=int, default=0, help="filtre sur une année")
    ap.add_argument("--comparer-calcul", action="store_true",
                    help="recalcule chaque journée depuis les trajets en base "
                         "(fonction PURE, aucune écriture) et compare au stocké")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL", "sqlite:///./lss.db")
    prod = not url.startswith("sqlite:////tmp/")
    print("=" * 78)
    print("  AUDIT DRY-RUN DES ARCHIVES — v1.53 — LECTURE SEULE")
    print(f"  Base : {url}")
    if prod:
        print("  ⚠ BASE HORS /tmp : cet outil ne fait QUE LIRE, mais vérifiez le chemin")
    print("  Aucune écriture n'est possible depuis cet outil (il n'existe pas de")
    print("  mode réparation : toute réécriture d'archive demande une validation).")
    print("=" * 78)

    db = SessionLocal()
    rapport = {"base": url, "total_archives": 0, "anomalies": {},
               "archives_a_reparer": [], "aucune_ecriture": True}
    try:
        q = select(HistoriqueJournalier)
        if args.annee:
            q = q.where(HistoriqueJournalier.annee == args.annee)
        archives = list(db.scalars(q.order_by(HistoriqueJournalier.date_jour)).all())
        rapport["total_archives"] = len(archives)

        compteurs = {}
        compteurs_conf = {}
        a_reparer = []
        for h in archives:
            anomalies, a_confirmer = diagnostiquer(h.donnees or {})
            if args.comparer_calcul:
                ecarts = comparer_au_calcul(db, h)
                anomalies = anomalies + ecarts
            if not anomalies and not a_confirmer:
                continue
            for a in anomalies:
                compteurs[a] = compteurs.get(a, 0) + 1
            for a in a_confirmer:
                compteurs_conf[a] = compteurs_conf.get(a, 0) + 1
            plaque = None
            v = db.get(Vehicule, h.vehicule_id)
            plaque = v.plaque if v else h.vehicule_id
            # v1.54/P3 — lecture CONTRAT (rétrocompatible) : dit ce que le
            # logiciel sert réellement pour cette archive, et si elle doit être
            # recalculée. Lecture pure : rien n'est écrit.
            try:
                _lu = s_historique(h, seuils=get_seuils(db))
            except Exception as exc:                     # noqa: BLE001
                _lu = {"compteurs_source": f"ERREUR_LECTURE:{type(exc).__name__}",
                       "a_recalculer": True}
            a_reparer.append({
                "archive_id": h.id,
                "date_jour": h.date_jour.isoformat(),
                "plaque": plaque,
                "anomalies": anomalies,
                "a_confirmer": a_confirmer,
                "schema_version": int((h.donnees or {}).get("schema_version") or 0),
                "compteurs_source": _lu.get("compteurs_source"),
                "a_recalculer": bool(_lu.get("a_recalculer")),
                "nb_trajets_valides_reels": _lu.get("nb_trajets_valides_reels"),
                "nb_sequences_affichees": _lu.get("nb_sequences_affichees"),
                "nb_trajets_stocke": (h.donnees or {}).get("nb_trajets"),
                "tcc_s_stocke": (h.donnees or {}).get("tcc_s"),
                "tcj_s_stocke": (h.donnees or {}).get("tcj_s"),
                "lignes_stockees": len((h.donnees or {}).get("trajets") or []),
            })
        rapport["anomalies"] = compteurs
        rapport["a_confirmer"] = compteurs_conf
        rapport["archives_a_reparer"] = a_reparer
        rapport["archives_a_recalculer"] = sum(
            1 for a in a_reparer if a.get("a_recalculer"))
        rapport["schema_courant"] = ARCHIVE_SCHEMA_VERSION
        rapport["conseil"] = ("Aucun mode apply automatique : toute reprise "
                              "d'archive exige une validation humaine explicite.")

        print(f"\n  Archives examinées : {len(archives)}")
        print(f"  Archives conforme  : {len(archives) - len(a_reparer)}")
        print(f"  Archives à réparer : {len(a_reparer)}")
        if compteurs:
            print("\n  ── Anomalies par type ──")
            libelles = {
                "COMPTEURS_ABSENTS":
                    "archive antérieure à v1.53 (compteurs distincts absents) "
                    "→ réparable par un recalcul encadré",
                "SCHEMA_ANTERIEUR":
                    "archive sans version de schéma (antérieure à la v1.54) "
                    "→ statut « à recalculer » ; ses champs absents ne sont "
                    "JAMAIS lus comme zéro",
                "TRAJETS_FUSIONNES_EN_BASE":
                    "le snapshot ne garde que la vue fusionnée "
                    "→ les trajets individuels doivent être réécrits (validation requise)",
                "NB_TRAJETS_INCOHERENT":
                    "compteur de trajets incohérent (ancien double sens) "
                    "→ recalcul",
                "TCC_DETRUIT":
                    "TCC remis à 0 alors que la journée a roulé "
                    "→ valeur calculée perdue : recalcul depuis les trajets",
                "VALEUR_HORS_JOURNEE":
                    "durée > 24 h : donnée anormale à vérifier à la main",
                "TTJ_INFERIEUR_A_TCJ":
                    "TTJ < TCJ : incohérence arithmétique à vérifier à la main",
                "TCC_ABSENT":
                    "clé TCC absente alors que la journée a roulé "
                    "→ valeur jamais écrite (archive ancienne)",
                "ECART_CALCUL":
                    "la valeur stockée DIFFÈRE du recalcul depuis les trajets "
                    "→ archive à régénérer (validation requise)",
                "NON_VERIFIABLE":
                    "aucun trajet en base pour recalculer → archive de "
                    "démonstration ou trajets purgés : contrôle manuel",
            }
            for cle, n in sorted(compteurs.items(), key=lambda kv: -kv[1]):
                print(f"    {n:>5}  {cle}")
                print(f"           {libelles.get(cle, '')}")
        print(f"\n  ── STATUT « À RECALCULER » ──")
        print(f"    {rapport['archives_a_recalculer']:>5}  archive(s) à recalculer "
              f"(version de schéma courante : {ARCHIVE_SCHEMA_VERSION})")
        print("           Aucun mode apply automatique : rien n'est réécrit ici.")
        if compteurs_conf:
            print("\n  ── POINTS À CONFIRMER (pas des anomalies certaines) ──")
            for cle, n in sorted(compteurs_conf.items(), key=lambda kv: -kv[1]):
                if cle == "TCC_NUL_JOURNEE_ROULEE":
                    print(f"    {n:>5}  {cle}")
                    print("           TCC = 0 alors que la journée a roulé : sur une "
                          "journée CLOSE c'est\n           NORMAL (la nuit est une "
                          "interruption ≥ 30 min) ; l'anomalie ne se\n           "
                          "démontre qu'en comparant au recalcul "
                          "(--comparer-calcul).")
                else:
                    print(f"    {n:>5}  {cle}")

        limite = args.limite or len(a_reparer)
        if a_reparer:
            print(f"\n  ── Détail (max {limite}) ──")
            for item in a_reparer[:limite]:
                print(f"    {item['date_jour']}  {item['plaque']:<10} "
                      f"lignes={item['lignes_stockees']:<3} nb_trajets="
                      f"{item['nb_trajets_stocke']}  tcc={item['tcc_s_stocke']}  "
                      f"tcj={item['tcj_s_stocke']}  → {', '.join(item['anomalies'])}")

        print("\n" + "=" * 78)
        print("  AUCUNE ÉCRITURE EFFECTUÉE — rapport de diagnostic uniquement.")
        print("  Prochaine étape (hors de cet outil) : validation humaine explicite,")
        print("  sauvegarde de la base, puis recalcul encadré archive par archive.")
        print("=" * 78)

        if args.chemin_json:
            chemin = Path(args.chemin_json)
            chemin.parent.mkdir(parents=True, exist_ok=True)
            chemin.write_text(json.dumps(rapport, indent=2, ensure_ascii=False),
                              encoding="utf-8")
            print(f"  Rapport JSON écrit (hors base) : {chemin}")
        return 0
    finally:
        db.rollback()          # aucune transaction laissée ouverte, aucune écriture
        db.close()


if __name__ == "__main__":
    sys.exit(main())
