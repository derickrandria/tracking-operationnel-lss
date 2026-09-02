"""Module 1 — Dashboard : KPI, cartes, graphiques (lecture agrégée §6.1)."""
from datetime import date, datetime, timedelta
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import now_local
from ..database import get_db
from ..models import (Alerte, Conducteur, HistoriqueJournalier, Infraction,
                      Mission, StatutAlerte, StatutCamion, StatutMission,
                      StatutVehicule, SuiviJournalier, Vehicule)
from ..security import TOUS, require_roles

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    aujourd = now_local().date()
    debut_mois = aujourd.replace(day=1)

    suivis = db.scalars(select(SuiviJournalier)
                        .where(SuiviJournalier.date_jour == aujourd)).all()
    par_statut = {"LIBRE": 0, "VIDE": 0, "CHARGÉ": 0, "NON_RENSEIGNÉ": 0}
    for s in suivis:
        cle = s.statut_camion.value if s.statut_camion else "NON_RENSEIGNÉ"
        par_statut[cle] = par_statut.get(cle, 0) + 1

    camions_actifs = db.scalar(select(func.count(Vehicule.id))
                               .where(Vehicule.statut == StatutVehicule.ACTIF)) or 0
    missions_jour = db.scalars(select(Mission).where(Mission.date_jour == aujourd)).all()
    missions_en_cours = sum(1 for m in missions_jour if m.statut == StatutMission.EN_COURS)
    missions_retardees = sum(1 for m in missions_jour if m.statut == StatutMission.RETARDEE)
    missions_terminees = sum(1 for m in missions_jour if m.statut == StatutMission.TERMINEE)

    # v3 AM-5/C3 (22/08/2026) : seules les infractions d'une plateforme EXTERNE
    # comptent — comme l'onglet Infractions (vitre en attente de source : 0)
    infractions_jour = db.scalar(select(func.count(Infraction.id))
                                 .where(Infraction.date_jour == aujourd,
                                        Infraction.exterieure.is_(True))) or 0
    infractions_mois = db.scalar(select(func.count(Infraction.id))
                                 .where(Infraction.date_jour >= debut_mois,
                                        Infraction.exterieure.is_(True))) or 0
    alertes_non_vues = db.scalar(select(func.count(Alerte.id))
                                 .where(Alerte.statut == StatutAlerte.NOUVELLE)) or 0

    avec_depart = [s for s in suivis if s.tcj_s]
    tcj_moyen = int(sum(s.tcj_s for s in avec_depart) / len(avec_depart)) if avec_depart else 0
    avec_pauses = [s for s in suivis if s.total_pause_s]
    pause_moyenne = int(sum(s.total_pause_s for s in avec_pauses) / len(avec_pauses)) if avec_pauses else 0
    km_total = round(sum(s.km_parcourus or 0 for s in suivis), 1)

    # ---- top chauffeurs : les moins d'infractions avec le plus de km ------
    inf_par_chauffeur = dict(db.execute(
        select(Infraction.conducteur_id, func.count(Infraction.id))
        .where(Infraction.date_jour >= debut_mois, Infraction.conducteur_id.isnot(None),
               Infraction.exterieure.is_(True))
        .group_by(Infraction.conducteur_id)).all())
    km_par_chauffeur = defaultdict(float)
    miss_par_chauffeur = defaultdict(int)
    for s in suivis:
        if s.conducteur_id:
            km_par_chauffeur[s.conducteur_id] += s.km_parcourus or 0
    for m in missions_jour:
        if m.conducteur_id and m.statut == StatutMission.TERMINEE:
            miss_par_chauffeur[m.conducteur_id] += 1
    candidats = [(cid, km) for cid, km in km_par_chauffeur.items() if km > 1]
    candidats.sort(key=lambda t: (inf_par_chauffeur.get(t[0], 0), -miss_par_chauffeur.get(t[0], 0), -t[1]))
    top_chauffeurs = []
    for cid, km in candidats[:5]:
        c = db.get(Conducteur, cid)
        if c:
            top_chauffeurs.append({
                "id": cid, "prenom_usuel": c.prenom_usuel, "nom_prenom": c.nom_prenom,
                "km_jour": round(km, 1), "missions_terminees": miss_par_chauffeur.get(cid, 0),
                "infractions_mois": inf_par_chauffeur.get(cid, 0)})

    # ---- top véhicules : kilométrage et disponibilité ---------------------
    top_vehicules = sorted(
        ({"plaque": s.vehicule.plaque if s.vehicule else "?",
          "km_jour": round(s.km_parcourus or 0, 1),
          "statut_camion": s.statut_camion.value if s.statut_camion else None}
         for s in suivis if (s.km_parcourus or 0) > 1),
        key=lambda v: -v["km_jour"])[:5]

    # ---- graphiques --------------------------------------------------------
    depots = defaultdict(int)
    produits = defaultdict(int)
    for s in suivis:
        if s.depot_recepteur:
            depots[s.depot_recepteur] += 1
        if s.produit:
            produits[s.produit] += 1

    inf_par_type = dict(db.execute(
        select(Infraction.type, func.count(Infraction.id))
        .where(Infraction.date_jour >= debut_mois, Infraction.exterieure.is_(True))
        .group_by(Infraction.type)).all())
    inf_par_type = {k.value if hasattr(k, "value") else k: v for k, v in inf_par_type.items()}

    # courbe : temps moyen de conduite journalier sur 30 jours (archives + jour en cours)
    debut_30 = aujourd - timedelta(days=29)
    archives = db.scalars(select(HistoriqueJournalier)
                          .where(HistoriqueJournalier.date_jour >= debut_30)).all()
    serie: dict[str, list] = defaultdict(list)
    for h in archives:
        d = h.donnees or {}
        if d.get("tcj_s"):
            serie[h.date_jour.isoformat()].append((d["tcj_s"], d.get("ttj_s") or 0))
    if avec_depart:
        serie[aujourd.isoformat()].extend((s.tcj_s, s.ttj_s) for s in avec_depart)
    conduite_30j = [{"date": j,
                     "tcj_moyen_s": int(sum(t[0] for t in v) / len(v)),
                     "ttj_moyen_s": int(sum(t[1] for t in v) / len(v))}
                    for j, v in sorted(serie.items())]

    # heatmap : densité d'infractions heure × jour de la semaine (30 jours)
    infrs = db.scalars(select(Infraction).where(Infraction.date_jour >= debut_30)).all()
    heat = defaultdict(int)
    for i in infrs:
        heat[(i.heure.hour, i.date_jour.weekday())] += 1
    heatmap = [[h, j, heat[(h, j)]] for h in range(24) for j in range(7) if heat[(h, j)]]

    # ---- positions temps réel pour la carte -------------------------------
    positions = []
    statut_par_vehicule = {s.vehicule_id: (s.statut_camion.value if s.statut_camion else None)
                           for s in suivis}
    conducteur_par_vehicule = {s.vehicule_id: s.conducteur.prenom_usuel if s.conducteur else None
                               for s in suivis}
    for v in db.scalars(select(Vehicule).where(Vehicule.last_lat.isnot(None))).all():
        positions.append({
            "vehicule_id": v.id, "plaque": v.plaque,
            "lat": v.last_lat, "lng": v.last_lng,
            "vitesse": v.last_vitesse, "adresse": v.last_adresse,
            "moteur": v.moteur_on, "maj": v.last_event_at.isoformat() if v.last_event_at else None,
            "statut_camion": statut_par_vehicule.get(v.id),
            "conducteur": conducteur_par_vehicule.get(v.id),
        })

    return {
        "kpis": {
            "camions_actifs": camions_actifs,
            "camions_suivis": len(suivis),
            "libres": par_statut.get("LIBRE", 0),
            "vides": par_statut.get("VIDE", 0),
            "charges": par_statut.get("CHARGÉ", 0),
            "non_renseignes": par_statut.get("NON_RENSEIGNÉ", 0),
            "missions_en_cours": missions_en_cours,
            "missions_retardees": missions_retardees,
            "missions_terminees": missions_terminees,
            "infractions_jour": infractions_jour,
            "infractions_mois": infractions_mois,
            "alertes_non_vues": alertes_non_vues,
            "km_total_jour": km_total,
            "tcj_moyen_s": tcj_moyen,
            "pause_moyenne_s": pause_moyenne,
        },
        "top_chauffeurs": top_chauffeurs,
        "top_vehicules": top_vehicules,
        "charts": {
            "statuts": [{"label": k, "value": v} for k, v in par_statut.items()],
            "depots": [{"label": k, "value": v} for k, v in sorted(depots.items())],
            "produits": [{"label": k, "value": v} for k, v in sorted(produits.items())],
            "infractions_par_type": [{"label": k, "value": v} for k, v in inf_par_type.items()],
            "conduite_30j": conduite_30j,
            "heatmap": heatmap,
        },
        "positions": positions,
    }


@router.get("/recherche")
def recherche_globale(q: str, db: Session = Depends(get_db),
                      _=Depends(require_roles(*TOUS))):
    """Recherche globale multi-modules (§12)."""
    motif = f"%{q.strip()}%"
    conducteurs = db.scalars(select(Conducteur).where(or_(
        Conducteur.nom_prenom.ilike(motif), Conducteur.prenom_usuel.ilike(motif),
        Conducteur.matricule.ilike(motif))).limit(6)).all()
    vehicules = db.scalars(select(Vehicule).where(or_(
        Vehicule.plaque.ilike(motif), Vehicule.description.ilike(motif))).limit(6)).all()
    alertes = db.scalars(select(Alerte).where(Alerte.message.ilike(motif))
                         .order_by(Alerte.date_heure.desc()).limit(6)).all()
    infractions = db.scalars(select(Infraction).join(Vehicule, Infraction.vehicule_id == Vehicule.id)
                             .where(Vehicule.plaque.ilike(motif))
                             .order_by(Infraction.date_jour.desc()).limit(6)).all()
    return {
        "conducteurs": [{"id": c.id, "prenom_usuel": c.prenom_usuel,
                         "nom_prenom": c.nom_prenom, "matricule": c.matricule} for c in conducteurs],
        "vehicules": [{"id": v.id, "plaque": v.plaque, "description": v.description} for v in vehicules],
        "alertes": [{"id": a.id, "message": a.message,
                     "date_heure": a.date_heure.isoformat()} for a in alertes],
        "infractions": [{"id": i.id, "plaque": i.vehicule.plaque if i.vehicule else None,
                         "type": i.type.value, "date_jour": i.date_jour.isoformat()} for i in infractions],
    }
