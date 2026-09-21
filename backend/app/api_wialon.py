"""§0sexies A4 (arbitrage LSS du 20/08/2026) — Client API Wialon (CamtrackPro).

Le portail « CamtrackPro » est un **Wialon (Gurtam) blanchi** (constat public
20/08/2026 : `wialon_sdk_url` dans la page de pré-connexion). Son API Remote
publique vit à ``https://hst-api.wialon.com/wialon/ajax.html``. Authentification
par **jeton** (créé par l'exploitant dans le formulaire oAuth du portail —
validité illimitée, droits complets) : `svc=token/login` → `sid` de session ;
toute erreur 1 (session expirée ≈ 5 min d'inactivité) re-login transparent.

Données validées en direct le 20/08/2026 (compte LSS) :
- ``core/search_items`` (avl_unit, flag 1025) → **19 unités** à jour par le
  dernier message (position + vitesse) — Niveau 1 CAMTRACKPRO enfin possible
  (borne historique §5 levée par l'arbitrage A4 : flux temps réel fiable) ;
- ``report/exec_report`` + ``get_result_rows`` sur le gabarit **« Detail
  Trajet Vehicule »** (type avl_unit, table « Detail Trajet », 12 colonnes)
  → trajets officiels identiques au rapport lu à l'écran — Niveau 2 ;
- les libellés unités (« 6546 TCE-MERCEDES-LPSA(LSS) ») → recensement D4
  infaillible (supersède Statuts+combo v1.23/24).

Contrats respectés : MÊME ``ingest_event`` (§7.1, seuils §0ter) pour les
points, MÊME ``normaliser_valides``/réconciliation pour les trajets, MÊME
``recenser_flotte`` pour D4 — aucune règle métier (§1-§12) ne change.

**Rectificatif §0octies C1 (20/08/2026, v1.28)** — l'hypothèse initiale
« heures du rapport déjà locales » était fausse : le serveur rend les textes
horaires en UTC (mesuré à la seconde : cellule « 2026-08-20 02:24:44 » = epoch
1787192684 = 05:24:44 affiché à l'écran). ``_parse_instant`` lit désormais
l'epoch « v » des cellules dict en priorité et relit le texte comme UTC en
repli ; le correctif de données +3h (unique, audité) vit dans
``engine.corriger_fuseau_camtrackpro``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
import time
import httpx

from .config import TZ, plaque_depuis_libelle_portail

log = logging.getLogger("lss.api_wialon")

API_URL = os.getenv("WIALON_API_URL",
                    "https://hst-api.wialon.com/wialon/ajax.html")
_TIMEOUT = float(os.getenv("CAMTRACKPRO_API_TIMEOUT_S", "30.0"))
# Correctif PROPOSÉ v1.46 — opt-in STRICT (l'arbitrage O4 du 01/09/2026 « laisser
# tel quel » reste le comportement PAR DÉFAUT) : WIALON_SESSION_PARTAGEE=1 fait
# réutiliser UNE session Wialon unique par le processus (plus d'invalidation de
# session à chaque cycle — cause mesurée de la dégradation CamtrackPro du
# 04/09/2026 : 2-15 pts/h au lieu de 150-220). Le portail web reste utilisable.
SESSION_PARTAGEE = os.getenv("WIALON_SESSION_PARTAGEE", "1") == "1"
_sid_partage: dict = {"sid": None}
_SID_VERROU = threading.Lock()
"""Verrou GLOBAL protégeant `_sid_partage` : l'ancien verrou par instance ne
couvrait pas l'état partagé — deux threads pouvaient lire le même sid périmé,
se re-logger chacun et s'invalider mutuellement (la « dégradation CamtrackPro »
que SESSION_PARTAGEE était censée supprimer)."""
NOM_RAPPORT = os.getenv("CAMTRACKPRO_API_RAPPORT_NOM", "Detail Trajet Vehicule")
RESSOURCE_ID = os.getenv("CAMTRACKPRO_API_RESSOURCE_ID", "").strip()
GABARIT_ID = os.getenv("CAMTRACKPRO_API_GABARIT_ID", "").strip()
# colonnes du tableau « Detail Trajet » (détection dynamique avec repli par défaut) :
# 0 Début · 1 Emplacement initial · 2 Fin · 3 Emplacement final ·
# 4 Heures moteur · 5 Ralenti moteur · 6 En mouvement · 7 Distance parcourue ·
# 8 Vitesse moyenne · 9 Vitesse max · 10 Durée depuis trajet · 11 Conducteur
COL_DEBUT, COL_FIN, COL_DISTANCE, COL_CONDUCTEUR = 0, 2, 7, 11
COL_RALENTI, COL_VMAX = 5, 9


# v148 — un en-tête d'EMPLACEMENT porte une ADRESSE, jamais un instant : « Emplacement
# final » contient « fin » et écrasait la vraie colonne « Date et heure fin » dans la
# détection par simple sous-chaîne (cause racine mesurée le 16/09/2026 : `fin = None`
# sur TOUTES les lignes CamtrackPro → plus aucune fin officielle, trajets jamais
# refermés, TCJ/TTJ/TCH faux et repli sur des tables codées en dur).
_EN_TETES_SANS_INSTANT = ("emplacement", "lieu", "adresse", "position",
                          "location", "address")


def _en_tete_normalise(libelle) -> str:
    """Libellé d'en-tête Wialon normalisé (minuscules, sans accents, espaces simples)."""
    import unicodedata
    txt = unicodedata.normalize(
        "NFKD", str(libelle or "").strip().lower())
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return " ".join(txt.split())


def detecter_colonnes_wialon(headers: list[str] | None = None) -> dict[str, int]:
    """Détecte les index des colonnes à partir des libellés du gabarit Wialon.
    Supporte les gabarits 9 colonnes (standard), 10, 11 ou 12 colonnes avec repli robuste.

    Gabarit réel du compte LSS (« Detail Trajet Vehicule », 12 colonnes — relevé
    API du 16/09/2026 sur 6256TCE) :
      0 « Date et heure debut » · 1 « Emplacement initial » · 2 « Date et heure
      fin » · 3 « Emplacement final » · 4 « Durée de trajet » · 5 « Ralenti
      moteur » · 6 « En mouvement » · 7 « Distance parcourue » · 8 « Vitesse
      moyenne » · 9 « Vitesse maximal » · 10 « Durée (Depuis Precedent
      Trajet)) » · 11 « Conducteur ».

    v148 (constat métier du 16/09/2026) — l'ancienne détection laissait le
    DERNIER libellé gagner : « Emplacement finAL » (index 3, cellule d'adresse
    sans epoch) écrasait « Date et heure fin » (index 2, instant) → la lecture
    rendait `fin = None` pour chaque ligne et **toutes les fins de trajets
    CamtrackPro étaient perdues**. La détection retient désormais le libellé le
    plus SPÉCIFIQUE (intitulé exact > préfixe daté > préfixe) et ignore tout
    en-tête d'emplacement.
    """
    mapping = {
        "debut": COL_DEBUT,
        "fin": COL_FIN,
        "distance": COL_DISTANCE,
        "ralenti": COL_RALENTI,
        "v_max": COL_VMAX,
        "conducteur": COL_CONDUCTEUR,
    }
    if not headers:
        return mapping

    meilleurs: dict[str, tuple[int, int]] = {}   # clé → (score, index)

    def _retenir(cle: str, score: int, index: int) -> None:
        if cle not in meilleurs or score > meilleurs[cle][0]:
            meilleurs[cle] = (score, index)

    for i, h in enumerate(headers):
        hl = _en_tete_normalise(h)
        if not hl:
            continue
        if not any(m in hl for m in _EN_TETES_SANS_INSTANT):
            if hl in ("debut", "start", "date debut", "heure debut",
                      "date et heure debut"):
                _retenir("debut", 3, i)
            elif hl in ("fin", "end", "date fin", "heure fin",
                        "date et heure fin"):
                _retenir("fin", 3, i)
            elif hl.startswith(("date et heure debut", "date debut",
                                "heure debut", "debut du", "debut de")):
                _retenir("debut", 2, i)
            elif hl.startswith(("date et heure fin", "date fin", "heure fin",
                                "fin du", "fin de")):
                _retenir("fin", 2, i)
            elif hl.startswith("debut") or hl.startswith("start"):
                _retenir("debut", 1, i)
            elif hl.startswith("fin") or hl.startswith("end"):
                _retenir("fin", 1, i)
        if "distance" in hl or "parcour" in hl or "km" in hl:
            _retenir("distance", 2 if ("distance" in hl or "parcour" in hl)
                     else 1, i)
        if "ralenti" in hl or "idle" in hl:
            _retenir("ralenti", 2, i)
        if "max" in hl and ("vitesse" in hl or "speed" in hl):
            _retenir("v_max", 2, i)
        if "conducteur" in hl or "driver" in hl or "chauffeur" in hl:
            _retenir("conducteur", 2, i)

    for cle, (_score, index) in meilleurs.items():
        mapping[cle] = index
    return mapping


class ErreurApiWialon(RuntimeError):
    """Échec de lecture de l'API Wialon (réseau, jeton, 4xx/5xx, schéma)."""


def jeton_configure() -> bool:
    """True si le jeton API Wialon est présent (§0sexies A4) et l'usage actif."""
    return (os.getenv("CAMTRACKPRO_API_ENABLE", "1") == "1"
            and bool(os.getenv("CAMTRACKPRO_TOKEN", "").strip()))


def plaque_unite(nom: str | None) -> str | None:
    """« 6546 TCE-MERCEDES-LPSA(LSS) » → « 6546TCE » (garde D3 §0quater)."""
    if not nom:
        return None
    return plaque_depuis_libelle_portail(str(nom)) or None


def point_depuis_position_wialon(u: dict) -> dict | None:
    """Unité (dernier message) → point du contrat CollectorBase.

    `pos.t` = epoch UTC du DERNIER message du boîtier ; `pos.s` = vitesse km/h.
    Filtre strict bruit GPS : vitesse > 3.0 km/h requise pour le roulage et l'incrémentation TCJ.
    """
    plaque = plaque_unite(u.get("nm"))
    pos = u.get("pos") or {}
    t, y, x = pos.get("t"), pos.get("y"), pos.get("x")
    if plaque is None or not t or y is None or x is None:
        return None
    ts = datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(
        TZ).replace(tzinfo=None)
    vit_brute = max(0.0, float(pos.get("s") or 0.0))
    roule = (vit_brute > 3.0)
    return {"gps_associe": plaque, "horodatage": ts,
            "lat": float(y), "lng": float(x), "adresse": None,
            "vitesse": vit_brute if roule else 0.0,
            "moteur": "ON" if roule else "OFF",
            "type_evenement": "POSITION" if roule else "ARRET"}


def _texte(cellule) -> str:
    """Cellule Wialon : str directe ou dict {"t": texte, ...}."""
    if isinstance(cellule, dict):
        return str(cellule.get("t") or "")
    return "" if cellule is None else str(cellule)


def _parse_instant(cellule) -> datetime | None:
    """Instant d'une cellule « Début »/« Fin » du rapport → heure LOCALE naïve.

    Rectificatif §0octies C1 (constat mesuré le 20/08/2026, arbitrage LSS) :
    le serveur rend les textes « YYYY-MM-DD HH:MM:SS » en **UTC**, pas en
    heure locale (preuve : « 2026-08-20 02:24:44 » lu ici = 05:24:44 affiché
    à l'écran du portail — décalage exact +3h00, vérifié à la seconde).
    La cellule dict porte « v » = epoch UTC de l'instant → source de vérité
    prioritaire, insensible au fuseau d'affichage du compte. Repli (cellule
    texte seule) : le texte est relu comme UTC puis converti — c'est le MÊME
    instant rendu par le MÊME serveur (constat du 20/08 : décalage constant)."""
    if isinstance(cellule, dict):
        v = cellule.get("v")
        if v is not None:
            try:
                return (datetime.fromtimestamp(int(v), tz=timezone.utc)
                        .astimezone(TZ).replace(tzinfo=None))
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        cellule = cellule.get("t")
    texte = ("" if cellule is None else str(cellule)).strip()
    if not texte:
        return None
    try:
        brut = datetime.strptime(texte[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return brut.replace(tzinfo=timezone.utc).astimezone(TZ).replace(tzinfo=None)


def _parse_distance(texte: str) -> float | None:
    """« 30.30 km » → 30.30 (borne inférieure 0, §2)."""
    texte = (texte or "").replace("\u00a0", " ").replace(",", ".").strip()
    if not texte:
        return None
    try:
        return max(0.0, float(texte.split()[0]))
    except (ValueError, IndexError):
        return None


def _parse_duree(texte: str) -> int | None:
    """« 0:12:33 » / « 12:33 » (durée affichée du rapport) → secondes."""
    texte = (texte or "").strip()
    if not texte:
        return None
    try:
        parts = [int(float(p.replace(",", "."))) for p in texte.split(":")]
    except ValueError:
        return None
    if len(parts) == 3:
        return max(0, parts[0] * 3600 + parts[1] * 60 + parts[2])
    if len(parts) == 2:
        return max(0, parts[0] * 60 + parts[1])
    if len(parts) == 1:
        return max(0, parts[0])
    return None


def item_depuis_ligne_rapport(nom_unite: str, cellules: list, col_map: dict[str, int] | None = None) -> dict | None:
    """Ligne « Detail Trajet » Wialon → item du contrat réconciliation.

    Supporte dynamiquement les gabarits Wialon à 9, 10, 11 ou 12 colonnes.
    §0septies B3 : la vitesse max et le ralenti moteur de la ligne officielle
    accompagnent le conducteur lorsqu'ils sont disponibles."""
    plaque = plaque_unite(nom_unite)
    if plaque is None or not cellules or len(cellules) < 3:
        return None

    col = col_map or {
        "debut": COL_DEBUT,
        "fin": COL_FIN,
        "distance": COL_DISTANCE,
        "ralenti": COL_RALENTI,
        "v_max": COL_VMAX,
        "conducteur": COL_CONDUCTEUR,
    }
    idx_deb = col.get("debut", COL_DEBUT)
    idx_fin = col.get("fin", COL_FIN)
    idx_dist = col.get("distance", COL_DISTANCE)
    idx_ral = col.get("ralenti", COL_RALENTI)
    idx_vmax = col.get("v_max", COL_VMAX)
    idx_cond = col.get("conducteur", COL_CONDUCTEUR)

    if idx_deb >= len(cellules):
        return None
    debut = _parse_instant(cellules[idx_deb])
    if debut is None:
        return None

    cellule_fin = cellules[idx_fin] if idx_fin < len(cellules) else None
    fin = _parse_instant(cellule_fin)
    if fin is None and _texte(cellule_fin).strip():
        # v148 — la cellule « Fin » porte un texte mais AUCUN instant exploitable :
        # signal d'une détection de colonnes décalée (constat 16/09/2026 :
        # « Emplacement final » — une adresse — pris pour « Date et heure fin »).
        # Le trajet serait alors laissé « en cours » à tort : on trace.
        log.warning("Wialon API : heure de fin ILLISIBLE pour %s — cellule %r "
                    "(colonnes détectées %s) : trajet laissé « en cours »",
                    plaque, cellule_fin, col)
    if fin is not None and fin <= debut:
        # `_parse_instant` convertit déjà UTC→local (§0octies C1) : une fin
        # antérieure ou égale au début est une donnée corrompue du portail —
        # on ignore la ligne (PAS de correction +3h en dur qui gonflait
        # artificiellement des durées ou jetait des trajets légitimes).
        log.warning("Wialon API : trajet ignoré pour %s car fin (%s) <= début (%s)",
                    plaque, fin, debut)
        return None

    dist_txt = _texte(cellules[idx_dist]) if idx_dist < len(cellules) else ""
    distance_km = _parse_distance(dist_txt)

    cond_txt = _texte(cellules[idx_cond]).strip() if idx_cond < len(cellules) else ""
    conducteur = cond_txt or None

    vmax_txt = _texte(cellules[idx_vmax]) if idx_vmax < len(cellules) else ""
    v_max = _parse_distance(vmax_txt)

    ral_txt = _texte(cellules[idx_ral]) if idx_ral < len(cellules) else ""
    ralenti_s = _parse_duree(ral_txt)

    return {
        "plaque": plaque,
        "debut": debut,
        "fin": fin,
        "distance_km": distance_km,
        "conducteur": conducteur,
        "v_max": v_max,
        "ralenti_s": ralenti_s,
        "source": "CAMTRACKPRO",
    }


class ApiWialon:
    """Client Wialon Remote API (session par jeton, re-login transparent)."""

    def __init__(self, jeton: str | None = None):
        self._jeton = jeton or os.getenv("CAMTRACKPRO_TOKEN", "").strip()
        if not self._jeton:
            raise ErreurApiWialon("CAMTRACKPRO_TOKEN absent de backend/.env")
        self._sid: str | None = None
        if SESSION_PARTAGEE:
            with _SID_VERROU:
                self._sid = _sid_partage.get("sid")
        self._ids_rapport: tuple[int, int] | None = None
        self._verrou = threading.Lock()

    def _invalider_sid(self) -> None:
        """Invalide la session COURANTE, sans écraser une session plus récente
        qu'un autre thread viendrait d'ouvrir (lecture/écriture atomiques)."""
        with _SID_VERROU:
            if SESSION_PARTAGEE and _sid_partage.get("sid") == self._sid:
                _sid_partage["sid"] = None
            self._sid = None

    # ---------------------------------------------------------------- bas
    def _appel(self, svc: str, params: dict, reessai: bool = True) -> dict | list:
        url = (f"{API_URL}?svc={svc}&params="
               f"{urllib.parse.quote(json.dumps(params))}"
               + (f"&sid={self._sid}" if self._sid else ""))
        try:
            with httpx.Client(timeout=httpx.Timeout(_TIMEOUT, connect=5.0)) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    raise ErreurApiWialon(f"svc={svc} → HTTP {resp.status_code}")
                data = resp.json()
        except httpx.TimeoutException as e:
            raise TimeoutError(f"svc={svc} timeout après {_TIMEOUT}s : {e}") from e
        except Exception as e:
            raise ErreurApiWialon(f"svc={svc} injoignable : {type(e).__name__} {e}") from e
        if isinstance(data, dict) and data.get("error"):
            code_err = int(data["error"])
            if code_err == 1 and reessai:      # session expirée
                self._invalider_sid()
                self.connecter()
                return self._appel(svc, params, reessai=False)
            if code_err in (1, 4, 7, 8):
                self._invalider_sid()
            raise ErreurApiWialon(f"svc={svc} → erreur Wialon "
                                  f"{data.get('error')} : "
                                  f"{str(data.get('reason'))[:120]}")
        return data

    def connecter(self) -> str:
        # Verrou GLOBAL : le check-then-login atomique empêche deux threads de
        # s'authentifier simultanément et de s'invalider mutuellement.
        with _SID_VERROU:
            with self._verrou:
                try:
                    r = self._appel("token/login", {"token": self._jeton},
                                    reessai=False)
                    if not isinstance(r, dict) or "eid" not in r:
                        self._invalider_sid()
                        raise ErreurApiWialon(f"jeton refusé : {r}")
                    self._sid = r["eid"]
                    if SESSION_PARTAGEE:
                        _sid_partage["sid"] = self._sid
                    log.info("CamtrackPro API : session Wialon ouverte (%s)",
                             r.get("user", {}).get("nm"))
                    return self._sid
                except Exception:
                    self._invalider_sid()
                    raise

    def _exiger_session(self) -> None:
        if not self._sid:
            self.connecter()

    def fermer(self) -> None:
        if self._sid:
            if SESSION_PARTAGEE:
                # session partagée : on NE se déconnecte PAS (les autres
                # utilisateurs du processus continuent de s'en servir) ;
                # le re-login transparent couvre l'expiration naturelle.
                self._sid = None
                return
            try:
                self._appel("core/logout", {}, reessai=False)
            except ErreurApiWialon:
                pass
            self._sid = None

    # ---------------------------------------------------------------- flotte
    def unites(self) -> list[dict]:
        """Unités du compte avec dernier message (flag 1025) — positions vives."""
        self._exiger_session()
        r = self._appel("core/search_items", {
            "spec": {"itemsType": "avl_unit", "propName": "sys_name",
                     "propValueMask": "*", "sortType": "sys_name"},
            "force": 1, "flags": 1025, "from": 0, "to": 0})
        return list(r.get("items", []))

    def recenser_flotte(self) -> list[str]:
        """D4 (§0quinquies) : libellés de TOUTES les unités du compte (UN appel)."""
        us = self.unites()
        libelles = [u.get("nm", "").strip() for u in us if u.get("nm")]
        log.info("CamtrackPro API — recensement D4 : %d unité(s) publiée(s)",
                 len(libelles))
        return libelles

    # ---------------------------------------------------------------- rapports
    def _gabarit_trajets(self) -> tuple[int, int]:
        """(ressource_id, gabarit_id) de « Detail Trajet Vehicule » (env ou
        découverte : 1ʳᵉ ressource dont les gabarits contiennent le nom)."""
        if RESSOURCE_ID and GABARIT_ID:
            return int(RESSOURCE_ID), int(GABARIT_ID)
        if self._ids_rapport:
            return self._ids_rapport
        self._exiger_session()
        r = self._appel("core/search_items", {
            "spec": {"itemsType": "avl_resource", "propName": "reporttemplates",
                     "propValueMask": "*", "sortType": "reporttemplates"},
            "force": 1, "flags": 8193, "from": 0, "to": 0})
        for res in r.get("items", []):
            for tid, tpl in (res.get("rep") or {}).items():
                if (str(tpl.get("n") or "").strip().lower() ==
                        NOM_RAPPORT.strip().lower()):
                    self._ids_rapport = (int(res["id"]), int(tpl["id"]))
                    log.info("CamtrackPro API : gabarit « %s » trouvé "
                             "(ressource %s, id %s)",
                             tpl.get("n"), self._ids_rapport[0],
                             self._ids_rapport[1])
                    return self._ids_rapport
        raise ErreurApiWialon(f"gabarit de rapport « {NOM_RAPPORT} » "
                              "introuvable dans les ressources du compte")

    def trajets_du_jour(self, jour, fin_locale: datetime | None = None) -> list[dict]:
        """Trajets officiels du jour LOCAL donné, unité par unité (idempotent).

        Borne de jour : 00:00 local → `fin_locale` (défaut : maintenant) —
        strictement l'équivalent du rapport lu à l'écran, mêmes valeurs.
        `fin_locale` explicite = relecture d'un jour PASSÉ complet (AM-4)."""
        rid, gid = self._gabarit_trajets()
        debut_local = datetime(jour.year, jour.month, jour.day)
        # bornes epoch (serveur UTC) : 00:00 local → fin_locale / maintenant
        debut_epoch = int(debut_local.replace(tzinfo=TZ).astimezone(
            timezone.utc).timestamp())
        fin_loc = fin_locale or datetime.now(TZ).replace(tzinfo=None)
        fin_loc = min(fin_loc, datetime.now(TZ).replace(tzinfo=None))
        fin_epoch = int(fin_loc.replace(tzinfo=TZ).astimezone(
            timezone.utc).timestamp())
        items: list[dict] = []
        unites_list = self.unites()
        log.info("CamtrackPro API : %d unités détectées pour le rapport trajets (%s, timestamps UTC: %d -> %d)",
                 len(unites_list), jour, debut_epoch, fin_epoch)
        for u in unites_list:
            nom, uid = u.get("nm", ""), u.get("id")
            if not plaque_unite(nom) or uid is None:
                continue
            pos = u.get("pos") or {}
            vitesse_actuelle = float(pos.get("s") or 0.0)
            dernier_ts_epoch = int(pos.get("t") or 0)

            # Allégement véhicules à l'arrêt : pour les camions immobiles (vitesse = 0),
            # si leur dernier signal est antérieur au début du jour ou sans mouvement,
            # on exploite directement la dernière position connue (last_location) sans requêter
            # l'intervalle complet via report/exec_report (évite les TimeoutError 30s).
            if vitesse_actuelle == 0 and dernier_ts_epoch > 0 and dernier_ts_epoch < debut_epoch:
                log.info("CamtrackPro API : véhicule à l'arrêt « %s » (immobile) — dernière position connue retenue", nom)
                continue

            try:
                log.info("CamtrackPro API: Exécution rapport trajets pour « %s » (uid=%s)...", nom, uid)
                r = self._appel("report/exec_report", {
                    "reportResourceId": rid, "reportTemplateId": gid,
                    "reportTemplate": None, "reportObjectId": uid,
                    "reportObjectSecId": 0,
                    "interval": {"from": debut_epoch, "to": fin_epoch,
                                 "flags": 0}})
                tables = (r.get("reportResult") or {}).get("tables", [])
                if not tables:
                    log.info("CamtrackPro API : aucune table dans le résultat du rapport pour « %s »", nom)
                    continue
                headers = tables[0].get("header") or []
                col_map = detecter_colonnes_wialon(headers)
                n_lig = int(tables[0].get("rows") or 0)
                log.info("CamtrackPro API : rapport « %s » -> %d ligne(s) détectée(s)", nom, n_lig)
                if n_lig <= 0:
                    continue
                # Pagination OBLIGATOIRE : `report/get_result_rows` plafonne le
                # nombre de lignes par réponse — un seul appel avec indexTo =
                # n_lig tronquait silencieusement les journées denses.
                lignes: list = []
                pas = 1000
                depuis = 0
                while depuis < n_lig:
                    lot = self._appel("report/get_result_rows", {
                        "tableIndex": 0, "indexFrom": depuis,
                        "indexTo": min(n_lig, depuis + pas)})
                    if not isinstance(lot, list) or not lot:
                        break
                    lignes.extend(lot)
                    depuis += len(lot)
                if not lignes:
                    continue
                bruts = [it for it in (
                    item_depuis_ligne_rapport(nom, lig.get("c") or [], col_map=col_map)
                    for lig in lignes) if it]
                items.extend(bruts)
                if bruts:
                    log.info("CamtrackPro API (rapport trajets) « %s » : "
                             "%d trajet(s)", nom, len(bruts))
            except (TimeoutError, ErreurApiWialon) as exc:
                log.warning("CamtrackPro API : rapport « %s » en erreur ou timeout (%s)", nom, exc)
        log.info("CamtrackPro API (rapport trajets) : %d trajet(s) officiel(s)",
                 len(items))
        return items

    def messages_du_jour(self, jour, fin_locale: datetime | None = None) -> dict[str, list[dict]]:
        """Historique brut des messages GPS (Niveau 1) du jour pour toute la flotte CamtrackPro.
        Utilise svc=messages/load_interval et svc=messages/get_messages.
        Retourne {plaque: [ {plaque, horodatage, lat, lng, vitesse, etat_moteur, type_evenement}, ... ]}."""
        self._exiger_session()
        debut_local = datetime(jour.year, jour.month, jour.day, 0, 0, 0)
        debut_epoch = int(debut_local.replace(tzinfo=TZ).astimezone(timezone.utc).timestamp())
        fin_loc = fin_locale or datetime(jour.year, jour.month, jour.day, 23, 59, 59)
        fin_epoch = int(fin_loc.replace(tzinfo=TZ).astimezone(timezone.utc).timestamp())

        resultats: dict[str, list[dict]] = {}
        unites_list = self.unites()
        log.info("CamtrackPro API : %d unités détectées pour messages_du_jour (%s, timestamps UTC: %d -> %d)",
                 len(unites_list), jour, debut_epoch, fin_epoch)
        for u in unites_list:
            # v1.54 — MÉTRIQUES PAR VÉHICULE + POINT D'ARRÊT CONTRÔLÉ : le coût
            # par unité est mesuré et publié, et la passe s'arrête AVANT l'unité
            # suivante si son budget est atteint (elle n'écrit plus après).
            verifier_etape("vehicule")
            compter("nb_vehicules")
            nom, uid = u.get("nm", ""), u.get("id")
            plaque = plaque_unite(nom)
            if not plaque or uid is None:
                continue

            try:
                log.info("CamtrackPro API: Requête messages/load_interval pour %s (id=%s) [%d -> %d]...",
                         plaque, uid, debut_epoch, fin_epoch)
                r = self._appel("messages/load_interval", {
                    "itemId": int(uid),
                    "timeFrom": debut_epoch,
                    "timeTo": fin_epoch,
                    "flags": 0,
                    "flagsMask": 0xFF00,
                    "loadCount": 0xFFFFFFFF
                })
                total = int((r or {}).get("count", 0) or 0)
                msgs = (r or {}).get("messages") or []
                log.info("CamtrackPro API: Réponse Wialon pour %s -> total=%d, messages_inline=%d",
                         plaque, total, len(msgs))

                # Pagination dès que `count` dépasse les messages reçus
                # INLINE (l'ancien test `if not msgs and total > 0` laissait
                # tomber la FIN de journée quand Wialon renvoyait un lot
                # partiel en ligne). Idempotent : on ne lit que le manquant.
                if total > len(msgs):
                    saute = len(msgs)
                    page_size = 1000
                    while saute < total:
                        lot = self._appel("messages/get_messages", {
                            "indexFrom": saute,
                            "indexTo": min(total, saute + page_size)
                        })
                        page_msgs = lot if isinstance(lot, list) else (lot.get("messages", []) if isinstance(lot, dict) else [])
                        if not page_msgs:
                            break
                        msgs.extend(page_msgs)
                        saute += len(page_msgs)
                        time.sleep(0.05)

                points_unite: list[dict] = []
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    pos = m.get("pos")
                    if not pos or pos.get("y") is None or pos.get("x") is None:
                        continue
                    t_epoch = int(m.get("t", 0))
                    if t_epoch <= 0:
                        continue
                    ts = datetime.fromtimestamp(t_epoch, tz=timezone.utc).astimezone(TZ).replace(tzinfo=None)
                    lat = float(pos["y"])
                    lng = float(pos["x"])
                    vit_brute = max(0.0, float(pos.get("s") or 0.0))
                    p_params = m.get("p") if isinstance(m.get("p"), dict) else {}
                    acc = p_params.get("acc", 1 if vit_brute > 0 else 0)
                    roule = (vit_brute > 3.0)
                    etat_moteur = "ON" if (vit_brute > 0 or acc == 1) else "OFF"

                    points_unite.append({
                        "plaque": plaque,
                        "horodatage": ts,
                        "lat": lat,
                        "lng": lng,
                        "vitesse": vit_brute if roule else 0.0,
                        "etat_moteur": etat_moteur,
                        "type_evenement": "POSITION" if roule else "ARRET"
                    })

                if points_unite:
                    resultats[plaque] = points_unite
                    log.info("CamtrackPro API (messages) « %s » : %d point(s) GPS extraits", plaque, len(points_unite))
                else:
                    log.warning("CamtrackPro API (messages) « %s » : 0 point GPS trouvé sur la plage [%s -> %s]",
                                plaque, debut_local, fin_loc)
            except Exception as exc:
                log.error("CamtrackPro API : échec extraction messages « %s » (id=%s) : %s", nom, uid, exc, exc_info=True)

        return resultats
