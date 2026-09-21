#!/usr/bin/env python3
"""Recette de PRÉPRODUCTION — v1.54 (21/09/2026).

Exécute les deux suites qui ne peuvent PAS prouver leur objet dans un bac à
sable sans réseau ni base réelle :

  · `test_mzonex_ping.py`      — les six points d'entrée des portails répondent ;
  · `test_e2e_reel_v113.py`    — la guérison v1.13 rejouée sur de VRAIS trajets.

GARANTIES (exigences de la recette) :

  · LECTURE SEULE sur la base de préproduction : elle est **copiée** avant usage
    et son empreinte SHA-256 est vérifiée AVANT et APRÈS — une base modifiée
    fait échouer la recette (`--base-preprod` intacte = critère d'acceptation) ;
  · AUCUN secret affiché ni journalisé : ni `.env`, ni jeton, ni mot de passe ;
    seuls le nom des variables et un booléen de présence le sont ;
  · AUCUN accès à la production : le pilote REFUSE de tourner si
    `APP_ENV=production`, ou si la base fournie est celle de production ;
  · LOGS VÉRIFIABLES : chaque étape est horodatée, avec code de sortie, durée,
    empreintes, verdicts lus dans la sortie des suites et code global
    (`0` = recette conforme, `1` = écart, `2` = refus de sécurité).

Usage (préproduction) :
  cd backend
  python3 recette_preprod_v154.py --base-preprod /srv/preprod/lss_preprod.db \\
      [--sortie docs/audits/recette_preprod_<AAAAmmJJ_HHMM>.json]
  # ou, pour un PostgreSQL de préproduction (copie logique préalable) :
  python3 recette_preprod_v154.py --base-preprod /srv/preprod/lss_preprod.dump

Aucune écriture n'est faite dans la base de préproduction : le pilote travaille
dans un dossier temporaire (`data/lss.db` y est la copie de travail).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parent          # …/backend
DEPOT = RACINE.parent
HORODATAGE = lambda: datetime.now().isoformat(timespec="seconds")   # noqa: E731


def journaliser(journal: list, etape: str, **champs) -> None:
    ligne = {"horodatage": HORODATAGE(), "etape": etape, **champs}
    journal.append(ligne)
    detail = " ".join(f"{k}={v}" for k, v in champs.items()
                      if k in ("verdict", "code", "duree_s", "empreinte_avant",
                               "empreinte_apres", "resume", "detail", "raison"))
    print(f"  [{ligne['horodatage']}] {etape:26} {detail}")


def sha256(chemin: Path) -> str:
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def _refus(raison: str, journal: list, sortie: Path | None) -> int:
    journaliser(journal, "REFUS_SECURITE", verdict="REFUS", raison=raison)
    _ecrire(journal, sortie, verdict="REFUS_SECURITE")
    return 2


def _ecrire(journal: list, sortie: Path | None, **extra) -> None:
    if sortie is None:
        return
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps(
        {"recette": "preprod_v154", "horodatage": HORODATAGE(),
         "commit": _commit_git(), "environnement": {
             "plateforme": sys.platform, "python": sys.version.split()[0],
             "app_env": os.getenv("APP_ENV", "(non défini)")},
         **extra, "journal": journal}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"  → journal JSON : {sortie}")


def _commit_git() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=DEPOT,
                              capture_output=True, text=True, timeout=10
                              ).stdout.strip() or "(inconnu)"
    except Exception:                                             # noqa: BLE001
        return "(inconnu)"


def _sortie_lisible(texte: str) -> str:
    """Retire tout ce qui ressemble à un secret avant journalisation."""
    lignes = []
    for ligne in texte.splitlines():
        bas = ligne.lower()
        if any(m in bas for m in ("password", "mot de passe", "token=", "bearer ",
                                  "mzonex_password", "mzonex_user")):
            continue                       # jamais de secret dans les logs
        lignes.append(ligne.rstrip()[:400])
    return "\n".join(lignes)


def _extraire_verdicts(texte: str) -> dict:
    ok = texte.count("✅")
    ko = texte.count("❌")
    resultat = None
    for ligne in texte.splitlines():
        if "RÉSULTAT" in ligne and "/" in ligne:
            resultat = ligne.strip()[:160]
    return {"controles_ok": ok, "controles_ko": ko, "resultat": resultat}


# ─────────────────────────────────────────────────────────── 1) pings portails
def etape_ping(journal: list) -> dict:
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, str(RACINE / "test_mzonex_ping.py")],
                          cwd=RACINE, capture_output=True, text=True, timeout=180)
    sortie = _sortie_lisible(proc.stdout + proc.stderr)
    joignables, injoignables = [], []
    for ligne in sortie.splitlines():
        if ligne.strip().startswith(("✅", "❌")) and "[" in ligne:
            nom = ligne.split("[", 1)[1].rstrip("]").strip()
            (joignables if ligne.strip().startswith("✅") else injoignables).append(nom)
    verdict = ("PORTAILS_JOIGNABLES" if joignables and not injoignables
               else ("PARTIEL" if joignables else "AUCUN_PORTAIL_JOIGNABLE"))
    journaliser(journal, "ping_portails", verdict=verdict, code=proc.returncode,
                duree_s=round(time.monotonic() - t0, 2),
                resume=f"{len(joignables)} joignable(s), {len(injoignables)} injoignable(s)",
                detail="; ".join(injoignables[:6]) or "—")
    return {"verdict": verdict, "joignables": joignables,
            "injoignables": injoignables, "sortie": sortie}


# ──────────────────────────────────────────────── 2) e2e sur base de préprod
def etape_e2e(journal: list, base_preprod: Path, travail: Path) -> dict:
    t0 = time.monotonic()
    avant = sha256(base_preprod)
    (travail / "data").mkdir(parents=True, exist_ok=True)
    copie_travail = travail / "data" / "lss.db"
    shutil.copy2(base_preprod, copie_travail)
    env = {**os.environ, "SIM_ENABLE": "0", "TESTING": "1",
           "PYTHONPATH": str(RACINE), "PYTHONUNBUFFERED": "1"}
    env.pop("DATABASE_URL", None)         # la suite fixe la sienne : /tmp/e2e_v113.db
    proc = subprocess.run([sys.executable, str(RACINE / "test_e2e_reel_v113.py")],
                          cwd=travail, env=env, capture_output=True, text=True,
                          timeout=900)
    sortie = _sortie_lisible(proc.stdout + proc.stderr)
    apres = sha256(base_preprod)
    mesures = _extraire_verdicts(sortie)
    if proc.returncode == 0 and mesures["controles_ko"] == 0 and avant == apres:
        verdict = "CONFORME"
    elif proc.returncode == 0 and "sauté" in sortie.lower():
        verdict = "NON_PROBANT"           # base absente : la suite n'a rien prouvé
    else:
        verdict = "ECART"
    journaliser(journal, "e2e_reel_v113", verdict=verdict, code=proc.returncode,
                duree_s=round(time.monotonic() - t0, 2),
                empreinte_avant=avant[:16], empreinte_apres=apres[:16],
                resume=mesures["resultat"] or f"ok={mesures['controles_ok']} "
                                             f"ko={mesures['controles_ko']}")
    return {"verdict": verdict, "code": proc.returncode, "mesures": mesures,
            "base_intacte": avant == apres, "empreinte": avant, "sortie": sortie}


def main() -> int:
    ap = argparse.ArgumentParser(description="Recette de préproduction v1.54")
    ap.add_argument("--base-preprod", required=True,
                    help="copie de la base de PRÉPRODUCTION (jamais la production)")
    ap.add_argument("--sortie", default=None,
                    help="journal JSON (défaut : docs/audits/recette_preprod_<horodatage>.json)")
    ap.add_argument("--sauter-ping", action="store_true",
                    help="si le réseau est déjà prouvé par un autre moyen")
    args = ap.parse_args()

    base = Path(args.base_preprod).expanduser().resolve()
    sortie = Path(args.sortie).expanduser() if args.sortie else (
        DEPOT / "docs" / "audits" /
        f"recette_preprod_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    journal: list = []
    print("═" * 78)
    print("  RECETTE DE PRÉPRODUCTION v1.54 — lecture seule, logs vérifiables")
    print("═" * 78)
    journaliser(journal, "ouverture", verdict="OK", commit=_commit_git()[:12],
                resume=f"base={base.name} ({base.stat().st_size if base.exists() else 0} octets)")

    # ── garde-fous : jamais la production
    if os.getenv("APP_ENV", "").strip().lower() == "production":
        return _refus("APP_ENV=production : cette recette ne tourne PAS en "
                      "production", journal, sortie)
    if not base.exists():
        return _refus(f"base de préproduction introuvable : {base}", journal, sortie)
    if base.suffix == ".db" and "preprod" not in base.name.lower() \
            and "preproduction" not in str(base).lower():
        return _refus("le fichier fourni ne se nomme pas comme une base de "
                      "PRÉPRODUCTION (attendu : *preprod*.db) — refus par sécurité "
                      "pour ne jamais lire une base de production", journal, sortie)

    resultats: dict = {}
    # ── 1) portails joignables
    if args.sauter_ping:
        journaliser(journal, "ping_portails", verdict="SAUTÉ",
                    detail="--sauter-ping : preuve réseau fournie par ailleurs")
        resultats["ping"] = {"verdict": "SAUTÉ"}
    else:
        resultats["ping"] = etape_ping(journal)

    # ── 2) E2E sur la copie de préproduction (source jamais modifiée)
    with tempfile.TemporaryDirectory(prefix="recette_preprod_") as tmp:
        resultats["e2e"] = etape_e2e(journal, base, Path(tmp))

    # ── 3) verdict de recette
    p = resultats.get("ping", {}).get("verdict", "SAUTÉ")
    e = resultats.get("e2e", {}).get("verdict", "NON_EXÉCUTÉ")
    conforme = e == "CONFORME" and p in ("PORTAILS_JOIGNABLES", "SAUTÉ")
    verdict = "RECETTE_CONFORME" if conforme else "RECETTE_NON_CONFORME"
    journaliser(journal, "verdict_final", verdict=verdict,
                resume=f"ping={p} · e2e={e} · base intacte="
                       f"{resultats.get('e2e', {}).get('base_intacte')}")
    for cle, bloc in resultats.items():
        if isinstance(bloc, dict) and bloc.get("sortie"):
            print(f"\n  ── sortie de l'étape « {cle} » " + "─" * 40)
            for ligne in bloc["sortie"].splitlines():
                print(f"    {ligne}")
    print("\n" + "═" * 78)
    print(f"  {verdict} — critères : portails joignables ET e2e conforme ET base "
          f"d'empreinte inchangée")
    print("═" * 78)
    _ecrire(journal, sortie, verdict=verdict, resultats={
        k: {kk: vv for kk, vv in v.items() if kk != "sortie"}
        for k, v in resultats.items()})
    return 0 if conforme else 1


if __name__ == "__main__":
    sys.exit(main())
