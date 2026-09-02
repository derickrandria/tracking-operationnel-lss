"""Sécurité : hachage des mots de passe, JWT, RBAC (matrice de rôles §11)."""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import JWT_ALG, JWT_SECRET, TOKEN_TTL_MIN
from .database import get_db

_bearer = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------- mots de passe
_ITER = 120_000


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER)
    return f"pbkdf2${_ITER}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, hexa = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), hexa)
    except Exception:
        return False


# ---------------------------------------------------------------- JWT
def create_token(user) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "nom": user.nom_complet,
        "role": user.role.value if hasattr(user.role, "value") else user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=TOKEN_TTL_MIN)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        return None


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
):
    from .models import User  # import tardif (évite les cycles)

    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token manquant")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalide ou expiré")
    user = db.get(User, payload["sub"])
    if user is None or not user.actif:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utilisateur inconnu ou désactivé")
    return user


def require_roles(*roles: str):
    """Fabrique une dépendance FastAPI exigeant un des rôles donnés.

    Matrice (§2/§11) :
      - ADMIN        : accès total (CRUD tous modules + configuration)
      - TRACKING     : lecture/écriture modules opérationnels
      - CONSULTATION : lecture seule + export
    """

    def dep(user=Depends(get_current_user)):
        role = user.role.value if hasattr(user.role, "value") else user.role
        if role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Droits insuffisants")
        return user

    return dep


TOUS = ("ADMIN", "TRACKING", "CONSULTATION")
ECRITURE = ("ADMIN", "TRACKING")
ADMIN = ("ADMIN",)


# ---------------------------------------------------------------- audit (§11)
def audit(db: Session, user, action: str, entite: str, entite_id: str, details: dict | None = None):
    """Journal d'audit : utilisateur, date/heure, valeur avant/après."""
    from .models import AuditLog
    from .config import now_local

    username = getattr(user, "username", None) or str(user or "système")
    db.add(AuditLog(
        date_heure=now_local(),
        username=username,
        action=action,
        entite=entite,
        entite_id=str(entite_id),
        details=details or {},
    ))
