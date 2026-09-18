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

attentes: list = []


def ecrivain_concurrent(tag, arret):
    """Passe « N1 » concurrente : écrit son checkpoint pendant l'insertion."""
    i = 0
    while not arret.is_set():
        t0 = time.time()
        i += 1
        try:
            cx = sqlite3.connect(chemin_fichier, timeout=30.0)
            cx.execute(
                "INSERT INTO collecte_checkpoints (id, source, fenetre_debut,"
                " fenetre_fin, statut, tentatives, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (f"cp-{tag}-{i}", "MZONEX_N1",
                 f"2026-09-18 06:{i % 60:02d}:00",
                 f"2026-09-18 07:{i % 60:02d}:00", "EN_COURS", 1,
                 "2026-09-18 07:00:00"))
            cx.commit()
            cx.close()
            attentes.append(("ok", time.time() - t0))
        except Exception as exc:                      # database is locked…
            attentes.append((f"{type(exc).__name__}", time.time() - t0))
        time.sleep(0.05)


# v1.50 — LA BONNE MESURE : pendant combien de temps le verrou d'écriture
# reste-t-il pris SANS INTERRUPTION ? C'est elle qui décide si les écritures du
# reste de la plateforme (validations, PATCH, audits) passent ou attendent.
# (L'attente d'un écrivain donné dépend en plus du back-off interne de SQLite,
# dont les paliers montent jusqu'à 100 ms : il peut rater une fenêtre de 10 ms
# et attendre la suivante — sans jamais échouer, ce qui est le point capital.)
plages = {"suite": 0, "max": 0, "fenetres_libres": 0, "stop": False}


def moniteur():
    while not plages["stop"]:
        try:
            cx = sqlite3.connect(chemin_fichier, timeout=0.0)
            cx.execute("BEGIN IMMEDIATE")
            cx.execute("COMMIT")
            cx.close()
            plages["fenetres_libres"] += 1
            plages["suite"] = 0
        except Exception:
            plages["suite"] += 1
            plages["max"] = max(plages["max"], plages["suite"])
        time.sleep(0.01)


arret = threading.Event()
th = threading.Thread(target=ecrivain_concurrent, args=("concurrent", arret),
                      daemon=True)
mo = threading.Thread(target=moniteur, daemon=True)
th.start()
mo.start()
t0 = time.time()
inseres = MZoneXApiCollector().inserer(payload, historique=True)
duree = time.time() - t0
arret.set()
plages["stop"] = True
th.join(timeout=5)
mo.join(timeout=2)

echecs = [a for a in attentes if a[0] != "ok"]
attente_max = max((a[1] for a in attentes), default=0.0)
check(f"{N} points insérés sans échec", inseres == N, f"→ {inseres}")
check("AUCUN écrivain concurrent n'a échoué (0 « database is locked »)",
      not echecs, f"→ {echecs[:3]} sur {len(attentes)} tentatives")
check("le verrou n'est plus repris en continu : plage bloquée maximale < 1 s "
      "(mesurée : 3,81 s avant le souffle inter-lots)",
      plages["max"] * 0.01 < 1.0,
      f"→ {plages['max'] * 10} ms de blocage continu, "
      f"{plages['fenetres_libres']} fenêtres libres pendant {duree:.1f} s")
check("un écrivain concurrent finit TOUJOURS par être servi (< 5 s, le temps "
      "que le back-off de SQLite tombe sur une fenêtre libre)",
      attente_max < 5.0,
      f"→ {attente_max:.2f} s (insertion totale {duree:.1f} s)")

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
