"""v1.54 (21/09/2026) — CONCURRENCE DE COLLECTE : possession, budget, métriques.

Module AUTONOME (stdlib uniquement) : il ne dépend d'aucun autre module de
l'application, ce qui évite tout import circulaire et permet de le tester seul.

Il corrige trois défauts prouvés le 21/09/2026 sur un incident de collecte :

1. **VERROU À POSSESSION PAR JETON** (`VerrouPossede`).
   Avant : `_acquerir_verrou_n1()` renvoyait un booléen et le verrou était une
   RÉFÉRENCE GLOBALE, remplacée à l'auto-libération. La passe périmée continuait
   de détenir « son » objet et, à sa sortie, appelait la libération globale —
   c'est-à-dire celle de la passe SUIVANTE. Deux collectes écrivaient alors en
   même temps (défaut reproduit en isolation : « exclusion mutuelle PERDUE »).
   Désormais :
     - l'acquisition renvoie un **jeton de possession** (identifiant unique) ;
     - la libération **vérifie le jeton ET la génération** : un jeton étranger
       ne libère jamais le verrou d'une autre tâche ;
     - l'expiration est **explicite et tracée** (elle ne remplace jamais
       silencieusement la possession) ;
     - chaque verrou publie **propriétaire, date de prise, dernière activité,
       expiration** ;
     - la libération est TOUJOURS exécutée dans un `finally`.

2. **UN VERROU PAR SOURCE** (`verrou_de`). MZONEX (temps réel), CAMTRACKPRO et
   MZONEX_RELECTURE ne partagent plus un verrou unique : la relecture
   historique (7 jours) ne peut plus affamer la collecte temps réel.

3. **BUDGET À POINTS D'ARRÊT CONTRÔLÉS** (`PasseCourante`, `BudgetDepasse`).
   Une passe qui dépasse son budget s'arrête à un point d'arrêt prévu (avant une
   page, avant un véhicule, avant un lot d'écriture) : elle **n'écrit plus rien
   après son échéance**, rend son verrou et déclare l'étape qui a consommé le
   temps (attente HTTP, SQLite, verrou, pagination, écriture, parsing). Le
   budget n'est jamais « augmenté » pour faire disparaître le symptôme.

Les métriques par étape sont publiées pour `/api/sante` : authentification,
pagination, par véhicule, parsing, écriture, attente de verrou, durée totale,
nombre de pages, de véhicules et de lignes.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger("lss.concurrence")

# ════════════════════════════════════════════════════════════════════════════
# M1 (22/09/2026) — TRACES DE COLLECTE : LISTE BLANCHE + TEXTE ASSAINI
# ════════════════════════════════════════════════════════════════════════════
# PORTÉE STRICTE : ces helpers servent aux NOUVELLES traces `collecte.*` de M1.
# Ils ne touchent NI `scrapers._audit_collecte`, NI un audit existant, NI un
# audit historique (aucune réécriture) : M1 n'écrit que ses propres traces.
# AUCUN commutateur d'exécution : ce sont des constantes de module.

# LISTE BLANCHE : seules ces clés peuvent être publiées. Toute clé absente est
# DÉTRUITE avant la mise en file — un brut de portail, un en-tête HTTP, une URL
# complète ou un `details` libre ne peuvent donc PAS atteindre la base.
CHAMPS_TRACE_PUBLIABLES = frozenset({
    # conteneur / identité
    "source", "source_reelle", "passe", "couvre_sources", "compteurs_partages",
    "metriques_attribuables_a", "lancee", "source_demarree",
    "non_demarree_raison", "jours", "jour", "heure",
    # temps
    "debut_le", "fin_le", "demarrage_le", "total_s", "budget_s",
    "duree_lecture_s", "duree_ecriture_s", "passes", "somme_total_s",
    "max_total_s",
    # issue / cause
    "issue", "phase", "etape_bloquante", "raison_annulation", "issues",
    # phases mesurées (clés fixes)
    "auth_s", "attente_http_s", "pagination_s", "vehicule_s", "parsing_s",
    "ecran_s", "ecriture_s", "attente_sqlite_s",
    # volumes
    "nb_pages", "nb_lignes_lues", "nb_ecran_vehicules",
    # résultat CONFIRMÉ de réconciliation (par source)
    "recus", "ecrits", "ignores", "confirme_apres_commit",
    # état des traces (énumération fermée)
    "origine_lecture", "traces_perdues", "file_max", "purge_active",
    "retention_cycles_jours", "retention_syntheses_jours",
})

# Clés qui ne doivent JAMAIS sortir. Elles ne sont PAS dans la liste blanche ;
# cette liste sert AUSSI au masquage des textes (messages d'erreur).
CLES_INTERDITES = frozenset({
    "sid", "session", "sessionid", "token", "access_token", "refresh_token",
    "password", "passwd", "pwd", "secret", "client_secret", "apikey",
    "api_key", "authorization", "cookie", "set-cookie", "user", "login",
    "psw", "jwt", "signature", "sign", "access_hash", "imei", "hwid", "gsm",
})

_RE_URL_QUERY = re.compile(r"\?[^\s\"']*")
_RE_SECRET = re.compile(
    r"(?i)\b(sid|session|sessionid|token|access_token|refresh_token|password|"
    r"passwd|pwd|secret|client_secret|apikey|api_key|authorization|cookie|"
    r"user|login|psw|jwt|signature|sign|access_hash|imei|hwid|gsm)"
    r"\b\s*([=:])\s*[^\s,;&\"']*")


def texte_trace_sur(valeur, max_len: int = 120):
    """Texte PUBLIABLE : URL réduite à sa partie avant « ? », tout `clé=valeur`
    sensible masqué, espaces normalisés, longueur bornée.

    M1 uniquement (nouvelles traces `collecte.*`). Ne lève JAMAIS : en cas de
    doute la valeur est REFUSÉE (None) plutôt que publiée en clair.
    """
    if valeur is None:
        return None
    try:
        texte = valeur if isinstance(valeur, str) else str(valeur)
        texte = _RE_URL_QUERY.sub("?<masqué>", texte)
        texte = _RE_SECRET.sub(
            lambda m: f"{m.group(1)}{m.group(2)}<masqué>", texte)
        return " ".join(texte.split())[:max_len]
    except Exception:
        return None


def champs_publiables(details):
    """LISTE BLANCHE : ne laisse passer que `CHAMPS_TRACE_PUBLIABLES`.

    · clé hors liste ou interdite → SUPPRIMÉE (jamais publiée « au cas où ») ;
    · texte → `texte_trace_sur` (URL complète et secrets neutralisés) ;
    · booléen / nombre / None → conservé tel quel ;
    · liste (jours, couvre_sources) → bornée à 32 éléments, textes assainis ;
    · dict / objet → REFUSÉ (aucun `details` libre ne peut entrer en base).
    """
    sortie = {}
    for cle, valeur in (details or {}).items():
        if cle not in CHAMPS_TRACE_PUBLIABLES or cle.lower() in CLES_INTERDITES:
            continue
        if isinstance(valeur, str):
            sortie[cle] = texte_trace_sur(valeur)
        elif valeur is None:
            sortie[cle] = None
        elif isinstance(valeur, (bool, int, float)):
            sortie[cle] = valeur
        elif isinstance(valeur, (list, tuple)):
            sortie[cle] = [texte_trace_sur(v) for v in list(valeur)[:32]]
    return sortie


def _horodatage() -> str:
    """Libellé lisible local (diagnostic uniquement)."""
    return datetime.now().isoformat(timespec="seconds")


# ════════════════════════════════════════════════════════════════════════════
# 1. VERROU À POSSESSION PAR JETON
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Possession:
    """Jeton de possession d'un verrou — NON interchangeable, NON falsifiable."""

    ressource: str
    source: str
    jeton: str
    generation: int
    pris_le: str
    pris_mono: float
    expire_mono: float

    def duree_s(self, maintenant: float | None = None) -> float:
        return round((maintenant if maintenant is not None else time.monotonic()) - self.pris_mono, 1)

    def expire_dans_s(self, maintenant: float | None = None) -> float:
        return round(self.expire_mono - (maintenant if maintenant is not None else time.monotonic()), 1)

    def est_expiree(self, maintenant: float | None = None) -> bool:
        # `maintenant or ...` retombait sur l'horloge courante quand on passait
        # 0.0 — un instant explicite doit toujours être respecté.
        t = time.monotonic() if maintenant is None else maintenant
        return t >= self.expire_mono


class VerrouPossede:
    """Un verrou par RESSOURCE, à jeton de possession.

    Règles garanties (testées dans `test_concurrence_verrous_v154.py`) :
      - une seconde acquisition N'ÉCRASE JAMAIS la possession en cours ;
      - l'expiration est EXPLICITE : elle est journalisée avec le nom du
        propriétaire sortant, comptée, et invalide son jeton (génération) ;
      - seule la possession courante (jeton + génération) peut libérer ;
      - toute tentative de libération par un autre jeton est REFUSÉE et comptée.
    """

    def __init__(self, ressource: str, duree_defaut_s: float = 30.0) -> None:
        self.ressource = ressource
        self.duree_defaut_s = float(duree_defaut_s)
        self._mutex = threading.RLock()
        self._possession: Possession | None = None
        self._generation = 0
        self._derniere_activite_mono: float | None = None
        self._expirations = 0
        self._refus = 0
        self._liberations_refusees = 0
        self._expiration_explicite = False

    # ------------------------------------------------------------ acquisition
    def acquerir(self, source: str, duree_s: float | None = None) -> Possession | None:
        """Acquiert la ressource. Renvoie le JETON de possession, ou None si
        une autre tâche la détient (jamais d'écrasement silencieux)."""
        with self._mutex:
            maintenant = time.monotonic()
            if self._possession is not None:
                if not self._possession.est_expiree(maintenant):
                    self._refus += 1
                    return None
                # Expiration EXPLICITE (jamais silencieuse) : le propriétaire
                # sortant est nommé et son jeton devient définitivement invalide
                # (la génération augmente ci-dessous).
                self._expirations += 1
                log.warning(
                    "Verrou « %s » : possession de « %s » EXPIRÉE après %.1fs "
                    "(jeton %s, génération %d) — reprise explicite par « %s »",
                    self.ressource, self._possession.source,
                    self._possession.duree_s(maintenant),
                    self._possession.jeton[:8], self._possession.generation, source)
                self._possession = None
                self._expiration_explicite = True
            duree = float(duree_s if duree_s is not None else self.duree_defaut_s)
            self._generation += 1
            possession = Possession(
                ressource=self.ressource, source=source,
                jeton=uuid.uuid4().hex, generation=self._generation,
                pris_le=_horodatage(), pris_mono=maintenant,
                expire_mono=maintenant + max(0.05, duree))
            self._possession = possession
            self._derniere_activite_mono = maintenant
            return possession

    # -------------------------------------------------------------- activité
    def activite(self, possession: Possession, prolonger_s: float | None = None) -> bool:
        """Signale une activité (santé du verrou) et prolonge l'expiration.
        Refusé si le jeton n'est pas celui de la possession courante."""
        with self._mutex:
            if not self._est_proprietaire(possession):
                return False
            maintenant = time.monotonic()
            self._derniere_activite_mono = maintenant
            if prolonger_s:
                self._possession = Possession(
                    **{**possession.__dict__,
                       "expire_mono": maintenant + max(0.05, float(prolonger_s))})
            return True

    # -------------------------------------------------------------- libération
    def liberer(self, possession: Possession | None) -> bool:
        """Libère SEULEMENT si `possession` est la possession courante
        (jeton + génération). Un jeton étranger est refusé et compté."""
        with self._mutex:
            if possession is None:
                return False
            if not self._est_proprietaire(possession):
                self._liberations_refusees += 1
                courant = self._possession
                log.error(
                    "Verrou « %s » : libération REFUSÉE — « %s » (jeton %s, gén. %d) "
                    "n'est pas propriétaire (possession courante : « %s », jeton %s, gén. %s)",
                    self.ressource, possession.source, possession.jeton[:8],
                    possession.generation,
                    courant.source if courant else "aucune",
                    (courant.jeton[:8] if courant else "—"),
                    (courant.generation if courant else "—"))
                return False
            self._possession = None
            self._derniere_activite_mono = time.monotonic()
            self._expiration_explicite = False
            return True

    def _est_proprietaire(self, possession: Possession | None) -> bool:
        if possession is None or self._possession is None:
            return False
        return (self._possession.jeton == possession.jeton
                and self._possession.generation == possession.generation
                and self._possession.ressource == possession.ressource)

    # ---------------------------------------------------- expiration explicite
    def expirer(self, raison: str = "expiration") -> Possession | None:
        """Expiration EXPLICITE (surveillance ou administration) : renvoie la
        possession sortante, invalide son jeton, et NE remplace pas la
        possession — la prochaine acquisition créera un jeton neuf."""
        with self._mutex:
            sortante = self._possession
            if sortante is None:
                return None
            self._expirations += 1
            self._generation += 1          # tout jeton antérieur devient caduc
            self._possession = None
            self._expiration_explicite = True
            log.warning("Verrou « %s » expiré explicitement (%s) — propriétaire « %s » "
                        "(%.1fs), jeton %s invalidé",
                        self.ressource, raison, sortante.source,
                        sortante.duree_s(), sortante.jeton[:8])
            return sortante

    def forcer(self, raison: str = "manuel") -> bool:
        """Alias administratif (API /api/sante/reset-verrou)."""
        return self.expirer(raison=raison) is not None

    # ------------------------------------------------------------------ état
    def etat(self) -> dict:
        with self._mutex:
            maintenant = time.monotonic()
            p = self._possession
            return {
                "ressource": self.ressource,
                "occupe": p is not None,
                "proprietaire": p.source if p else None,
                "jeton": (p.jeton[:8] if p else None),
                "generation": self._generation,
                "pris_le": p.pris_le if p else None,
                "derniere_activite": (
                    datetime.fromtimestamp(
                        time.time() - (maintenant - self._derniere_activite_mono)
                    ).isoformat(timespec="seconds")
                    if self._derniere_activite_mono else None),
                "duree_s": p.duree_s(maintenant) if p else None,
                "expire_dans_s": p.expire_dans_s(maintenant) if p else None,
                "expirations": self._expirations,
                "refus": self._refus,
                "liberations_refusees": self._liberations_refusees,
            }

    @property
    def occupe(self) -> bool:
        with self._mutex:
            return self._possession is not None

    @property
    def proprietaire(self) -> str | None:
        with self._mutex:
            return self._possession.source if self._possession else None


# ─────────────────────────────────────────────────────────── registre global
_VERROUS: dict[str, VerrouPossede] = {}
_VERROUS_MUTEX = threading.Lock()
N1_RESSOURCES = ("MZONEX", "CAMTRACKPRO", "MZONEX_RELECTURE")


def est_n2(ressource: str) -> bool:
    return ressource.startswith("N2_")


def verrou_de(ressource: str, duree_defaut_s: float = 30.0) -> VerrouPossede:
    """Verrou d'une ressource (créé à la demande, stable ensuite)."""
    with _VERROUS_MUTEX:
        v = _VERROUS.get(ressource)
        if v is None:
            v = VerrouPossede(ressource, duree_defaut_s=duree_defaut_s)
            _VERROUS[ressource] = v
        return v


def etat_verrous() -> dict[str, dict]:
    with _VERROUS_MUTEX:
        verrous = dict(_VERROUS)
    return {nom: v.etat() for nom, v in verrous.items()}


def etat_famille(n2: bool) -> dict:
    """État agrégé d'une famille (N1 ou N2) — pour `/api/sante`.
    `acquis_par` liste les sources qui détiennent un verrou de la famille."""
    etats = {n: e for n, e in etat_verrous().items() if est_n2(n) == n2}
    detenteurs = sorted(e["proprietaire"] for e in etats.values() if e["occupe"])
    durees = [e["duree_s"] for e in etats.values()
              if e["occupe"] and e["duree_s"] is not None]
    return {
        "occupe": bool(detenteurs),
        "acquis_par": detenteurs,
        "duree_s": (max(durees) if durees else None),
        "par_source": etats,
        "expirations": sum(e["expirations"] for e in etats.values()),
        "liberations_refusees": sum(e["liberations_refusees"] for e in etats.values()),
    }


def forcer_famille(n2: bool, raison: str = "manuel") -> bool:
    with _VERROUS_MUTEX:
        verrous = dict(_VERROUS)
    touche = False
    for nom, v in verrous.items():
        if est_n2(nom) == n2:
            touche = v.forcer(raison=raison) or touche
    return touche


# ════════════════════════════════════════════════════════════════════════════
# 2. BUDGET DE PASSE + POINTS D'ARRÊT CONTRÔLÉS
# ════════════════════════════════════════════════════════════════════════════
ETAPES = ("verrou", "authentification", "attente_http", "pagination",
          "vehicule", "parsing", "ecriture", "inconnue")


class BudgetDepasse(Exception):
    """La passe a atteint son échéance.

    R14 (21/09/2026) — QUATRE INFORMATIONS SÉPARÉES, jamais confondues :
      · `etape`             : la PHASE réelle où le temps a été passé
                              (écriture, attente_http, pagination…) ;
      · `raison_annulation`  : le MOTIF de l'arrêt (surveillance, échéance
                              sans point d'arrêt…), quand il y en a un ;
      · `issue`              : l'issue de la passe (« BUDGET_DEPASSE ») ;
      · `etape_brute`        : le libellé d'origine, conservé même s'il n'est
                              pas une phase connue (plus de perte silencieuse :
                              un ancien libellé `surveillance_...` affichait
                              « inconnue » alors que la phase était connue).
    """

    def __init__(self, etape: str = "inconnue", ecoule_s: float = 0.0,
                 budget_s: float = 0.0, source: str | None = None,
                 raison_annulation: str | None = None,
                 etape_connue: str | None = None) -> None:
        self.etape_brute = str(etape)
        # La PHASE ne se perd jamais : « inconnue » et les motifs de surveillance
        # ne sont PAS des phases — on retient alors celle de la métrique
        # (`etape_connue`), qui est toujours mesurée. Avant, le motif
        # `surveillance_limite_depassee` était retenu comme phase puis écrasé en
        # « inconnue » : l'écran affichait « étape inconnue » pour un
        # dépassement dont la phase (écriture, attente_http…) était connue.
        fournie = etape if (etape in ETAPES and etape != "inconnue") else None
        phase = fournie or etape_connue or "inconnue"
        self.etape = phase if phase in ETAPES else "inconnue"
        self.raison_annulation = raison_annulation
        self.issue = "BUDGET_DEPASSE"
        self.ecoule_s = round(float(ecoule_s), 1)
        self.budget_s = round(float(budget_s), 1)
        self.source = source
        motif = f" (motif : {raison_annulation})" if raison_annulation else ""
        super().__init__(
            f"Budget de collecte dépassé ({self.budget_s}s, étape « {self.etape} »)"
            f"{motif} — arrêt contrôlé, replanifié au prochain cycle")


class VerrouOccupe(Exception):
    """La passe n'a pas pu démarrer : une autre tâche détient la ressource."""


@dataclass
class Metriques:
    """Métriques par étape d'une passe (publiées dans /api/sante)."""

    source: str = "?"
    total_s: float = 0.0
    auth_s: float = 0.0
    attente_http_s: float = 0.0
    pagination_s: float = 0.0
    vehicule_s: float = 0.0
    parsing_s: float = 0.0
    # M1 — LECTURE ÉCRAN (Playwright) : cette phase n'existait dans AUCUNE
    # métrique ; un repli écran de plusieurs minutes était invisible.
    ecran_s: float = 0.0
    ecriture_s: float = 0.0
    attente_verrou_s: float = 0.0
    attente_sqlite_s: float = 0.0
    nb_pages: int = 0
    nb_vehicules: int | None = 0
    nb_lignes: int = 0
    nb_lignes_lues: int = 0
    # M1 — véhicules effectivement RELEVÉS À L'ÉCRAN (Playwright) : volume lu du
    # chemin de repli, distinct de `nb_vehicules` (temps réel N1).
    nb_ecran_vehicules: int = 0
    nb_lignes_ecrites: int = 0
    nb_points_ecrits: int | None = 0
    nb_commits: int = 0
    commits_apres_echeance: int = 0
    etape_bloquante: str | None = None
    phase: str = "inconnue"
    issue: str = "EN_COURS"
    annulee: bool = False
    raison_annulation: str | None = None
    budget_s: float = 0.0
    debut_le: str | None = None
    fin_le: str | None = None
    # M1 — trace de LANCEMENT / NON-DÉMARRAGE d'une source. Aucune décision ne
    # lit ces champs : ils ne servent qu'aux traces `collecte.*`.
    demarrage_le: str | None = None
    source_demarree: bool = False
    non_demarree_raison: str | None = None
    # R19 — champs qu'une source NE PEUT PAS mesurer : publiés `null`, jamais
    # « 0 » (un 0 se lirait comme une mesure : « rien n'a été écrit »).
    _non_applicables: tuple = ()

    def _champ(self, champ: str, suffixe: str = "") -> str | None:
        """Résout le nom RÉEL du champ : `chrono("auth")` doit alimenter
        `auth_s`. Sans cette résolution, toute mesure de phase était
        silencieusement perdue (défaut trouvé par la suite v154)."""
        for candidat in (champ, f"{champ}{suffixe}"):
            if candidat and hasattr(self, candidat):
                return candidat
        return None

    @property
    def non_applicables(self) -> list:
        """Compteurs NON MESURABLES pour cette source (publiés `null`)."""
        return list(self._non_applicables)

    def marquer_non_applicable(self, *champs: str) -> None:
        """R19 — déclare des compteurs NON MESURABLES pour cette source."""
        self._non_applicables = tuple(dict.fromkeys(self._non_applicables + champs))
        for champ in self._non_applicables:
            if hasattr(self, champ):
                setattr(self, champ, None)

    def ajouter(self, champ: str, secondes: float) -> None:
        nom = self._champ(champ, "_s")
        if nom and nom not in self._non_applicables:
            setattr(self, nom, round(getattr(self, nom) + float(secondes), 3))

    def compter(self, champ: str, n: int = 1) -> None:
        nom = self._champ(champ)
        if nom and nom not in self._non_applicables:
            setattr(self, nom, int(getattr(self, nom)) + int(n))

    # ------------------------------------------------------------ M1 (traces)
    def marquer_demarrage(self, quand: str) -> None:
        """La source a été LANCÉE (idempotent). Aucune décision ne lit ce champ.
        """
        self.source_demarree = True
        self.demarrage_le = self.demarrage_le or quand

    def marquer_non_demarree(self, raison: str) -> None:
        """La source N'A PAS DÉMARRÉ, avec sa raison réelle. Ne remplace JAMAIS
        un démarrage réellement constaté."""
        if not self.source_demarree:
            self.non_demarree_raison = raison

    def depouiller(self) -> dict:
        return {c: getattr(self, c) for c in (
            "source", "total_s", "auth_s", "attente_http_s", "pagination_s",
            "vehicule_s", "parsing_s", "ecriture_s", "attente_verrou_s",
            "attente_sqlite_s", "nb_pages", "nb_vehicules", "nb_lignes",
            "nb_lignes_lues", "nb_lignes_ecrites", "nb_points_ecrits",
            "nb_commits", "commits_apres_echeance", "etape_bloquante", "phase",
            "issue", "annulee", "raison_annulation", "budget_s",
            "debut_le", "fin_le", "non_applicables",
            # M1 — AJOUTS ADDITIFS : aucune clé existante renommée ni retirée
            # (les assertions existantes sont inclusives : vérifié).
            "ecran_s", "nb_ecran_vehicules", "demarrage_le", "source_demarree",
            "non_demarree_raison")}

    def phases_s(self) -> dict:
        """Durées par phase (pour l'interface : « où est passé le temps »)."""
        return {"authentification": self.auth_s, "attente_http": self.attente_http_s,
                "pagination": self.pagination_s, "vehicule": self.vehicule_s,
                "parsing": self.parsing_s, "ecriture": self.ecriture_s,
                "attente_verrou": self.attente_verrou_s,
                "attente_sqlite": self.attente_sqlite_s,
                # M1 — phase « écran » : aucune clé existante modifiée.
                "ecran": self.ecran_s}

    def etape_dominante(self) -> str:
        """Étape qui a le plus consommé de temps — sert à nommer la cause d'un
        dépassement de budget au lieu de la classer « locale » d'office."""
        phases = {
            "authentification": self.auth_s,
            "attente_http": self.attente_http_s,
            "pagination": self.pagination_s,
            "vehicule": self.vehicule_s,
            "parsing": self.parsing_s,
            "ecriture": self.ecriture_s,
            "verrou": self.attente_verrou_s,
        }
        nom, valeur = max(phases.items(), key=lambda it: it[1])
        return nom if valeur > 0 else "inconnue"


class PasseCourante:
    """Budget + métriques de la passe en cours DANS CE FIL D'EXÉCUTION."""

    def __init__(self, source: str, budget_s: float,
                 debut_le: str | None = None) -> None:
        self.source = source
        self.budget_s = float(budget_s)
        self.debut_mono = time.monotonic()
        self.debut_le = debut_le
        self.metriques = Metriques(source=source, budget_s=float(budget_s),
                                   debut_le=debut_le)
        self.annulee = False
        self.annulee_raison: str | None = None
        self.etape_courante = "inconnue"
        # M1 — sources RÉELLEMENT lancées dans cette passe (traçabilité : sert à
        # déclarer honnêtement `compteurs_partages`). Aucune décision ne lit
        # cette liste ; elle appartient à CETTE passe, donc à CE fil.
        self.sources_lancees: list[str] = []
        # R19 — compteurs qu'une source ne peut pas mesurer (publiés `null`).
        if source.startswith("N2_"):
            self.metriques.marquer_non_applicable("nb_vehicules",
                                                  "nb_points_ecrits")
        elif source in ("MZONEX", "MZONEX_RELECTURE"):
            self.metriques.marquer_non_applicable("nb_vehicules")

    # ----------------------------------------------------------- échéance
    def ecoule_s(self) -> float:
        return time.monotonic() - self.debut_mono

    def restant_s(self) -> float:
        return self.budget_s - self.ecoule_s()

    def instantane(self) -> dict:
        """R9/R2 — la PASSE EN COURS, décrite séparément de la dernière passe
        publiée : `fin = null`, `issue = "en_cours"`, durée courante."""
        d = self.metriques.depouiller()
        d.update({"total_s": round(self.ecoule_s(), 3), "fin_le": None,
                  "issue": "en_cours", "phase": self.etape_courante,
                  "annulee": self.annulee,
                  "raison_annulation": self.annulee_raison,
                  "budget_s": self.budget_s, "debut_le": self.debut_le,
                  "restant_s": round(self.restant_s(), 3)})
        return d

    def depassee(self) -> bool:
        return self.annulee or self.restant_s() <= 0

    def verifier(self, etape: str = "inconnue") -> None:
        """Point d'arrêt contrôlé : lève BudgetDepasse si l'échéance est
        atteinte. À appeler AVANT **et APRÈS** chaque travail coûteux (appel
        réseau, lot, unité d'écriture, clôture) : c'est ce qui garantit
        qu'aucune écriture ne commence après l'échéance.

        R14 — la phase est TOUJOURS transmise (`etape`) ; le motif d'annulation
        voyage dans un champ distinct. Avant le 21/09, la raison d'annulation
        REMPLAÇAIT la phase, et le statut affichait « étape inconnue » alors que
        la métrique connaissait la phase.
        """
        self.etape_courante = etape
        self.metriques.phase = etape
        if self.depassee():
            self.annulee = True
            self.metriques.etape_bloquante = etape
            self.metriques.annulee = True
            raise BudgetDepasse(etape, self.ecoule_s(), self.budget_s,
                                source=self.source,
                                raison_annulation=self.annulee_raison)

    def annuler(self, raison: str = "annulation") -> None:
        """R14 — l'annulation ne fait que DEMANDER l'arrêt : elle ne remplace
        jamais la phase (celle-ci reste celle du dernier point d'arrêt)."""
        self.annulee = True
        self.annulee_raison = raison
        self.metriques.annulee = True
        self.metriques.raison_annulation = raison

    # ----------------------------------------------------------- clôture
    def terminer(self, issue: str, fin_le: str | None = None) -> dict:
        self.metriques.total_s = round(self.ecoule_s(), 3)
        self.metriques.issue = issue
        self.metriques.fin_le = fin_le
        if self.metriques.etape_bloquante is None and self.annulee:
            self.metriques.etape_bloquante = self.annulee_raison
        if self.metriques.etape_bloquante is None:
            self.metriques.etape_bloquante = self.metriques.etape_dominante()
        # R14 — la phase publiée est TOUJOURS connue quand elle peut l'être :
        # « inconnue » est réservé au cas où aucune phase n'a été mesurée.
        if self.metriques.phase in ("inconnue", "", None):
            self.metriques.phase = self.metriques.etape_bloquante
        self.metriques.annulee = self.annulee
        self.metriques.raison_annulation = self.annulee_raison
        return self.metriques.depouiller()


# ────────────────────────────────────────────────── passe courante (par fil)
_COURANT = threading.local()
_DERNIERES_METRIQUES: dict[str, dict] = {}
_CUMUL: dict[str, dict] = {}
_METRIQUES_MUTEX = threading.Lock()


def mesurer(champ: str, depuis_mono: float | None) -> None:
    """Ajoute au compteur `champ` le temps écoulé depuis `depuis_mono`.

    Utile quand l'entourage d'un bloc par `with chrono(...)` n'est pas possible
    sans réindenter un long corps de boucle (ex. : traitement par véhicule)."""
    if depuis_mono is None:
        return
    passe = passe_courante()
    if passe is not None:
        passe.metriques.ajouter(champ, time.monotonic() - depuis_mono)


def mesurer_depuis(champ: str, depuis_mono: float | None) -> float:
    """M1 — clôt la mesure ouverte depuis `depuis_mono` et rend un NOUVEAU départ.

    Pourquoi : avec `mesurer(champ, t)` où `t` valait `None`, AUCUNE mesure ne
    s'ouvrait (le tour était perdu), et la DERNIÈRE unité d'une boucle n'était
    jamais clôturée. Ici :
      · jamais d'appel interne avec `None` (garde explicite, testé) ;
      · clôture possible dans un `finally` (mesure même en exception) ;
      · la valeur rendue ouvre l'unité SUIVANTE : chaque intervalle est mesuré
        une seule fois (aucun double comptage).
    Ne lève jamais et n'ajoute rien hors passe (`passe_courante()` est None).
    """
    if depuis_mono is not None:
        passe = passe_courante()
        if passe is not None:
            passe.metriques.ajouter(champ, time.monotonic() - depuis_mono)
    return time.monotonic()


def passe_courante() -> PasseCourante | None:
    return getattr(_COURANT, "passe", None)


def restant_budget(defaut: float | None = None) -> float | None:
    """R3 — temps restant de la passe courante (None hors passe).

    Sert à dimensionner un appel réseau sur le temps RESTANT : un timeout HTTP
    de 30 s sur une passe qui n'a plus que 2 s garantit un dépassement.
    """
    passe = passe_courante()
    return None if passe is None else passe.restant_s()


# ════════════════════════════════════════════════════════════════════════════
# 2bis. CYCLES **PAR SOURCE** (R18) — jamais de comparaison croisée
# ════════════════════════════════════════════════════════════════════════════
# Avant : `dernier_cycle_debut` et `dernier_cycle_fin` étaient DEUX CLÉS
# GLOBALES écrites par toutes les passes. Deux passes concurrentes (MZONEX et
# CAMTRACKPRO) produisaient une « fin antérieure au début » qui ne signalait
# rien de réel — et faisait croire à un cycle terminé avant d'avoir commencé.
_CYCLES: dict[str, dict] = {}
_CYCLES_MUTEX = threading.Lock()


def ouvrir_cycle(source: str, quand: str) -> None:
    with _CYCLES_MUTEX:
        _CYCLES[source] = {"source": source, "debut": quand, "fin": None,
                           "en_cours": True, "issue": "en_cours"}


def fermer_cycle(source: str, quand: str, issue: str) -> None:
    with _CYCLES_MUTEX:
        cycle = _CYCLES.setdefault(source, {"source": source, "debut": None})
        cycle.update({"fin": quand, "en_cours": False, "issue": issue})


def cycles_par_source() -> dict[str, dict]:
    """Cycles par source + verdict d'inversion CALCULÉ PAR SOURCE.

    R18 — `CYCLES_EN_ECHEC` ne peut naître que de la comparaison du début et de
    la fin d'UN MÊME cycle : jamais du début de l'une et de la fin de l'autre.
    Une passe en cours publie `fin = null` (R9) — aucune date de fin inventée.
    """
    with _CYCLES_MUTEX:
        cycles = {source: dict(c) for source, c in _CYCLES.items()}
    en_echec = []
    for source, cycle in cycles.items():
        debut, fin = cycle.get("debut"), cycle.get("fin")
        duree = None
        if debut and fin:
            try:
                duree = round((datetime.fromisoformat(fin)
                               - datetime.fromisoformat(debut)).total_seconds(), 3)
            except ValueError:
                duree = None
        cycle["duree_s"] = duree
        inversion = bool(duree is not None and duree < 0)
        cycle["inversion"] = inversion
        if inversion:
            en_echec.append(source)
        elif cycle.get("en_cours"):
            en_echec.append(source) if cycle.get("issue") == "ECHEC" else None
    return {"cycles": cycles, "sources_en_echec_cycle": sorted(en_echec),
            "cycles_en_echec": bool(en_echec)}


# ════════════════════════════════════════════════════════════════════════════
# 2ter. PROGRESSION DE LA RELECTURE HISTORIQUE (R10/R11)
# ════════════════════════════════════════════════════════════════════════════
_PROGRESSION: dict[str, dict] = {}
_PROGRESSION_MUTEX = threading.Lock()


def enregistrer_progression(source: str, *, journee=None, page=None,
                            vehicule=None, dernier_id=None,
                            unite_traitee: bool = False) -> None:
    """Mémorise où en est la relecture : journée, page, véhicule, dernier
    identifiant traité. La reprise et l'anti-rejeu s'appuient dessus."""
    with _PROGRESSION_MUTEX:
        etat = _PROGRESSION.setdefault(source, {
            "source": source, "journee": None, "page": 0, "vehicule": None,
            "dernier_id": None, "unites_traitees": 0, "maj_le": None})
        for cle, valeur in (("journee", journee), ("page", page),
                            ("vehicule", vehicule), ("dernier_id", dernier_id)):
            if valeur is not None:
                etat[cle] = valeur
        if unite_traitee:
            etat["unites_traitees"] = int(etat["unites_traitees"]) + 1
        etat["maj_le"] = datetime.now().isoformat(timespec="seconds")


def progression(source: str | None = None) -> dict:
    with _PROGRESSION_MUTEX:
        if source is not None:
            return dict(_PROGRESSION.get(source) or {})
        return {k: dict(v) for k, v in _PROGRESSION.items()}


@contextmanager
def activer_passe(passe: PasseCourante):
    """Installe la passe pour CE fil (les passes concurrentes ne se mélangent
    pas : chaque fil a la sienne)."""
    ancienne = getattr(_COURANT, "passe", None)
    _COURANT.passe = passe
    try:
        yield passe
    finally:
        _COURANT.passe = ancienne


def publier_metriques(depouillees: dict) -> None:
    """Publie la DERNIÈRE passe ET met à jour le CUMUL de la source.

    R6 (21/09/2026) — les trois mesures ne doivent JAMAIS être confondues :
      · `total_s` de la dernière passe  = durée de CETTE passe ;
      · `cumul.total_s`                 = somme des passes terminées ;
      · `cumul.moyenne_s`               = cumul / nombre de passes.
    """
    source = depouillees.get("source", "?")
    with _METRIQUES_MUTEX:
        _DERNIERES_METRIQUES[source] = depouillees
        cumul = _CUMUL.setdefault(source, {
            "passes": 0, "total_s": 0.0, "issues": {},
            "derniere_reussie": None, "dernier_echec": None})
        cumul["passes"] += 1
        cumul["total_s"] = round(cumul["total_s"] + float(depouillees.get("total_s") or 0), 3)
        issue = str(depouillees.get("issue") or "?")
        cumul["issues"][issue] = int(cumul["issues"].get(issue, 0)) + 1
        instant = depouillees.get("fin_le") or depouillees.get("debut_le")
        if issue == "TERMINE":
            cumul["derniere_reussie"] = {"fin_le": instant, "debut_le": depouillees.get("debut_le"),
                                         "total_s": depouillees.get("total_s"),
                                         "budget_s": depouillees.get("budget_s"),
                                         "nb_lignes_ecrites": depouillees.get("nb_lignes_ecrites"),
                                         "nb_pages": depouillees.get("nb_pages")}
        else:
            cumul["dernier_echec"] = {"fin_le": instant, "issue": issue,
                                      "etape": depouillees.get("etape_bloquante")}


def cumul_publie(source: str | None = None) -> dict:
    with _METRIQUES_MUTEX:
        if source is not None:
            return dict(_CUMUL.get(source) or {})
        return {k: dict(v) for k, v in _CUMUL.items()}


def metriques_publiees() -> dict[str, dict]:
    with _METRIQUES_MUTEX:
        return {k: dict(v) for k, v in _DERNIERES_METRIQUES.items()}


# ─────────────────── passes en cours (surveillance NON intrusive) ───────────
# La surveillance ne VOLE JAMAIS un verrou : si une passe dépasse sa limite,
# elle est ANNULÉE de façon coopérative (elle s'arrête à son prochain point
# d'arrêt et libère elle-même son jeton). C'est ce qui évite la double écriture
# qu'occasionnait l'ancienne « auto-libération » par échange d'objet.
_PASSES: dict[str, PasseCourante] = {}
_PASSES_MUTEX = threading.Lock()


def enregistrer_passe(ressource: str, passe: PasseCourante) -> None:
    with _PASSES_MUTEX:
        _PASSES[ressource] = passe


def retirer_passe(ressource: str, passe: PasseCourante | None = None) -> None:
    with _PASSES_MUTEX:
        courante = _PASSES.get(ressource)
        if courante is not None and (passe is None or courante is passe):
            _PASSES.pop(ressource, None)


def passe_de(ressource: str) -> PasseCourante | None:
    with _PASSES_MUTEX:
        return _PASSES.get(ressource)


def passes_en_cours() -> dict[str, dict]:
    """R9 — une passe EN COURS : `fin = null`, `issue = "en_cours"`, durée
    courante. Utilisé par la surveillance et publié par /api/sante."""
    with _PASSES_MUTEX:
        return {r: p.instantane() for r, p in _PASSES.items()}


# ─────────────────────────── aides utilisées par le code instrumenté
@contextmanager
def chrono(champ: str):
    """Mesure une phase (auth, pagination, parsing, écriture…). Sans passe
    active, ne fait rien (les outils hors collecte restent inchangés)."""
    passe = passe_courante()
    if passe is None:
        yield
        return
    t0 = time.monotonic()
    try:
        yield
    finally:
        passe.metriques.ajouter(champ, time.monotonic() - t0)


def compter(champ: str, n: int = 1) -> None:
    passe = passe_courante()
    if passe is not None:
        passe.metriques.compter(champ, n)


def verifier_etape(etape: str) -> None:
    passe = passe_courante()
    if passe is not None:
        passe.verifier(etape)


def commit_autorise() -> bool:
    """R4 — l'échéance interdit de COMMENCER une écriture.

    Appelé juste avant chaque `commit()` : si la passe a dépassé son échéance,
    l'écriture n'est pas engagée (la donnée est reposée et rejouée au cycle
    suivant — idempotence). Le refus est COMPTÉ (`commits_apres_echeance`) et le
    point d'arrêt lève `BudgetDepasse` : la passe s'arrête proprement.
    """
    passe = passe_courante()
    if passe is None:
        return True
    if passe.depassee():
        passe.metriques.commits_apres_echeance += 1
        passe.annulee = True
        passe.verifier(passe.etape_courante or "ecriture")
        return False
    return True


# ════════════════════════════════════════════════════════════════════════════
# 3. CLASSIFICATION FINE DES ÉCHECS (le statut ne doit plus tout confondre)
# ════════════════════════════════════════════════════════════════════════════
CLASSE_PORTAIL_INDISPONIBLE = "portail_indisponible"
CLASSE_PORTAIL_LENT = "portail_lent"
CLASSE_ATTENTE_SQLITE = "attente_sqlite"
CLASSE_VERROU_OCCUPE = "verrou_occupe"
CLASSE_BUDGET_DEPASSE = "budget_depasse"
CLASSE_CONFIGURATION_ABSENTE = "configuration_absente"
CLASSE_COLLECTE_EN_COURS = "collecte_en_cours"
CLASSE_COLLECTE_ECHOUEE = "collecte_echouee"

CLASSES = (CLASSE_PORTAIL_INDISPONIBLE, CLASSE_PORTAIL_LENT, CLASSE_ATTENTE_SQLITE,
           CLASSE_VERROU_OCCUPE, CLASSE_BUDGET_DEPASSE, CLASSE_CONFIGURATION_ABSENTE,
           CLASSE_COLLECTE_EN_COURS, CLASSE_COLLECTE_ECHOUEE)

MOTIFS_SQLITE = ("database is locked", "database table is locked", "sqlite_busy",
                 "database is busy", "disk i/o error", "no space left",
                 "readonly database", "read-only database",
                 "attempt to write a readonly", "unable to open database")
MOTIFS_CONFIGURATION = ("mzonex_user", "mzonex_password", "absents de backend/.env",
                        "jeton non configuré", "token absent", "non configuré",
                        "not configured", "aucun groupe de véhicules publié")
MOTIFS_PORTAIL_INDISPONIBLE = ("httpx.connecterror", "connecterror", "tls/ssl",
                               "name or service not known", "temporary failure in name resolution",
                               "connection refused", "connection reset", "http 401", "http 403",
                               "http 500", "http 502", "http 503", "http 504", "injoignable",
                               "erreurauth", "erreurapi", "timeout après")

# Étapes d'une passe dont le dépassement n'est PAS un défaut de notre
# infrastructure : le portail (ou le réseau) est lent. C'est exactement ce que
# l'ancien code classait « locale » d'office.
ETAPES_PORTAIL = ("authentification", "attente_http", "pagination", "vehicule")
ETAPES_LOCALES = ("ecriture", "verrou")


def classer_erreur(exc: BaseException) -> dict:
    """Classe FINE d'un échec + catégorie historique (« locale »/« portail »).

    Le statut de santé s'appuie sur la CLASSE : un dépassement de budget passé
    à attendre le portail n'est plus annoncé comme un problème local.
    """
    texte = f"{type(exc).__name__}: {exc}".lower()
    etape = getattr(exc, "etape", None)

    if isinstance(exc, VerrouOccupe):
        return {"classe": CLASSE_VERROU_OCCUPE, "categorie": "locale",
                "etape": "verrou"}
    if isinstance(exc, BudgetDepasse):
        # R14 — UN DÉPASSEMENT DE BUDGET EST UNE **ISSUE**, PAS UNE CAUSE.
        # Quelle que soit la phase où le temps a été consommé (attente du
        # portail, écriture, verrou), la classe publiée reste « budget_depasse » ;
        # la PHASE, elle, est publiée à part (`etape`). Avant le 21/09, un
        # dépassement survenu pendant la phase d'écriture était annoncé
        # « attente_sqlite » : l'écran affichait « collecte bloquée localement —
        # base verrouillée » alors que la base n'y était pour rien.
        return {"classe": CLASSE_BUDGET_DEPASSE,
                "categorie": ("locale" if etape in ETAPES_LOCALES else "portail"),
                "etape": etape}
    if any(m in texte for m in MOTIFS_SQLITE):
        return {"classe": CLASSE_ATTENTE_SQLITE, "categorie": "locale",
                "etape": etape}
    if any(m in texte for m in MOTIFS_CONFIGURATION):
        return {"classe": CLASSE_CONFIGURATION_ABSENTE, "categorie": "portail",
                "etape": etape}
    if any(m in texte for m in MOTIFS_PORTAIL_INDISPONIBLE):
        return {"classe": CLASSE_PORTAIL_INDISPONIBLE, "categorie": "portail",
                "etape": etape}
    return {"classe": CLASSE_COLLECTE_ECHOUEE, "categorie": "portail",
            "etape": etape}


def distinguer_erreur_locale(texte_erreur: str) -> bool:
    """Compatibilité : « l'échec vient-il de NOTRE infrastructure ? »"""
    t = (texte_erreur or "").lower()
    return (any(m in t for m in MOTIFS_SQLITE)
            or "budget de collecte" in t
            or "verrou" in t and "occupe" in t)
