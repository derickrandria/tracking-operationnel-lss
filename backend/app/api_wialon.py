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
import urllib.request
from datetime import datetime, timedelta, timezone

from .config import TZ, plaque_depuis_libelle_portail

log = logging.getLogger("lss.api_wialon")

API_URL = os.getenv("WIALON_API_URL",
                    "https://hst-api.wialon.com/wialon/ajax.html")
_TIMEOUT = 60
NOM_RAPPORT = os.getenv("CAMTRACKPRO_API_RAPPORT_NOM", "Detail Trajet Vehicule")
RESSOURCE_ID = os.getenv("CAMTRACKPRO_API_RESSOURCE_ID", "").strip()
GABARIT_ID = os.getenv("CAMTRACKPRO_API_GABARIT_ID", "").strip()
# colonnes du tableau « Detail Trajet » (validées en direct le 20/08/2026) :
# 0 Début · 1 Emplacement initial · 2 Fin · 3 Emplacement final ·
# 4 Heures moteur · 5 Ralenti moteur · 6 En mouvement · 7 Distance parcourue ·
# 8 Vitesse moyenne · 9 Vitesse max · 10 Durée depuis trajet · 11 Conducteur
COL_DEBUT, COL_FIN, COL_DISTANCE, COL_CONDUCTEUR = 0, 2, 7, 11
# §0septies B3 (20/08/2026) — carnet de conduite : ralenti moteur + vitesse max
COL_RALENTI, COL_VMAX = 5, 9


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
    Type None : la machine états §7.1 (v>3 km/h, §0ter) reste souveraine.
    """
    plaque = plaque_unite(u.get("nm"))
    pos = u.get("pos") or {}
    t, y, x = pos.get("t"), pos.get("y"), pos.get("x")
    if plaque is None or not t or y is None or x is None:
        return None
    ts = datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(
        TZ).replace(tzinfo=None)
    return {"gps_associe": plaque, "horodatage": ts,
            "lat": float(y), "lng": float(x), "adresse": None,
            "vitesse": max(0.0, float(pos.get("s") or 0.0)),
            "moteur": "ON", "type_evenement": None}


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


def item_depuis_ligne_rapport(nom_unite: str, cellules: list) -> dict | None:
    """Ligne « Detail Trajet » Wialon → item du contrat réconciliation.

    §0septies B3 : la vitesse max et le ralenti moteur de la ligne officielle
    accompagnent le conducteur (compteurs d'infractions détaillés : gabarits
    5/17, extension ultérieure possible — §0septies B3 in fine)."""
    plaque = plaque_unite(nom_unite)
    if plaque is None or len(cellules) <= COL_CONDUCTEUR:
        return None
    debut = _parse_instant(cellules[COL_DEBUT])
    if debut is None:
        return None
    return {"plaque": plaque, "debut": debut,
            "fin": _parse_instant(cellules[COL_FIN]),
            "distance_km": _parse_distance(_texte(cellules[COL_DISTANCE])),
            "conducteur": (_texte(cellules[COL_CONDUCTEUR]).strip() or None),
            "v_max": _parse_distance(_texte(cellules[COL_VMAX])),
            "ralenti_s": _parse_duree(_texte(cellules[COL_RALENTI])),
            "source": "CAMTRACKPRO"}


class ApiWialon:
    """Client Wialon Remote API (session par jeton, re-login transparent)."""

    def __init__(self, jeton: str | None = None):
        self._jeton = jeton or os.getenv("CAMTRACKPRO_TOKEN", "").strip()
        if not self._jeton:
            raise ErreurApiWialon("CAMTRACKPRO_TOKEN absent de backend/.env")
        self._sid: str | None = None
        self._ids_rapport: tuple[int, int] | None = None
        self._verrou = threading.Lock()

    # ---------------------------------------------------------------- bas
    def _appel(self, svc: str, params: dict, reessai: bool = True) -> dict | list:
        url = (f"{API_URL}?svc={svc}&params="
               f"{urllib.parse.quote(json.dumps(params))}"
               + (f"&sid={self._sid}" if self._sid else ""))
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:
                data = json.load(r)
        except (TimeoutError, OSError) as e:
            raise ErreurApiWialon(f"svc={svc} injoignable : "
                                  f"{type(e).__name__}") from e
        if isinstance(data, dict) and data.get("error"):
            if int(data["error"]) == 1 and reessai:      # session expirée
                self._sid = None
                self.connecter()
                return self._appel(svc, params, reessai=False)
            raise ErreurApiWialon(f"svc={svc} → erreur Wialon "
                                  f"{data.get('error')} : "
                                  f"{str(data.get('reason'))[:120]}")
        return data

    def connecter(self) -> str:
        with self._verrou:
            r = self._appel("token/login", {"token": self._jeton},
                            reessai=False)
            if "eid" not in r:
                raise ErreurApiWialon(f"jeton refusé : {r}")
            self._sid = r["eid"]
            log.info("CamtrackPro API : session Wialon ouverte (%s)",
                     r.get("user", {}).get("nm"))
            return self._sid

    def _exiger_session(self) -> None:
        if not self._sid:
            self.connecter()

    def fermer(self) -> None:
        if self._sid:
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
        for u in self.unites():
            nom, uid = u.get("nm", ""), u.get("id")
            if not plaque_unite(nom) or uid is None:
                continue
            try:
                r = self._appel("report/exec_report", {
                    "reportResourceId": rid, "reportTemplateId": gid,
                    "reportTemplate": None, "reportObjectId": uid,
                    "reportObjectSecId": 0,
                    "interval": {"from": debut_epoch, "to": fin_epoch,
                                 "flags": 0}})
                tables = (r.get("reportResult") or {}).get("tables", [])
                if not tables:
                    continue
                n_lig = int(tables[0].get("rows") or 0)
                if n_lig <= 0:
                    continue
                lignes = self._appel("report/get_result_rows", {
                    "tableIndex": 0, "indexFrom": 0, "indexTo": n_lig})
                if not isinstance(lignes, list):
                    continue
                bruts = [it for it in (
                    item_depuis_ligne_rapport(nom, lig.get("c") or [])
                    for lig in lignes) if it]
                items.extend(bruts)
                if bruts:
                    log.info("CamtrackPro API (rapport trajets) « %s » : "
                             "%d trajet(s)", nom, len(bruts))
            except ErreurApiWialon:
                log.exception("CamtrackPro API : rapport « %s » en échec "
                              "(unité ignorée, les autres continuent)", nom)
        log.info("CamtrackPro API (rapport trajets) : %d trajet(s) officiel(s)",
                 len(items))
        return items
