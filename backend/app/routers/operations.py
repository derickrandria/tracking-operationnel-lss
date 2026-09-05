"""Module 2 — Suivi Journalier (cœur du système) et Module 3 — Missions."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import now_local
from ..database import get_db
from ..engine import (PUBLISH_ENABLED, _formater_code_mission, appliquer_champs_suivi,
                      ensure_suivi, ensure_suivis_du_jour, get_seuils,
                      initialiser_ou_maj_mission, prefill_positions_gps)
from ..models import (Alerte, Conducteur, Infraction, Mission, StatutAlerte,
                      StatutCamion, StatutMission, SuiviJournalier, TypeAlerte,
                      Vehicule, uid)
from ..security import ECRITURE, TOUS, audit, require_roles
from ..serializers import iso, s_alerte, s_mission, s_suivi

router = APIRouter(prefix="/api", tags=["operations"])


def _jour(date_param: str | None) -> date:
    if not date_param:
        return now_local().date()
    return date.fromisoformat(date_param)


# ============================== SUIVI JOURNALIER ==============================
def _masquer_tcc_si_jour_passe(lignes: list[dict], jour: date) -> list[dict]:
    """§0vicies decies N1 (31/08/2026) — le TCC (chrono temps réel, remis à
    zéro après toute pause ≥ 30 min) n'a pas de sens sur une journée terminée :
    cellules « 0:00 » dès que le jour consulté/exporté n'est pas le jour en
    cours. Masquage à la lecture seulement — `tcc_s` reste stocké en base."""
    if jour >= now_local().date():
        return lignes
    for l in lignes:
        l["tcc_s"] = 0
    return lignes


@router.get("/suivi")
def liste_suivi(date: str | None = None, db: Session = Depends(get_db),
                _=Depends(require_roles(*TOUS))):
    jour = _jour(date)
    if jour == now_local().date():
        ensure_suivis_du_jour(db, jour)  # couvre les véhicules ajoutés en cours de journée
    suivis = db.scalars(
        select(SuiviJournalier)
        .options(selectinload(SuiviJournalier.trajets))
        .where(SuiviJournalier.date_jour == jour)
        .join(Vehicule, SuiviJournalier.vehicule_id == Vehicule.id)
        .where(Vehicule.statut == "ACTIF")
        .order_by(Vehicule.plaque)).all()
    seuils = get_seuils(db)
    lignes = _masquer_tcc_si_jour_passe(
        [s_suivi(s, seuils) for s in suivis], jour)
    return {"date": jour.isoformat(), "seuils": seuils, "lignes": lignes}


class SuiviPatch(BaseModel):
    conducteur_id: str | None = None
    situation: str | None = None
    statut_camion: str | None = None
    depot_recepteur: str | None = None
    distributeur: str | None = None
    produit: str | None = None
    numero_ot: str | None = None
    emplacement_j_moins_1: str | None = None
    position_08h: str | None = None
    position_10h: str | None = None
    position_12h: str | None = None
    position_14h: str | None = None
    position_16h: str | None = None
    position_18h: str | None = None
    # §0vicies decies N2 (31/08/2026) — relevés du soir (auto + éditables)
    position_20h: str | None = None
    position_22h: str | None = None


@router.patch("/suivi/{sid}")
def modifier_suivi(sid: str, data: SuiviPatch, db: Session = Depends(get_db),
                   user=Depends(require_roles(*ECRITURE))):
    s = db.get(SuiviJournalier, sid)
    if s is None:
        raise HTTPException(404, "Ligne de suivi introuvable")
    if s.date_jour != now_local().date():
        raise HTTPException(409, "Seule la journée en cours est modifiable (voir Historique)")
    champs = {k: v for k, v in data.model_dump(exclude_unset=True).items()}
    diffs = appliquer_champs_suivi(db, s, champs)  # déclenche la synchro §9
    if diffs:
        audit(db, user, "suivi.modification", "suivi", sid, diffs)
        db.commit()
    return s_suivi(s, get_seuils(db))


CHAMPS_SAISIE = set(SuiviPatch.model_fields.keys())


class SuiviLignePatch(BaseModel):
    id: str
    champs: dict[str, str | None]


class SuiviBatchPatch(BaseModel):
    """Addendum v1.1 §2 — sauvegarde automatique : lot de modifications
    (debounce 1-2 s + synchronisation de sécurité toutes les 60 s).
    Chaque ligne est traitée comme une transaction atomique ; en cas de
    conflit, règle « dernière écriture gagne » (horodatage serveur)."""
    modifications: list[SuiviLignePatch]


@router.patch("/suivi")
def modifier_suivi_batch(data: SuiviBatchPatch, db: Session = Depends(get_db),
                         user=Depends(require_roles(*ECRITURE))):
    aujourd = now_local().date()
    seuils = get_seuils(db)
    lignes_maj, ignores = [], 0
    for mod in data.modifications[:300]:
        s = db.get(SuiviJournalier, mod.id)
        if s is None or s.date_jour != aujourd:
            ignores += 1
            continue
        champs = {k: v for k, v in mod.champs.items() if k in CHAMPS_SAISIE}
        if not champs:
            continue
        diffs = appliquer_champs_suivi(db, s, champs)  # synchro §9 + last-write-wins
        if diffs:
            audit(db, user, "suivi.autosave", "suivi", s.id, diffs)
        lignes_maj.append(s_suivi(s, seuils))
    if lignes_maj:
        db.commit()
    return {"lignes": lignes_maj, "ignores": ignores,
            "serveur_heure": now_local().strftime("%H:%M:%S")}


# ------------------- exports Suivi Journalier (Addendum v1.1 §3) -------------------
def _suivis_filtres(db: Session, jour: date, statut: str | None, q: str | None) -> list[dict]:
    """Lignes de suivi du jour filtrées EXACTEMENT comme à l'écran (§3.3 :
    l'export respecte les filtres actifs date / statut / recherche)."""
    if jour == now_local().date():
        ensure_suivis_du_jour(db, jour)
    suivis = db.scalars(
        select(SuiviJournalier)
        .options(selectinload(SuiviJournalier.trajets))
        .where(SuiviJournalier.date_jour == jour)
        .join(Vehicule, SuiviJournalier.vehicule_id == Vehicule.id)
        .order_by(Vehicule.plaque)).all()
    seuils = get_seuils(db)
    # §0vicies decies N1 — exports d'un jour passé : TCC masqué « 0:00 »
    lignes = _masquer_tcc_si_jour_passe([s_suivi(s, seuils) for s in suivis], jour)
    if statut:
        lignes = [l for l in lignes if (l.get("statut_camion") or "—") == statut]
    if q and q.strip():
        motif = q.strip().lower()
        lignes = [l for l in lignes
                  if motif in (l.get("plaque") or "").lower()
                  or motif in (l.get("description") or "").lower()
                  or motif in ((l.get("conducteur") or {}).get("prenom_usuel") or "").lower()
                  or motif in ((l.get("conducteur") or {}).get("nom_prenom") or "").lower()]
    return lignes


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/suivi/export.xlsx")
def export_suivi_xlsx(date: str | None = None, statut: str | None = None,
                      q: str | None = None, mode: str = "detail",
                      db: Session = Depends(get_db),
                      user=Depends(require_roles(*TOUS))):
    from ..exporters import export_suivi_excel
    jour = _jour(date)
    lignes = _suivis_filtres(db, jour, statut, q)
    detail = mode != "compact"
    audit(db, user, "suivi.export_excel", "suivi", str(jour),
          {"mode": "detail" if detail else "compact", "statut": statut,
           "recherche": q, "nb_lignes": len(lignes)})
    db.commit()
    contenu = export_suivi_excel(jour.strftime("%d/%m/%Y"), lignes, detail,
                                 utilisateur=user.nom_complet)
    nom = f"Suivi_Journalier_{jour.isoformat()}.xlsx"
    return Response(contenu, media_type=XLSX_MIME,
                    headers={"Content-Disposition": f"attachment; filename={nom}"})


@router.get("/suivi/export.pdf")
def export_suivi_pdf(date: str | None = None, statut: str | None = None,
                     q: str | None = None, mode: str = "detail",
                     db: Session = Depends(get_db),
                     user=Depends(require_roles(*TOUS))):
    from ..exporters import export_suivi_pdf
    jour = _jour(date)
    lignes = _suivis_filtres(db, jour, statut, q)
    detail = mode != "compact"
    audit(db, user, "suivi.export_pdf", "suivi", str(jour),
          {"mode": "detail" if detail else "compact", "statut": statut,
           "recherche": q, "nb_lignes": len(lignes)})
    db.commit()
    contenu = export_suivi_pdf(jour.strftime("%d/%m/%Y"), lignes, detail,
                               utilisateur=user.nom_complet)
    nom = f"Suivi_Journalier_{jour.isoformat()}.pdf"
    return Response(contenu, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={nom}"})


@router.post("/suivi/prefill")
def prefill_gps(date: str | None = None, db: Session = Depends(get_db),
                user=Depends(require_roles(*ECRITURE))):
    """Aide à la saisie Partie C : pré-remplit 08h…18h depuis le GPS."""
    jour = _jour(date)
    nb = prefill_positions_gps(db, jour)
    audit(db, user, "suivi.prefill_gps", "suivi", str(jour), {"positions_remplies": nb})
    db.commit()
    return {"positions_remplies": nb}


# ============================== MISSIONS ==============================
class MissionCreate(BaseModel):
    vehicule_id: str
    conducteur_id: str | None = None
    numero_ot: str | None = None
    distributeur: str | None = None
    produit: str | None = None
    depot_prevu: str | None = None
    date_jour: str | None = None


class MissionPatch(BaseModel):
    numero_ot: str | None = None
    distributeur: str | None = None
    produit: str | None = None
    depot_prevu: str | None = None
    depot_effectif: str | None = None
    est_deviee: bool | None = None
    motif_deviation: str | None = None
    statut: str | None = None  # EN_COURS, TERMINÉE, DÉVIÉE, RETARDÉE
    statut_camion_actuel: str | None = None  # LIBRE, VIDE, CHARGÉ
    validation_chargement: str | None = None
    validation_dechargement: str | None = None
    motif_invalidation: str | None = None


class MissionValiderChargement(BaseModel):
    horodatage: str | None = None


class MissionValiderDechargement(BaseModel):
    horodatage: str | None = None


class MissionInvaliderDechargement(BaseModel):
    motif: str
    commentaire: str | None = None


class MissionDeclarerDeviation(BaseModel):
    nouveau_depot: str
    motif: str | None = None


def _calculer_stats_missions(missions: list[Mission], db: Session) -> dict:
    total = len(missions)
    en_cours = [m for m in missions if m.statut == StatutMission.EN_COURS]
    terminees = sum(1 for m in missions if m.statut == StatutMission.TERMINEE)
    deviees = sum(1 for m in missions if m.statut == StatutMission.DEVIEE or getattr(m, "est_deviee", False))
    retardees = sum(1 for m in missions if m.statut == StatutMission.RETARDEE)

    nb_vide = sum(1 for m in en_cours if getattr(m, "statut_camion_actuel", "VIDE") == "VIDE")
    nb_charge = sum(1 for m in en_cours if getattr(m, "statut_camion_actuel", "VIDE") == "CHARGE")

    km_vide = sum(getattr(m, "km_vide", 0.0) or 0.0 for m in missions)
    km_charge = sum(getattr(m, "km_charge", 0.0) or 0.0 for m in missions)
    km_tot = sum(getattr(m, "kilometrage_total", 0.0) or (m.kilometrage or 0.0) or (getattr(m, "km_vide", 0.0) or 0.0) + (getattr(m, "km_charge", 0.0) or 0.0) for m in missions)

    # Infractions
    m_ids = [m.id for m in missions]
    if m_ids:
        nb_inf = db.scalar(
            select(func.count(Infraction.id))
            .where(Infraction.mission_id.in_(m_ids),
                   Infraction.validation != "INVALIDE")
        ) or 0
    else:
        nb_inf = 0

    return {
        "total": total,
        "en_cours": len(en_cours),
        "nb_en_cours_vide": nb_vide,
        "nb_en_cours_charge": nb_charge,
        "terminees": terminees,
        "deviees": deviees,
        "retardees": retardees,
        "km_vide": round(km_vide, 1),
        "km_charge": round(km_charge, 1),
        "km_total": round(km_tot, 1),
        "nb_infractions": nb_inf,
    }


def _recuperer_missions_filtrees(db: Session, date_debut: str | None,
                                 date_fin: str | None, statut: str | None,
                                 conducteur_id: str | None, vehicule_id: str | None,
                                 depot: str | None, distributeur: str | None,
                                 q: str | None) -> tuple[date, date, list[Mission]]:
    now = now_local()
    fin = date.fromisoformat(date_fin) if date_fin else now.date()
    debut = date.fromisoformat(date_debut) if date_debut else (fin - timedelta(days=30))

    query = select(Mission).where(Mission.date_jour >= debut, Mission.date_jour <= fin)

    if statut and statut != "TOUTES":
        try:
            st_enum = StatutMission(statut)
            query = query.where(Mission.statut == st_enum)
        except ValueError:
            pass

    if conducteur_id:
        query = query.where(Mission.conducteur_id == conducteur_id)
    if vehicule_id:
        query = query.where(Mission.vehicule_id == vehicule_id)
    if distributeur:
        query = query.where(Mission.distributeur == distributeur)
    if depot:
        depot_l = f"%{depot.lower()}%"
        query = query.where(func.lower(Mission.depot_prevu).like(depot_l) |
                            func.lower(Mission.depot_effectif).like(depot_l) |
                            func.lower(Mission.depot).like(depot_l))

    missions = db.scalars(query.order_by(Mission.date_jour.desc(), Mission.heure_debut.desc())).all()

    if q and q.strip():
        motif = q.strip().lower()
        missions = [
            m for m in missions
            if motif in (m.code_mission or "").lower()
            or motif in (m.numero_ot or "").lower()
            or (m.vehicule and motif in m.vehicule.plaque.lower())
            or (m.conducteur and (
                motif in (m.conducteur.nom_prenom or "").lower()
                or motif in (m.conducteur.prenom_usuel or "").lower()
                or motif in (m.conducteur.matricule or "").lower()
            ))
        ]

    return debut, fin, missions


@router.get("/missions")
def liste_missions(date_debut: str | None = None, date_fin: str | None = None,
                   statut: str | None = None, conducteur_id: str | None = None,
                   vehicule_id: str | None = None, depot: str | None = None,
                   distributeur: str | None = None, q: str | None = None,
                   date: str | None = None,  # compatibilité rétroactive
                   rattrapage: bool = False,
                   db: Session = Depends(get_db), user=Depends(require_roles(*TOUS))):
    if date and not date_debut and not date_fin:
        date_debut = date_fin = date

    if rattrapage:
        from ..engine import rattraper_missions_7j
        rattraper_missions_7j(db)

    debut, fin, missions = _recuperer_missions_filtrees(
        db, date_debut, date_fin, statut, conducteur_id, vehicule_id, depot, distributeur, q)

    stats = _calculer_stats_missions(missions, db)

    # Récupération en lot des infractions par mission
    m_ids = [m.id for m in missions]
    inf_counts = {}
    if m_ids:
        rows = db.execute(
            select(Infraction.mission_id, func.count(Infraction.id))
            .where(Infraction.mission_id.in_(m_ids), Infraction.validation != "INVALIDE")
            .group_by(Infraction.mission_id)
        ).all()
        inf_counts = {r[0]: r[1] for r in rows}

    return {
        "date_debut": debut.isoformat(),
        "date_fin": fin.isoformat(),
        "stats": stats,
        "missions": [s_mission(m, inf_counts.get(m.id, 0)) for m in missions],
    }


@router.post("/missions/rattrapage")
@router.get("/missions/rattrapage-7j")
def declencher_rattrapage_missions_7j(db: Session = Depends(get_db),
                                      user=Depends(require_roles(*TOUS))):
    """Exécute la collecte rétrospective et reconstruction des missions sur les 7 derniers jours (§3)."""
    from ..engine import rattraper_missions_7j
    stats = rattraper_missions_7j(db)
    audit(db, user, "mission.rattrapage_7j", "mission", "rattrapage_7j", stats)
    return {
        "statut": "OK",
        "message": "Collecte rétrospective et rattrapage sur 7 jours exécutés avec succès.",
        "stats": stats
    }


@router.post("/missions")
def creer_mission(data: MissionCreate, db: Session = Depends(get_db),
                  user=Depends(require_roles(*ECRITURE))):
    """Option A : Attribution d'un OT / Création de mission depuis l'UI."""
    vehicule = db.get(Vehicule, data.vehicule_id)
    if not vehicule:
        raise HTTPException(404, "Véhicule introuvable")

    jour = date.fromisoformat(data.date_jour) if data.date_jour else now_local().date()
    suivi = ensure_suivi(db, vehicule.id, jour)

    if data.conducteur_id:
        suivi.conducteur_id = data.conducteur_id

    m = initialiser_ou_maj_mission(
        db, suivi, vehicule,
        numero_ot=data.numero_ot,
        distributeur=data.distributeur,
        produit=data.produit,
        depot_prevu=data.depot_prevu,
    )
    audit(db, user, "mission.creation", "mission", m.id, {
        "plaque": vehicule.plaque, "ot": data.numero_ot, "produit": data.produit,
        "distributeur": data.distributeur, "depot": data.depot_prevu
    })
    db.commit()
    return s_mission(m)


@router.patch("/missions/{mid}")
def modifier_mission(mid: str, data: MissionPatch, db: Session = Depends(get_db),
                     user=Depends(require_roles(*ECRITURE))):
    """Mise à jour / Forçage manuel de statut d'une mission."""
    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404, "Mission introuvable")

    diffs = {}
    for cle, val in data.model_dump(exclude_unset=True).items():
        if val is None and cle not in ("motif_deviation", "depot_effectif"):
            continue
        if cle == "statut" and val:
            try:
                st_val = StatutMission(val)
                if m.statut != st_val:
                    diffs["statut"] = {"avant": m.statut.value, "apres": st_val.value}
                    m.statut = st_val
                    if st_val == StatutMission.TERMINEE:
                        m.heure_fin = m.heure_fin or now_local()
                        m.statut_camion_actuel = "LIBRE"
                        if m.heure_debut:
                            m.duree_s = int((m.heure_fin - m.heure_debut).total_seconds())
                        # Détachement du suivi actif si présent
                        suivi = db.scalar(select(SuiviJournalier).where(SuiviJournalier.mission_id == m.id))
                        if suivi:
                            suivi.statut_camion = StatutCamion.LIBRE
                            suivi.mission_id = None
            except ValueError:
                pass
            continue

        if cle == "statut_camion_actuel" and val:
            if m.statut_camion_actuel != val:
                diffs["statut_camion_actuel"] = {"avant": m.statut_camion_actuel, "apres": val}
                m.statut_camion_actuel = val
                if val == "CHARGE":
                    m.heure_chargement = m.heure_chargement or now_local()
                elif val == "LIBRE":
                    m.statut = StatutMission.TERMINEE
                    m.heure_fin = m.heure_fin or now_local()
                    if m.heure_debut:
                        m.duree_s = int((m.heure_fin - m.heure_debut).total_seconds())
                    suivi = db.scalar(select(SuiviJournalier).where(SuiviJournalier.mission_id == m.id))
                    if suivi:
                        suivi.statut_camion = StatutCamion.LIBRE
                        suivi.mission_id = None
            continue

        anc = getattr(m, cle, None)
        if anc != val:
            diffs[cle] = {"avant": anc, "apres": val}
            setattr(m, cle, val)

    if diffs:
        audit(db, user, "mission.modification", "mission", m.id, diffs)
        db.commit()
        if PUBLISH_ENABLED["on"]:
            from ..event_bus import publish
            publish("mission.update", s_mission(m))

    return s_mission(m)


@router.post("/missions/{mid}/valider-chargement")
def valider_chargement_mission(mid: str, data: MissionValiderChargement | None = None,
                               db: Session = Depends(get_db),
                               user=Depends(require_roles(*ECRITURE))):
    """Validation opérationnelle du chargement GRT."""
    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404, "Mission introuvable")

    ts_val = None
    if data and data.horodatage:
        try:
            ts_val = datetime.fromisoformat(data.horodatage)
        except ValueError:
            pass
    if not ts_val:
        for e in reversed(m.etapes or []):
            if e.get("etat") in ("ENTREE_GRT", "CHARGEMENT_EFFECTUE") and e.get("ts"):
                try:
                    ts_val = datetime.fromisoformat(e["ts"])
                    break
                except Exception:
                    pass
    if not ts_val:
        ts_val = m.heure_chargement or now_local()

    m.validation_chargement = "VALIDÉ"
    m.statut_camion_actuel = "CHARGE"
    m.heure_chargement = ts_val

    suivi = db.scalar(select(SuiviJournalier).where(SuiviJournalier.mission_id == m.id))
    if suivi:
        suivi.statut_camion = StatutCamion.CHARGE

    # Auto-résolution des alertes associées
    db.query(Alerte).filter(
        Alerte.vehicule_id == m.vehicule_id,
        Alerte.type.in_([TypeAlerte.VALIDATION_CHARGEMENT, TypeAlerte.MISSION_SANS_OT]),
        Alerte.statut != StatutAlerte.TRAITEE
    ).update({Alerte.statut: StatutAlerte.TRAITEE}, synchronize_session=False)

    audit(db, user, "mission.validation_chargement", "mission", m.id, {
        "plaque": m.vehicule.plaque if m.vehicule else "?",
        "heure_chargement": iso(ts_val)
    })
    db.commit()
    if PUBLISH_ENABLED["on"]:
        from ..event_bus import publish
        publish("mission.update", s_mission(m))
    return s_mission(m)


@router.post("/missions/{mid}/valider-dechargement")
def valider_dechargement_mission(mid: str, data: MissionValiderDechargement | None = None,
                                 db: Session = Depends(get_db),
                                 user=Depends(require_roles(*ECRITURE))):
    """Validation opérationnelle du déchargement -> Clôture mission et bascule en LIBRE."""
    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404, "Mission introuvable")

    ts_val = None
    if data and data.horodatage:
        try:
            ts_val = datetime.fromisoformat(data.horodatage)
        except ValueError:
            pass
    if not ts_val:
        for e in reversed(m.etapes or []):
            if e.get("etat") in ("ARRIVEE_DEPOT_RECEPTEUR", "DECHARGEMENT_EFFECTUE", "DEVIATION_DETECTEE") and e.get("ts"):
                try:
                    ts_val = datetime.fromisoformat(e["ts"])
                    break
                except Exception:
                    pass
    if not ts_val:
        ts_val = m.heure_fin or now_local()

    m.validation_dechargement = "VALIDÉ"
    m.statut = StatutMission.TERMINEE
    m.statut_camion_actuel = "LIBRE"
    m.heure_fin = ts_val
    if m.heure_debut:
        m.duree_s = max(0, int((ts_val - m.heure_debut).total_seconds()))

    if not any(e.get("etat") == "DECHARGEMENT_EFFECTUE" for e in (m.etapes or [])):
        m.etapes = (m.etapes or []) + [{"etat": "DECHARGEMENT_EFFECTUE", "ts": iso(ts_val),
                                       "lieu": m.depot_effectif or m.depot_prevu or "Dépôt Récepteur",
                                       "zone": "DEPOT"}]

    suivi = db.scalar(select(SuiviJournalier).where(SuiviJournalier.mission_id == m.id))
    if suivi:
        suivi.statut_camion = StatutCamion.LIBRE
        suivi.mission_id = None

    # Auto-résolution des alertes
    db.query(Alerte).filter(
        Alerte.vehicule_id == m.vehicule_id,
        Alerte.type.in_([TypeAlerte.VALIDATION_DECHARGEMENT, TypeAlerte.DEVIATION_DETECTEE]),
        Alerte.statut != StatutAlerte.TRAITEE
    ).update({Alerte.statut: StatutAlerte.TRAITEE}, synchronize_session=False)

    audit(db, user, "mission.validation_dechargement", "mission", m.id, {
        "plaque": m.vehicule.plaque if m.vehicule else "?",
        "heure_fin": iso(ts_val)
    })
    db.commit()
    if PUBLISH_ENABLED["on"]:
        from ..event_bus import publish
        publish("mission.update", s_mission(m))
    return s_mission(m)


@router.post("/missions/{mid}/invalider-dechargement")
def invalider_dechargement_mission(mid: str, data: MissionInvaliderDechargement,
                                   db: Session = Depends(get_db),
                                   user=Depends(require_roles(*ECRITURE))):
    """Invalidation du déchargement (échantillonnage, repos parking) -> Conserve statut CHARGÉ et EN_COURS."""
    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404, "Mission introuvable")

    m.validation_dechargement = "INVALIDÉ"
    m.motif_invalidation = data.motif + (f" ({data.commentaire})" if data.commentaire else "")
    m.statut = StatutMission.EN_COURS
    m.statut_camion_actuel = "CHARGE"

    suivi = db.scalar(select(SuiviJournalier).where(SuiviJournalier.mission_id == m.id))
    if suivi:
        suivi.statut_camion = StatutCamion.CHARGE

    db.query(Alerte).filter(
        Alerte.vehicule_id == m.vehicule_id,
        Alerte.type == TypeAlerte.VALIDATION_DECHARGEMENT,
        Alerte.statut != StatutAlerte.TRAITEE
    ).update({Alerte.statut: StatutAlerte.TRAITEE}, synchronize_session=False)

    audit(db, user, "mission.invalidation_dechargement", "mission", m.id, {
        "plaque": m.vehicule.plaque if m.vehicule else "?",
        "motif": m.motif_invalidation
    })
    db.commit()
    if PUBLISH_ENABLED["on"]:
        from ..event_bus import publish
        publish("mission.update", s_mission(m))
    return s_mission(m)


@router.post("/missions/{mid}/declarer-deviation")
def declarer_deviation_mission(mid: str, data: MissionDeclarerDeviation,
                               db: Session = Depends(get_db),
                               user=Depends(require_roles(*ECRITURE))):
    """Déclaration manuelle ou confirmation d'une déviation de dépôt."""
    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404, "Mission introuvable")

    m.est_deviee = True
    m.statut = StatutMission.DEVIEE
    m.depot_effectif = data.nouveau_depot
    m.motif_deviation = data.motif or f"Déviation vers {data.nouveau_depot} (prévu : {m.depot_prevu or '—'})"

    db.query(Alerte).filter(
        Alerte.vehicule_id == m.vehicule_id,
        Alerte.type == TypeAlerte.DEVIATION_DETECTEE,
        Alerte.statut != StatutAlerte.TRAITEE
    ).update({Alerte.statut: StatutAlerte.TRAITEE}, synchronize_session=False)

    audit(db, user, "mission.deviation", "mission", m.id, {
        "plaque": m.vehicule.plaque if m.vehicule else "?",
        "nouveau_depot": data.nouveau_depot,
        "motif": m.motif_deviation
    })
    db.commit()
    if PUBLISH_ENABLED["on"]:
        from ..event_bus import publish
        publish("mission.update", s_mission(m))
    return s_mission(m)


@router.get("/missions/alertes")
def alertes_missions(db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    """Récupère les alertes spécifiques au cycle des Missions."""
    types_missions = [
        TypeAlerte.MISSION_SANS_OT,
        TypeAlerte.VALIDATION_CHARGEMENT,
        TypeAlerte.VALIDATION_DECHARGEMENT,
        TypeAlerte.DEVIATION_DETECTEE,
        TypeAlerte.MISSION_RETARDEE
    ]
    alertes = list(db.scalars(
        select(Alerte)
        .where(
            Alerte.type.in_(types_missions),
            Alerte.statut.in_([StatutAlerte.NOUVELLE, StatutAlerte.VUE])
        )
        .order_by(Alerte.date_heure.desc())
    ).all())

    return {
        "nb_alertes": len(alertes),
        "items": [s_alerte(a) for a in alertes]
    }


@router.get("/missions/stats")
def stats_missions(date_debut: str | None = None, date_fin: str | None = None,
                   statut: str | None = None, conducteur_id: str | None = None,
                   vehicule_id: str | None = None, depot: str | None = None,
                   distributeur: str | None = None, q: str | None = None,
                   db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    debut, fin, missions = _recuperer_missions_filtrees(
        db, date_debut, date_fin, statut, conducteur_id, vehicule_id, depot, distributeur, q)
    stats = _calculer_stats_missions(missions, db)
    return {"date_debut": debut.isoformat(), "date_fin": fin.isoformat(), "stats": stats}


@router.get("/missions/export.xlsx")
def export_missions_excel_endpoint(date_debut: str | None = None, date_fin: str | None = None,
                                  statut: str | None = None, conducteur_id: str | None = None,
                                  vehicule_id: str | None = None, depot: str | None = None,
                                  distributeur: str | None = None, q: str | None = None,
                                  db: Session = Depends(get_db),
                                  user=Depends(require_roles(*TOUS))):
    from ..exporters import export_missions_excel
    debut, fin, missions = _recuperer_missions_filtrees(
        db, date_debut, date_fin, statut, conducteur_id, vehicule_id, depot, distributeur, q)

    m_ids = [m.id for m in missions]
    inf_counts = {}
    if m_ids:
        rows = db.execute(
            select(Infraction.mission_id, func.count(Infraction.id))
            .where(Infraction.mission_id.in_(m_ids), Infraction.validation != "INVALIDE")
            .group_by(Infraction.mission_id)
        ).all()
        inf_counts = {r[0]: r[1] for r in rows}

    lignes = [s_mission(m, inf_counts.get(m.id, 0)) for m in missions]
    titre_periode = f"du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}"
    audit(db, user, "mission.export_excel", "mission", titre_periode, {"nb_missions": len(lignes)})
    db.commit()

    contenu = export_missions_excel(titre_periode, lignes, utilisateur=user.nom_complet)
    nom = f"Missions_{debut.isoformat()}_{fin.isoformat()}.xlsx"
    return Response(contenu, media_type=XLSX_MIME,
                    headers={"Content-Disposition": f"attachment; filename={nom}"})


@router.get("/missions/export.pdf")
def export_missions_pdf_endpoint(date_debut: str | None = None, date_fin: str | None = None,
                                 statut: str | None = None, conducteur_id: str | None = None,
                                 vehicule_id: str | None = None, depot: str | None = None,
                                 distributeur: str | None = None, q: str | None = None,
                                 db: Session = Depends(get_db),
                                 user=Depends(require_roles(*TOUS))):
    from ..exporters import export_missions_pdf
    debut, fin, missions = _recuperer_missions_filtrees(
        db, date_debut, date_fin, statut, conducteur_id, vehicule_id, depot, distributeur, q)

    m_ids = [m.id for m in missions]
    inf_counts = {}
    if m_ids:
        rows = db.execute(
            select(Infraction.mission_id, func.count(Infraction.id))
            .where(Infraction.mission_id.in_(m_ids), Infraction.validation != "INVALIDE")
            .group_by(Infraction.mission_id)
        ).all()
        inf_counts = {r[0]: r[1] for r in rows}

    lignes = [s_mission(m, inf_counts.get(m.id, 0)) for m in missions]
    titre_periode = f"du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}"
    audit(db, user, "mission.export_pdf", "mission", titre_periode, {"nb_missions": len(lignes)})
    db.commit()

    contenu = export_missions_pdf(titre_periode, lignes, utilisateur=user.nom_complet)
    nom = f"Missions_{debut.isoformat()}_{fin.isoformat()}.pdf"
    return Response(contenu, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={nom}"})


@router.get("/missions/jour/{date_param}")
def missions_par_chauffeur(date_param: str, db: Session = Depends(get_db),
                           _=Depends(require_roles(*TOUS))):
    """Écran d'entrée §6.3 : chauffeurs actifs du jour + missions détectées."""
    jour = date.fromisoformat(date_param)
    suivis = db.scalars(
        select(SuiviJournalier)
        .where(SuiviJournalier.date_jour == jour, SuiviJournalier.conducteur_id.isnot(None))
        .join(Vehicule, SuiviJournalier.vehicule_id == Vehicule.id)
        .order_by(Vehicule.plaque)).all()
    missions = db.scalars(select(Mission).where(Mission.date_jour == jour)
                          .order_by(Mission.heure_debut)).all()
    par_conducteur: dict[str, list] = {}
    for m in missions:
        par_conducteur.setdefault(m.conducteur_id, []).append(m)

    lignes = []
    for s in suivis:
        if not s.conducteur:
            continue
        ms = par_conducteur.get(s.conducteur_id, [])
        lignes.append({
            "conducteur": {"id": s.conducteur.id, "nom_prenom": s.conducteur.nom_prenom,
                           "prenom_usuel": s.conducteur.prenom_usuel,
                           "telephone": s.conducteur.telephone},
            "plaque": s.vehicule.plaque if s.vehicule else None,
            "vehicule_id": s.vehicule_id,
            "situation": s.situation,
            "statut_camion": s.statut_camion.value if s.statut_camion else None,
            "nb_missions": len(ms),
            "missions": [s_mission(m) for m in ms],
        })
    lignes.sort(key=lambda l: (-l["nb_missions"], l["plaque"] or ""))
    return {"date": jour.isoformat(), "lignes": lignes}


@router.get("/missions/{mid}")
def detail_mission(mid: str, db: Session = Depends(get_db),
                   _=Depends(require_roles(*TOUS))):
    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404, "Mission introuvable")
    return s_mission(m)
