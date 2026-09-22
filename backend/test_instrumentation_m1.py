"""M1 — instrumentation de la collecte : tests SANS base, SANS réseau, SANS portail.

Ce que ces tests vérifient (et rien d'autre) — ce sont les garanties M1 :

  1. la LISTE BLANCHE des champs publiables et l'assainissement des textes
     (aucun SID, jeton, cookie, mot de passe, URL complète, réponse portail) ;
  2. « source LANCÉE » et « source NON DÉMARRÉE » — avec la RAISON RÉELLE et,
     pour une échéance, la PHASE et le MOTIF réels de l'exception ;
  3. la SÉPARATION par source : `collecte.source_resultat` ne publie que les
     compteurs de SA source, et `collecte.passe_synthese` DÉCLARE le partage
     quand la passe couvre plusieurs sources (valable pour N2_MIXTE) ;
  4. AUCUN compteur après un échec : un rollback (ou une échéance) ne laisse
     derrière lui ni « reçu », ni « écrit », ni motif de rejet compté ;
  5. les CHRONOMÈTRES : jamais de mesure `None`, dernière unité mesurée, unité
     et page mesurées en exception, parsing et écran mesurés en exception,
     aucune double mesure (mesures emboîtées : pagination ⊆ véhicule) ;
  6. VOLUMÉTRIE : une trace par PASSE (et une par période close), jamais une
     trace par page ni par véhicule ; file bornée, pertes COMPTÉES, jamais
     propagées ; rétention DÉCLARÉE et purge INACTIVE ;
  7. INERTIE MÉTIER : les clés existantes de `/api/sante` sont toujours là
     (`etat_collecte_memoire()` EST la source de `etat_collecteur`) et les
     phases/compteurs existants ne sont ni renommés ni retirés.

Hors périmètre de ce fichier (déjà couvert ailleurs, NON rejoué ici) :
  · `/api/sante` au niveau HTTP → `test_concurrence_sante_v154.py` ;
  · le PARSING des lignes de portail → `test_positions_portails_v145.py` ;
  · les verrous et l'interruption → `test_verrous_sqlite_v150.py`.
Les deux remplacements volontaires de fonctions de parsing (T-M1-11, T-M1-12)
servent uniquement à mesurer l'INSTRUMENTATION, jamais à éviter une vérification.

Exécution (depuis `backend/`, aucun portail contacté, aucune base réelle) :
    DATABASE_URL="sqlite:////tmp/test_instrumentation_m1.db" python3 test_instrumentation_m1.py
La base éventuellement créée dans /tmp est supprimée à la fin.
"""
import os
import sys
import time
from datetime import date, datetime

os.environ.setdefault("SIM_ENABLE", "0")
os.environ.setdefault("DATABASE_URL",
                      "sqlite:////tmp/test_instrumentation_m1.db")
os.environ["MZONEX_REPLI_ECRAN"] = "1"

from app import concurrence as C
from app import scrapers as S
from app import api_wialon as W

R = {"ok": 0, "ko": 0}
JOUR = [date(2026, 9, 22)]


def check(nom, cond, info=""):
    if cond:
        R["ok"] += 1
        print(f"  OK   {nom}")
    else:
        R["ko"] += 1
        print(f"  KO   {nom} {info}")


def titre(texte):
    print(f"\n{texte}\n{'-' * min(len(texte), 78)}")


# ── la FILE est lue DIRECTEMENT : l'écrivain dédié n'est pas démarré, donc
#    aucune ligne d'audit n'est écrite dans la base pendant ces tests.
S._TRACES_WORKER["demarre"] = True


def vider_file():
    while True:
        try:
            S._TRACES_FILE.get_nowait()
        except Exception:
            return


def traces_file(timeout=0.30):
    """Toutes les traces présentes en file (attente bornée, jamais infinie)."""
    vues, limite = [], time.monotonic() + timeout
    while time.monotonic() < limite:
        try:
            vues.append(S._TRACES_FILE.get_nowait())
        except Exception:
            time.sleep(0.005)
    return vues


def traces_action(vues, action):
    return [details for nom, details in vues if nom == action]


def passe_de(source, budget=30.0):
    return C.PasseCourante(source, budget,
                           debut_le="2026-09-22T10:00:00")


# ═══════════════════════════════════════════════════════ [T-M1-01] liste blanche
titre("[T-M1-01] assainissement d'un texte : URL complète et secrets masqués")
CAS = [
    ("https://portail.lss.mg/ajax.html?svc=trajets&sid=ABC123DEF", "ABC123DEF"),
    ("client_secret=csec-42", "csec-42"),
    ("signature=sig-9", "sig-9"),
    ("sign=s-9", "s-9"),
    ("access_hash=ah-9", "ah-9"),
    ("imei=355661234567890", "355661234567890"),
    ("hwid=HW-77", "HW-77"),
    ("gsm=261340000000", "261340000000"),
    ("authorization=Bearer-zzz", "Bearer-zzz"),
    ("cookie=ck-1", "ck-1"),
    ("token=t-1", "t-1"),
    ("password=p-1", "p-1"),
    ("api_key=k-1", "k-1"),
    ("SID=MAJUSCULE-9", "MAJUSCULE-9"),
]
for brut, secret in CAS:
    sortie = C.texte_trace_sur(brut)
    check(f"« {secret} » n'est jamais publié",
          secret not in (sortie or "") and "<masqué>" in (sortie or ""),
          f"→ {sortie}")
long = "x" * 500
check("un texte trop long est borné (120)",
      len(C.texte_trace_sur(long) or "") <= 120)
check("texte_trace_sur(None) rend None", C.texte_trace_sur(None) is None)

# ═══════════════════════════════════════════════════════ [T-M1-02] liste blanche
titre("[T-M1-02] liste blanche : seules les clés prévues sortent")
entree = {"source": "MZONEX", "issue": "TERMINE", "ecran_s": 1.5,
          "brut": "réponse portail", "reponse_html": "<html>reponse</html>",
          "headers": {"Cookie": "ck-1"}, "details": {"a": 1},
          "sid": "ABC123", "imei": "355661234567890", "gsm": "261340000000",
          "authorization": "Bearer zzz"}
sortie = C.champs_publiables(entree)
check("clés hors liste et clés interdites ABSENTES",
      set(sortie) == {"source", "issue", "ecran_s"}, f"→ {sorted(sortie)}")
check("aucune valeur interdite ne subsiste",
      not any(v in str(sortie) for v in ("ABC123", "355661234567890",
                                         "261340000000", "Bearer zzz")))
rejet = C.champs_publiables({"issue": {"texte": "x"}, "attente_http_s": 2.0})
check("une valeur dict est ÉCARTÉE même pour une clé autorisée",
      "issue" not in rejet and rejet.get("attente_http_s") == 2.0,
      f"→ {rejet}")

# ═══════════════════════════════════════════════════════ [T-M1-03] lancement
titre("[T-M1-03] « source lancée » : la source le DIT, la passe s'en souvient")
vider_file()
passe = passe_de("MZONEX")
S._tracer_lancement("MZONEX", passe, jours=JOUR)
vues = traces_file()
details = traces_action(vues, "collecte.source_lancee")
check("1 trace collecte.source_lancee", len(details) == 1, f"→ {len(details)}")
check("la trace porte source, issue de lancement et budget",
      details and details[0].get("source") == "MZONEX"
      and details[0].get("source_demarree") is True
      and details[0].get("budget_s") == 30.0, f"→ {details}")
check("la passe mémorise la source lancée",
      passe.sources_lancees == ["MZONEX"]
      and passe.metriques.demarrage_le, f"→ {passe.sources_lancees}")

# ═══════════════════════════════════════════════════ [T-M1-04] échéance N2 MIXTE
titre("[T-M1-04] échéance : la source attendue est dite NON DÉMARRÉE (phase et motif réels)")
vider_file()
S.forcer_deverrouillage_n2("test M1")
_orig_sync = S._synchroniser_trajets_valides


def _echeance(source=None):
    raise C.BudgetDepasse("attente_http", 121.0, 120.0, source="N2_MIXTE",
                          raison_annulation="surveillance")


S._synchroniser_trajets_valides = _echeance
try:
    resultat = S.synchroniser_trajets_valides("MIXTE")
finally:
    S._synchroniser_trajets_valides = _orig_sync
vues = traces_file()
non_demarrees = traces_action(vues, "collecte.source_non_demarree")
noms = sorted(d.get("source") for d in non_demarrees)
check("l'échéance est rendue telle quelle (aucune exception avalée)",
      resultat.get("budget_depasse") is True
      and resultat.get("etape") == "attente_http"
      and resultat.get("raison_annulation") == "surveillance",
      f"→ {resultat}")
check("les 2 sources attendues sont dites NON DÉMARRÉES",
      noms == ["N2_CAMTRACKPRO", "N2_MZONEX"], f"→ {noms}")
check("la raison est réelle, la phase et le motif viennent de l'exception",
      all(d.get("non_demarree_raison") == "echeance_atteinte_ailleurs"
          and d.get("phase") == "attente_http"
          and d.get("raison_annulation") == "surveillance"
          for d in non_demarrees), f"→ {non_demarrees}")
check("aucune source n'est dite LANCÉE (l'échéance a précédé l'appel réseau)",
      not traces_action(vues, "collecte.source_lancee"))

# ═══════════════════════════════════════════════════ [T-M1-05] refus de verrou
titre("[T-M1-05] verrou N1 refusé : aucune collecte, mais la raison est publiée")
vider_file()
possession = S._acquerir_verrou_n1("MZONEX", duree_s=60)
appels = {"n": 0}


def _action_portail():
    appels["n"] += 1
    return 0


try:
    rendu = S._collecte_protegee("MZONEX", _action_portail, timeout_s=1.0)
finally:
    S._liberer_verrou_n1(possession)
non_demarrees = traces_action(traces_file(), "collecte.source_non_demarree")
check("aucun appel portail n'a été fait", appels["n"] == 0 and rendu == 0,
      f"→ appels={appels['n']} rendu={rendu}")
check("la source est dite NON DÉMARRÉE pour cause de verrou occupé",
      len(non_demarrees) == 1
      and non_demarrees[0].get("non_demarree_raison") == "verrou_occupe"
      and non_demarrees[0].get("phase") == "attente_verrou",
      f"→ {non_demarrees}")

# ═════════════════════════════════════════════ [T-M1-06] MIXTE : séparation
titre("[T-M1-06] passe MIXTE : compteurs PAR SOURCE, synthèse DÉCLARÉE PARTAGÉE")
vider_file()
passe = passe_de("N2_MIXTE", 120.0)
S._tracer_lancement("MZONEX", passe, jours=JOUR)
S._tracer_lancement("CAMTRACKPRO", passe, jours=JOUR)
S._tracer_source_resultat(
    "MZONEX", passe_nom="N2_MIXTE", jours=JOUR, origine="API_N2",
    stats={"recus": 10, "crees": 4, "ignores": 2},
    duree_lecture_s=3.0, duree_ecriture_s=1.0,
    sources_passe=["MZONEX", "CAMTRACKPRO"], confirme=True)
S._tracer_source_resultat(
    "CAMTRACKPRO", passe_nom="N2_MIXTE", jours=JOUR, origine="API_N2",
    stats={"recus": 7, "crees": 3, "ignores": 1},
    duree_lecture_s=9.0, duree_ecriture_s=2.0,
    sources_passe=["MZONEX", "CAMTRACKPRO"], confirme=True)
S._tracer_passe_synthese(passe, {"issue": "TERMINE", "total_s": 15.0,
                                 "budget_s": 120.0, "attente_http_s": 11.0,
                                 "source_demarree": True})
vues = traces_file()
resultats = traces_action(vues, "collecte.source_resultat")
syntheses = traces_action(vues, "collecte.passe_synthese")
par_source = {d.get("source"): d for d in resultats}
check("2 résultats, un PAR SOURCE, avec LEURS compteurs",
      len(resultats) == 2
      and par_source["MZONEX"].get("recus") == 10
      and par_source["MZONEX"].get("ecrits") == 4
      and par_source["MZONEX"].get("ignores") == 2
      and par_source["CAMTRACKPRO"].get("recus") == 7
      and par_source["CAMTRACKPRO"].get("ecrits") == 3,
      f"→ {resultats}")
check("chaque résultat confirme qu'il vient d'un commit réussi",
      all(d.get("confirme_apres_commit") is True
          and d.get("metriques_attribuables_a") == [d.get("source")]
          and d.get("compteurs_partages") is False for d in resultats))
check("la synthèse de passe est DÉCLARÉE PARTAGÉE (2 sources)",
      len(syntheses) == 1
      and syntheses[0].get("compteurs_partages") is True
      and syntheses[0].get("metriques_attribuables_a") is None
      and sorted(syntheses[0].get("couvre_sources")) == ["CAMTRACKPRO",
                                                         "MZONEX"],
      f"→ {syntheses}")
check("aucun total fusionné n'est publié dans la synthèse de passe",
      syntheses and not any(cle in syntheses[0]
                            for cle in ("recus", "ecrits", "ignores")))

# ═════════════════════════════════════════════════════ [T-M1-07] volumétrie
titre("[T-M1-07] une trace par PASSE et par PÉRIODE CLOSE — jamais par page")
vider_file()
passe = passe_de("N2_MIXTE", 120.0)
S._tracer_lancement("MZONEX", passe, jours=JOUR)
gros = {"issue": "TERMINE", "total_s": 100.0, "budget_s": 120.0,
        "nb_pages": 2000, "nb_lignes_lues": 250000, "nb_commits": 1000}
S._tracer_passe_synthese(passe, gros)
vues = traces_file()
check("1 seule ligne de passe pour 2000 pages",
      len(traces_action(vues, "collecte.passe_synthese")) == 1
      and not traces_action(vues, "collecte.page")
      and not traces_action(vues, "collecte.vehicule"), f"→ {len(vues)}")
_orig_now = S.now_local
S._SYNTHESE_ETAT.pop("SYNTH_TEST", None)
S.now_local = lambda: datetime(2026, 9, 22, 10, 30, 0)
try:
    S._synthese_periodique("SYNTH_TEST", gros)
    check("période non close : aucune ligne écrite",
          not traces_action(traces_file(), "collecte.synthese_heure"))
    S.now_local = lambda: datetime(2026, 9, 22, 11, 5, 0)
    S._synthese_periodique("SYNTH_TEST", gros)
    vues = traces_file()
    heures = traces_action(vues, "collecte.synthese_heure")
    check("bascule d'heure : la période CLOSE est émise UNE fois, avec le "
          "cumul de l'heure écoulée",
          len(heures) == 1 and heures[0].get("heure") == "2026-09-22T10"
          and heures[0].get("passes") == 1
          and heures[0].get("nb_pages") == 2000, f"→ {heures}")
    S.now_local = lambda: datetime(2026, 9, 22, 12, 5, 0)
    S._synthese_periodique("SYNTH_TEST", gros)
    vues += traces_file()
    heures = traces_action(vues, "collecte.synthese_heure")
    check("chaque heure CLOSE est publiée avec SON cumul (passes, pages, "
          "volume, durée)",
          len(heures) == 2 and heures[1].get("heure") == "2026-09-22T11"
          and heures[1].get("passes") == 1
          and heures[1].get("nb_pages") == 2000
          and heures[1].get("nb_lignes_lues") == 250000
          and heures[1].get("somme_total_s") == 100.0
          and heures[1].get("issues") == "TERMINE=1", f"→ {heures}")
    check("le compteur d'issues est publié en CHAÎNE bornée (la liste "
          "blanche refuse les dicts : `dict(issues)` ne publiait rien)",
          all(not isinstance(h.get("issues"), dict) for h in heures),
          f"→ {heures}")
finally:
    S.now_local = _orig_now
    S._SYNTHESE_ETAT.pop("SYNTH_TEST", None)
check("la rétention est DÉCLARÉE et la purge INACTIVE",
      S.traces_info().get("purge_active") is False
      and S.traces_info().get("retention_cycles_jours") == 30
      and S.traces_info().get("retention_syntheses_jours") == 90,
      f"→ {S.traces_info()}")

# ═══════════════════════════════════════════════ [T-M1-08] file pleine
titre("[T-M1-08] file pleine : trace PERDUE, comptée, jamais propagée")
vider_file()
taille = S._TRACES_FILE.maxsize
pertes_avant = S._TRACES_PERTES
S._TRACES_PERTES = 0
S._TRACES_FILE.maxsize = 1
try:
    S._TRACES_FILE.put_nowait(("collecte.test", {}))
    for _ in range(5):
        S._tracer("collecte.source_lancee", {"source": "MZONEX"})
    leve = False
except Exception:
    leve = True
finally:
    S._TRACES_FILE.maxsize = taille
    vider_file()
check("aucune exception ne remonte à la collecte", leve is False)
check("les pertes sont COMPTÉES", S.traces_perdues() >= 1,
      f"→ {S.traces_perdues()}")
S._TRACES_PERTES = pertes_avant

# ══════════════════════════════════════════════ [T-M1-09] mesurer_depuis
titre("[T-M1-09] `mesurer_depuis` : jamais de mesure None, jamais deux fois")
passe = passe_de("MZONEX")
with C.activer_passe(passe):
    avant = passe.metriques.parsing_s
    depart = C.mesurer_depuis("parsing", None)
    check("un départ None ne mesure RIEN",
          passe.metriques.parsing_s == avant
          and isinstance(depart, float) and depart > 0,
          f"→ {passe.metriques.parsing_s}")
    time.sleep(0.02)
    C.mesurer_depuis("parsing", depart)
    mesure = passe.metriques.parsing_s
    check("la mesure est prise une fois (≥ 20 ms)", mesure >= 0.019,
          f"→ {mesure}")
    C.mesurer_depuis("parsing", None)
    check("aucun cumul fantôme après une mesure None",
          passe.metriques.parsing_s == mesure, f"→ {passe.metriques.parsing_s}")

# ════════════════════════════════════════════════════ [T-M1-10] attente HTTP
titre("[T-M1-10] attente HTTP Wialon mesurée MÊME sur timeout (jamais 0)")
vider_file()
_orig_httpx = W.httpx


class _FauxHttpx:
    class TimeoutException(Exception):
        pass

    @staticmethod
    def Timeout(*args, **kwargs):
        return None

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            time.sleep(0.02)   # attente RÉELLE : la mesure doit être > 0,000
            raise _FauxHttpx.TimeoutException("simulé")


W.httpx = _FauxHttpx
passe = passe_de("N2_CAMTRACKPRO", 60.0)
api = W.ApiWialon.__new__(W.ApiWialon)
api._sid = None
api._jeton = "faux"
api._ids_rapport = None
try:
    with C.activer_passe(passe):
        try:
            api._appel("token/login", {})
            erreur = None
        except Exception as exc:                      # noqa: BLE001
            erreur = type(exc).__name__
finally:
    W.httpx = _orig_httpx
check("l'exception d'origine remonte inchangée (TimeoutError)",
      erreur == "TimeoutError", f"→ {erreur}")
check("attente_http_s est mesurée malgré l'échec",
      passe.metriques.attente_http_s > 0, f"→ {passe.metriques.attente_http_s}")

# ══════════════════════════ [T-M1-11] unités, pages, parsing (rapport Wialon)
titre("[T-M1-11] chaque mesure est PRISE (y compris en exception et sur la "
      "DERNIÈRE unité)")
vider_file()
_orig_col = W.detecter_colonnes_wialon
_orig_item = W.item_depuis_ligne_rapport
_orig_mesurer_w = W.mesurer_depuis
_orig_compter_w = W.compter
W.detecter_colonnes_wialon = lambda headers=None: {}
W.item_depuis_ligne_rapport = lambda nom, cellules, col_map=None: (
    time.sleep(0.00002) or None)      # parsing RÉEL de chaque ligne
mesures: list = []


def _recorder_mesure(champ, depuis_mono):
    """Preuve DIRECTE qu'une mesure est prise : on compte les appels réels."""
    mesures.append(champ)
    return _orig_mesurer_w(champ, depuis_mono)


W.mesurer_depuis = _recorder_mesure


def _faux_appel(svc, params, reessai=True):
    time.sleep(0.003)                 # coût RÉEL de l'appel simulé
    if svc == "report/exec_report":
        return {"reportResult": {"tables": [{"header": ["h"], "rows": 2500}]}}
    depuis = int(params.get("indexFrom") or 0)
    return [{} for _ in range(min(1000, 2500 - depuis))]


def _api_de_test():
    api = W.ApiWialon.__new__(W.ApiWialon)
    api._ids_rapport = (1, 2)
    api._gabarit_trajets = lambda: (1, 2)
    api.unites = lambda: [
        {"nm": "4006 TBS (LSS)", "id": 11, "pos": {"s": 5, "t": int(time.time())}},
        {"nm": "4296 TCC (LSS)", "id": 22, "pos": {"s": 5, "t": int(time.time())}}]
    return api


# ---- 1) marche nominale : CHAQUE page, CHAQUE unité, CHAQUE parsing mesurés
mesures.clear()
passe = passe_de("N2_CAMTRACKPRO", 180.0)
api = _api_de_test()
api._appel = _faux_appel
with C.activer_passe(passe):
    trajets = api.trajets_du_jour(JOUR[0])
m = passe.metriques
check("3 pages par unité × 2 unités = 6 pages", m.nb_pages == 6, f"→ {m.nb_pages}")
check("2500 lignes lues par unité × 2 = 5000", m.nb_lignes_lues == 5000,
      f"→ {m.nb_lignes_lues}")
check("la PAGE est mesurée une fois par page (6 mesures)",
      mesures.count("pagination") == 6, f"→ {mesures}")
check("la DERNIÈRE unité est bien mesurée (2 mesures, la dernière en fin de "
      "boucle)",
      mesures.count("vehicule") == 2 and mesures[-1] == "vehicule",
      f"→ {mesures}")
check("le parsing est mesuré une fois par unité (phase plus jamais morte)",
      mesures.count("parsing") == 2 and m.parsing_s > 0, f"→ {m.parsing_s}")
check("durée d'unité mesurée et jamais None",
      isinstance(m.vehicule_s, float) and m.vehicule_s > 0
      and isinstance(m.pagination_s, float),
      f"→ {m.pagination_s} / {m.vehicule_s}")
check("mesures emboîtées : pagination ⊆ véhicule (aucune double mesure)",
      m.pagination_s <= m.vehicule_s + 0.001,
      f"→ {m.pagination_s} / {m.vehicule_s}")
check("aucun trajet fabriqué par le faux parseur", trajets == [], f"→ {trajets}")

# ---- 2) exception LOCALE pendant une page : page ET unité restent mesurées
mesures.clear()
passe = passe_de("N2_CAMTRACKPRO", 180.0)
api = _api_de_test()
api._appel = _faux_appel
_compteur = {"n": 0}


def _compter_qui_casse(champ, *args, **kwargs):
    _compteur["n"] += 1
    if _compteur["n"] == 2:
        raise RuntimeError("échec local pendant la page (test)")
    return _orig_compter_w(champ, *args, **kwargs)


W.compter = _compter_qui_casse
try:
    with C.activer_passe(passe):
        try:
            api.trajets_du_jour(JOUR[0])
            leve = None
        except Exception as exc:                      # noqa: BLE001
            leve = type(exc).__name__
finally:
    W.compter = _orig_compter_w
check("l'échec local remonte (jamais avalé)", leve == "RuntimeError",
      f"→ {leve}")
check("page ET unité mesurées MALGRÉ l'exception (les 2 dernières mesures)",
      mesures[-2:] == ["pagination", "vehicule"], f"→ {mesures}")

# ---- 3) timeout sur la PREMIÈRE unité : mesurée, et la suivante est traitée
mesures.clear()
passe = passe_de("N2_CAMTRACKPRO", 180.0)
api = _api_de_test()
_exec = {"n": 0}


def _appel_avec_timeout(svc, params, reessai=True):
    if svc == "report/exec_report":
        _exec["n"] += 1
        if _exec["n"] == 1:
            time.sleep(0.02)          # attente RÉELLE avant le timeout
            raise TimeoutError("véhicule 1 : timeout (test)")
        return {"reportResult": {"tables": [{"header": ["h"], "rows": 1500}]}}
    time.sleep(0.003)
    depuis = int(params.get("indexFrom") or 0)
    return [{} for _ in range(min(1000, 1500 - depuis))]


api._appel = _appel_avec_timeout
with C.activer_passe(passe):
    trajets = api.trajets_du_jour(JOUR[0])
check("l'unité en TIMEOUT est mesurée (2 unités) et son temps n'est pas perdu",
      mesures.count("vehicule") == 2, f"→ {mesures}")
check("l'unité suivante est allée jusqu'au bout (2 pages + parsing)",
      mesures.count("pagination") == 2 and mesures.count("parsing") == 1
      and mesures[-1] == "vehicule", f"→ {mesures}")
W.mesurer_depuis = _orig_mesurer_w
W.detecter_colonnes_wialon = _orig_col
W.item_depuis_ligne_rapport = _orig_item

# ═══════════════════════════════════ [T-M1-12] écran mesuré malgré exception
titre("[T-M1-12] écran (Playwright) : mesuré MÊME quand la lecture échoue")
vider_file()
_orig_ecran = S.MZoneXTrajetsCollector
_orig_api_active = S._mzonex_api_active


class _EcranQuiEchoue:
    recensement = []

    def collecter_valides(self, jours=None):
        time.sleep(0.02)   # attente RÉELLE avant l'échec
        raise RuntimeError("écran indisponible (test)")


S.MZoneXTrajetsCollector = lambda *a, **k: _EcranQuiEchoue()
S._mzonex_api_active = lambda: False
passe = passe_de("N2_MZONEX", 120.0)
try:
    with C.activer_passe(passe):
        try:
            S._collecter_n2_mzonex(JOUR, attendues=None)
            leve = None
        except Exception as exc:                      # noqa: BLE001
            leve = type(exc).__name__
finally:
    S.MZoneXTrajetsCollector = _orig_ecran
    S._mzonex_api_active = _orig_api_active
check("l'échec de l'écran remonte (jamais avalé)",
      leve == "RuntimeError", f"→ {leve}")
check("ecran_s est mesuré malgré l'exception",
      passe.metriques.ecran_s > 0, f"→ {passe.metriques.ecran_s}")

# ══════════════════════════ [T-M1-13] ROLLBACK : aucun compteur, aucun rejet
titre("[T-M1-13] ROLLBACK : aucun compteur publié, aucun motif de rejet compté")
from app import reconciliation as REC                          # noqa: E402
vider_file()
S.forcer_deverrouillage_n2("test M1")
S.DERNIER_ETAT_N2.clear()      # aucune écriture d'audit de repli pendant ce test
_orig_collecte = S._collecter_n2_mzonex
_orig_reconcilier = REC.reconcilier_trajets_valides


def _collecte_vide(jours=None, *, attendues=None, bilan=None):
    return [], []


def _reconciliation_qui_rollback(db, items, username=None):
    db.rollback()
    raise RuntimeError("database is locked (simulé)")


S._collecter_n2_mzonex = _collecte_vide
REC.reconcilier_trajets_valides = _reconciliation_qui_rollback
try:
    S.synchroniser_trajets_valides("MZONEX")
finally:
    S._collecter_n2_mzonex = _orig_collecte
    REC.reconcilier_trajets_valides = _orig_reconcilier
vues = traces_file()
resultats = traces_action(vues, "collecte.source_resultat")
compteurs = {"recus", "ecrits", "ignores", "confirme_apres_commit",
             "rejets", "motifs_rejet"}
check("le résultat de la source est publié en ÉCHEC",
      len(resultats) == 1 and resultats[0].get("issue") == "ECHEC",
      f"→ {resultats}")
check("AUCUN compteur publié après un rollback",
      resultats and not (set(resultats[0]) & compteurs),
      f"→ {sorted(set(resultats[0])) if resultats else []}")
check("aucune trace de M1 ne contient de motif de rejet",
      not any({"rejets", "motifs_rejet", "motif"} & set(d)
              for _, d in vues), f"→ {[set(d) for _, d in vues]}")
check("`Metriques` n'a AUCUN champ de rejet (M1 ne compte pas les motifs)",
      not hasattr(C.Metriques(), "nb_rejets")
      and not hasattr(C.Metriques(), "motifs_rejet"))

# ══════════════════════════════════ [T-M1-14] additivité et non-régression
titre("[T-M1-14] additivité : clés existantes INTACTES, clés M1 en plus")
etat = S.etat_collecte_memoire()
attendues = {"verrou_occupe", "verrou_acquis_par", "verrou_duree_s",
             "verrou_n1", "verrou_n2", "verrous_par_source",
             "collecte_en_cours", "metriques_collecte",
             "metriques_par_source", "cycles_par_source",
             "progression_relecture", "collecte_traces"}
check("`etat_collecte_memoire()` (source de `etat_collecteur` de /api/sante) "
      "porte toutes les clés d'avant + M1",
      attendues <= set(etat), f"→ manquantes {sorted(attendues - set(etat))}")
m = C.Metriques()
phases = set(m.phases_s())
check("les phases d'avant sont inchangées, `ecran` s'ajoute",
      {"authentification", "attente_http", "pagination", "vehicule",
       "parsing", "ecriture", "attente_verrou", "attente_sqlite",
       "ecran"} <= phases, f"→ {sorted(phases)}")
champs = set(m.depouiller())
check("les champs d'avant sont inchangés, les champs M1 s'ajoutent",
      {"ecran_s", "nb_ecran_vehicules", "demarrage_le", "source_demarree",
       "non_demarree_raison"} <= champs
      and {"debut_le", "fin_le", "phase", "issue", "nb_pages",
           "nb_lignes_lues", "non_applicables"} <= champs,
      f"→ {sorted(champs)}")
check("la synthèse de période est cumulative et ne touche à aucun compteur "
      "métier",
      set(S._CHAMPS_CUMUL) == {"nb_pages", "nb_lignes_lues",
                               "nb_ecran_vehicules", "nb_commits",
                               "commits_apres_echeance"},
      f"→ {S._CHAMPS_CUMUL}")

# ────────────────────────────────────────────────────────────── bilan final
print("\n" + "=" * 74)
print(f"  RÉSULTAT : {R['ok']} OK / {R['ko']} KO")
print("=" * 74)
try:
    for suffixe in ("", "-wal", "-shm"):
        chemin = "/tmp/test_instrumentation_m1.db" + suffixe
        if os.path.exists(chemin):
            os.remove(chemin)
            print(f"  (base de test supprimée : {chemin})")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
