"""Modules 7 & 8 — référentiels Conducteurs et Véhicules (+ listes §Annexe C/D).

Toute modification émet un événement `referentiels.changed` consommé par les
autres modules (§9) et met à jour le Suivi Journalier du jour.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..config import (DEPOTS, DISTRIBUTEURS, PRODUITS, calculer_tokens_set,
                      normaliser_libelle, normaliser_saisie, now_local)
from ..database import get_db
from ..engine import ensure_suivi, get_seuils
from ..event_bus import publish
from ..models import (Alerte, Conducteur, ConducteurAlias, Infraction, Mission,
                      SituationCamion, StatutCamion, StatutConducteur,
                      StatutVehicule, SuiviJournalier, Trajet, Vehicule)
from ..security import ADMIN, ECRITURE, TOUS, audit, require_roles
from ..serializers import s_conducteur, s_suivi, s_vehicule

router = APIRouter(prefix="/api", tags=["referentiels"])


# ------------------------------------------------------------------ listes normalisées
@router.get("/referentiels")
def referentiels(db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    situations = db.scalars(select(SituationCamion)
                            .where(SituationCamion.actif.is_(True))
                            .order_by(SituationCamion.ordre)).all()
    return {
        "situations": [s.libelle for s in situations],
        "statuts_camion": [s.value for s in StatutCamion],
        "depots": DEPOTS,
        "distributeurs": DISTRIBUTEURS,
        "produits": PRODUITS,
        "statuts_vehicule": [s.value for s in StatutVehicule],
        "statuts_conducteur": [s.value for s in StatutConducteur],
    }


# ============================== CONDUCTEURS (module 7) ==============================
class ConducteurIn(BaseModel):
    nom_prenom: str
    prenom_usuel: str
    telephone: str | None = None
    statut: str = "ACTIF"
    matricule: str | None = None
    code_badge_mzonex: int | None = None


class ConducteurPatch(BaseModel):
    nom_prenom: str | None = None
    prenom_usuel: str | None = None
    telephone: str | None = None
    statut: str | None = None
    matricule: str | None = None
    code_badge_mzonex: int | None = None


class ConducteurFusionIn(BaseModel):
    source_id: str
    cible_id: str
    creer_alias: bool = True


class AliasIn(BaseModel):
    alias: str


def _vehicule_assigne(db: Session) -> dict:
    return {v.conducteur_actuel_id: v.plaque
            for v in db.scalars(select(Vehicule).where(Vehicule.conducteur_actuel_id.isnot(None)))}


@router.get("/conducteurs")
def liste_conducteurs(q: str | None = None, statut: str | None = None,
                      db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    query = select(Conducteur)
    if q:
        motif = f"%{q.strip()}%"
        query = query.where(or_(Conducteur.nom_prenom.ilike(motif),
                                Conducteur.prenom_usuel.ilike(motif),
                                Conducteur.matricule.ilike(motif)))
    if statut:
        query = query.where(Conducteur.statut == statut)
    conducteurs = db.scalars(query.order_by(Conducteur.prenom_usuel)).all()
    plaques = _vehicule_assigne(db)
    return [{**s_conducteur(c), "vehicule_plaque": plaques.get(c.id)} for c in conducteurs]


def _prochain_matricule(db: Session) -> str:
    n = db.scalar(select(func.count(Conducteur.id))) or 0
    while True:
        n += 1
        m = f"CH{n:03d}"
        if not db.scalar(select(Conducteur).where(Conducteur.matricule == m)):
            return m


@router.post("/conducteurs", status_code=status.HTTP_201_CREATED)
def creer_conducteur(data: ConducteurIn, db: Session = Depends(get_db),
                     user=Depends(require_roles(*ECRITURE))):
    norm = normaliser_libelle(data.nom_prenom.strip())[:170]
    tset = calculer_tokens_set(data.nom_prenom.strip())[:170]
    if db.scalar(select(Conducteur).where(
            (Conducteur.nom_normalise == norm) | (Conducteur.tokens_set == tset))):
        raise HTTPException(409, "Un chauffeur au nom équivalent existe déjà "
                                 "(casse, accents et ordre des mots ignorés)")
    if db.scalar(select(ConducteurAlias).where(ConducteurAlias.alias_normalise == norm)):
        raise HTTPException(409, "Ce libellé est déjà enregistré comme alias pour un autre chauffeur")

    # Code badge MZoneX / matricule
    badge_code = data.code_badge_mzonex
    mat = (data.matricule or "").strip() or (str(badge_code) if badge_code else None)
    if mat and db.scalar(select(Conducteur).where(Conducteur.matricule == mat)):
        raise HTTPException(409, "Matricule déjà utilisé")
    if badge_code and db.scalar(select(Conducteur).where(Conducteur.code_badge_mzonex == badge_code)):
        raise HTTPException(409, "Code badge MZoneX déjà utilisé")

    c = Conducteur(
        nom_prenom=data.nom_prenom.strip(), prenom_usuel=data.prenom_usuel.strip().upper(),
        telephone=(data.telephone or "").strip() or None,
        statut=StatutConducteur(data.statut),
        matricule=mat,
        code_badge_mzonex=badge_code,
        nom_normalise=norm,
        tokens_set=tset)
    db.add(c)
    db.flush()
    audit(db, user, "conducteur.creation", "conducteur", c.id, {"apres": s_conducteur(c)})
    db.commit()
    publish("referentiels.changed", {"entite": "conducteur", "action": "creation", "id": c.id})
    return s_conducteur(c)


@router.patch("/conducteurs/{cid}")
def modifier_conducteur(cid: str, data: ConducteurPatch, db: Session = Depends(get_db),
                        user=Depends(require_roles(*ECRITURE))):
    c = db.get(Conducteur, cid)
    if c is None:
        raise HTTPException(404, "Conducteur introuvable")
    avant = s_conducteur(c)
    donnees = data.model_dump(exclude_unset=True)

    if donnees.get("nom_prenom"):
        cible = normaliser_libelle(str(donnees["nom_prenom"]).strip())[:170]
        tset = calculer_tokens_set(str(donnees["nom_prenom"]).strip())[:170]
        if db.scalar(select(Conducteur).where(
                (Conducteur.nom_normalise == cible) | (Conducteur.tokens_set == tset),
                Conducteur.id != cid)):
            raise HTTPException(409, "Un chauffeur au nom équivalent existe "
                                     "déjà (casse, accents et ordre des mots ignorés)")
        c.nom_normalise = cible
        c.tokens_set = tset

    if "matricule" in donnees:
        mat = (donnees["matricule"] or "").strip() or None
        if mat and db.scalar(select(Conducteur).where(Conducteur.matricule == mat, Conducteur.id != cid)):
            raise HTTPException(409, "Matricule déjà utilisé")
        c.matricule = mat

    if "code_badge_mzonex" in donnees:
        badge = donnees["code_badge_mzonex"]
        if badge and db.scalar(select(Conducteur).where(Conducteur.code_badge_mzonex == badge, Conducteur.id != cid)):
            raise HTTPException(409, "Code badge MZoneX déjà utilisé")
        c.code_badge_mzonex = badge

    for champ, val in donnees.items():
        if champ in ("matricule", "code_badge_mzonex"):
            continue
        if champ == "statut" and val is not None:
            val = StatutConducteur(val)
        setattr(c, champ, val)

    audit(db, user, "conducteur.modification", "conducteur", cid,
          {"avant": avant, "apres": s_conducteur(c)})
    db.commit()
    publish("referentiels.changed", {"entite": "conducteur", "action": "modification", "id": cid})
    return s_conducteur(c)


@router.post("/conducteurs/fusionner")
def fusionner_conducteurs(data: ConducteurFusionIn, db: Session = Depends(get_db),
                          user=Depends(require_roles(*ECRITURE))):
    """Fusionne un chauffeur source (ex. AUTO-xxxx) vers un chauffeur cible officiel."""
    if data.source_id == data.cible_id:
        raise HTTPException(400, "Impossible de fusionner un chauffeur avec lui-même")

    source = db.get(Conducteur, data.source_id)
    cible = db.get(Conducteur, data.cible_id)
    if source is None:
        raise HTTPException(404, "Chauffeur source introuvable")
    if cible is None:
        raise HTTPException(404, "Chauffeur cible introuvable")

    # 1. Création de l'alias si demandé
    if data.creer_alias and source.nom_prenom:
        alias_norm = normaliser_libelle(source.nom_prenom)[:170]
        if alias_norm and alias_norm != cible.nom_normalise:
            if not db.scalar(select(ConducteurAlias).where(ConducteurAlias.alias_normalise == alias_norm)):
                db.add(ConducteurAlias(conducteur_id=cible.id, alias_brut=source.nom_prenom,
                                       alias_normalise=alias_norm, source="FUSION"))

    # 2. Réassignation des alias existants de source vers cible
    db.execute(update(ConducteurAlias).where(ConducteurAlias.conducteur_id == source.id).values(conducteur_id=cible.id))

    # 3. Réassignation de toutes les clés étrangères vives
    db.execute(update(Vehicule).where(Vehicule.conducteur_actuel_id == source.id).values(conducteur_actuel_id=cible.id))
    db.execute(update(SuiviJournalier).where(SuiviJournalier.conducteur_id == source.id).values(conducteur_id=cible.id))
    db.execute(update(Trajet).where(Trajet.conducteur_badge_id == source.id).values(
        conducteur_badge_id=cible.id, conducteur_badge=cible.nom_prenom))
    db.execute(update(Mission).where(Mission.conducteur_id == source.id).values(conducteur_id=cible.id))
    db.execute(update(Infraction).where(Infraction.conducteur_id == source.id).values(conducteur_id=cible.id))
    db.execute(update(Alerte).where(Alerte.conducteur_id == source.id).values(conducteur_id=cible.id))

    # 4. Héritage du code_badge_mzonex si présent sur la source et absent sur la cible
    if source.code_badge_mzonex and not cible.code_badge_mzonex:
        cible.code_badge_mzonex = source.code_badge_mzonex
        if not cible.matricule:
            cible.matricule = str(source.code_badge_mzonex)

    # 5. Audit log complet de la fusion
    audit(db, user, "conducteur.fusion", "conducteur", cible.id, {
        "source": s_conducteur(source),
        "cible": s_conducteur(cible),
        "alias_cree": data.creer_alias
    })

    # 6. Suppression propre de la fiche source
    db.delete(source)
    db.commit()
    publish("referentiels.changed", {"entite": "conducteur", "action": "fusion", "id": cible.id})
    return s_conducteur(cible)


@router.post("/conducteurs/{cid}/aliases")
def ajouter_alias(cid: str, data: AliasIn, db: Session = Depends(get_db),
                  user=Depends(require_roles(*ECRITURE))):
    c = db.get(Conducteur, cid)
    if c is None:
        raise HTTPException(404, "Conducteur introuvable")
    alias_txt = data.alias.strip()
    if not alias_txt or len(alias_txt) < 3:
        raise HTTPException(400, "Libellé d'alias invalide (minimum 3 caractères)")
    alias_norm = normaliser_libelle(alias_txt)[:170]
    if alias_norm == c.nom_normalise:
        raise HTTPException(400, "Cet alias correspond déjà au nom officiel du chauffeur")
    if db.scalar(select(ConducteurAlias).where(ConducteurAlias.alias_normalise == alias_norm)):
        raise HTTPException(409, "Cet alias est déjà associé à un chauffeur")

    alias_obj = ConducteurAlias(conducteur_id=c.id, alias_brut=alias_txt,
                                alias_normalise=alias_norm, source="MANUEL")
    db.add(alias_obj)
    audit(db, user, "conducteur.alias_ajoute", "conducteur", c.id, {"alias": alias_txt})
    db.commit()
    publish("referentiels.changed", {"entite": "conducteur", "action": "modification", "id": c.id})
    return {"ok": True, "id": alias_obj.id, "alias_brut": alias_obj.alias_brut}


@router.delete("/conducteurs/{cid}/aliases/{aid}")
def supprimer_alias(cid: str, aid: str, db: Session = Depends(get_db),
                    user=Depends(require_roles(*ECRITURE))):
    alias_obj = db.get(ConducteurAlias, aid)
    if alias_obj is None or alias_obj.conducteur_id != cid:
        raise HTTPException(404, "Alias introuvable pour ce chauffeur")
    db.delete(alias_obj)
    audit(db, user, "conducteur.alias_supprime", "conducteur", cid, {"alias_id": aid})
    db.commit()
    publish("referentiels.changed", {"entite": "conducteur", "action": "modification", "id": cid})
    return {"ok": True}


@router.delete("/conducteurs/{cid}")
def supprimer_conducteur(cid: str, db: Session = Depends(get_db),
                         user=Depends(require_roles(*ECRITURE))):
    """Supprime définitivement un chauffeur et détache proprement ses références vives."""
    c = db.get(Conducteur, cid)
    if c is None:
        raise HTTPException(404, "Conducteur introuvable")

    avant = s_conducteur(c)

    # 1. Détachement propre de toutes les clés étrangères vives
    db.execute(update(Vehicule).where(Vehicule.conducteur_actuel_id == cid).values(conducteur_actuel_id=None))
    db.execute(update(SuiviJournalier).where(SuiviJournalier.conducteur_id == cid).values(conducteur_id=None))
    db.execute(update(Trajet).where(Trajet.conducteur_badge_id == cid).values(conducteur_badge_id=None))
    db.execute(update(Mission).where(Mission.conducteur_id == cid).values(conducteur_id=None))
    db.execute(update(Infraction).where(Infraction.conducteur_id == cid).values(conducteur_id=None))
    db.execute(update(Alerte).where(Alerte.conducteur_id == cid).values(conducteur_id=None))

    # 2. Suppression de la fiche (les alias sont supprimés en cascade) et audit
    audit(db, user, "conducteur.suppression", "conducteur", cid, {"avant": avant})
    db.delete(c)
    db.commit()
    publish("referentiels.changed", {"entite": "conducteur", "action": "suppression", "id": cid})
    return {"ok": True}


# ============================== VÉHICULES (module 8) ==============================
class VehiculeIn(BaseModel):
    plaque: str
    description: str | None = None
    marque: str | None = None
    capacite: float | None = None
    statut: str = "ACTIF"
    gps_associe: str | None = None
    conducteur_actuel_id: str | None = None


class VehiculePatch(BaseModel):
    plaque: str | None = None
    description: str | None = None
    marque: str | None = None
    capacite: float | None = None
    statut: str | None = None
    gps_associe: str | None = None
    conducteur_actuel_id: str | None = None


@router.get("/vehicules")
def liste_vehicules(q: str | None = None, statut: str | None = None,
                    db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    query = select(Vehicule)
    if q:
        motif = f"%{q.strip()}%"
        query = query.where(or_(Vehicule.plaque.ilike(motif),
                                Vehicule.description.ilike(motif),
                                Vehicule.marque.ilike(motif),
                                Vehicule.gps_associe.ilike(motif)))
    if statut:
        query = query.where(Vehicule.statut == statut)
    return [s_vehicule(v) for v in db.scalars(query.order_by(Vehicule.plaque)).all()]


@router.post("/vehicules", status_code=status.HTTP_201_CREATED)
def creer_vehicule(data: VehiculeIn, db: Session = Depends(get_db),
                   user=Depends(require_roles(*ECRITURE))):
    # §0quater D0 (14/08/2026) : normalisation anti-fautes (espaces retirés,
    # MAJUSCULES — jamais de coupe au tiret : « OBC-… » légitime) —
    # « 8076 TCB » devient « 8076TCB » (sinon la fiche ne correspond jamais
    # aux relevés : données inexploitées, constat métier du 14/08)
    plaque = normaliser_saisie(data.plaque)
    gps = normaliser_saisie(data.gps_associe) if data.gps_associe else None
    if db.scalar(select(Vehicule).where(Vehicule.plaque == plaque)):
        raise HTTPException(409, "Plaque déjà enregistrée")
    v = Vehicule(plaque=plaque, description=data.description, marque=data.marque,
                 capacite=data.capacite, statut=StatutVehicule(data.statut),
                 gps_associe=gps or f"OBC-{plaque}",
                 conducteur_actuel_id=data.conducteur_actuel_id or None)
    db.add(v)
    db.flush()
    audit(db, user, "vehicule.creation", "vehicule", v.id, {"apres": s_vehicule(v)})
    db.commit()
    publish("referentiels.changed", {"entite": "vehicule", "action": "creation", "id": v.id})
    return s_vehicule(v)


@router.patch("/vehicules/{vid}")
def modifier_vehicule(vid: str, data: VehiculePatch, db: Session = Depends(get_db),
                      user=Depends(require_roles(*ECRITURE))):
    v = db.get(Vehicule, vid)
    if v is None:
        raise HTTPException(404, "Véhicule introuvable")
    avant = s_vehicule(v)
    champs = data.model_dump(exclude_unset=True)
    changement_conducteur = "conducteur_actuel_id" in champs and \
        champs["conducteur_actuel_id"] != v.conducteur_actuel_id
    for champ, val in champs.items():
        if champ == "statut" and val is not None:
            val = StatutVehicule(val)
        # §0quater D0 : plaque et boîtier normalisés (espaces + casse,
        # tiret conservé — « OBC-8076 TCB » → « OBC-8076TCB »)
        elif champ == "plaque" and val is not None:
            val = normaliser_saisie(val)
        elif champ == "gps_associe" and val:
            val = normaliser_saisie(val)
        setattr(v, champ, val)
    db.flush()

    # --- synchronisation immédiate vers le Suivi Journalier (§9) ----------
    if changement_conducteur:
        suivi = db.scalar(select(SuiviJournalier).where(
            SuiviJournalier.vehicule_id == vid, SuiviJournalier.date_jour == now_local().date()))
        if suivi:
            suivi.conducteur_actuel_id = None  # no-op cohérence
            suivi.conducteur_id = v.conducteur_actuel_id
            # §0septies B2 (20/08/2026) — une attribution faite à la main est
            # marquée « MANUEL » : aucun badge ne l'écrasera jamais ensuite
            suivi.conducteur_origine = "MANUEL"
            db.flush()
            publish("suivi.update", {"suivi": s_suivi(suivi, get_seuils(db))})

    audit(db, user, "vehicule.modification", "vehicule", vid,
          {"avant": avant, "apres": s_vehicule(v)})
    db.commit()
    publish("referentiels.changed", {"entite": "vehicule", "action": "modification", "id": vid})
    return s_vehicule(v)


@router.delete("/vehicules/{vid}")
def supprimer_vehicule(vid: str, db: Session = Depends(get_db),
                       user=Depends(require_roles(*ADMIN))):
    v = db.get(Vehicule, vid)
    if v is None:
        raise HTTPException(404, "Véhicule introuvable")
    refs = db.scalar(select(func.count(SuiviJournalier.id)).where(SuiviJournalier.vehicule_id == vid)) or 0
    if refs:
        raise HTTPException(409, "Véhicule référencé dans le suivi : passez-le en INACTIF")
    audit(db, user, "vehicule.suppression", "vehicule", vid, {"avant": s_vehicule(v)})
    db.delete(v)
    db.commit()
    publish("referentiels.changed", {"entite": "vehicule", "action": "suppression", "id": vid})
    return {"ok": True}


# ============================== SITUATIONS (Annexe C éditable) ==============================
class SituationIn(BaseModel):
    libelle: str
    ordre: int | None = None


class SituationPatch(BaseModel):
    libelle: str | None = None
    actif: bool | None = None
    ordre: int | None = None


@router.get("/situations")
def liste_situations(toutes: bool = True, db: Session = Depends(get_db),
                     _=Depends(require_roles(*TOUS))):
    query = select(SituationCamion).order_by(SituationCamion.ordre)
    if not toutes:
        query = query.where(SituationCamion.actif.is_(True))
    return [{"id": s.id, "libelle": s.libelle, "actif": s.actif, "ordre": s.ordre}
            for s in db.scalars(query).all()]


@router.post("/situations", status_code=status.HTTP_201_CREATED)
def creer_situation(data: SituationIn, db: Session = Depends(get_db),
                    user=Depends(require_roles(*ADMIN))):
    libelle = data.libelle.strip()
    if db.scalar(select(SituationCamion).where(SituationCamion.libelle == libelle)):
        raise HTTPException(409, "Situation déjà existante")
    ordre = data.ordre
    if ordre is None:
        ordre = (db.scalar(select(func.max(SituationCamion.ordre))) or 0) + 1
    s = SituationCamion(libelle=libelle, ordre=ordre)
    db.add(s)
    db.flush()
    audit(db, user, "situation.creation", "situation", s.id, {"apres": libelle})
    db.commit()
    publish("referentiels.changed", {"entite": "situation", "action": "creation"})
    return {"id": s.id, "libelle": s.libelle, "actif": s.actif, "ordre": s.ordre}


@router.patch("/situations/{sid}")
def modifier_situation(sid: str, data: SituationPatch, db: Session = Depends(get_db),
                       user=Depends(require_roles(*ADMIN))):
    s = db.get(SituationCamion, sid)
    if s is None:
        raise HTTPException(404, "Situation introuvable")
    avant = s.libelle
    for champ, val in data.model_dump(exclude_unset=True).items():
        setattr(s, champ, val)
    audit(db, user, "situation.modification", "situation", sid,
          {"avant": avant, "apres": s.libelle})
    db.commit()
    publish("referentiels.changed", {"entite": "situation", "action": "modification"})
    return {"id": s.id, "libelle": s.libelle, "actif": s.actif, "ordre": s.ordre}
