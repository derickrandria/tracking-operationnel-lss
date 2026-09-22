"""M1 — FUMÉE du MOTEUR D'AUDIT DÉDIÉ (traces `collecte.*`) sur base TEMPORAIRE.

Ce que ce test prouve, et rien d'autre :

  1. la base utilisée est un fichier TEMPORAIRE du SYSTÈME (Windows comme Linux),
     créé et supprimé par le test — jamais la base du serveur local, jamais une
     base de production, jamais un chemin venu de l'environnement (garde-fou en
     tête, portable : `tempfile.gettempdir()`) ;
  2. le moteur d'audit est un moteur DISTINCT du moteur applicatif ;
  3. écrire 20 traces ne modifie AUCUNE table métier (signatures avant/après) ;
  4. seules des lignes `action LIKE 'collecte.%'` s'ajoutent à `audit_log` ;
  5. aucun `database is locked` : aucune exception, et la phase complète dure
     très en dessous des 30 s du `busy_timeout` (aucune attente subie) ;
  6. AUCUN commit métier pendant la phase de traces (compteur du moteur métier,
     dont le bon fonctionnement est prouvé par un commit de contrôle) ;
  7. aucune connexion d'audit laissée ouverte et moteur refermé proprement ;
  8. aucune perte de trace SILENCIEUSE : file vidée, `traces_perdues()` = 0,
     et le nombre de lignes réellement écrites égale le nombre émis.

Le compteur de commits du moteur métier est celui-là même qui sert aux suites
de concurrence (événement SQLAlchemy « commit ») : c'est ce qui garantit que
l'audit ne se fait pas passer pour du travail métier.

Exécution (depuis `backend/`) :
    python3 test_audit_traces_m1.py
Aucun portail contacté, aucune collecte lancée, aucun serveur requis.
"""
import hashlib
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# ── GARDE-FOU PORTABLE (Windows et Linux) ────────────────────────────────────
# La base de ce test est un fichier TEMPORAIRE DU SYSTÈME, rien d'autre. Le chemin
# n'est JAMAIS pris dans l'environnement ni dans un `.env` : il est construit à
# partir de `tempfile.gettempdir()`, puis VÉRIFIÉ — il doit rester SOUS ce
# répertoire, hors du dépôt, et ne pas porter le nom d'une base d'exploitation.
RACINE_DEPOT = Path(__file__).resolve().parent           # …/backend
TMP_SYSTEME = Path(os.path.realpath(tempfile.gettempdir()))
CHEMIN_RESOLU = Path(os.path.realpath(TMP_SYSTEME / "test_audit_traces_m1.db"))
NOMS_INTERDITS = {"lss.db", "lss_preprod.db", "lss_prod.db", "lss_production.db"}


def _sous_tmp(chemin: Path) -> bool:
    """Vrai si `chemin` est le répertoire temporaire ou se trouve SOUS lui."""
    return chemin == TMP_SYSTEME or TMP_SYSTEME in chemin.parents


def _refus(motif: str) -> None:
    print(f"REFUS : {motif}", file=sys.stderr)
    print(f"        répertoire temporaire système autorisé : {TMP_SYSTEME}",
          file=sys.stderr)
    print(f"        chemin refusé : {CHEMIN_RESOLU}", file=sys.stderr)
    print("        Ce test n'écrit QUE dans le répertoire temporaire du système :"
          " ni le dépôt, ni une base d'exploitation, ni le serveur local,"
          " ni un chemin fourni par l'environnement/`.env`.", file=sys.stderr)
    sys.exit(3)


_env_base = (os.environ.get("DATABASE_URL") or "").strip()
_env_chemin = None
if _env_base.startswith("sqlite") and "///" in _env_base:
    _brut = _env_base.split("///", 1)[-1]
    if _brut:
        _env_chemin = Path(os.path.realpath(_brut))

SITUATION = {
    "sous_tmp": _sous_tmp(CHEMIN_RESOLU),
    "hors_depot": RACINE_DEPOT not in CHEMIN_RESOLU.parents,
    "hors_nom_exploitation": CHEMIN_RESOLU.name.lower() not in NOMS_INTERDITS,
    "env_compatible": _env_chemin is None or _sous_tmp(_env_chemin),
}

if not SITUATION["sous_tmp"]:
    _refus(f"le chemin de la base n'est pas SOUS le répertoire temporaire système "
           f"« {TMP_SYSTEME} »")
if not SITUATION["hors_depot"]:
    _refus("le chemin de la base est DANS le dépôt : jamais la base du produit")
if not SITUATION["hors_nom_exploitation"]:
    _refus("le nom du fichier correspond à une base d'exploitation")
if not SITUATION["env_compatible"]:
    _refus("DATABASE_URL désigne un chemin HORS du répertoire temporaire système "
           "(chemin fourni par l'environnement/`.env`) : ce test ne l'utilise pas")

CHEMIN = str(CHEMIN_RESOLU)          # concaténation des suffixes -wal / -shm
print(f"  base de test : {CHEMIN_RESOLU}")
print(f"  répertoire temporaire système utilisé : {TMP_SYSTEME}")
os.environ["DATABASE_URL"] = f"sqlite:///{CHEMIN_RESOLU.as_posix()}"
os.environ.setdefault("SIM_ENABLE", "0")

for _suffixe in ("", "-wal", "-shm"):          # table rase AVANT tout import
    if os.path.exists(CHEMIN + _suffixe):
        os.remove(CHEMIN + _suffixe)

from sqlalchemy import event                            # noqa: E402
from sqlalchemy.exc import OperationalError, IntegrityError   # noqa: E402

from app import engine as MOTEUR_EVENEMENTS             # noqa: E402
MOTEUR_EVENEMENTS.PUBLISH_ENABLED["on"] = False         # aucune publication

from app import scrapers as S                           # noqa: E402
from app.database import SessionLocal, engine as MOTEUR_METIER   # noqa: E402
from app.main import migrer_schema                      # noqa: E402
from app.models import Vehicule                         # noqa: E402
from app.seed import seed_si_vide                       # noqa: E402

R = {"ok": 0, "ko": 0}
TABLES_METIER = []          # remplies après création du schéma
lignes = []                 # journal lisible des investigations


def check(nom, cond, info=""):
    if cond:
        R["ok"] += 1
        print(f"  OK   {nom}")
    else:
        R["ko"] += 1
        print(f"  KO   {nom} {info}")


def note(texte):
    lignes.append(texte)
    print(f"  ·    {texte}")


def tables_existantes():
    with sqlite3.connect(CHEMIN) as cx:
        return sorted(r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"))


def signature(table):
    """Empreinte de TOUTES les lignes d'une table (ordre stable) — aucune
    interprétation métier : on compare des octets, pas des hypothèses."""
    with sqlite3.connect(f"file:{CHEMIN}?mode=ro", uri=True) as cx:
        try:
            lignes_t = list(cx.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))
        except sqlite3.Error as exc:                    # table sans rowid
            return f"illisible:{exc}"
    return hashlib.sha256(repr(lignes_t).encode()).hexdigest()[:16]


def signatures(tables):
    return {t: signature(t) for t in tables}


def lignes_audit():
    with sqlite3.connect(f"file:{CHEMIN}?mode=ro", uri=True) as cx:
        return list(cx.execute(
            "SELECT id, action FROM audit_log ORDER BY date_heure, id"))


print("\n[A] Base de test : fichier TEMPORAIRE, jamais celle du serveur local")
check("la base est un fichier TEMPORAIRE du système (aucun serveur, aucune "
      "production, aucun chemin d'environnement)",
      SITUATION["sous_tmp"] and SITUATION["hors_depot"]
      and SITUATION["hors_nom_exploitation"] and SITUATION["env_compatible"],
      f"→ {CHEMIN_RESOLU} (répertoire temporaire : {TMP_SYSTEME})")

seed_si_vide()          # crée le schéma (create_all) et les données de démo
migrer_schema()         # migrations additives, comme au démarrage du produit
db = SessionLocal()
vehicule = Vehicule(plaque="TEST-M1", gps_associe="M1-001")
db.add(vehicule)
db.commit()
PLAQUE = vehicule.plaque
db.close()

TABLES_METIER = [t for t in tables_existantes() if t != "audit_log"]
check("le schéma est créé et une donnée MÉTIER existe (contrôle non vide)",
      "vehicules" in TABLES_METIER and len(TABLES_METIER) >= 5,
      f"→ {len(TABLES_METIER)} table(s) métier")
note(f"tables métier surveillées : {len(TABLES_METIER)}")

print("\n[B] Moteur d'audit DISTINCT du moteur applicatif")
_ = S._session_traces().close()          # force la création du moteur d'audit
check("le moteur d'audit est un objet différent du moteur applicatif",
      S._ENGINE_TRACES is not None and S._ENGINE_TRACES is not MOTEUR_METIER)

print("\n[C] Compteur de commits du moteur métier : contrôle non vide")
compteur = {"metier": 0, "audit": 0, "audit_actif": False}


def _sur_commit(conn):
    if conn.engine is MOTEUR_METIER:
        compteur["metier"] += 1
    elif compteur["audit_actif"] and conn.engine is S._ENGINE_TRACES:
        compteur["audit"] += 1


event.listen(MOTEUR_METIER, "commit", _sur_commit)
event.listen(S._ENGINE_TRACES, "commit", _sur_commit)

db = SessionLocal()
db.add(Vehicule(plaque="TEST-M1-CTRL", gps_associe="M1-002"))
db.commit()
db.close()
check("le compteur métier compte bien un vrai commit (contrôle non vide)",
      compteur["metier"] == 1, f"→ {compteur['metier']}")
compteur["metier"] = 0

print("\n[D] Écriture de 20 traces : audit isolé, métier intact")
AVANT = signatures(TABLES_METIER)
avant_audit = {ligne[0] for ligne in lignes_audit()}
compteur["audit_actif"] = True
debut = time.monotonic()
exception = None
S.demarrer_worker_traces()

passe = S.PasseCourante("N2_MZONEX", 120.0, debut_le="2026-09-22T10:00:00")
try:
    S._tracer_lancement("MZONEX", passe, jours=[])
    S._tracer_non_demarree("N2_CAMTRACKPRO", "non_atteinte",
                           phase="attente_http", motif="surveillance",
                           passe_nom="N2_MIXTE")
    for i in range(8):                       # 8 « sources » × 2 lignes
        S._tracer_source_resultat(f"SRC_{i}", passe_nom="N2_MIXTE",
                                  jours=[], stats={"recus": i, "crees": i},
                                  origine="API_N2", duree_lecture_s=0.5,
                                  duree_ecriture_s=0.2, issue="TERMINE",
                                  sources_passe=[f"SRC_{i}"], confirme=True)
        S._tracer_passe_synthese(passe, {"issue": "TERMINE", "total_s": 1.0,
                                         "budget_s": 120.0, "nb_pages": 3})
    S._synthese_periodique("MZONEX", {"issue": "TERMINE", "total_s": 1.0})
    for i in range(2):
        S._tracer(f"collecte.smoke.{i}", {"source": "MZONEX", "nb_pages": i})
except (OperationalError, IntegrityError) as exc:      # aucun verrou subi
    exception = f"{type(exc).__name__}: {exc}"
except Exception as exc:                               # noqa: BLE001
    exception = f"{type(exc).__name__}: {exc}"
finally:
    S.vider_traces(silence_s=15.0)
    duree = time.monotonic() - debut
    compteur["audit_actif"] = False

check("aucune exception côté collecte (aucun « database is locked »)",
      exception is None, f"→ {exception}")
check("aucune attente de 30 s : la phase complète dure bien moins",
      duree < 10.0, f"→ {duree:.2f}s (busy_timeout = 30 s)")
check("file vidée : aucune trace laissée en attente",
      S.traces_info()["en_file"] == 0
      and S.traces_info()["perdues"] == S.traces_perdues(),
      f"→ {S.traces_info()}")

noires = [r for r in lignes_audit() if r[0] not in avant_audit]
actions = sorted({a for _, a in noires})
check("20 traces ont bien été ÉCRITES (aucune perte silencieuse)",
      len(noires) == 20, f"→ {len(noires)} ligne(s) ajoutée(s)")
check("aucune perte signalée (compteur de pertes à zéro)",
      S.traces_perdues() == 0, f"→ perdues={S.traces_perdues()}")
check("seules des actions `collecte.*` sont ajoutées à audit_log",
      all(a.startswith("collecte.") for a in actions),
      f"→ {actions}")
check("AUCUN commit métier pendant la phase de traces",
      compteur["metier"] == 0, f"→ {compteur['metier']} commit(s) métier")
check("le moteur d'audit a bien commité (l'écriture est réelle, pas simulée)",
      compteur["audit"] == 20, f"→ {compteur['audit']} commit(s) d'audit")
note(f"audit : {compteur['audit']} commit(s) — métier : {compteur['metier']} commit(s)")

APRES = signatures(TABLES_METIER)
identiques = [t for t in TABLES_METIER if AVANT.get(t) == APRES.get(t)]
check("les tables MÉTIER sont bit à bit IDENTIQUES avant/après",
      len(identiques) == len(TABLES_METIER),
      f"→ modifiées : {[t for t in TABLES_METIER if AVANT.get(t) != APRES.get(t)]}")
note(f"signatures comparées : {len(TABLES_METIER)} table(s)")
with sqlite3.connect(f"file:{CHEMIN}?mode=ro", uri=True) as _cx:
    _plaques = {r[0] for r in _cx.execute("SELECT plaque FROM vehicules")}
check("la donnée métier du contrôle est toujours là (contrôle non vide)",
      {"TEST-M1", "TEST-M1-CTRL"} <= _plaques, f"→ {sorted(_plaques)[:4]}")

print("\n[E] Fermeture du moteur d'audit et absence de verrou résiduel")
check("aucune connexion d'audit laissée ouverte",
      S._ENGINE_TRACES.pool.checkedout() == 0,
      f"→ {S._ENGINE_TRACES.pool.checkedout()} connexion(s) en cours")
S._ENGINE_TRACES.dispose()
try:
    with sqlite3.connect(CHEMIN, timeout=2.0) as cx:
        cx.execute("PRAGMA quick_check").fetchone()
        cx.execute("CREATE TABLE _probe_verrou (x INTEGER)")
        cx.execute("DROP TABLE _probe_verrou")
    verrou = None
except sqlite3.Error as exc:
    verrou = f"{type(exc).__name__}: {exc}"
check("aucun verrou résiduel : une autre connexion écrit immédiatement",
      verrou is None, f"→ {verrou}")

print("\n" + "=" * 74)
print(f"  RÉSULTAT : {R['ok']} OK / {R['ko']} KO")
print("=" * 74)
for _suffixe in ("", "-wal", "-shm"):
    if os.path.exists(CHEMIN + _suffixe):
        os.remove(CHEMIN + _suffixe)
print(f"  (base temporaire supprimée : {CHEMIN})")
sys.exit(1 if R["ko"] else 0)
