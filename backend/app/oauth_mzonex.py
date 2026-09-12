"""§0sexies (arbitrage LSS du 20/08/2026, A3) — Authentification OAuth2 MZoneX.

MZoneX expose une API OData ouverte (découverte publique 20/08/2026) protégée
par un serveur d'identité OAuth2/OIDC (IdentityServer4) à
``login.mzoneweb.net``. Le client public du portail (« mz6 PKCE ») impose le
flux « code d'autorisation + PKCE » (le mot de passe seul est refusé par
conception). Ce module REJOUE ce flux officiel de bout en bout avec les
identifiants déjà configurés (les mêmes que le lecteur d'écran), puis entretient
le jeton automatiquement :

- jeton d'accès (1 h) servi depuis un cache disque tant qu'il est valide ;
- jeton d'actualisation (``offline_access``) utilisé en priorité — aucune
  reconnexion tant qu'il vit (rotation IS4 prise en charge) ;
- reconnexion complète automatique si l'actualisation est refusée.

Le fichier de jetons vit dans ``backend/data/`` (données locales, JAMAIS
zippées — la liste blanche exclut ``data/``). Aucun secret n'est journalisé.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.parse
import httpx

socket.setdefaulttimeout(10.0)

log = logging.getLogger("lss.api_mzonex")

SSO_URL = os.getenv("MZONEX_SSO_URL", "https://login.mzoneweb.net").rstrip("/")
CLIENT_ID = os.getenv("MZONEX_API_CLIENT_ID", "mz6 PKCE")
SCOPES = os.getenv("MZONEX_API_SCOPES",
                   "openid mz_username mz6-api.all offline_access")
URI_REDIRECTION = os.getenv("MZONEX_API_REDIRECT_URI",
                            "https://live.mzoneweb.net/mzonex/")
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_TIMEOUT = float(os.getenv("MZONEX_AUTH_TIMEOUT_S", "10.0"))

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data")
FICHIER_JETONS = os.getenv("MZONEX_API_JETONS",
                           os.path.join(_DATA_DIR, "mz_oauth.json"))


class ErreurAuthMZoneX(RuntimeError):
    """Échec d'authentification OAuth2 MZoneX (SSO, jeton, réseau)."""


def _post_formulaire(url: str, donnees: dict, timeout: float = _TIMEOUT) -> dict:
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
            resp = client.post(
                url, data=donnees,
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _UA}
            )
            if resp.status_code != 200:
                raise ErreurAuthMZoneX(f"POST {url} → HTTP {resp.status_code} : {resp.text[:200]}")
            return resp.json()
    except httpx.TimeoutException as e:
        raise TimeoutError(f"POST {url} timeout après {timeout}s : {e}") from e
    except Exception as e:
        raise ErreurAuthMZoneX(f"POST {url} injoignable : {type(e).__name__} {e}") from e


# ------------------------------------------------------------------ métier
def flux_code_pkce(username: str, password: str,
                   url_autorisation: str | None = None) -> dict:
    """Rejoue le flux officiel « code + PKCE » du portail MZoneX (navigateur)."""
    verificateur = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    defi = base64.urlsafe_b64encode(
        hashlib.sha256(verificateur.encode()).digest()).rstrip(b"=").decode()
    etat = base64.urlsafe_b64encode(os.urandom(12)).rstrip(b"=").decode()
    url_auth = url_autorisation or (
        SSO_URL + "/connect/authorize?" + urllib.parse.urlencode({
            "client_id": CLIENT_ID, "response_type": "code",
            "redirect_uri": URI_REDIRECTION, "scope": SCOPES,
            "state": etat, "nonce": etat, "code_challenge": defi,
            "code_challenge_method": "S256"}))

    timeout_cfg = httpx.Timeout(_TIMEOUT, connect=5.0)
    try:
        with httpx.Client(timeout=timeout_cfg, follow_redirects=True) as client:
            # 1) page de connexion
            r = client.get(url_auth, headers={"User-Agent": _UA})
            page = r.text
            url_login = str(r.url)
            if "Username" not in page:
                raise ErreurAuthMZoneX("page de connexion SSO inattendue (champ Username absent)")
            caches = {k: html.unescape(v) for k, v in
                      re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', page)}
            for k in ("Username", "Password"):
                caches.pop(k, None)
            post = {"Username": username, "Password": password, "button": "login", **caches}

        with httpx.Client(timeout=timeout_cfg, follow_redirects=False) as client_stop:
            # 2) POST identifiants (sans suivre) puis chaîne de redirections
            r2 = client_stop.post(
                url_login, data=post,
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _UA}
            )
            loc = r2.headers.get("Location")
            if not loc:
                raise ErreurAuthMZoneX(f"connexion SSO refusée (HTTP {r2.status_code}) — identifiants portail à vérifier")
            for _ in range(8):
                if loc.startswith(URI_REDIRECTION):
                    break
                if loc.startswith("/"):
                    loc = urllib.parse.urljoin(SSO_URL, loc)
                r3 = client_stop.get(loc, headers={"User-Agent": _UA})
                loc2 = r3.headers.get("Location")
                if not loc2:
                    break
                loc = loc2
            code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("code", [None])[0]
            if not code:
                raise ErreurAuthMZoneX("flux SSO terminé sans code d'autorisation")

        # 3) échange du code (+ preuve PKCE) contre les jetons
        return _post_formulaire(SSO_URL + "/connect/token", {
            "grant_type": "authorization_code", "client_id": CLIENT_ID,
            "redirect_uri": URI_REDIRECTION, "code": code,
            "code_verifier": verificateur})
    except httpx.TimeoutException as e:
        raise TimeoutError(f"SSO MZoneX timeout après {_TIMEOUT}s : {e}") from e


class GestionnaireJetonsMZoneX:
    """Sert un jeton d'accès valide, le renouvelle, le persiste (§0sexies A3).

    `fournir_login` / `fournir_refresh` injectables (tests unitaires).
    """

    def __init__(self, fichier: str | None = None,
                 fournir_login=None, fournir_refresh=None):
        self.fichier = fichier or FICHIER_JETONS
        self._verrou = threading.Lock()
        self._fournir_login = fournir_login or self._login_defaut
        self._fournir_refresh = fournir_refresh or self._refresh_defaut

    # ---- persistance (données locales, jamais zippées) --------------------
    def _charger(self) -> dict | None:
        try:
            with open(self.fichier, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _sauver(self, etat: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.fichier), exist_ok=True)
            tmp = self.fichier + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(etat, f)
            os.replace(tmp, self.fichier)
        except OSError:
            log.warning("Jetons MZoneX : écriture impossible (%s)",
                        self.fichier)

    def invalider(self) -> None:
        """Appelé sur 401 API : le prochain `jeton()` se ré-authentifiera."""
        try:
            os.unlink(self.fichier)
        except OSError:
            pass

    # ---- fournisseurs par défaut (réseau réel) -----------------------------
    @staticmethod
    def _login_defaut() -> dict:
        user = os.getenv("MZONEX_USER", "").strip()
        mdp = os.getenv("MZONEX_PASSWORD", "")
        if not user or not mdp:
            raise ErreurAuthMZoneX("MZONEX_USER / MZONEX_PASSWORD absents "
                                   "de backend/.env")
        return flux_code_pkce(user, mdp)

    @staticmethod
    def _refresh_defaut(refresh_token: str) -> dict:
        return _post_formulaire(SSO_URL + "/connect/token", {
            "grant_type": "refresh_token", "client_id": CLIENT_ID,
            "refresh_token": refresh_token})

    # ---- point d'entrée ----------------------------------------------------
    def jeton(self) -> str:
        """Renvoie un access_token valide (login/refresh transparents)."""
        with self._verrou:
            stock = self._charger()
            if stock and stock.get("expire", 0) > time.time() + 120:
                return stock["access_token"]
            if stock and stock.get("refresh_token"):
                try:
                    brut = self._fournir_refresh(stock["refresh_token"])
                except Exception as exc:
                    log.info("MZoneX API : actualisation refusée (%s) — "
                             "invalidation du jeton et reconnexion complète", exc)
                    self.invalider()
                else:
                    stock = {"access_token": brut["access_token"],
                             # rotation IS4 : conserver le NOUVEAU refresh s'il
                             # est fourni, sinon garder l'ancien
                             "refresh_token": brut.get("refresh_token")
                                              or stock["refresh_token"],
                             "expire": time.time() + int(
                                 brut.get("expires_in", 3600))}
                    self._sauver(stock)
                    log.info("MZoneX API : jeton actualisé (valide ~1 h)")
                    return stock["access_token"]
            try:
                brut = self._fournir_login()
            except Exception:
                self.invalider()
                raise
            stock = {"access_token": brut["access_token"],
                     "refresh_token": brut.get("refresh_token"),
                     "expire": time.time() + int(brut.get("expires_in", 3600))}
            self._sauver(stock)
            log.info("MZoneX API : connecté au serveur d'identité MZone "
                     "(jeton ~1 h, actualisation automatique)")
            return stock["access_token"]


_INSTANCE: GestionnaireJetonsMZoneX | None = None
_VERROU_INSTANCE = threading.Lock()


def gestionnaire() -> GestionnaireJetonsMZoneX:
    """Singleton applicatif (verrouillé pour les threads collecteurs)."""
    global _INSTANCE
    with _VERROU_INSTANCE:
        if _INSTANCE is None:
            _INSTANCE = GestionnaireJetonsMZoneX()
        return _INSTANCE
