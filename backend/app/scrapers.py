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
from sqlalchemy.exc import IntegrityError, OperationalError

from .concurrence import (BudgetDepasse, PasseCourante, Possession,
                          VerrouOccupe, activer_passe, chrono, classer_erreur,
                          compter, enregistrer_passe, etat_famille,
                          forcer_famille, metriques_publiees, passe_courante, passe_de,
                          passes_en_cours, publier_metriques, retirer_passe,
                          verifier_etape, verrou_de)
from .config import (TZ, normaliser_ident, now_local,
                     plaque_depuis_libelle_portail)
from .database import SessionLocal
from .engine import (cle_idempotence_evenement, creer_conducteur_auto,
                     creer_vehicule_auto, ingest_event, verifier_alertes_conduite)
from .geozones import charger_zones, en_geozone
from .models import (CollecteCheckpoint, EvenementGPS, SourceEvenement,
                      TypeEvenement, Vehicule)
from .api_mzonex import (FENETRE_MAX_S, TRANCHE_S, ApiMZoneX,
                         depuis_utc, point_depuis_evenement_api,
                         trajet_depuis_api)
from .api_wialon import (ApiWialon, jeton_configure,
                         point_depuis_position_wialon)

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

log = logging.getLogger("lss.scraper")

_COLLECTE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lss-collector")

# ==============================================================================
# SÉPARATION STRICTE DES VERROUS : NIVEAU 1 (GPS LIVE) vs NIVEAU 2 (TRAITEMENT)
# L'ingestion temps réel N1 ne doit JAMAIS être bloquée par un calcul / réconciliation N2.
# ==============================================================================

# ══════════════════════════════════════════════════════════════════════════
# v1.54 (21/09/2026) — VERROUS À JETON DE POSSESSION, UN PAR SOURCE.
#
# Avant : UN verrou global réassignable. Une passe périmée continuait de tenir
# « son » objet et, à sa sortie, libérait le verrou de la passe SUIVANTE
# (défaut reproduit le 21/09 : exclusion mutuelle perdue, deux écritures en
# même temps). Désormais : `concurrence.VerrouPossede` par ressource —
# l'acquisition rend un JETON, la libération le vérifie, l'expiration est
# explicite et nominative.
#
# MZONEX (temps réel), CAMTRACKPRO et MZONEX_RELECTURE ont chacun LEUR verrou :
# la relecture historique (7 jours) ne peut plus affamer le temps réel.
# ══════════════════════════════════════════════════════════════════════════
LOCK_N1_TIMEOUT_S = float(os.getenv("COLLECTE_N1_LOCK_TIMEOUT_S", "30.0"))
LOCK_N2_TIMEOUT_S = float(os.getenv("TRAITEMENT_N2_LOCK_TIMEOUT_S", "120.0"))


class _VerrouCompatN1:
    """Rétrocompatibilité de LECTURE : `VERROU_COLLECTE_N1.locked()` répond
    « au moins un verrou de la famille est détenu ». Aucune acquisition ne
    passe par cet objet (elles passent toutes par un jeton)."""

    def locked(self) -> bool:
        return etat_famille(n2=False)["occupe"]


VERROU_COLLECTE_N1 = _VerrouCompatN1()
VERROU_TRAITEMENT_N2 = _VerrouCompatN1()
COLLECTE_LOCK = VERROU_COLLECTE_N1
LOCK_TIMEOUT_S = LOCK_N1_TIMEOUT_S

_ETAT_COLLECTE_LOCK = threading.Lock()
_ETAT_COLLECTE = {
    "orchestrateur": "ASYNCIO",
    "pid": os.getpid(),
    "dernier_cycle_debut": None,
    "dernier_cycle_fin": None,
    "derniere_erreur": None,
    "sources": {},
}


def forcer_deverrouillage_n1(raison: str = "manuel") -> bool:
    """Expire EXPLICITEMENT les verrous N1 (jeton invalidé — jamais remplacé en
    silence). Utilisé au démarrage et par l'API de secours."""
    touche = forcer_famille(n2=False, raison=raison)
    if touche:
        log.warning("Verrous N1 expirés de force (raison: %s)", raison)
    return touche


def forcer_deverrouillage_n2(raison: str = "manuel") -> bool:
    """Expire EXPLICITEMENT les verrous N2 (recalculs / réconciliation)."""
    touche = forcer_famille(n2=True, raison=raison)
    if touche:
        log.warning("Verrous N2 expirés de force (raison: %s)", raison)
    return touche


def forcer_deverrouillage_collecte(raison: str = "manuel") -> bool:
    """Force la libération immédiate des verrous N1 et N2."""
    b1 = forcer_deverrouillage_n1(raison=raison)
    b2 = forcer_deverrouillage_n2(raison=raison)
    return b1 or b2


def _acquerir_verrou_n1(source: str, duree_s: float | None = None) -> Possession | None:
    """Acquiert le verrou DE CETTE SOURCE. Renvoie le JETON de possession
    (à conserver pour libérer) ou None si la ressource est déjà détenue.

    Aucune acquisition ne peut écraser la possession d'une autre tâche : si la
    possession est expirée, `VerrouPossede` la déclare EXPIRÉE (journal
    nominatif + compteur) et délivre un jeton d'une génération supérieure — le
    jeton de la tâche sortante ne pourra plus rien libérer.
    """
    duree = float(duree_s if duree_s is not None
                  else max(LOCK_N1_TIMEOUT_S, ATTENTE_SORTIE_PASSE_S + 10))
    return verrou_de(source, duree_defaut_s=duree).acquerir(source, duree_s=duree)


def _liberer_verrou_n1(possession: Possession | None) -> bool:
    """Libère le verrou N1 **de la possession fournie** — un jeton étranger est
    refusé (jamais la libération du verrou d'une autre tâche). À appeler dans un
    `finally`."""
    if possession is None:
        return False
    return verrou_de(possession.ressource).liberer(possession)


def _acquerir_verrou_n2(source: str, duree_s: float | None = None) -> Possession | None:
    """Acquiert le verrou N2 de cette source (mêmes garanties que N1)."""
    ressource = source if source.startswith("N2_") else f"N2_{source}"
    duree = float(duree_s if duree_s is not None else LOCK_N2_TIMEOUT_S)
    return verrou_de(ressource, duree_defaut_s=duree).acquerir(ressource, duree_s=duree)


def _liberer_verrou_n2(possession: Possession | None) -> bool:
    """Libère le verrou N2 **de la possession fournie** (jeton vérifié)."""
    if possession is None:
        return False
    return verrou_de(possession.ressource).liberer(possession)


# Alias de compatibilité
# v1.54 — les alias `_acquerir_verrou_collecte` / `_liberer_verrou_collecte`
# ont été SUPPRIMÉS : leur appel sans jeton (héritage d'avant le correctif)
# libérait un verrou sans preuve de propriété, exactement le défaut corrigé.


# ═══════════ v1.50 — VERROUS SQLITE : CAUSE LOCALE, PAS PANNE PORTAIL ═══════
# Constat du 18/09/2026 (/api/sante) : « MZONEX » annoncée EN PANNE alors que
# le portail répondait — la seule erreur était notre « database is locked ».
# Deux réglages rendent ce diagnostic impossible à confondre désormais.
LOT_INSERTION = int(os.getenv("COLLECTE_LOT_INSERTION", "250"))
# v1.50 — SOUFFLE ENTRE LES LOTS : SQLite n'a qu'UN écrivain à la fois et le
# nôtre reprenait le verrou aussitôt (mesuré : verrou détenu 94 % du temps,
# plages continues de 3,8 s). Ce court relâchement laisse passer les écritures
# que le reste de la plateforme attend (validations, PATCH, audits) : quelques
# millisecondes contre un ordre de grandeur de réactivité en moins pour elles.
SOUFFLE_INTER_LOTS_S = float(os.getenv("COLLECTE_SOUFFLE_S", "0.01"))
COMMIT_ESSAIS = int(os.getenv("COLLECTE_COMMIT_ESSAIS", "4"))
# v1.54 — au-delà de ce coût, la durée d'un commit est de l'ATTENTE du verrou
# d'écriture de SQLite (un autre écrivain tenait la base), pas du travail à
# nous : c'est cette part qui alimente `attente_sqlite_s` (distincte du temps
# passé chez le portail). Mesuré en local : commit normal < 30 ms.
SQLITE_COMMIT_BASE_S = float(os.getenv("COLLECTE_COMMIT_BASE_S", "0.1"))
ATTENTE_SORTIE_PASSE_S = float(os.getenv("COLLECTE_ATTENTE_SORTIE_S", "60"))
RELECTURE_N1_MAX_POINTS = int(os.getenv("RELECTURE_N1_MAX_POINTS", "4000"))
RELECTURE_N1_TIMEOUT_S = float(os.getenv("RELECTURE_N1_TIMEOUT_S", "180"))

MOTIFS_ERREUR_LOCALE = (
    "database is locked", "database table is locked", "sqlite_busy",
    "budget de collecte",
    "disk i/o error", "no space left", "readonly database", "read-only database",
    "attempt to write a readonly", "unable to open database",
)


def _erreur_locale(exc: Exception) -> bool:
    """Vrai si l'échec vient de NOTRE infrastructure (base verrouillée, disque,
    droits) et non du portail."""
    texte = f"{type(exc).__name__}: {exc}".lower()
    return any(motif in texte for motif in MOTIFS_ERREUR_LOCALE)


class DepassementBudgetCollecte(BudgetDepasse):
    """v1.50 — la passe a dépassé le budget de temps imparti par NOTRE
    planificateur : cause locale (notre orchestration), jamais une panne du
    portail. Avant, ce cas était rangé du côté « source en panne ».

    v1.54 : la classe conserve sa signature d'origine (« message » en premier
    argument) mais hérite de `concurrence.BudgetDepasse`, qui porte l'ÉTAPE
    consommatrice du temps — c'est elle qui permet au statut de distinguer
    « portail lent » de « attente SQLite » au lieu de tout ranger en « local ».
    """

    def __init__(self, message: str = "Budget de collecte dépassé",
                 etape: str = "inconnue", ecoule_s: float = 0.0,
                 budget_s: float = 0.0, source: str | None = None) -> None:
        super().__init__(etape=etape, ecoule_s=ecoule_s, budget_s=budget_s,
                         source=source)
        self.message_compat = message


def categorie_erreur(exc: Exception) -> str:
    """« locale » (base/disque — notre problème) ou « portail » (auth, HTTP,
    réseau — le leur). Sans cette distinction, un verrou SQLite s'affichait
    comme une panne de la source : le 18/09/2026 l'écran et /api/sante
    annonçaient « MZONEX en panne » alors que MZoneX répondait."""
    if isinstance(exc, BudgetDepasse):
        # Compatibilité v1.50 : au niveau « locale / portail » ce dépassement
        # reste rangé du côté de NOTRE planificateur (il a déclenché l'arrêt).
        # La distinction fine (portail lent / attente SQLite / verrou) est
        # portée par `concurrence.classer_erreur()` et publiée par /api/sante.
        return "locale"
    return "locale" if _erreur_locale(exc) else "portail"


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
             "derniere_erreur": None, "derniere_erreur_categorie": None})


def _etat_collecte_erreur(source: str, exc: Exception):
    """Enregistre l'échec AVEC sa classe fine (v1.54) : c'est cette classe qui
    alimente le statut de santé. `categorie` reste la valeur historique
    (« locale » / « portail ») pour ne rien casser des contrats existants."""
    classe = classer_erreur(exc)
    with _ETAT_COLLECTE_LOCK:
        erreur = f"{type(exc).__name__}: {exc}"
        _ETAT_COLLECTE["derniere_erreur"] = erreur
        _ETAT_COLLECTE["sources"].setdefault(source, {}).update(
            {"derniere_erreur": erreur,
             "derniere_erreur_categorie": categorie_erreur(exc),
             "derniere_erreur_classe": classe["classe"],
             "derniere_erreur_etape": classe.get("etape")})


def _noter_verrou_refuse(source: str) -> None:
    """Une passe N1 n'a pas pu démarrer : la ressource est détenue par une
    autre tâche. Événement INFORMATIF (distingué d'une panne) et compté."""
    with _ETAT_COLLECTE_LOCK:
        _ETAT_COLLECTE["sources"].setdefault(source, {}).update(
            {"dernier_verrou_refuse": now_local().isoformat()})
        refus = _ETAT_COLLECTE.setdefault("verrous_refuses", {})
        refus[source] = int(refus.get(source, 0)) + 1


def etat_collecte_memoire() -> dict:
    """État publié à `/api/sante`. v1.54 : chaque verrou est décrit par SA
    source (propriétaire, prise, dernière activité, expiration, compteurs
    d'expiration et de libérations refusées) — plus de propriétaire affiché
    qui ne correspond plus au détenteur réel. Les métriques par étape de la
    dernière passe de chaque source sont jointes."""
    verrous_n1 = etat_famille(n2=False)
    verrous_n2 = etat_famille(n2=True)
    with _ETAT_COLLECTE_LOCK:
        etat = {
            **_ETAT_COLLECTE,
            "sources": {k: dict(v) for k, v in _ETAT_COLLECTE["sources"].items()},
            "verrous_refuses": dict(_ETAT_COLLECTE.get("verrous_refuses") or {}),
        }
    return {
        **etat,
        "verrou_occupe": verrous_n1["occupe"],
        "verrou_acquis_par": verrous_n1["acquis_par"] or None,
        "verrou_duree_s": verrous_n1["duree_s"],
        "verrou_n1": verrous_n1,
        "verrou_n2": verrous_n2,
        "verrous_par_source": verrous_n1["par_source"],
        "collecte_en_cours": bool(verrous_n1["acquis_par"]),
        "metriques_collecte": metriques_publiees(),
    }


def sources_en_echec() -> list[str]:
    """v1.48 — noms des sources dont la DERNIÈRE tentative a échoué.

    `_etat_collecte_fin` remet `derniere_erreur` à None à chaque succès : la
    présence d'une erreur signifie donc « la dernière tentative a échoué ».
    Base commune de `/api/sante` (statut honnête) et de l'écran Suivi
    Journalier (badge « source en panne » au lieu de « données en transit »).
    """
    with _ETAT_COLLECTE_LOCK:
        sources = dict(_ETAT_COLLECTE.get("sources") or {})
    return sorted(nom for nom, etat in sources.items()
                  if isinstance(etat, dict) and etat.get("derniere_erreur"))


def sources_en_echec_detail() -> list[dict]:
    """v1.50 — la liste des sources en échec AVEC la cause : c'est ce qui
    permet de dire « source MZONEX en panne » (portail) ou « collecte bloquée
    localement — base verrouillée » (nous) au lieu de tout confondre."""
    with _ETAT_COLLECTE_LOCK:
        sources = dict(_ETAT_COLLECTE.get("sources") or {})
    def _detail(nom: str, etat: dict) -> dict:
        erreur = str(etat.get("derniere_erreur") or "")
        classe = etat.get("derniere_erreur_classe")
        etape = etat.get("derniere_erreur_etape")
        if not classe:      # état posé hors passe (ex. outil de contrôle)
            fine = classer_erreur(Exception(erreur))
            classe, etape = fine["classe"], fine.get("etape")
        return {"source": nom,
                "categorie": etat.get("derniere_erreur_categorie")
                or categorie_erreur(Exception(erreur)),
                "classe": classe,
                "etape": etape,
                "erreur": erreur[:300],
                "dernier_debut": etat.get("dernier_debut")}

    return sorted((_detail(nom, etat) for nom, etat in sources.items()
                   if isinstance(etat, dict) and etat.get("derniere_erreur")),
                  key=lambda d: d["source"])


def _executer_avec_passe(action, passe: PasseCourante):
    """Exécute la passe DANS SON FIL, avec son budget et ses métriques.

    Les métriques vivent dans un espace PAR FIL (`threading.local`) : deux
    passes concurrentes (temps réel + relecture) ne mélangent jamais leurs
    compteurs.
    """
    with activer_passe(passe):
        return action()


def _finaliser_passe(passe: PasseCourante, issue: str,
                     exc: Exception | None = None) -> dict:
    """Clôt la passe : état publié, métriques, clôture du cycle.

    `dernier_cycle_fin` est désormais écrit à CHAQUE fin de cycle (succès ou
    échec) : l'inversion « fin antérieure au début » qui déroutait l'exploitant
    ne signale plus qu'une chose : une passe terminée en échec
    (`derniere_issue_cycle` le dit explicitement). La DERNIÈRE RÉUSSITE reste
    publiée séparément (`derniere_reussite`).
    """
    depouillees = passe.terminer(issue)
    publier_metriques(depouillees)
    if exc is not None:
        _etat_collecte_erreur(passe.source, exc)
    with _ETAT_COLLECTE_LOCK:
        _ETAT_COLLECTE["dernier_cycle_fin"] = now_local().isoformat()
        _ETAT_COLLECTE["derniere_issue_cycle"] = issue
        _ETAT_COLLECTE["sources"].setdefault(passe.source, {}).update(
            {"derniere_issue": issue,
             "etape_bloquante": depouillees.get("etape_bloquante")})
    if issue != "TERMINE":
        log.warning("Collecte %s : cycle clôturé en %s — étape « %s », %.1fs "
                    "(budget %.1fs)", passe.source, issue,
                    depouillees.get("etape_bloquante"),
                    depouillees.get("total_s") or 0.0, passe.budget_s)
    return depouillees


def _collecte_protegee(source: str, action, timeout_s: float = 30.0) -> int:
    """Exécute une passe de source N1 sous LE VERROU DE CETTE SOURCE, avec un
    budget à points d'arrêt contrôlés.

    Garanties (v1.54) :
      - la possession est matérialisée par un JETON ; la libération le vérifie ;
      - une passe qui dépasse son budget s'arrête à un point d'arrêt prévu
        (avant une page, un véhicule, un lot d'écriture) : elle N'ÉCRIT PLUS
        après son échéance ;
      - la libération est TOUJOURS exécutée (`finally`), même en cas d'échec ;
      - chaque source a SON verrou (MZONEX / CAMTRACKPRO / MZONEX_RELECTURE).
    """
    t_attente = time.monotonic()
    possession = _acquerir_verrou_n1(
        source, duree_s=timeout_s + ATTENTE_SORTIE_PASSE_S + 10)
    attente_verrou_s = time.monotonic() - t_attente
    if possession is None:
        _noter_verrou_refuse(source)
        etat = verrou_de(source).etat()
        log.warning("Collecte N1 %s ignorée : verrou « %s » détenu par « %s » "
                    "depuis %.1fs (jeton %s) — aucune écriture de ma part",
                    source, source, etat.get("proprietaire"),
                    etat.get("duree_s") or 0.0, etat.get("jeton"))
        return 0

    passe = PasseCourante(source=source, budget_s=timeout_s)
    passe.metriques.attente_verrou_s = round(attente_verrou_s, 3)
    enregistrer_passe(source, passe)
    _etat_collecte_debut(source)
    try:
        fut = _COLLECTE_EXECUTOR.submit(_executer_avec_passe, action, passe)
        try:
            nombre = int(fut.result(timeout=timeout_s + ATTENTE_SORTIE_PASSE_S) or 0)
        except BudgetDepasse as exc:
            # ARRÊT CONTRÔLÉ : la passe a atteint son échéance à un point
            # d'arrêt prévu et s'est arrêtée elle-même.
            _finaliser_passe(passe, "BUDGET_DEPASSE", exc)
            log.warning("Collecte N1 %s arrêtée proprement : %s", source, exc)
            if source.startswith("MZONEX") and source != "MZONEX_RELECTURE":
                try:
                    _synchroniser_dernier_point_mzonex()
                except Exception:
                    pass
            return 0
        except FutureTimeoutError:
            # La passe n'a pas atteint de point d'arrêt (appel réseau bloqué) :
            # dernier recours, borné et explicite.
            limite = passe.metriques.etape_dominante()
            passe.annuler("echeance_sans_point_d_arret")
            _finaliser_passe(passe, "BUDGET_DEPASSE", BudgetDepasse(
                etape=limite, ecoule_s=passe.ecoule_s(), budget_s=timeout_s,
                source=source))
            try:
                fut.result(timeout=ATTENTE_SORTIE_PASSE_S)
                log.info("Collecte N1 %s : passe interrompue terminée proprement "
                         "(verrou rendu)", source)
            except Exception:
                log.warning("Collecte N1 %s : la passe interrompue n'a pas rendu "
                            "la main en %ss — elle reste surveillée (son jeton "
                            "reste la seule clé de son verrou)",
                            source, ATTENTE_SORTIE_PASSE_S)
            return 0
        except Exception as exc:
            _finaliser_passe(passe, "ECHEC", exc)
            log.exception("Échec collecte protégée N1 %s", source)
            return 0
        _etat_collecte_fin(source, nombre)
        passe.metriques.nb_points_ecrits = nombre
        _finaliser_passe(passe, "TERMINE")
        return nombre
    finally:
        retirer_passe(source, passe)
        # TOUJOURS libéré, et SEULEMENT si le jeton est bien le nôtre.
        if not _liberer_verrou_n1(possession):
            log.warning("Collecte N1 %s : verrou « %s » déjà expiré ou repris "
                        "(jeton %s, génération %d) — libération sans effet",
                        source, source, possession.jeton[:8],
                        possession.generation)


def _libelle_local(dt_utc_naif: datetime) -> str:
    """UTC naïf → libellé LOCAL lisible (heure d'Antananarivo) pour l'audit."""
    from datetime import timezone as _tz
    try:
        return dt_utc_naif.replace(tzinfo=_tz.utc).astimezone(TZ).isoformat()
    except Exception:
        return str(dt_utc_naif)


def _audit_collecte(action: str, details: dict) -> None:
    """v1.54 (18/09/2026) — règle 8 : la SOURCE et la PÉRIODE relue sont
    consignées dans la table d'audit, avec l'issue et le volume.

    Volontairement NON BLOQUANT (même philosophie que les checkpoints v1.50) :
    un verrou d'écriture ne doit jamais faire tomber la collecte. Réservé aux
    faits notables (rattrapage, échec) pour ne pas noyer la table d'audit.
    """
    try:
        from .database import SessionLocal
        from .models import AuditLog
        db = SessionLocal()
        try:
            db.add(AuditLog(username="collecte",
                            action=action,
                            entite="source",
                            entite_id=details.get("source"),
                            details=details))
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("Traçage d'audit de collecte en échec (collecte non bloquée)")


def _checkpoint_ouvre(source: str, debut: datetime, fin: datetime) -> str | None:
    """v1.50 — LE CHECKPOINT N'EST QU'UNE TRACE : son échec ne doit jamais
    faire tomber la collecte. Le 18/09/2026, un « database is locked » sur cet
    INSERT a avorté TOUTE la passe N1 MZoneX (le checkpoint est écrit AVANT
    l'appel au portail) : la source était déclarée en panne alors que seul un
    verrou d'écriture — chez nous — l'empêchait d'écrire sa ligne de journal."""
    db = None
    try:
        db = SessionLocal()
        cp = db.scalar(select(CollecteCheckpoint).where(
            CollecteCheckpoint.source == source,
            CollecteCheckpoint.fenetre_debut == debut,
            CollecteCheckpoint.fenetre_fin == fin))
        if cp is None:
            cp = CollecteCheckpoint(source=source, fenetre_debut=debut,
                                   fenetre_fin=fin, statut="EN_COURS",
                                   tentatives=1)
            db.add(cp)
        else:
            cp.statut = "EN_COURS"
            cp.tentatives = (cp.tentatives or 0) + 1
            cp.derniere_erreur = None
        db.commit()
        return cp.id
    except Exception as exc:
        log.warning("Checkpoint d'ouverture %s non écrit (%s) — la collecte "
                    "CONTINUE (une trace ne commande pas la collecte)",
                    source, f"{type(exc).__name__}: {exc}"[:120])
        return None
    finally:
        if db is not None:
            db.close()


def _checkpoint_ferme(checkpoint_id: str | None, statut: str,
                      erreur: str | None = None):
    if checkpoint_id is None:
        return
    db = None
    try:
        db = SessionLocal()
        cp = db.get(CollecteCheckpoint, checkpoint_id)
        if cp is not None:
            cp.statut = statut
            cp.derniere_erreur = erreur
            db.commit()
    except Exception as exc:
        log.warning("Checkpoint de fermeture non écrit (%s) — sans effet sur "
                    "la collecte", f"{type(exc).__name__}: {exc}"[:120])
    finally:
        if db is not None:
            db.close()


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

    def _mapper_vehicules(self, db) -> dict:
        """Plaque/gps_associe → véhicule (v1.50 : réutilisé après un rollback
        de lot, pour ne jamais rejouer sur des objets détachés)."""
        mapping: dict[str, Vehicule] = {}
        for v in db.scalars(select(Vehicule)).all():
            if v.gps_associe:
                mapping[str(v.gps_associe).strip().upper()] = v
            mapping[v.plaque.strip().upper()] = v
        return mapping

    @staticmethod
    def _sonder_attente_sqlite() -> float:
        """Mesure RÉELLE de l'attente du verrou d'écriture de SQLite (v1.54).

        On demande le verrou d'écriture pour de vrai (`BEGIN IMMEDIATE`) et on
        le rend aussitôt : la durée obtenue est le temps que la BASE nous a fait
        attendre — ni le portail, ni notre code. Sans cette sonde, l'attente se
        fondait dans la durée d'écriture et un portail lent était soupçonné à
        tort (constat du 21/09).

        Passe par une connexion BRUTE (`raw_connection`) pour ne pas perturber
        la transaction SQLAlchemy de l'appelant. Coût : quelques microsecondes
        quand personne n'écrit.
        """
        from .database import engine
        t0 = time.monotonic()
        try:
            cx = engine.raw_connection()
            try:
                cur = cx.cursor()
                cur.execute("BEGIN IMMEDIATE")
                cur.execute("COMMIT")
            finally:
                cx.close()
        except Exception as exc:                       # noqa: BLE001
            log.warning("Sonde du verrou d'écriture SQLite en échec (%s)",
                        str(exc)[:120])
        return time.monotonic() - t0

    def _inserer_tranche(self, db, tranche: list[dict],
                         mapping: dict, vus: dict, historique: bool) -> int:
        """Corps d'insertion d'UN LOT (le lot est committé par l'appelant)."""
        n = 0
        for p in tranche:
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
            if historique:
                cle = cle_idempotence_evenement(
                    vehicule.id, p["horodatage"], p["lat"], p["lng"], self.source)
                if db.scalar(select(EvenementGPS.id).where(
                        EvenementGPS.idempotence_key == cle)):
                    continue
                try:
                    with db.begin_nested():
                        db.add(EvenementGPS(
                            vehicule_id=vehicule.id, horodatage=p["horodatage"],
                            latitude=p["lat"], longitude=p["lng"],
                            adresse=p.get("adresse"), vitesse=p["vitesse"],
                            etat_moteur=p["moteur"],
                            type_evenement=p.get("type_evenement") or TypeEvenement.POSITION,
                            source=self.source, idempotence_key=cle,
                            received_at=now_local(), historique=True))
                        db.flush()
                        n += 1
                except IntegrityError:
                    pass
                continue
            try:
                ingest_event(db, vehicule, p["horodatage"], p["lat"], p["lng"],
                             p.get("adresse"), p["vitesse"], p["moteur"],
                             p.get("type_evenement"), self.source,
                             observation=bool(p.get("observation")))
                n += 1
            except IntegrityError:
                pass
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
            # NB : PAS de second `n += 1` ici — l'ancien double
            # comptage faisait renvoyer 2 pour un seul point inséré
            # (métriques de collecte fausses : « N points insérés »,
            # etat_collecte, diagnostics).
        # v1.17 — AUTO-RÉPARATION (bug métier 05/08) : un « Début du
        # trajet » CONNU (anti-rejeu) mais resté SANS trajet (création
        # manquée lors d'une passe défectueuse) n'était JAMAIS ré-essayé
        # → le trajet en cours restait invisible (2736TCC 11:37, 4886TBU
        # 11:00). On ré-ingère une fois ; la couverture est revérifiée à
        # chaque passe (idempotent).
        return n

    def inserer(self, points: list[dict], historique: bool = False) -> int:
        db = SessionLocal()
        inseres = 0
        try:
            mapping = self._mapper_vehicules(db)
            vus: dict[str, Vehicule] = {}
            # v1.50 — COMMIT PAR LOTS. Avant, TOUT le lot vivait dans une seule
            # transaction : mesuré 10 000 points = 10,9 s de verrou d'écriture
            # (≈ 44 s pour une fenêtre d'arrêt de 25 h), au-dessus du
            # busy_timeout de 30 s de la base → « database is locked » en
            # cascade le 18/09/2026. Avec des lots de LOT_INSERTION (250), le
            # verrou n'est tenu que ~0,3 s ; un verrou résiduel fait REJOUER le
            # lot (idempotent : anti-rejeu par clé et par horodatage) au lieu de
            # perdre la passe entière.
            taille_lot = max(1, LOT_INSERTION)
            # v1.54 — l'attente du verrou d'écriture est MESURÉE AVANT d'écrire
            # (une fois par passe) et publiée à part : l'exploitant voit si son
            # temps part chez le portail ou dans son propre SQLite.
            _attente_base = self._sonder_attente_sqlite()
            if _attente_base > SQLITE_COMMIT_BASE_S:
                _pasee = passe_courante()
                if _pasee is not None:
                    _pasee.metriques.ajouter("attente_sqlite_s",
                                             _attente_base - SQLITE_COMMIT_BASE_S)
            for debut_lot in range(0, len(points), taille_lot):
                # v1.54 — POINT D'ARRÊT CONTRÔLÉ AVANT CHAQUE LOT : une passe
                # qui a dépassé son budget N'ÉCRIT PLUS RIEN. L'écriture en
                # cours est terminée (transaction courte et atomique), puis la
                # passe se retire : plus de demi-lot écrit après l'échéance.
                verifier_etape("ecriture")
                tranche = points[debut_lot:debut_lot + taille_lot]
                for tentative in range(1, COMMIT_ESSAIS + 1):
                    t_lot = time.monotonic()
                    avant_tranche = inseres
                    try:
                        t_commit = None
                        with chrono("ecriture"):
                            inseres += self._inserer_tranche(
                                db, tranche, mapping, vus, historique)
                            t_commit = time.monotonic()
                            db.commit()
                        # v1.54 — ATTENTE SQLITE MESURÉE À CHAQUE COMMIT (pas
                        # seulement en cas d'échec) : si le commit a attendu le
                        # verrou d'écriture, cette attente est publiée à part,
                        # sans être confondue avec le travail d'écriture.
                        _duree_commit = time.monotonic() - t_commit
                        if _duree_commit > SQLITE_COMMIT_BASE_S:
                            _p = passe_courante()
                            if _p is not None:
                                _p.metriques.ajouter(
                                    "attente_sqlite_s",
                                    _duree_commit - SQLITE_COMMIT_BASE_S)
                        # métrique EXACTE : on ne compte que ce qui a été écrit
                        # (un lot entièrement dédoublonné ne « compte » pas).
                        compter("nb_points_ecrits", inseres - avant_tranche)
                        if SOUFFLE_INTER_LOTS_S > 0:
                            time.sleep(SOUFFLE_INTER_LOTS_S)
                        break
                    except OperationalError as exc:
                        db.rollback()
                        # v1.54 — ATTENTE SQLITE MESURÉE : le temps perdu à
                        # attendre le verrou d'écriture de la base est
                        # désormais publié (il n'est plus confondu avec un
                        # temps passé chez le portail).
                        _p = passe_courante()
                        if _p is not None and _erreur_locale(exc):
                            _p.metriques.ajouter("attente_sqlite_s",
                                                 time.monotonic() - t_lot)
                        if not _erreur_locale(exc) or tentative == COMMIT_ESSAIS:
                            raise
                        log.warning(
                            "Collecte %s : verrou d'écriture sur le lot "
                            "%d-%d (tentative %d/%d : %s) — lot rejoué",
                            getattr(self.source, "value", self.source),
                            debut_lot + 1, debut_lot + len(tranche),
                            tentative, COMMIT_ESSAIS, str(exc)[:80])
                        time.sleep(0.4 * tentative)
                        mapping = self._mapper_vehicules(db)
                        vus.clear()
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
            if fin <= debut:
                # `parse_dt` convertit déjà UTC→local : une fin <= début est
                # une donnée corrompue du portail — on ignore la ligne (PAS
                # de correction +3h en dur : durées gonflées / trajets longs
                # jetés silencieusement).
                log.warning("CamtrackPro screen : trajet ignoré car fin (%s) <= début (%s) pour %s", fin, debut, cellules[ci["veh"]])
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
        checkpoint_id = _checkpoint_ouvre("MZONEX_N1", debut_utc, fin_utc)
        try:
            lignes = self.api.evenements(debut_utc, fin_utc)
        except Exception as exc:
            _checkpoint_ferme(checkpoint_id, "ECHEC", str(exc)[:500])
            _audit_collecte("collecte.echec_n1", {
                "source": "MZONEX", "niveau": "N1_EVENEMENTS",
                "periode_utc": [debut_utc.isoformat(), fin_utc.isoformat()],
                "periode_locale": [_libelle_local(debut_utc), _libelle_local(fin_utc)],
                "profondeur_s": FENETRE_MAX_S, "tranche_s": TRANCHE_S,
                "erreur": f"{type(exc).__name__}: {exc}"[:300],
                "issue": "ECHEC", "donnees_ingerees": 0})
            raise
        _checkpoint_ferme(checkpoint_id, "TERMINE")
        if lignes:
            # Règle 8 — source + PÉRIODE relue inscrites dans l'audit (les
            # boîtiers muets renvoient ici leurs tampons tardifs).
            _audit_collecte("collecte.fenetre_n1", {
                "source": "MZONEX", "niveau": "N1_EVENEMENTS",
                "periode_utc": [debut_utc.isoformat(), fin_utc.isoformat()],
                "periode_locale": [_libelle_local(debut_utc), _libelle_local(fin_utc)],
                "profondeur_s": FENETRE_MAX_S, "tranche_s": TRANCHE_S,
                "evenements": len(lignes), "issue": "LU",
                "donnees_ingerees": len(lignes)})
        log.info("MZoneX API (Événements) : %d événement(s), fenêtre %s → %s UTC",
                 len(lignes), debut_utc.strftime("%H:%M:%S"),
                 fin_utc.strftime("%H:%M:%S"))
        return lignes

    def normaliser(self, brut: list[dict]) -> list[dict]:
        map_vehs = {}
        try:
            map_vehs = self.api.map_vehicules()
        except Exception:
            pass
        points = [p for p in (point_depuis_evenement_api(v, map_vehicules=map_vehs) for v in brut)
                  if p is not None]
        try:
            derniers = self.api.dernieres_positions()
            points.extend(derniers)
        except Exception:
            pass
        nq = len(brut) - len(points)
        if nq > 0:
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
        map_vehs = {}
        try:
            map_vehs = self.api.map_vehicules()
        except Exception:
            pass
        items: list[dict] = []
        for jour in sorted(set(jours)):
            bruts = self.api.trajets_jour_local(jour)
            items.extend(it for it in (trajet_depuis_api(t, map_vehicules=map_vehs) for t in bruts)
                         if it)
            log.info("MZoneX API (Trajets) : %d ligne(s) API du %s",
                     len(bruts), jour.isoformat())
        log.info("MZoneX API (Trajets) : %d jour(s) relu(s) → %d trajet(s) "
                 "officiel(s) ; recensement D4 : %d publié(s)",
                 len(set(jours)), len(items), len(self.recensement))
        return items


def _collecter_mzonex_n1_avec_repli(classe_ecran) -> int:
    """§0sexies A2 : API d'abord ; repli écran uniquement si activé."""
    try:
        return MZoneXApiCollector().run()
    except Exception as e:
        _etat_collecte_erreur("MZONEX", e)
        log.warning("MZoneX API (Événements) indisponible (%s)", e)
        if os.getenv("MZONEX_REPLI_ECRAN", "0") == "1":
            try:
                return classe_ecran().run()
            except Exception as e_scr:
                _etat_collecte_erreur("MZONEX", e_scr)
                log.warning("MZoneX lecteur d'écran également indisponible (%s) — cycle reporté", e_scr)
                raise
        raise


def _synchroniser_dernier_point_mzonex(db=None) -> dict:
    """Synchronise la dernière position connue de chaque véhicule MZoneX afin d'actualiser le timestamp
    de communication et lever le repère 'boîtier muet'."""
    from .api_mzonex import ApiMZoneX
    from .models import StatutVehicule
    from .config import now_local
    
    fermer_db = False
    if db is None:
        db = SessionLocal()
        fermer_db = True
        
    actualises = 0
    muets: list = []       # v1.49 — véhicules muets côté portail (jamais inventés)
    now = now_local()
    
    try:
        # 1. Si l'API MZoneX est active et joignable, interroger Vehicles
        points_api = []
        if _mzonex_api_active() and os.getenv("MZONEX_USER"):
            try:
                points_api = ApiMZoneX().dernieres_positions()
            except Exception as e:
                log.info("MZoneX API dernieres_positions indisponible (%s)", e)
                
        # Mapping des points reçus par plaque
        map_points = {p["gps_associe"]: p for p in points_api if p.get("gps_associe")}
        
        # 2. Mettre à jour les véhicules MZoneX
        vehicules = db.scalars(select(Vehicule).where(
            Vehicule.statut == StatutVehicule.ACTIF,
            Vehicule.plateforme_gps == "MZONEX"
        )).all()
        
        for v in vehicules:
            p = map_points.get(v.plaque)
            if p:
                v.last_lat = p["lat"]
                v.last_lng = p["lng"]
                v.last_vitesse = p["vitesse"]
                v.last_event_at = p["horodatage"]
                v.moteur_on = (p.get("moteur") == "ON")
                actualises += 1
                # v1.49 — clé d'idempotence : la MÊME photo ne doit pas
                # s'empiler dans la trace à chaque appel de « Sync GPS »
                # (l'ancien code écrivait sans clé — doublons garantis).
                from .engine import cle_idempotence_evenement
                ev = EvenementGPS(
                    source=SourceEvenement.MZONEX,
                    vehicule_id=v.id,
                    horodatage=p["horodatage"],
                    latitude=p["lat"],
                    longitude=p["lng"],
                    vitesse=p.get("vitesse", 0.0),
                    etat_moteur=p.get("moteur", "ON"),
                    type_evenement=TypeEvenement.POSITION,
                    idempotence_key=cle_idempotence_evenement(
                        v.id, p["horodatage"], p["lat"], p["lng"],
                        SourceEvenement.MZONEX)
                )
                try:
                    with db.begin_nested():
                        db.add(ev)
                        db.flush()
                except IntegrityError:
                    pass
            else:
                # v1.49 — PLUS AUCUNE TRACE FABRIQUÉE (défaut majeur du 17/09).
                # Cette branche forçait `v.last_event_at = now - 2 min` et
                # écrivait un événement inventé (« il y a 2 minutes », position
                # et vitesse recopiées du dernier signal) « afin d'actualiser le
                # timestamp de communication et lever le repère boîtier muet ».
                # Conséquences mesurées :
                #   (a) le repère « boîtier muet » ne pouvait JAMAIS s'afficher
                #       pour un camion MZoneX muet — le mensonge contredisait
                #       /api/sante (v1.48) ;
                #   (b) le bornage v1.48 des compteurs (« dernière trace +
                #       30 min ») était neutralisé : la fausse trace étant
                #       toujours « fraîche », les compteurs couraient encore
                #       jusqu'à minuit ;
                #   (c) l'arbitrage R2 (engine.rattraper_ouvertures : signal
                #       ≤ 15 min ET vitesse > 3 km/h) croyait à un roulage en
                #       cours et ROUVRAIT une ligne datée du dernier événement
                #       connu → « mauvaises heures de départ » (08:43, 15:32) ;
                #   (d) ces événements, écrits SANS clé d'idempotence,
                #       s'empilaient à chaque cycle dans la trace.
                # Le portail ne publie rien pour ce véhicule : on le DIT
                # (compteur `portail_muet`), on n'invente rien.
                muets.append(v.plaque)
                    
        db.commit()
        log.info("_synchroniser_dernier_point_mzonex : %d véhicule(s) actualisé(s)", actualises)
        return {
            "vehicules_actualises": actualises,
            # v1.49 — ce que le portail N'A PAS publié : la mesure honnête du
            # silence (avant, il était masqué par une trace fabriquée).
            "portail_muet": len(muets),
            "portail_muet_plaques": muets[:20],
            "statut": "OK" if not muets else "PARTIEL"
        }
    except Exception:
        db.rollback()
        log.exception("Échec _synchroniser_dernier_point_mzonex")
        raise
    finally:
        if fermer_db:
            db.close()


# ============================================================================
# v1.54/P2 (18/09/2026) — REPLI API : échec TRACÉ, ÉCRAN SYSTÉMATIQUE,
# RÉSULTAT UTILISÉ SEULEMENT S'IL EST COMPLET, SOURCE IDENTIFIÉE PAR LIGNE.
# Avant : un échec de l'API renvoyait une liste VIDE (données perdues pour le
# cycle) sauf si MZONEX_REPLI_ECRAN=1 était positionné à la main — alors que la
# règle documentée (§0sexies A2) dit « TOUT ÉCHEC API replie automatiquement ».
# Le repli n'est JAMAIS utilisé partiellement : une lecture incomplète
# (véhicules de la journée absents du relevé) est REJETÉE en bloc, pour ne
# jamais produire d'archive partielle (R-11).
# ============================================================================
DERNIER_ETAT_N2: dict = {}

# Origine de lecture portée par CHAQUE ligne collectée (traçabilité).
ORIGINE_API = "API_N2"
ORIGINE_ECRAN = "REPLI_ECRAN"


def _plaque_cle(valeur) -> str:
    """Forme comparable d'une plaque : « 4006 TBS (LSS) » → « 4006TBS »."""
    return "".join(ch for ch in str(valeur or "").upper() if ch.isalnum())


def _identifier_source(valides: list, origine: str) -> list:
    """P2-6 : chaque ligne porte la SOURCE DE LECTURE utilisée."""
    for item in valides or []:
        if isinstance(item, dict):
            item.setdefault("origine_lecture", origine)
    return list(valides or [])


def _completude_lecture(valides: list, recensement: list,
                        attendues=None) -> tuple:
    """P2-3 : le résultat couvre-t-il TOUT ce qu'on attend ?

    `attendues` = plaques attendues pour les journées relues (véhicules suivis).
    Sans référence connue (`attendues` vide), la complétude ne peut pas être
    prouvée : la lecture est acceptée seulement si elle n'est pas vide, et le
    mode est consigné (« sans_reference ») pour l'audit.
    """
    couvertes = {_plaque_cle(v.get("plaque")) for v in (valides or [])
                 if isinstance(v, dict)}
    couvertes |= {_plaque_cle(x) for x in (recensement or [])}
    couvertes.discard("")
    attendues_cles = {_plaque_cle(x) for x in (attendues or [])}
    attendues_cles.discard("")
    if not attendues_cles:
        return bool(valides), {"mode": "sans_reference",
                               "couvertes": len(couvertes), "attendues": 0,
                               "manquantes": []}
    manquantes = sorted(attendues_cles - couvertes)
    return (bool(valides) and not manquantes,
            {"mode": "reference", "couvertes": len(couvertes),
             "attendues": len(attendues_cles), "manquantes": manquantes[:20]})


def _plaques_attendues(jours: list | None) -> list:
    """P2-3 — plaques SUIVIES ces journées-là : référence de complétude du repli."""
    if not jours:
        return []
    try:
        from .database import SessionLocal
        from .models import SuiviJournalier, Vehicule
        db = SessionLocal()
        try:
            lignes = db.execute(
                select(Vehicule.plaque)
                .join(SuiviJournalier, SuiviJournalier.vehicule_id == Vehicule.id)
                .where(SuiviJournalier.date_jour.in_(list(jours)))
                .distinct()).all()
            return [l[0] for l in lignes if l[0]]
        finally:
            db.close()
    except Exception:
        log.exception("Référentiel de complétude indisponible — contrôle ignoré")
        return []


def _tracer_repli_n2(source: str, jours: list | None) -> None:
    """P2-1 — l'échec de l'API et l'usage du repli sont CONSIGNÉS en base."""
    etat = dict(DERNIER_ETAT_N2 or {})
    if not etat.get("echec") or etat.get("origine") != ORIGINE_ECRAN:
        return
    try:
        from .database import SessionLocal
        from .models import AuditLog
        db = SessionLocal()
        try:
            db.add(AuditLog(username="collecte-n2", action="lecture.repli_ecran",
                            entite="source", entite_id=source,
                            details={"echec_api": etat.get("echec"),
                                     "origine_utilisee": etat.get("origine"),
                                     "complet": etat.get("complet"),
                                     "detail": etat.get("detail"),
                                     "jours": [str(j) for j in (jours or [])]}))
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("Traçage du repli N2 en échec (collecte non bloquée)")


def _collecter_n2_mzonex(jours: list | None = None, *,
                         attendues=None) -> tuple:
    """§0sexies A2 + P2 : API d'abord, ÉCRAN EN SECOURS SYSTÉMATIQUE, et le
    secours n'est utilisé QUE S'IL EST COMPLET (jamais d'archive partielle).

    1. l'échec de l'API est enregistré (journal + `DERNIER_ETAT_N2`) ;
    2. le lecteur d'écran est TOUJOURS sollicité après un échec (désactivable
       par `MZONEX_REPLI_ECRAN=0` pour un diagnostic) ;
    3. la complétude du repli est vérifiée ;
    4. le repli n'est utilisé que s'il est complet ;
    5. sinon : listes vides (le cycle n'écrit rien — aucune archive partielle) ;
    6. chaque ligne porte son origine de lecture.
    """
    etat = {"echec": None, "origine": None, "complet": None, "detail": {}}
    repli_autorise = os.getenv("MZONEX_REPLI_ECRAN", "1") == "1"
    if _mzonex_api_active():
        try:
            c = MZoneXTrajetsApiCollector()
            valides = list(c.collecter_valides(jours) or [])
            rec = list(c.recensement or [])
            complet, detail = _completude_lecture(valides, rec, attendues)
            if valides and complet:
                etat.update(origine=ORIGINE_API, complet=True, detail=detail)
                DERNIER_ETAT_N2.clear(); DERNIER_ETAT_N2.update(etat)
                return _identifier_source(valides, ORIGINE_API), rec
            etat["echec"] = ("api_incomplete" if valides else "api_sans_ligne")
            etat["detail"] = detail
            log.warning("MZoneX API (Trajets) %s — repli écran (A2/P2) : %s",
                        etat["echec"], detail)
        except Exception as exc:                              # P2-1
            etat["echec"] = f"api_{type(exc).__name__}"
            log.exception("MZoneX API (Trajets) en échec — repli écran (A2/P2)")
    else:
        etat["echec"] = "api_desactivee"
    # P2-2 : repli écran (systématique par défaut — A2 « écran en secours »)
    if not repli_autorise and etat["echec"] == "api_desactivee":
        log.info("MZoneX : API désactivée et repli écran refusé "
                 "(MZONEX_REPLI_ECRAN=0) — aucun relevé pour ce cycle")
        etat.update(origine=None, complet=False)
        DERNIER_ETAT_N2.clear(); DERNIER_ETAT_N2.update(etat)
        return [], []
    if not repli_autorise:
        log.warning("MZoneX API en échec et repli écran DÉSACTIVÉ "
                    "(MZONEX_REPLI_ECRAN=0) — aucun relevé pour ce cycle")
        etat.update(origine=None, complet=False)
        DERNIER_ETAT_N2.clear(); DERNIER_ETAT_N2.update(etat)
        return [], []
    c = MZoneXTrajetsCollector()
    valides = list(c.collecter_valides() or [])
    rec = list(getattr(c, "recensement", []) or [])
    complet, detail = _completude_lecture(valides, rec, attendues)   # P2-3
    etat.update(origine=ORIGINE_ECRAN, complet=bool(complet), detail=detail)
    if not complet:                                                 # P2-5
        log.warning("Repli ÉCRAN MZoneX INCOMPLET → relevé REJETÉ en bloc "
                    "(aucune archive partielle) : %s", detail)
        DERNIER_ETAT_N2.clear(); DERNIER_ETAT_N2.update(etat)
        return [], []
    log.info("Repli ÉCRAN MZoneX utilisé (API %s) : %d ligne(s) — %s",
             etat["echec"], len(valides), detail)
    DERNIER_ETAT_N2.clear(); DERNIER_ETAT_N2.update(etat)
    return _identifier_source(valides, ORIGINE_ECRAN), rec      # P2-6


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
    except Exception as e:
        _etat_collecte_erreur("CAMTRACKPRO", e)
        log.exception("CamtrackPro API (positions) en échec — cycle reporté "
                      "(§5, aucun flux écran de secours)")
        raise


def _collecter_n2_camtrackpro(jours: list | None = None) -> tuple:
    """§0sexies A2/A4 : Niveau 2 CamtrackPro — API d'abord, repli écran si demandé."""
    if jeton_configure():
        try:
            c = CamtrackProTrajetsApiCollector()
            try:
                valides = c.collecter_valides(jours)
            except TypeError:
                valides = c.collecter_valides()
            return valides, list(c.recensement or [])
        except Exception:
            log.exception("CamtrackPro API (rapport trajets) en échec")
            if os.getenv("CAMTRACKPRO_REPLI_ECRAN", "1") != "1":
                return [], []
    if os.getenv("CAMTRACKPRO_REPLI_ECRAN", "1") == "1":
        c = CamtrackProTrajetsCollector()
        return c.collecter_valides(), list(getattr(c, "recensement", []) or [])
    return [], []


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
    """Passe Niveau 2 : sous LE verrou « N2_<source> », avec SON budget, SES
    métriques et SON point d'arrêt.

    v1.54 — comme la passe N1, la passe N2 peut être ANNULÉE proprement (par la
    surveillance, au-delà de sa limite) : elle s'arrête avant le travail suivant
    (appel réseau ou écriture) et rend ELLE-MÊME son verrou.
    """
    source_nom = source or "MIXTE"
    ressource = f"N2_{source_nom}"
    possession_n2 = _acquerir_verrou_n2(ressource)
    if possession_n2 is None:
        log.warning("Synchronisation Niveau 2 ignorée : le verrou « N2_%s » est "
                    "détenu par « %s » — aucune écriture de ma part",
                    source_nom, verrou_de(ressource).proprietaire)
        return {"occupee": True}
    passe = PasseCourante(source=ressource, budget_s=LOCK_N2_TIMEOUT_S)
    enregistrer_passe(ressource, passe)
    try:
        with activer_passe(passe):
            stats = _synchroniser_trajets_valides(source)
        publier_metriques(passe.terminer("TERMINE"))
        return stats
    except BudgetDepasse as exc:
        publier_metriques(passe.terminer("BUDGET_DEPASSE"))
        _etat_collecte_erreur(ressource, exc)
        log.warning("Synchronisation Niveau 2 (%s) arrêtée proprement : %s",
                    source_nom, exc)
        return {"budget_depasse": True, "etape": exc.etape}
    except Exception as exc:
        publier_metriques(passe.terminer("ECHEC"))
        _etat_collecte_erreur(ressource, exc)
        raise
    finally:
        retirer_passe(ressource, passe)
        _liberer_verrou_n2(possession_n2)


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
            verifier_etape("pagination")   # v1.54 — arrêt avant un appel réseau
            if nom == "MZONEX":
                # §0sexies A2 + P2 — API MZoneX en principal, écran en secours
                # SYSTÉMATIQUE, utilisé seulement s'il est COMPLET.
                bruts, rec = _collecter_n2_mzonex(
                    jours, attendues=_plaques_attendues(jours))
                _tracer_repli_n2(nom, jours)
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
        except BudgetDepasse:
            raise                          # v1.54 — signal d'ARRÊT : jamais absorbé
        except Exception as exc:
            # v1.54 — l'échec d'une source N2 est PUBLIÉ (classe fine) au lieu
            # d'être seulement journalisé : un « database is locked » pendant N2
            # devient visible dans /api/sante (attente_sqlite), jamais muet.
            _etat_collecte_erreur(f"N2_{nom}", exc)
            log.exception("Échec collecte Niveau 2 (%s)", nom)
            continue
        db = SessionLocal()
        try:
            # v1.54 — l'attente éventuelle du verrou d'écriture de SQLite est
            # mesurée AVANT d'écrire (comme pour la collecte N1) : elle est
            # publiée à part du temps passé chez le portail.
            _attente_n2 = CollectorBase._sonder_attente_sqlite()
            if _attente_n2 > SQLITE_COMMIT_BASE_S:
                _p = passe_courante()
                if _p is not None:
                    _p.metriques.ajouter("attente_sqlite_s",
                                         _attente_n2 - SQLITE_COMMIT_BASE_S)
            verifier_etape("ecriture")     # v1.54 — arrêt avant une écriture
            with chrono("ecriture"):
                stats = reconcilier_trajets_valides(
                    db, items, username=f"collecteur-{nom.lower()}")
        except BudgetDepasse:
            raise                          # échéance : la PASSE s'arrête ici
        except Exception as exc:
            # v1.54 — un « database is locked » (ou toute autre panne) sur la
            # réconciliation d'UNE source ne doit pas emporter les autres : il
            # est PUBLIÉ (classe fine, visible dans /api/sante) et la passe
            # continue — aucune écriture ne reste « non traitée ».
            _etat_collecte_erreur(f"N2_{nom}", exc)
            log.exception("Échec réconciliation Niveau 2 (%s) — source suivante",
                          nom)
            continue
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
    # v1.50 — BUDGET PAR CYCLE : une fenêtre d'arrêt peut couvrir 20 h et
    # des dizaines de milliers d'événements. Sans plafond, la relecture
    # verrouillait la base pendant ~44 s (mesuré : 10 000 points = 11 s), bien
    # au-delà du busy_timeout de 30 s — d'où les « database is locked » en
    # cascade du 18/09. La collecte reste REPRENABLE : les fenêtres sont
    # recalculées à chaque cycle à partir de ce qui manque réellement en base,
    # donc les points non traités le sont au cycle suivant (idempotent).
    tracees: list[dict] = []           # v1.54 — périodes réellement relues
    budget = max(0, RELECTURE_N1_MAX_POINTS)
    # Traitement par petits lots de 2 fenêtres max pour garantir une exécution rapide (< 5s)
    fenetres_a_traiter = fenetres[:2]
    try:
        for debut_local, fin_local in fenetres_a_traiter:
            if total >= budget:
                log.info("Relecture N1 MZoneX : budget de %d point(s) atteint "
                         "— la suite au prochain cycle", budget)
                break
            debut_utc = coll.api._utc_naive(debut_local)
            fin_utc = coll.api._utc_naive(fin_local)
            checkpoint_id = _checkpoint_ouvre("MZONEX_N1_RELECTURE",
                                              debut_utc, fin_utc)
            try:
                brut = coll.api.evenements(debut_utc, fin_utc)
            except Exception as exc:
                _checkpoint_ferme(checkpoint_id, "ECHEC", str(exc)[:500])
                _audit_collecte("collecte.relecture_n1_echec", {
                    "source": "MZONEX", "niveau": "N1_RELECTURE_TROUS",
                    "periode_locale": [debut_local.isoformat(),
                                       fin_local.isoformat()],
                    "jours": jours, "erreur": f"{type(exc).__name__}: {exc}"[:300],
                    "issue": "ECHEC", "donnees_ingerees": 0})
                raise
            points = coll.normaliser(brut)
            restant = max(0, budget - total)
            if len(points) > restant:
                # ordre chronologique garanti (CollectorBase.normaliser trie)
                log.warning("Relecture N1 %s → %s : %d événement(s) lus, "
                            "budget de %d point(s) — les %d restants sont "
                            "laissés au prochain cycle (la fenêtre sera "
                            "recalculée depuis la base)",
                            debut_local.strftime("%m-%d %H:%M"),
                            fin_local.strftime("%H:%M"), len(points), restant,
                            len(points) - restant)
                points = points[:restant]
            n = 0
            if points:
                n = coll.inserer(points, historique=True)
                total += n
            _checkpoint_ferme(checkpoint_id, "TERMINE")
            tracees.append({"debut": debut_local.isoformat(),
                            "fin": fin_local.isoformat(),
                            "lus": len(brut), "points": n})
            log.info("Relecture N1 MZoneX %s → %s : %d événement(s) lu(s), "
                     "%d point(s) inséré(s)", debut_local.strftime("%m-%d %H:%M"),
                     fin_local.strftime("%H:%M"), len(brut), n)
            time.sleep(0.2)
    except Exception:
        log.exception("Relecture N1 MZoneX en échec (retraitée au prochain "
                      "passage)")
    if total:
        # Règle 8 — le rattrapage (données arrivées tardivement) est AUDITÉ avec
        # sa source et les PÉRIODES réellement relues.
        _audit_collecte("collecte.relecture_n1", {
            "source": "MZONEX", "niveau": "N1_RELECTURE_TROUS",
            "jours": jours, "points_inseres": total,
            "fenetres_relues": tracees[:20],
            "fenetres_total": len(tracees), "issue": "RATTRAPAGE",
            "donnees_ingerees": total})
    return total


# ══════════════════════════════════════════════════════════════════════════
# v1.54 — WORKER DE RELECTURE HISTORIQUE (priorité inférieure, verrou séparé)
# ══════════════════════════════════════════════════════════════════════════
_RELECTURE_WORKER = {"demarre": False, "mono": 0.0}


def relecture_due(maintenant_mono: float | None = None) -> bool:
    """La relecture est-elle due ? (au démarrage, puis toutes les
    RELECTURE_N1_PERIODE_S). Pur calcul — testable sans réseau."""
    m = time.monotonic() if maintenant_mono is None else maintenant_mono
    return (_RELECTURE_WORKER["mono"] == 0.0
            or (m - _RELECTURE_WORKER["mono"]) >= RELECTURE_N1_PERIODE_S)


def relire_si_due() -> int:
    """Exécute UNE relecture si elle est due, sous son propre verrou. Renvoie
    le nombre de points rattrapés (0 si rien à faire ou déjà en cours).

    C'est le point d'entrée du worker : il ne touche JAMAIS aux verrous du
    temps réel (MZONEX / CAMTRACKPRO)."""
    if not relecture_due():
        return 0
    _RELECTURE_WORKER["mono"] = time.monotonic()
    n = _collecte_protegee("MZONEX_RELECTURE", relecture_n1_mzonex,
                           timeout_s=RELECTURE_N1_TIMEOUT_S)
    if n:
        log.info("Relecture N1 (Événements MZoneX, %d jours) : %d point(s) "
                 "rattrapé(s) [worker dédié]", RELECTURE_N1_JOURS, n)
    return n


def worker_relecture_n1(arret: threading.Event | None = None,
                        sommeil_s: float | None = None) -> None:
    """Boucle du worker de relecture (priorité INFÉRIEURE au temps réel).

    Un seul worker à la fois (`_RELECTURE_WORKER["demarre"]`) ; il ne démarre
    jamais une relecture si la précédente tourne encore (son verrou le dit)."""
    pause = float(sommeil_s if sommeil_s is not None
                  else os.getenv("RELECTURE_N1_SOMMEIL_S", "20"))
    log.info("Worker de relecture N1 démarré (période %ss, sommeil %ss, verrou "
             "dédié « MZONEX_RELECTURE ») — priorité inférieure au temps réel",
             RELECTURE_N1_PERIODE_S, pause)
    while True:
        if arret is not None and arret.is_set():
            return
        try:
            relire_si_due()
        except Exception:
            log.exception("Worker de relecture N1 — échec (retraité au cycle "
                          "suivant)")
        time.sleep(max(1.0, pause))


def demarrer_worker_relecture() -> bool:
    """Démarre le worker UNE SEULE FOIS. Renvoie True s'il vient d'être lancé."""
    if _RELECTURE_WORKER["demarre"]:
        return False
    _RELECTURE_WORKER["demarre"] = True
    threading.Thread(target=worker_relecture_n1, name="lss-relecture-n1",
                     daemon=True).start()
    return True


def boucle_collecte():
    """Collecte planifiée Niveau 1 en continu (période COLLECTOR_PERIODE_S, §10).
    `COLLECTOR_SOURCE=MIXTE` → Niveau 1 MZoneX (CamtrackPro = VALIDÉ direct,
    borne §5 : pas de flux temps réel fiable côté Camtrack)."""
    source = os.getenv("COLLECTOR_SOURCE", "MIXTE").upper()
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
    # QC étape 1 (21/09/2026) — la configuration de la fenêtre de rattrapage est
    # AFFICHÉE au démarrage : profondeur (3 h) et taille de tranche (15 min) sont
    # deux réglages distincts, contrôlables dans le journal du serveur.
    log.info("MZoneX — profondeur de rattrapage : %d s (%dh) · tranche de payload : "
             "%d s · %d tranche(s) pour une fenêtre pleine",
             FENETRE_MAX_S, FENETRE_MAX_S // 3600, TRANCHE_S,
             max(1, FENETRE_MAX_S // max(60, TRANCHE_S)))
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
    # v1.54 — le worker de relecture (priorité inférieure, verrou dédié) est
    # démarré ICI, une seule fois : le temps réel ne l'attend jamais.
    demarrer_worker_relecture()

    while True:
        # §0bis — SURVEILLANCE COOPÉRATIVE des verrous (v1.54).
        # L'ancienne « réinitialisation forcée » VOLAIT le verrou : la passe
        # périmée continuait d'écrire pendant qu'une autre démarrait. Ici, on
        # ANNULE la passe qui dépasse sa limite : elle s'arrête à son prochain
        # point d'arrêt et libère ELLE-MÊME son jeton. Aucun vol, aucune double
        # écriture.
        for _passes in passes_en_cours().items():
            _ressource, _info = _passes
            _limite = (LOCK_N2_TIMEOUT_S if _ressource.startswith("N2_")
                       else LOCK_N1_TIMEOUT_S)
            if _info["ecoule_s"] > _limite and not _info["annulee"]:
                _passe = passe_de(_ressource)
                if _passe is not None:
                    log.warning("Passe « %s » au-delà de sa limite (%.1fs > %.1fs, "
                                "étape « %s ») — ANNULATION COOPÉRATIVE (arrêt au "
                                "prochain point d'arrêt)",
                                _ressource, _info["ecoule_s"], _limite,
                                _info["etape"])
                    _passe.annuler("surveillance_limite_depassee")

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
        # v1.54 (21/09/2026) — LA RELECTURE HISTORIQUE N'EST PLUS DANS CETTE
        # BOUCLE. Elle vit dans un WORKER DÉDIÉ, de priorité inférieure, avec
        # SON PROPRE VERROU (« MZONEX_RELECTURE ») : une relecture de 7 jours
        # peut durer, elle n'empêche plus jamais le temps réel d'être collecté.
        # (Avant : même verrou + 180 s de budget ⇒ MZONEX et CamtrackPro
        # étaient ignorés pendant toute la relecture.)
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
