# -*- coding: utf-8 -*-
"""v1.48 — BILAN DE COLLECTE ET DE FLOTTE (v2, lecture seule).

Étape de contrôle sur le serveur, après déploiement du patch v148.

  1. lit `/api/sante`          → statut honnête, sources en échec, et DÉTECTION
     du cas « service non redémarré » (les champs v148 sont absents) ;
  2. (option `--sync`) force un cycle de collecte réel et affiche la réponse
     des portails — l'endpoint est SYNCHRONE et peut durer plusieurs minutes :
     il n'est donc plus exécuté par défaut ;
  3. relit `/api/sante`        → état après cycle ;
  4. interroge la BASE         → flotte par portail, véhicules jamais vus,
     camions muets, et pour chaque journée : trajets en archive, mouvements
     GPS réellement présents, journées signalées par l'audit (portails
     absents / archivage sur la base locale / archivage réécrit).

La section 4 ne se contente plus de « archive sans trajet » (trop grossier :
un camion peut légitimement n'avoir pas roulé) : elle croise les MOUVEMENTS
GPS de la journée avec le contenu de l'archive. Un camion qui a bougé ce
jour-là et dont l'archive ne contient aucun trajet = trou réel.

Lecture seule : rien n'est modifié.

Usage (sur le serveur, dans `backend/`) :
    python3 verifier_collecte.py                 # observation (rapide)
    python3 verifier_collecte.py --sync          # force un cycle (patience)
    python3 verifier_collecte.py --rapide        # saute l'analyse GPS par jour
"""
from __future__ import annotations

import os
# v1.50 — outil de CONTRÔLE en lecture seule : on n'ouvre pas de transaction
# d'écriture (sinon ses longues analyses prendraient le verrou de la base et
# gêneraient la collecte du service en cours d'exécution).
os.environ.setdefault("LSS_SQLITE_IMMEDIATE", "0")

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta

ACTIONS_REPRISE = (
    "cycle_minuit.relecture_partielle",   # minuit : portails injoignables
    "jour.catchup_consolide_local",       # catch-up : archivé sur la base seule
    "jour.catchup_sans_source",           # catch-up : jour laissé de côté
    "jour.archive_reecrite_v130",         # archive réécrite par la réparation
    "historique.recalculer_archive",      # reprise manuelle déjà faite
    "trajet.sans_fin_jour_passe",         # v147 : fins manquantes réparées
)


def appel(url: str, chemin: str, methode: str = "GET", timeout: float = 30.0):
    req = urllib.request.Request(url.rstrip("/") + chemin, method=methode)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"erreur_http": e.read().decode()[:200]}
    except Exception as e:
        return None, {"erreur": f"{type(e).__name__}: {e}"}


def afficher_sante(d: dict, titre: str) -> bool:
    """Affiche l'état de santé. Renvoie True si le serveur est bien à jour (v148)."""
    print(titre)
    a_jour = "sources_en_echec" in d and "collecte_par_source" in d
    for cle in ("statut", "statut_collecte", "retard_collecte_min",
                "dernier_evenement_gps", "vehicules_actifs", "mode_collecte",
                "erreur_diagnostic"):
        if cle in d:
            print(f"    {cle:22s} {d.get(cle)!r}")
    if a_jour:
        print(f"    {'sources_en_echec':22s} {d.get('sources_en_echec')!r}")
        for nom, s in (d.get("collecte_par_source") or {}).items():
            erreur = (s or {}).get("derniere_erreur")
            marque = "❌" if erreur else "✅"
            print(f"      {marque} {nom:20s} dernière réussite="
                  f"{(s or {}).get('derniere_reussite')} "
                  f"erreur={str(erreur)[:150] if erreur else 'aucune'}")
        if d.get("statut") == "COLLECTE_OK" and d.get("sources_en_echec"):
            print("      ⚠️  incohérence : statut OK avec des sources en échec")
        # v1.50 — « qui est en panne : le portail ou nous ? ». Un verrou de base
        # ne se répare pas en attendant le portail (constat du 18/09/2026 :
        # MZONEX déclarée en panne alors que la seule erreur était un
        # « database is locked » local).
        if "sources_bloquees_localement" in d:
            locales = d.get("sources_bloquees_localement") or []
            portails = d.get("sources_portail_en_panne") or []
            if locales:
                print(f"      🔴 CAUSE LOCALE (base/disque) : {locales}")
                print("         → ce n'est PAS une panne du portail. Regarder "
                      "`sqlite` ci-dessous, la charge disque, et "
                      "DIAGNOSTIC_VERROUS_SQLITE_v150.md")
            if portails:
                print(f"      🔴 PORTAIL EN PANNE : {portails}")
            if not locales and not portails:
                print("      ✅ aucune cause d'échec identifiée")
        for cle in ("sqlite", "collecte_bloquee_localement"):
            if cle in d:
                print(f"    {cle:22s} {d.get(cle)!r}")
    else:
        print("    ⚠️  CHAMPS v148 ABSENTS (`sources_en_echec`, "
              "`collecte_par_source`)")
        print("        → le processus en cours n'exécute PAS le code mis à jour :")
        print("          redémarrer le service, puis relancer ce bilan.")
        print("        → en attendant, le statut affiché reste AVEUGLE aux "
              "sources mortes.")
    return a_jour


def section_base(args) -> None:
    print("\n[4] FLOTTE, MOUVEMENTS ET ARCHIVES (lecture directe de la base)")
    try:
        from app.database import SessionLocal
        from app.models import (AuditLog, EvenementGPS, HistoriqueJournalier,
                                Vehicule)
        from app.config import now_local
        from sqlalchemy import func
    except Exception as e:
        print(f"    ⚠️  base non interrogée ({type(e).__name__}: {e}) — lancez "
              f"ce script depuis le dossier `backend/`")
        return
    db = SessionLocal()
    try:
        vehs = db.query(Vehicule).all()
        par_portail: dict[str, int] = {}
        for v in vehs:
            par_portail[v.plateforme_gps or "?"] = \
                par_portail.get(v.plateforme_gps or "?", 0) + 1
        print("    Répartition de la flotte : " + " · ".join(
            f"{p} {n}" for p, n in sorted(par_portail.items())))

        jamais = [v for v in vehs if v.last_event_at is None]
        if jamais:
            print(f"\n    ⚠️  VÉHICULES JAMAIS VUS ({len(jamais)}) — aucun point "
                  f"GPS depuis le début : rattachement portail à vérifier")
            for v in jamais[:12]:
                print(f"      · {v.plaque:12s} portail={v.plateforme_gps or '?':12s} "
                      f"gps={v.gps_associe or '—':16s} statut={v.statut.value}")
            if len(jamais) > 12:
                print(f"      … et {len(jamais) - 12} autre(s)")

        maintenant = now_local()
        seuil = timedelta(minutes=args.muet_min)
        muets = [v for v in vehs if v.last_event_at is not None
                 and (maintenant - v.last_event_at) > seuil]
        muets.sort(key=lambda v: v.last_event_at)
        print(f"\n    Camions sans point GPS depuis > {args.muet_min:.0f} min : "
              f"{len(muets)} / {len(vehs)}")
        if muets:
            plus_recent = muets[-1].last_event_at
            plus_ancien = muets[0].last_event_at
            print(f"      fenêtre des derniers points : "
                  f"{plus_ancien:%d/%m %H:%M} → {plus_recent:%d/%m %H:%M}")
            par_p: dict[str, int] = {}
            for v in muets:
                par_p[v.plateforme_gps or "?"] = par_p.get(v.plateforme_gps or "?", 0) + 1
            print("      muets par portail : " + " · ".join(
                f"{p} {n}" for p, n in sorted(par_p.items())))

        print(f"\n    Journées ({args.jours} derniers jours) — croisement "
              f"mouvements GPS / contenu d'archive :")
        aujour = maintenant.date()
        for offset in range(args.jours, 0, -1):
            jour = aujour - timedelta(days=offset)
            archives = db.query(HistoriqueJournalier).filter(
                HistoriqueJournalier.date_jour == jour).all()
            trajs: dict[str, int] = {}
            for h in archives:
                trajs[h.vehicule_id] = len((h.donnees or {}).get("trajets") or [])

            mouvements: dict[str, int] = {}
            if not args.rapide:
                debut = datetime.combine(jour, dtime.min)
                fin = debut + timedelta(days=1)
                try:
                    lignes = db.query(EvenementGPS.vehicule_id,
                                      func.count(EvenementGPS.id)).filter(
                        EvenementGPS.horodatage >= debut,
                        EvenementGPS.horodatage < fin,
                        EvenementGPS.vitesse > 3.0).group_by(
                        EvenementGPS.vehicule_id).all()
                    mouvements = {vid: n for vid, n in lignes}
                except Exception as e:
                    print(f"      ⚠️  mouvements non mesurés pour {jour:%d/%m} "
                          f"({type(e).__name__})")

            trous = sorted(v for v, n in mouvements.items()
                           if n >= 5 and trajs.get(v, 0) == 0)
            plaques = {}
            if trous:
                plaques = {v.id: v.plaque for v in db.query(Vehicule).filter(
                    Vehicule.id.in_(trous[:40])).all()}

            audits: list[str] = []
            try:
                debut_audit = datetime.combine(jour, dtime.min)
                fin_audit = debut_audit + timedelta(days=2)
                for a in db.query(AuditLog).filter(
                        AuditLog.action.in_(ACTIONS_REPRISE),
                        AuditLog.date_heure >= debut_audit,
                        AuditLog.date_heure < fin_audit).all():
                    # un audit de minuit est horodaté le LENDEMAIN du jour qu'il
                    # clôture : on le rattache par son champ `jour`, sinon par
                    # sa nature (cycle de minuit) et sa date.
                    texte = json.dumps(a.details or {}, ensure_ascii=False)
                    if jour.isoformat() in texte or (
                            a.action.startswith("cycle_minuit")
                            and a.date_heure.date() == jour + timedelta(days=1)):
                        audits.append(a.action)
            except Exception:
                pass
            compte: dict[str, int] = {}
            for a in audits:
                compte[a] = compte.get(a, 0) + 1

            etat = "⚠️ À REPRENDRE" if (trous or compte) else "✅"
            print(f"      {etat}  {jour:%d/%m}  archives={len(archives)} · "
                  f"sans trajet={sum(1 for n in trajs.values() if n == 0)}"
                  + (f" · a bougé sans trajet={len(trous)}" if mouvements else ""))
            if compte:
                print("              audits : " + " · ".join(
                    f"{a}×{n}" for a, n in sorted(compte.items())))
            for vid in trous[:6]:
                print(f"              └ {plaques.get(vid, vid)[:12]:12s} a roulé "
                      f"ce jour-là, archive sans trajet "
                      f"({mouvements[vid]} points en mouvement)")
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Bilan de collecte et de flotte (v148 v2)")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--jours", type=int, default=7)
    ap.add_argument("--sync", action="store_true",
                    help="force un cycle de collecte (l'endpoint est synchrone : "
                         "peut durer plusieurs minutes)")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="délai maximal du cycle forcé, en secondes (défaut 900)")
    ap.add_argument("--rapide", action="store_true",
                    help="saute l'analyse des mouvements GPS par journée")
    ap.add_argument("--muet-min", type=float, default=60.0)
    args = ap.parse_args()

    print("=" * 78)
    print("BILAN DE COLLECTE ET DE FLOTTE — plateforme LSS Tracking")
    print(f"date : {datetime.now():%d/%m/%Y %H:%M} · API : {args.url}")
    print("=" * 78)

    print("\n[1] ÉTAT DE SANTÉ")
    statut, d1 = appel(args.url, "/api/sante")
    a_jour = False
    if statut is None:
        print(f"    ⚠️  API injoignable ({d1.get('erreur')}) — lancez la "
              f"plateforme ou corrigez --url")
    else:
        a_jour = afficher_sante(d1, "    /api/sante :")

    if args.sync and statut:
        print("\n[2] CYCLE DE COLLECTE FORCÉ (réponse réelle des portails)")
        debut = time.monotonic()
        st2, d2 = appel(args.url, "/api/sante/sync", "POST", timeout=args.timeout)
        ecoule = time.monotonic() - debut
        if st2 is None:
            print(f"    ⚠️  pas de réponse après {ecoule:.0f}s "
                  f"({d2.get('erreur')})")
            print("        Le cycle tourne probablement ENCORE côté serveur "
                  "(l'endpoint est synchrone) : relancez ce bilan SANS --sync "
                  "dans quelques minutes pour lire son résultat dans [1].")
        else:
            print(f"    HTTP {st2} en {ecoule:.0f}s")
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
    elif statut:
        print("\n[2] CYCLE FORCÉ : non demandé (option `--sync`) — l'état [1] "
              "reflète déjà la dernière tentative réelle.")

    section_base(args)

    print("\n" + "=" * 78)
    if a_jour:
        print("LECTURE : `sources_en_echec` vide + statut COLLECTE_OK = toutes "
              "les sources collectent.")
    else:
        print("ACTION IMMÉDIATE : le service n'exécute pas le code v148 — le "
              "redémarrer, puis relancer ce bilan.")
    print("Une journée marquée ⚠️ À REPRENDRE peut être recalculée (écriture) :")
    print("  POST /api/suivi/recalculer-archive?date_jour=AAAA-MM-JJ   (admin)")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
