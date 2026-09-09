"""Modules 4 & 5 — Infractions (100 % automatiques) et Alertes (temps réel)."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import now_local
from ..database import get_db
from ..exporters import export_excel, export_pdf
from ..models import (Alerte, AuditLog, GraviteAlerte, Infraction, StatutAlerte)
from ..security import ECRITURE, TOUS, require_roles
from ..serializers import s_alerte, s_infraction

router = APIRouter(prefix="/api", tags=["surveillance"])

TYPES_INFRACTION = {
    "EXCES_VITESSE": "Excès de vitesse",
    "ACCELERATION_BRUSQUE": "Accélération brusque",
    "FREINAGE_BRUSQUE": "Freinage brusque",
    "DEPASSEMENT_TCC": "Dépassement TCC",
    "DEPASSEMENT_TCJ": "Dépassement TCJ",
    "DEPASSEMENT_TTJ": "Dépassement TTJ",
}

TYPES_ALERTE = {
    "TCC_DEPASSE": "Seuil réglementaire dépassé",
    "PAUSE_NON_PRISE": "Pause non prise",
    "CAMION_IMMOBILE": "Camion immobile",
    "GPS_HORS_LIGNE": "GPS hors ligne",
    "MISSION_RETARDEE": "Mission retardée",
    "EXCES_VITESSE": "Excès de vitesse",
    "HORS_ITINERAIRE": "Camion hors itinéraire",
    "CARBURANT_SUSPECT": "Carburant suspect",
    "NB_TRAJETS_EXCEPTIONNEL": "Nb trajets exceptionnel (> 10)",
    "NOUVEAU_VEHICULE": "Nouveau véhicule détecté (§0quater D1)",
    "NOUVEAU_CONDUCTEUR": "Nouveau chauffeur détecté (§0quater D2)",
    "VITESSE_LIVE": "Excès de vitesse en direct (> 45 km/h hors zone)",
    "SANS_BADGE": "Camion qui roule sans badge chauffeur",
    "REPARATION_DONNEES": "Réparation de données (§0decies)",
    "COLLECTE_YMANE": "Collecte Ym@ne en échec (§0septies decies K1)",
    "TCH_PROCHE_LIMITE": "TCH proche de la limite (≥ 46h)",
    "TCH_LIMITE_ATTEINTE": "TCH limite atteinte (≥ 56h)",
    "CONFLIT_AFFECTATION": "Conflit d'affectation chauffeur (manuel vs portail)",
    "DOUBLON_CONDUCTEUR": "Doublon chauffeur sur la journée",
    "CHANGEMENT_CONDUCTEUR_DETECTE": "Changement de conducteur détecté (arbitrage requis)",
}


def _periode(du: str | None, au: str | None):
    d_du = date.fromisoformat(du) if du else date.today() - timedelta(days=30)
    d_au = date.fromisoformat(au) if au else date.today()
    return d_du, d_au


# ============================== INFRACTIONS ==============================
def _query_infractions(db, du, au, type_=None, gravite=None, vehicule_id=None,
                       conducteur_id=None, niveau=None, validation=None,
                       famille=None):
    # v3 AM-5 / C3 (arbitrage LSS 22/08/2026), confirmé par §0quinquies decies
    # I1 (25/08/2026) : l'onglet Infractions est une VITRE DE LECTURE des
    # infractions pré-filtrées par une autre plateforme — depuis v1.35, la
    # source est Ym@ne (MZoneX uniquement) ; zéro écriture locale n'y arrive
    # jamais (les lignes historiques locales restent en base pour l'audit —
    # jamais effacées, jamais affichées).
    q = select(Infraction).where(Infraction.date_jour >= du, Infraction.date_jour <= au,
                                 Infraction.exterieure.is_(True))
    if type_:
        q = q.where(Infraction.type == type_)
    if gravite:
        q = q.where(Infraction.gravite == gravite)
    if vehicule_id:
        q = q.where(Infraction.vehicule_id == vehicule_id)
    if conducteur_id:
        q = q.where(Infraction.conducteur_id == conducteur_id)
    if niveau:                      # I3 — filtre d'écran ALERTE / ALARME
        q = q.where(Infraction.niveau == niveau)
    if validation:                  # I3 — filtre d'écran état de validation
        q = q.where(Infraction.validation == validation)
    if famille:                     # §0octies decies L3 (27/08) — filtre type
        q = q.where(Infraction.nom_ymane == famille)   # libellé verbatim exact
    return q


@router.get("/infractions")
def liste_infractions(du: str | None = None, au: str | None = None,
                      type: str | None = None, gravite: str | None = None,
                      vehicule_id: str | None = None, conducteur_id: str | None = None,
                      niveau: str | None = None, validation: str | None = None,
                      famille: str | None = None,
                      limite: int = 1000,
                      db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    d_du, d_au = _periode(du, au)
    q = _query_infractions(db, d_du, d_au, type, gravite, vehicule_id,
                           conducteur_id, niveau, validation, famille)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    # I4 — compteurs sur le MÊME périmètre filtré ; la ligne invalidée reste
    # visible à l'écran (jamais masquée) mais est exclue des totaux métiers.
    par_etat = {v: int(n) for v, n in db.execute(
        select(Infraction.validation, func.count())
        .where(Infraction.date_jour >= d_du, Infraction.date_jour <= d_au,
               Infraction.exterieure.is_(True))
        .group_by(Infraction.validation)).all()}
    items = db.scalars(q.order_by(Infraction.date_jour.desc(), Infraction.heure.desc())
                       .limit(limite)).all()
    invalidees = par_etat.get("INVALIDE", 0)
    return {"total": total, "types": TYPES_INFRACTION,
            "compteurs": {
                "non_traitees": par_etat.get("NON_TRAITEE", 0),
                "validees": par_etat.get("VALIDE", 0),
                "invalidees": invalidees,
                "comptabilisees": sum(par_etat.values()) - invalidees},
            "items": [s_infraction(i) for i in items]}


@router.get("/infractions/familles")
def familles_infractions(db: Session = Depends(get_db),
                         _=Depends(require_roles(*TOUS))):
    """§0octies decies L3 (27/08/2026) — liste des familles Ym@ne réellement
    présentes en base (libellés verbatim), pour le filtre « type » de l'écran."""
    return db.scalars(
        select(Infraction.nom_ymane)
        .where(Infraction.exterieure.is_(True),
               Infraction.nom_ymane.isnot(None))
        .group_by(Infraction.nom_ymane)
        .order_by(Infraction.nom_ymane)).all()


class InfractionValidationIn(BaseModel):
    decision: str                 # VALIDE | INVALIDE
    observation: str | None = None


@router.post("/infractions/{iid}/validation")
def valider_infraction(iid: str, data: InfractionValidationIn,
                       db: Session = Depends(get_db),
                       user=Depends(require_roles(*ECRITURE))):
    """§0quinquies decies I4 (25/08/2026) — workflow de validation :
    NON_TRAITEE → VALIDE / INVALIDE ; INVALIDE exige une observation ;
    re-décision possible, chaque changement tracé (audit infraction.validation)
    ; jamais de suppression, jamais de masquage écran."""
    i = db.get(Infraction, iid)
    if i is None:
        raise HTTPException(404, "Infraction introuvable")
    if data.decision not in ("VALIDE", "INVALIDE"):
        raise HTTPException(400, "Décision inconnue : VALIDE ou INVALIDE attendue")
    observation = (data.observation or "").strip()[:500] or None
    if data.decision == "INVALIDE" and not observation:
        raise HTTPException(400, "Observation obligatoire pour invalider "
                            "une infraction (cause de l'invalidation)")
    avant = {"validation": i.validation, "observation": i.observation,
             "validee_par": i.validee_par,
             "validee_le": i.validee_le.isoformat() if i.validee_le else None}
    i.validation = data.decision
    i.observation = observation if data.decision == "INVALIDE" else None
    i.validee_par = user.username
    i.validee_le = now_local()
    db.add(AuditLog(username=user.username, action="infraction.validation",
                    entite="Infraction", entite_id=i.id,
                    details={"avant": avant,
                             "apres": {"validation": i.validation,
                                       "observation": i.observation,
                                       "validee_par": i.validee_par,
                                       "validee_le": i.validee_le.isoformat()},
                             "nom": i.nom_ymane,
                             "plaque": i.vehicule.plaque if i.vehicule else None,
                             "date_jour": i.date_jour.isoformat(),
                             "heure": i.heure.strftime("%H:%M:%S") if i.heure else None}))
    db.commit()
    return s_infraction(i)


@router.post("/infractions/ymane/rejouer")
def rejouer_collecte_ymane(db: Session = Depends(get_db),
                           user=Depends(require_roles(*ECRITURE))):
    """§0septies decies K2 (arbitrage LSS 27/08/2026) — « Relancer la
    collecte maintenant » depuis l'onglet Infractions. Relance synchrone et
    idempotente (anti-doublon §0quinquies decies I5) ; résultat renvoyé pour
    affichage immédiat ; chaque relance est consignée en audit."""
    from ..api_ymane import actif
    if not actif():
        db.add(AuditLog(username=user.username,
                        action="infraction.relance_ymane",
                        entite="infraction", entite_id=None,
                        details={"actif": False,
                                 "raison": "collecte désactivée"}))
        db.commit()
        return {"ok": False, "actif": False,
                "message": "La collecte Ym@ne est désactivée dans la "
                           "configuration (YMANE_ACTIVE≠1 dans "
                           "backend/.env) — rien à relancer."}
    # K2 blindé (mise au point 27/08 après-midi) : JAMAIS de 500 — toute
    # erreur, même imprévue, devient un message français affiché à l'écran.
    try:
        from .. import ymane_import                      # import tardif (tests)
        stats = ymane_import.cycle_ymane_avec_alerte()
    except Exception as exc:
        db.add(AuditLog(username=user.username,
                        action="infraction.relance_ymane",
                        entite="infraction", entite_id=None,
                        details={"ok": False,
                                 "raison": f"{type(exc).__name__}: {exc}"}))
        db.commit()
        return {"ok": False,
                "message": f"Le module de collecte a rencontré une erreur "
                           f"({type(exc).__name__}) — prévenez le support "
                           "technique en copiant ce message."}
    if stats.get("conflit"):
        raise HTTPException(409, "Une collecte Ym@ne est déjà en cours — "
                                 "elle se terminera seule, réessayez dans "
                                 "quelques instants.")
    db.add(AuditLog(username=user.username,
                    action="infraction.relance_ymane", entite="infraction",
                    entite_id=None, details=dict(stats)))
    db.commit()
    if not stats.get("ok"):
        return {"ok": False,
                "message": f"Échec de la collecte : {stats.get('raison')}. "
                           "Une alerte visible est tenue dans l'onglet "
                           "Alertes tant que le problème dure.",
                "stats": stats}
    message = (f"{stats.get('recus', 0)} ligne(s) lues sur la fenêtre J-8→J "
               f"({stats.get('filtrees_i2', 0)} « enregistrements » écartés) "
               f"— {stats.get('nouvelles', 0)} nouvelle(s), "
               f"{stats.get('maj', 0)} mise(s) à jour, "
               f"{stats.get('doublons', 0)} doublon(s) écarté(s)")
    if stats.get("sans_vehicule"):
        message += (f", {stats['sans_vehicule']} sans véhicule connu "
                    "(reportées au prochain cycle)")
    return {"ok": True, "message": message + ".", "stats": stats}


def _periode_txt(date_iso, heure):
    """§0octies decies L2 — « JJ/MM/AAAA HH:MM:SS » verbatim ; « — » si la
    fin n'a pas été publiée par Ym@ne (jamais d'invention)."""
    if not date_iso or not heure:
        return "—"
    a, m, j = date_iso.split("-")
    return f"{j}/{m}/{a} {heure}"


def _lignes_infractions(items) -> list[list]:
    """§0quinquies decies I3 AMENDÉ par §0octies decies L2 (27/08/2026) — les
    12 colonnes (écran = export, §A.2) ; les lignes INVALIDES exclues (I4)."""
    return [[s["date_jour"].split("-")[2] + "/" + s["date_jour"].split("-")[1]
             + "/" + s["date_jour"].split("-")[0],
             s["heure"] or "—",
             s["plaque"] or "—",
             s["chauffeur_affiche"] or "—",
             s["nom"] or "—",
             s["niveau"] or "—",
             s["seuil_libelle"] or "—",
             s["coordonnees"] or "—",
             _periode_txt(s["date_jour"], s["heure"]),
             _periode_txt(s.get("date_fin"), s.get("heure_fin")),
             {"NON_TRAITEE": "Non traitée", "VALIDE": "Valide",
              "INVALIDE": "Invalide"}.get(s["validation"], s["validation"]),
             s["observation"] or "—"] for s in items]


# §0octies decies L2/L3 (27/08/2026) — 12 colonnes, écran = export (§A.2)
HEADERS_INF = ["Date", "Heure", "Immatriculation", "Chauffeur", "Infraction",
               "Niveau", "Seuil", "Coordonnées GPS", "Début de l'infraction",
               "Fin de l'infraction", "Validation", "Observation"]


@router.get("/infractions/export.xlsx")
def export_infractions_xlsx(du: str | None = None, au: str | None = None,
                            famille: str | None = None,
                            db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    d_du, d_au = _periode(du, au)
    # I4 : les lignes invalidées sont EXCLUES des exports (§A.2 : écran = export
    # sur le périmètre comptabilisé) ; jamais supprimées de la base.
    items = db.scalars(_query_infractions(db, d_du, d_au, famille=famille)
                       .where(Infraction.validation != "INVALIDE")
                       .order_by(Infraction.date_jour.desc(), Infraction.heure.desc())).all()
    contenu = export_excel(f"Infractions {d_du:%d/%m/%Y} - {d_au:%d/%m/%Y}",
                           HEADERS_INF, _lignes_infractions(
                               [s_infraction(i) for i in items]))
    return Response(contenu, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=infractions.xlsx"})


@router.get("/infractions/export.pdf")
def export_infractions_pdf(du: str | None = None, au: str | None = None,
                           famille: str | None = None,
                           db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    d_du, d_au = _periode(du, au)
    items = db.scalars(_query_infractions(db, d_du, d_au, famille=famille)
                       .where(Infraction.validation != "INVALIDE")
                       .order_by(Infraction.date_jour.desc(), Infraction.heure.desc()).limit(1200)).all()
    contenu = export_pdf(f"Infractions du {d_du:%d/%m/%Y} au {d_au:%d/%m/%Y}",
                         HEADERS_INF, _lignes_infractions(
                             [s_infraction(i) for i in items]))
    return Response(contenu, media_type="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=infractions.pdf"})


# ============================== ALERTES ==============================
class AlerteStatutIn(BaseModel):
    statut: str  # VUE | TRAITEE


@router.get("/alertes")
def liste_alertes(du: str | None = None, au: str | None = None,
                  statut: str | None = None, gravite: str | None = None,
                  type: str | None = None, vehicule_id: str | None = None,
                  limite: int = 500,
                  db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    d_du, d_au = _periode(du, au)
    borne_sup = datetime.combine(d_au, datetime.max.time())
    q = select(Alerte).where(Alerte.date_heure >= datetime.combine(d_du, datetime.min.time()),
                             Alerte.date_heure <= borne_sup)
    if statut:
        q = q.where(Alerte.statut == statut)
    if gravite:
        q = q.where(Alerte.gravite == gravite)
    if type:
        q = q.where(Alerte.type == type)
    if vehicule_id:
        q = q.where(Alerte.vehicule_id == vehicule_id)
    items = db.scalars(q.order_by(Alerte.date_heure.desc()).limit(limite)).all()
    return {"types": TYPES_ALERTE, "items": [s_alerte(a) for a in items]}


@router.get("/alertes/compteurs")
def compteurs_alertes(db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    non_vues = db.scalar(select(func.count(Alerte.id)).where(Alerte.statut == StatutAlerte.NOUVELLE)) or 0
    par_gravite = {g.value: db.scalar(select(func.count(Alerte.id)).where(
        Alerte.statut == StatutAlerte.NOUVELLE, Alerte.gravite == g)) or 0
        for g in GraviteAlerte}
    return {"non_vues": non_vues, "par_gravite": par_gravite}


@router.post("/alertes/{aid}/statut")
def maj_alerte(aid: str, data: AlerteStatutIn, db: Session = Depends(get_db),
               user=Depends(require_roles(*ECRITURE))):
    a = db.get(Alerte, aid)
    if a is None:
        raise HTTPException(404, "Alerte introuvable")
    a.statut = StatutAlerte(data.statut)
    db.commit()
    return s_alerte(a)


@router.post("/alertes/tout-marquer-vues")
def tout_marquer_vues(db: Session = Depends(get_db), _=Depends(require_roles(*ECRITURE))):
    nb = db.query(Alerte).filter(Alerte.statut == StatutAlerte.NOUVELLE) \
        .update({Alerte.statut: StatutAlerte.VUE}, synchronize_session=False)
    db.commit()
    return {"marquees": nb}


@router.get("/alertes/export.xlsx")
def export_alertes_xlsx(du: str | None = None, au: str | None = None,
                        db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    d_du, d_au = _periode(du, au)
    items = db.scalars(select(Alerte).where(
        Alerte.date_heure >= datetime.combine(d_du, datetime.min.time()),
        Alerte.date_heure <= datetime.combine(d_au, datetime.max.time()))
        .order_by(Alerte.date_heure.desc())).all()
    headers = ["Date/heure", "Type", "Gravité", "Véhicule", "Conducteur", "Message", "Statut"]
    rows = [[a.date_heure.strftime("%d/%m/%Y %H:%M"),
             TYPES_ALERTE.get(a.type.value, a.type.value), a.gravite.value,
             a.vehicule.plaque if a.vehicule else "—",
             a.conducteur.prenom_usuel if a.conducteur else "—",
             a.message, a.statut.value] for a in items]
    contenu = export_excel(f"Alertes {d_du:%d/%m/%Y} - {d_au:%d/%m/%Y}", headers, rows)
    return Response(contenu, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=alertes.xlsx"})


@router.get("/alertes/export.pdf")
def export_alertes_pdf(du: str | None = None, au: str | None = None,
                       db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    d_du, d_au = _periode(du, au)
    items = db.scalars(select(Alerte).where(
        Alerte.date_heure >= datetime.combine(d_du, datetime.min.time()),
        Alerte.date_heure <= datetime.combine(d_au, datetime.max.time()))
        .order_by(Alerte.date_heure.desc()).limit(1200)).all()
    headers = ["Date/heure", "Type", "Gravité", "Véhicule", "Message", "Statut"]
    rows = [[a.date_heure.strftime("%d/%m/%Y %H:%M"),
             TYPES_ALERTE.get(a.type.value, a.type.value), a.gravite.value,
             a.vehicule.plaque if a.vehicule else "—", a.message, a.statut.value] for a in items]
    contenu = export_pdf(f"Alertes du {d_du:%d/%m/%Y} au {d_au:%d/%m/%Y}", headers, rows)
    return Response(contenu, media_type="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=alertes.pdf"})
