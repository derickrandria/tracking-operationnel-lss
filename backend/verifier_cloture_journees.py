# -*- coding: utf-8 -*-
"""v1.48 — VÉRIFICATEUR DE CLÔTURE DU SUIVI JOURNALIER (lecture seule).

Répond à : « mes journées se ferment-elles vraiment ? » Une journée passée
doit être 100 % OFFICIELLE (pièce CA-4 / Addendum v1.8 §4, [R-11] : l'historique
reflète les données réelles CONSOLIDÉES). Ce script mesure, jour par jour :

  1. la GRILLE  (ce que voit l'opérateur et ce qu'un export produit) ;
  2. l'ARCHIVE  (HistoriqueJournalier — ce que lit l'onglet Historique).

et compte les lignes restées PROVISOIRE (orange) — donc non closes.

Usage :
    cd backend
    python3 verifier_cloture_journees.py                # 7 derniers jours
    python3 verifier_cloture_journees.py --jours 15
    DATABASE_URL="sqlite:////tmp/copie.db" python3 verifier_cloture_journees.py

Sortie : un verdict par jour + un verdict global, code retour 1 si une
journée passée n'est pas close.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from app.database import SessionLocal
from app.daily import SEUIL_SPLIT_S  # noqa: F401  (import de cohérence)
from app.engine import get_seuils
from app.models import HistoriqueJournalier, SuiviJournalier
from app.serializers import s_suivi


def mesurer_jour(db, jour: date, seuils: dict) -> dict:
    """Renvoie les compteurs de non-clôture pour un jour passé."""
    r = {"jour": jour, "vehicules": 0, "grille_ok": 0, "grille_prov": 0,
         "archive_lignes": 0, "archive_prov": 0, "archive_presente": False,
         "exemples": []}
    suivis = db.query(SuiviJournalier).filter(
        SuiviJournalier.date_jour == jour).all()
    r["vehicules"] = len(suivis)
    for s in suivis:
        try:
            lignes = s_suivi(s, seuils).get("trajets", [])
        except Exception:                      # jamais casser la mesure
            continue
        for lg in lignes:
            if lg.get("statut_source") == "PROVISOIRE":
                r["grille_prov"] += 1
                if len(r["exemples"]) < 3:
                    r["exemples"].append(
                        (getattr(s.vehicule, "plaque", "?"),
                         (lg.get("heure_debut") or "?")[11:16],
                         (lg.get("heure_fin") or "—")[11:16]))
            else:
                r["grille_ok"] += 1
    archives = db.query(HistoriqueJournalier).filter(
        HistoriqueJournalier.date_jour == jour).all()
    r["archive_presente"] = bool(archives)
    for h in archives:
        for t in ((h.donnees or {}).get("trajets") or []):
            r["archive_lignes"] += 1
            if t.get("statut_source") == "PROVISOIRE":
                r["archive_prov"] += 1
    return r


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Mesure la clôture des journées du suivi journalier")
    ap.add_argument("--jours", type=int, default=7,
                    help="nombre de jours passés à contrôler (défaut 7)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        seuils = get_seuils(db)
    except Exception:
        seuils = {}
    aujourd = date.today()
    print("=" * 78)
    print("CLÔTURE DU SUIVI JOURNALIER — une journée passée doit être 100 % "
          "OFFICIELLE (CA-4 / [R-11])")
    print(f"contrôle au {aujourd:%d/%m/%Y} sur les {args.jours} derniers jours "
          f"écoulés")
    print("=" * 78)
    jours_ko, jours_vides = [], []
    try:
        for offset in range(args.jours, 0, -1):
            jour = aujourd - timedelta(days=offset)
            r = mesurer_jour(db, jour, seuils)
            if r["vehicules"] == 0 and r["archive_lignes"] == 0:
                jours_vides.append(jour)
                print(f"  ·  {jour:%d/%m}  aucune donnée (ni suivi, ni archive)"
                      f" — non mesurable")
                continue
            if r["grille_ok"] + r["grille_prov"] == 0 and r["archive_lignes"] == 0:
                jours_vides.append(jour)
                print(f"  ·  {jour:%d/%m}  {r['vehicules']} camion(s) mais AUCUN "
                      f"trajet (ni à l'écran, ni en archive) — non mesurable "
                      f"(collecte sans données ?)")
                continue
            ko = r["grille_prov"] or r["archive_prov"]
            etat = "❌ NON CLOSE" if ko else "✅ close"
            print(f"  {etat}  {jour:%d/%m}  {r['vehicules']} camion(s) · "
                  f"grille {r['grille_ok']} VALIDÉ / {r['grille_prov']} PROVISOIRE"
                  f" · archive "
                  + (f"{r['archive_lignes']} ligne(s) dont "
                     f"{r['archive_prov']} PROVISOIRE"
                     if r["archive_presente"] else "ABSENTE"))
            for p, d, f in r["exemples"]:
                print(f"        └ ex. {p} : {d} → {f} resté PROVISOIRE")
            if ko:
                jours_ko.append(r)
    finally:
        db.close()

    print("\n" + "=" * 78)
    if jours_ko:
        prov_g = sum(x["grille_prov"] for x in jours_ko)
        prov_a = sum(x["archive_prov"] for x in jours_ko)
        print(f"VERDICT : {len(jours_ko)} journée(s) passée(s) NON CLOSE(S) — "
              f"{prov_g} ligne(s) PROVISOIRE à l'écran, {prov_a} dans les archives.")
        print("Cause mesurée sur la branche (v1.48) : `serializers.journee_suivi`")
        print("évalue le test « journée passée » APRÈS avoir ramené `maintenant`")
        print("à 23:59:59 du jour → la condition devient fausse → la dernière")
        print("ligne de chaque journée n'est jamais officialisée. Correctif :")
        print("évaluer le test AVANT la réécriture (voir "
              "PATCH_v148_journee_officielle.patch).")
        return 1
    if jours_vides and len(jours_vides) == args.jours:
        print("VERDICT : AUCUNE donnée sur la période — rien à conclure "
              "(base vide ou portails non configurés).")
        return 0
    print("VERDICT : toutes les journées mesurées sont CLOSES (0 % PROVISOIRE).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
