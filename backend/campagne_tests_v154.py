# -*- coding: utf-8 -*-
"""CAMPAGNE DE TESTS — classement HONNÊTE (v1.54, 21/09/2026).

Pourquoi cet outil existe : un bilan de campagne ne doit JAMAIS être présenté
comme « vert » quand des suites ne prouvent rien. L'outil classe chaque suite
dans **cinq catégories distinctes** et vérifie la **stabilité** (flaky).

  RÉUSSI        : verdict rendu, zéro contrôle en échec
                  (compteur « N OK / 0 KO » **ou** bilan texte du type
                  « TOUS LES TESTS … PASSENT », « 100% SUCCÈS »)
  ÉCHOUÉ        : verdict rendu, au moins un contrôle en échec
  CRASH         : arrêt brutal (exception) — RIEN n'est prouvé
  NON EXÉCUTÉ   : la suite s'est explicitement abstenue (saut, base absente,
                  réseau indisponible, mauvaise invocation)
  NON CONCLUANT : sortie sans bilan exploitable — RIEN n'est prouvé
  FLAKY         : résultat DIFFÉRENT selon les exécutions (détecté avec --flaky N)

Usage :
  cd backend
  python campagne_tests_v154.py                 # une passe
  python campagne_tests_v154.py --flaky 5       # 5 passes (détection d'instabilité)
  python campagne_tests_v154.py --seulement test_api_v125 test_chaines_v113
  python campagne_tests_v154.py --json /tmp/campagne.json

Aucune écriture en base réelle : chaque suite reçoit une base TEMPORAIRE
(`/tmp/qc_campagne_<suite>.db`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent
PYTHON = sys.executable
TIMEOUT_S = 600

RE_COMPTEUR = re.compile(r"(\d+)\s*OK\s*/\s*(\d+)\s*KO")
RE_BILAN_TEXTE = re.compile(
    r"((TOUS|TOUTES) LES (TESTS|R[ÈE]GLES|CONTR[ÔO]LES)[^\n]*"
    r"(PASSENT|VALID|RÉUSS|REUSS|SUCC[ÈE]S)"
    r"|100\s*%\s*SUCC[ÈE]S"
    r"|ONT RÉUSSI AVEC SUCCÈS"
    r"|TOUT EST (OK|BON))", re.IGNORECASE)
RE_SAUTE = re.compile(r"[Tt]est saut[ée]|test skipped|SKIP\b", re.IGNORECASE)
RE_TRACEBACK = re.compile(r"Traceback \(most recent call last\)")
RE_ERREUR_FIN = re.compile(r"^[A-Za-z_.]*(Error|Exception):", re.MULTILINE)
RE_RESEAU = re.compile(r"ConnectError|TLS/SSL|Name or service not known"
                       r"|Temporary failure in name resolution", re.IGNORECASE)
RE_INVOCATION = re.compile(r"can't open file|No such file or directory: '.*\.py'")
# Cadriciel `unittest` (norme de la bibliothèque standard) : son bilan est
# « Ran N tests in Xs » puis « OK » ou « FAILED (failures=…, errors=…) ».
# Sans ces motifs, une suite unittest PARFAITEMENT verte était comptée
# « sans verdict » — un faux négatif du contrôle qualité.
RE_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
RE_UNITTEST_OK = re.compile(r"^OK( \(.*\))?\s*$", re.MULTILINE)
RE_UNITTEST_KO = re.compile(r"^FAILED \((.*)\)\s*$", re.MULTILINE)

CAT_R = "RÉUSSI"
CAT_E = "ÉCHOUÉ"
CAT_C = "CRASH"
CAT_NX = "NON EXÉCUTÉ"
CAT_NC = "NON CONCLUANT"


def classer(stdout: str, stderr: str, code_retour: int) -> tuple[str, str]:
    """(catégorie, détail) d'UNE exécution — jamais « vert » par défaut.

    IMPORTANT : `stdout` et `stderr` sont analysés SÉPARÉMENT. Plusieurs suites
    éprouvent VOLONTAIREMENT des pannes (repli d'API, fenêtre N1, verrous) et
    écrivent des tracebacks internes sur `stderr` : concaténer les deux flux
    ferait passer ces suites pour des « crashes » alors qu'elles rendent un
    verdict COMPLET sur `stdout`. Un vrai crash = traceback APRÈS le dernier
    bilan, ou aucun bilan du tout.
    """
    compteurs = RE_COMPTEUR.findall(stdout)
    ok = ko = None
    pos_bilan = -1
    if compteurs:
        ok, ko = (int(x) for x in compteurs[-1])
        pos_bilan = stdout.rindex(compteurs[-1][0])
    for m in RE_BILAN_TEXTE.finditer(stdout):
        pos_bilan = max(pos_bilan, m.end())
    # Verdict d'un cadriciel (unittest) : « Ran N tests » + « OK »/« FAILED (…) ».
    # unittest écrit ce bilan sur STDERR par défaut → on le cherche dans les DEUX
    # flux, mais toujours de façon NOMINATIVE : les deux lignes doivent être
    # présentes dans le MÊME flux (un « OK » isolé ne compte jamais comme bilan).
    verdict_unittest = None      # (flux, « Ran N tests », verdict, est_ok)
    for indice_flux, flux in enumerate((stdout, stderr)):
        m_ran = RE_UNITTEST_RAN.search(flux)
        if m_ran is None:
            continue
        m_ok = RE_UNITTEST_OK.search(flux)
        m_ko = RE_UNITTEST_KO.search(flux)
        if m_ok is not None or m_ko is not None:
            verdict_unittest = (indice_flux, m_ran,
                                m_ok if m_ok is not None else m_ko,
                                m_ok is not None)
            break
    if verdict_unittest is not None and verdict_unittest[0] == 0:
        pos_bilan = max(pos_bilan, verdict_unittest[2].end())
    bilan_texte = pos_bilan >= 0 and not compteurs

    # Un vrai crash = traceback APRÈS le dernier bilan, ou AUCUN bilan du tout.
    # Si le bilan vient d'un autre flux (cadriciel → stderr), les positions ne
    # sont pas comparables : le verdict tient alors lieu de bilan.
    tb_apres = (any(m.start() > pos_bilan for m in RE_TRACEBACK.finditer(stdout))
                if (verdict_unittest is None or verdict_unittest[0] == 0)
                else False)
    crash = tb_apres or (pos_bilan < 0 and verdict_unittest is None
                         and (RE_TRACEBACK.search(stdout)
                              or RE_TRACEBACK.search(stderr)))

    if crash:
        erreur = ""
        for ligne in reversed((stdout + "\n" + stderr).splitlines()):
            if RE_ERREUR_FIN.match(ligne):
                erreur = ligne.strip()[:90]
                break
        detail = f"exception : {erreur}" if erreur else "exception non identifiée"
        if ok is not None:
            detail += f" (après {ok} contrôle(s) réussi(s))"
        return CAT_C, detail
    if ok is not None and ko is not None:
        return (CAT_R, f"{ok} contrôles, 0 en échec") if ko == 0 else (
            CAT_E, f"{ko} contrôle(s) en échec sur {ok + ko}")
    if verdict_unittest is not None:
        _, m_ran, m_verdict, est_ok = verdict_unittest
        nb = int(m_ran.group(1))
        if est_ok:
            detail = f"unittest : {nb} test(s), 0 en échec"
            if m_verdict.group(1):         # ex. « OK (skipped=2) » → on le DIT
                detail += f" {m_verdict.group(1).strip()}"
            return CAT_R, detail
        return CAT_E, f"unittest : {m_verdict.group(1)} sur {nb} test(s)"
    if RE_BILAN_TEXTE.search(stdout):
        return CAT_R, "bilan texte « tout est passé »"
    if RE_RESEAU.search(stdout + stderr):
        return CAT_NX, "environnement : hôte injoignable (aucun accès réseau — ne prouve rien)"
    if RE_INVOCATION.search(stderr):
        return CAT_NX, "invocation incorrecte (chemin de fichier)"
    if RE_SAUTE.search(stdout + stderr):
        motif = ""
        for ligne in (stdout + "\n" + stderr).splitlines():
            if RE_SAUTE.search(ligne):
                motif = ligne.strip()[:90]
                break
        return CAT_NX, f"saut explicite : {motif}"
    if code_retour != 0:
        return CAT_NC, f"code de retour {code_retour}, aucun bilan lisible"
    return CAT_NC, "sortie sans bilan exploitable"


def executer(suite: Path) -> tuple[str, str, float]:
    base = f"/tmp/qc_campagne_{suite.stem}.db"
    for suffixe in ("", "-wal", "-shm"):
        try:
            os.remove(base + suffixe)
        except OSError:
            pass
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{base}"
    debut = time.time()
    try:
        proc = subprocess.run([PYTHON, suite.name], cwd=str(RACINE), env=env,
                              capture_output=True, text=True, timeout=TIMEOUT_S)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return CAT_C, f"délai dépassé ({TIMEOUT_S} s)", float(TIMEOUT_S)
    categorie, detail = classer(stdout, stderr, code)
    return categorie, detail, time.time() - debut


def main() -> int:
    ap = argparse.ArgumentParser(description="Campagne de tests classée")
    ap.add_argument("--flaky", type=int, default=1, metavar="N",
                    help="nombre de passes par suite (détection d'instabilité)")
    ap.add_argument("--seulement", nargs="*", default=None,
                    help="n'exécuter que ces suites (sans .py)")
    ap.add_argument("--json", default=None, help="écrire le rapport JSON ici")
    ap.add_argument("--discret", action="store_true", help="n'afficher que le tableau final")
    args = ap.parse_args()

    suites = sorted(RACINE.glob("test_*.py"))
    if args.seulement:
        voulues = {s if s.endswith(".py") else s + ".py" for s in args.seulement}
        suites = [s for s in suites if s.name in voulues]
    if not suites:
        print("Aucune suite trouvée")
        return 2

    passes = max(1, args.flaky)
    print(f"CAMPAGNE — {len(suites)} suite(s), {passes} passe(s) chacune, "
          f"bases temporaires uniquement")
    resultats: dict[str, dict] = {}
    for suite in suites:
        etats = []
        last_detail = ""
        for _ in range(passes):
            categorie, detail, duree = executer(suite)
            etats.append(categorie)
            last_detail = detail
            if not args.discret:
                print(f"  · {suite.stem:<38} {categorie:<13} {detail[:60]}")
        cat = etats[-1]
        instable = len(set(etats)) > 1
        resultats[suite.stem] = {
            "categorie": CAT_C + " (instable)" if instable else cat,
            "etats_observes": sorted(set(etats)),
            "instable": instable,
            "detail": last_detail,
            "duree_s": round(duree, 1),
        }

    print("\n" + "=" * 78)
    print("TABLEAU FINAL — cinq catégories distinctes")
    print("=" * 78)
    ordre = [CAT_R, CAT_E, CAT_C, CAT_NX, CAT_NC]
    def cle(r):
        c = r["categorie"]
        base = CAT_C if c.startswith(CAT_C) else c
        return ordre.index(base) if base in ordre else len(ordre)
    for nom, r in sorted(resultats.items(), key=lambda it: cle(it[1])):
        marque = " ⚠INSTABLE" if r["instable"] else ""
        print(f"  {r['categorie']:<18}{marque:<11} {nom:<38} {r['detail'][:52]}")

    compte = {c: 0 for c in ordre}
    for r in resultats.values():
        base = CAT_C if r["categorie"].startswith(CAT_C) else r["categorie"]
        compte[base] = compte.get(base, 0) + 1
    flaky = [n for n, r in resultats.items() if r["instable"]]
    print("-" * 78)
    total = len(resultats)
    for c in ordre:
        print(f"  {c:<15} : {compte[c]}")
    print(f"  {'TOTAL':<15} : {total}  "
          f"({compte[CAT_R]} + {compte[CAT_E]} + {compte[CAT_C]} + "
          f"{compte[CAT_NX]} + {compte[CAT_NC]} = "
          f"{sum(compte.values())})")
    if flaky:
        print(f"\n  ⚠ FLAKY ({len(flaky)}) : {', '.join(flaky)}")
    non_prouve = compte[CAT_C] + compte[CAT_NX] + compte[CAT_NC]
    if non_prouve or flaky:
        print(f"\n  ⚠ CAMPAGNE NON VERTE — {non_prouve} suite(s) ne prouvent rien "
              f"({compte[CAT_C]} plantée(s), {compte[CAT_NX]} non exécutée(s), "
              f"{compte[CAT_NC]} non concluante(s)), {compte[CAT_E]} en échec, "
              f"{len(flaky)} instable(s).")
    else:
        print("\n  ✅ CAMPAGNE VERTE — toutes les suites ont rendu un verdict favorable.")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"total": total, "compte": compte, "flaky": flaky,
                        "suites": resultats}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"  rapport JSON : {args.json}")
    return 0 if (non_prouve == 0 and compte[CAT_E] == 0 and not flaky) else 1


if __name__ == "__main__":
    sys.exit(main())
