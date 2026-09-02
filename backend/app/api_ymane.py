# -*- coding: utf-8 -*-
"""Collecteur **Ym@ne** (§0quinquies decies I1/I2/I5 — exécution du 26/08/2026).

Ym@ne = plateforme d'infractions de MZoneX (https://bi.camtrack.pro — appli
AngularJS/Spring-Tomcat Pumex, session applicative obligatoire).

BRANCHEMENT GRAVÉ (exécution de I5, loi §0quinquies decies — vérifié en
direct avec le compte LSS le 26/08/2026) :

- Session : ``POST /login/`` JSON ``{username, password, language}`` →
  ``errorCode: 200`` + cookie ``JSESSIONID`` ; la réponse porte
  ``customerid`` / ``affiliateid`` / ``transporterid`` (2004 pour LSS),
  réutilisés dans la requête du rapport — jamais recopiés à la main.
- Garde : ``GET /isvalidaccess`` → ``true`` ; sur réponse non-JSON ou
  HTTP 401/403/500 à la lecture → UNE re-connexion puis UNE nouvelle
  tentative (session expirée).
- Lecture : ``GET /detailedexceptionreport`` (``vehicleid=0`` = tous,
  ``exceptiontype=0`` = toutes familles, ``exceptionlevel=0`` = tous niveaux
  — le filtrage I2 reste souverain À L'IMPORT, jamais délégué au portail).
- Fenêtre : J-8→J à chaque cycle (amendement A-I5 du 26/08 — ABROGE le
  J-1→J de la v1.36 : Ym@ne peut traiter une infraction pendant plusieurs
  jours ; l'anti-doublon rend la relecture sans effet).

Règles (inchangées depuis l'origine) :
- I1 : seules des infractions Ym@ne entrent (exterieure=True) ; CamtrackPro
  n'y figure jamais.
- I2 : le niveau ``Recording`` (« enregistrement ») est filtré ICI, à la
  normalisation — il n'entre jamais en base ; seuls ALERTE et ALARME passent.
- I5 : collecte idempotente par ``exceptionid`` (``ymane_id``) sinon par clé
  naturelle — jamais de doublon, jamais de suppression.
"""
from __future__ import annotations

import http.cookiejar
import json as _json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

log = logging.getLogger("lss.api_ymane")

_TIMEOUT = 45


# v1.40 (27/08/2026) — §0septies decies K1, mise au point technique gravée :
# le transport HTTP d'Ym@ne est la bibliothèque STANDARD `urllib` — comme
# api_mzonex.py et api_wialon.py. AUCUN paquet à installer : fin définitive
# du ModuleNotFoundError « requests » qui rendait la collecte muette chez
# l'exploitant (échec AVANT même de pouvoir lever l'alerte K1).
class _Reponse:
    """Réponse HTTP minimale (même contrat que la façade « requests »
    historique : status_code / ok / text / json / raise_for_status)."""

    def __init__(self, statut: int, corps: bytes):
        self.status_code = statut
        self.ok = 200 <= statut < 400
        self._corps = corps

    @property
    def text(self) -> str:
        return self._corps.decode("utf-8", "replace")

    def json(self):
        return _json.loads(self.text)          # ValueError si non-JSON

    def raise_for_status(self) -> None:
        if not self.ok:
            raise urllib.error.HTTPError(
                "", self.status_code, f"HTTP {self.status_code}", None, None)


class _SessionUrl:
    """Session HTTP (cookies mémorisés — JSESSIONID) bâtie sur urllib."""

    def __init__(self) -> None:
        jar = http.cookiejar.CookieJar()
        self._ouvreur = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))

    def _ouvrir(self, req, timeout: int) -> _Reponse:
        try:
            with self._ouvreur.open(req, timeout=timeout) as r:
                return _Reponse(r.status, r.read())
        except urllib.error.HTTPError as e:      # réponse malgré tout (4xx/5xx)
            return _Reponse(e.code, e.read() or b"")
        # URLError / TimeoutError / OSError remontent — classifiés en
        # français par ymane_import._raison_fr (§0septies decies K1).

    def get(self, url: str, params: dict | None = None,
            timeout: int = _TIMEOUT, **_) -> _Reponse:
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        return self._ouvrir(urllib.request.Request(url, method="GET"), timeout)

    def post(self, url: str, json: dict | None = None,
             timeout: int = _TIMEOUT, **_) -> _Reponse:
        corps = _json.dumps(json).encode("utf-8") if json is not None else None
        req = urllib.request.Request(
            url, data=corps, method="POST",
            headers={"Content-Type": "application/json"})
        return self._ouvrir(req, timeout)

# §0quinquies decies A-I5 (26/08/2026, v1.37) : le traitement d'une
# infraction par Ym@ne peut dépasser 12 h — la fenêtre de relecture est
# gravée à 8 JOURS en arrière (ABROGE la fenêtre J-1→J de la v1.36 ;
# l'anti-doublon par exceptionid rend la relecture sans effet).
FENETRE_RELECTURE_J = 8

# Familles réelles constatées (24-25/08/2026) → libellé FR officiel du portail
# (prélevé sur /getcurrentlanguage en session française ; repli : clé brute).
NOMS_FAMILLES = {
    "menu_speeding": "Excès de Vitesse",
    "menu_harsh_acceleration": "Accélération Brusque",
    "menu_harsh_braking": "Freinage Brusque",
    "menu_continuous_driving": "Conduite Continue",
    "menu_daily_driving": "Conduite Journalière",
    "menu_night_driving": "Conduite de Nuit",
    "menu_daily_rest_time": "Temps de Repos Journalier",
    "menu_weekly_rest_time": "Temps de Repos Hebdomadaire",
}
# Famille → code TypeInfraction interne (best-effort documenté : le NOM Ym@ne
# prime à l'affichage ; le type ne sert qu'à l'icône/filtre historique).
TYPES_FAMILLES = {
    "Speeding": "EXCES_VITESSE",
    "Acceleration": "ACCELERATION_BRUSQUE",
    "HarshBrake": "FREINAGE_BRUSQUE",
    "ContinuousDrive": "DEPASSEMENT_TCC",
    "NightDrive": "DEPASSEMENT_TCC",
    "DailyDrive": "DEPASSEMENT_TCJ",
    "DailyRest": "DEPASSEMENT_TTJ",
    "WeeklyRest": "DEPASSEMENT_TTJ",
}
# Familles dont le seuil/les durées sont des HEURES décimales (4.50 = 4h30).
FAMILLES_HORAIRES = {"ContinuousDrive", "DailyDrive", "WeeklyDrive",
                     "DailyRest", "WeeklyRest"}


class YmaneDesactive(RuntimeError):
    """Levée quand le collecteur est sollicité alors qu'il est désactivé."""


def actif() -> bool:
    return os.getenv("YMANE_ACTIVE", "0") in ("1", "true", "oui")


class ApiYmane:
    """Session applicative Ym@ne + lecture des infractions d'une fenêtre.

    Identifiants : les MÊMES que MZoneX (consigne exploitant du 25/08).
    L'identité du compte (client/affilié/transporteur) provient de la
    réponse de connexion — surcharge possible par variables d'environnement.
    """

    def __init__(self, base_url: str | None = None,
                 utilisateur: str | None = None, mot_de_passe: str | None = None,
                 session=None):
        self.base = (base_url or os.getenv("YMANE_URL")
                     or "https://bi.camtrack.pro").rstrip("/")
        self.user = utilisateur or os.getenv("YMANE_USER") \
            or os.getenv("MZONEX_USER") or ""
        self.secret = mot_de_passe or os.getenv("YMANE_PASSWORD") \
            or os.getenv("MZONEX_PASSWORD") or ""
        self.langue = os.getenv("YMANE_LANGUE", "French")
        self.login_path = os.getenv("YMANE_LOGIN_PATH", "/login/")
        self.liste_path = os.getenv("YMANE_LISTE_PATH",
                                    "/detailedexceptionreport")
        self._http = session or _SessionUrl()    # urllib std (v1.40)
        self._connecte = False
        self._identite: dict = {}     # customerid / affiliateid / transporterid

    # ------------------------------------------------------------------ auth
    def _connecter(self) -> None:
        """Ouvre la session applicative (JSESSIONID) — méthode gravée I5."""
        if not actif():
            raise YmaneDesactive("Collecteur Ym@ne désactivé (YMANE_ACTIVE=0)")
        try:                                       # langue FR (libellés)
            self._http.get(f"{self.base}/changelanguage/{self.langue}",
                           timeout=_TIMEOUT)
        except OSError:                    # URLError/TimeoutError (v1.40, urllib std)
            log.debug("Ym@ne : changelanguage inaccessible — login tenté "
                      "quand même")
        r = self._http.post(self.base + self.login_path,
                            json={"username": self.user,
                                  "password": self.secret,
                                  "language": self.langue},
                            timeout=_TIMEOUT)
        r.raise_for_status()
        donnees = r.json()
        if donnees.get("errorCode") != 200:        # refus applicatif
            raise RuntimeError(
                "Ym@ne : authentification refusée — "
                f"{donnees.get('errorMessage')!r}")
        d = donnees.get("data") or {}
        # surcharge éventuelle par l'environnement ; sinon valeurs du compte
        self._identite = {
            "clientid": int(os.getenv("YMANE_CLIENT_ID")
                            or d.get("customerid") or 1),
            "affiliateid": int(os.getenv("YMANE_AFFILIATE_ID")
                               or d.get("affiliateid") or 1),
            "transporterid": int(os.getenv("YMANE_TRANSPORTER_ID")
                                 or d.get("transporterid") or 2004),
        }
        self._connecte = True
        log.info("Ym@ne : session ouverte — transporteur %s (%s)",
                 self._identite["transporterid"],
                 d.get("transportername") or d.get("username") or "?")

    def _session_valide(self) -> bool:
        try:
            r = self._http.get(f"{self.base}/isvalidaccess", timeout=_TIMEOUT)
            return r.ok and r.text.strip() == "true"
        except OSError:                    # URLError/TimeoutError (v1.40, urllib std)
            return False

    # -------------------------------------------------------------- lecture
    def _assure_connexion(self) -> None:
        """Garantit une session applicative ouverte AVANT toute requête
        (l'identité clientid/affiliateid/transporterid est lue sur la
        réponse de connexion — elle doit donc PRÉCÉDER le rapport)."""
        if not self._connecte or not self._session_valide():
            self._connecte = False
            self._connecter()

    def _get_json(self, chemin: str, **params):
        """GET JSON avec UNE re-connexion sur session expirée (garde I5)."""
        self._assure_connexion()
        r = self._http.get(self.base + chemin, params=params,
                           timeout=_TIMEOUT)
        if r.status_code in (401, 403, 500):
            log.warning("Ym@ne : HTTP %s sur %s — re-connexion puis nouvelle "
                        "tentative", r.status_code, chemin)
            self._connecte = False
            self._connecter()
            r = self._http.get(self.base + chemin, params=params,
                               timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def exceptions_fenetre(self, debut: date, fin: date) -> list[dict]:
        """Items BRUTS Ym@ne sur [debut, fin] inclus (dates ISO locales)."""
        if not actif():
            return []
        self._assure_connexion()   # AVANT de déplier self._identite : sur une
        # session fraîche l'identité est encore vide au moment de l'appel.
        brut = self._get_json(
            self.liste_path, **self._identite,
            vehicleid=0, exceptiontype=0, exceptionlevel=0,
            startdate=debut.isoformat(), enddate=fin.isoformat())
        return [x for x in brut if isinstance(x, dict)] if \
            isinstance(brut, list) else []

    def infractions_recentes(self, jour: date) -> list[dict]:
        """Fenêtre J-8→J (A-I5 du 26/08/2026 : Ym@ne peut traiter une
        infraction pendant plusieurs jours — elle serait invisible d'une
        fenêtre plus courte ; l'upsert par exceptionid rend le
        recouvrement sans effet)."""
        from datetime import timedelta
        return self.exceptions_fenetre(
            jour - timedelta(days=FENETRE_RELECTURE_J), jour)

    # ---------------------------------------------------------- normalisation
    @staticmethod
    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def normaliser(item: dict) -> dict | None:
        """Item brut Ym@ne → forme normalisée d'import, ou None si rejet.

        I2 : le niveau « Recording » (enregistrement) est écarté ICI — il
        n'entre jamais en base. Le seuil est conservé VERBATIM (jamais
        d'unité inventée) : km/h · heures décimales · plage horaire brute.
        """
        niveau = str(item.get("levellabel") or item.get("level") or "") \
            .strip().lower()
        if "recording" in niveau or "enregistrement" in niveau:
            return None                            # I2 : ne compte pas
        if "alarm" in niveau:
            niveau_n = "ALARME"
        elif "alert" in niveau:
            niveau_n = "ALERTE"
        else:
            return None                            # niveau inconnu : prudence

        parametre = str(item.get("parameter") or "").strip()
        nom = NOMS_FAMILLES.get(str(item.get("parameterlabel") or ""),
                                str(item.get("parameterlabel")
                                    or parametre or "Infraction"))[:160]
        debut = str(item.get("startdatetime") or "").strip()
        jour_txt, heure_txt = (debut.split(" ", 1) + [""])[:2] \
            if " " in debut else (debut, "")
        # §0octies decies L2 (27/08/2026) — fin VERBATIM du portail
        # (enddatetime publié pour toutes les familles, vérifié sur données
        # réelles ; peut être au J+1) ; absent → None (« — » à l'écran,
        # jamais d'invention).
        fin = str(item.get("enddatetime") or "").strip()
        fjour_txt, fheure_txt = (fin.split(" ", 1) + [""])[:2] \
            if " " in fin else (fin, "")
        seuil_brut = item.get("threshold")
        seuil_f = ApiYmane._f(seuil_brut)
        if parametre == "Speeding":
            seuil_unite = "kmh"
        elif parametre in FAMILLES_HORAIRES:
            seuil_unite = "s"
        else:
            seuil_unite = "brut"
        # coordonnées : Ym@ne publie "[longitude,latitude]"
        lat = lng = None
        brut_gps = str(item.get("startgps") or "").strip("[] ")
        if "," in brut_gps:
            a, b = brut_gps.split(",", 1)
            lng, lat = ApiYmane._f(a.strip()), ApiYmane._f(b.strip())
        duree_h = ApiYmane._f(item.get("totalduration"))
        valeur = ApiYmane._f(item.get("maxvalue"))
        if valeur is None and parametre in FAMILLES_HORAIRES:
            valeur = duree_h * 3600 if duree_h is not None else None
        return {
            "ymane_id": (str(item["exceptionid"])
                         if item.get("exceptionid") is not None else None),
            "nom": nom,
            "niveau": niveau_n,
            "plaque_brute": (str(item["vehiclename"]).strip()
                             if item.get("vehiclename") else None),
            "chauffeur": (str(item["drivername"]).strip()
                          if item.get("drivername") else None),
            "date_txt": jour_txt or None,
            "heure_txt": heure_txt or None,
            "fin_date_txt": fjour_txt or None,
            "fin_heure_txt": fheure_txt or None,
            "type_code": TYPES_FAMILLES.get(parametre, "DEPASSEMENT_TCC"),
            "seuil": (seuil_f * 3600 if seuil_unite == "s" and seuil_f
                      is not None else seuil_f),
            "seuil_unite": seuil_unite,
            "seuil_texte": (str(seuil_brut).strip()
                            if seuil_brut is not None and seuil_unite != "kmh"
                            else None),
            "valeur": valeur,
            "duree_s": int(round(duree_h * 3600)) if duree_h is not None
            else None,
            "distance_km": ApiYmane._f(item.get("distanceunderexception")),
            "lat": lat, "lng": lng,
        }
