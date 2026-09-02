# -*- coding: utf-8 -*-
"""Tests v1.42 — §0octies decies « Mise au point du 29/08/2026 » (correctif L1) :
barres de défilement de l'onglet HISTORIQUE (verticale + horizontale) restaurées.

[H1→H5] Historique : racine SANS hauteur fixe (défilement vertical DE PAGE),
     grille du jour bornée à 72 % d'écran avec défilement interne des deux sens,
     tableau de synthèse borné à 60 % (id.) — plus AUCUNE carte compressée sans
     barre (régression v1.41 : flexbox h-full écrasait les sections empilées) ;
[P1→P4] Paramètres : même correctif par anticipation (gravé dans la loi) ;
[S1→S5] Non-régression L1 : Suivi / Infractions / Véhicules / Conducteurs /
     Conduite conservent le patron « une seule zone de données, défilement
     interne pleine hauteur » ;
[V1→V2] Version 1.42 (backend + bandeau de connexion) ;
[L1→L2] Loi : mise au point gravée AVANT codage (§A.9).

Suite PUREMENT STATIQUE (sources frontend + loi + version) : aucune base
requise, aucun import de l'application. Exécution : python3 test_historique_scroll_v142.py
"""
import os
import sys
from pathlib import Path

R = {"ok": 0, "ko": 0}


def verif(nom: str, cond: bool, detail: str = "") -> None:
    if cond:
        R["ok"] += 1
        print(f"  [OK] {nom}")
    else:
        R["ko"] += 1
        print(f"  [KO] {nom} — {detail}")


RACINE = Path(__file__).resolve().parent.parent
PAGES = RACINE / "frontend" / "src" / "pages"


def lit(p: Path) -> str:
    return p.read_text(encoding="utf-8")


hist = lit(PAGES / "Historique.tsx")
param = lit(PAGES / "Parametres.tsx")
suivi = lit(PAGES / "Suivi.tsx")
infra = lit(PAGES / "Infractions.tsx")
veh = lit(PAGES / "Vehicules.tsx")
cond = lit(PAGES / "Conducteurs.tsx")
conduite = lit(PAGES / "Conduite.tsx")
mainpy = lit(RACINE / "backend" / "app" / "main.py")
login = lit(PAGES / "Login.tsx")
loi = lit(RACINE / "REFERENCE_IA_REGLES.md")

print("== [H] Historique — correctif v1.42 ==")
verif("H1 racine sans hauteur fixe (défilement DE PAGE)",
      'className="flex flex-col gap-4"' in hist and "flex h-full flex-col gap-4" not in hist)
verif("H2 commentaire « mise au point v1.42 » présent",
      "mise au point v1.42" in hist)
verif("H3 grille du jour : zone bornée 72vh + défilement 2 sens + GrilleSuivi rendue",
      'max-h-[72vh] overflow-auto' in hist and "<GrilleSuivi" in hist)
verif("H4 tableau de synthèse : plus AUCUN flex-1 compressible (régression ciblée)",
      "min-h-0 flex-1" not in hist and "flex-1 min-h-0" not in hist)
verif("H5 tableau de synthèse : zone bornée 60vh, défilement des deux sens",
      "max-h-[60vh] overflow-auto" in hist)

print("== [P] Paramètres — même correctif (anticipation, gravé) ==")
verif("P1 racine sans hauteur fixe",
      'className="flex flex-col gap-4"' in param and "flex h-full flex-col gap-4" not in param)
verif("P2 plus aucune carte compressée sans barre",
      "min-h-0 flex-1" not in param and "flex-1 min-h-0" not in param)
verif("P3 comptes utilisateurs bornés à 60vh avec défilement interne",
      "overflow-auto max-h-[60vh]" in param)
verif("P4 journal d'audit borné à 60vh (deux sens)",
      "overflow-x-auto max-h-[60vh] overflow-y-auto" in param)

print("== [S] Non-régression L1 — patron Suivi intact partout ailleurs ==")
verif("S1 Suivi : racine pleine hauteur",
      'className="flex h-full flex-col gap-3"' in suivi)
verif("S2 Suivi : zone de grille en défilement interne",
      "min-h-0 flex-1 overflow-auto" in suivi)
verif("S3 Infractions : patron L1 (racine h-full + contenu overflow)",
      "flex h-full flex-col gap-4" in infra and 'contenuClasse="overflow-auto !p-0"' in infra)
verif("S4 Véhicules / Conducteurs / Conduite : cartes L1 intactes",
      all("min-h-0 flex-1" in s and "flex h-full flex-col gap-4" in s
          for s in (veh, cond, conduite)))
verif("S5 pages sans tableau (Alertes, Missions, Dashboard) : non modifiées v1.42",
      all("v1.42" not in lit(PAGES / n)
          for n in ("Alertes.tsx", "Missions.tsx", "Dashboard.tsx")))

print("== [V] Version ≥ 1.42 ==")
# réalignement v1.43 (version vivante) : la règle L1/scroll reste en vigueur,
# le numéro a avancé normalement
import re as _re
_ver = _re.search(r'APP_VERSION = "1\.(4[2-9]|[5-9]\d)(\.\d+)?"', mainpy)
verif("V1 APP_VERSION backend ≥ 1.42", _ver is not None, mainpy[:120])
verif("V2 bandeau de connexion à jour (v1.42+)",
      _re.search(r"v1\.(4[2-9]|[5-9]\d)", login) is not None, login[:120])

print("== [L] Loi — gravée AVANT codage (§A.9) ==")
verif("L1 mise au point 0octies decies présente",
      "Mise au point du 29/08/2026 (correctif v1.42" in loi)
verif("L2 règle « défilement vertical DE PAGE » (pages multi-sections) gravée",
      "défilement vertical DE PAGE" in loi)

print(f"\n==== {R['ok']} OK / {R['ko']} KO ====")
sys.exit(0 if R["ko"] == 0 else 1)
