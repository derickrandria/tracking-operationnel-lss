"""Authentification JWT (§2/§11)."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..security import (create_token, get_current_user, verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


def _user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "nom_complet": u.nom_complet,
            "role": u.role.value if hasattr(u.role, "value") else u.role}


@router.post("/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == data.username.strip().lower()))
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides")
    if not user.actif:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Compte désactivé")
    return {"access_token": create_token(user), "token_type": "bearer", "user": _user_dict(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_dict(user)


@router.post("/refresh")
def refresh(user: User = Depends(get_current_user)):
    """Rafraîchissement de token (§11) : réémet un token pour un token valide."""
    return {"access_token": create_token(user), "token_type": "bearer",
            "user": _user_dict(user)}
