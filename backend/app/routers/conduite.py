# -*- coding: utf-8 -*-
"""Module « Conduite » — §0septies B3 (arbitrages LSS du 20/08/2026).

Carnet de conduite construit UNIQUEMENT sur les données officielles des
portails, rangées sur les trajets validés au Niveau 2 :
  · MZoneX      : compteurs natifs par trajet (vitesse, freinage brusque,
                  accélération, ralenti, sur-régime, autres) + vitesse max ;
  · CamtrackPro : vitesse max + ralenti moteur (rapport id 9) — les compteurs
                  détaillés (gabarits 5/17) sont une extension ultérieure.

Deux vues arbitrées :
  1. par chauffeur  — trajets, km, infractions par type, infractions/100 km ;
  2. « trajets noirs » — le détail des trajets à infractions.

Aucune colonne n'est ajoutée aux exports figés 25/51 (§9) : ce module ne
touche ni la grille, ni les exports, ni l'archive. Lecture seule (TOUS rôles).
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (StatutValidationTrajet, SuiviJournalier, Trajet,
                      Vehicule)
from ..security import TOUS, require_roles
from ..serializers import iso

router = APIRouter(prefix="/api/conduite", tags=["conduite"])

_ECO = ("exc_vitesse", "exc_freinage", "exc_accel", "exc_ralenti",
        "exc_surregime", "exc_autres")
_LIBELLES = {"exc_vitesse": "Vitesse", "exc_freinage": "Freinage brusque",
             "exc_accel": "Accélération", "exc_ralenti": "Ralenti excessif",
             "exc_surregime": "Sur-régime", "exc_autres": "Autres"}


def _periode(du: str | None, au: str | None) -> tuple[date, date]:
    """Défaut : la semaine en cours (lundi → aujourd'hui)."""
    fin = date.fromisoformat(au) if au else date.today()
    debut = date.fromisoformat(du) if du else fin - timedelta(days=fin.weekday())
    if debut > fin:
        debut, fin = fin, debut
    return debut, fin


def _trajets_period(db: Session, debut: date, fin: date):
    """Trajets de la période : jamais les REJETÉS (manœuvres §7/invalides §2 —
    règle absolue) ; chaque ligne garde véhicule + chauffeur badge."""
    rows = db.execute(
        select(Trajet, SuiviJournalier, Vehicule)
        .join(SuiviJournalier, Trajet.suivi_id == SuiviJournalier.id)
        .join(Vehicule, SuiviJournalier.vehicule_id == Vehicule.id)
        .where(SuiviJournalier.date_jour >= debut,
               SuiviJournalier.date_jour <= fin,
               Trajet.statut_validation != StatutValidationTrajet.REJETE)
        .order_by(SuiviJournalier.date_jour, Vehicule.plaque,
                  Trajet.heure_debut)).all()
    return rows


def _total_exc(t: Trajet) -> int:
    return sum(int(getattr(t, k) or 0) for k in _ECO)


@router.get("/chauffeurs")
def carnet_chauffeurs(du: str | None = None, au: str | None = None,
                      db: Session = Depends(get_db),
                      _=Depends(require_roles(*TOUS))):
    """Vue 1 — par chauffeur (badge portail ; « (Sans badge) » regroupe les
    trajets publiés sans conducteur — traçabilité B5 côté CamtrackPro)."""
    debut, fin = _periode(du, au)
    lignes: dict[str, dict] = {}
    for t, s, v in _trajets_period(db, debut, fin):
        cle = t.conducteur_badge_id or ("sans-badge:" + (t.badge_ecarte or ""))
        nom = t.conducteur_badge \
            or (f"(Clé de service : {t.badge_ecarte})" if t.badge_ecarte
                else "(Sans badge)")
        l = lignes.setdefault(cle, {
            "conducteur_id": t.conducteur_badge_id, "chauffeur": nom,
            "sans_badge": t.conducteur_badge_id is None,
            "trajets": 0, "km": 0.0, "v_max": None, "ralenti_s": 0,
            "total": 0, "pour_100km": None, "vehicules": set(),
            **{k: 0 for k in _ECO}})
        l["trajets"] += 1
        l["km"] = round(l["km"] + (t.distance_km or 0.0), 3)
        if t.v_max is not None:
            l["v_max"] = max(l["v_max"] or 0.0, t.v_max)
        l["ralenti_s"] += int(t.ralenti_s or 0)
        for k in _ECO:
            l[k] += int(getattr(t, k) or 0)
        l["total"] += _total_exc(t)
        if v:
            l["vehicules"].add(v.plaque)
    sortie = []
    for l in lignes.values():
        l2 = dict(l)
        l2["vehicules"] = sorted(l["vehicules"])
        l2["km"] = round(l2["km"], 1)
        l2["pour_100km"] = (round(100.0 * l2["total"] / l2["km"], 1)
                            if l2["km"] > 0 else None)
        sortie.append(l2)
    sortie.sort(key=lambda x: (x["sans_badge"], -x["total"],
                               -(x["pour_100km"] or 0)))
    return {"du": debut.isoformat(), "au": fin.isoformat(),
            "libelles": _LIBELLES, "chauffeurs": sortie}


@router.get("/trajets")
def trajets_noirs(du: str | None = None, au: str | None = None,
                  min_total: int = 1,
                  db: Session = Depends(get_db),
                  _=Depends(require_roles(*TOUS))):
    """Vue 2 — « trajets noirs » : détail des trajets dont le total
    d'infractions ≥ min_total (1 par défaut)."""
    debut, fin = _periode(du, au)
    items = []
    for t, s, v in _trajets_period(db, debut, fin):
        total = _total_exc(t)
        if total < max(0, min_total):
            continue
        items.append({
            "date": s.date_jour.isoformat(), "plaque": v.plaque,
            "debut": iso(t.heure_debut), "fin": iso(t.heure_fin),
            "distance_km": t.distance_km,
            "chauffeur": t.conducteur_badge
                         or (f"(Clé de service : {t.badge_ecarte})"
                             if t.badge_ecarte else "(Sans badge)"),
            "source": t.source_plateforme,
            "v_max": t.v_max, "ralenti_s": t.ralenti_s,
            **{k: int(getattr(t, k) or 0) for k in _ECO},
            "total": total})
    items.sort(key=lambda x: (-x["total"], x["debut"]))
    return {"du": debut.isoformat(), "au": fin.isoformat(),
            "libelles": _LIBELLES, "trajets": items}
