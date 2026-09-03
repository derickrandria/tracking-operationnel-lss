"""Module 2 — Suivi Journalier (cœur du système) et Module 3 — Missions."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import now_local
from ..database import get_db
from ..engine import (appliquer_champs_suivi, ensure_suivis_du_jour,
                      get_seuils, prefill_positions_gps)
from ..models import Mission, SuiviJournalier, Vehicule
from ..security import ECRITURE, TOUS, audit, require_roles
from ..serializers import s_mission, s_suivi

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
@router.get("/missions")
def liste_missions(date: str | None = None, statut: str | None = None,
                   conducteur_id: str | None = None,
                   db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    jour = _jour(date)
    query = select(Mission).where(Mission.date_jour == jour)
    if statut:
        query = query.where(Mission.statut == statut)
    if conducteur_id:
        query = query.where(Mission.conducteur_id == conducteur_id)
    missions = db.scalars(query.order_by(Mission.heure_debut)).all()
    return {"date": jour.isoformat(), "missions": [s_mission(m) for m in missions]}


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
