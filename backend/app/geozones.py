# -*- coding: utf-8 -*-
"""§0septies B4 (arbitrage LSS 20/08/2026) — géozones des DEUX portails.

Sert à trancher, pour chaque position reçue, la question « en zone ou hors
zone ? » :
  · HORS zone → l'alerte vitesse en direct (45 km/h, B4) s'applique ;
  · DANS une zone → silence : les SEUILS DES PORTAILS gouvernent (consommés
    via les compteurs officiels du carnet de conduite, B3).

Sources (lecture seule, cadence 6 h — une géozone bouge rarement) :
  · MZoneX      : `Places` (centreLatitude/centerLongitude + rayon « buffer »,
                  secours : emprise left/right/top/bottom) — 749 zones au
                  20/08/2026 ;
  · CamtrackPro : géozones de la ressource (« zl », centre + emprise) — rayon
                  inscrit à 70 % de l'emprise (choix d'implémentation inscrit
                  en loi, B4) — 450 zones au 20/08/2026.

Robustesse §10 : toute panne de chargement conserve l'ancien cache ; s'il
n'existe aucun cache, un point est considéré HORS zone (choix inscrit en loi)
et l'indisponibilité est journalisée une fois.
"""
import logging
import math
import os
import threading
import time

log = logging.getLogger("lss.geozones")

_PERIODE_CACHE_S = int(os.getenv("GEOZONES_PERIODE_CACHE_S", str(6 * 3600)))
_FACTOR_CTPRO = 0.7          # rayon inscrit à 70 % de l'emprise (loi B4)
_RAYON_DEFAUT_M = 300.0      # zone sans géométrie exploitable : cercle minimal

_lock = threading.Lock()
_zones: list[tuple[float, float, float, str, str]] = []   # lat, lng, r_m, nom, portail
_charge_ts = 0.0
_indisponible_note = False


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _rayon_depuis_emprise(min_lat, max_lat, min_lng, max_lng, facteur=1.0):
    """Rayon à partir d'une emprise rectangulaire (mètres, borné ≥ 30 m)."""
    try:
        demi_h = _haversine_m(min_lat, min_lng, max_lat, min_lng) / 2
        demi_w = _haversine_m(min_lat, min_lng, min_lat, max_lng) / 2
        return max(30.0, facteur * min(demi_h, demi_w))
    except (TypeError, ValueError):
        return _RAYON_DEFAUT_M


_DEGRES_EN_M = 111320.0
# §0undecies E4 (24/08/2026) — le buffer des Places MZoneX est en DEGRÉS
# (corrigé : il était consommé en mètres → les zones MZoneX n'étaient jamais
# reconnues, ni pour B4 ni pour le nommage de position).
_RAYON_NOMMAGE_MAX_M = 5000.0   # on ne nomme pas avec une zone géante (région)
_PROXIMITE_NOMMAGE_M = 5000.0   # repli générique : « proche <nom> » si centre < 5 km
# §0unvicies decies O3 (arbitrage LSS du 01/09/2026, amendement E4 extension #1)
# — un lieu PRÉCIS (onglet « Lieux » MZoneX / « Zones » CamtrackPro, hors
# référentiel automatique -CC/-TOWN) est cherché d'abord, mesuré à son CENTRE
# à moins de 3 km : un dépôt précis à 1 km gagne face à une grande zone de
# ville ; le bord-à-bord (qui favorisait les grosses zones) est abandonné.
_PROXIMITE_PRECISE_M = 3000.0   # « proche <lieu précis> » si centre < 3 km
_GENERIQUE_MARQUEURS = ("-CC", "-TOWN")   # référentiel automatique (24 zones)


def _est_generique(nom: str) -> bool:
    """True si la zone appartient au référentiel automatique de villes/quartiers
    (noms MZoneX « …-CC » / « …-TOWN ») — dernier recours de nommage (O3)."""
    n = (nom or "").upper()
    return any(m in n for m in _GENERIQUE_MARQUEURS)


def _rayon_mzonex_m(p: dict) -> float:
    """Rayon (mètres) d'une Place MZoneX : `buffer` en DEGRÉS (loi E4,
    correctif d'implémentation), sinon emprise rectangulaire."""
    try:
        buffer_deg = float(p.get("buffer") or 0)
    except (TypeError, ValueError):
        buffer_deg = 0.0
    if buffer_deg > 0:
        return max(30.0, buffer_deg * _DEGRES_EN_M)
    return _rayon_depuis_emprise(
        p.get("bottomLatitude"), p.get("topLatitude"),
        p.get("leftLongitude"), p.get("rightLongitude"))


def _charger_mzonex() -> list[tuple]:
    """`Places` OData : centre + buffer (rayon) — pagination $skip native."""
    from .api_mzonex import ApiMZoneX
    zones = []
    api = ApiMZoneX()
    saute = 0
    while True:
        lot = api._get(f"Places?$top=500&$skip={saute}").get("value", [])
        for p in lot:
            lat, lng = p.get("centerLatitude"), p.get("centerLongitude")
            if lat is None or lng is None:
                continue
            zones.append((float(lat), float(lng), _rayon_mzonex_m(p),
                          (p.get("description") or "?")[:80], "MZONEX"))
        if len(lot) < 500:
            break
        saute += 500
    return zones


def _charger_camtrackpro() -> list[tuple]:
    """Géozones « zl » de la ressource Wialon — rayon inscrit 70 % (loi B4)."""
    import json
    import urllib.parse
    import urllib.request
    from .api_wialon import ApiWialon, API_URL, jeton_configure
    if not jeton_configure():
        return []
    zones = []
    api = ApiWialon()
    try:
        api.connecter()
        tous = (1 | 2 | 4 | 8 | 16 | 32 | 128 | 256 | 512 | 1024 | 2048
                | 4096 | 8192 | 16384 | 32768 | 65536)
        params = {"spec": {"itemsType": "avl_resource", "propName": "sys_name",
                           "propValueMask": "*", "sortType": "sys_name"},
                  "force": 1, "flags": tous, "from": 0, "to": 50}
        url = (f"{API_URL}?svc=core/search_items&params="
               f"{urllib.parse.quote(json.dumps(params))}&sid={api._sid}")
        with urllib.request.urlopen(url, timeout=60) as r:
            data = json.load(r)
        for it in (data.get("items") or []):
            for z in (it.get("zl") or {}).values():
                b = z.get("b") or {}
                lat, lng = b.get("cen_y"), b.get("cen_x")
                if lat is None or lng is None:
                    continue
                rayon = _rayon_depuis_emprise(
                    b.get("min_y", lat), b.get("max_y", lat),
                    b.get("min_x", lng), b.get("max_x", lng),
                    facteur=_FACTOR_CTPRO)
                zones.append((float(lat), float(lng), rayon,
                              (z.get("n") or "?")[:80], "CAMTRACKPRO"))
    finally:
        api.fermer()
    return zones


def charger_zones(force: bool = False) -> int:
    """(Re)charge le cache si périmé (> 6 h) ou forcé. Retourne le total.

    Aucune exception ne remonte (§10) : un portail en panne n'empêche pas
    l'autre ; l'ancien cache est conservé en cas d'échec complet."""
    global _zones, _charge_ts, _indisponible_note
    with _lock:
        if not force and _zones and (time.time() - _charge_ts) < _PERIODE_CACHE_S:
            return len(_zones)
    nouvelles: list[tuple] = []
    erreurs = 0
    for chargeur, nom in ((_charger_mzonex, "MZoneX"),
                          (_charger_camtrackpro, "CamtrackPro")):
        try:
            lot = chargeur()
            nouvelles.extend(lot)
            log.info("Géozones %s : %d zone(s) chargée(s)", nom, len(lot))
        except Exception:
            erreurs += 1
            log.exception("Géozones %s : chargement en échec — cache conservé",
                          nom)
    with _lock:
        if nouvelles:
            _zones = nouvelles
            _charge_ts = time.time()
            _indisponible_note = False
        elif not _zones and not _indisponible_note:
            _indisponible_note = True
            log.warning("Géozones : AUCUNE zone chargée (%d échec(s)) — tout "
                        "point est considéré HORS zone jusqu'au prochain "
                        "succès (choix inscrit §0septies B4)", erreurs)
        total = len(_zones)
    log.info("Géozones pour l'alerte vitesse : %d zone(s) active(s) "
             "(période cache %d h)", total, _PERIODE_CACHE_S // 3600)
    return total


def en_geozone(lat: float, lng: float) -> bool:
    """True si le point est DANS au moins une géozone connue (union des deux
    portails). Cache vide → False (hors zone — choix inscrit, loi B4)."""
    with _lock:
        zones = list(_zones)
    for zlat, zlng, rayon, _nom, _portail in zones:
        # borne rapide en degrés avant le calcul exact (~111 km par degré)
        if abs(lat - zlat) > 0.06 or abs(lng - zlng) > 0.06:
            continue
        if _haversine_m(lat, lng, zlat, zlng) <= rayon:
            return True
    return False


def etat_cache() -> dict:
    """Supervision (diagnostic §10 / tests) : taille et âge du cache."""
    with _lock:
        return {"zones": len(_zones),
                "age_s": (None if not _charge_ts
                          else int(time.time() - _charge_ts))}


def libelle_position(lat: float, lng: float) -> str | None:
    """§0undecies E4 (24/08/2026), AMENDÉE §0unvicies decies O3 (01/09/2026,
    extension #1) — « le nom de lieu affiché sur carte à proximité », choisi
    UNIQUEMENT parmi l'onglet « Lieux » de MZoneX et l'onglet « Zones » de
    CamtrackPro :

    1. point DANS un lieu PRÉCIS → son nom (le plus spécifique = plus petit
       rayon, plafonné à 5 km : pas de nom de région géante) ;
    2. sinon lieu PRÉCIS le plus proche mesuré À SON CENTRE à moins de 3 km →
       « proche <nom> » (un dépôt précis à 1 km gagne face à une grande zone
       de ville — le bord-à-bord, qui les favorisait, est abandonné) ;
    3. sinon point DANS une zone GÉNÉRIQUE du référentiel automatique
       (« …-CC », « …-TOWN ») → son nom ;
    4. sinon zone GÉNÉRIQUE dont le centre est à moins de 5 km →
       « proche <nom> » ;
    5. sinon None (l'appelant écrit alors les coordonnées — jamais de
       cellule vide).

    Cache vide (portails jamais joints) → None (choix §10 : pas de nom
    inventé). N'affecte QUE le libellé — jamais le comportement B4.
    """
    if lat is None or lng is None:
        return None
    with _lock:
        zones = list(_zones)
    dans_p: tuple[float, str] | None = None    # (rayon, nom) précis
    dans_g: tuple[float, str] | None = None    # (rayon, nom) générique
    proche_p: tuple[float, str] | None = None  # (distance centre, nom) précis
    proche_g: tuple[float, str] | None = None  # (distance centre, nom) générique
    for zlat, zlng, rayon, nom, _portail in zones:
        if rayon > _RAYON_NOMMAGE_MAX_M:
            continue                      # région géante : jamais un libellé
        generique = _est_generique(nom)
        # borne rapide en degrés (~10 km) avant le calcul exact
        if abs(lat - zlat) > 0.09 or abs(lng - zlng) > 0.09:
            continue
        d = _haversine_m(lat, lng, zlat, zlng)
        if d <= rayon:
            if generique:
                if dans_g is None or rayon < dans_g[0]:
                    dans_g = (rayon, nom)
            elif dans_p is None or rayon < dans_p[0]:
                dans_p = (rayon, nom)
        elif not generique and d < _PROXIMITE_PRECISE_M:
            if proche_p is None or d < proche_p[0]:
                proche_p = (d, nom)
        elif generique and d < _PROXIMITE_NOMMAGE_M:
            if proche_g is None or d < proche_g[0]:
                proche_g = (d, nom)
    if dans_p is not None:
        return dans_p[1]
    if proche_p is not None:
        return f"proche {proche_p[1]}"
    if dans_g is not None:
        return dans_g[1]
    if proche_g is not None:
        return f"proche {proche_g[1]}"
    return None


# =============================================================================
# RECONNAISSANCE DES GEOFENCES LOGISTIQUES OFFICIELLES
# Chargement : GRT (GALANA RAFINERIE TERMINALE) unique.
# Déchargement : 7 dépôts officiels stricts (DSNR, DABI, DMMG, DFIA, DMDV, DMKR, DABE).
# Tout autre lieu non listé n'est pas un dépôt officiel et ne génère AUCUNE alerte.
# =============================================================================
DEPOT_OFFICIEL_CHARGEMENT = {
    "code": "GRT",
    "nom": "GRT (GALANA RAFINERIE TERMINALE)",
}

DEPOTS_OFFICIELS_DECHARGEMENT = {
    "DSNR": "Depot Soanierana (DSNR)",
    "DABI": "Depot Alarobia (DABI)",
    "DMMG": "Depot Moramanga (DMMG)",
    "DFIA": "Depot Fianarantsoa (DFIA)",
    "DMDV": "Depot Morondava (DMDV)",
    "DMKR": "Depot Manakara (DMKR)",
    "DABE": "Depot Antsirabe (DABE)",
}

DEPOTS_DECHARGEMENT_CODES = {"DSNR", "DABI", "DMMG", "DFIA", "DMDV", "DMKR", "DABE"}
DEPOTS_SUD_CODES = {"DABE", "DFIA", "DMDV", "DMKR"}

ZONES_CANONIQUES = {
    "BASETNR": {
        "type": "BASETNR",
        "code": "BASETNR",
        "nom": "Base Tana (BASETNR)",
        "mots_cles": ["jovenna by pass", "by pass", "alamabrah", "alasora",
                      "ambohimangakely-iavoloha-cc", "iavoloha", "ambohimangakely",
                      "base lss", "base tana", "base antananarivo"],
        "coords": [(-18.9537, 47.5449, 2500)],
        "est_depot_sud": False,
    },
    "GRT": {
        "type": "GRT",
        "code": "GRT",
        "nom": "GRT (GALANA RAFINERIE TERMINALE)",
        "mots_cles": ["galana rafinerie terminale", "galana rafinérie terminale",
                      "galana terminal", "grt", "galana rafinerie", "galana toamasina"],
        "coords": [(-18.1492, 49.4023, 3500)],
        "est_depot_sud": False,
    },
    "DSNR": {
        "type": "DEPOT_RECEPTEUR",
        "code": "DSNR",
        "nom": "Depot Soanierana (DSNR)",
        "mots_cles": ["depot soanierana", "dépôt soanierana", "soanierana", "dsnr"],
        "coords": [(-18.9300, 47.5200, 3000)],
        "est_depot_sud": False,
    },
    "DABI": {
        "type": "DEPOT_RECEPTEUR",
        "code": "DABI",
        "nom": "Depot Alarobia (DABI)",
        "mots_cles": ["depot alarobia", "dépôt alarobia", "alarobia", "dabi", "ambohibao"],
        "coords": [(-18.8100, 47.4450, 3000)],
        "est_depot_sud": False,
    },
    "DMMG": {
        "type": "DEPOT_RECEPTEUR",
        "code": "DMMG",
        "nom": "Depot Moramanga (DMMG)",
        "mots_cles": ["depot moramanga", "dépôt moramanga", "moramanga", "dmmg"],
        "coords": [(-18.9489, 48.2257, 1500)],
        "est_depot_sud": False,
    },
    "DFIA": {
        "type": "DEPOT_RECEPTEUR",
        "code": "DFIA",
        "nom": "Depot Fianarantsoa (DFIA)",
        "mots_cles": ["depot fianarantsoa", "dépôt fianarantsoa", "fianarantsoa", "dfia"],
        "coords": [(-21.4536, 47.0857, 4000)],
        "est_depot_sud": True,
    },
    "DMDV": {
        "type": "DEPOT_RECEPTEUR",
        "code": "DMDV",
        "nom": "Depot Morondava (DMDV)",
        "mots_cles": ["depot morondava", "dépôt morondava", "morondava", "dmdv"],
        "coords": [(-20.2833, 44.2833, 5000)],
        "est_depot_sud": True,
    },
    "DMKR": {
        "type": "DEPOT_RECEPTEUR",
        "code": "DMKR",
        "nom": "Depot Manakara (DMKR)",
        "mots_cles": ["depot manakara", "dépôt manakara", "manakara", "dmkr"],
        "coords": [(-22.1486, 48.0106, 4000)],
        "est_depot_sud": True,
    },
    "DABE": {
        "type": "DEPOT_RECEPTEUR",
        "code": "DABE",
        "nom": "Depot Antsirabe (DABE)",
        "mots_cles": ["depot antsirabe", "dépôt antsirabe", "antsirabe", "dabe"],
        "coords": [(-19.8659, 47.0333, 4000)],
        "est_depot_sud": True,
    },
}


def normaliser_code_depot(texte: str | None) -> str | None:
    """Normalise un nom de dépôt (ou code) vers son code canonique officiel strict.
    Ne reconnaît STRICTEMENT QUE :
      - GRT (chargement)
      - DSNR, DABI, DMMG, DFIA, DMDV, DMKR, DABE (déchargement)
    Retourne None pour tout autre lieu non officiel.
    """
    if not texte:
        return None
    t = texte.strip().lower()
    if "soanierana" in t or "dsnr" in t:
        return "DSNR"
    if "alarobia" in t or "dabi" in t or "ambohibao" in t:
        return "DABI"
    if "moramanga" in t or "dmmg" in t:
        return "DMMG"
    if "fianarantsoa" in t or "dfia" in t:
        return "DFIA"
    if "morondava" in t or "dmdv" in t:
        return "DMDV"
    if "manakara" in t or "dmkr" in t:
        return "DMKR"
    if "antsirabe" in t or "dabe" in t:
        return "DABE"
    if "grt" in t or "galana" in t:
        return "GRT"
    
    code_maj = texte.strip().upper()
    if code_maj in DEPOTS_DECHARGEMENT_CODES or code_maj == "GRT":
        return code_maj
    return None


def nom_officiel_depot(code_ou_nom: str | None) -> str | None:
    """Retourne le libellé officiel strict (ex. 'Depot Soanierana (DSNR)')."""
    code = normaliser_code_depot(code_ou_nom)
    if not code:
        return None
    if code == "GRT":
        return "GRT (GALANA RAFINERIE TERMINALE)"
    return DEPOTS_OFFICIELS_DECHARGEMENT.get(code)


def detecter_zone_logistique(lat: float | None, lng: float | None, adresse: str | None = None) -> dict:
    """Classifie le lieu courant selon les zones logistiques officielles strictes.
    La télématique GPS (lat/lng) constitue la vérité terrain prioritaire.
    Seuls GRT (chargement) et les 7 dépôts officiels de déchargement sont classifiés comme dépôts.
    Aucun lieu non officiel ne sera classé comme dépôt récepteur ni ne générera d'alerte.
    """
    # 1. Vérification par coordonnées GPS (vérité terrain prioritaire)
    if lat is not None and lng is not None:
        for code, z in ZONES_CANONIQUES.items():
            for (clat, clng, rayon_m) in z["coords"]:
                if _haversine_m(lat, lng, clat, clng) <= rayon_m:
                    return {
                        "type": z["type"],
                        "code": z["code"],
                        "nom": z["nom"],
                        "est_depot_sud": z["est_depot_sud"],
                    }

    # 2. Vérification par mots-clés stricts dans le libellé d'adresse si coordonnées absentes
    adr_l = (adresse or "").lower()
    if (lat is None or lng is None) and adr_l:
        for code, z in ZONES_CANONIQUES.items():
            for mot in z["mots_cles"]:
                if mot in adr_l:
                    return {
                        "type": z["type"],
                        "code": z["code"],
                        "nom": z["nom"],
                        "est_depot_sud": z["est_depot_sud"],
                    }

    # 3. Vérification axe routier
    if "rn2" in adr_l:
        return {"type": "AXE_ROUTIER", "code": "RN2", "nom": "Axe RN2 (Est)", "est_depot_sud": False}
    if "rn7" in adr_l:
        return {"type": "AXE_ROUTIER", "code": "RN7", "nom": "Axe RN7 (Sud)", "est_depot_sud": True}

    return {"type": "AUTRE", "code": "AUTRE", "nom": adresse or "Hors zone", "est_depot_sud": False}

