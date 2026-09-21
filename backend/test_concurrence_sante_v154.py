"""Tests v1.54 (21/09/2026) — R14→R19 : phases, erreurs, points d'arrêt, métriques.

Suite de preuve des corrections demandées après le diagnostic du 21/09 :

  [T1] R14 — phase / raison d'annulation / classe / issue SÉPARÉES
  [T2] R15 — l'erreur s'EFFACE après une réussite (historique conservé)
  [T3] R15 — N2 : erreur collante purgée, `dernier_debut` plus jamais null
  [T4] R16/R4 — points d'arrêt : AUCUNE écriture après l'échéance
              (cas MZONEX 54,9 s / budget 30 s / phase écriture / verrou rendu)
  [T5] R5 — réconciliation N2 interruptible et IDEMPOTENTE (aucune unité
             partielle, reprise au cycle suivant, zéro doublon)
  [T6] R6/R19 — métriques par source COMPLÈTES, `null` si non applicable
  [T7] R7 — N2 publie SA phase et SA durée (pas le cumul, pas 2 635 s)
  [T8] R8/R18 — cycles PAR source (jamais MZONEX-début vs CamtrackPro-fin)
  [T9] R9 — passe en cours : `fin=null`, `issue=en_cours`, durée vive
  [T10] R10 — progression de relecture mémorisée (journée/page/véhicule/id)
  [T11] R11 — la relecture cède le temps réel, sans jamais le bloquer
  [T12] R13/santé — /api/sante expose la cause réelle (message adapté)
  [T13] stabilité de l'API : deux lectures consécutives identiques

Exécution (base temporaire OBLIGATOIRE) :
  DATABASE_URL="sqlite:////tmp/test_sante_v154.db" python3 test_concurrence_sante_v154.py
Aucun réseau, aucune donnée réelle, aucune attente métier modifiée.
"""
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("SIM_ENABLE", "0")
os.environ.setdefault("TESTING", "1")
os.environ["COLLECTE_COMMIT_ESSAIS"] = "1"
os.environ["COLLECTE_SOUFFLE_S"] = "0"
os.environ["COLLECTE_LOT_INSERTION"] = "5"

RACINE_DEPOT = Path(__file__).resolve().parents[1]
if str(RACINE_DEPOT) not in sys.path:
    sys.path.insert(0, str(RACINE_DEPOT))

_url = os.environ.get("DATABASE_URL", "")
if not _url:
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'test_sante_v154.db'}"
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

from sqlalchemy import func, select                     # noqa: E402

from app import concurrence as C                        # noqa: E402
from app import scrapers as S                           # noqa: E402
from app.config import now_local                        # noqa: E402
from app.database import SessionLocal                   # noqa: E402
from app.models import EvenementGPS, Trajet, Vehicule    # noqa: E402
from app.seed import seed_si_vide                       # noqa: E402

R = {"ok": 0, "ko": 0}
SECTION = {"nom": ""}


def titre(nom: str):
    SECTION["nom"] = nom
    print(f"\n{'═' * 74}\n{nom}\n{'═' * 74}")


def check(nom: str, condition, info: str = ""):
    if condition:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} {info}")


def _plaques(n: int, plateforme: str | None = None) -> list[str]:
    """Plaques RÉELLES de la base de test (jamais de plaque inventée)."""
    db = SessionLocal()
    try:
        req = select(Vehicule.plaque)
        if plateforme:
            req = req.where(Vehicule.plateforme_gps == plateforme)
        req = req.order_by(Vehicule.plaque).limit(n)
        return [p for p in db.scalars(req).all()]
    finally:
        db.close()


def _nb_evenements() -> int:
    db = SessionLocal()
    try:
        return int(db.scalar(select(func.count(EvenementGPS.id))) or 0)
    finally:
        db.close()


def _nb_trajets() -> int:
    db = SessionLocal()
    try:
        return int(db.scalar(select(func.count(Trajet.id))) or 0)
    finally:
        db.close()


def points_de(plaque: str, n: int, base: datetime) -> list[dict]:
    return [{"gps_associe": plaque, "horodatage": base + timedelta(seconds=37 * i),
             "lat": -18.9 + i * 0.0001, "lng": 47.5, "vitesse": 35.0,
             "moteur": "ON", "type_evenement": None, "adresse": None,
             "observation": False} for i in range(n)]


def bruts_de(plaque: str, source: str, debut: datetime) -> list[dict]:
    return [{"plaque": plaque, "debut": debut, "fin": debut + timedelta(hours=1),
             "distance_km": 22.0, "source": source}]


print("═" * 74)
print("  test_concurrence_sante_v154 — R14→R19 : phase, erreurs, arrêts, métriques")
print("═" * 74)
seed_si_vide()
from app.main import migrer_schema                      # noqa: E402
migrer_schema()
MAINT = now_local().replace(microsecond=0)
PLQ_MZ = (_plaques(1, "MZONEX") or _plaques(1))[0]
PLQ_2 = _plaques(2, "MZONEX") or _plaques(2)
print(f"  plaques réelles utilisées : {PLQ_2}")

# ════════════════════════════════════════════ [T1] R14 — quatre grandeurs
titre("[T1] R14 — PHASE, RAISON D'ANNULATION, CLASSE et ISSUE sont SÉPARÉES")
exc = C.BudgetDepasse("inconnue", 2635.797, 120.0, source="N2_MIXTE",
                      raison_annulation="surveillance_limite_depassee",
                      etape_connue="ecriture")
check("la PHASE réelle (écriture) est conservée même si l'appel dit « inconnue »",
      exc.etape == "ecriture" and exc.etape_brute == "inconnue", f"→ {exc.etape}")
check("la RAISON D'ANNULATION voyage dans son propre champ (jamais à la place de la phase)",
      exc.raison_annulation == "surveillance_limite_depassee")
check("l'ISSUE est explicite : BUDGET_DEPASSE",
      exc.issue == "BUDGET_DEPASSE", f"→ {exc.issue}")
check("le message ne dit JAMAIS « inconnue » quand la phase est connue",
      "inconnue" not in str(exc) and "ecriture" in str(exc), f"→ {exc}")
check("aucune phase n'est INVENTÉE quand rien n'est mesuré",
      C.BudgetDepasse("inconnue", 1.0, 1.0).etape == "inconnue")

S._etat_collecte_erreur("T1_SOURCE", exc)
_t1 = (S.etat_metriques_par_source().get("T1_SOURCE") or {}).get("derniere_erreur") or {}
check("la santé publie la CLASSE (budget_depasse) séparément de la phase",
      _t1.get("classe") == "budget_depasse" and _t1.get("phase") == "ecriture",
      f"→ {_t1}")
check("la santé publie aussi la raison d'annulation et l'issue",
      _t1.get("raison_annulation") == "surveillance_limite_depassee"
      and _t1.get("issue") == "BUDGET_DEPASSE")

# ════════════════════════════════════════════ [T2] R15 — purge de l'erreur
titre("[T2] R15 — UNE RÉUSSITE EFFACE L'ERREUR ; L'HISTORIQUE RESTE")
S._etat_collecte_erreur("MZONEX", C.BudgetDepasse(
    "ecriture", 40.0, 30.0, source="MZONEX", raison_annulation="surveillance_limite_depassee"))
_avant = (S.etat_metriques_par_source().get("MZONEX") or {})
check("l'erreur est ACTIVE avant la réussite", _avant.get("erreur_active") is True)
S._collecte_protegee("MZONEX", lambda: 3, timeout_s=30.0)
_apres = (S.etat_metriques_par_source().get("MZONEX") or {})
check("après une passe RÉUSSIE, plus aucune erreur active",
      _apres.get("erreur_active") is False, f"→ {_apres.get('derniere_erreur')}")
check("l'erreur passée est CONSERVÉE dans l'historique",
      isinstance(_apres.get("historique_erreur"), dict)
      and _apres["historique_erreur"].get("erreur"), f"→ {_apres.get('historique_erreur')}")
check("la dernière RÉUSSITE est visible", bool(_apres.get("derniere_reussite")))
check("`dernier_debut` n'est plus jamais null après une passe",
      _apres.get("dernier_debut") is not None, f"→ {_apres.get('dernier_debut')}")
check("la dernière passe publiée est TERMINE",
      (_apres.get("derniere_passe") or {}).get("issue") == "TERMINE")

# ════════════════════════════════════════════ [T3] R15 — N2 (erreur collante)
titre("[T3] R15 — N2 : l'erreur collante est purgée, le début est publié")
_S_mz, _S_cp = S._collecter_n2_mzonex, S._collecter_n2_camtrackpro
S._collecter_n2_mzonex = lambda jours, attendues=None: ([], [])
S._collecter_n2_camtrackpro = lambda jours: ([], [])
S._etat_collecte_erreur("N2_MIXTE", C.BudgetDepasse(
    "inconnue", 2635.797, 120.0, source="N2_MIXTE",
    raison_annulation="surveillance_limite_depassee", etape_connue="ecriture"))
_avant_n2 = (S.etat_metriques_par_source().get("N2_MIXTE") or {})
check("l'erreur N2 est active avant la réussite", _avant_n2.get("erreur_active") is True)
stats_n2 = S.synchroniser_trajets_valides("MIXTE")
_apres_n2 = (S.etat_metriques_par_source().get("N2_MIXTE") or {})
check("après une passe N2 réussie, l'erreur n'est plus affichée",
      _apres_n2.get("erreur_active") is False, f"→ {stats_n2}")
check("une passe N2 a un DÉBUT visible (plus de `dernier_debut` null)",
      _apres_n2.get("dernier_debut") is not None, f"→ {_apres_n2.get('dernier_debut')}")
check("le cycle N2 est fermé en TERMINE (aucune inversion de dates)",
      (_apres_n2.get("cycle") or {}).get("en_cours") is False
      and (_apres_n2.get("cycle") or {}).get("duree_s") is not None)

# ════════════════════════════════════════ [T4] R16/R4 — arrêt avant écriture
titre("[T4] R16/R4 — MZONEX 54,9 s sur un budget de 30 s : ZÉRO écriture après l'échéance")
print("      (l'horloge de la passe est avancée de 54,9 s à un point d'arrêt prévu :")
print("       le scénario réel du 21/09, sans faire attendre la suite)")
COL = S.CollectorBase()
PLQ_T4 = PLQ_MZ
_base = MAINT - timedelta(hours=6)
avant_t4 = _nb_evenements()
ETAT_T4 = {}


def scenario_mzonex():
    """Écrit 5 points, puis dépasse l'échéance, puis TENTE d'écrire à nouveau."""
    n1 = COL.inserer(points_de(PLQ_T4, 5, _base))
    C.verifier_etape("attente_http")
    passe = C.passe_courante()
    passe.debut_mono -= 54.9                     # 54,9 s de travail simulées
    C.verifier_etape("ecriture")                 # POINT D'ARRÊT avant l'écriture
    n2 = COL.inserer(points_de(PLQ_T4, 5, _base + timedelta(hours=1)))
    ETAT_T4["ecrit_apres_echeance"] = n2         # ne doit JAMAIS être atteint
    return n1 + n2


retour_t4 = S._collecte_protegee("MZONEX", scenario_mzonex, timeout_s=30.0)
apres_t4 = _nb_evenements()
_p4 = (S.etat_metriques_par_source().get("MZONEX") or {}).get("derniere_passe") or {}
check("la passe s'est ARRÊTÉE avant la seconde écriture (jamais atteinte)",
      "ecrit_apres_echeance" not in ETAT_T4)
check("les 5 points écrits AVANT l'échéance sont conservés (aucune perte)",
      apres_t4 - avant_t4 == 5, f"→ {apres_t4 - avant_t4}")
check("AUCUNE écriture après l'échéance (compteur dédié = 0)",
      _p4.get("commits_apres_echeance") == 0, f"→ {_p4.get('commits_apres_echeance')}")
check("l'issue publiée dit BUDGET_DEPASSE et la PHASE réelle dit écriture",
      _p4.get("issue") == "BUDGET_DEPASSE" and _p4.get("phase") == "ecriture",
      f"→ {_p4.get('issue')} / {_p4.get('phase')}")
check("la durée publiée (≈54,9 s) dépasse le budget de 30 s : l'écart est EXPLIQUÉ",
      _p4.get("budget_s") == 30.0 and (_p4.get("total_s") or 0) >= 54.0,
      f"→ {_p4.get('total_s')} s pour {_p4.get('budget_s')} s")
check("le VERROU MZONEX est rendu (aucune libération oubliée)",
      C.verrou_de("MZONEX").occupe is False)
check("l'objet renvoyé est 0 : la passe est signalée interrompue (jamais silencieuse)",
      retour_t4 == 0, f"→ {retour_t4}")

# ════════════════════════════════════════ [T5] R5 — N2 interruptible/idempotent
titre("[T5] R5 — N2 interruptible : unité entière, reprise au cycle suivant, zéro doublon")
items_n2 = (bruts_de(PLQ_2[0], "MZONEX", MAINT.replace(hour=6, minute=0))
            + bruts_de(PLQ_2[1], "MZONEX", MAINT.replace(hour=9, minute=0)))
S._collecter_n2_mzonex = lambda jours, attendues=None: (list(items_n2), [PLQ_2[0], PLQ_2[1]])
S._collecter_n2_camtrackpro = lambda jours: ([], [])
_orig_verif_rec = None
import app.reconciliation as REC                            # noqa: E402
_orig_verif_rec = REC.verifier_etape
_orig_reconcilier = REC.reconcilier_trajets_valides


def _spy_verif(declencheur: int):
    """Avance l'horloge au 3ᵉ point d'arrêt de la réconciliation (avant l'unité 2)."""
    compteur = {"n": 0}

    def _v(etape):
        compteur["n"] += 1
        if compteur["n"] == declencheur:
            p = C.passe_courante()
            if p is not None:
                p.debut_mono -= 10_000.0
        return _orig_verif_rec(etape)
    return _v


avant_t5 = _nb_trajets()
REC.verifier_etape = _spy_verif(3)
try:
    stats_t5 = S.synchroniser_trajets_valides("MZONEX")
finally:
    REC.verifier_etape = _orig_verif_rec
apres_t5 = _nb_trajets()
_p5 = (S.etat_metriques_par_source().get("N2_MZONEX") or {}).get("derniere_passe") or {}
check("l'arrêt est signalé (budget dépassé) et NON avalé en « erreur de trajet »",
      stats_t5.get("budget_depasse") is True and (stats_t5.get("etape") == "ecriture"),
      f"→ {stats_t5}")
check("la 1ʳᵉ unité est ENTIÈREMENT écrite (transaction courte, jamais partielle)",
      apres_t5 - avant_t5 == 1, f"→ {apres_t5 - avant_t5}")
check("la 2ᵉ unité n'a pas commencé (aucune écriture après l'échéance)",
      (_p5.get("commits_apres_echeance") or 0) == 0 and _p5.get("nb_lignes_ecrites") == 1,
      f"→ commits_apres_echeance={_p5.get('commits_apres_echeance')}, "
      f"lignes={_p5.get('nb_lignes_ecrites')}")
check("le cycle N2_MZONEX est fermé en BUDGET_DEPASSE et la phase est publiée",
      _p5.get("issue") == "BUDGET_DEPASSE" and _p5.get("phase") == "ecriture",
      f"→ {_p5.get('issue')} / {_p5.get('phase')}")
check("le verrou N2_MZONEX est rendu malgré l'interruption",
      C.verrou_de("N2_MZONEX").occupe is False)
# reprise au cycle suivant : la même charge, sans interruption
stats_t5b = S.synchroniser_trajets_valides("MZONEX")
apres_t5b = _nb_trajets()
check("la reprise au cycle suivant COMPLÈTE le travail (2 unités, pas 1)",
      apres_t5b == avant_t5 + 2, f"→ {apres_t5b - avant_t5}")
check("ZÉRO doublon : la 1ʳᵉ unité n'est pas réécrite (idempotence)",
      apres_t5b == avant_t5 + 2 and stats_t5b.get("budget_depasse") is None,
      f"→ {stats_t5b}")
REC.verifier_etape = _orig_verif_rec

# ════════════════════════════════════════ [T6] R6/R19 — métriques complètes
titre("[T6] R6/R19 — métriques complètes par source ; `null` si NON APPLICABLE")
_etat = S.etat_metriques_par_source()
check("chaque source est décrite par CINQ blocs séparés",
      all(all(cle in bloc for cle in ("passe_actuelle", "derniere_passe",
                                      "dernier_resultat", "cumul", "derniere_erreur"))
          for bloc in _etat.values()), f"→ {sorted(_etat)}")
_p6 = (_etat.get("MZONEX") or {}).get("derniere_passe") or {}
_champs = ("debut_le", "fin_le", "total_s", "budget_s", "phase", "issue", "annulee",
           "nb_pages", "nb_lignes_lues", "nb_lignes_ecrites", "nb_commits",
           "commits_apres_echeance", "attente_http_s", "attente_sqlite_s",
           "attente_verrou_s", "ecriture_s", "non_applicables")
check("la dernière passe MZONEX publie début, fin, durée, budget, phase et issue",
      _p6.get("debut_le") and _p6.get("fin_le") and _p6.get("total_s") is not None
      and _p6.get("budget_s") and _p6.get("phase") and _p6.get("issue"),
      f"→ {_p6}")
check("les compteurs d'attente (HTTP, SQLite, verrou) sont publiés SÉPARÉMENT",
      all(champ in _p6 for champ in ("attente_http_s", "attente_sqlite_s",
                                     "attente_verrou_s")))
check("les compteurs NON APPLICABLES sont `null`, jamais 0 (R19)",
      _p6.get("nb_vehicules") is None and "nb_vehicules" in (_p6.get("non_applicables") or []),
      f"→ nb_vehicules={_p6.get('nb_vehicules')}")
_n2p = (_etat.get("N2_MZONEX") or {}).get("derniere_passe") or {}
check("N2 : véhicules et points écrits sont `null` (non mesurables par cette source)",
      _n2p.get("nb_vehicules") is None and _n2p.get("nb_points_ecrits") is None
      and set(_n2p.get("non_applicables") or []) >= {"nb_vehicules", "nb_points_ecrits"},
      f"→ {_n2p.get('non_applicables')}")
check("la moyenne est calculée à partir du NOMBRE de passes (jamais sur 1 passe)",
      isinstance((_etat.get("N2_MZONEX") or {}).get("cumul", {}).get("moyenne_s"),
                 (int, float))
      and (_etat["N2_MZONEX"]["cumul"]["passes"] or 0) >= 1)

# ═════════════════════════════════════════════ [T7] R7 — durée et phase réelles
titre("[T7] R7 — N2 publie SA durée et SA phase (jamais le cumul, jamais 2 635 s)")
_p7_avant = (_etat.get("N2_MZONEX") or {}).get("cumul") or {}
S._collecter_n2_mzonex = lambda jours, attendues=None: ([], [])
time.sleep(0.25)
S.synchroniser_trajets_valides("MZONEX")
_e7 = S.etat_metriques_par_source().get("N2_MZONEX") or {}
_d7 = _e7.get("derniere_passe") or {}
_c7 = _e7.get("cumul") or {}
check("`derniere_passe.total_s` = durée de la DERNIÈRE passe (courte), pas le cumul",
      isinstance(_d7.get("total_s"), (int, float))
      and _d7["total_s"] < 5.0 and (_c7.get("total_s") or 0) >= (_d7.get("total_s") or 0),
      f"→ passe={_d7.get('total_s')} s / cumul={_c7.get('total_s')} s")
check("le cumul de passes INCRÉMENTE (le total de la dernière passe ne l'écrase pas)",
      (_c7.get("passes") or 0) > (_p7_avant.get("passes") or 0),
      f"→ {_p7_avant.get('passes')} → {_c7.get('passes')}")
check("aucune durée d'écriture n'est déclarée quand RIEN n'a été écrit",
      (not _d7.get("nb_lignes_ecrites")) and (_d7.get("ecriture_s") or 0.0) < 0.05,
      f"→ ecriture_s={_d7.get('ecriture_s')} / lignes={_d7.get('nb_lignes_ecrites')}")
check("la phase publiée n'est jamais « inconnue » si une phase a été mesurée",
      _d7.get("phase") != "inconnue" or _d7.get("total_s") == 0,
      f"→ {_d7.get('phase')}")

# ═════════════════════════════════════════════ [T8] R8/R18 — cycles par source
titre("[T8] R8/R18 — cycles PAR source : jamais MZONEX-début vs CamtrackPro-fin")
C.fermer_cycle("MZONEX", MAINT.isoformat(), "TERMINE")
C.fermer_cycle("CAMTRACKPRO", MAINT.isoformat(), "TERMINE")
t_debut = MAINT - timedelta(minutes=5)
C.ouvrir_cycle("MZONEX", t_debut.isoformat())
C.ouvrir_cycle("CAMTRACKPRO", t_debut.isoformat())
C.fermer_cycle("CAMTRACKPRO", MAINT.isoformat(), "TERMINE")
_cycles = C.cycles_par_source()
_c_mz = (_cycles.get("cycles") or {}).get("MZONEX") or {}
_c_cp = (_cycles.get("cycles") or {}).get("CAMTRACKPRO") or {}
check("MZONEX (en cours) publie `fin = null` et `en_cours = True`",
      _c_mz.get("fin") is None and _c_mz.get("en_cours") is True, f"→ {_c_mz}")
check("CAMTRACKPRO (fermé) publie SA fin et SA durée",
      _c_cp.get("fin") == MAINT.isoformat() and (_c_cp.get("duree_s") or 0) >= 300.0,
      f"→ {_c_cp}")
check("aucune inversion croisée : la durée de MZONEX n'est pas celle de CAMTRACKPRO",
      _c_mz.get("debut") == t_debut.isoformat()
      and _c_cp.get("debut") == t_debut.isoformat()
      and _c_mz.get("fin") is None)
check("les cycles en ÉCHEC sont identifiables (pour ne pas les confondre avec un retard)",
      "cycles_en_echec" in _cycles
      and isinstance(_cycles["sources_en_echec_cycle"], list))
check("aucun cycle en échec après deux fermetures TERMINE",
      _cycles.get("cycles_en_echec") is False
      and _cycles.get("sources_en_echec_cycle") == [],
      f"→ {_cycles.get('sources_en_echec_cycle')}")

# ═════════════════════════════════════════════ [T9] R9 — passe en cours
titre("[T9] R9 — la passe EN COURS est publiée à part : fin=null, issue=en_cours")
passe_t9 = C.PasseCourante("MZONEX_T9", 30.0, debut_le=MAINT.isoformat())
time.sleep(0.05)
_inst = passe_t9.instantane()
check("la passe en cours ne prétend PAS être terminée (`fin_le = null`)",
      _inst.get("fin_le") is None)
check("son issue est `en_cours` (et non un verdict inventé)",
      _inst.get("issue") == "en_cours", f"→ {_inst.get('issue')}")
check("sa durée est VIVE (mesurée, pas figée) et le restant de budget est publié",
      (_inst.get("total_s") or 0) > 0 and 0 < (_inst.get("restant_s") or 0) <= 30.0,
      f"→ {_inst.get('total_s')} s / restant {_inst.get('restant_s')} s")
check("elle n'est PAS confondue avec la dernière passe publiée",
      passe_t9.metriques.issue is None or passe_t9.metriques.issue != "TERMINE")
C.enregistrer_passe("MZONEX_T9", passe_t9)
_publie = (S.etat_metriques_par_source().get("MZONEX_T9") or {}).get("passe_actuelle") or {}
check("l'interface reçoit la passe en cours séparément de la dernière passe",
      _publie.get("issue") == "en_cours" and _publie.get("fin_le") is None
      and "derniere_passe" in (S.etat_metriques_par_source().get("MZONEX_T9") or {}))
C.retirer_passe("MZONEX_T9", passe_t9)

# ═════════════════════════════════════════ [T10] R10 — progression de relecture
titre("[T10] R10 — la relecture mémorise OÙ elle en est (reprise exacte)")
C.enregistrer_progression("MZONEX_RELECTURE", journee="2026-09-20", page=3,
                          vehicule=PLQ_MZ, dernier_id="EV-123456")
_prog = C.progression("MZONEX_RELECTURE")
check("journée, page, véhicule et dernier identifiant sont mémorisés",
      _prog.get("journee") == "2026-09-20" and _prog.get("page") == 3
      and _prog.get("vehicule") == PLQ_MZ and _prog.get("dernier_id") == "EV-123456",
      f"→ {_prog}")
C.enregistrer_progression("MZONEX_RELECTURE", page=4, unite_traitee=True)
_prog2 = C.progression("MZONEX_RELECTURE")
check("la progression AVANCE sans perdre le contexte (journée et véhicule conservés)",
      _prog2.get("page") == 4 and _prog2.get("journee") == "2026-09-20"
      and _prog2.get("unites_traitees") == 1, f"→ {_prog2}")
check("la progression est publiée dans la santé (l'exploitant la voit)",
      (S.etat_collecte_memoire().get("progression_relecture") or {})
      .get("MZONEX_RELECTURE", {}).get("page") == 4)

# ═════════════════════════════════════════ [T11] R11 — relecture vs temps réel
titre("[T11] R11 — la relecture cède le temps réel, sans jamais le bloquer")
_possession_reel = C.verrou_de("MZONEX").acquerir("TEST_TEMPS_REEL", duree_s=30)
t0 = time.monotonic()
_attente = S.laisser_passer_temps_reel(0.4)
_duree_attente = time.monotonic() - t0
check("pendant le temps réel, la relecture ATTEND (elle cède la place)",
      _attente >= 0.3, f"→ {_attente} s")
check("…mais son attente est BORNÉE : jamais de blocage indéfini",
      _duree_attente < 3.0, f"→ {_duree_attente:.2f} s")
check("la relecture a son PROPRE verrou : le temps réel n'est pas retardé par elle",
      C.verrou_de("MZONEX_RELECTURE").occupe is False)
check("son propre verrou est bien distinct de celui de MZONEX",
      C.verrou_de("MZONEX").occupe is True
      and C.verrou_de("MZONEX").etat().get("proprietaire") == "TEST_TEMPS_REEL")
C.verrou_de("MZONEX").liberer(_possession_reel)
t0 = time.monotonic()
_attente2 = S.laisser_passer_temps_reel(0.4)
check("temps réel rendu : la relecture repart IMMÉDIATEMENT",
      _attente2 < 0.3 and time.monotonic() - t0 < 0.3, f"→ {_attente2} s")

# ══════════════════════════════════════ [T12] santé : la cause RÉELLE publiée
titre("[T12] R13/santé — /api/sante publie la cause RÉELLE (message adapté)")
S._collecter_n2_mzonex = lambda jours, attendues=None: ([], [])
S._collecter_n2_camtrackpro = lambda jours: ([], [])
S.synchroniser_trajets_valides("MIXTE")                      # une réussite N2_MIXTE


def _echec_n2(db, items, username="", **k):
    raise C.BudgetDepasse("inconnue", 2635.797, 120.0, source="N2_MIXTE",
                          raison_annulation="surveillance_limite_depassee",
                          etape_connue="ecriture")


REC.reconcilier_trajets_valides = _echec_n2
try:
    S.synchroniser_trajets_valides("MIXTE")
finally:
    REC.reconcilier_trajets_valides = _orig_reconcilier

from fastapi.testclient import TestClient                      # noqa: E402
from app.main import app                                       # noqa: E402
_client = TestClient(app)
_reponse = _client.get("/api/sante")
check("l'interface de santé répond", _reponse.status_code == 200,
      f"→ {_reponse.status_code}")
_sante = _reponse.json()
_msg = (_sante.get("collecte_messages") or {}).get("N2_MIXTE") or {}
check("le message publie la PHASE RÉELLE (écriture), jamais « inconnue »",
      _msg.get("phase") == "ecriture", f"→ {_msg.get('phase')}")
check("le message dit « budget dépassé », PAS « base verrouillée »",
      "budget dépassé" in (_msg.get("message") or "")
      and "verrouillée" not in (_msg.get("message") or ""),
      f"→ {_msg.get('message')}")
check("la classe (budget_depasse) et la raison (surveillance) restent distinctes",
      _msg.get("classe") == "budget_depasse"
      and _msg.get("raison_annulation") == "surveillance_limite_depassee",
      f"→ {_msg}")
check("l'action attendue est indiquée à l'exploitant", bool(_msg.get("action")))
check("la santé publie les métriques par source, les cycles et la progression",
      isinstance(_sante.get("metriques_par_source"), dict)
      and isinstance(_sante.get("cycles_par_source"), dict)
      and isinstance(_sante.get("progression_relecture"), dict)
      and isinstance(_sante.get("metriques_collecte"), dict))
check("le statut global reste lisible et la classe d'échec est publiée",
      isinstance(_sante.get("statut"), str) and _sante["statut"]
      and "budget_depasse" in (_sante.get("classes_en_echec") or []),
      f"→ {_sante.get('statut')} / {_sante.get('classes_en_echec')}")
check("aucun cycle en échec n'est signalé sur les verrous (ils sont libres)",
      _sante.get("cycles_en_echec") is False)

# ═════════════════════════════════════════════ [T13] stabilité de l'API
titre("[T13] STABILITÉ — deux lectures consécutives décrivent la même réalité")
_sante2 = _client.get("/api/sante").json()
_msg2 = (_sante2.get("collecte_messages") or {}).get("N2_MIXTE") or {}
check("le message ne change pas d'une lecture à l'autre (aucun état collant)",
      _msg2.get("message") == _msg.get("message")
      and _msg2.get("phase") == _msg.get("phase"))
check("les compteurs de cumul sont publiés et non nuls",
      ((_sante2.get("metriques_par_source") or {}).get("N2_MIXTE") or {})
      .get("cumul", {}).get("passes", 0) >= 1)
_sensibles = {k: v for k, v in _sante2.items()
              if any(m in str(k).lower() for m in ("token", "password", "mot_de_passe", "jeton"))}
check("aucune VALEUR secrète n'est publiée (les clés sensibles ne portent qu'un booléen "
      "de présence)",
      all(isinstance(v, bool) or v is None for v in _sensibles.values()),
      f"→ {sorted(_sensibles)}")

# ─────────────────────────────────────────────────────────────────── résultats
print("\n" + "=" * 74)
print(f"  RÉSULTAT : {R['ok']} OK / {R['ko']} KO")
print("=" * 74)
for _suffixe in ("", "-wal", "-shm"):
    try:
        Path(CHEMIN_DB + _suffixe).unlink()
    except OSError:
        pass
print("  Base de test supprimée.")
sys.exit(1 if R["ko"] else 0)
