"""Connexion base de données (SQLAlchemy 2.0).

SQLite en mode démo autonome ; PostgreSQL en production via DATABASE_URL
(le modèle reste identique, transactions ACID conservées).

────────────────────────────────────────────────────────────────────────────
v1.50 — « database is locked » : ce n'était NI le portail NI le disque, mais
la façon dont nos transactions SQLite s'ouvraient.
────────────────────────────────────────────────────────────────────────────
Constat du 18/09/2026 (`/api/sante`) : `MZONEX` déclarée EN PANNE alors que le
portail répondait — la seule erreur était

    OperationalError: (sqlite3.OperationalError) database is locked
    [SQL: INSERT INTO collecte_checkpoints ...]

DEUX mécanismes se cumulaient, tous deux reproduits en laboratoire :

1. **Le piège WAL du « lire puis écrire ».** Pysqlite (le pilote SQLite de
   Python) ouvre chaque transaction en `BEGIN` DIFFÉRÉ : le verrou d'écriture
   n'est pris qu'au premier INSERT/UPDATE, APRÈS les SELECT (véhicules, clé
   d'idempotence, suivi…). Si une autre connexion écrit entre-temps, la
   montée en écriture est refusée par SQLite (`SQLITE_BUSY_SNAPSHOT`) et
   AUCUNE attente ne peut la débloquer : le pilote réessaie jusqu'à son
   `timeout` (30 s) puis rend « database is locked ». Mesuré : la RELECTURE
   MZoneX elle-même est morte sur un `UPDATE vehicules` — pas le checkpoint.
   → corrigé par des transactions d'écriture COURTES (commit par lots, cf.
   `scrapers.LOT_INSERTION`, et souffle inter-lots) : la montée en écriture
   trouve toujours une fenêtre libre. Mesuré : le motif qui échouait (lire puis
   écrire pendant une relecture) réussit après correctif, et le blocage continu
   du verrou tombe de 3,81 s à 270 ms. `BEGIN IMMEDIATE` a été essayé puis
   écarté : il fait prendre le verrou aux transactions de LECTURE, si bien que
   deux sessions du même service s'attendent (voir `SQLITE_IMMEDIATE`).

2. **Des transactions trop longues.** Tout un lot de collecte (jusqu'à une
   fenêtre de rattrapage entière) était inséré dans UNE transaction : mesuré
   10 000 points = 11 s de verrou d'écriture, soit ≈ 44 s pour une fenêtre
   d'arrêt de 25 h. Aucun `busy_timeout` ne survit à cela.
   → corrigé dans `scrapers.CollectorBase.inserer` (commit par lots) et
   `scrapers.relecture_n1_mzonex` (travail borné par cycle).

Les réglages WAL restent nécessaires : ils sont posés sur chaque connexion
(`journal_mode=WAL`, `busy_timeout`, `synchronous=NORMAL`).
"""
import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

EST_SQLITE = DATABASE_URL.startswith("sqlite")
# v1.50 — `BEGIN IMMEDIATE` est disponible mais DÉSACTIVÉ PAR DÉFAUT, et voici
# pourquoi (mesuré, pas supposé) :
#   • la théorie : prendre le verrou d'écriture dès le BEGIN évite le piège WAL
#     « lire puis écrire » (montée en écriture refusée définitivement) ;
#   • la pratique : avec IMMEDIATE, MÊME une transaction de LECTURE prend le
#     verrou d'écriture. Deux sessions vivantes du même service (un lecteur de
#     l'API + la collecte, ou deux sessions d'un même test) s'attendent alors
#     mutuellement jusqu'à l'expiration du busy_timeout → « database is locked »
#     sur `BEGIN IMMEDIATE` (constaté : 7 suites de tests cassées, dont
#     test_conduite_v127) ;
#   • le vrai levier était ailleurs : des transactions d'écriture COURTES
#     (commit par lots + souffle inter-lots, cf. scrapers). Ce seul correctif
#     suffit : le motif qui échouait en production (lire puis écrire pendant une
#     relecture) réussit désormais en 0,1-4 s.
# Le mode reste activable (LSS_SQLITE_IMMEDIATE=1) pour qui préfère la file
# d'attente — et il est explicitement désactivé par les outils de contrôle en
# lecture seule (verifier_*.py).
SQLITE_IMMEDIATE = os.getenv("LSS_SQLITE_IMMEDIATE", "0") == "1"

connect_args = ({"check_same_thread": False, "timeout": 30} if EST_SQLITE
                else {})

engine = create_engine(DATABASE_URL, connect_args=connect_args,
                       pool_pre_ping=True)

# v1.50 — journal SQLite : `journal_mode` est PERSISTANT (inscrit dans le
# fichier), `busy_timeout`/`synchronous` sont par connexion. Exposés par
# /api/sante pour que le diagnostic ne repose plus sur une supposition.
PRAGMAS_SQLITE = {"journal_mode": "?", "busy_timeout": "?",
                  "synchronous": "?", "foreign_keys": "?"}

if EST_SQLITE:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    # ── v1.50 — BEGIN IMMEDIATE (recette canonique SQLAlchemy/pysqlite) ──────
    # (désactivable par LSS_SQLITE_IMMEDIATE=0 pour les outils de contrôle)
    # Sans cela, chaque transaction commence par des lectures (véhicules,
    # idempotence, suivi) et ne demande le verrou d'écriture qu'ensuite : si
    # une autre connexion a écrit entre-temps, SQLite refuse définitivement la
    # montée (SQLITE_BUSY_SNAPSHOT) et l'on obtient « database is locked »
    # après 30 s d'attente inutile — le défaut observé le 18/09.
    @event.listens_for(engine, "connect")
    def _immediate_transactions(dbapi_connection, connection_record):
        if not SQLITE_IMMEDIATE:
            return
        # pysqlite n'impose plus son BEGIN implicite : c'est nous qui le
        # prononçons, en mode IMMEDIATE (voir l'écouteur « begin »).
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):
        if SQLITE_IMMEDIATE:
            conn.exec_driver_sql("BEGIN IMMEDIATE")


def pragmas_sqlite() -> dict:
    """Réglages SQLite réellement en vigueur (pour /api/sante) : un journal
    « delete » ou un `busy_timeout` retombé à 0 expliquerait des verrous."""
    if not EST_SQLITE:
        return {"moteur": "non-sqlite"}
    chemin = DATABASE_URL.split("sqlite:///", 1)[-1]
    if not chemin:
        return {"moteur": "sqlite (mémoire)"}
    try:
        # v1.50 — connexion DIRECTE (pas le moteur SQLAlchemy) : lire les
        # réglages ne doit jamais prendre le verrou d'écriture, sinon
        # /api/sante gênerait la collecte qu'il sert à surveiller.
        import sqlite3
        # la connexion de lecture est réglée COMME celles du service (sinon on
        # annoncerait le busy_timeout de la sonde, pas celui de la collecte)
        cx = sqlite3.connect(chemin, timeout=float(connect_args.get("timeout", 5)))
        try:
            cx.execute("PRAGMA journal_mode=WAL")
            cx.execute("PRAGMA busy_timeout=30000")
            cx.execute("PRAGMA synchronous=NORMAL")
            return {p: cx.execute(f"PRAGMA {p}").fetchone()[0]
                    for p in PRAGMAS_SQLITE}
        finally:
            cx.close()
    except Exception as exc:                                  # jamais bloquant
        return {"erreur": f"{type(exc).__name__}: {exc}"[:200]}


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
