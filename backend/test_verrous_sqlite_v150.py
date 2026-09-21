"""Tests v1.50 — « database is locked » : cause LOCALE, jamais une panne portail.

Capture `/api/sante` du 18/09/2026 (10:08, MZoneX rétabli) :

    statut : COLLECTE_DEGRADEE
    sources_en_echec : ["MZONEX", "MZONEX_RELECTURE"]
    MZONEX → derniere_erreur : OperationalError: (sqlite3.OperationalError)
              database is locked [SQL: INSERT INTO collecte_checkpoints ...]
    MZONEX_RELECTURE → derniere_erreur : Timeout dur de 30.0s dépassé

Le portail répondait. Les deux échecs venaient de NOTRE base. Quatre défauts :

  [TD-120] BEGIN DIFFÉRÉ (piège WAL « lire puis écrire »)
           Pysqlite ouvrait chaque transaction en `BEGIN` différé : les SELECT
           (véhicules, idempotence, suivi) précèdent le premier INSERT, et si
           une autre connexion écrit entre-temps, SQLite refuse DÉFINITIVEMENT
           la montée en écriture (SQLITE_BUSY_SNAPSHOT). Le pilote réessaie
           jusqu'à son `timeout` (30 s) puis rend « database is locked ».
           Repro (laboratoire) : la relecture MZoneX mourait sur son propre
           `UPDATE vehicules`, pas sur le checkpoint.
           → corrigé par `BEGIN IMMEDIATE` (recette canonique SQLAlchemy/
           pysqlite) : les écrivains font la QUEUE et le busy_timeout s'applique.
  [TD-121] TRANSACTIONS TROP LONGUES
           Tout un lot vivait dans UNE transaction : mesuré 10 000 points =
           10,9 s de verrou (≈ 44 s pour une fenêtre d'arrêt de 25 h), au-delà
           du busy_timeout de 30 s.
           → commit par lots (LOT_INSERTION = 250) + rejeu idempotent du lot.
  [TD-122] LA TRACE FAISAIT TOMBER LA COLLECTE
           `_checkpoint_ouvre` s'exécute AVANT l'appel au portail : un verrou
           sur cet INSERT avortait TOUTE la passe N1 (source déclarée en panne
           sans avoir interrogé le portail).
           → une trace ne commande pas la collecte : échec journalisé, None.
  [TD-123] UN TIMEOUT LAISSAIT UN ORPHELIN
           `_collecte_protegee` abandonnait l'attente, mais le thread
           poursuivait son insertion — et tenait le verrou d'écriture —
           pendant que le cycle suivant démarrait (cascade).
           → la passe est laissée se terminer (bornée) ; son dépassement est
           classé « locale » (notre budget), pas « portail ».

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v150.db" python3 test_verrous_sqlite_v150.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta

from app import engine
engine.PUBLISH_ENABLED["on"] = False

from app import scrapers
from app.database import SessionLocal, pragmas_sqlite
from app.main import migrer_schema
from app.models import EvenementGPS, Vehicule
from app.seed import seed_si_vide
from app.scrapers import MZoneXApiCollector

R = {"ok": 0, "ko": 0}


def check(nom, cond, info=""):
    if cond:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} {info}")


db_url = os.environ.get("DATABASE_URL", "")
if "/tmp/" not in db_url and "test" not in db_url:
    print("⛔ Sécurité : lancez ce test avec DATABASE_URL pointant une base de "
          "test — jamais la base de production.")
    sys.exit(2)
chemin_fichier = db_url.replace("sqlite:///", "")

seed_si_vide()
migrer_schema()
db = SessionLocal()
plaque = db.query(Vehicule).first().plaque
db.close()

print("\n═══ [TD-120] réglages SQLite réellement en vigueur ═══")
pr = pragmas_sqlite()
check("journal WAL actif", str(pr.get("journal_mode", "")).lower() == "wal", pr)
check("busy_timeout ≥ 30000 ms", int(pr.get("busy_timeout") or 0) >= 30000, pr)

print("\n═══ [TD-121] une transaction de lot est COURTE ═══")
N = 6000
base = datetime(2026, 9, 18, 4, 0, 0)
payload = [{"gps_associe": plaque, "horodatage": base + timedelta(seconds=3 * i),
            "lat": -18.9 + i * 1e-6, "lng": 47.5, "adresse": None,
            "vitesse": 40.0, "moteur": "ON", "type_evenement": None}
           for i in range(N)]

# ════════════════════════════════════════════════════════════════════════════
# v1.54 (21/09/2026) — MESURE DÉTERMINISTE, PAR ÉVÉNEMENTS (aucun `sleep`,
# aucun seuil dépendant de la charge de la machine).
#
# Ce qui rendait cette suite instable : l'ancien instrument ÉCHANTILLONNAIT le
# verrou toutes les 10 ms (`time.sleep(0.01)`) et convertissait un nombre
# d'échantillons en millisecondes, puis comparait ce total à 1 s. Sous charge,
# deux échantillons consécutifs peuvent s'espacer de bien plus que 10 ms — la
# mesure « dérivait » sans qu'aucun comportement du produit n'ait changé. Le
# « < 5 s » de l'écrivain concurrent dépendait, lui, du moment où le back-off
# interne de SQLite tombait sur une fenêtre libre.
#
# Nouveau protocole (LOCK-STEP) : la suite N'ATTEND PAS une fenêtre au hasard,
# elle l'OFFRE et attend qu'elle soit prise. Après chaque lot committé, elle
# ouvre une fenêtre, attend (sur ÉVÉNEMENT, pas sur délai) qu'un écrivain
# EXTÉRIEUR ait réellement pris le verrou, écrit et committé, puis seulement
# commence le lot suivant. Chaque fait vérifié est ainsi STRUCTUREL, jamais
# temporel :
#   · offres == servies == nombre_de_lots − 1  → aucun maintien continu du
#     verrou au-delà d'UN lot, et un autre écrivain passe entre chaque paire ;
#   · sonde SANS attente pendant chaque lot  → le verrou est bien TENU pendant
#     la transaction du lot (donc le commit par lot est réel) ;
#   · commits == nombre_de_lots              → aucune transaction géante ;
#   · aucun échec d'écriture                 → jamais « database is locked ».
# Les durées sont AFFICHÉES (diagnostic), plus jamais utilisées comme verdict.
# ════════════════════════════════════════════════════════════════════════════
FENETRE_MAX_S = 30.0          # borne de SYNCHRONISATION = busy_timeout de l'écrivain
                              # (ce n'est pas un délai d'attente « pour laisser
                              #  passer le temps » : la suite ne progresse que
                              #  lorsque l'écrivain a fini)


class Orchestre:
    lot_ecrit = threading.Event()     # un lot a été écrit (transaction OUVERTE)
    fenetre = threading.Event()       # le verrou est LIBRE : fenêtre offerte
    servi = threading.Event()         # l'écrivain extérieur a terminé
    arret = threading.Event()         # fin de l'insertion : plus de fenêtre
    offres = 0
    servies = 0
    commits = 0
    echecs: list = []
    attentes: list = []
    fenetres_perdues: list = []
    sondes_tenues = 0                 # lots pendant lesquels le verrou était TENU
    sondes_libres = 0                 # (doit rester 0 : un lot est transactionnel)


def _ecrire_checkpoint(tag: str, i: int, attente_s: float) -> float:
    """Écriture EXTÉRIEURE (connexion brute) — comme une validation d'écran."""
    t0 = time.monotonic()
    cx = sqlite3.connect(chemin_fichier, timeout=attente_s)
    try:
        cx.execute("BEGIN IMMEDIATE")
        cx.execute(
            "INSERT INTO collecte_checkpoints (id, source, fenetre_debut,"
            " fenetre_fin, statut, tentatives, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (f"cp-{tag}-{i}", "MZONEX_N1",
             f"2026-09-18 06:{i % 60:02d}:00", f"2026-09-18 07:{i % 60:02d}:00",
             "EN_COURS", 1, "2026-09-18 07:00:00"))
        cx.commit()
    finally:
        cx.close()
    return time.monotonic() - t0


def ecrivain_exterieur():
    """Prend CHAQUE fenêtre offerte : il est la preuve que le verrou est rendu."""
    i = 0
    while not Orchestre.arret.is_set():
        if not Orchestre.fenetre.wait(timeout=1.0):
            continue                                   # aucune fenêtre offerte
        Orchestre.fenetre.clear()
        if Orchestre.arret.is_set():
            break            # fin de l'insertion : la fenêtre finale n'est pas comptée
        i += 1
        try:
            Orchestre.attentes.append(_ecrire_checkpoint("ext", i, FENETRE_MAX_S))
            Orchestre.servies += 1
        except Exception as exc:                       # database is locked…
            Orchestre.echecs.append(f"{type(exc).__name__}: {exc}")
        Orchestre.servi.set()


def _sonder_verrou_pris() -> bool:
    """Sonde SANS attente : le verrou d'écriture est-il tenu EN CE MOMENT ?

    `timeout=0.0` → aucun temporisation, aucune dépendance à la charge : la
    réponse est immédiate (BUSY = verrou pris, succès = verrou libre)."""
    try:
        cx = sqlite3.connect(chemin_fichier, timeout=0.0)
        try:
            cx.execute("BEGIN IMMEDIATE")
            cx.execute("COMMIT")
        finally:
            cx.close()
        return False
    except sqlite3.OperationalError:
        return True


class CollecteurOrchestre(MZoneXApiCollector):
    """Instrumente la frontière des LOTS, là où le produit committe."""

    premier_lot = True

    def _inserer_tranche(self, db, tranche, mapping, vus, historique):
        # ① Le lot PRÉCÉDENT est déjà committé (l'appelant committe après ce
        #    retour) : on OFFRE la fenêtre et on ATTEND (événement) qu'un
        #    écrivain extérieur l'ait prise.
        if not CollecteurOrchestre.premier_lot:
            Orchestre.offres += 1
            Orchestre.servi.clear()
            Orchestre.fenetre.set()
            if not Orchestre.servi.wait(timeout=FENETRE_MAX_S):
                Orchestre.fenetres_perdues.append(Orchestre.offres)
        CollecteurOrchestre.premier_lot = False
        n = super()._inserer_tranche(db, tranche, mapping, vus, historique)
        # ② Les INSERT du lot sont exécutés, le commit n'a PAS encore eu lieu :
        #    le verrou d'écriture doit être TENU (fait structurel, vérifié par
        #    une sonde sans attente).
        if _sonder_verrou_pris():
            Orchestre.sondes_tenues += 1
        else:
            Orchestre.sondes_libres += 1
        Orchestre.lot_ecrit.set()
        return n


_vrai_sessionlocal = scrapers.SessionLocal


class _SessionComptee:
    """Compte les `commit()` : prouve qu'il y en a UN PAR LOT (pas une seule
    transaction géante)."""

    def __init__(self, *a, **k):
        self._s = _vrai_sessionlocal(*a, **k)

    def __getattr__(self, nom):
        return getattr(self._s, nom)

    def commit(self):
        Orchestre.commits += 1
        return self._s.commit()


import math                                                  # noqa: E402
lots_attendus = math.ceil(N / scrapers.LOT_INSERTION)

scrapers.SessionLocal = _SessionComptee
th = threading.Thread(target=ecrivain_exterieur, daemon=True)
th.start()
t0 = time.monotonic()
try:
    inseres = CollecteurOrchestre().inserer(payload, historique=True)
finally:
    duree = time.monotonic() - t0
    Orchestre.arret.set()
    Orchestre.fenetre.set()          # débloque un écrivain en attente de fenêtre
    th.join(timeout=5)
    scrapers.SessionLocal = _vrai_sessionlocal

attente_max = max(Orchestre.attentes, default=0.0)
check(f"{N} points insérés sans échec", inseres == N, f"→ {inseres}")
check("AUCUN écrivain extérieur refusé (0 « database is locked ») — "
      f"{Orchestre.servies} écritures concurrentes réussies",
      not Orchestre.echecs,
      f"→ {Orchestre.echecs[:3]} sur {Orchestre.servies} écritures")
check("le verrou est OFFERT et REPRIS entre CHAQUE paire de lots "
      "(preuve structurelle : aucun maintien continu au-delà d'un lot) — "
      "offres == servies == lots − 1",
      Orchestre.offres == lots_attendus - 1
      and Orchestre.servies == Orchestre.offres
      and not Orchestre.fenetres_perdues,
      f"→ offres={Orchestre.offres}, servies={Orchestre.servies}, "
      f"perdues={Orchestre.fenetres_perdues}, lots={lots_attendus}")
check("pendant CHAQUE lot, le verrou est réellement TENU (sonde sans attente : "
      "BUSY) — les lots sont donc de vraies transactions",
      Orchestre.sondes_tenues == lots_attendus and Orchestre.sondes_libres == 0,
      f"→ tenues={Orchestre.sondes_tenues}, libres={Orchestre.sondes_libres}, "
      f"lots={lots_attendus}")
check("UN COMMIT PAR LOT (aucune transaction géante) : commits == lots + le "
      "commit de clôture (celui de `_reparer_debuts_sans_trajet`)",
      Orchestre.commits == lots_attendus + 1,
      f"→ {Orchestre.commits} commit(s) pour {lots_attendus} lot(s) + clôture")
print(f"  ℹ️  diagnostic (jamais un verdict) : insertion {duree:.2f} s, "
      f"attente maximale de l'écrivain extérieur {attente_max * 1000:.0f} ms, "
      f"{Orchestre.servies} fenêtres prises")

print("\n═══ [TD-121-bis] le progrès est MONOTONE : un lot déjà committé survit ═══")
db = SessionLocal()
avant = db.query(EvenementGPS).count()
db.close()
mapping = {"plaque": plaque}


class FauxCollecteur(MZoneXApiCollector):
    """Casse le 3ᵉ lot : les deux premiers doivent rester en base."""
    def _inserer_tranche(self, db, tranche, mapping, vus, historique):
        n = super()._inserer_tranche(db, tranche, mapping, vus, historique)
        FauxCollecteur.lots += 1
        if FauxCollecteur.lots == 3:
            raise RuntimeError("panne simulée au 3ᵉ lot")
        return n


FauxCollecteur.lots = 0
payload2 = [{"gps_associe": plaque,
             "horodatage": base + timedelta(days=1, seconds=3 * i),
             "lat": -18.9 + i * 1e-6, "lng": 47.5, "adresse": None,
             "vitesse": 40.0, "moteur": "ON", "type_evenement": None}
            for i in range(600)]
try:
    FauxCollecteur().inserer(payload2, historique=True)
except RuntimeError:
    pass
db = SessionLocal()
apres = db.query(EvenementGPS).count()
db.close()
check("les lots committés avant la panne sont CONSERVÉS (pas de tout-ou-rien)",
      apres - avant == 2 * scrapers.LOT_INSERTION,
      f"→ {apres - avant} points conservés (attendu "
      f"{2 * scrapers.LOT_INSERTION})")

print("\n═══ [TD-122] la trace ne commande plus la collecte ═══")
vrai_sessionlocal = scrapers.SessionLocal


class SessionQuiEchoue:
    def __init__(self, *a, **k):
        raise scrapers.OperationalError("sqlite3.OperationalError",
                                        None, Exception("database is locked"))


scrapers.SessionLocal = SessionQuiEchoue
try:
    res = scrapers._checkpoint_ouvre("MZONEX_N1", base, base + timedelta(minutes=5))
    leve = False
except Exception as exc:
    res, leve = None, f"{type(exc).__name__}: {exc}"
finally:
    scrapers.SessionLocal = vrai_sessionlocal
check("_checkpoint_ouvre ne lève plus (retourne None) — la collecte continue",
      res is None and not leve, f"→ res={res} leve={leve}")
scrapers._checkpoint_ferme(None, "TERMINE")     # idempotent, sans effet
check("_checkpoint_ferme accepte un identifiant absent", True)

print("\n═══ [TD-123] catégorisation : LOCALE vs PORTAIL ═══")
cas_local = [
    "OperationalError: (sqlite3.OperationalError) database is locked\n"
    "[SQL: INSERT INTO collecte_checkpoints ...]",
    "OperationalError: database table is locked",
    "OperationalError: disk I/O error",
]
cas_portail = [
    "ErreurAuthMZoneX: MZONEX_USER / MZONEX_PASSWORD absents de backend/.env",
    "ErreurApiMZoneX: GET Events → HTTP 503 : Service Unavailable",
    "ConnectError: TLS/SSL connection has been closed (EOF)",
]
for msg in cas_local:
    check(f"« {msg.splitlines()[0][:52]}… » → locale",
          scrapers.categorie_erreur(Exception(msg)) == "locale",
          f"→ {scrapers.categorie_erreur(Exception(msg))}")
for msg in cas_portail:
    check(f"« {msg[:52]}… » → portail",
          scrapers.categorie_erreur(Exception(msg)) == "portail",
          f"→ {scrapers.categorie_erreur(Exception(msg))}")
check("un dépassement de budget est une cause LOCALE (notre planificateur)",
      scrapers.categorie_erreur(
          scrapers.DepassementBudgetCollecte("Budget de collecte dépassé")) == "locale")

print("\n═══ l'erreur EXACTE de /api/sante est enfin rangée du bon côté ═══")
erreur_production = (
    "OperationalError: (sqlite3.OperationalError) database is locked\n"
    "[SQL: INSERT INTO collecte_checkpoints (id, source, fenetre_debut, "
    "fenetre_fin, statut, curseur, tentatives, derniere_erreur, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)]")
check("l'erreur du 18/09 → « locale » (et non « source MZONEX en panne »)",
      scrapers.categorie_erreur(Exception(erreur_production)) == "locale")

# l'état de collecte doit porter la cause
scrapers._ETAT_COLLECTE["sources"].pop("MZONEX", None)
scrapers._etat_collecte_debut("MZONEX")
scrapers._etat_collecte_erreur("MZONEX", Exception(erreur_production))
detail = scrapers.sources_en_echec_detail()
check("sources_en_echec_detail() expose source + catégorie + message",
      detail and detail[0]["source"] == "MZONEX"
      and detail[0]["categorie"] == "locale",
      f"→ {detail}")

print(f"\n{'=' * 66}\n  RÉSULTAT : {R['ok']} OK / {R['ko']} KO\n{'=' * 66}")
try:
    if "/tmp/" in db_url:
        for suffixe in ("", "-wal", "-shm"):
            try:
                os.remove(chemin_fichier + suffixe)
            except OSError:
                pass
        print("  (base de test supprimée)")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
