# -*- coding: utf-8 -*-
"""v1.50 — DÉTECTEUR D'APPELS MORTS (dérive appelant ↔ fonction).

POURQUOI CET OUTIL. La plateforme avale ses erreurs de démarrage : un appel dont
la signature a dérivé lève un `TypeError`… attrapé par un `except Exception`
qui le journalise en WARNING et continue. Le produit tourne, donc personne ne
voit que la fonctionnalité est morte.

Cas réellement constaté (17/09/2026, au démarrage de la plateforme) :

    app/main.py:610   importer_infractions_ymane(db, debut=jour_cible, fin=…)
    app/ymane_import.py:115  def importer_infractions_ymane(db, items, …)

→ `TypeError: unexpected keyword argument 'debut'`, journalisé en WARNING :
le rattrapage Ym@ne au démarrage n'importe PLUS RIEN depuis sa réécriture.

CE QU'IL VÉRIFIE. Pour chaque appel `f(...)` (ou `module.f(...)`) où `f` est
importé d'un module du projet, il compare les arguments nommés passés avec la
signature RÉELLE de la fonction : tout nom absent (et sans `**kwargs`) est un
appel mort — l'erreur est certaine, pas une heuristique.

Ce qu'il ne fait pas (assumé, pour rester sans faux positifs) : les appels par
attribut dynamique (`getattr`), les fonctions importées sous alias complexe, et
les erreurs de POSITION/ARITÉ (un argument manquant peut être légitime si la
fonction a des défauts). Le périmètre est volontairement étroit : chaque
signalement doit être un vrai défaut.

Exécution :
    cd backend && python3 verifier_appels_morts.py            # rapport
    cd backend && python3 verifier_appels_morts.py --strict   # rc=1 si un mort
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


class Fonction:
    __slots__ = ("module", "nom", "params", "kwonly", "var_kw", "ligne")

    def __init__(self, module, nom, params, kwonly, var_kw, ligne):
        self.module, self.nom = module, nom
        self.params, self.kwonly = params, kwonly
        self.var_kw, self.ligne = var_kw, ligne

    @property
    def accepte(self) -> set[str]:
        return set(self.params) | set(self.kwonly)


def indexer(chemin: Path, module: str) -> dict[str, Fonction]:
    """Fonctions de premier niveau du module (y compris async)."""
    arbre = ast.parse(chemin.read_text(encoding="utf-8", errors="replace"))
    trouve: dict[str, Fonction] = {}
    for noeud in arbre.body:
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = noeud.args
            params = [p.arg for p in (a.posonlyargs + a.args)]
            kwonly = [p.arg for p in a.kwonlyargs]
            trouve[noeud.name] = Fonction(module, noeud.name, params, kwonly,
                                          a.kwarg is not None, noeud.lineno)
    return trouve


def main() -> int:
    ap = argparse.ArgumentParser(description="Appels morts (dérive de signature)")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--dossier", default=None, help="racine à analyser (défaut : ./app)")
    args = ap.parse_args()

    racine = Path(__file__).resolve().parent
    cible = Path(args.dossier) if args.dossier else racine / "app"
    fichiers = sorted(cible.rglob("*.py"))
    if not fichiers:
        print(f"⛔ aucun fichier Python dans {cible}")
        return 2

    # --- index du projet : nom de fonction → définitions candidates
    definitions: dict[str, list[Fonction]] = {}
    for f in fichiers:
        module = f.stem
        for nom, fn in indexer(f, module).items():
            definitions.setdefault(nom, []).append(fn)

    morts: list[tuple[str, int, str, str, str]] = []
    for f in fichiers:
        source = f.read_text(encoding="utf-8", errors="replace")
        arbre = ast.parse(source)
        # imports locaux : nom utilisé → module d'origine
        origine: dict[str, str] = {}
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.ImportFrom):
                mod = (noeud.module or "").split(".")[-1]
                for alias in noeud.names:
                    origine[alias.asname or alias.name] = mod
            elif isinstance(noeud, ast.Import):
                for alias in noeud.names:
                    origine[(alias.asname or alias.name).split(".")[-1]] = \
                        (alias.asname or alias.name).split(".")[-1]
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Call):
                continue
            appel: str | None = None
            if isinstance(noeud.func, ast.Name) and noeud.func.id in definitions:
                appel = noeud.func.id
            elif (isinstance(noeud.func, ast.Attribute)
                  and isinstance(noeud.func.value, ast.Name)
                  # `module.f(...)` avec module IMPORTÉ, jamais `db.refresh(...)`
                  # ni `self.helper(...)` : sinon on confondrait une méthode de
                  # bibliothèque (SQLAlchemy, FastAPI) avec une fonction du projet.
                  and noeud.func.value.id in origine
                  and noeud.func.attr in definitions):
                appel = noeud.func.attr
            if appel is None:
                continue
            cands = definitions[appel]
            mod_attendu = origine.get(appel)
            if mod_attendu:
                precis = [c for c in cands if c.module == mod_attendu]
                if precis:
                    cands = precis
            for mot in noeud.keywords:
                if mot.arg is None:                     # **dictionnaire
                    continue
                if all(any(c.var_kw or mot.arg in c.accepte for c in cands)
                       for c in cands[:1]) and \
                   any(mot.arg in c.accepte or c.var_kw for c in cands):
                    continue
                if not any(mot.arg in c.accepte or c.var_kw for c in cands):
                    defin = cands[0]
                    attendus = ", ".join(sorted(defin.accepte)) or "(aucun)"
                    morts.append((f.name, noeud.lineno, appel, mot.arg, attendus))

    print("=" * 78)
    print("DÉTECTEUR D'APPELS MORTS — dérive appelant ↔ fonction (projet)")
    print("=" * 78)
    print(f"Périmètre : {len(fichiers)} fichiers, {len(definitions)} noms de fonctions")
    if not morts:
        print("\n✅ AUCUN appel mort : chaque argument nommé correspond à une "
              "signature réelle.")
        return 0

    print(f"\n❌ {len(morts)} APPEL(S) MORT(S) — `TypeError` garanti à l'exécution,"
          f"\n   silencieusement avalé si le site d'appel est sous `except "
          f"Exception` :\n")
    for nom_f, ligne, fn, mot, attendus in sorted(morts):
        print(f"  · {nom_f}:{ligne} → {fn}(…, {mot}=…)"
              f"\n      signature réelle : ({attendus})")
    print("\nPRIORITÉ : les appels situés dans un chemin de DÉMARRAGE ou une tâche "
          "de fond\n(sinon la fonctionnalité est morte sans alerte rouge).")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
