"""Scrapers MZoneX / CamtrackPro (§10) — stratégie hybride (Addendum v1.4).

Historique : jusqu'à la v1.24 incluse, AUCUNE API n'était connue des deux
plateformes tierces → scraping Playwright uniquement.
v1.25 (§0sexies, arbitrage LSS du 20/08/2026) : l'API OData PUBLIQUE de MZoneX
(`mzone62.api`, découverte OAuth2/OIDC à `login.mzoneweb.net`) devient la source
PRINCIPALE — N1 événements à 60 s, N2 trajets officiels, recensement D4 — et le
scraping ci-dessous (Playwright, Selenium §4) reste le SECOURS AUTOMATIQUE,
cycle par cycle (A2).
v1.26 (§0sexies A4) : l'API Wialon PUBLIQUE (CamtrackPro blanchi,
`hst-api.wialon.com`) rejoint MZoneX — jeton de l'exploitant → positions des 19
unités à 60 s (N1 CamtrackPro enfin possible, borne §5 levée par A4), rapport
« Detail Trajet Vehicule » officiel et recensement D4 en appels directs.

┌─────────────────────────────────────────────────────────────────────────┐
│  STRATÉGIE HYBRIDE (Addendum v1.4 §2) — deux connecteurs par plateforme │
│                                                                         │
│  NIVEAU 1 (temps réel, provisoire)                                      │
│    MZoneX  : onglet Événements, filtre « DEMARRAGE/ARRET (2) »          │
│    → engine.ingest_event() → reconstruction maison → trajets PROVISOIRE │
│                                                                         │
│  NIVEAU 2 (consolidé, validé)                                           │
│    MZoneX      : onglet Trajets (calcul natif, dispo après clôture)     │
│    CamtrackPro : rapport « Detail Trajet groupe de véhicules »          │
│    → reconciliation.reconcilier_trajets_valides() (remplace, jamais     │
│      de doublon, recalcul TCC/TCJ/TTJ, propagation §9, audits §11)      │
└─────────────────────────────────────────────────────────────────────────┘

════════════ CALIBRATION MZoneX — vérifiée sur le portail réel (31/07/2026)
- Connexion SSO Keycloak : #login-input-username / #login-input-password /
  #login-button   (page id.mzoneweb.net)
- Application Angular + grilles **Wijmo FlexGrid** (PAS de balises <table> :
  en-têtes `div[wj-part='ch'] .wj-cell`, lignes `div[wj-part='cells'] .wj-row`)
- Onglets : clic texte robuste (« Véhicules », « Lieux », « Conducteurs »,
  « Trajets », « Evénements » — attention : « Evénements » sans accent)
- Filtres : ComboBox Wijmo — repérage par la VALEUR courante (« Favourite
  vehicles… » → groupe ; « All Event Types… » → type d'événement) ; ouverture
  par le bouton « ˅ » interne (.wj-btn), option dans
  « .wj-dropdown-panel .wj-listbox-item » (« LSS (LPSA) (37) »,
  « DEMARRAGE/ARRET (2) »)
- Pagination du bas (~15 lignes/page « 1 / N ») : flèche droite interne
  (.wj-glyph-right) ; descriptible via MZONEX_SEL_PAGE_SUIVANTE
- Colonnes Événements (réel) : 3=Véhicule, 4=Événement, 5=Lieu, 6=Temps,
  7=Latitude, 8=Longitude, 9=Vitesse  — « 4886 TBU (LSS) », « 31/07/2026
  14:36:36 », lat/lng à virgule (« -18,20793 »)
- Colonnes Trajets (réel) : 4=Véhicule, 5=Durée, 6=Distance, 7=Point de
  départ, 8=Heure de début, 9=Position finale, 10=Heure de fin, 11=Conducteur

════════════ CALIBRATION CamtrackPro — vérifiée sur le portail réel (31/07/2026)
- Connexion simple : #user / #passw / #submit (https://hosting.camtrack.net)
- Rapport « Detail Trajet groupe de véhicules » : combos wui
  #report_templates_filter_reports / #report_templates_filter_units
  (VRAI clic souris requis sur les options li.itm), Exécuter =
  #report_templates_filter_params_execute, période « aujourd'hui » pré-remplie
- Tableau résultat sans ID : remontée depuis la cellule « Date - Heure
  Début » ; 13 colonnes utiles (détail au-dessus de SessionCamtrackPro)
- §5 : pas de temps réel fiable → trajets CamtrackPro = VALIDÉ direct
  (exécution véhicule par véhicule, ~3-4 min le cycle complet de 13 camions)
- Mode MIXTE (COLLECTOR_SOURCE=MIXTE) : Niveau 1 = MZoneX temps réel ;
  Niveau 2 = MZoneX (onglet Trajets) + CamtrackPro (rapport) dans le même
  cycle de synchronisation.
═══════════════════════════════════════════════════════════════════════════
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .config import (normaliser_ident, now_local,
                     plaque_depuis_libelle_portail)
from .database import SessionLocal
from .engine import (creer_conducteur_auto, creer_vehicule_auto,
                     ingest_event, verifier_alertes_conduite)
from .geozones import charger_zones, en_geozone
from .models import EvenementGPS, SourceEvenement, Vehicule
from .api_mzonex import (ApiMZoneX, point_depuis_evenement_api,
                         trajet_depuis_api)
from .api_wialon import (ApiWialon, jeton_configure,
                         point_depuis_position_wialon)

log = logging.getLogger("lss.scraper")

COLLECTE_LOCK = threading.Lock()
_ETAT_COLLECTE_LOCK = threading.Lock()
_ETAT_COLLECTE = {
    "orchestrateur": "ASYNCIO",
    "pid": os.getpid(),
    "dernier_cycle_debut": None,
    "dernier_cycle_fin": None,
    "derniere_erreur": None,
    "sources": {},
}


def _etat_collecte_debut(source: str):
    with _ETAT_COLLECTE_LOCK:
        instant = now_local().isoformat()
        _ETAT_COLLECTE["dernier_cycle_debut"] = instant
        _ETAT_COLLECTE["sources"].setdefault(source, {})["dernier_debut"] = instant


def _etat_collecte_fin(source: str, nombre: int):
    with _ETAT_COLLECTE_LOCK:
        instant = now_local().isoformat()
        _ETAT_COLLECTE["dernier_cycle_fin"] = instant
        _ETAT_COLLECTE["sources"].setdefault(source, {}).update(
            {"derniere_reussite": instant, "dernier_nombre": nombre,
             "derniere_erreur": None})


def _etat_collecte_erreur(source: str, exc: Exception):
    with _ETAT_COLLECTE_LOCK:
        erreur = f"{type(exc).__name__}: {exc}"
        _ETAT_COLLECTE["derniere_erreur"] = erreur
        _ETAT_COLLECTE["sources"].setdefault(source, {})["derniere_erreur"] = erreur


def etat_collecte_memoire() -> dict:
    with _ETAT_COLLECTE_LOCK:
        return {
            **_ETAT_COLLECTE,
            "sources": {k: dict(v) for k, v in _ETAT_COLLECTE["sources"].items()},
            "verrou_occupe": COLLECTE_LOCK.locked(),
        }


def _collecte_protegee(source: str, action) -> int:
    """Exécute une passe de source sans chevauchement dans le processus."""
    if not COLLECTE_LOCK.acquire(blocking=False):
        log.warning("Collecte %s ignorée : une autre passe est en cours", source)
        return 0
    _etat_collecte_debut(source)
    try:
        nombre = int(action() or 0)
        _etat_collecte_fin(source, nombre)
        return nombre
    except Exception as exc:
        _etat_collecte_erreur(source, exc)
        log.exception("Échec collecte protégée %s", source)
        return 0
    finally:
        COLLECTE_LOCK.release()


def _mzonex_api_active() -> bool:
    """§0sexies A2 (arbitrage LSS 20/08/2026) : API MZoneX en PRINCIPAL.

    Coupable simplement via MZONEX_API_ENABLE=0 (diagnostic §10) ; quand elle
    est active, TOUT ÉCHEC API replie automatiquement, pour ce cycle-là, sur
    le lecteur d'écran Playwright historique (A2 : « écran en secours »)."""
    return os.getenv("MZONEX_API_ENABLE", "1") == "1"


def _env_int(nom: str, defaut: int) -> int:
    try:
        return int(os.getenv(nom, str(defaut)))
    except ValueError:
        return defaut


def _env(nom: str, defaut: str = "") -> str:
    return os.getenv(nom, defaut).strip()


# ------------------------------------------------------------------ parsing commun
FORMATS_HEURE = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                 "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                 "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")


def parse_duree_hms(texte: str) -> int | None:
    """« 0:23:11 » / « 6:27:04 » → secondes (None si illisible)."""
    if not texte:
        return None
    morceaux = str(texte).strip().split(":")
    if len(morceaux) != 3:
        return None
    try:
        h, m, s = (int(x) for x in morceaux)
        return h * 3600 + m * 60 + s
    except ValueError:
        return None


def parse_dt(texte: str) -> datetime | None:
    """« 31/07/2026 15:14:08 » (format MZoneX) ou ISO → datetime, sinon None."""
    texte = (texte or "").strip()
    for fmt in FORMATS_HEURE:
        try:
            return datetime.strptime(texte, fmt)
        except ValueError:
            continue
    return None


def parse_float(texte: str) -> float | None:
    """« 16.24 km » / « 1.99 Km » / « 32 km/h » / « -18,20793 » → float
    (None si illisible). Unités retirées sans tenir compte de la casse ;
    virgule décimale et espaces (insécables) gérés."""
    if texte is None:
        return None
    t = str(texte).strip().lower()
    for unite in ("km/h", "kmh", "km"):
        t = t.replace(unite, "")
    t = t.replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def ident_vehicule(texte: str) -> str:
    """« 0576 TCD (LSS) » / « 0826 TBS-MERCEDES-LPSA(…) » / « 4926 TBU-(LSS) »
    → « 0576TCD » / « 0826TBS » / « 4926TBU » (rapprochement par plaque ou
    gps_associe, §10). v1.21 : délégué à config.normaliser_ident (§0quater D0)."""
    return normaliser_ident(texte)


# =============================================================================
# MZoneX — session Playwright partagée (SSO Keycloak + onglets + grilles Wijmo)
# =============================================================================
_JS_CLICK_ONGLET = """(cible) => {
  const norm = s => (s || '').normalize('NFC').replace(/\\s+/g, ' ').trim().toLowerCase();
  const els = Array.from(document.querySelectorAll('span, a, button, div, li'))
    .filter(e => e.offsetWidth > 0 && e.offsetHeight > 0);
  let best = null;
  for (const e of els) {
    const t = norm(e.textContent);
    if (t === cible) { best = e; break; }
    if (t.includes(cible) && (!best || t.length < norm(best.textContent).length)) best = e;
  }
  if (!best) return null;
  (best.closest('a,button,[role=button],[role=tab],li') || best).click();
  return norm(best.textContent).slice(0, 40);
}"""


class SessionMZoneX:
    """Une session de navigation MZoneX : connexion SSO, navigation onglets,
    filtres ComboBox, lecture d'une grille Wijmo paginée (viewport virtualisé).
    Tout est surchargeable par variables d'environnement MZONEX_*."""

    def __init__(self, pw):
        self.pw = pw
        self.page = None
        self.navig = None

    # ---------------- cycle de vie ----------------
    def __enter__(self):
        self.navig = self.pw.chromium.launch(headless=True, args=["--no-sandbox"])
        self.page = self.navig.new_page(viewport={"width": 1750, "height": 980})
        return self

    def __exit__(self, *exc):
        try:
            self.navig.close()
        except Exception:
            pass

    def connecter(self):
        """SSO Keycloak — sélecteurs vérifiés le 31/07/2026 (env-surchargeables)."""
        p = self.page
        p.goto(os.environ["MZONEX_URL"], timeout=60_000)
        p.wait_for_selector(_env("MZONEX_SEL_USER", "#login-input-username"),
                            timeout=45_000)
        p.fill(_env("MZONEX_SEL_USER", "#login-input-username"),
               os.environ["MZONEX_USER"])
        p.fill(_env("MZONEX_SEL_PASS", "#login-input-password"),
               os.environ["MZONEX_PASSWORD"])
        p.click(_env("MZONEX_SEL_SUBMIT", "#login-button"))
        p.wait_for_url("**/mzonex/**", timeout=45_000)
        p.wait_for_timeout(7000)   # chargement complet de l'application Angular
        url = _env("MZONEX_URL_WORKSPACE",
                   "https://live.mzoneweb.net/mzonex/workspace/(map//grid:vehicles)")
        p.goto(url, timeout=45_000)
        p.wait_for_timeout(4000)

    # ---------------- navigation ----------------
    def ouvrir_onglet(self, cible: str) -> bool:
        """Clique un onglet de la barre (« Evénements », « Trajets »…).
        Clic JS tolérant (accents, nœud exact) — vérif. portail réel."""
        clique = self.page.evaluate(_JS_CLICK_ONGLET, cible.lower())
        self.page.wait_for_timeout(5000)
        return bool(clique)

    def choisir_combo(self, fragment_valeur: str, texte_option: str,
                      pause_ms: int = 1800) -> bool:
        """ComboBox Wijmo repérée par sa VALEUR courante (« favourite »,
        « all event types ») ; ouvre « ˅ » (.wj-btn) puis clique l'option
        contenant `texte_option` dans .wj-dropdown-panel .wj-listbox-item."""
        combo = None
        for el in self.page.query_selector_all(".wj-combobox"):
            try:
                inp = el.query_selector("input")
                if el.is_visible() and inp and fragment_valeur.lower() in \
                        (inp.input_value() or "").lower():
                    combo = el
                    break
            except Exception:
                continue
        if combo is None:
            log.warning("ComboBox MZoneX introuvable (valeur ~%r)", fragment_valeur)
            return False
        btn = combo.query_selector(".wj-btn") or combo
        btn.click()
        self.page.wait_for_timeout(pause_ms)
        for o in self.page.query_selector_all(
                ".wj-dropdown-panel .wj-listbox-item, .wj-listbox-item"):
            try:
                if o.is_visible() and texte_option.lower() in (o.inner_text() or "").lower():
                    libelle = (o.inner_text() or "").strip()
                    o.click()
                    self.page.wait_for_timeout(900)
                    log.info("Filtre MZoneX : « %s » sélectionné", libelle)
                    return True
            except Exception:
                continue
        log.warning("Option ComboBox introuvable : %r (combo ~%r)",
                    texte_option, fragment_valeur)
        return False

    # ---------------- filtre véhicule (onglet Trajets, v1.10) ----------------
    def _combo_vehicules(self):
        """Combo Wijmo « Rechercher véhicules » (repérée par son placeholder)."""
        for el in self.page.query_selector_all(".wj-combobox"):
            try:
                inp = el.query_selector("input")
                if el.is_visible() and inp and "véhicules" in \
                        (inp.get_attribute("placeholder") or ""):
                    return el
            except Exception:
                continue
        return None

    def filtrer_vehicule(self, plaque: str) -> bool:
        """Combo « Rechercher véhicules » : taper les chiffres de la plaque
        (le libellé contient un espace, ex. « 0916 TBV (LSS) (0916 TBV, …) »)
        puis cliquer l'option correspondante. v1.22 : correspondance d'abord
        EXACTE sur la plaque normalisée du libellé (sous-chaîne en repli) —
        éviterait d'accrocher « 8206 » pour « 206 » si la famille existe."""
        import re as _re
        cible = self._combo_vehicules()
        if cible is None:
            log.warning("MZoneX Trajets : combo « Rechercher véhicules » introuvable")
            return False
        champ = cible.query_selector("input")
        champ.click()
        self.page.wait_for_timeout(400)
        champ.fill("")
        chiffres = _re.match(r"\d+", plaque.replace(" ", ""))
        champ.type(chiffres.group(0) if chiffres else plaque, delay=50)
        self.page.wait_for_timeout(2500)
        norme = plaque.replace(" ", "").upper()
        repli = None
        for o in self.page.query_selector_all(
                ".wj-dropdown-panel .wj-listbox-item, .wj-listbox-item"):
            try:
                if not o.is_visible():
                    continue
                t = (o.inner_text() or "").strip()
                if plaque_depuis_libelle_portail(t) == norme:
                    o.click()                    # 1 · plaque exacte
                    self.page.wait_for_timeout(3500)
                    return True
                if repli is None and norme in t.replace(" ", "").upper():
                    repli = o                    # 2 · repli « contient »
            except Exception:
                continue
        if repli is not None:
            try:
                repli.click()
                self.page.wait_for_timeout(3500)
                return True
            except Exception:
                pass
        log.warning("MZoneX Trajets : aucune option véhicule pour %r "
                    "(aussi marqué « absent » au recensement D4 si le camion "
                    "n'est pas publié par le portail)", plaque)
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(500)
        return False

    # ---------------- recensement du groupe (§0quinquies D4) ----------------
    def recenser_vehicules(self) -> list:
        """§0quinquies D4 (14/08/2026, arbitrage métier) — liste COMPLÈTE des
        véhicules que MZoneX PUBLIE pour le groupe courant. Le combo Wijmo est
        VIRTUALISÉ (≈ 10 options matérialisées, constat réel 14/08 : 10/37) :
        on relit donc le filtre avec CHAQUE CHIFFRE 0-9 (toute plaque contient
        ses chiffres) en défilant À L'INTÉRIEUR du panneau déroulant à chaque
        passe, puis union. Ne clique aucune option."""
        cible = self._combo_vehicules()
        if cible is None:
            log.warning("MZoneX — recensement D4 : combo véhicules introuvable")
            return []
        champ = cible.query_selector("input")
        vus: list = []
        try:
            champ.click()
            self.page.wait_for_timeout(400)
            for filtre in (" ",) + tuple("0123456789"):
                try:
                    champ.fill("")
                    champ.type(filtre, delay=30)
                    self.page.wait_for_timeout(2200)
                    avant = -1
                    for _ in range(12):          # défilement borné, DANS la liste
                        for o in self.page.query_selector_all(
                                ".wj-dropdown-panel .wj-listbox-item"):
                            try:
                                t = (o.inner_text() or "").strip()
                            except Exception:
                                continue
                            if t and t not in vus:
                                vus.append(t)
                        if len(vus) == avant:
                            break
                        avant = len(vus)
                        try:
                            self.page.evaluate(
                                "for (const d of document.querySelectorAll("
                                "'.wj-dropdown-panel *')) { if (d.scrollHeight"
                                " > d.clientHeight + 40) d.scrollTop += 800 }")
                            self.page.wait_for_timeout(550)
                        except Exception:
                            break
                except Exception:
                    log.exception("MZoneX — recensement D4 : passe %r en "
                                  "échec", filtre)
            log.info("MZoneX — recensement D4 : %d véhicule(s) publié(s) "
                     "dans le groupe courant", len(vus))
            return vus
        finally:
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(400)
            except Exception:
                pass

    # ---------------- lecture de grille ----------------
    def lire_lignes(self, max_pages: int | None = None) -> list[list[str]]:
        """Toutes les lignes de la grille courante, toutes pages confondues.

        La FlexGrid ne matérialise dans le DOM que les lignes visibles à
        l'écran (~13-15) : à chaque page on fait d'abord DÉFILER le viewport
        interne (div[wj-part='root']) pour révéler les lignes restantes, puis
        l'on tourne la page (flèche droite .wj-glyph-right)."""
        max_pages = max_pages or _env_int("MZONEX_PAGES_MAX", 12)
        lignes: list[list[str]] = []
        vus: set[tuple] = set()

        def moissonner():
            for r in self.page.query_selector_all(
                    "div[wj-part='cells'] .wj-row, .wj-cells .wj-row"):
                try:
                    cellules = [(c.inner_text() or "").strip()
                                for c in r.query_selector_all(".wj-cell")]
                except Exception:
                    continue
                cle = tuple(cellules)
                if not any(cellules) or cle in vus:
                    continue
                vus.add(cle)
                lignes.append(cellules)

        for _ in range(max_pages):
            # ---- défilement du viewport : révèle toutes les lignes de la page
            for _ in range(8):
                avant = len(vus)
                moissonner()
                nouveau = len(vus)
                scrolled = self.page.evaluate("""() => {
                    const h = document.querySelector("div[wj-part='root']");
                    if (!h) return false;
                    if (h.scrollTop + h.clientHeight >= h.scrollHeight - 5) return false;
                    h.scrollTop += h.clientHeight; return true;
                }""")
                self.page.wait_for_timeout(600)
                if not scrolled or nouveau == avant:
                    moissonner()
                    if nouveau == avant:
                        break
            if not self._page_suivante():
                break
        return lignes

    def entetes(self) -> list[str]:
        return [(c.inner_text() or "").strip()
                for c in self.page.query_selector_all(
                    "div[wj-part='ch'] .wj-cell, .wj-colheaders .wj-cell")]

    def _page_suivante(self) -> bool:
        """Flèche « page suivante » du pied de grille ; False si dernière page.
        Le glyphe est cliqué en JS (Wijmo ne garantit pas un <button> parent)."""
        sel = _env("MZONEX_SEL_PAGE_SUIVANTE")
        try:
            if sel:
                self.page.click(sel, timeout=3_000)
                self.page.wait_for_timeout(1500)
                return True
            clique = self.page.evaluate("""() => {
                const glyphes = Array.from(document.querySelectorAll('.wj-glyph-right'));
                for (const g of glyphes) {
                    const hote = g.closest('button, a, span, div');
                    const cible = (hote && hote.tagName.toLowerCase() !== 'div') ? hote : g;
                    const cls = (cible.className || '') + ' ' + (g.className || '');
                    const desactive = cible.disabled || /disabled/i.test(cls);
                    if (!desactive && cible.offsetWidth > 0) {
                        cible.click(); return true;
                    }
                }
                return false;
            }""")
            if clique:
                self.page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
        return False


# =============================================================================
# CamtrackPro — session Playwright (connexion + onglet Rapports + rapport)
# ═════════════ CALIBRATION vérifiée sur le portail réel (31/07/2026)
# - Formulaire de connexion : #user / #passw / #submit (bouton « Se connecter »)
# - Barre d'onglets haute : « Rapports » est un item DIV.horizontalbar-menu-item
#   (clic texte robuste par flux JS, comme pour MZoneX)
# - Panneau Rapports : combos « wui » (input + liste déroulante li.itm,
#   conteneur div.vtblist). La SELECTION ne se verrouille qu'avec un VRAI
#   clic souris (page.locator(...).click()) — un el.click() JS retourne le
#   texte libre au lieu du choix. Entrée fonctionne aussi.
#   · #report_templates_filter_reports   → modèle de rapport
#     (« Detail Trajet groupe de véhicules » existe, 4 items « li.itm »
#      commençant par « Detail Trajet »)
#   · #report_templates_filter_units     → objet (véhicule) ; taper les 4
#     chiffres de la plaque filtre la liste (« 0826 » →
#     « 0826 TBS-MERCEDES -LPSA(LSS) ») ; « Ajouter objet » = multi-véhicules
#     (non utilisé : on exécute véhicule par véhicule, plus robuste)
#   · Période : #time_from_report_templates_filter_time /
#     #time_to_report_templates_filter_time, pré-remplie « aujourd'hui »
#   · Exécuter : #report_templates_filter_params_execute (input[type=button])
# - Tableau résultat : PAS d'ID stable → remonter depuis la cellule contenant
#   « Date - Heure Début » vers table ancêtre ; lignes de données = lignes où
#   cells[3] (brut) ressemble à « 2026-07-31 05:20:09 » ; slice(2, 15) donne
#   les 13 colonnes : 0=Regroupement (véhicule), 1=Date-Heure Début,
#   2=Emplacement initial, 3=Date-Heure Fin, 4=Emplacement Final,
#   5=Heures moteur, 6=Durée Idle, 7=Durée En mouvement, 8=Distance (« 1.99
#   Km »), 9=Vitesse moyenne, 10=Vitesse maxi, 11=Pause, 12=Conducteur.
#   Durée d'un cycle complet (1 connexion + 13 exécutions) ≈ 3-4 min.
# =============================================================================

# §0quinquies D4 (complément 14/08 après-midi) — scan de la page « Statuts »
# de CamtrackPro : retourne les plaques affichées, en faisant DÉFILER le
# panneau de liste d'un cran à chaque appel (rendu asynchrone → une seule
# évaluation ne suffit pas). Basé uniquement sur le TEXTE visible
# (« 6256 TCE-CNHTC-LPSA(LSS) ») : insensible aux variations du DOM.
_JS_SCAN_STATUTS = r"""() => {
  const RE = /(\d{3,4})\s?([A-Z]{2,3})-/g;
  const vu = new Set();
  const lis = () => {
    for (const el of document.querySelectorAll('li,div,span,td,label,a')) {
      const t = el.innerText;
      if (!t || t.length > 100 || t.indexOf('-') < 0) continue;
      RE.lastIndex = 0;
      let m;
      while ((m = RE.exec(t)) !== null)
        vu.add((m[1] + m[2]).replace(/\s+/g, '').toUpperCase());
    }
  };
  lis();
  let cible = null, scoreMax = 0;
  for (const el of document.querySelectorAll('div,ul')) {
    if (el.scrollHeight > el.clientHeight + 100 && el.clientHeight > 250) {
      const t = el.innerText || '';
      const score = ((t.match(RE)) || []).length;
      if (score > scoreMax) { scoreMax = score; cible = el; }
    }
  }
  let fini = true;
  if (cible) {
    const avant = cible.scrollTop;
    cible.scrollTop += 800;
    fini = (cible.scrollTop === avant);   // bas atteint (ou pas de défilement)
    lis();
    if (fini) cible.scrollTop = 0;        // remise en haut pour la suite
  }
  return { plaques: Array.from(vu), fini: fini };
}"""


class SessionCamtrackPro:
    """Session CamtrackPro pilotée en Playwright (une connexion pour toute
    la passe : onglet Rapports + choix du modèle une seule fois, puis
    exécutions par véhicule). Sélecteurs surchargeables via CTPRO_SEL_*."""

    def __init__(self, pw):
        self.pw = pw
        self.navigateur = None
        self.page = None
        self.sel_user = _env("CTPRO_SEL_USER", "#user")
        self.sel_pass = _env("CTPRO_SEL_PASS", "#passw")
        self.sel_submit = _env("CTPRO_SEL_SUBMIT", "#submit")
        self.sel_onglet_rapports = _env("CTPRO_ONGLET_RAPPORTS", "Rapports")
        self.sel_combo_modele = _env("CTPRO_SEL_COMBO_MODELE",
                                     "#report_templates_filter_reports")
        self.sel_combo_objet = _env("CTPRO_SEL_COMBO_OBJET",
                                    "#report_templates_filter_units")
        self.sel_executer = _env("CTPRO_SEL_EXECUTER",
                                 "#report_templates_filter_params_execute")
        # v1.10 — le rapport DÉTAILLÉ est « Detail Trajet Vehicule » (1 ligne
        # par trajet, comme les captures métier) ; « Detail Trajet groupe de
        # véhicules » rendait UNE ligne-synthèse par véhicule (jour entier
        # aggloméré → trajet géant à l'écran, vrais trajets jamais lus)
        self.modele = _env("CTPRO_MODELE_TRAJETS", "Detail Trajet Vehicule")
        self.attente_rapport_s = _env_int("CTPRO_ATTENTE_RAPPORT_S", 11)

    def __enter__(self):
        self.navigateur = self.pw.chromium.launch(headless=True, args=["--no-sandbox"])
        self.page = self.navigateur.new_page(viewport={"width": 1680, "height": 950})
        return self

    def __exit__(self, *exc):
        try:
            if self.navigateur:
                self.navigateur.close()
        except Exception:
            pass

    def connecter(self):
        page = self.page
        url = os.environ.get("CAMTRACKPRO_URL")
        if not url:
            raise RuntimeError(
                "CAMTRACKPRO_URL absente du fichier backend\\.env — connexion "
                "CamtrackPro impossible. Verifiez que backend/.env contient "
                "CAMTRACKPRO_URL, CAMTRACKPRO_USER et CAMTRACKPRO_PASSWORD.")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(self.sel_user, state="visible", timeout=30000)
        page.fill(self.sel_user, os.environ["CAMTRACKPRO_USER"])
        page.fill(self.sel_pass, os.environ["CAMTRACKPRO_PASSWORD"])
        page.click(self.sel_submit)
        # l'application est longue à se charger : le menu « Rapports » existe
        # en doublon masqué/visible → attente « attachée » seulement, puis
        # temporisation fixe (le JS de clic filtrera les éléments VISIBLES)
        page.wait_for_selector("text=Rapports", state="attached", timeout=45000)
        page.wait_for_timeout(6000)

    def _clic_texte(self, cible: str) -> bool:
        """Clic robuste par texte (barre d'onglets) — réutilise le flux JS
        MZoneX (NFC, casse-insensible)."""
        return bool(self.page.evaluate(_JS_CLICK_ONGLET, cible))

    def ouvrir_rapports(self) -> bool:
        # v1.10 — la barre d'onglets met ~10-20 s à monter après le login
        # (course à l'ouverture → cycle CTPRO entier silencieusement vide avec
        # l'ancien code). Vérifier le panneau AVANT chaque clic : l'item est
        # une BASCULE, cliquer deux fois refermerait la section (flip-flop).
        onglet = self.sel_onglet_rapports.strip().lower()
        for _ in range(15):
            combo = self.page.locator(self.sel_combo_modele)
            if combo.count() and combo.first.is_visible():
                return True                    # déjà ouvert (ou vient de l'être)
            self.page.evaluate("""(onglet) => {
                const norm = s => (s || '').normalize('NFC').replace(/\\s+/g, ' ')
                                    .trim().toLowerCase();
                for (const el of document.querySelectorAll(
                        'div.horizontalbar-menu-item')) {
                    if (el.offsetWidth > 0 && norm(el.textContent) === onglet) {
                        el.click(); return true;
                    }
                }
                return false;
            }""", onglet) or self._clic_texte(self.sel_onglet_rapports)
            self.page.wait_for_timeout(3000)
        return False

    def _clic_option_combo(self, fragment: str, exact_prefixe: bool = True) -> str:
        """Clique (VRAI clic souris — requis par le composant wui) l'option
        li.itm visible correspondante. Retourne le libellé cliqué ou ''.
        v1.10 : égalité EXACTE avant tout (sinon « Detail Trajet groupe de
        véhicules » gagne contre « Detail Trajet Vehicule » par préfixe)."""
        options = self.page.locator("li.itm:visible")
        n = options.count()
        frag_l = fragment.lower().strip()
        libelle = ""
        for i in range(min(n, 60)):                     # 1 · égalité exacte
            t = (options.nth(i).inner_text() or "").strip()
            if t.lower() == frag_l:
                libelle = t
                break
        if not libelle and exact_prefixe:               # 2 · préfixe
            for i in range(min(n, 40)):
                t = (options.nth(i).inner_text() or "").strip()
                if t.lower().startswith(frag_l):
                    libelle = t
                    break
        if not libelle and n:                           # 3 · contient
            for i in range(min(n, 40)):
                t = (options.nth(i).inner_text() or "").strip()
                if frag_l in t.lower():
                    libelle = t
                    break
        if not libelle and n:
            libelle = (options.first.inner_text() or "").strip()
        if libelle:
            self.page.locator("li.itm:visible", has_text=libelle).first.click()
        return libelle

    def choisir_modele(self) -> bool:
        """Sélectionne le modèle de rapport ET vérifie que c'est bien lui qui
        sera exécuté (v1.10 : refuser de polluer la base avec un rapport
        regroupé si « Detail Trajet Vehicule » n'est pas sélectionné)."""
        page = self.page
        page.click(self.sel_combo_modele)
        page.wait_for_timeout(1000)
        page.fill(self.sel_combo_modele, "")
        page.type(self.sel_combo_modele, self.modele, delay=35)
        page.wait_for_timeout(1800)
        libelle = self._clic_option_combo(self.modele)
        page.wait_for_timeout(1500)
        if not libelle:
            return False
        try:
            courant = (page.locator(self.sel_combo_modele).first
                           .input_value() or "").strip()
        except Exception:
            courant = ""
        ok = courant.lower() == self.modele.lower() or libelle.lower() == self.modele.lower()
        log.info("CTPRO modèle de rapport exécuté : %r (attendu %r)%s",
                 courant or libelle, self.modele, "" if ok else " — REFUS")
        if not ok:
            raise RuntimeError(
                f"modèle de rapport inattendu : {courant or libelle!r} "
                f"(attendu {self.modele!r}) — synchronisation CTPRO interrompue "
                "par sécurité")
        return True

    def recenser_vehicules(self) -> list:
        """§0quinquies D4 (14/08/2026, arbitrage métier) — liste COMPLÈTE des
        objets publiés par CamtrackPro : combo « objet » du rapport ouvert
        puis VIDÉ (tous les objets du compte s'affichent), lecture des
        options, Échap. Ne lance aucun rapport. Cap 200 options."""
        page = self.page
        try:
            page.click(self.sel_combo_objet)
            page.wait_for_timeout(800)
            page.keyboard.press("Control+a")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(2500)
            vus = []
            options = page.locator("li.itm:visible")
            for i in range(min(options.count(), 200)):
                try:
                    t = (options.nth(i).inner_text() or "").strip()
                except Exception:
                    continue
                if t and t not in vus:
                    vus.append(t)
            log.info("CamtrackPro — recensement D4 : %d véhicule(s) "
                     "publié(s)", len(vus))
            return vus
        except Exception:
            log.exception("CamtrackPro — recensement D4 en échec")
            return []
        finally:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            except Exception:
                pass

    # -------- complément D4 : page « Statuts » (liste complète du compte) --
    def recenser_statuts(self, max_etapes: int = 30) -> list:
        """§0quinquies D4 (complément 14/08 après-midi) — le filtre « objet »
        du rapport CamtrackPro omet des unités pourtant visibles dans
        l'onglet « Statuts » (constat métier 14/08 : 6256/7136/7206TCE et
        7766TBL). L'onglet Statuts liste TOUTES les unités du compte :
        clic sur « Statuts » (barre du haut), attente du chargement, puis
        extraction des plaques depuis le TEXTE affiché
        (« 6256 TCE-CNHTC-… ») — insensible au DOM — avec défilement borné
        du panneau. En cas d'échec → [] : le recensement combo fait foi."""
        page = self.page
        try:
            clique = False
            for _ in range(10):
                clique = bool(page.evaluate("""(cible) => {
                    const norm = s => (s || '').normalize('NFC')
                        .replace(/\\s+/g, ' ').trim().toLowerCase();
                    for (const el of document.querySelectorAll(
                            'div.horizontalbar-menu-item')) {
                        if (el.offsetWidth > 0 &&
                                norm(el.textContent) === cible) {
                            el.click(); return true;
                        }
                    }
                    return false;
                }""", "statuts"))
                if clique:
                    break
                page.wait_for_timeout(2000)
            if not clique:
                log.info("CamtrackPro — recensement D4 (Statuts) : onglet "
                         "introuvable, combo seule fera foi")
                return []
            page.wait_for_timeout(7000)      # chargement liste + carte
            vus: set = set()
            for _ in range(max_etapes):
                try:
                    r = page.evaluate(_JS_SCAN_STATUTS) or {}
                except Exception:
                    break
                vus.update(r.get("plaques") or [])
                if r.get("fini"):
                    break
                page.wait_for_timeout(600)
            log.info("CamtrackPro — recensement D4 (page Statuts) : %d "
                     "véhicule(s) publié(s)", len(vus))
            return sorted(vus)
        except Exception:
            log.exception("CamtrackPro — recensement D4 (Statuts) en échec "
                          "— combo seule fera foi")
            return []

    def executer_pour(self, fragment_plaque: str) -> tuple[list[list[str]], str]:
        """Filtre l'objet sur la plaque, lance le rapport, lit le corps du
        tableau. Retour : (listes de 13 cellules, libellé complet de l'objet).
        v1.10 — lecture du SEUL tableau de données (report-result-body-table,
        la ligne TOTAL du pied est dans un autre tableau : jamais lue) et
        attente STABILISÉE du résultat (le corps se vide puis se remplit)."""
        page = self.page
        page.click(self.sel_combo_objet)
        page.wait_for_timeout(800)
        page.keyboard.press("Control+a")
        page.type(self.sel_combo_objet, fragment_plaque, delay=55)
        page.wait_for_timeout(2000)
        choisi = self._clic_option_combo(fragment_plaque + " ")
        if not choisi:
            log.warning("CTPRO : aucun objet ne commence par « %s »", fragment_plaque)
            page.keyboard.press("Escape")
            return [], ""
        page.wait_for_timeout(800)
        page.click(self.sel_executer)
        # attente du NOUVEAU résultat : lecture toutes les 2 s jusqu'à 2
        # lectures identiques NON vides, ou barre « Rapports de 0 à 0 sur 0 »
        # confirmée (véhicule sans activité), bornée à ~40 s
        lignes: list[list[str]] = []
        vide_confirme = 0
        for _ in range(max(8, self.attente_rapport_s * 2)):
            page.wait_for_timeout(2000)
            courantes = page.evaluate(_JS_LIRE_RESULTAT_CTPRO)
            if courantes and courantes == lignes:
                break                       # stable et non vide
            if courantes != lignes:
                lignes = courantes
                vide_confirme = 0
                continue
            if not courantes:               # vide stable : confirmation ?
                vide_confirme += 1
                if vide_confirme >= 4:      # ~8 s de vide persistant
                    break
        # sécurité pagination (rare : > 25 lignes/jour) — une seule tentative
        # d'élargissement à 500 lignes/page si la barre annonce plus de lignes
        try:
            barre = " ".join(page.locator(
                "table.report-result-toolbar-table").all_inner_texts())
            import re as _re
            m = _re.search(r"sur\\s*(\\d+)", barre)
            if m and int(m.group(1)) > len(lignes) and len(lignes) in (25, 50):
                el = page.locator("table.report-result-toolbar-table >> text=500")
                if el.count():
                    el.first.click()
                    page.wait_for_timeout(4000)
                    lignes = page.evaluate(_JS_LIRE_RESULTAT_CTPRO)
        except Exception:
            pass
        log.info("CTPRO rapport « %s » (%s) : %d ligne(s)",
                 choisi, fragment_plaque, len(lignes))
        return lignes, choisi


# v1.10 — le corps de données du rapport « Detail Trajet Vehicule » vit dans
# table.report-result-body-table (l'en-tête et la ligne TOTAL sont des tables
# séparées : jamais lues). 13 cellules : 0=vide, 1=Date et heure début,
# 2=Emplacement initial, 3=Date et heure fin, 4=Emplacement final,
# 5=Heures moteur, 6=Ralenti moteur, 7=En mouvement (« 1:47:46 »),
# 8=Distance parcourue (« 14.60 Km »), 9=Vitesse moyenne, 10=Vitesse maxi,
# 11=Durée (depuis…), 12=Conducteur. La colonne véhicule n'existe plus dans
# le détail : l'objet choisi dans le filtre fait foi.
_JS_LIRE_RESULTAT_CTPRO = """() => {
  const txt = e => (e.innerText||'').trim();
  const lignes = [];
  for (const t of document.querySelectorAll('table.report-result-body-table')) {
    for (const tr of t.querySelectorAll('tr')) {
      const cells = Array.from(tr.querySelectorAll('th,td'))
        .map(c => txt(c).replace(/\\n/g,' '));
      if (cells.length >= 13 && /^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}/.test(cells[1])) {
        lignes.push(cells.slice(0, 13));
      }
    }
  }
  return lignes;
}"""


# =============================================================================
# NIVEAU 1 — temps réel (onglet Événements) → trajets PROVISOIRE (§2.2)
# =============================================================================
class CollectorBase:
    """Interface d'une source de données GPS (§10 « modulaire »)."""

    source: SourceEvenement = SourceEvenement.SIMULATEUR

    def collecter(self) -> list[dict]:
        """Points bruts normalisés :
        {gps_associe, horodatage, lat, lng, adresse, vitesse, moteur, type_evenement}"""
        raise NotImplementedError

    # --- nettoyage systématique avant insertion (§10) -------------------
    def normaliser(self, brut: list[dict]) -> list[dict]:
        vus = set()
        propres = []
        for p in brut:
            try:
                p["gps_associe"] = ident_vehicule(p["gps_associe"]) or str(p["gps_associe"]).strip()
                ts = p["horodatage"]
                if isinstance(ts, str):
                    ts = parse_dt(ts) or datetime.fromisoformat(ts)
                p["horodatage"] = ts
                cle = (p["gps_associe"], ts.isoformat(), str(p.get("type_evenement")))
                if cle in vus:                        # dédoublonnage intra-lot
                    continue
                vus.add(cle)
                lat, lng = float(p["lat"]), float(p["lng"])
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):  # plausibilité
                    continue
                p["lat"], p["lng"] = round(lat, 6), round(lng, 6)
                p["vitesse"] = max(0.0, float(p.get("vitesse") or 0))
                p["moteur"] = "ON" if str(p.get("moteur", "ON")).strip().upper() == "ON" else "OFF"
                propres.append(p)
            except (KeyError, TypeError, ValueError):
                log.warning("Point GPS invalide écarté : %r", p)
        propres.sort(key=lambda p: p["horodatage"])   # rejeu chronologique
        return propres

    def inserer(self, points: list[dict]) -> int:
        db = SessionLocal()
        inseres = 0
        try:
            mapping: dict[str, Vehicule] = {}
            for v in db.scalars(select(Vehicule)).all():
                if v.gps_associe:
                    mapping[str(v.gps_associe).strip().upper()] = v
                mapping[v.plaque.strip().upper()] = v
            vus: dict[str, Vehicule] = {}
            for p in points:
                vehicule = mapping.get(p["gps_associe"].upper())
                if vehicule is None:
                    # §0quater D1 (14/08/2026) — véhicule inconnu → fiche
                    # créée automatiquement (plateforme = ce collecteur) et
                    # exploitée immédiatement ; un identifiant parasite est
                    # écarté avec trace en fenêtre noire (D3).
                    plateforme = getattr(self.source, "value", str(self.source))
                    vehicule = creer_vehicule_auto(
                        db, p["gps_associe"], plateforme)
                    if vehicule is None:
                        continue
                    mapping[vehicule.plaque.strip().upper()] = vehicule
                    if vehicule.gps_associe:
                        mapping[str(vehicule.gps_associe).strip().upper()] = vehicule
                if p.get("conducteur"):
                    creer_conducteur_auto(db, p["conducteur"])   # §0quater D2
                vus[vehicule.id] = vehicule

                # Garantie fraîcheur position : maintien du dernier état connu
                if vehicule.last_event_at is None or p["horodatage"] >= vehicule.last_event_at:
                    vehicule.last_lat, vehicule.last_lng = p["lat"], p["lng"]
                    vehicule.last_vitesse = p["vitesse"]
                    vehicule.last_event_at = p["horodatage"]
                    vehicule.moteur_on = (p["moteur"] == "ON")

                # anti-rejeu : même événement déjà collecté à la passe
                # précédente → ignoré (collecte périodique idempotente)
                deja = db.scalar(select(func.count(EvenementGPS.id)).where(
                    EvenementGPS.vehicule_id == vehicule.id,
                    EvenementGPS.horodatage == p["horodatage"],
                    EvenementGPS.type_evenement == p.get("type_evenement")
                    if p.get("type_evenement") else True)) or 0
                if deja:
                    continue
                ingest_event(db, vehicule, p["horodatage"], p["lat"], p["lng"],
                             p.get("adresse"), p["vitesse"], p["moteur"],
                             p.get("type_evenement"), self.source)
                # §0septies B4/B5 (20/08/2026) — alertes conduite EN DIRECT :
                # vitesse > seuil hors géozone (B4) ; roulage sans clé MZoneX
                # (B5 — badge None pour les sources qui ne la publient pas :
                # la fonction n'évalue alors jamais « sans badge », loi B5)
                try:
                    verifier_alertes_conduite(
                        db, vehicule, p["horodatage"], p["vitesse"],
                        (None if "badge_code" not in p
                         else p["badge_code"] is not None),
                        en_geozone(p["lat"], p["lng"]), self.source)
                except Exception:
                    log.exception("Vérification conduite en échec (%s) — "
                                  "le point, lui, est enregistré",
                                  vehicule.plaque)
                inseres += 1
            # v1.17 — AUTO-RÉPARATION (bug métier 05/08) : un « Début du
            # trajet » CONNU (anti-rejeu) mais resté SANS trajet (création
            # manquée lors d'une passe défectueuse) n'était JAMAIS ré-essayé
            # → le trajet en cours restait invisible (2736TCC 11:37, 4886TBU
            # 11:00). On ré-ingère une fois ; la couverture est revérifiée à
            # chaque passe (idempotent).
            inseres += _reparer_debuts_sans_trajet(db, list(vus.values()),
                                                   self.source)
            db.commit()
            return inseres
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def run(self) -> int:
        brut = self.collecter()
        return self.inserer(self.normaliser(brut))


def _reparer_debuts_sans_trajet(db, vehicules: list, source) -> int:
    """v1.17 — auto-réparation du Niveau 1 (bug métier 05/08).

    Pour chaque véhicule vu dans la passe : si le DERNIER « Début du trajet »
    connu (table technique, peuplée une fois par l'anti-rejeu) ne recouvre
    AUCUN trajet (ouvert ou fermé, quel que soit le statut) et qu'aucune
    « Fin du trajet » ne lui est postérieure, alors le trajet en cours a été
    manqué → ré-ingestion UNE fois de l'événement stocké (l'anti-rejeu l'aurait
    sinon ignoré pour toujours, l'écran restant vide jusqu'à la fin officielle
    du trajet — c'était le cas 2736TCC 11:37 et 4886TBU 11:00).
    Les trajets TERMINÉS ne sont pas recréés ici : la couverture complète des
    fins de trajet est déjà garantie par les officiels du Niveau 2 (15 min)."""
    from .config import AGE_MAX_REPARATION_S, jour_attribution, now_local
    from .engine import get_seuils, ingest_event
    from .models import EvenementGPS as _EG, SuiviJournalier as _SJ, \
        Trajet as _T, TypeEvenement as _TE

    maintenant = now_local()
    borne = datetime.combine(jour_attribution(maintenant), datetime.min.time())
    tol = timedelta(seconds=900)
    age_max = _env_int("REPARATION_AGE_MAX_S", AGE_MAX_REPARATION_S)
    seuil_arret = float(get_seuils(db).get("SEUIL_VITESSE_ARRET", 3))
    rep = 0
    for vehicule in vehicules:
        try:
            ev = db.scalars(select(_EG).where(
                _EG.vehicule_id == vehicule.id,
                _EG.horodatage >= borne,
                _EG.type_evenement == _TE.DEBUT_MOUVEMENT,
            ).order_by(_EG.horodatage.desc())).first()
            if ev is None:
                continue
            fin_apres = db.scalar(select(func.count(_EG.id)).where(
                _EG.vehicule_id == vehicule.id,
                _EG.horodatage > ev.horodatage,
                _EG.type_evenement == _TE.ARRET)) or 0
            if fin_apres:
                continue                     # trajet terminé → Niveau 2 gère
            # §0quinquies (14/08/2026) — FRAÎCHEUR : un « Début du trajet »
            # ANCIEN sans « Fin » ni mouvement n'est pas un camion en route
            # mais un boîtier muet (contact coupé, camion garé) : le rouvrir
            # produisait un va-et-vient re-création/rejet manœuvre à chaque
            # cycle (constat réel 14/08, 7936TCB et 8806TCB). Au-delà de
            # REPARATION_AGE_MAX_S (45 min), R2 et sa garde de fraîcheur
            # reprennent la main dès le premier roulage réel.
            if (maintenant - ev.horodatage).total_seconds() > age_max:
                log.debug("Auto-réparation ignorée (%s) : début %s plus "
                          "ancien que %ds (boîtier probablement muet)",
                          vehicule.plaque, ev.horodatage, age_max)
                continue
            # §0quinquies (correctif 14/08 PM) — GARDE DE MOUVEMENT : un
            # « Début du trajet » seul ne prouve rien (le portail les publie à
            # 0 km/h, blips de contact compris : va-et-vient ligne fantôme /
            # rejet manœuvre constaté le 14/08, 0926TBV et 8076TCB). On ne
            # répare que si la dernière position connue du camion PROUVE le
            # roulage (vitesse > seuil, signal ≤ 20 min — la condition R2,
            # §0quater) ; sinon la ligne « en cours » orange officielle du
            # Niveau 2 prend le relais au plus tard 15 min après le départ.
            dernier = db.scalars(select(_EG).where(
                _EG.vehicule_id == vehicule.id,
            ).order_by(_EG.horodatage.desc())).first()
            en_route = bool(
                dernier is not None
                and float(dernier.vitesse or 0.0) > seuil_arret
                and (maintenant - dernier.horodatage).total_seconds()
                <= 1200)
            if not en_route:
                log.debug("Auto-réparation ignorée (%s) : pas de preuve de "
                          "roulage récent (blip de contact probable)",
                          vehicule.plaque)
                continue
            suivi = db.scalars(select(_SJ).where(
                _SJ.vehicule_id == vehicule.id,
                _SJ.date_jour == jour_attribution(ev.horodatage))).first()
            couvert = False
            if suivi is not None:
                for t in db.scalars(select(_T).where(
                        _T.suivi_id == suivi.id)).all():
                    fin_t = (t.heure_fin + tol) if t.heure_fin else maintenant + tol
                    if (t.heure_debut - tol) <= ev.horodatage <= fin_t:
                        couvert = True
                        break
            if couvert:
                continue
            log.info("v1.17 — Auto-réparation : « Début du trajet » de %s (%s) "
                     "sans trajet → ligne « en cours » recréée",
                     vehicule.plaque, ev.horodatage)
            ingest_event(db, vehicule, ev.horodatage, ev.latitude, ev.longitude,
                         ev.adresse, max(12.0, ev.vitesse or 0.0), "ON",
                         _TE.DEBUT_MOUVEMENT, source)
            rep += 1
        except Exception:
            log.exception("Auto-réparation « Début du trajet » en échec — %s",
                          getattr(vehicule, "plaque", "?"))
    return rep


# Mapping des libellés d'événements MZoneX (constaté sur le portail réel) →
# comportement moteur §7. « Début du trajet » : vitesse forcée > 0 pour que le
# moteur OUVRRE le trajet à cet horodatage (vitesse affichée 0 km/h).
def _point_depuis_evenement(prefixe: str, cellules: list[str]) -> dict | None:
    from .models import TypeEvenement as TE
    ci = {
        # indices vérifiés sur le portail réel (3 premières colonnes = icônes)
        "veh": _env_int(f"{prefixe}_EVT_COL_VEH", 3),
        "evt": _env_int(f"{prefixe}_EVT_COL_EVT", 4),
        "lieu": _env_int(f"{prefixe}_EVT_COL_LIEU", 5),
        "temps": _env_int(f"{prefixe}_EVT_COL_TEMPS", 6),
        "lat": _env_int(f"{prefixe}_EVT_COL_LAT", 7),
        "lng": _env_int(f"{prefixe}_EVT_COL_LNG", 8),
        "vit": _env_int(f"{prefixe}_EVT_COL_VIT", 9),
    }
    if len(cellules) < max(ci["veh"], ci["evt"], ci["lieu"], ci["temps"],
                           ci["lat"], ci["lng"]) + 1:
        return None
    # §0quater D2 — « dernier conducteur » (col. 11, surchargeable) si visible
    icond = _env_int(f"{prefixe}_EVT_COL_CONDUCTEUR", 11)
    conducteur = (cellules[icond].strip()
                  if icond < len(cellules) else "").strip()
    ts = parse_dt(cellules[ci["temps"]])
    if ts is None:
        return None
    lib = cellules[ci["evt"]].strip().lower()
    lat = parse_float(cellules[ci["lat"]])
    lng = parse_float(cellules[ci["lng"]])
    if lat is None or lng is None:
        return None
    # la Vitesse est parfois hors du viewport matérialisé (virtualisation Wijmo)
    vit = parse_float(cellules[ci["vit"]]) if ci["vit"] < len(cellules) else None
    vit = vit or 0.0

    if "début du trajet" in lib or "debut du trajet" in lib:
        vitesse, moteur, type_ev = max(12.0, vit), "ON", TE.DEBUT_MOUVEMENT
    elif "fin du trajet" in lib:
        vitesse, moteur, type_ev = 0.0, "OFF", TE.ARRET
    elif lib.startswith("stationnement"):
        vitesse, moteur, type_ev = 0.0, "OFF", TE.ARRET
    elif "exces de vitesse" in lib or "excès de vitesse" in lib:
        vitesse, moteur, type_ev = max(91.0, vit), "ON", TE.EXCES_VITESSE
    elif "accélération" in lib or "acceleration" in lib or "accelération" in lib:
        vitesse, moteur, type_ev = max(20.0, vit), "ON", TE.ACCELERATION_BRUSQUE
    elif "freinage" in lib:
        vitesse, moteur, type_ev = max(15.0, vit), "ON", TE.FREINAGE_BRUSQUE
    else:  # position régulière / hors voyage / autre
        vitesse = vit
        moteur = "ON" if vit > 0 else "OFF"
        type_ev = TE.POSITION
    return {"gps_associe": cellules[ci["veh"]], "horodatage": ts,
            "lat": lat, "lng": lng, "adresse": cellules[ci["lieu"]],
            "vitesse": vitesse, "moteur": moteur, "type_evenement": type_ev,
            "conducteur": conducteur}


class MZoneXCollector(CollectorBase):
    """Collecteur Playwright — onglet Événements MZoneX (Niveau 1, §2.2).

    Séquence calibrée : connexion SSO → onglet « Evénements » → groupe
    « LSS (LPSA) (37) » → filtre type « DEMARRAGE/ARRET (2) » → lecture
    paginée de la FlexGrid. Tout est surchargeable par MZONEX_* (voir
    .env.exemple) sans toucher au code."""

    source = SourceEvenement.MZONEX

    def collecter(self) -> list[dict]:
        from playwright.sync_api import sync_playwright  # noqa: import différé

        cible_onglet = _env("MZONEX_ONGLET_EVENEMENTS", "evénements")
        groupe = _env("MZONEX_GROUPE", "LSS (LPSA)")
        fragment_groupe = _env("MZONEX_COMBO_GROUPE", "favourite")
        fragment_type = _env("MZONEX_COMBO_TYPE_EVT", "all event types")
        opt_type = _env("MZONEX_OPT_DEMARRAGE_ARRET", "DEMARRAGE")
        pages = _env_int("MZONEX_EVT_PAGES", 12)

        points: list[dict] = []
        with sync_playwright() as pw:
            for tentative in range(3):  # retry automatique (§10 résilience)
                try:
                    with SessionMZoneX(pw) as mz:
                        mz.connecter()
                        if not mz.ouvrir_onglet(cible_onglet):
                            raise RuntimeError(f"onglet « {cible_onglet} » introuvable")
                        mz.choisir_combo(fragment_groupe, groupe)
                        mz.page.wait_for_timeout(6000)
                        mz.choisir_combo(fragment_type, opt_type)
                        mz.page.wait_for_timeout(5000)
                        lignes = mz.lire_lignes(max_pages=pages)
                    for cellules in lignes:
                        p = _point_depuis_evenement("MZONEX", cellules)
                        if p:
                            points.append(p)
                    log.info("MZoneX (Événements) : %d lignes lues, %d points exploitables",
                             len(lignes), len(points))
                    break
                except Exception:
                    log.exception("MZoneX : tentative %s/3 échouée", tentative + 1)
        return points


class CamtrackProCollector(CollectorBase):
    """Collecteur Selenium — statuts courants CamtrackPro (Niveau 1, §10)."""

    source = SourceEvenement.CAMTRACKPRO

    def collecter(self) -> list[dict]:
        from selenium import webdriver  # noqa: import différé
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        sel_user = _env("CAMTRACKPRO_SEL_USER", "#username")
        sel_pass = _env("CAMTRACKPRO_SEL_PASS", "#password")
        sel_submit = _env("CAMTRACKPRO_SEL_SUBMIT", "button[type=submit]")
        sel_lignes = _env("CAMTRACKPRO_SEL_LIGNES", "table tr")
        url_suivi = _env("CAMTRACKPRO_URL_SUIVI")

        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        driver = webdriver.Chrome(options=options)
        try:
            url_base = os.environ.get("CAMTRACKPRO_URL")
            if not url_base:
                raise RuntimeError(
                    "CAMTRACKPRO_URL absente du fichier backend\\.env — "
                    "connexion CamtrackPro impossible. Verifiez que "
                    "backend/.env contient CAMTRACKPRO_URL, CAMTRACKPRO_USER "
                    "et CAMTRACKPRO_PASSWORD.")
            driver.get(url_base)
            driver.find_element(By.CSS_SELECTOR, sel_user).send_keys(
                os.environ["CAMTRACKPRO_USER"])
            driver.find_element(By.CSS_SELECTOR, sel_pass).send_keys(
                os.environ["CAMTRACKPRO_PASSWORD"])
            driver.find_element(By.CSS_SELECTOR, sel_submit).click()
            if url_suivi:
                driver.get(url_suivi)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel_lignes)))
            lignes = [
                [c.text.strip() for c in ligne.find_elements(By.TAG_NAME, "td")]
                for ligne in driver.find_elements(By.CSS_SELECTOR, sel_lignes)
            ]
            ci = {"gps": _env_int("CAMTRACKPRO_COL_GPS", 0),
                  "lat": _env_int("CAMTRACKPRO_COL_LAT", 1),
                  "lng": _env_int("CAMTRACKPRO_COL_LNG", 2),
                  "adr": _env_int("CAMTRACKPRO_COL_ADR", 3),
                  "vit": _env_int("CAMTRACKPRO_COL_VIT", 4),
                  "mot": _env_int("CAMTRACKPRO_COL_MOT", 5)}
            points = []
            for cellules in lignes:
                if len(cellules) < max(ci.values()) + 1:
                    continue
                try:
                    points.append({
                        "gps_associe": cellules[ci["gps"]], "horodatage": now_local(),
                        "lat": parse_float(cellules[ci["lat"]]),
                        "lng": parse_float(cellules[ci["lng"]]),
                        "adresse": cellules[ci["adr"]],
                        "vitesse": parse_float(cellules[ci["vit"]]) or 0.0,
                        "moteur": cellules[ci["mot"]]})
                except (IndexError, TypeError):
                    continue
            points = [p for p in points if p["lat"] is not None and p["lng"] is not None]
            log.info("CamtrackPro : %d lignes lues, %d points exploitables",
                     len(lignes), len(points))
            return points
        finally:
            driver.quit()


# =============================================================================
# NIVEAU 2 — consolidé (onglet Trajets / rapport « Detail Trajet ») (§2.3)
# =============================================================================
def _trajets_valides_depuis_lignes(lignes_texte: list[list[str]],
                                   prefixe: str, source: str) -> list[dict]:
    """Lignes de l'onglet Trajets → trajets officiels normalisés :
    {plaque/gps, debut, fin, distance_km, source}. Indices (réel MZoneX) :
    4=Véhicule, 8=Heure de début, 10=Heure de fin, 6=Distance — surchargeables
    par {PREFIXE}_TRA_COL_*."""
    ci = {
        "veh": _env_int(f"{prefixe}_TRA_COL_VEH", 4),
        "debut": _env_int(f"{prefixe}_TRA_COL_DEBUT", 8),
        "fin": _env_int(f"{prefixe}_TRA_COL_FIN", 10),
        "dist": _env_int(f"{prefixe}_TRA_COL_DIST", 6),
        # §0quater D2 — « Conducteur » (col. 11, surchargeable) si visible
        "cond": _env_int(f"{prefixe}_TRA_COL_CONDUCTEUR", 11),
    }
    besoin = max(ci["veh"], ci["debut"], ci["fin"], ci["dist"]) + 1
    items = []
    for cellules in lignes_texte:
        if len(cellules) < besoin:
            continue
        debut = parse_dt(cellules[ci["debut"]])
        fin = parse_dt(cellules[ci["fin"]])
        if debut is None:
            continue
        items.append({"plaque": ident_vehicule(cellules[ci["veh"]]),
                      "gps_associe": ident_vehicule(cellules[ci["veh"]]),
                      "debut": debut, "fin": fin,
                      "distance_km": parse_float(cellules[ci["dist"]]),
                      "conducteur": (cellules[ci["cond"]].strip()
                                     if ci["cond"] < len(cellules) else ""),
                      "source": source})
    return items


class MZoneXTrajetsCollector:
    """Connecteur Niveau 2 — onglet TRAJETS de MZoneX (Playwright).

    L'onglet expose des trajets DÉJÀ calculés par MZoneX avec les mêmes seuils
    métier que les nôtres (0,3 km / 20 min) ; ils n'y apparaissent qu'une fois
    TERMINÉS (heure de fin vide = trajet encore en cours : pris tels quels,
    ils confirment simplement le PROVISOIRE en cours).

    v1.10 — la grille TOUT VÉHICULES est PLAFONNÉE par le portail (~100
    lignes les plus récentes : les lignes du matin sont évincées au fil de la
    journée → trajets jamais lus, constat métier 03/08/2026 sur le 0916TBV).
    La lecture fiable, comme le fait le métier, est véhicule par véhicule via
    le filtre « Rechercher véhicules » (la journée tient alors en page 1/1)."""

    source = "MZONEX"

    def collecter_valides(self) -> list[dict]:
        from playwright.sync_api import sync_playwright  # noqa: import différé
        from .database import SessionLocal
        from .models import Vehicule

        self.recensement: list = []      # §0quinquies D4 — lu par la sync
        cible_onglet = _env("MZONEX_ONGLET_TRAJETS", "trajets")
        groupe = _env("MZONEX_GROUPE", "LSS (LPSA)")
        fragment_groupe = _env("MZONEX_COMBO_GROUPE", "favourite")
        pause = _env_int("MZONEX_PAUSE_VEHICULE_S", 2)

        db = SessionLocal()
        try:
            vehicules = [v.plaque for v in db.scalars(
                select(Vehicule).where(Vehicule.plateforme_gps == "MZONEX",
                                       Vehicule.statut == "ACTIF")
                .order_by(Vehicule.plaque)).all()]
        finally:
            db.close()
        if not vehicules:
            log.warning("MZoneX Trajets : aucun véhicule MZONEX actif en base")
            return []

        items: list[dict] = []
        with sync_playwright() as pw:
            for tentative in range(3):
                try:
                    with SessionMZoneX(pw) as mz:
                        mz.connecter()
                        if not mz.ouvrir_onglet(cible_onglet):
                            raise RuntimeError(f"onglet « {cible_onglet} » introuvable")
                        mz.choisir_combo(fragment_groupe, groupe)
                        mz.page.wait_for_timeout(5000)
                        # §0quinquies D4 — liste publiée par le portail AVANT
                        # la boucle par véhicule (une seule ouverture de la
                        # combo ; ne clique aucune option)
                        self.recensement = mz.recenser_vehicules()
                        for plaque in vehicules:
                            try:
                                if not mz.filtrer_vehicule(plaque):
                                    continue
                                lignes = mz.lire_lignes(max_pages=2)
                            except Exception:
                                log.exception("MZoneX Trajets « %s » : échec", plaque)
                                lignes = []
                            bruts = _trajets_valides_depuis_lignes(
                                lignes, "MZONEX", "MZONEX")
                            # sécurité anti-relecture : ne retenir que CE véhicule
                            norme = plaque.replace(" ", "").upper()
                            bruts = [b for b in bruts if norme in
                                     (b.get("plaque") or "").replace(" ", "").upper()]
                            items.extend(bruts)
                            if lignes:
                                log.info("MZoneX Trajets « %s » : %d ligne(s), "
                                         "%d trajet(s)", plaque, len(lignes), len(bruts))
                            time.sleep(pause)
                    log.info("MZoneX (Trajets) : %d trajets lus sur %d véhicule(s)",
                             len(items), len(vehicules))
                    break
                except Exception:
                    log.exception("MZoneX Trajets : tentative %s/3 échouée", tentative + 1)
        return items


class CamtrackProTrajetsCollector:
    """Connecteur Niveau 2 — rapport détaillé « Detail Trajet Vehicule »
    CamtrackPro (Playwright, calibré sur le portail réel).

    v1.10 — NE JAMAIS revenir au rapport « Detail Trajet groupe de
    véhicules » : il rend UNE ligne-synthèse par véhicule (premier départ →
    dernière fin de la journée, km cumulés), ce qui affichait un trajet
    géant unique et ne lisait jamais les vrais trajets (constat métier
    03/08/2026 sur le 4296TCC et le 5716TBS ; `choisir_modele` refuse
    désormais d'exécuter un modèle inattendu).

    §5 (Addendum v1.4) : pas de source temps réel fiable sur CamtrackPro →
    les trajets CamtrackPro entrent directement en VALIDÉ dès qu'ils sont
    clôturés et visibles dans le rapport (période « aujourd'hui » par défaut
    sur le portail — colonnes surchargeables via CAMTRACKPRO_TRA_COL_*).

    Une connexion pour toute la passe ; le rapport est exécuté véhicule par
    véhicule (liste = véhicules `plateforme=CAMTRACKPRO` en base — fragment =
    4 premiers caractères de la plaque, ex. « 0826 »)."""

    source = "CAMTRACKPRO"

    # indices dans la tranche de 13 colonnes du tableau résultat
    COLS_DEFAUT = {"veh": 0, "debut": 1, "fin": 3, "dist": 8}

    def _fragments_vehicules(self) -> list[str]:
        """Véhicules CamtrackPro connus → fragments de saisie (« 0826 »…).
        Surcharge complète possible via CAMTRACKPRO_FRAGMENTS (séparés par
        des virgules) si un jour la numérotation diverge."""
        force = _env("CAMTRACKPRO_FRAGMENTS")
        if force:
            return [f.strip() for f in force.split(",") if f.strip()]
        db = SessionLocal()
        try:
            fragments = []
            for v in db.scalars(select(Vehicule)).all():
                if (v.plateforme_gps or "").upper() != "CAMTRACKPRO":
                    continue
                # la plaque est la référence (le gps_associe est préfixé
                # « OBC- ») : fragment = chiffres de tête de la plaque
                # (« 0826TBS » → « 0826 »)
                chiffres = ""
                for ch in (v.plaque or ""):
                    if not ch.isdigit():
                        break
                    chiffres += ch
                frag = chiffres or (v.plaque or "")[:4]
                if frag and frag not in fragments:
                    fragments.append(frag)
            return fragments
        finally:
            db.close()

    def _trajets_depuis_lignes(self, lignes: list[list[str]],
                               plaque_attendue: str) -> list[dict]:
        ci = {"veh": _env_int("CAMTRACKPRO_TRA_COL_VEH", 0),
              "debut": _env_int("CAMTRACKPRO_TRA_COL_DEBUT", 1),
              "fin": _env_int("CAMTRACKPRO_TRA_COL_FIN", 3),
              "dist": _env_int("CAMTRACKPRO_TRA_COL_DIST", 8),
              # Addendum v1.5 §4.2 — « Durée En mouvement » (col. 7 du rapport)
              "mov": _env_int("CAMTRACKPRO_TRA_COL_MOV", 7)}
        besoin = max(ci.values()) + 1
        items = []
        for cellules in lignes:
            if len(cellules) < besoin:
                continue
            debut = parse_dt(cellules[ci["debut"]])
            fin = parse_dt(cellules[ci["fin"]])
            if debut is None or fin is None:      # trajet non clôturé → ignoré
                continue
            plaque = ident_vehicule(cellules[ci["veh"]]) or plaque_attendue
            # Addendum v1.5 §4.2 : validité = distance ≥ 0,3 km ET
            # en_mouvement ≥ 20 min (le rejet est tranché côté réconciliation,
            # qui tient les seuils de Paramètres et journalise l'audit)
            items.append({"plaque": plaque, "gps_associe": plaque,
                          "debut": debut, "fin": fin,
                          "distance_km": parse_float(cellules[ci["dist"]]),
                          "duree_mouvement_s": parse_duree_hms(cellules[ci["mov"]]),
                          "source": "CAMTRACKPRO"})
        return items

    def collecter_valides(self) -> list[dict]:
        from playwright.sync_api import sync_playwright  # noqa: import différé

        self.recensement: list = []      # §0quinquies D4 — lu par la sync
        fragments = self._fragments_vehicules()
        if not fragments:
            log.warning("CamtrackPro Trajets : aucun véhicule « CAMTRACKPRO » "
                        "en base — synchronisation ignorée")
            return []
        pause = _env_int("CAMTRACKPRO_PAUSE_VEHICULE_S", 2)

        total: list[dict] = []
        with sync_playwright() as pw:
            for tentative in range(3):
                try:
                    with SessionCamtrackPro(pw) as ctp:
                        ctp.connecter()
                        # §0quinquies D4 (complément 14/08) — la page « Statuts »
                        # liste TOUTES les unités du compte ; le filtre objet
                        # du rapport, lu juste après, en omet parfois
                        statuts = ctp.recenser_statuts()
                        if not ctp.ouvrir_rapports():
                            raise RuntimeError("onglet « Rapports » introuvable")
                        if not ctp.choisir_modele():
                            raise RuntimeError(f"modèle « {ctp.modele} » introuvable")
                        # §0quinquies D4 — listes publiées par le portail AVANT
                        # la boucle par véhicule : UNION Statuts ∪ combo du
                        # rapport, fusionnée sur la PLAQUE normalisée (les
                        # deux sources n'ont pas la même forme de libellé)
                        combo = ctp.recenser_vehicules()
                        n_stat = {plaque_depuis_libelle_portail(x)
                                  for x in statuts}
                        n_stat.discard("")
                        n_comb = {plaque_depuis_libelle_portail(x)
                                  for x in combo}
                        n_comb.discard("")
                        self.recensement = sorted(n_stat | n_comb)
                        log.info("CamtrackPro — recensement D4 : Statuts %d ∪ "
                                 "combo %d → %d véhicule(s) publié(s)",
                                 len(n_stat), len(n_comb),
                                 len(self.recensement))
                        if len(n_stat) - len(n_comb) > 0:
                            log.info("CamtrackPro — recensement D4 : la page "
                                     "Statuts publie %d véhicule(s) de plus "
                                     "que le filtre du rapport — union "
                                     "retenue", len(n_stat) - len(n_comb))
                        for frag in fragments:
                            try:
                                lignes, choisi = ctp.executer_pour(frag)
                            except Exception:
                                log.exception("CTPRO rapport « %s » : échec", frag)
                                lignes, choisi = [], frag
                            # la cellule véhicule est vide dans le détail →
                            # l'objet choisi (normalisé « 4296TCC ») fait foi
                            plaque = ident_vehicule(choisi) or choisi or frag
                            total.extend(
                                self._trajets_depuis_lignes(lignes, plaque))
                            time.sleep(pause)
                    break
                except Exception:
                    log.exception("CamtrackPro Trajets : tentative %s/3 échouée",
                                  tentative + 1)
        log.info("CamtrackPro (rapport trajets) : %d trajets clôturés sur "
                 "%d véhicule(s)", len(total), len(fragments))
        return total


# ------------------------------------------------------------------ registres
class MZoneXApiCollector(CollectorBase):
    """§0sexies A1/A2 (arbitrage LSS 20/08/2026) — NIVEAU 1 via l'API OData
    MZoneX (PRINCIPAL) : fenêtre incrémentale d'événements à cadence 60 s.

    Fenêtre glissante : reprise du dernier événement connu (base) − 3 min
    de chevauchement → maintenant − 20 s (décalage de publication boîtiers),
    plafonnée à 30 min à la première passe (§10 — l'anti-rejeu existant
    dédoublonne le chevauchement). Le repli écran est assuré PAR LA BOUCLE
    (_collecter_mzonex_n1_avec_repli), pas ici : toute exception remonte.
    """

    source = SourceEvenement.MZONEX

    def __init__(self, api: ApiMZoneX | None = None):
        self.api = api or ApiMZoneX()

    def collecter(self) -> list[dict]:
        db = SessionLocal()
        try:
            derniere = db.scalar(select(func.max(EvenementGPS.horodatage)).where(
                EvenementGPS.source == SourceEvenement.MZONEX))
        finally:
            db.close()
        maintenant = now_local()
        debut_utc, fin_utc = self.api.fenetre_incrementale(derniere, maintenant)
        lignes = self.api.evenements(debut_utc, fin_utc)
        log.info("MZoneX API (Événements) : %d événement(s), fenêtre %s → %s UTC",
                 len(lignes), debut_utc.strftime("%H:%M:%S"),
                 fin_utc.strftime("%H:%M:%S"))
        return lignes

    def normaliser(self, brut: list[dict]) -> list[dict]:
        points = [p for p in (point_depuis_evenement_api(v) for v in brut)
                  if p is not None]
        nq = len(brut) - len(points)
        if nq:
            log.info("MZoneX API (Événements) : %d ligne(s) sans plaque/GPS "
                     "exploitable (%d conservée(s))", nq, len(points))
        return super().normaliser(points)


class MZoneXTrajetsApiCollector:
    """§0sexies A2 — NIVEAU 2 « Trajets » via l'API OData MZoneX (PRINCIPAL).

    Remplace les 37 lectures du filtre « Rechercher véhicules » : UN appel
    Trips + UN appel Vehicles (recensement D4 infaillible, sans la
    virtualisation du portail corrigée en v1.24). Les items servis respectent
    le MÊME contrat que l'onglet (normaliser_valides / réconciliation)."""

    source = "MZONEX"

    def __init__(self, api: ApiMZoneX | None = None):
        self.api = api or ApiMZoneX()
        self.recensement: list = []

    def collecter_valides(self, jours: list | None = None) -> list[dict]:
        """§0nonies decies M1 (arbitrage LSS du 29/08/2026) — RELECTURE des
        jours passés : `jours` = liste de dates à relire (défaut : aujourd'hui
        seul). UN appel API par jour, upsert idempotent en aval."""
        if not jours:
            jours = [now_local().date()]
        self.recensement = self.api.recenser_flotte()
        items: list[dict] = []
        for jour in sorted(set(jours)):
            bruts = self.api.trajets_jour_local(jour)
            items.extend(it for it in (trajet_depuis_api(t) for t in bruts)
                         if it)
            log.info("MZoneX API (Trajets) : %d ligne(s) API du %s",
                     len(bruts), jour.isoformat())
        log.info("MZoneX API (Trajets) : %d jour(s) relu(s) → %d trajet(s) "
                 "officiel(s) ; recensement D4 : %d publié(s)",
                 len(set(jours)), len(items), len(self.recensement))
        return items


def _collecter_mzonex_n1_avec_repli(classe_ecran) -> int:
    """§0sexies A2 : API d'abord ; à TOUT échec, tentative lecteur d'écran sans blocage."""
    try:
        return MZoneXApiCollector().run()
    except Exception as e:
        log.warning("MZoneX API (Événements) indisponible (%s) — tentative repli écran", e)
        try:
            return classe_ecran().run()
        except Exception as e_scr:
            log.warning("MZoneX lecteur d'écran également indisponible (%s) — cycle reporté", e_scr)
            return 0


def _collecter_n2_mzonex(jours: list | None = None) -> tuple:
    """§0sexies A2 : Niveau 2 MZoneX — API d'abord, repli écran sur échec.
    §0nonies decies M1 : relit `jours` via l'API (le repli écran, jour courant
    seul, est historiquement inchangé)."""
    if _mzonex_api_active():
        try:
            c = MZoneXTrajetsApiCollector()
            return c.collecter_valides(jours), list(c.recensement or [])
        except Exception:
            log.exception("MZoneX API (Trajets) en échec — REPLI lecteur "
                          "d'écran (§0sexies A2) pour ce cycle (jour courant)")
    c = MZoneXTrajetsCollector()
    return c.collecter_valides(), list(getattr(c, "recensement", []) or [])


class CamtrackProApiCollector(CollectorBase):
    """§0sexies A4 (arbitrage LSS 20/08/2026) — NIVEAU 1 CamtrackPro via
    l'API Wialon : dernier message de chacune des 19 unités à cadence 60 s.

    Borne historique §5 (« pas de temps réel fiable côté Camtrack ») LEVÉE
    par l'arbitrage A4 : le dernier message horodaté à la seconde est un flux
    temps réel fiable. Incrémentalité native : l'anti-rejeu (véhicule, ts)
    ne laisse passer que les NOUVEAUX messages. Sans repli écran possible
    (§5) : un échec ne fait que reporter d'un cycle (collecte suivante).
    """

    source = SourceEvenement.CAMTRACKPRO

    def __init__(self, api: ApiWialon | None = None):
        self.api = api or ApiWialon()

    def collecter(self) -> list[dict]:
        try:
            return self.api.unites()
        finally:
            self.api.fermer()

    def normaliser(self, brut: list[dict]) -> list[dict]:
        points = [p for p in (point_depuis_position_wialon(u) for u in brut)
                  if p is not None]
        log.info("CamtrackPro API (positions) : %d/%d unité(s) avec position",
                 len(points), len(brut))
        return super().normaliser(points)


class CamtrackProTrajetsApiCollector:
    """§0sexies A4 — NIVEAU 2 CamtrackPro via l'API Wialon (rapport « Detail
    Trajet Vehicule », mêmes valeurs officielles que l'écran)."""

    source = "CAMTRACKPRO"

    def __init__(self, api: ApiWialon | None = None):
        self.api = api or ApiWialon()
        self.recensement: list = []

    def collecter_valides(self, jours: list | None = None) -> list[dict]:
        """§0nonies decies M1 (29/08/2026) — RELECTURE : `jours` = dates à
        relire (défaut : aujourd'hui) ; pour un jour PASSÉ, borne de fin =
        23:59:59 (relecture complète, supportée nativement par l'API, AM-4)."""
        if not jours:
            jours = [now_local().date()]     # jour civil local, comme le portail
        jours = sorted(set(jours))
        aujour = now_local().date()
        items: list[dict] = []
        try:
            self.recensement = self.api.recenser_flotte()
            for jour in jours:
                fin_loc = (datetime.combine(jour, datetime.max.time()
                                            .replace(microsecond=0))
                           if jour < aujour else None)
                items.extend(self.api.trajets_du_jour(jour,
                                                      fin_locale=fin_loc))
        finally:
            self.api.fermer()
        log.info("CamtrackPro API (trajets) : %d jour(s) relu(s) → %d "
                 "trajet(s) officiel(s) ; recensement D4 : %d publié(s)",
                 len(jours), len(items), len(self.recensement))
        return items


def _collecter_camtrackpro_n1() -> int:
    """§0sexies A4 : N1 CamtrackPro (API seul — pas de flux écran fiable, §5).
    Un échec reporte au cycle suivant (comportement antérieur : aucun N1)."""
    try:
        return CamtrackProApiCollector().run()
    except Exception:
        log.exception("CamtrackPro API (positions) en échec — cycle reporté "
                      "(§5, aucun flux écran de secours)")
        return 0


def _collecter_n2_camtrackpro(jours: list | None = None) -> tuple:
    """§0sexies A2/A4 : Niveau 2 CamtrackPro — API d'abord, écran en secours.
    §0nonies decies M1 : relit `jours` via l'API (repli écran = jour courant)."""
    if jeton_configure():
        try:
            c = CamtrackProTrajetsApiCollector()
            return c.collecter_valides(jours), list(c.recensement or [])
        except Exception:
            log.exception("CamtrackPro API (rapport trajets) en échec — "
                          "REPLI lecteur d'écran (§0sexies A2) pour ce cycle "
                          "(jour courant)")
    c = CamtrackProTrajetsCollector()
    return c.collecter_valides(), list(getattr(c, "recensement", []) or [])


# §0nonies decies M1 (arbitrage LSS du 29/08/2026) — fenêtre de RELECTURE des
# jours passés : chaque cycle → J et J-1 ; toutes les heures → J-2 → J-7
# (même réflexe que Ym@ne J-8→J, déjà gravé §0quinquies decies). Paramétrable.
RELECTURE_TRAJETS_JOURS = _env_int("RELECTURE_TRAJETS_JOURS", 7)
RELECTURE_PROFONDE_PERIODE_S = _env_int("RELECTURE_PROFONDE_PERIODE_S", 3600)
_DERNIERE_PASSE_PROFONDE = 0.0


def _jours_a_relire(maintenant: datetime) -> list:
    """Dates à relire ce cycle : [J-1, J] ; passe PROFONDE au démarrage puis
    toutes les `RELECTURE_PROFONDE_PERIODE_S` secondes : J-7 → J (croissant)."""
    global _DERNIERE_PASSE_PROFONDE
    aujour = maintenant.date()
    jours = [aujour - timedelta(days=1), aujour]
    mono = time.monotonic()
    if (_DERNIERE_PASSE_PROFONDE == 0.0
            or mono - _DERNIERE_PASSE_PROFONDE >= RELECTURE_PROFONDE_PERIODE_S):
        _DERNIERE_PASSE_PROFONDE = mono
        jours = [aujour - timedelta(days=k)
                 for k in range(RELECTURE_TRAJETS_JOURS, -1, -1)]
        log.info("Relecture PROFONDE Niveau 2 (%d jours : %s → %s)",
                 len(jours), jours[0].isoformat(), jours[-1].isoformat())
    return jours


SOURCES = {
    "MZONEX": MZoneXCollector,
    "CAMTRACKPRO": CamtrackProCollector,
    # « SIMULATEUR » est géré par simulator.py
}

# CamtrackPro n'a pas de source temps réel fiable (Addendum v1.4 §5) : il n'a
# donc pas de connecteur Niveau 1 actif — ses trajets entrent en VALIDÉ via
# le Niveau 2. En mode MIXTE, le Niveau 1 = MZoneX seul, le Niveau 2 = les deux.
SOURCES_NIVEAU1_MIXTE = ["MZONEX"]
SOURCES_NIVEAU2_MIXTE = ["MZONEX", "CAMTRACKPRO"]

VALIDATEURS_TRAJETS = {
    "MZONEX": MZoneXTrajetsCollector,
    "CAMTRACKPRO": CamtrackProTrajetsCollector,
}


def synchroniser_trajets_valides(source: str | None = None) -> dict:
    if not COLLECTE_LOCK.acquire(blocking=False):
        log.warning("Synchronisation Niveau 2 ignorée : une collecte est déjà en cours")
        return {"occupee": True}
    try:
        return _synchroniser_trajets_valides(source)
    finally:
        COLLECTE_LOCK.release()


def _synchroniser_trajets_valides(source: str | None = None) -> dict:
    """Addendum v1.4 §2.4 — Collecte l'onglet Trajets / rapport trajets de la
    plateforme `source` puis réconcilie (remplacement PROVISOIRE → VALIDÉ,
    recalcul TCC/TCJ/TTJ, propagation §9, audits §11).
    `COLLECTOR_SOURCE=MIXTE` → les DEUX validateurs à la suite (MZoneX puis
    CamtrackPro), totaux agrégés."""
    from .reconciliation import normaliser_valides, reconcilier_trajets_valides
    source = (source or os.getenv("COLLECTOR_SOURCE", "MZONEX")).upper()
    sources = SOURCES_NIVEAU2_MIXTE if source == "MIXTE" else [source]
    totaux = {"recus": 0, "remplaces": 0, "crees": 0, "maj": 0, "ouverts": 0,
              "clotures": 0, "divergences": 0, "ignores": 0, "erreurs": 0,
              "rejets": 0, "epures": 0, "corrections": 0, "geants": 0}
    # §0nonies decies M1 (29/08/2026) — relire J ET J-1 à chaque cycle,
    # J-2 → J-7 toutes les heures : tout tampon boîtier remonté tard au
    # portail est rattrapé SEUL (upsert idempotent, jamais de suppression).
    jours = _jours_a_relire(now_local())
    recensements: dict = {}              # §0quinquies D4 — listes portails
    for nom in sources:
        classe = VALIDATEURS_TRAJETS.get(nom)
        if classe is None:
            log.info("Pas de validateur Niveau 2 pour %s — sync ignorée", nom)
            continue
        try:
            if nom == "MZONEX":
                # §0sexies A2 — API MZoneX en principal, écran en secours
                bruts, rec = _collecter_n2_mzonex(jours)
                recensements[nom] = rec
            elif nom == "CAMTRACKPRO":
                # §0sexies A2/A4 — API Wialon en principal, écran en secours
                bruts, rec = _collecter_n2_camtrackpro(jours)
                recensements[nom] = rec
            else:
                collecteur = classe()
                bruts = collecteur.collecter_valides()
                recensements[nom] = list(getattr(collecteur, "recensement", [])
                                         or [])
            items = normaliser_valides(bruts)
        except Exception:
            log.exception("Échec collecte Niveau 2 (%s)", nom)
            continue
        db = SessionLocal()
        try:
            stats = reconcilier_trajets_valides(
                db, items, username=f"collecteur-{nom.lower()}")
        finally:
            db.close()
        for cle in totaux:
            totaux[cle] += int(stats.get(cle, 0) or 0)
        log.info("Sync Niveau 2 (%s) : %s", nom, stats)
    if source == "MIXTE":
        log.info("Sync Niveau 2 (MIXTE) — total : %s", totaux)
    # §0quinquies decies I1/I5 (25/08/2026) — collecte Ym@ne adossée au cycle
    # N2 (15 min) : infractions pré-filtrées ALERTE/ALARME seulement, upsert
    # idempotent, jamais de suppression. ACTIF depuis la v1.36 (YMANE_ACTIVE=1)
    # ; §0septies decies K1 (27/08/2026) : tout échec devient une alerte
    # visible (anti-spam, auto-refermée à la guérison) — plus jamais muet.
    try:
        from .ymane_import import cycle_ymane_avec_alerte
        stats_y = cycle_ymane_avec_alerte()
        if stats_y.get("actif"):
            if stats_y.get("ok"):
                log.info("Collecte Ym@ne (§0quinquies decies) : %s", stats_y)
            elif stats_y.get("conflit"):
                log.info("Collecte Ym@ne : %s — cycle planifié ignoré",
                         stats_y.get("raison"))
            else:
                log.warning("Collecte Ym@ne en échec (alerte K1 signalée) : %s",
                            stats_y.get("raison"))
    except Exception:
        log.exception("Collecte Ym@ne en échec — la synchronisation des "
                      "trajets, elle, est enregistrée")
    # §0quinquies D4 (arbitrage 14/08/2026) — recensement des véhicules tels
    # que les portails les PUBLIENT : création des inconnus avec la plateforme
    # d'origine (D1), bascule automatique de plateforme au vu du portail (D4),
    # signalement des véhicules actifs vus nulle part. Jamais de suppression.
    if any(recensements.values()):
        from .engine import recenser_flotte
        db = SessionLocal()
        try:
            recenser_flotte(db, recensements, username="collecteur")
            db.commit()
        except Exception:
            db.rollback()
            log.exception("Recensement D4 en échec — la synchronisation des "
                          "trajets, elle, est enregistrée")
        finally:
            db.close()
    # §0nonies decies M4 (29/08/2026) — repère « boîtier muet » : audit unique
    # par camion et par jour (jamais de spam) quand un boîtier actif se tait.
    try:
        from .engine import auditer_boitiers_muets
        db = SessionLocal()
        try:
            muets = auditer_boitiers_muets(db)
        finally:
            db.close()
        if muets:
            log.info("Repère « boîtier muet » (§0nonies decies M4) : %d "
                     "nouvel(le)(s) audit(s)", muets)
    except Exception:
        log.exception("Audit boîtiers muets en échec — cycle reporté")
    # §0vicies decies N3 (31/08/2026) — rattrapage des relevés 18h/20h/22h VIDES
    # des jours passés (J-1 → J-7) : cellules remplies jamais écrasées, audit
    # `suivi.position_rattrapee` ; archives régénérées via le pipeline M1.
    try:
        from .engine import rattraper_positions_horaires
        from .models import SuiviJournalier as _SJN3
        from .reconciliation import _synchroniser_archive as _sync_arch_n3
        db = SessionLocal()
        try:
            ids_modif = rattraper_positions_horaires(db)
            if ids_modif:
                for sid in ids_modif:
                    suivi = db.get(_SJN3, sid)
                    if suivi is not None:
                        _sync_arch_n3(db, suivi)
                db.commit()
        finally:
            db.close()
        if ids_modif:
            log.info("Positions rattrapées (§0vicies decies N3) : %d ligne(s) "
                     "complétée(s) sur les jours passés", len(ids_modif))
    except Exception:
        log.exception("Rattrapage positions jours passés (N3) — échec, cycle "
                      "reporté")
    # §0unvicies decies O1/O2 (arbitrage LSS du 01/09/2026, mandants) —
    # positions du soir EXACTES depuis l'historique des portails : J-1 à
    # chaque cycle (≤ 1×/30 min), réparation des signatures identiques
    # J-1 → J-7 (≤ 1×/h, idempotent) ; archives régénérées via le pipeline M1.
    try:
        from .config import now_local as _now_o
        from .engine import (integrer_positions_portails,
                             integration_throttle_ok,
                             reparer_positions_horaires)
        from .models import SuiviJournalier as _SJO
        from .reconciliation import _synchroniser_archive as _sync_arch_o
        def _sync_ids(ids: list[str]) -> int:
            """Régénère les archives des suivis modifiés (pipeline M1) et
            COMMIT tout de suite — appelée par journée, jamais en fin de
            balayage entier (un arrêt en cours de route ne laisse aucune
            archive en retard sur un suivi réparé)."""
            if not ids:
                return 0
            for sid in dict.fromkeys(ids):
                suivi = db.get(_SJO, sid)
                if suivi is not None:
                    _sync_arch_o(db, suivi)
            db.commit()
            return len(ids)

        def _apres_jour(jour, lot):
            """Par journée : archives des lignes réparées + auto-guérison des
            archives en retard sur le suivi (§0unvicies decies O2 addendum,
            pur local — un arrêt en plein balayage ne laisse JAMAIS une
            archive désynchronisée derrière un suivi corrigé)."""
            from .engine import aligner_archives_positions
            _sync_ids(lot)
            retards = aligner_archives_positions(db, jour)
            if retards:
                log.info("Auto-guérison archives (O2 addendum) %s : %d "
                         "ligne(s) resynchronisée(s)", jour.isoformat(),
                         len(retards))

        ids_o: list[str] = []
        db = SessionLocal()
        try:
            hier = _now_o().date() - timedelta(days=1)
            if integration_throttle_ok("integration.J-1." + hier.isoformat(),
                                       1800):
                ids_o.extend(integrer_positions_portails(db, hier))
                _apres_jour(hier, ids_o)   # J-1 : sync + auto-guérison archive
            ids_o.extend(reparer_positions_horaires(db, apres_jour=_apres_jour))
        finally:
            db.close()
        if ids_o:
            log.info("Positions via portails (§0unvicies decies O1/O2) : %d "
                     "ligne(s) corrigée(s)/complétée(s)", len(set(ids_o)))
    except Exception:
        log.exception("Intégration positions portails (O1/O2) — échec, cycle "
                      "reporté")
    return totaux


# ═══════════ Correctif v1.46 — RELECTURE N1 (Événements MZoneX) ═══════════
# Constats du 04/09/2026 : (a) le plafond de pagination tronquait silencieusement
# des fenêtres larges ; (b) toute panne de collecte > 3 h était PERDUE à jamais
# (fenêtre incrémentale FENETRE_MAX_S) ; (c) la relecture M1 ne couvre que les
# TRAJETS (N2), jamais les ÉVÉNEMENTS (N1). Or le fil « Events » de l'API OData
# est rejouable à la demande, et l'anti-rejeu de CollectorBase.inserer rend le
# rejeu IDOMPOTENT (zéro doublon). On rejoue donc les J derniers jours en
# tranches horaires (le découpage v1.46 d'evenements() garantit < 9 000/tranche).
RELECTURE_N1_ACTIVE = os.getenv("RELECTURE_N1_ACTIVE", "1") == "1"
RELECTURE_N1_JOURS = int(os.getenv("RELECTURE_N1_JOURS", "7"))
RELECTURE_N1_PERIODE_S = int(os.getenv("RELECTURE_N1_PERIODE_S", "3600"))
_relecture_n1_memo: dict = {"mono": 0.0}


def _fenetres_manquantes_mzonex(db, jours: int, maintenant: datetime) -> list[tuple]:
    """Détecte les TROUS de la base MZoneX : fenêtres locales [debut, fin]
    (bornées 04h00 → 23h30, hors nuit — le serveur est éteint le soir, §8)
    sans aucun événement pendant > FENETRE_MAX_S. Ciblé : on ne relit
    QUE ce qui manque, jamais des journées complètes déjà en base (leçon du
    04/09 : la relecture intégrale de 8 jours ~200 000 points sature le GIL
    et fige le serveur). Une journée ENTIÈREMENT vide (ex. 30/08/2026) donne
    une seule fenêtre couvrant le jour → rattrapée elle aussi."""
    from .api_mzonex import FENETRE_MAX_S
    fenetres: list[tuple[datetime, datetime]] = []
    for k in range(jours, -1, -1):
        jour = maintenant.date() - timedelta(days=k)
        debut_j = max(datetime.combine(jour, datetime.min.time()).replace(hour=4),
                      datetime.combine(jour, datetime.min.time()))
        fin_j = min(datetime.combine(jour, datetime.min.time()).replace(hour=23, minute=30),
                    maintenant)
        if fin_j <= debut_j:
            continue
        hords = db.scalars(select(EvenementGPS.horodatage).where(
            EvenementGPS.source == SourceEvenement.MZONEX,
            EvenementGPS.horodatage >= debut_j,
            EvenementGPS.horodatage < fin_j).order_by(
                EvenementGPS.horodatage)).all()
        prev = debut_j
        for h in hords:
            if (h - prev).total_seconds() > FENETRE_MAX_S:
                fenetres.append((prev, h))
            prev = max(prev, h)
        # trou de queue : jour passé clos à 23h30 → la fenêtre 23h30→minuit
        # n'est pas chassée ; jour courant → rattrape jusqu'à maintenant
        if (fin_j - prev).total_seconds() > FENETRE_MAX_S:
            fin_t = fin_j if k == 0 else min(fin_j, prev + timedelta(days=1))
            fenetres.append((prev, fin_t))
    return fenetres


def relecture_n1_mzonex(jours: int | None = None) -> int:
    """Rattrape les TROUS de la base en rejouant les Événements MZoneX des
    fenêtres manquantes uniquement (J-7 → J, découpage horaire v1.46) et les
    insère par le MÊME pipeline. Idempotent (anti-rejeu : zéro doublon).
    Retourne le nombre de points insérés. Jamais d'exception propagée (§10)."""
    if not RELECTURE_N1_ACTIVE or not _mzonex_api_active():
        return 0
    jours = RELECTURE_N1_JOURS if jours is None else max(0, jours)
    maintenant = now_local()
    db = SessionLocal()
    try:
        fenetres = _fenetres_manquantes_mzonex(db, jours, maintenant)
    finally:
        db.close()
    if not fenetres:
        return 0
    coll = MZoneXApiCollector()
    total = 0
    try:
        for debut_local, fin_local in fenetres:
            brut = coll.api.evenements(coll.api._utc_naive(debut_local),
                                       coll.api._utc_naive(fin_local))
            points = coll.normaliser(brut)
            n = 0
            if points:
                n = coll.inserer(points)
                total += n
            log.info("Relecture N1 MZoneX %s → %s : %d événement(s) lu(s), "
                     "%d point(s) inséré(s)", debut_local.strftime("%m-%d %H:%M"),
                     fin_local.strftime("%H:%M"), len(brut), n)
            time.sleep(0.5)   # laisser respirer la boucle d'événements (GIL)
    except Exception:
        log.exception("Relecture N1 MZoneX en échec (retraitée au prochain "
                      "passage)")
    return total


def boucle_collecte():
    """Collecte planifiée Niveau 1 en continu (période COLLECTOR_PERIODE_S, §10).
    `COLLECTOR_SOURCE=MIXTE` → Niveau 1 MZoneX (CamtrackPro = VALIDÉ direct,
    borne §5 : pas de flux temps réel fiable côté Camtrack)."""
    source = os.getenv("COLLECTOR_SOURCE", "SIMULATEUR").upper()
    periode = _env_int("COLLECTOR_PERIODE_S", 10)
    noms = SOURCES_NIVEAU1_MIXTE if source == "MIXTE" else [source]
    classes = [(nom, SOURCES.get(nom)) for nom in noms]
    classes = [(nom, c) for nom, c in classes if c is not None]
    if not classes:
        log.info("Collecteur %s non configuré — collecte désactivée", source)
        return
    if source == "MIXTE":
        if jeton_configure():
            # §0sexies A2/A4 : CamtrackPro rejoint le Niveau 1 via l'API Wialon
            log.info("Mode MIXTE : Niveau 1 = %s + CAMTRACKPRO (API Wialon, "
                     "§0sexies A2/A4) — lecteurs d'écran en secours",
                     ", ".join(nom for nom, _ in classes))
        else:
            log.info("Mode MIXTE : Niveau 1 = %s (CamtrackPro via Niveau 2 "
                     "— jeton API absent, §5)",
                     ", ".join(nom for nom, _ in classes))
    log.info("Boucle de collecte %s démarrée (toutes les %ds)", source, periode)
    # §0septies B4 — géozones des deux portails (gate « en zone / hors zone »
    # de l'alerte vitesse en direct) : chargée au démarrage, auto-rechargée
    # toutes les 6 h par le cache interne — jamais d'exception ici (§10)
    try:
        n_zones = charger_zones()
        log.info("Géozones prêtes pour l'alerte vitesse : %d zone(s) "
                 "(§0septies B4)", n_zones)
    except Exception:
        log.exception("Chargement initial des géozones en échec — retraité "
                      "par le cache (choix « hors zone » inscrit, loi B4)")
    while True:
        try:
            charger_zones()          # rechargement périodique (cache 6 h)
        except Exception:
            pass
        for nom, classe in classes:
            n = _collecte_protegee(
                nom,
                (lambda c=classe: _collecter_mzonex_n1_avec_repli(c)
                 if nom == "MZONEX" and _mzonex_api_active()
                 else c().run()))
            log.info("Collecte %s : %d points insérés", nom, n)
        # §0sexies A4 (arbitrage 20/08/2026) — N1 CamtrackPro via l'API Wialon
        # à la même cadence (dernier message par unité ; échec → cycle reporté,
        # aucun flux écran fiable §5)
        if source != "CAMTRACKPRO" and jeton_configure():
            n_ctp = _collecte_protegee("CAMTRACKPRO", _collecter_camtrackpro_n1)
            if n_ctp:
                log.info("Collecte CAMTRACKPRO (API) : %d points insérés",
                         n_ctp)
        # Correctif v1.46 — relecture N1 (Événements MZoneX) : au démarrage puis
        # toutes les RELECTURE_N1_PERIODE_S (défaut 1 h) — rattrape les trous
        # > 3 h laissés par une panne de collecte (idempotent, anti-rejeu).
        mono_n1 = time.monotonic()
        if (_relecture_n1_memo["mono"] == 0.0
                or mono_n1 - _relecture_n1_memo["mono"] >= RELECTURE_N1_PERIODE_S):
            _relecture_n1_memo["mono"] = mono_n1
            try:
                n_n1 = _collecte_protegee("MZONEX_RELECTURE", relecture_n1_mzonex)
                if n_n1:
                    log.info("Relecture N1 (Événements MZoneX, %d jours) : "
                             "%d point(s) rattrapé(s)", RELECTURE_N1_JOURS, n_n1)
            except Exception:
                log.exception("Relecture N1 MZoneX — échec (retraité)")
        # §0quater R2 (arbitrage 14/08/2026) — jamais un camion en route sans
        # ligne : (ré)ouverture des lignes manquantes d'après le dernier signal
        try:
            from .engine import rattraper_ouvertures
            r2 = rattraper_ouvertures()
            if r2.get("reouvertes") or r2.get("creees"):
                log.info("Rattrapage ouvertures (§0quater R2) : %s", r2)
        except Exception:
            log.exception("Rattrapage ouvertures (§0quater R2) — échec")
        # §0vicies decies N2 (31/08/2026) — relevés automatiques 18h/20h/22h
        # (dernière position connue à l'heure dite ; réécriture si de meilleures
        # données arrivent, jusqu'au verrouillage de minuit)
        try:
            from .engine import auto_positions_horaires
            db = SessionLocal()
            try:
                n_pos = auto_positions_horaires(db)
            finally:
                db.close()
            if n_pos:
                log.info("Positions auto (§0vicies decies N2) : %d cellule(s) "
                         "18h/20h/22h mises à jour", n_pos)
        except Exception:
            log.exception("Positions auto (§0vicies decies N2) — échec, cycle "
                          "reporté")
        # §0unvicies decies O1 (01/09/2026) — passe du SOIR (≥ 22h05, ≤ 1×/15
        # min) : version PORTAIL définitive des relevés 18h/20h/22h du jour
        # courant — couvre les boîtiers muets qui remontent leur tampon tard
        # (réécriture libre jusqu'au verrouillage de minuit, N2 v1.44 inchangé)
        try:
            from .config import now_local as _now_s
            from .engine import (integrer_positions_portails,
                                 integration_throttle_ok)
            mnt = _now_s()
            if ((mnt.hour, mnt.minute) >= (22, 5)
                    and integration_throttle_ok(
                        "integration.soir." + mnt.date().isoformat(), 900)):
                db = SessionLocal()
                try:
                    ids_soir = integrer_positions_portails(db, mnt.date(),
                                                           maintenant=mnt)
                finally:
                    db.close()
                if ids_soir:
                    log.info("Positions du soir via portails (§0unvicies "
                             "decies O1) : %d ligne(s) actualisée(s)",
                             len(ids_soir))
        except Exception:
            log.exception("Passe du soir positions portails — échec, cycle "
                          "reporté")
        time.sleep(periode)


if __name__ == "__main__":
    # Mode TEST opérateur : vérifie identifiants + sélecteurs SANS écrire en base.
    #   python -m app.scrapers MZONEX              → Niveau 1 (onglet Événements)
    #   python -m app.scrapers MZONEX --trajets    → Niveau 2 (onglet Trajets)
    #   … --insert                               → écriture réelle (moteur §7 /
    #                                              réconciliation §2.4)
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    source = (sys.argv[1] if len(sys.argv) > 1 else "MZONEX").upper()
    ecrire = "--insert" in sys.argv
    mode_trajets = "--trajets" in sys.argv

    if mode_trajets:
        classe_t = VALIDATEURS_TRAJETS.get(source)
        if classe_t is None:
            print(f"Pas de connecteur Trajets pour {source}")
            raise SystemExit(2)
        try:
            valides = classe_t().collecter_valides()
        except ImportError as e:
            print(f"⚠ Dépendance manquante : {e}\n"
                  f"  MZoneX       → python -m pip install playwright && python -m playwright install chromium\n"
                  f"  CamtrackPro  → python -m pip install selenium webdriver-manager")
            raise SystemExit(3)
        print(f"\n=== {source} · Niveau 2 : {len(valides)} trajet(s) clôturé(s) lu(s) ===")
        for it in valides[:30]:
            print(" •", json.dumps({k: str(v) for k, v in it.items()}, ensure_ascii=False))
        if not valides:
            print("\n⚠ Aucun trajet : onglet vide, ou sélecteurs à calibrer "
                  f"({source}_TRA_COL_*, {source}_GROUPE…).")
        if ecrire and valides:
            # v1.16 — réconcilier DIRECTEMENT les lignes déjà collectées :
            # synchroniser_trajets_valides() relancerait une SECONDE collecte
            # Playwright complète (~5 min, 38 véhicules) — doublon inutile.
            from .database import SessionLocal as _SL
            from .reconciliation import (
                normaliser_valides as _nv, reconcilier_trajets_valides as _rv)
            db = _SL()
            try:
                stats = _rv(db, _nv([dict(it) for it in valides]),
                            username=f"collecteur-{source.lower()}")
            finally:
                db.close()
            print(f"\nRéconciliation §2.4 : {stats}")
        elif valides:
            print("\nMode TEST — aucune écriture. Relancer avec --trajets --insert.")
        raise SystemExit(0)

    classe = SOURCES.get(source)
    if classe is None:
        print(f"Source inconnue « {source} » — choix : {', '.join(SOURCES)}")
        raise SystemExit(2)
    collecteur = classe()
    try:
        bruts = collecteur.collecter()
    except ImportError as e:
        print(f"⚠ Dépendance manquante : {e}\n"
              f"  MZoneX       → python -m pip install playwright && python -m playwright install chromium\n"
              f"  CamtrackPro  → python -m pip install selenium webdriver-manager")
        raise SystemExit(3)
    propres = collecteur.normaliser(bruts)
    print(f"\n=== {source} · Niveau 1 : {len(propres)} point(s) normalisé(s) "
          f"({len(bruts)} brut(s) lu(s)) ===")
    for p in propres[:20]:
        print(" •", json.dumps({k: str(v) for k, v in p.items()}, ensure_ascii=False))
    if not propres:
        print("\n⚠ Aucun point : vérifier URL/identifiants, puis les sélecteurs "
              f"({source}_SEL_*, {source}_GROUPE).")
    if ecrire and propres:
        n = collecteur.inserer(propres)
        print(f"\n{n} point(s) inséré(s) en base via ingest_event (moteur §7).")
    elif propres:
        print("\nMode TEST — aucune écriture. Relancer avec --insert pour insérer.")
