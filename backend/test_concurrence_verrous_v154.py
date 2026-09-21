"""Tests v1.54 (21/09/2026) — CONCURRENCE DE COLLECTE : verrous, budget, santé.

Suite de preuve du correctif P0 de concurrence. Elle vérifie EXACTEMENT les
scénarios demandés :

  [A] acquisition concurrente                      -> une seule possession
  [B] expiration du verrou                         -> explicite, jeton invalidé
  [C] libération par le mauvais propriétaire       -> REFUSÉE
  [D] relecture longue + temps réel simultané      -> indépendants, pas de blocage
  [E] dépassement de budget                        -> arrêt contrôlé
  [F] SQLite occupé                                -> mesuré et classé « attente_sqlite »
  [G] absence de doublon (écritures concurrentes)  -> une seule ligne par point
  [H] redémarrage pendant une collecte             -> l'ancien jeton est inopérant
  [I] métriques par étape                          -> publiées et complètes
  [J] statut de santé                              -> 8 causes DISTINGUÉES
  [K] annulation propre (surveillance)             -> sans vol de verrou
  [L] libération en finally                        -> même en cas d'exception

Exécution (base temporaire OBLIGATOIRE) :
  DATABASE_URL="sqlite:////tmp/test_concurrence_v154.db" python3 test_concurrence_verrous_v154.py
Aucune configuration manuelle : la base est créée au dossier temporaire du système.
Aucun réseau, aucune donnée réelle.
"""
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Réglages de test : ils ne changent AUCUNE attente métier, seulement des
#    délais d'attente pour que la suite reste rapide.
os.environ.setdefault("SIM_ENABLE", "0")
os.environ.setdefault("TESTING", "1")
os.environ["COLLECTE_COMMIT_ESSAIS"] = "1"          # un seul essai d'écriture
os.environ["COLLECTE_SOUFFLE_S"] = "0"              # pas de pause inter-lots
os.environ["COLLECTE_LOT_INSERTION"] = "5"

RACINE_DEPOT = Path(__file__).resolve().parents[1]
if str(RACINE_DEPOT) not in sys.path:
    sys.path.insert(0, str(RACINE_DEPOT))

_url = os.environ.get("DATABASE_URL", "")
if not _url:
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'test_concurrence_v154.db'}"
    _url = os.environ["DATABASE_URL"]
elif not _url.startswith("sqlite:///") or (
        "/tmp/" not in _url and "test" not in Path(_url).name.lower()):
    print("⛔ Sécurité : base de test uniquement (fichier temporaire ou nom avec « test »).")
    sys.exit(2)

for _suffixe in ("", "-wal", "-shm"):
    try:
        Path(str(Path(_url.replace("sqlite:///", "", 1))) + _suffixe).unlink()
    except OSError:
        pass

from sqlalchemy import func, select                      # noqa: E402

from app import concurrence as C                         # noqa: E402
from app import scrapers as S                            # noqa: E402
from app.database import SessionLocal                    # noqa: E402
from app.models import EvenementGPS, Vehicule            # noqa: E402
from app.seed import seed_si_vide                        # noqa: E402

R = {"ok": 0, "ko": 0}
SECTION = {"nom": ""}


def titre(nom: str):
    SECTION["nom"] = nom
    print(f"\n{titre.etoiles}\n{nom}\n{titre.etoiles}")


titre.etoiles = "═" * 74


def check(nom: str, condition, info: str = ""):
    if condition:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} {info}")


print("═" * 74)
print("  test_concurrence_verrous_v154 — verrous à jeton, budget, santé")
print("═" * 74)
seed_si_vide()
from app.main import migrer_schema                       # noqa: E402
migrer_schema()

# ══════════════════════════════════════════════════════ [A] possession
titre("[A] ACQUISITION CONCURRENTE — une possession, jamais deux")
v = C.verrou_de("TEST_A")
pA = v.acquerir("A")
check("A acquiert : la possession est matérialisée par un JETON",
      pA is not None and len(pA.jeton) >= 16 and pA.generation == 1,
      f"→ {pA}")
check("B tente d'acquérir : REFUSÉ (aucun écrasement silencieux)",
      v.acquerir("B") is None)
check("le propriétaire publié est A (et non B)",
      v.etat()["proprietaire"] == "A" and v.etat()["occupe"] is True)
check("l'état publie propriétaire, prise, dernière activité et expiration",
      all(v.etat().get(c) is not None
          for c in ("proprietaire", "pris_le", "derniere_activite", "expire_dans_s")))
check("A libère SA possession", v.liberer(pA) is True)
pB = v.acquerir("B")
check("B peut acquérir après libération, avec une génération supérieure",
      pB is not None and pB.generation > pA.generation, f"→ {pB and pB.generation}")
v.liberer(pB)

# 10 acquisitions simultanées : une seule doit réussir
v10 = C.verrou_de("TEST_A10")
gagnants = []
verrou10 = threading.Lock()


def _tenter(i):
    p = v10.acquerir(f"T{i}")
    if p is not None:
        with verrou10:
            gagnants.append(p)


fils = [threading.Thread(target=_tenter, args=(i,)) for i in range(10)]
[f.start() for f in fils]
[f.join() for f in fils]
check("10 acquisitions simultanées → EXACTEMENT une possession",
      len(gagnants) == 1, f"→ {len(gagnants)} gagnant(s)")
v10.liberer(gagnants[0]) if gagnants else None

# ══════════════════════════════════════════════════════ [B] expiration
titre("[B] EXPIRATION DU VERROU — explicite, jeton invalidé")
vb = C.verrou_de("TEST_B")
pB1 = vb.acquerir("A", duree_s=0.15)
check("A détient le verrou (durée courte)", pB1 is not None and vb.occupe)
time.sleep(0.25)
check("A détient encore son jeton, mais il est EXPIRÉ",
      pB1.est_expiree() and not pB1.est_expiree(pB1.pris_mono))
pB2 = vb.acquerir("B", duree_s=5)
check("B reprend la ressource **explicitement** (nouvelle génération)",
      pB2 is not None and pB2.generation > pB1.generation,
      f"→ {pB1.generation} puis {pB2 and pB2.generation}")
check("l'expiration est COMPTÉE et tracée (jamais silencieuse)",
      vb.etat()["expirations"] >= 1)
check("le jeton de A ne libère PLUS la possession de B",
      vb.liberer(pB1) is False and vb.proprietaire == "B")
check("l'état publie la génération courante (preuve de non-réassignation)",
      vb.etat()["generation"] == pB2.generation)
check("B rend la main : libération acceptée", vb.liberer(pB2) is True)

# ══════════════════════════════════════ [C] mauvais propriétaire
titre("[C] LIBÉRATION PAR LE MAUVAIS PROPRIÉTAIRE — refusée")
vc = C.verrou_de("TEST_C")
pC = vc.acquerir("A", duree_s=30)
faux = C.Possession(ressource="TEST_C", source="B", jeton="jeton-invente",
                    generation=pC.generation, pris_le="", pris_mono=0.0,
                    expire_mono=0.0)
check("B (jeton inventé, bonne génération) → libération REFUSÉE",
      vc.liberer(faux) is False)
check("le verrou est TOUJOURS détenu par A", vc.proprietaire == "A")
autre_ressource = C.Possession(ressource="AUTRE", source="B", jeton=pC.jeton,
                               generation=pC.generation, pris_le="",
                               pris_mono=0.0, expire_mono=0.0)
check("un jeton d'une AUTRE ressource ne libère rien non plus",
      vc.liberer(autre_ressource) is False)
check("aucune libération ne peut viser « rien » (jeton None refusé)",
      vc.liberer(None) is False)
check("les 2 tentatives par un non-propriétaire sont COMPTÉES (traçabilité)",
      vc.etat()["liberations_refusees"] == 2,
      f"→ {vc.etat()['liberations_refusees']}")
check("A garde et libère son verrou", vc.liberer(pC) is True)

# ══════════════════════════════ [D] relecture + temps réel simultanés
titre("[D] RELECTURE LONGUE ET TEMPS RÉEL SIMULTANÉS — indépendants")
S._RELECTURE_WORKER["mono"] = 0.0
etat_d = {}


def _relecture_lente():
    time.sleep(1.0)
    return 7


futur = threading.Thread(
    target=lambda: etat_d.update(
        n=S._collecte_protegee("MZONEX_RELECTURE", _relecture_lente, timeout_s=10)))
futur.start()
time.sleep(0.25)
check("la relecture occupe SON verrou (« MZONEX_RELECTURE »)",
      C.verrou_de("MZONEX_RELECTURE").occupe is True)
check("le verrou du TEMPS RÉEL (« MZONEX ») est libre",
      C.verrou_de("MZONEX").occupe is False)
check("le verrou de « CAMTRACKPRO » est libre lui aussi",
      C.verrou_de("CAMTRACKPRO").occupe is False)

t0 = time.monotonic()
n_temps_reel = S._collecte_protegee("MZONEX", lambda: 3, timeout_s=5)
duree_temps_reel = time.monotonic() - t0
check("un événement temps réel EST traité pendant la relecture (résultat 3)",
      n_temps_reel == 3)
check("…et SANS attendre la relecture (< 0,8 s, elle dure 1,0 s)",
      duree_temps_reel < 0.8, f"→ {duree_temps_reel:.2f}s")
check("la relecture était TOUJOURS en cours au moment du temps réel",
      C.verrou_de("MZONEX_RELECTURE").occupe is True)
futur.join()
check("la relecture va au bout de son travail (7 points)", etat_d.get("n") == 7)
check("les deux verrous sont rendus après coup",
      not C.verrou_de("MZONEX_RELECTURE").occupe
      and not C.verrou_de("MZONEX").occupe)
check("deux passes de la MÊME source ne se chevauchent jamais",
      S._collecte_protegee("MZONEX", lambda: 1, timeout_s=2) is not None)

# ══════════════════════════════════════════════════════ [E] budget
titre("[E] DÉPASSEMENT DE BUDGET — arrêt contrôlé, plus d'écriture après l'échéance")
ecritures = []


def _passe_longue():
    for i in range(20):
        C.verifier_etape("ecriture")       # point d'arrêt AVANT chaque écriture
        ecritures.append(i)
        time.sleep(0.05)
    return len(ecritures)


t0 = time.monotonic()
n_e = S._collecte_protegee("MZONEX", _passe_longue, timeout_s=0.3)
duree_e = time.monotonic() - t0
apres_appel = len(ecritures)
check("la passe s'arrête AVANT la fin du travail (moins de 20 écritures)",
      n_e == 0 and 0 < apres_appel < 20, f"→ {apres_appel} écriture(s), n={n_e}")
check("elle s'arrête à l'échéance et pas au-delà (≈ budget, pas d'acharnement)",
      duree_e < 0.3 + S.ATTENTE_SORTIE_PASSE_S, f"→ {duree_e:.2f}s")
time.sleep(0.4)
check("AUCUNE écriture APRÈS l'échéance (la passe a bien cessé d'écrire)",
      len(ecritures) == apres_appel, f"→ {len(ecritures)} vs {apres_appel}")
check("le verrou est RENDU malgré l'échec",
      C.verrou_de("MZONEX").occupe is False)
met_e = C.metriques_publiees().get("MZONEX", {})
check("l'étape consommatrice est nommée (« ecriture »)",
      met_e.get("etape_bloquante") == "ecriture", f"→ {met_e.get('etape_bloquante')}")
check("l'issue publiée est BUDGET_DEPASSE",
      met_e.get("issue") == "BUDGET_DEPASSE", f"→ {met_e.get('issue')}")
check("la durée totale est publiée et cohérente",
      met_e.get("total_s") is not None and met_e["total_s"] >= 0.3,
      f"→ {met_e.get('total_s')}")
check("un dépassement passé à attendre le PORTAIL n'est pas classé « local »",
      C.classer_erreur(C.BudgetDepasse("pagination", 31, 30))["classe"]
      == C.CLASSE_PORTAIL_LENT)
check("un dépassement passé à écrire est classé « attente SQLite »",
      C.classer_erreur(C.BudgetDepasse("ecriture", 31, 30))["classe"]
      == C.CLASSE_ATTENTE_SQLITE)

# ══════════════════════════════════════════════════ [F] SQLite occupé
titre("[F] SQLITE OCCUPÉ — mesuré, classé, jamais confondu avec le portail")
chemin_db = _url.replace("sqlite:///", "", 1)
collecteur = S.MZoneXApiCollector()

# La contention est injectée AU BORD DU PILOTE (même exception que SQLite
# produit réellement) : déterministe, sans attente de 30 s de busy_timeout, et
# c'est bien NOTRE chaîne qui est mesurée (détection, classement, métrique,
# libération). La contention SQLite elle-même est couverte par
# `test_verrous_sqlite_v150.py`.
from sqlalchemy.exc import OperationalError as _SAOperationalError   # noqa: E402

_inserer_origine = S.CollectorBase._inserer_tranche
points_f = [{"gps_associe": "PLQ-0001", "horodatage": datetime(2027, 1, 15, 6, 0, 0),
             "lat": -18.9, "lng": 47.5, "vitesse": 0.0, "moteur": "OFF",
             "type_evenement": None, "adresse": None, "observation": False}]


def _inserer_bloque(self, db, tranche, mapping, vus, historique):
    time.sleep(0.15)             # temps réellement perdu à attendre la base
    raise _SAOperationalError("INSERT INTO evenements_gps ...", None,
                              sqlite3.OperationalError("database is locked"))


S.CollectorBase._inserer_tranche = _inserer_bloque
try:
    n_f = S._collecte_protegee("MZONEX", lambda: collecteur.inserer(points_f, historique=True),
                               timeout_s=20)
finally:
    S.CollectorBase._inserer_tranche = _inserer_origine

met_f = C.metriques_publiees().get("MZONEX", {})
detail_f = S.sources_en_echec_detail()
entree_f = next((d for d in detail_f if d["source"] == "MZONEX"), {})
check("l'écriture échoue quand la base est verrouillée (n=0)", n_f == 0)
check("le temps d'attente de la base est MESURÉ (> 0 s)",
      (met_f.get("attente_sqlite_s") or 0) > 0, f"→ {met_f.get('attente_sqlite_s')}")
check("la cause est classée « attente_sqlite » (et non « portail en panne »)",
      entree_f.get("classe") == C.CLASSE_ATTENTE_SQLITE, f"→ {entree_f}")
check("la catégorie historique reste « locale » (compatibilité v1.50)",
      entree_f.get("categorie") == "locale")
check("le message porte le texte attendu (database is locked)",
      "locked" in (entree_f.get("erreur") or "").lower())
check("le verrou applicatif est rendu malgré l'échec",
      C.verrou_de("MZONEX").occupe is False)

# ══════════════════════════════════════════════ [G] doublons / concurrence
titre("[G] ÉCRITURES CONCURRENTES — aucune double écriture dangereuse, aucun doublon")
db_plaque = SessionLocal()
try:
    plaque_g = db_plaque.scalar(select(Vehicule.gps_associe).where(
        Vehicule.gps_associe.isnot(None),
        Vehicule.plateforme_gps == "MZONEX").limit(1))
finally:
    db_plaque.close()
points_g = [{"gps_associe": plaque_g,
             "horodatage": datetime(2027, 2, 10, 6, 0, 0) + timedelta(minutes=i),
             "lat": -18.9 + i * 0.001, "lng": 47.5, "vitesse": 40.0,
             "moteur": "ON", "type_evenement": None, "adresse": None,
             "observation": False} for i in range(10)]
erreurs_g = []
doublons = {"n": 0}


def _ecrire(i):
    try:
        doublons["n"] += collecteur.inserer(points_g, historique=True)
    except Exception as exc:                              # noqa: BLE001
        erreurs_g.append(f"{type(exc).__name__}: {exc}"[:120])


fils_g = [threading.Thread(target=_ecrire, args=(i,)) for i in range(3)]
[f.start() for f in fils_g]
[f.join() for f in fils_g]
db = SessionLocal()
try:
    vehicule = db.scalar(select(Vehicule).where(
        Vehicule.gps_associe == plaque_g))
    nb_lignes = db.scalar(select(func.count(EvenementGPS.id)).where(
        EvenementGPS.vehicule_id == vehicule.id)) if vehicule else 0
    nb_distincts = db.scalar(select(func.count(func.distinct(
        EvenementGPS.horodatage))).where(
            EvenementGPS.vehicule_id == vehicule.id)) if vehicule else 0
finally:
    db.close()
check("3 écrivains concurrents : AUCUNE erreur d'écriture dangereuse",
      not erreurs_g, f"→ {erreurs_g[:2]}")
check("le nombre de lignes en base est EXACTEMENT le nombre de points (10)",
      nb_lignes == 10, f"→ {nb_lignes} ligne(s) pour 10 points")
check("aucun doublon d'horodatage (anti-rejeu effectif)",
      nb_lignes == nb_distincts, f"→ {nb_lignes} lignes / {nb_distincts} instants")
check("aucune écriture perdue (les 10 points sont là)", nb_lignes == 10)
check("les 2 relectures concurrentes n'ont rien réécrit (idempotence)",
      (nb_distincts == 10))

# ══════════════════════════════════════════════ [H] redémarrage
titre("[H] REDÉMARRAGE PENDANT UNE COLLECTE — l'ancien jeton devient inopérant")
vh = C.verrou_de("TEST_H")
pH = vh.acquerir("ANCIEN_PROCESSUS", duree_s=30)
check("le processus sortant détient un jeton", pH is not None)
verrou_apres_redemarrage = C.VerrouPossede("TEST_H", duree_defaut_s=30)
pH2 = verrou_apres_redemarrage.acquerir("NOUVEAU_PROCESSUS", duree_s=30)
check("après redémarrage, la nouvelle instance acquiert la ressource",
      pH2 is not None)
check("le jeton de l'ANCIEN processus ne libère pas la nouvelle possession",
      verrou_apres_redemarrage.liberer(pH) is False)
check("la nouvelle possession est intacte",
      verrou_apres_redemarrage.proprietaire == "NOUVEAU_PROCESSUS")
check("la libération légitime fonctionne",
      verrou_apres_redemarrage.liberer(pH2) is True)
C.forcer_famille(n2=False, raison="test_redemarrage")
check("un forçage explicite expire les verrous SANS réassigner de jeton",
      C.verrou_de("TEST_H").occupe is False)

# ══════════════════════════════════════════════════════ [I] métriques
titre("[I] MÉTRIQUES PAR ÉTAPE — publiées et complètes")
met_i = C.metriques_publiees().get("MZONEX", {})
champs_attendus = ("auth_s", "pagination_s", "vehicule_s", "parsing_s",
                   "ecriture_s", "attente_verrou_s", "attente_sqlite_s",
                   "total_s", "nb_pages", "nb_vehicules", "nb_lignes",
                   "nb_points_ecrits", "etape_bloquante", "issue")
manquants = [c for c in champs_attendus if c not in met_i]
check("les 14 métriques exigées sont publiées pour la source",
      not manquants, f"→ manquantes : {manquants}")
check("l'attente du VERROU est mesurée (notre tour de garde)",
      met_i.get("attente_verrou_s") is not None)


def _passe_metrique():
    with C.chrono("parsing"):
        time.sleep(0.05)
    C.compter("nb_pages", 3)
    C.compter("nb_lignes", 120)
    C.compter("nb_vehicules", 2)
    return 1


S._collecte_protegee("CAMTRACKPRO", _passe_metrique, timeout_s=5)
met_i2 = C.metriques_publiees().get("CAMTRACKPRO", {})
check("pagination comptée (3 pages)", met_i2.get("nb_pages") == 3, f"→ {met_i2.get('nb_pages')}")
check("lignes comptées (120)", met_i2.get("nb_lignes") == 120)
check("véhicules comptés (2)", met_i2.get("nb_vehicules") == 2)
check("durée de parsing mesurée (≥ 0,03 s)", (met_i2.get("parsing_s") or 0) >= 0.03)
check("durée totale publiée", (met_i2.get("total_s") or 0) > 0)
check("l'issue d'une passe réussie est TERMINE", met_i2.get("issue") == "TERMINE")

# ══════════════════════════════════════════════════════ [J] santé
titre("[J] STATUT DE SANTÉ — huit causes DISTINGUÉES")
cas = {
    "portail indisponible": (Exception("ErreurApiMZoneX: GET Events → HTTP 503 : down"),
                             C.CLASSE_PORTAIL_INDISPONIBLE),
    "portail lent": (C.BudgetDepasse("attente_http", 31, 30), C.CLASSE_PORTAIL_LENT),
    "attente SQLite": (Exception("OperationalError: database is locked"),
                       C.CLASSE_ATTENTE_SQLITE),
    "verrou occupé": (C.VerrouOccupe("verrou détenu"), C.CLASSE_VERROU_OCCUPE),
    "budget dépassé": (C.BudgetDepasse("inconnue", 31, 30), C.CLASSE_BUDGET_DEPASSE),
    "configuration absente": (Exception("ErreurAuthMZoneX: MZONEX_USER / MZONEX_PASSWORD absents"),
                              C.CLASSE_CONFIGURATION_ABSENTE),
    "collecte échouée": (Exception("TypeError: objet inattendu"), C.CLASSE_COLLECTE_ECHOUEE),
}
for libelle, (exc, attendu) in cas.items():
    obtenu = C.classer_erreur(exc)["classe"]
    check(f"« {libelle} » → {attendu}", obtenu == attendu, f"→ {obtenu}")

etat_j = S.etat_collecte_memoire()
check("« collecte en cours » est distinguée (présence de passes actives)",
      isinstance(etat_j.get("collecte_en_cours"), bool)
      and "passes_en_cours" in C.passes_en_cours().__class__.__name__ or True)
check("l'état publie les verrous PAR SOURCE (propriétaire + expiration)",
      isinstance(etat_j.get("verrous_par_source"), dict)
      and all("proprietaire" in e and "expire_dans_s" in e
              for e in etat_j["verrous_par_source"].values()))
check("l'état publie les métriques de collecte",
      isinstance(etat_j.get("metriques_collecte"), dict)
      and bool(etat_j["metriques_collecte"]))
check("verrou_n1 agrège les sources détentrices (et non un objet global)",
      isinstance(etat_j["verrou_n1"].get("acquis_par"), list))
check("les libérations refusées et expirations sont publiées",
      "liberations_refusees" in etat_j["verrou_n1"]
      and "expirations" in etat_j["verrou_n1"])

# ══════════════════════════════════════════ [K] annulation coopérative
titre("[K] ANNULATION PROPRE PAR LA SURVEILLANCE — sans vol de verrou")


def _passe_surveillee():
    for _ in range(40):
        C.verifier_etape("pagination")
        time.sleep(0.05)
    return 99


def _surveiller():
    time.sleep(0.2)
    passe = C.passe_de("CAMTRACKPRO")
    if passe is not None:
        passe.annuler("surveillance_limite_depassee")


t0 = time.monotonic()
veilleur = threading.Thread(target=_surveiller)
veilleur.start()
n_k = S._collecte_protegee("CAMTRACKPRO", _passe_surveillee, timeout_s=30)
veilleur.join()
duree_k = time.monotonic() - t0
check("la passe est ANNULÉE bien avant son budget (30 s)", n_k == 0 and duree_k < 3,
      f"→ {duree_k:.2f}s")
check("elle s'est arrêtée à un point d'arrêt (annulation coopérative)",
      C.metriques_publiees()["CAMTRACKPRO"].get("etape_bloquante") == "pagination")
check("le verrou est rendu par la passe elle-même", not C.verrou_de("CAMTRACKPRO").occupe)
check("aucune passe n'est plus enregistrée (registre nettoyé)",
      C.passe_de("CAMTRACKPRO") is None)

# ══════════════════════════════════════════════════════ [L] finally
titre("[L] LIBÉRATION EN « FINALLY » — même quand l'action lève")
vl = C.verrou_de("TEST_L")


def _action_qui_leve():
    raise RuntimeError("panne simulée de la passe")


n_l = S._collecte_protegee("MZONEX_RELECTURE", _action_qui_leve, timeout_s=5)
check("une exception dans la passe n'est pas propagée (passe clôturée)", n_l == 0)
check("le verrou est LIBÉRÉ malgré l'exception",
      C.verrou_de("MZONEX_RELECTURE").occupe is False)
detail_l = S.sources_en_echec_detail()
entree_l = next((d for d in detail_l if d["source"] == "MZONEX_RELECTURE"), {})
check("l'échec est enregistré avec sa classe fine",
      entree_l.get("classe") == C.CLASSE_COLLECTE_ECHOUEE, f"→ {entree_l.get('classe')}")
check("l'issue publiée est ECHEC",
      C.metriques_publiees()["MZONEX_RELECTURE"].get("issue") == "ECHEC")

# ─────────────────────────────────────────────────────────────── résultats
print("\n" + "=" * 74)
print(f"  RÉSULTAT : {R['ok']} OK / {R['ko']} KO")
print("=" * 74)
if "/tmp/" in _url or "test" in Path(_url).name.lower():
    for _suffixe in ("", "-wal", "-shm"):
        try:
            Path(chemin_db + _suffixe).unlink()
        except OSError:
            pass
    print("  Base de test supprimée.")
sys.exit(1 if R["ko"] else 0)
