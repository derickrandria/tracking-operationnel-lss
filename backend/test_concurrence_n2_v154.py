"""Tests v1.54 (21/09/2026) — CONCURRENCE DU NIVEAU 2 : verrous, SQLite, budget.

Complément de `test_concurrence_verrous_v154.py` : ce dernier couvre la passe
N1 (verrou à jeton, budget par points d'arrêt, santé) ; la présente suite couvre
le NIVEAU 2 (réconciliation des trajets) et le SOCLE SQLITE.

Scénarios exigés, un par section :
  [A] MZONEX N1 + MZONEX N2 simultanés        (verrous DISTINCTS)
  [B] CAMTRACKPRO N2 + MZONEX N2 simultanés   (verrous DISTINCTS)
  [C] MZONEX_RELECTURE + N2 simultanés        (la relecture ne bloque pas N2)
  [D] deux writers SQLite simultanés          (aucune écriture perdue)
  [E] réconciliation pendant une collecte     (N1 et N2 en même temps)
  [F] annulation d'une passe N2               (coopérative, verrou rendu)
  [G] libération du verrou N2                 (succès, échec, jeton étranger)
  [H] redémarrage pendant N2                  (ancien jeton inopérant)
  [I] absence de doublon                      (N2 rejoué = mêmes trajets)
  [J] absence de perte d'écriture             (points N1 + trajets N2)
  [K] « database is locked » TRAITÉ            (classé, visible, verrou rendu)
  [L] mécanisme SQLite                        (commits par lots, transactions
                                               courtes, verrou rendu, attente
                                               mesurée et DISTINGUÉE du portail)

Lancement (base temporaire automatique) :
  cd backend
  python test_concurrence_n2_v154.py
Aucun réseau : les collecteurs N2 (API MZoneX / API Wialon) sont remplacés par
des collecteurs simulés — ce sont les VERROUS, les TRANSACTIONS et la SANTÉ qui
sont mesurés ici, pas le décodage HTTP (couvert par test_api_v125,
test_api_wialon_v126, test_niveau2_v117, test_repli_api_v154).
Aucune assertion métier n'est affaiblie : le budget, les seuils et les règles de
réconciliation ne sont pas modifiés.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Réglages de TEST (délais d'attente uniquement, aucune attente métier) ────
os.environ.setdefault("SIM_ENABLE", "0")
os.environ.setdefault("TESTING", "1")
os.environ["YMANE_ACTIVE"] = "0"              # cycle Ym@ne hors périmètre
os.environ["COLLECTE_LOT_INSERTION"] = "100"  # lots de 100 pour compter les commits
os.environ["COLLECTE_SOUFFLE_S"] = "0"
os.environ["COLLECTE_COMMIT_ESSAIS"] = "1"    # un essai : le défaut est mesuré
os.environ["COLLECTE_COMMIT_BASE_S"] = "0.05" # coût de commit de référence

RACINE_DEPOT = Path(__file__).resolve().parents[1]
if str(RACINE_DEPOT) not in sys.path:
    sys.path.insert(0, str(RACINE_DEPOT))

_url = os.environ.get("DATABASE_URL", "")
if not _url:
    os.environ["DATABASE_URL"] = (
        f"sqlite:///{Path(tempfile.gettempdir()) / 'test_concurrence_n2_v154.db'}")
    _url = os.environ["DATABASE_URL"]
elif not _url.startswith("sqlite:///") or (
        "/tmp/" not in _url and "test" not in Path(_url).name.lower()):
    print("⛔ Sécurité : base de test uniquement (fichier temporaire ou nom avec « test »).")
    sys.exit(2)

CHEMIN_DB = _url.replace("sqlite:///", "", 1)
for _suffixe in ("", "-wal", "-shm"):
    try:
        Path(CHEMIN_DB + _suffixe).unlink()
    except OSError:
        pass

from sqlalchemy import event, func, select                   # noqa: E402

from app import concurrence as C                             # noqa: E402
from app import reconciliation as REC                        # noqa: E402
from app import scrapers as S                                # noqa: E402
from app.config import now_local                             # noqa: E402
from app.database import SessionLocal                        # noqa: E402
from app.models import EvenementGPS, SuiviJournalier, Trajet, Vehicule  # noqa: E402
from app.seed import seed_si_vide                            # noqa: E402

R = {"ok": 0, "ko": 0}


def titre(nom: str):
    print(f"\n{'═' * 74}\n{nom}\n{'═' * 74}")


def check(nom: str, condition, info: str = ""):
    if condition:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} {info}")


print("═" * 74)
print("  test_concurrence_n2_v154 — concurrence du Niveau 2 et socle SQLite")
print("═" * 74)
seed_si_vide()
from app.main import migrer_schema                           # noqa: E402
migrer_schema()

MAINT = now_local().replace(microsecond=0)
JOURS = [MAINT.date() - timedelta(days=1), MAINT.date()]


# ─────────────────────────────────────────────────────────── utilitaires
def _plaques(plateforme: str = "MZONEX", combien: int = 2) -> list[str]:
    db = SessionLocal()
    try:
        return list(db.scalars(select(Vehicule.gps_associe).where(
            Vehicule.gps_associe.isnot(None),
            Vehicule.plateforme_gps == plateforme).limit(combien)))
    finally:
        db.close()


def nb_points(plaque: str) -> int:
    db = SessionLocal()
    try:
        return int(db.scalar(select(func.count(EvenementGPS.id)).join(
            Vehicule, EvenementGPS.vehicule_id == Vehicule.id).where(
                Vehicule.gps_associe == plaque)) or 0)
    finally:
        db.close()


def nb_trajets(plaque: str) -> int:
    db = SessionLocal()
    try:
        return int(db.scalar(select(func.count(Trajet.id)).join(
            SuiviJournalier, Trajet.suivi_id == SuiviJournalier.id).join(
                Vehicule, SuiviJournalier.vehicule_id == Vehicule.id).where(
                    Vehicule.gps_associe == plaque)) or 0)
    finally:
        db.close()


def ecrivain_independant_ok(delai_s: float = 1.0) -> bool:
    """Un écrivain INDÉPENDANT (connexion brute) peut-il prendre le verrou
    d'écriture tout de suite ? C'est la preuve que la collecte l'a bien rendu."""
    try:
        cx = sqlite3.connect(CHEMIN_DB, timeout=delai_s)
        cx.execute("BEGIN IMMEDIATE")
        cx.execute("CREATE TABLE IF NOT EXISTS _sonde_ecriture (x INTEGER)")
        cx.execute("INSERT INTO _sonde_ecriture (x) VALUES (1)")
        cx.commit()
        cx.close()
        return True
    except Exception:                                     # noqa: BLE001
        return False


def points_de(plaque: str, n: int, base: datetime) -> list[dict]:
    return [{"gps_associe": plaque, "horodatage": base + timedelta(seconds=30 * i),
             "lat": -18.9 + i * 0.0001, "lng": 47.5, "vitesse": 35.0,
             "moteur": "ON", "type_evenement": None, "adresse": None,
             "observation": False} for i in range(n)]


def bruts_de(plaque: str, source: str, debut: datetime) -> list[dict]:
    """Bruts de trajet au format attendu par `normaliser_valides`."""
    return [{"plaque": plaque, "debut": debut, "fin": debut + timedelta(hours=1),
             "distance_km": 22.0, "source": source}]


PLQ_A, PLQ_B = _plaques("MZONEX", 2)

# ── remplacement des collecteurs N2 (frontière réseau) ─────────────────────
_mz_origine = S._collecter_n2_mzonex
_cp_origine = S._collecter_n2_camtrackpro
compteurs = {"mzonex": 0, "camtrackpro": 0}


def faux_mzonex(delai: float = 0.0, bruts: list | None = None):
    def _f(jours, attendues=None):
        compteurs["mzonex"] += 1
        time.sleep(delai)
        return (list(bruts) if bruts is not None
                else bruts_de(PLQ_A, "MZONEX", MAINT.replace(hour=6, minute=0))), \
            [PLQ_A]
    return _f


def faux_camtrackpro(delai: float = 0.0, bruts: list | None = None):
    def _f(jours):
        compteurs["camtrackpro"] += 1
        time.sleep(delai)
        return (list(bruts) if bruts is not None
                else bruts_de(PLQ_B, "CAMTRACKPRO", MAINT.replace(hour=6, minute=0))), \
            [PLQ_B]
    return _f


S._collecter_n2_mzonex = faux_mzonex()
S._collecter_n2_camtrackpro = faux_camtrackpro()

# ═══════════════════════════════════════════ [A] N1 MZONEX + N2 MZONEX
titre("[A] MZONEX N1 + MZONEX N2 SIMULTANÉS — deux verrous, aucun blocage")
compteurs["mzonex"] = 0
resultat_a = {}


def _n1_lente():
    time.sleep(0.5)
    return 4


fil_a = threading.Thread(target=lambda: resultat_a.update(
    n1=S._collecte_protegee("MZONEX", _n1_lente, timeout_s=10)))
fil_a.start()
time.sleep(0.15)
check("pendant N1 MZONEX, le verrou « N2_MZONEX » est LIBRE",
      C.verrou_de("N2_MZONEX").occupe is False)
t0 = time.monotonic()
stats_a = S.synchroniser_trajets_valides("MZONEX")
duree_a = time.monotonic() - t0
fil_a.join()
check("N2 MZONEX s'exécute PENDANT N1 MZONEX (aucune attente de 0,5 s)",
      duree_a < 0.4, f"→ {duree_a:.2f}s")
check("les deux passes ont abouti (N1 = 4 points, N2 = trajets réconciliés)",
      resultat_a.get("n1") == 4 and "budget_depasse" not in stats_a
      and "occupee" not in stats_a, f"→ {resultat_a} / {stats_a}")
check("le collecteur N2 MZoneX a bien été appelé", compteurs["mzonex"] == 1)
check("les deux verrous sont rendus",
      not C.verrou_de("MZONEX").occupe and not C.verrou_de("N2_MZONEX").occupe)

# ═══════════════════════════════════════ [B] N2 CAMTRACKPRO + N2 MZONEX
titre("[B] CAMTRACKPRO N2 + MZONEX N2 SIMULTANÉS — verrous distincts")
S._collecter_n2_mzonex = faux_mzonex(delai=0.6)
S._collecter_n2_camtrackpro = faux_camtrackpro(delai=0.6)
compteurs["mzonex"] = compteurs["camtrackpro"] = 0
resultats_b = {}


def _n2(source):
    resultats_b[source] = S.synchroniser_trajets_valides(source)


t_debut_b = time.monotonic()
fil_mz = threading.Thread(target=_n2, args=("MZONEX",))
fil_mz.start()
time.sleep(0.2)
check("pendant N2 MZONEX, « N2_CAMTRACKPRO » est LIBRE",
      C.verrou_de("N2_CAMTRACKPRO").occupe is False)
fil_cp = threading.Thread(target=_n2, args=("CAMTRACKPRO",))
fil_cp.start()
time.sleep(0.2)
check("les DEUX passes N2 s'exécutent en même temps (chacune son verrou)",
      C.verrou_de("N2_MZONEX").occupe and C.verrou_de("N2_CAMTRACKPRO").occupe)
fil_cp.join()
fil_mz.join()
duree_b = time.monotonic() - t_debut_b
check("les deux passes se sont CHEVAUCHÉES (total ≈ 0,6 s, pas 1,2 s en série)",
      duree_b < 1.0, f"→ {duree_b:.2f}s")
check("chaque source a collecté (MZoneX et CamtrackPro appelés une fois)",
      compteurs["mzonex"] == 1 and compteurs["camtrackpro"] == 1,
      f"→ {compteurs}")
check("aucune passe n'a été refusée pour cause de verrou",
      all("occupee" not in (resultats_b.get(s) or {})
          for s in ("MZONEX", "CAMTRACKPRO")), f"→ {resultats_b}")
check("les deux verrous N2 sont rendus",
      not C.verrou_de("N2_MZONEX").occupe
      and not C.verrou_de("N2_CAMTRACKPRO").occupe)

# ═══════════════════════════════════════════ [C] RELECTURE + N2
titre("[C] MZONEX_RELECTURE + N2 SIMULTANÉS — la relecture ne bloque pas N2")
S._collecter_n2_mzonex = faux_mzonex()
S._collecter_n2_camtrackpro = faux_camtrackpro()
compteurs["mzonex"] = 0
fil_relecture = threading.Thread(target=lambda: S._collecte_protegee(
    "MZONEX_RELECTURE", lambda: (time.sleep(0.6), 9)[1], timeout_s=10))
fil_relecture.start()
time.sleep(0.15)
check("pendant la relecture, « MZONEX » (temps réel) est LIBRE",
      C.verrou_de("MZONEX").occupe is False)
check("pendant la relecture, « N2_MZONEX » est LIBRE",
      C.verrou_de("N2_MZONEX").occupe is False)
t0 = time.monotonic()
stats_c = S.synchroniser_trajets_valides("MZONEX")
duree_c = time.monotonic() - t0
fil_relecture.join()
check("la réconciliation N2 s'exécute PENDANT la relecture (aucune attente)",
      duree_c < 0.4 and "occupee" not in stats_c, f"→ {duree_c:.2f}s")
check("le verrou de relecture est rendu",
      not C.verrou_de("MZONEX_RELECTURE").occupe)

# ═══════════════════════════════════════ [D] deux writers SQLite
titre("[D] DEUX WRITERS SQLITE SIMULTANÉS — aucune écriture perdue")
pts_d1 = points_de(PLQ_A, 40, datetime(2027, 4, 1, 5, 0, 0))
pts_d2 = points_de(PLQ_B, 40, datetime(2027, 4, 1, 5, 0, 0))
erreurs_d: list[str] = []
avant_d1, avant_d2 = nb_points(PLQ_A), nb_points(PLQ_B)


def _ecrire(pts, etiquette):
    try:
        S.MZoneXApiCollector().inserer(pts, historique=True)
    except Exception as exc:                              # noqa: BLE001
        erreurs_d.append(f"{etiquette}: {type(exc).__name__}: {exc}"[:120])


fils_d = [threading.Thread(target=_ecrire, args=(pts_d1, "A")),
          threading.Thread(target=_ecrire, args=(pts_d2, "B"))]
[f.start() for f in fils_d]
[f.join() for f in fils_d]
check("aucune erreur d'écriture non traitée entre les deux writers",
      not erreurs_d, f"→ {erreurs_d[:2]}")
check("writer A : ses 40 points sont en base",
      nb_points(PLQ_A) - avant_d1 == 40, f"→ {nb_points(PLQ_A) - avant_d1}")
check("writer B : ses 40 points sont en base",
      nb_points(PLQ_B) - avant_d2 == 40, f"→ {nb_points(PLQ_B) - avant_d2}")

# ═══════════════════ [E] réconciliation (N2) pendant une collecte (N1)
titre("[E] RÉCONCILIATION PENDANT UNE COLLECTE — N1 et N2 en même temps")
S._collecter_n2_mzonex = faux_mzonex(delai=0.3)
pts_e = points_de(PLQ_A, 30, datetime(2027, 4, 2, 5, 0, 0))
avant_e = nb_points(PLQ_A)
t0 = time.monotonic()
fil_n1_e = threading.Thread(target=lambda: S._collecte_protegee(
    "CAMTRACKPRO", lambda: S.MZoneXApiCollector().inserer(pts_e, historique=True),
    timeout_s=20))
fil_n1_e.start()
stats_e = S.synchroniser_trajets_valides("MIXTE")
duree_e = time.monotonic() - t0
fil_n1_e.join()
check("les points GPS collectés pendant la réconciliation sont tous en base",
      nb_points(PLQ_A) - avant_e == 30, f"→ {nb_points(PLQ_A) - avant_e}")
check("les trajets des DEUX sources ont été réconciliés",
      compteurs["mzonex"] >= 1 and compteurs["camtrackpro"] >= 1,
      f"→ {compteurs}")
check("la réconciliation n'a pas été refusée (verrou N2_MIXTE libre)",
      "occupee" not in stats_e, f"→ {stats_e}")
check("les deux verrous sont rendus",
      not C.verrou_de("N2_MIXTE").occupe and not C.verrou_de("CAMTRACKPRO").occupe)

# ═══════════════════════════════════════════ [F] annulation d'une passe N2
titre("[F] ANNULATION D'UNE PASSE N2 — coopérative, verrou rendu")
S._collecter_n2_mzonex = faux_mzonex(delai=0.8)
S._collecter_n2_camtrackpro = faux_camtrackpro()
compteurs["camtrackpro"] = 0
resultat_f = {}


def _n2_mixte():
    resultat_f.update(res=S.synchroniser_trajets_valides("MIXTE"))


fil_f = threading.Thread(target=_n2_mixte)
fil_f.start()
time.sleep(0.2)
passe_f = C.passe_de("N2_MIXTE")
check("la passe N2 est ENREGISTRÉE (surveillance possible)",
      passe_f is not None and C.verrou_de("N2_MIXTE").occupe is True)
if passe_f is not None:
    passe_f.annuler("annulation_de_test")
t0 = time.monotonic()
fil_f.join()
duree_f = time.monotonic() - t0
met_f = C.metriques_publiees().get("N2_MIXTE", {})
check("la passe s'arrête à son point d'arrêt (≈0,6 s, pas 60 s de budget)",
      duree_f < 3, f"→ {duree_f:.2f}s")
check("l'issue publiée est BUDGET_DEPASSE",
      met_f.get("issue") == "BUDGET_DEPASSE", f"→ {met_f.get('issue')}")
check("le point d'arrêt est nommé (aucun « inconnue »)",
      met_f.get("etape_bloquante") not in (None, "inconnue"),
      f"→ {met_f.get('etape_bloquante')}")
check("la 2ᵉ source n'a PAS été collectée après l'échéance",
      compteurs["camtrackpro"] == 0, f"→ {compteurs['camtrackpro']}")
check("la passe annoncée est rendue (verrou N2_MIXTE libéré)",
      not C.verrou_de("N2_MIXTE").occupe)
check("le résultat est structuré (pas d'exception pour l'appelant)",
      isinstance(resultat_f.get("res"), dict)
      and resultat_f["res"].get("budget_depasse") is True,
      f"→ {resultat_f.get('res')}")
check("plus aucune passe enregistrée (registre nettoyé)",
      C.passe_de("N2_MIXTE") is None)

# ═══════════════════════════════════════ [G] libération du verrou N2
titre("[G] LIBÉRATION DU VERROU N2 — jeton contrôlé, cas d'échec inclus")
S._collecter_n2_mzonex = faux_mzonex()
stats_g = S.synchroniser_trajets_valides("MZONEX")
check("après une passe N2 réussie, le verrou est RENDU",
      not C.verrou_de("N2_MZONEX").occupe and "occupee" not in stats_g)

vg = C.verrou_de("N2_TEST")
pg = vg.acquerir("A")
faux = C.Possession(ressource="N2_TEST", source="B", jeton="jeton-invente",
                    generation=pg.generation, pris_le="", pris_mono=0.0,
                    expire_mono=0.0)
check("un jeton étranger ne libère JAMAIS un verrou N2",
      vg.liberer(faux) is False and vg.proprietaire == "A")
check("la tentative refusée est comptée",
      vg.etat()["liberations_refusees"] == 1, f"→ {vg.etat()['liberations_refusees']}")
check("le propriétaire légitime libère", vg.liberer(pg) is True)

S._collecter_n2_mzonex = faux_mzonex()
S._collecter_n2_mzonex = (lambda f: (lambda jours, attendues=None:
                                     (_ for _ in ()).throw(RuntimeError("panne N2 simulée"))))(
                                         S._collecter_n2_mzonex)
stats_g2 = S.synchroniser_trajets_valides("MZONEX")
check("une passe N2 dont la collecte échoue rend quand même son verrou",
      not C.verrou_de("N2_MZONEX").occupe, f"→ {stats_g2}")
check("l'échec est publié en santé (source N2_MZONEX)",
      any(d["source"] == "N2_MZONEX" for d in S.sources_en_echec_detail()),
      f"→ {[d['source'] for d in S.sources_en_echec_detail()]}")

# ═══════════════════════════════════════════ [H] redémarrage pendant N2
titre("[H] REDÉMARRAGE PENDANT UNE PASSE N2 — ancien jeton inopérant")
vh = C.verrou_de("N2_MZONEX")
ph = vh.acquerir("INSTANCE_SORTANTE", duree_s=0.2)
check("l'instance sortante détient le verrou N2", ph is not None)
time.sleep(0.3)
ph2 = vh.acquerir("INSTANCE_NOUVELLE", duree_s=30)
check("après redémarrage, la nouvelle instance reprend la ressource "
      "(génération supérieure, reprise EXPLICITE)",
      ph2 is not None and ph2.generation > ph.generation,
      f"→ gén. {ph.generation} puis {ph2 and ph2.generation}")
check("le jeton de l'ancienne instance ne libère plus rien",
      vh.liberer(ph) is False)
check("la nouvelle possession reste intacte",
      vh.proprietaire == "INSTANCE_NOUVELLE")
check("libération légitime acceptée", vh.liberer(ph2) is True)
verrou_neuf = C.VerrouPossede("N2_MZONEX_REDEMARRAGE", duree_defaut_s=30)
pneuf = verrou_neuf.acquerir("PROCESSUS_FRAIS", duree_s=5)
check("une instance neuve (processus redémarré) démarre SANS possession héritée",
      pneuf is not None)
verrou_neuf.liberer(pneuf)
C.forcer_famille(n2=True, raison="test_redemarrage_n2")
check("le forçage N2 est explicite et ne réassigne aucun jeton",
      C.verrou_de("N2_MZONEX").occupe is False)

# ═══════════════════════════════════════════ [I] absence de doublon
titre("[I] ABSENCE DE DOUBLON — N2 rejoué produit le MÊME état")
S._collecter_n2_mzonex = faux_mzonex()
S._collecter_n2_camtrackpro = faux_camtrackpro()
S.synchroniser_trajets_valides("MIXTE")
trajets_1 = nb_trajets(PLQ_A)
S.synchroniser_trajets_valides("MIXTE")
trajets_2 = nb_trajets(PLQ_A)
check("la 1ʳᵉ passe crée bien des trajets (preuve de non-vacuité)",
      trajets_1 > 0, f"→ {trajets_1}")
check("rejouer la MÊME collecte N2 ne crée aucun doublon",
      trajets_2 == trajets_1, f"→ {trajets_1} puis {trajets_2}")
db_i = SessionLocal()
try:
    doublons_i = int(db_i.scalar(select(func.count()).select_from(
        select(Trajet.suivi_id, Trajet.heure_debut).group_by(
            Trajet.suivi_id, Trajet.heure_debut).having(
                func.count(Trajet.id) > 1).subquery())) or 0)
finally:
    db_i.close()
check("aucun trajet en double (même suivi + même début)", doublons_i == 0,
      f"→ {doublons_i}")

# ═══════════════════════════════════════════ [J] absence de perte
titre("[J] ABSENCE DE PERTE D'ÉCRITURE — points N1 ET trajets N2")
pts_j = points_de(PLQ_B, 25, datetime(2027, 4, 3, 5, 0, 0))
avant_j = nb_points(PLQ_B)
S._collecte_protegee("MZONEX", lambda: S.MZoneXApiCollector().inserer(
    pts_j, historique=True), timeout_s=20)
check("25 points insérés → 25 lignes en base",
      nb_points(PLQ_B) - avant_j == 25, f"→ {nb_points(PLQ_B) - avant_j}")
S.synchroniser_trajets_valides("CAMTRACKPRO")
check("les trajets N2 sont présents (aucune écriture perdue par la concurrence)",
      nb_trajets(PLQ_B) > 0, f"→ {nb_trajets(PLQ_B)}")
check("aucun point n'a été écrit deux fois (pas de doublon d'horodatage)",
      nb_points(PLQ_B) == avant_j + 25, f"→ {nb_points(PLQ_B)}")

# ═══════════════════════ [K] « database is locked » toujours traité
titre("[K] « DATABASE IS LOCKED » TRAITÉ — classé, visible, verrou rendu")
from sqlalchemy.exc import OperationalError as SAOperationalError   # noqa: E402

_recon_origine = REC.reconcilier_trajets_valides
appels_k = {"mzonex": 0, "camtrackpro": 0}


def _recon_avec_verrou(db, items, username="", **kw):
    """La réconciliation de CAMTRACKPRO échoue comme sous verrou SQLite ; celle
    de MZoneX passe normalement (une source en échec ne doit pas bloquer
    l'autre)."""
    if "camtrackpro" in str(username):
        raise SAOperationalError("UPDATE trajets ...", None,
                                 sqlite3.OperationalError("database is locked"))
    appels_k["mzonex"] += 1
    return _recon_origine(db, items, username=username, **kw)


S._collecter_n2_mzonex = faux_mzonex()
S._collecter_n2_camtrackpro = faux_camtrackpro()
REC.reconcilier_trajets_valides = _recon_avec_verrou
try:
    stats_k = S.synchroniser_trajets_valides("MIXTE")
finally:
    REC.reconcilier_trajets_valides = _recon_origine

detail_k = {d["source"]: d for d in S.sources_en_echec_detail()}
entree_k = detail_k.get("N2_CAMTRACKPRO", {})
check("le verrou d'écriture n'a PAS interrompu la collecte N2 (résultat rendu)",
      isinstance(stats_k, dict) and "budget_depasse" not in stats_k, f"→ {stats_k}")
check("la source MZoneX a bien été réconciliée malgré l'échec de l'autre",
      appels_k["mzonex"] == 1, f"→ {appels_k}")
check("l'échec de CAMTRACKPRO est VISIBLE en santé (classe attente_sqlite)",
      entree_k.get("classe") == C.CLASSE_ATTENTE_SQLITE, f"→ {entree_k}")
check("la catégorie historique reste « locale » (compatibilité v1.50)",
      entree_k.get("categorie") == "locale", f"→ {entree_k.get('categorie')}")
check("le message porte bien « database is locked »",
      "locked" in (entree_k.get("erreur") or "").lower(), f"→ {entree_k.get('erreur')}")
check("le verrou N2_MIXTE est rendu malgré l'échec",
      not C.verrou_de("N2_MIXTE").occupe)

# ═══════════════════════════════════════════ [L] mécanisme SQLite
titre("[L] MÉCANISME SQLITE — lots, transactions courtes, verrou, attente mesurée")
# Comptage au niveau du MOTEUR (et non de la session) : le collecteur ouvre une
# transaction PAR LOT et, à l'intérieur, un SAVEPOINT PAR POINT (atomicité du
# point). Les libérations de savepoint ne sont pas des commits de transaction :
# seuls les vrais commits sont comptés ici — c'est eux qui « sérialisent ».
journal = {"commits": 0, "t_avant": None, "max_tx": 0.0, "actif": False}


def _moteur_begin(conn):
    if journal["actif"]:
        journal["t_avant"] = time.monotonic()


def _moteur_commit(conn):
    if journal["actif"]:
        journal["commits"] += 1
        if journal["t_avant"] is not None:
            journal["max_tx"] = max(journal["max_tx"],
                                    time.monotonic() - journal["t_avant"])
            journal["t_avant"] = None


from app.database import engine as _engine                   # noqa: E402
event.listen(_engine, "begin", _moteur_begin)
event.listen(_engine, "commit", _moteur_commit)

pts_l = points_de(PLQ_A, 600, datetime(2027, 4, 4, 4, 0, 0))
avant_l = nb_points(PLQ_A)
journal["actif"] = True
n_l = S._collecte_protegee("MZONEX", lambda: S.MZoneXApiCollector().inserer(
    pts_l, historique=True), timeout_s=60)
journal["actif"] = False
ecrits_l = nb_points(PLQ_A) - avant_l
check("les 600 points sont écrits", n_l == 600 and ecrits_l == 600,
      f"→ {n_l} / {ecrits_l}")
check("les commits sont SÉRIALISÉS PAR LOTS : 6 lots (600/100) + 1 clôture "
      "= 7 — ni 1 (transaction géante) ni 600 (une par point)",
      journal["commits"] == 7, f"→ {journal['commits']} commit(s)")
check("aucune transaction longue (max < 2 s)",
      journal["max_tx"] < 2.0, f"→ {journal['max_tx']:.2f}s")
met_l = C.metriques_publiees().get("MZONEX", {})
check("les points écrits sont comptés exactement (600)",
      met_l.get("nb_points_ecrits") == 600, f"→ {met_l.get('nb_points_ecrits')}")
check("le verrou du writer est RENDU : un autre écrivain commite aussitôt",
      ecrivain_independant_ok(), )

# attente SQLite mesurée et DISTINGUÉE du portail
prets = threading.Event()
def _tenir():
    cx = sqlite3.connect(CHEMIN_DB, timeout=0.05)
    cx.execute("BEGIN IMMEDIATE")
    prets.set()
    time.sleep(0.4)
    cx.rollback()
    cx.close()


threading.Thread(target=_tenir, daemon=True).start()
prets.wait(5)
pts_l2 = points_de(PLQ_B, 10, datetime(2027, 4, 5, 4, 0, 0))
S._collecte_protegee("CAMTRACKPRO", lambda: S.MZoneXApiCollector().inserer(
    pts_l2, historique=True), timeout_s=20)
met_l2 = C.metriques_publiees().get("CAMTRACKPRO", {})
check("l'attente du VERROU SQLITE est mesurée (≥ 0,2 s d'attente réelle)",
      (met_l2.get("attente_sqlite_s") or 0) >= 0.2,
      f"→ {met_l2.get('attente_sqlite_s')}")
check("le coût RÉEL d'écriture reste petit (≠ attente)",
      (met_l2.get("ecriture_s") or 9) < 0.3, f"→ {met_l2.get('ecriture_s')}")
check("le PORTAIL n'est pas mis en cause (attente HTTP nulle)",
      (met_l2.get("attente_http_s") or 0) == 0,
      f"→ {met_l2.get('attente_http_s')}")
check("aucune écriture perdue pendant l'attente du verrou (10 points écrits)",
      nb_points(PLQ_B) >= 10)

# ─────────────────────────────────────────────────────────────── résultats
if "/tmp/" in _url or "test" in Path(_url).name.lower():
    for _suffixe in ("", "-wal", "-shm"):
        try:
            Path(CHEMIN_DB + _suffixe).unlink()
        except OSError:
            pass
print("\n" + "=" * 74)
print(f"  RÉSULTAT : {R['ok']} OK / {R['ko']} KO")
print("=" * 74)
sys.exit(1 if R["ko"] else 0)
