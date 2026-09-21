"""v1.49 — VÉRIFICATEUR DES HEURES DE DÉPART (lecture seule, aucun réseau).

Répond à la question : « cette heure de départ affichée, d'où vient-elle ? »

Pour une date donnée, il sort pour chaque camion :

  1. la journée telle que l'ÉCRAN l'affiche (heure de départ, TCC/TCJ/TTJ,
     état des lignes : finies / en cours) ;
  2. la PROVENANCE de chaque ligne (source portail, statut, distance officielle) ;
  3. les AUDITS de la journée (`trajet.ouverture_rattrapage` = ligne ouverte par
     l'arbitrage R2 « camion en route sans ligne », `trajet.rejet_distance` =
     manœuvre < seuil, `trajet.reactivation`…) : c'est là qu'on voit une heure
     de départ fabriquée ;
  4. le DERNIER signal réel du camion (`last_event_at`, `last_vitesse`) et l'âge
     de ce signal — un signal périmé ne doit jamais produire de ligne.

Usage (LECTURE SEULE, sur la base de production comme sur une copie) :

    python3 verifier_heures_depart.py                     # aujourd'hui
    python3 verifier_heures_depart.py --date 2026-09-17
    python3 verifier_heures_depart.py --plaque 2746TCC
    python3 verifier_heures_depart.py --suspects          # seulement les lignes douteuses

Sortie : un tableau + une liste « À VÉRIFIER » (ligne en cours dont le début
n'est précédé d'aucun événement Début du trajet/Reprise, ou ligne ouverte par
R2, ou signal périmé). Aucune écriture en base.
"""
from __future__ import annotations

import os
# v1.50 — outil de CONTRÔLE en lecture seule : on n'ouvre pas de transaction
# d'écriture (sinon ses longues analyses prendraient le verrou de la base et
# gêneraient la collecte du service en cours d'exécution).
os.environ.setdefault("LSS_SQLITE_IMMEDIATE", "0")

import argparse
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select                                    # noqa: E402

from app.config import now_local                                 # noqa: E402
from app.database import SessionLocal                            # noqa: E402
from app.models import (AuditLog, EvenementGPS, SuiviJournalier,  # noqa: E402
                        Trajet, TypeEvenement, Vehicule)
from app.serializers import journee_suivi, s_suivi               # noqa: E402


def _h(d) -> str:
    return d.strftime("%H:%M:%S") if d else "—"


def _mn(s) -> str:
    s = int(s or 0)
    return f"{s // 3600}:{(s % 3600) // 60:02d}"


def analyser(jour: date, plaque: str | None, seulement_suspects: bool) -> int:
    db = SessionLocal()
    maintenant = now_local()
    suspects = 0
    try:
        suivis = db.scalars(select(SuiviJournalier).where(
            SuiviJournalier.date_jour == jour)).all()
        suivis.sort(key=lambda s: (s.vehicule.plaque if s.vehicule else ""))
        print(f"\n═══ Heures de départ du {jour.isoformat()} "
              f"(maintenant {maintenant:%H:%M:%S}) ═══")
        for s in suivis:
            v = s.vehicule
            if plaque and (not v or v.plaque.upper() != plaque.upper()):
                continue
            ligne = journee_suivi(s)
            trajets = sorted(s.trajets, key=lambda t: t.numero)
            evs = db.scalars(select(EvenementGPS).where(
                EvenementGPS.vehicule_id == (v.id if v else ""),
                EvenementGPS.horodatage >= datetime.combine(jour, datetime.min.time()),
                EvenementGPS.horodatage < datetime.combine(jour, datetime.min.time())
                + timedelta(days=1)).order_by(EvenementGPS.horodatage)).all()
            debuts = [e for e in evs if e.type_evenement in (
                TypeEvenement.DEBUT_MOUVEMENT, TypeEvenement.REPRISE)]
            audits = db.scalars(select(AuditLog).where(
                AuditLog.entite == "trajet",
                AuditLog.details["jour"].as_string() == jour.isoformat())
            ).all() if False else [a for a in db.scalars(select(AuditLog).where(
                AuditLog.entite == "trajet")).all()
                if (a.details or {}).get("jour") == jour.isoformat()
                and (v is None or (a.details or {}).get("plaque") == v.plaque)]

            rig = []
            depart = trajets[0].heure_debut if trajets else None
            if depart is not None and not any(
                    abs((e.horodatage - depart).total_seconds()) <= 900
                    for e in debuts):
                rig.append("départ sans événement « Début du trajet/Reprise » "
                           "à ±15 min")
            if any(a.action == "trajet.ouverture_rattrapage" for a in audits):
                rig.append("ligne ouverte par R2 (aucun événement n'a ouvert "
                           "cette ligne)")
            age = ((maintenant - v.last_event_at).total_seconds()
                   if (v and v.last_event_at) else None)
            if any(t.heure_fin is None and t.statut_validation !=
                   Trajet.statut_validation.property.columns[0].type.enum_class.REJETE
                   for t in trajets) and age is not None and age > 1800:
                rig.append(f"ligne « en cours » alors que le dernier signal a "
                           f"{int(age // 60)} min (boîtier muet)")

            if seulement_suspects and not rig:
                continue
            if rig:
                suspects += 1
            print(f"\n  {v.plaque if v else '?'}  ({v.plateforme_gps if v else '?'})"
                  f"  départ affiché : {_h(depart)}"
                  f"  |  TCC {_mn(s.tcc_s)} · TCJ {_mn(ligne.tcj_s)}"
                  f" · TTJ {_mn(ligne.ttj_s)}")
            if v:
                print(f"     dernier signal : {_h(v.last_event_at)}"
                      f" ({'—' if age is None else f'{int(age // 60)} min'})"
                      f" · vitesse publiée {v.last_vitesse or 0:.0f} km/h")
            print(f"     {len(trajets)} ligne(s) en base, "
                  f"{len(ligne.lignes)} à l'écran, {len(evs)} événement(s) GPS, "
                  f"{len(debuts)} début(s) de mouvement")
            for t in trajets:
                print(f"       · n°{t.numero:<2} {_h(t.heure_debut)} → "
                      f"{_h(t.heure_fin):<9} {t.statut_source.value:<10} "
                      f"{t.statut_validation.value:<9} "
                      f"{t.source_plateforme or '?':<12} "
                      f"{(f'{t.distance_km:.3f} km' if t.distance_km is not None else '—')}")
            for a in audits:
                det = a.details or {}
                print(f"       ↳ audit {a.action} : début "
                      f"{det.get('debut') or det.get('heure') or '—'}"
                      + (f" · {det.get('raison')}" if det.get("raison") else "")
                      + (f" · règle {str(det.get('regle'))[:70]}"
                         if det.get("regle") else ""))
            for r in rig:
                print(f"     ⚠️  {r}")
        print(f"\n  {suspects} camion(s) signalé(s) — lecture seule, "
              f"aucune écriture.\n")
    finally:
        db.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Vérificateur des heures de départ (v1.49)")
    ap.add_argument("--date", default=None,
                    help="journée à inspecter (AAAA-MM-JJ, défaut : aujourd'hui)")
    ap.add_argument("--plaque", default=None, help="ne traiter qu'un camion")
    ap.add_argument("--suspects", action="store_true",
                    help="n'afficher que les lignes douteuses")
    a = ap.parse_args()
    jour = (date.fromisoformat(a.date) if a.date else now_local().date())
    return analyser(jour, a.plaque, a.suspects)


if __name__ == "__main__":
    sys.exit(main())
