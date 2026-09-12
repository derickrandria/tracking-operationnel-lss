"""Administration (§5.8, §7.4, §11) : seuils, utilisateurs, journal d'audit."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..engine import invalider_cache_seuils
from ..event_bus import publish
from ..models import AuditLog, ParametrageSeuil, Role, User
from ..security import ADMIN, TOUS, audit, hash_password, require_roles
from ..serializers import iso

router = APIRouter(prefix="/api", tags=["admin"])


# ------------------------------------------------------------------ seuils
@router.get("/parametres")
def liste_parametres(db: Session = Depends(get_db), _=Depends(require_roles(*TOUS))):
    rows = db.scalars(select(ParametrageSeuil).order_by(ParametrageSeuil.cle)).all()
    return [{"cle": r.cle, "valeur": r.valeur, "type_valeur": r.type_valeur,
             "description": r.description} for r in rows]


class SeuilIn(BaseModel):
    valeur: float


@router.patch("/parametres/{cle}")
def maj_parametre(cle: str, data: SeuilIn, db: Session = Depends(get_db),
                  user=Depends(require_roles(*ADMIN))):
    r = db.scalar(select(ParametrageSeuil).where(ParametrageSeuil.cle == cle))
    if r is None:
        raise HTTPException(404, "Paramètre inconnu")
    avant = r.valeur
    r.valeur = data.valeur
    audit(db, user, "parametre.modification", "parametre", cle,
          {"avant": avant, "apres": data.valeur})
    db.commit()
    invalider_cache_seuils()  # prise en compte immédiate, sans toucher au code
    publish("parametres.update", {"cle": cle, "valeur": data.valeur})
    return {"cle": r.cle, "valeur": r.valeur, "type_valeur": r.type_valeur,
            "description": r.description}


# ------------------------------------------------------------------ audit
@router.get("/audit")
def journal_audit(limite: int = 200, db: Session = Depends(get_db),
                  _=Depends(require_roles(*ADMIN))):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.date_heure.desc())
                      .limit(limite)).all()
    return [{"id": r.id, "date_heure": iso(r.date_heure), "username": r.username,
             "action": r.action, "entite": r.entite, "entite_id": r.entite_id,
             "details": r.details} for r in rows]


# ------------------------------------------------------------------ §0duodecies F4
@router.get("/diagnostic/journee")
def diagnostic_journee(plaque: str, jour: str | None = None,
                       db: Session = Depends(get_db),
                       _=Depends(require_roles(*TOUS))):
    """Rapport de diagnostic exportable (arbitrage LSS F4 du 25/08/2026) :
    fichier texte horodaté (lignes du jour toutes statuts, compteurs,
    événements GPS, actions automatiques du journal) — lecture seule."""
    from datetime import date as _date, datetime as _dt
    from fastapi.responses import PlainTextResponse
    from ..config import jour_attribution, now_local
    from ..diagnostic import construire_rapport_diagnostic

    if jour:
        try:
            jour_d = _dt.fromisoformat(jour).date()
        except ValueError:
            raise HTTPException(400, "Format de date attendu : AAAA-MM-JJ")
    else:
        jour_d = jour_attribution(now_local())
    if not (plaque or "").strip():
        raise HTTPException(400, "Paramètre « plaque » requis (ex. 0926TBV)")
    texte = construire_rapport_diagnostic(db, plaque, jour_d)
    nom = f"diagnostic_{plaque.strip().replace(' ', '')}_{jour_d.isoformat()}.txt"
    return PlainTextResponse(texte, media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition":
                                      f'attachment; filename="{nom}"'})


# ------------------------------------------------------------------ utilisateurs
class UserIn(BaseModel):
    username: str
    password: str
    nom_complet: str
    role: str = "CONSULTATION"


class UserPatch(BaseModel):
    actif: bool | None = None
    role: str | None = None
    password: str | None = None
    nom_complet: str | None = None


def _u(u: User):
    return {"id": u.id, "username": u.username, "nom_complet": u.nom_complet,
            "role": u.role.value if hasattr(u.role, "value") else u.role,
            "actif": u.actif, "date_creation": iso(u.date_creation)}


@router.get("/utilisateurs")
def liste_utilisateurs(db: Session = Depends(get_db), _=Depends(require_roles(*ADMIN))):
    return [_u(u) for u in db.scalars(select(User).order_by(User.username)).all()]


@router.post("/utilisateurs", status_code=201)
def creer_utilisateur(data: UserIn, db: Session = Depends(get_db),
                      user=Depends(require_roles(*ADMIN))):
    username = data.username.strip().lower()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "Nom d'utilisateur déjà pris")
    u = User(username=username, password_hash=hash_password(data.password),
             nom_complet=data.nom_complet, role=Role(data.role))
    db.add(u)
    db.flush()
    audit(db, user, "utilisateur.creation", "utilisateur", u.id, {"username": username})
    db.commit()
    return _u(u)


@router.patch("/utilisateurs/{uid}")
def maj_utilisateur(uid: str, data: UserPatch, db: Session = Depends(get_db),
                    user=Depends(require_roles(*ADMIN))):
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "Utilisateur introuvable")
    champs = data.model_dump(exclude_unset=True)
    if "password" in champs and champs["password"]:
        u.password_hash = hash_password(champs.pop("password"))
    if "role" in champs and champs["role"]:
        u.role = Role(champs["role"])
    if "actif" in champs and champs["actif"] is not None:
        u.actif = champs["actif"]
    if "nom_complet" in champs and champs["nom_complet"]:
        u.nom_complet = champs["nom_complet"]
    audit(db, user, "utilisateur.modification", "utilisateur", uid,
          {"champs": list(champs.keys())})
    db.commit()
    return _u(u)


@router.get("/diagnostic/portails")
def diagnostic_connexions_portails(db: Session = Depends(get_db)):
    """Vérification en direct de la connectivité et de l'état des API MZoneX et CamtrackPro."""
    from ..api_mzonex import ApiMZoneX
    from ..api_wialon import ApiWialon, jeton_configure
    from ..config import now_local

    diag = {
        "maintenant": now_local().isoformat(),
        "mzonex": {"actif": False, "erreur": None, "flotte_recensee": 0, "token_ok": False},
        "camtrackpro": {"actif": False, "erreur": None, "unites_trouvees": 0, "token_ok": False},
    }

    # Test MZoneX API
    try:
        mz = ApiMZoneX()
        flotte = mz.recenser_flotte()
        diag["mzonex"]["actif"] = True
        diag["mzonex"]["token_ok"] = True
        diag["mzonex"]["flotte_recensee"] = len(flotte)
    except Exception as e:
        diag["mzonex"]["erreur"] = f"{type(e).__name__}: {str(e)}"

    # Test CamtrackPro (Wialon API)
    try:
        if jeton_configure():
            api_w = ApiWialon()
            try:
                sid = api_w.connecter()
                diag["camtrackpro"]["token_ok"] = bool(sid)
                unites = api_w.unites()
                diag["camtrackpro"]["actif"] = True
                diag["camtrackpro"]["unites_trouvees"] = len(unites)
            finally:
                api_w.fermer()
        else:
            diag["camtrackpro"]["erreur"] = "CAMTRACKPRO_TOKEN absent ou non configuré"
    except Exception as e:
        diag["camtrackpro"]["erreur"] = f"{type(e).__name__}: {str(e)}"

    return diag
