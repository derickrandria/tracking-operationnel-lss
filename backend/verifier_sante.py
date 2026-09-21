# -*- coding: utf-8 -*-
"""v1.50 — BILAN DE SANTÉ DE LA PLATEFORME (une commande, un verdict).

Répond à UNE question : « ma plateforme est-elle saine ? » — sans avis, avec des
mesures. Sept axes, chacun jugé ✅ conforme / ⚠️ à surveiller / ❌ à réparer :

  1. LOI        — SPEC_RULES_v3 présent, et les seuils du CODE sont ceux de la spec
  2. CODE       — appels morts (dérive appelant ↔ fonction) + compilation de tous les modules
  3. DÉMARRAGE  — l'application s'importe et se construit (chemin critique de démarrage)
  4. API        — si le serveur tourne : login + endpoints clés répondent
  5. BASE       — intégrité des compteurs : négatifs, TCJ > TTJ, TCC > 12 h,
                  trajets de jours PASSÉS restés sans fin (le défaut que v147 répare)
  6. COLLECTE   — fraîcheur réelle : âge du dernier point GPS, véhicules muets,
                  échecs de collecte tracés
  7. ÉCRAN      — le bundle servi (frontend/dist) est-il postérieur aux sources ?

L'outil ne modifie RIEN (lecture seule) et n'exige aucune donnée de production :
il tourne sur la machine de l'exploitant, avec la vraie base et le vrai .env.

Exécution :
    cd backend
    python3 verifier_sante.py                 # bilan complet (base de la machine)
    DATABASE_URL="sqlite:////tmp/x.db" python3 verifier_sante.py   # sur une copie
"""
from __future__ import annotations

import argparse
import ast
import compileall
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent          # racine du projet
BACKEND = Path(__file__).resolve().parent
RESULTATS: list[tuple[str, str, str]] = []               # (axe, statut, détail)


def noter(axe: str, statut: str, detail: str) -> None:
    RESULTATS.append((axe, statut, detail))
    icone = {"OK": "✅", "ATTENTION": "⚠️ ", "KO": "❌"}[statut]
    print(f"  {icone} {axe:22s} {detail}")


# ---------------------------------------------------------------- 1 · LA LOI
def axe_loi() -> None:
    spec = RACINE / "SPEC_RULES_v3.md"
    if not spec.is_file():
        noter("Loi", "KO", "SPEC_RULES_v3.md INTROUVABLE — la source de vérité "
                          "n'est pas dans le projet")
        return
    texte = spec.read_text(encoding="utf-8")
    noter("Loi", "OK", f"SPEC_RULES_v3.md présent ({len(texte.splitlines())} l.)")

    sys.path.insert(0, str(BACKEND))
    try:
        from app.engine import SEUILS_DEFAUT
    except Exception as exc:
        noter("Loi", "KO", f"seuils illisibles : {type(exc).__name__}: {exc}")
        return

    def valeur(cle: str):
        v = SEUILS_DEFAUT.get(cle)
        return v[0] if isinstance(v, tuple) else v

    attendus = {
        "SEUIL_DISTANCE_MIN_TRAJET_KM": 0.3,
        "SEUIL_PAUSE_COUPURE_TCC": 1800.0,
        "HEURE_PRE_CONSOLIDATION": 86399.0,
        "SEUIL_TTJ_MAX": 43200.0,
        "SEUIL_TCC_PREALERTE": 14400.0,
    }
    ecarts = []
    for cle, attendu in attendus.items():
        reel = valeur(cle)
        if reel is None:
            ecarts.append(f"{cle} absent du code")
        elif abs(float(reel) - attendu) > 1e-9:
            ecarts.append(f"{cle}={reel} (spec : {attendu})")
    if ecarts:
        noter("Loi / seuils", "KO", "; ".join(ecarts))
    else:
        noter("Loi / seuils", "OK",
              "0,3 km · pause 30 min · 23:59:59 · TTJ 12 h · pré-alerte 4 h — "
              "conformes à la spec")
    for interdit in ("SEUIL_DUREE_MIN_TRAJET_S", "REGLE_VALIDITE_TRAJET"):
        if interdit in SEUILS_DEFAUT:
            noter("Loi / seuils", "ATTENTION",
                  f"{interdit} réintroduit — seuil de durée de trajet : la spec "
                  f"[R-01] ne retient QUE la distance")


# --------------------------------------------------------------- 2 · LE CODE
def axe_code() -> None:
    try:
        r = subprocess.run([sys.executable, str(BACKEND / "verifier_appels_morts.py")],
                           capture_output=True, text=True, timeout=180, cwd=BACKEND)
        sortie = r.stdout.strip().splitlines()
        morts = [l for l in sortie if l.strip().startswith("· ")]
        if morts:
            noter("Code / appels", "KO",
                  f"{len(morts)} appel(s) mort(s) — TypeError garanti, avalé si "
                  f"sous `except` : " + " | ".join(m.strip()[:52] for m in morts[:3]))
        else:
            noter("Code / appels", "OK", "aucun appel mort (signatures cohérentes)")
    except Exception as exc:
        noter("Code / appels", "ATTENTION",
              f"vérificateur indisponible ({type(exc).__name__}) — copiez "
              f"verifier_appels_morts.py à côté")

    ok = compileall.compile_dir(str(BACKEND / "app"), quiet=2, force=True)
    noter("Code / syntaxe", "OK" if ok else "KO",
          "tous les modules compilent" if ok else "au moins un module ne compile pas")


# ---------------------------------------------------------- 3 · LE DÉMARRAGE
def axe_demarrage() -> None:
    code = ("import os; os.environ.setdefault('SIM_ENABLE','0');"
            "from app.main import app;"
            "print('ROUTES', len([r for r in app.routes if getattr(r,'path','')]))")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=300, cwd=BACKEND)
        ligne = [l for l in r.stdout.strip().splitlines() if l.startswith("ROUTES")]
        if r.returncode == 0 and ligne:
            noter("Démarrage", "OK", f"application construite ({ligne[0].split()[1]} routes)")
        else:
            erreur = (r.stderr.strip().splitlines() or ["(aucun détail)"])[-1]
            noter("Démarrage", "KO", f"import/construction en échec : {erreur[:110]}")
    except Exception as exc:
        noter("Démarrage", "KO", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- 4 · L'API
def axe_api(url: str) -> None:
    def appel(chemin, methode="GET", charge=None, jeton=None):
        donnee = json.dumps(charge).encode() if charge else None
        req = urllib.request.Request(f"{url.rstrip('/')}{chemin}", data=donnee,
                                     method=methode)
        req.add_header("Content-Type", "application/json")
        if jeton:
            req.add_header("Authorization", f"Bearer {jeton}")
        with urllib.request.urlopen(req, timeout=20) as rep:
            return rep.status, json.loads(rep.read().decode() or "{}")

    try:
        code, corps = appel("/api/auth/login", "POST",
                            {"username": "tracking", "password": "Tracking@2026"})
        jeton = corps.get("access_token")
    except Exception as exc:
        noter("API", "ATTENTION",
              f"serveur non joignable sur {url} ({type(exc).__name__}) — lancez la "
              f"plateforme puis relancez ce bilan")
        return
    noter("API / login", "OK" if jeton else "KO",
          "jeton obtenu" if jeton else "login refusé (identifiants de seed ?)")
    for chemin in ("/api/suivi", "/api/vehicules", "/api/dashboard",
                   "/api/temps-conduite", "/api/alertes"):
        try:
            statut, _ = appel(chemin, jeton=jeton)
            noter(f"API {chemin}", "OK" if statut == 200 else "KO", f"HTTP {statut}")
        except Exception as exc:
            noter(f"API {chemin}", "KO", f"{type(exc).__name__}: {str(exc)[:70]}")


# ---------------------------------------------------------------- 5 · LA BASE
def axe_base() -> None:
    try:
        from app.database import SessionLocal
        from app.models import SuiviJournalier, EvenementGPS, Trajet
        from sqlalchemy import func, select
    except Exception as exc:
        noter("Base", "ATTENTION", f"modules indisponibles ({type(exc).__name__})")
        return
    db = SessionLocal()
    try:
        negatifs = db.scalar(select(func.count(SuiviJournalier.id)).where(
            (SuiviJournalier.ttj_s < 0) | (SuiviJournalier.tcj_s < 0)
            | (SuiviJournalier.tcc_s < 0))) or 0
        noter("Base / compteurs", "OK" if negatifs == 0 else "KO",
              "aucun compteur négatif" if negatifs == 0
              else f"{negatifs} jour(s) avec compteur négatif")

        pire = db.scalar(select(func.count(SuiviJournalier.id)).where(
            SuiviJournalier.tcj_s > SuiviJournalier.ttj_s + 60)) or 0
        noter("Base / hiérarchie", "OK" if pire == 0 else "KO",
              "TCJ ≤ TTJ partout" if pire == 0
              else f"{pire} jour(s) où le TCJ dépasse le TTJ (impossible)")

        geants = db.scalar(select(func.count(SuiviJournalier.id)).where(
            SuiviJournalier.ttj_s > 24 * 3600)) or 0
        noter("Base / plafond 24 h", "OK" if geants == 0 else "ATTENTION",
              "aucune journée > 24 h" if geants == 0
              else f"{geants} journée(s) > 24 h")

        hier = datetime.now().date() - timedelta(days=1)
        sans_fin = db.scalar(select(func.count(Trajet.id)).join(
            SuiviJournalier, Trajet.suivi_id == SuiviJournalier.id).where(
            SuiviJournalier.date_jour < hier, Trajet.heure_fin.is_(None))) or 0
        noter("Base / trajets ouverts", "OK" if sans_fin == 0 else "KO",
              "aucun trajet passé resté ouvert" if sans_fin == 0
              else f"{sans_fin} trajet(s) de jours passés SANS FIN (réparation "
                   f"v147 attendue)")

        total_pts = db.scalar(select(func.count(EvenementGPS.id))) or 0
        dernier = db.scalar(select(func.max(EvenementGPS.horodatage)))
        noter("Base / volume", "OK" if total_pts else "ATTENTION",
              f"{total_pts} point(s) GPS en base"
              + (f", dernier : {dernier:%d/%m %H:%M}" if dernier else " (AUCUN)"))
    except Exception as exc:
        noter("Base", "ATTENTION", f"requêtes non abouties : {type(exc).__name__}: "
                                   f"{str(exc)[:80]}")
    finally:
        db.close()


# ------------------------------------------------------------- 6 · LA COLLECTE
def axe_collecte(fenetre_min: float = 30.0) -> None:
    try:
        from app.database import SessionLocal
        from app.models import EvenementGPS, Vehicule
        from sqlalchemy import func, select
    except Exception as exc:
        noter("Collecte", "ATTENTION", f"modules indisponibles ({type(exc).__name__})")
        return
    # Sans identifiants de portail, l'absence de points n'est PAS un défaut de la
    # plateforme : c'est une machine non configurée. On le dit, on n'alarme pas.
    env = BACKEND / ".env"
    configuree = env.is_file() and any(
        cle in env.read_text(encoding="utf-8", errors="replace")
        for cle in ("MZONEX_USER", "WIALON_TOKEN", "CAMTRACKPRO", "YMANE"))
    db = SessionLocal()
    try:
        dernier = db.scalar(select(func.max(EvenementGPS.horodatage)))
        if dernier is None:
            if not configuree:
                noter("Collecte", "ATTENTION",
                      "non mesurable sur cette machine : backend/.env absent ou sans "
                      "identifiants de portail (à mesurer sur le serveur réel)")
            else:
                noter("Collecte", "KO", "identifiants présents MAIS aucun point GPS "
                                        "en base : la collecte ne ramène rien")
            return
        age = (datetime.now() - dernier).total_seconds() / 60
        statut = "OK" if age <= fenetre_min else "KO"
        if not configuree:
            statut = "ATTENTION"
        noter("Collecte / fraîcheur", statut,
              f"dernier point GPS il y a {age:.0f} min"
              + ("" if statut == "OK" else
                 (" — machine non configurée (pas d'identifiants portail)"
                  if not configuree else
                  " — la collecte est ARRÊTÉE ou muette : à vérifier tout de suite")))
        nb_veh = db.scalar(select(func.count(Vehicule.id))) or 0
        muets = db.scalar(select(func.count(Vehicule.id)).where(
            Vehicule.last_event_at.is_(None))) or 0
        noter("Collecte / couverture", "OK" if muets < max(1, nb_veh * 0.2)
              else "ATTENTION",
              f"{nb_veh - muets}/{nb_veh} véhicule(s) ont déjà remonté un point")
    except Exception as exc:
        noter("Collecte", "ATTENTION", f"{type(exc).__name__}: {str(exc)[:80]}")
    finally:
        db.close()


# ---------------------------------------------------------------- 7 · L'ÉCRAN
def axe_ecran() -> None:
    dist = RACINE / "frontend" / "dist"
    src = RACINE / "frontend" / "src"
    if not dist.is_dir():
        noter("Écran / bundle", "KO", "frontend/dist ABSENT — l'écran servi est "
                                      "celui du build précédent ou rien")
        return
    bundles = list(dist.rglob("*.js"))
    noter("Écran / bundle", "OK" if bundles else "KO",
          f"{len(bundles)} fichier(s) JS construits" if bundles
          else "aucun JS dans dist — bundle vide")
    def git(*args):
        return subprocess.run(["git", *args], cwd=RACINE, capture_output=True,
                              text=True, timeout=60).stdout.strip()
    try:
        c_dist = git("log", "-1", "--format=%ad", "--date=short", "--", "frontend/dist")
        c_src = git("log", "-1", "--format=%ad", "--date=short", "--", "frontend/src")
        if c_dist and c_src:
            noter("Écran / fraîcheur", "OK" if c_dist >= c_src else "ATTENTION",
                  f"bundle {c_dist} vs sources {c_src}"
                  + ("" if c_dist >= c_src else " — le bundle est EN RETARD sur "
                                                "le code : reconstruire"))
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Bilan de santé de la plateforme LSS")
    ap.add_argument("--url", default="http://127.0.0.1:8000",
                    help="adresse de la plateforme (défaut : local)")
    ap.add_argument("--sans-api", action="store_true", help="ignorer l'axe API")
    args = ap.parse_args()

    print("=" * 78)
    print("BILAN DE SANTÉ — PLATEFORME LSS TRACKING")
    print(f"date : {datetime.now():%d/%m/%Y %H:%M} · projet : {RACINE}")
    print("=" * 78)
    print("\n[1] LA LOI (SPEC_RULES_v3, source de vérité)")
    axe_loi()
    print("\n[2] LE CODE (dérive appelant ↔ fonction, syntaxe)")
    axe_code()
    print("\n[3] LE DÉMARRAGE (chemin critique)")
    axe_demarrage()
    if not args.sans_api:
        print(f"\n[4] L'API ({args.url})")
        axe_api(args.url)
    print("\n[5] LA BASE (intégrité des compteurs)")
    axe_base()
    print("\n[6] LA COLLECTE (fraîcheur réelle)")
    axe_collecte()
    print("\n[7] L'ÉCRAN (bundle servi)")
    axe_ecran()

    ko = [r for r in RESULTATS if r[1] == "KO"]
    att = [r for r in RESULTATS if r[1] == "ATTENTION"]
    ok = [r for r in RESULTATS if r[1] == "OK"]
    print("\n" + "=" * 78)
    print(f"CONTRÔLES : {len(ok)} conformes · {len(att)} à surveiller · {len(ko)} à réparer")
    if not ko and not att:
        verdict = "PLATEFORME SAINE — tous les contrôles passent."
    elif any(r[0].startswith(("Loi", "Démarrage")) for r in ko):
        verdict = ("PLATEFORME BLOQUÉE — un contrôle CRITIQUE échoue (loi ou "
                   "démarrage) : à réparer avant toute autre chose.")
    elif ko:
        verdict = ("PLATEFORME DÉGRADÉE — le cœur tourne, mais "
                   f"{len(ko)} point(s) précis est/sont cassé(s) : "
                   + " | ".join(r[0] for r in ko))
    else:
        verdict = ("PLATEFORME SAINE AVEC RÉSERVES — rien de cassé, "
                   f"{len(att)} point(s) à surveiller.")
    print(verdict)
    print("=" * 78)
    return 1 if ko else 0


if __name__ == "__main__":
    sys.exit(main())
