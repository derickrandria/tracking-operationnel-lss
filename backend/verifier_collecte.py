# -*- coding: utf-8 -*-
"""v1.48 — BILAN DE COLLECTE ET DE FLOTTE (une commande, exécutable sur le serveur).

Exécute pour vous la séquence de contrôle après déploiement du patch v148 :

  1. lit `/api/sante`            → statut honnête + sources en échec ;
  2. POST `/api/sante/sync`      → force un cycle réel et récupère la RÉPONSE
     du portail (c'est elle qui dit si MZoneX est cassé par les identifiants,
     par le flux SSO ou par le réseau) ;
  3. relit `/api/sante`          → état après cycle ;
  4. interroge la BASE           → répartition de la flotte par portail,
     camions muets (aucun point GPS récent), et journées archivées SANS
     données MZoneX (celles qu'il faudra reprendre).

Lecture seule : aucun point, aucun trajet, aucune archive n'est modifié.

Usage (sur le serveur, dans `backend/`) :
    python3 verifier_collecte.py                       # + cycle forcé
    python3 verifier_collecte.py --sans-sync           # observation seule
    python3 verifier_collecte.py --jours 10 --url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta


def appel(url: str, chemin: str, methode: str = "GET"):
    req = urllib.request.Request(url.rstrip("/") + chemin, method=methode)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"erreur_http": e.read().decode()[:200]}
    except Exception as e:
        return None, {"erreur": f"{type(e).__name__}: {e}"}


def afficher_sante(d: dict, titre: str) -> None:
    print(f"\n{titre}")
    for cle in ("statut", "statut_collecte", "sources_en_echec",
                "retard_collecte_min", "dernier_evenement_gps",
                "vehicules_actifs", "mode_collecte", "erreur_diagnostic"):
        if cle in d:
            print(f"    {cle:22s} {d.get(cle)!r}")
    for nom, s in (d.get("collecte_par_source") or {}).items():
        erreur = (s or {}).get("derniere_erreur")
        reussite = (s or {}).get("derniere_reussite")
        marque = "❌" if erreur else "✅"
        print(f"      {marque} {nom:20s} dernière réussite={reussite} "
              f"erreur={str(erreur)[:80] if erreur else 'aucune'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bilan de collecte et de flotte (v148)")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--jours", type=int, default=7,
                    help="jours d'archives à contrôler (défaut 7)")
    ap.add_argument("--sans-sync", action="store_true",
                    help="ne pas forcer de cycle de collecte (observation seule)")
    ap.add_argument("--muet-min", type=float, default=60.0,
                    help="seuil de silence d'un camion, en minutes (défaut 60)")
    args = ap.parse_args()

    print("=" * 78)
    print("BILAN DE COLLECTE ET DE FLOTTE — plateforme LSS Tracking")
    print(f"date : {datetime.now():%d/%m/%Y %H:%M} · API : {args.url}")
    print("=" * 78)

    print("\n[1] ÉTAT DE SANTÉ (après patch v148)")
    statut, d1 = appel(args.url, "/api/sante")
    if statut is None:
        print(f"    ⚠️  API injoignable ({d1.get('erreur')}) — lancez la "
              f"plateforme ou corrigez --url")
    else:
        afficher_sante(d1, "    /api/sante :")

    if not args.sans_sync and statut:
        print("\n[2] CYCLE DE COLLECTE FORCÉ (réponse réelle des portails)")
        st2, d2 = appel(args.url, "/api/sante/sync", "POST")
        if st2 is None:
            print(f"    ⚠️  cycle non déclenché ({d2.get('erreur')})")
        else:
            print(f"    HTTP {st2}")
            for cle in ("mzonex_n1_points", "camtrackpro_n1_points", "duree_s"):
                if cle in d2:
                    print(f"      {cle:22s} {d2.get(cle)!r}")
            erreurs = d2.get("erreurs") or []
            if erreurs:
                print("      ❌ ERREURS DES SOURCES (cause réelle) :")
                for e in erreurs:
                    print(f"         · {e}")
            else:
                print("      ✅ aucune erreur de source sur ce cycle")

        print("\n[3] ÉTAT DE SANTÉ APRÈS CYCLE")
        _, d3 = appel(args.url, "/api/sante")
        afficher_sante(d3, "    /api/sante :")

    print("\n[4] FLOTTE ET ARCHIVES (lecture directe de la base)")
    try:
        from app.database import SessionLocal
        from app.models import HistoriqueJournalier, Vehicule
        from app.config import now_local
    except Exception as e:
        print(f"    ⚠️  base non interrogée ({type(e).__name__}: {e}) — lancez "
              f"ce script depuis le dossier `backend/`")
        return 0
    db = SessionLocal()
    try:
        vehs = db.query(Vehicule).all()
        par_portail: dict[str, int] = {}
        for v in vehs:
            par_portail[v.plateforme_gps or "?"] = par_portail.get(v.plateforme_gps or "?", 0) + 1
        print("    Répartition de la flotte par portail :")
        for portail, n in sorted(par_portail.items()):
            print(f"      {portail:14s} {n} véhicule(s)")

        maintenant = now_local()
        seuil = timedelta(minutes=args.muet_min)
        muets = [v for v in vehs if v.last_event_at is None
                 or (maintenant - v.last_event_at) > seuil]
        muets.sort(key=lambda v: v.last_event_at or datetime.min)
        print(f"\n    Camions SANS point GPS depuis > {args.muet_min:.0f} min : "
              f"{len(muets)} / {len(vehs)}")
        for v in muets[:12]:
            age = ("jamais" if v.last_event_at is None
                   else f"{(maintenant - v.last_event_at).total_seconds() / 3600:.1f} h")
            print(f"      · {v.plaque:12s} portail={v.plateforme_gps or '?':12s} "
                  f"dernier point={age}")
        if len(muets) > 12:
            print(f"      … et {len(muets) - 12} autre(s)")

        print(f"\n    Journées archivées sans données, par portail "
              f"({args.jours} derniers jours) :")
        aujour = maintenant.date()
        for offset in range(args.jours, 0, -1):
            jour = aujour - timedelta(days=offset)
            archives = db.query(HistoriqueJournalier).filter(
                HistoriqueJournalier.date_jour == jour).all()
            if not archives:
                print(f"      ·  {jour:%d/%m}  aucune archive")
                continue
            stats: dict[str, list[int]] = {}
            for h in archives:
                v = db.get(Vehicule, h.vehicule_id)
                portail = (v.plateforme_gps if v else None) or "?"
                nb_traj = len((h.donnees or {}).get("trajets") or [])
                stats.setdefault(portail, [0, 0])
                stats[portail][1] += 1
                if nb_traj == 0:
                    stats[portail][0] += 1
            detail = " · ".join(
                f"{p} {vides}/{tot} sans trajet"
                for p, (vides, tot) in sorted(stats.items()))
            alerte = "  ⚠️ À REPRENDRE" if any(v > 0 for v, _ in stats.values()) else ""
            print(f"      ·  {jour:%d/%m}  {detail}{alerte}")
    finally:
        db.close()

    print("\n" + "=" * 78)
    print("LECTURE : une source ❌ en [1] ou une erreur en [2] = collecte à "
          "réparer AVANT de trancher les archives.")
    print("Si MZoneX échoue encore après ce patch, l'erreur de [2] contient "
          "désormais la réponse du portail (code, cookies, corps) : elle dit "
          "la cause exacte — identifiants, flux SSO ou réseau.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
