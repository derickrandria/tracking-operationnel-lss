"""§0sexies (arbitrages LSS du 20/08/2026, A2/A3) — Client API OData MZoneX.

API PUBLIQUE de la plateforme MZoneX, découverte et validée le 20/08/2026 :
``https://live.mzoneweb.net/mzone62.api/`` (OData v4, ~277 ensembles). Elle
livre, avec le compte LSS existant, l'équivalent EXACT de ce que le portail
affiche — en direct au lieu de captures d'écran pilotées :

- ``VehicleGroups``        → groupes du compte (le groupe flotte a 37 unités) ;
- ``Vehicles``             → flotte publiée (description « 8086 TCB (LSS) ») —
  source D4 (§0quinquies) en UN appel, sans virtualisation (correctif v1.24
  rendu définitif) ;
- ``Events``               → fil d'événements (positions/alertes, horodaté UTC
  à la seconde) — Niveau 1 à cadence arbitrée 60 s (§0sexies A1) ;
- ``Trips``                → trajets officiels calculés par MZoneX — Niveau 2,
  mêmes seuils métier côté portail (remplacement verbatim).

Contrats respectés : les points mappés entrent dans le MÊME
``CollectorBase.inserer`` / ``ingest_event`` (§7.1) et les trajets dans la
MÊME ``normaliser_valides``/réconciliation que les lectures d'écran — aucune
règle métier ne change (§1-§12, §0bis→§0quinquies).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import urllib.parse
from datetime import datetime, timedelta, timezone
import httpx

socket.setdefaulttimeout(10.0)

from .concurrence import chrono, compter, restant_budget, verifier_etape
from .config import TZ, plaque_depuis_libelle_portail
from .oauth_mzonex import ErreurAuthMZoneX, gestionnaire

log = logging.getLogger("lss.api_mzonex")

BASE_API = os.getenv("MZONEX_API_BASE_URL",
                     "https://live.mzoneweb.net/mzone62.api").rstrip("/")
_UA = "LSS-Tracking/1.25"
_TIMEOUT = float(os.getenv("MZONEX_API_TIMEOUT_S", "30.0"))
# Fenêtre de relecture Niveau 1 : 3 min de chevauchement (l'anti-rejeu existant
# dédoublonne) ; 1ʳᵉ passe calibrée sur 15 min pour un payload léger et rapide.
CHEVAUCHEMENT_S = int(os.getenv("MZONEX_API_CHEVAUCHEMENT_S", "180"))
# §0nonies decies M3 (arbitrage LSS du 29/08/2026) — PROFONDEUR de la fenêtre
# de relecture N1 : un boîtier « muet » (zone sans couverture GSM) qui renvoie
# son tampon avec moins de 3 h de retard enrichit la journée EN COURS (volume
# mesuré en direct le 29/08 : ~600 événements/15 min, très loin du plafond
# technique) ; au-delà de 3 h, la relecture des jours passés (M1) fait foi.
# ⚠️ NE PAS confondre avec la TAILLE DES TRANCHES ci-dessous : la profondeur
# historique (3 h) et le découpage du payload (15 min) sont DEUX réglages
# INDÉPENDANTS. Une collecte toutes les 15 minutes ne limite jamais à 15
# minutes l'historique relu (régression du 12/09 corrigée le 18/09).
FENETRE_MAX_S = int(os.getenv("MZONEX_API_FENETRE_MAX_S", "10800"))
# Découpage du payload en tranches de 15 min (une requête OData par tranche) :
# c'est CE réglage qui garde les réponses légères et rapides. Une fenêtre de
# 3 h est servie en 12 tranches de 15 min — jamais tronquée.
TRANCHE_S = int(os.getenv("MZONEX_API_TRANCHE_S", "900"))
# Garde-fou anti-régression (18/09/2026) : une profondeur PLUS PETITE que la
# tranche signifie que le réglage a été confondu avec la taille du payload —
# c'est exactement la régression du 12/09. Signalé fort, jamais silencieux.
if FENETRE_MAX_S <= max(60, TRANCHE_S):
    logging.getLogger("lss.api_mzonex").warning(
        "MZONEX_API_FENETRE_MAX_S=%s s est INFÉRIEUR à la tranche de payload "
        "(%s s) : la profondeur de rattrapage des boîtiers muets est "
        "anormalement faible (attendu : 10800 s = 3 h).",
        FENETRE_MAX_S, TRANCHE_S)
DECALAGE_PUBLICATION_S = int(os.getenv("MZONEX_API_DECALAGE_S", "20"))
MAX_PAGES = int(os.getenv("MZONEX_API_MAX_PAGES", "10"))
TAILLE_PAGE = int(os.getenv("MZONEX_API_TAILLE_PAGE", "200"))


class ErreurApiMZoneX(RuntimeError):
    """Échec de lecture de l'API MZoneX (réseau, 4xx/5xx, schéma)."""


def iso_utc(dt: datetime) -> str:
    """datetime naïf LOCAL → chaîne UTC « …Z » paramètre de requête."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def depuis_utc(texte: str | None) -> datetime | None:
    """Chaîne UTC de l'API (« …Z ») → datetime NAÏVE en heure locale (§8)."""
    if not texte:
        return None
    try:
        dt = datetime.fromisoformat(texte.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(TZ).replace(tzinfo=None)


def plaque_depuis_ligne_api(libelle: str | None) -> str | None:
    """« 8086 TCB » / « 8086 TCB (LSS) » → « 8086TCB » (garde D3 §0quater)."""
    if not libelle:
        return None
    plaque = plaque_depuis_libelle_portail(str(libelle))
    return plaque or None


def point_depuis_evenement_api(v: dict, map_vehicules: dict[str, str] | None = None) -> dict | None:
    """Événement API → point brut du contrat CollectorBase (§0sexies A2).

    Type laissé à None : ``ingest_event`` (§7.1) déroule lui-même la machine à
    états vitesse>3 km/h (§0ter) — ouverture/clôture des lignes, R2, garde de
    mouvement v1.24 — exactement comme pour un point « Début/Arrêt » du portail.
    """
    plaque = plaque_depuis_ligne_api(
        v.get("vehicle_Registration") or v.get("vehicle_Description"))
    if not plaque and map_vehicules and v.get("vehicle_Id"):
        plaque = map_vehicules.get(str(v.get("vehicle_Id")))
    ts = depuis_utc(v.get("utcTimestamp"))
    lat, lng = v.get("latitude"), v.get("longitude")
    if plaque is None or ts is None or lat is None or lng is None:
        return None
    return {"gps_associe": plaque, "horodatage": ts,
            "lat": float(lat), "lng": float(lng),
            "adresse": None,                       # l'API Events ne géocode pas
            "vitesse": max(0.0, float(v.get("speed") or 0.0)),
            "moteur": "ON",                        # le boîtier émet ⇒ contact ON
            "type_evenement": None,
            # §0septies B5 — clé chauffeur portée EN DIRECT par le signal
            "badge_code": _entier(v, "driverKeyCode")}


def _entier(t: dict, cle: str) -> int | None:
    v = t.get(cle)
    if v is None:
        return None
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return None


def trajet_depuis_api(t: dict, map_vehicules: dict[str, str] | None = None) -> dict | None:
    """Trajet officiel API → item du contrat réconciliation (§2.4).

    Même forme que les lignes de l'onglet « Trajets » lu à l'écran : début/fin
    à la seconde, distance portail, incomplétude admise (fin None = en cours).

    §0septies B2/B3 (20/08/2026) : le Trips MZoneX porte en plus le conducteur
    (clé iButton) et les compteurs d'écoconduite natifs — tout est transmis
    tel quel ; « autres » = total officiel moins les types détaillés (borne 0).
    """
    plaque = plaque_depuis_ligne_api(
        t.get("vehicle_Registration") or t.get("vehicle_Description"))
    if not plaque and map_vehicules and t.get("vehicle_Id"):
        plaque = map_vehicules.get(str(t.get("vehicle_Id")))
    debut = depuis_utc(t.get("startUtcTimestamp"))
    if plaque is None or debut is None:
        return None
    exc = {"exc_vitesse": _entier(t, "numberOfSpeedingExceptions"),
           "exc_freinage": _entier(t, "numberOfHarshBrakingExceptions"),
           "exc_accel": _entier(t, "numberOfExcessiveAccelerationExceptions"),
           "exc_ralenti": _entier(t, "numberOfExcessiveIdleExceptions"),
           "exc_surregime": _entier(t, "numberOfExcessiveRPMExceptions")}
    total = _entier(t, "numberOfExceptions")
    detail = sum(v for v in exc.values() if v is not None)
    exc["exc_autres"] = (max(0, total - detail)
                         if total is not None else None)
    vmax = t.get("maxSpeed")
    conducteur = (t.get("driver_Description") or "").strip() or None
    return {"plaque": plaque, "debut": debut,
            "fin": depuis_utc(t.get("endUtcTimestamp")),
            "distance_km": (None if t.get("distance") is None
                            else max(0.0, float(t.get("distance") or 0))),
            "conducteur": conducteur,
            "badge_code": _entier(t, "driverKeyCode"),
            "v_max": (None if vmax is None else max(0.0, float(vmax))),
            **exc,
            "source": "MZONEX"}


class ApiMZoneX:
    """Client OData MZoneX (jeton OAuth2 automatique, repli amont garanti)."""

    def __init__(self, jetons=None):
        self._jetons = jetons or gestionnaire()
        self._groupe_id: str | None = None
        self._cache_map_vehicules: dict[str, str] = {}

    def map_vehicules(self) -> dict[str, str]:
        """Dictionnaire guid vehicle_Id -> plaque normalisée (avec cache interne)."""
        if self._cache_map_vehicules:
            return self._cache_map_vehicules
        vehs = self._pages("Vehicles?$orderby=description")
        res = {}
        for v in vehs:
            plaque = plaque_depuis_ligne_api(v.get("description") or v.get("registration"))
            vid = v.get("id")
            if vid and plaque:
                res[str(vid)] = plaque
        self._cache_map_vehicules = res
        return res

    def dernieres_positions(self) -> list[dict]:
        """Positions récentes des véhicules MZoneX issues de Vehicles (si publiées)."""
        vehs = self._pages("Vehicles?$orderby=description")
        points = []
        for v in vehs:
            plaque = plaque_depuis_ligne_api(v.get("description") or v.get("registration"))
            if not plaque:
                continue
            lat = v.get("lastKnownLatitude") or v.get("latitude")
            lng = v.get("lastKnownLongitude") or v.get("longitude")
            ts_str = v.get("lastEventUtcTimestamp") or v.get("utcTimestamp")
            ts = depuis_utc(ts_str) if ts_str else None
            if lat is not None and lng is not None and ts is not None:
                points.append({
                    "gps_associe": plaque,
                    "horodatage": ts,
                    "lat": float(lat),
                    "lng": float(lng),
                    "adresse": None,
                    "vitesse": max(0.0, float(v.get("speed") or 0.0)),
                    "moteur": "ON",
                    "type_evenement": None,
                    "badge_code": _entier(v, "driverKeyCode"),
                    # v1.49 — INSTANTANÉ ≠ ÉVÉNEMENT : la ligne « Vehicles »
                    # décrit l'état CONNU du camion (dernière position, dernière
                    # vitesse publiée), pas un fait horodaté. Le marquer
                    # `observation` interdit à la machine à états d'en ouvrir ou
                    # d'en fermer une ligne (engine.ingest_event) : sans cela,
                    # une photo prise pendant un trajet créait un DÉPART à
                    # l'heure de la photo (constat 17/09 : 15:32 affiché pour un
                    # trajet MZoneX commencé 14:08:56).
                    "observation": True
                })
        return points

    # ---------------------------------------------------------------- bas
    def _get(self, chemin_requete: str, reessai: bool = True) -> dict:
        # v1.54 — MÉTRIQUES PAR ÉTAPE : l'authentification (obtention du jeton,
        # potentiellement un aller-retour OAuth) et l'attente HTTP sont mesurées
        # séparément. Sans cela, un dépassement de budget ne pouvait pas dire si
        # le temps était passé à s'authentifier, à attendre le portail, à
        # paginer ou à écrire en base (trou d'observabilité du 21/09).
        with chrono("auth"):
            jeton = self._jetons.jeton()
        url = f"{BASE_API}/{chemin_requete}"
        headers = {
            "Authorization": "Bearer " + jeton,
            "User-Agent": _UA,
            "Accept": "application/json"
        }
        try:
            # R3 — le timeout HTTP se dimensionne sur le temps RESTANT : un
            # timeout de 30 s sur une passe qui n'a plus que 2 s garantissait un
            # dépassement. Hors passe, le timeout nominal reste inchangé.
            _restant = restant_budget()
            _timeout = _TIMEOUT if _restant is None else max(1.0, min(_TIMEOUT, _restant))
            with chrono("attente_http"):
                with httpx.Client(timeout=httpx.Timeout(_timeout, connect=min(5.0, _timeout))) as client:
                    resp = client.get(url, headers=headers)
                    if resp.status_code == 401 and reessai:
                        log.info("MZoneX API : jeton refusé (401) — ré-authentification")
                        self._jetons.invalider()
                        return self._get(chemin_requete, reessai=False)
                    if resp.status_code != 200:
                        corps = resp.text[:160]
                        raise ErreurApiMZoneX(
                            f"GET {chemin_requete.split('?')[0]} → HTTP {resp.status_code} : {corps}")
                    donnees = resp.json()
            return donnees
        except httpx.TimeoutException as e:
            raise TimeoutError(f"GET {chemin_requete.split('?')[0]} timeout après {_TIMEOUT}s : {e}") from e
        except Exception as e:
            raise ErreurApiMZoneX(
                f"GET {chemin_requete.split('?')[0]} injoignable : "
                f"{type(e).__name__} {e}") from e

    def _pages(self, chemin_requete: str) -> list:
        """Pagination par $skip (le serveur n'émet pas de @odata.nextLink).

        Correctif v1.46 (constat du 04/09/2026) : quand le plafond MAX_PAGES ×
        TAILLE_PAGE est atteint, la troncature était SILENCIEUSE — des données
        disparaissaient sans aucun log. Le plafond est maintenant signalé en
        WARNING (les volumes réels mesurés montent à ~2 600 évts/h, soit > 9 000
        sur une simple demi-journée : toute fenêtre large doit être découpée,
        cf. `evenements()` ci-dessous)."""
        lignes: list = []
        saute = 0
        sep = "&" if "?" in chemin_requete else "?"
        for _ in range(MAX_PAGES):
            # v1.54 — POINT D'ARRÊT CONTRÔLÉ : si le budget de la passe est
            # atteint, on s'arrête ICI (avant l'appel réseau) au lieu de
            # continuer en tâche de fond après avoir été déclaré en échec.
            verifier_etape("pagination")
            lot = self._get(
                f"{chemin_requete}{sep}$top={TAILLE_PAGE}&$skip={saute}")
            compter("nb_pages")
            vals = lot.get("value", [])
            compter("nb_lignes", len(vals))
            lignes.extend(vals)
            # R16 — point d'arrêt APRÈS chaque page : la suite n'est demandée
            # que si l'échéance n'est pas franchie.
            verifier_etape("pagination")
            if len(vals) < TAILLE_PAGE:
                break
            saute += TAILLE_PAGE
        else:
            log.warning(
                "MZoneX API — PLAFOND DE PAGINATION ATTEINT (%d pages × %d = "
                "%d lignes) pour « %s » : des DONNÉES ONT ÉTÉ TRONQUÉES — "
                "augmentez MZONEX_API_MAX_PAGES / MZONEX_API_TAILLE_PAGE ou "
                "découpez la fenêtre (le découpage horaire d'`evenements()` "
                "est là pour ça)", MAX_PAGES, TAILLE_PAGE, len(lignes),
                chemin_requete.split("?")[0])
            # Ne jamais transmettre un lot tronqué au moteur : le checkpoint
            # de la fenêtre sera marqué en échec et repris ultérieurement.
            raise ErreurApiMZoneX(
                f"pagination incomplète pour {chemin_requete.split('?')[0]} "
                f"({len(lignes)} lignes, plafond atteint)")
        return lignes

    # ---------------------------------------------------------------- flotte
    def groupe_flotte(self) -> str:
        """Guid du groupe publiant le plus de véhicules (le groupe « LSS »).

        Surcharge possible : MZONEX_API_GROUPE_ID (diagnostique §10).
        """
        force = os.getenv("MZONEX_API_GROUPE_ID", "").strip()
        if force:
            return force
        if self._groupe_id:
            return self._groupe_id
        data = self._get("VehicleGroups?$top=50")
        groupes = data.get("value", [])
        if not groupes:
            raise ErreurApiMZoneX("aucun groupe de véhicules publié")
        meilleur = max(groupes, key=lambda g: int(g.get("vehicleCount") or 0))
        self._groupe_id = str(meilleur["id"])
        log.info("MZoneX API : groupe flotte = %s (%s véhicule(s))",
                 self._groupe_id, meilleur.get("vehicleCount"))
        return self._groupe_id

    def recenser_flotte(self) -> list[str]:
        """D4 (§0quinquies) : libellés « 8086 TCB (LSS) » de la flotte publiée
        — UN appel, complet, sans virtualisation (supersède la moisson v1.24).
        """
        lignes = self._pages("Vehicles?$orderby=description")
        libelles = [str(v.get("description") or "").strip()
                    for v in lignes if v.get("description")]
        log.info("MZoneX API — recensement D4 : %d véhicule(s) publié(s)",
                 len(libelles))
        return libelles

    # ---------------------------------------------------------------- données
    def _fenetre(self, debut_utc: datetime, fin_utc: datetime) -> str:
        """Paramètres de requête — `debut_utc`/`fin_utc` = datetimes UTC NAÏVES."""
        return ("utcStartDate=%s&utcEndDate=%s&vehicleGroup_Id=%s"
                % (urllib.parse.quote(debut_utc.strftime("%Y-%m-%dT%H:%M:%SZ")),
                   urllib.parse.quote(fin_utc.strftime("%Y-%m-%dT%H:%M:%SZ")),
                   self.groupe_flotte()))

    def evenements(self, debut_utc: datetime, fin_utc: datetime) -> list[dict]:
        """Fil d'événements de la flotte sur [debut_utc ; fin_utc] (UTC naïves).

        Optimisation Payload v2026 : la fenêtre est découpée en tranches
        (`TRANCHE_S`, 15 min par défaut) pour éviter tout engorgement et garantir
        des temps de réponse < 2 s — la PROFONDEUR relue, elle, n'est jamais
        limitée par la tranche (une fenêtre de 3 h = 12 tranches)."""
        pas = timedelta(seconds=max(60, TRANCHE_S))
        evs: list[dict] = []
        borne = debut_utc
        while borne < fin_utc:
            verifier_etape("pagination")     # v1.54 — arrêt contrôlé par tranche
            bout = min(borne + pas, fin_utc)
            chemin = ("Events?" + self._fenetre(borne, bout)
                      + "&$orderby=utcTimestamp")
            evs.extend(self._pages(chemin))
            # R16 — point d'arrêt APRÈS chaque tranche lue.
            verifier_etape("pagination")
            borne = bout
        return evs

    def _utc_naive(self, d_locale: datetime) -> datetime:
        return d_locale.replace(tzinfo=TZ).astimezone(
            timezone.utc).replace(tzinfo=None)

    def trajets_jour_local(self, jour) -> list[dict]:
        """Trajets officiels du JOUR LOCAL donné (borne §8 gérée en amont) :
        00:00:00 → 23:59:59 locaux convertis en UTC."""
        debut_local = datetime(jour.year, jour.month, jour.day)
        fin_local = debut_local + timedelta(hours=23, minutes=59, seconds=59)
        chemin = ("Trips?" + self._fenetre(self._utc_naive(debut_local),
                                           self._utc_naive(fin_local))
                  + "&$orderby=startUtcTimestamp")
        return self._pages(chemin)

    def fenetre_incrementale(self, derniere_locale: datetime | None,
                             maintenant_locale: datetime) -> tuple:
        """Bornes UTC naïves de la fenêtre N1 :
        max(dernière − 3 min, maintenant − 30 min) → maintenant − 20 s
        (décalage de publication des boîtiers)."""
        fin_locale = maintenant_locale - timedelta(
            seconds=DECALAGE_PUBLICATION_S)
        plancher = fin_locale - timedelta(seconds=FENETRE_MAX_S)
        debut_locale = plancher
        if derniere_locale is not None:
            debut_locale = max(plancher,
                               derniere_locale - timedelta(
                                   seconds=CHEVAUCHEMENT_S))
        return self._utc_naive(debut_locale), self._utc_naive(fin_locale)
