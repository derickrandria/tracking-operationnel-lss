"""Tests v1.13 — CHAÎNES + UNICITÉ PAR DÉBUT.
Réaligné v3 (AMÉLIORATIONS — arbitrages LSS §0nonies du 22/08/2026) :
l'affichage par FUSION des pauses < 20 min est aboli (AM-2 : un trajet valide
= une ligne ; pause affichée seulement si ≥ 30 min) ; TCJ = tous les arrêts
déduits (AM-1) ; TTJ = amplitude brute (T1). Les scénarios des captures du
04/08 sont INCHANGÉS — seules les attentes des règles supplantées bougent.
Attendus recalés v1.29 ci-dessous, marqués « v3 ».

⚠️ PIÈGE HARNESS (v1.18.2) — RÉSOLU le 21/09/2026 (v1.54) : ces cas sont
ancrés à des HEURES FIXES (05:56 → 10:45) et la plateforme lit l'horloge
système. Le verdict dépendait donc de l'HEURE D'EXÉCUTION (10 checks
« en cours / orange / noir » échouaient artificiellement le matin) et de la
DATE du jour. L'horloge est désormais FIGÉE au 18/09/2026 à 17:30 (après-midi,
donc tous les états sont constatables) : le verdict est IDENTIQUE à toute heure
et à toute date, sans qu'aucune assertion métier n'ait été modifiée.
(Ancienne rustine : APP_TZ="Etc/GMT-12" … pour simuler ~17h locales.)

Rejoue EXACTEMENT les 4 cas des captures métier du 04/08 (~10h13-10h19) avec
les données réelles du portail relues le jour même. ATTENDUS v1.18 (arbitrages
métier du 06/08 — §5.1/§7 stricts, supersèdent la soudure v1.13) :

A) 4526TCC — chaîne EN COURS : jumeau vivant + doublon officiel au même début.
   Attendu : fusion des jumeaux, UNE ligne « 05:56:18 → en cours » orange
   (début = premier trajet RÉEL : la manœuvre de tête 05:42 est IGNORÉE, §7).
   Puis, camion arrêté et rapport final reçu : la même ligne devient noire
   « 05:56:18 → 10:13:23 » — JAMAIS deux lignes, JAMAIS de noir prématuré.

B) 5316TBU — chaîne terminée : jumeau PÉRIMÉ (camion arrêté depuis 09:48).
   Attendu : jumeau purgé, UNE ligne noire « 06:05:34 → 09:48:14 » ; puis le
   trajet suivant (vraie pause 43 min) produit une 2ᵉ ligne ORANGE tant que
   ses 20 min ne sont pas constatées.

C) 9856TCD — manœuvres IGNORÉES (v2 §7) : micro-mouvements de 2-8 min entre
   deux vrais trajets. Attendu : « 07:41:04 → 07:51:12 » noir, VRAIE pause
   1h17:04 (fin L1 → début du prochain trajet RÉEL 09:08:16, à travers les
   manœuvres), « 09:08:16 → 09:15:16 » noir — fin = premier arrêt réel (§5.1) :
   les manœuvres de fin ne prolongent plus la ligne ; la manœuvre isolée de
   06:04 n'apparaît nulle part, aucune ligne REJETÉE n'est posée par le cycle
   officiel, et les spans manœuvres restent exclus des compteurs (§2).

D) 0916TBV — manœuvre 0 km EN TÊTE puis 3 trajets valides séparés de pauses
   < 20 min + jumeau. Attendu : UNE ligne noire « 05:53:47 → 10:13:23 »
   (ancrée au premier trajet RÉEL — la manœuvre de tête ne décale rien, §7).

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v113.db" python3 test_chaines_v113.py
La base est SUPPRIMÉE à la fin (protection des données production).
Horloge FIGÉE depuis la v1.54 (21/09/2026) : verdict indépendant de l'heure.
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta


# ── DÉPENDANCES DE TEST (contrôles d'export P1j / P1k) ──────────────────────
# Avant : ces imports étaient faits AU MILIEU du scénario ; leur échec était
# alors absorbé par le `finally` qui terminait par `sys.exit(0)` : la suite
# affichait « 21 OK / 0 KO » et rendait un code de retour 0 alors que 30
# contrôles n'avaient JAMAIS tourné (faux vert constaté le 22/09, pypdf absent).
# Désormais l'import précède tout contrôle et son échec est un NON-EXÉCUTABLE
# explicite : aucun bilan, code de retour NON NUL.
try:
    from openpyxl import load_workbook          # export Excel (contrôle P1j)
    from pypdf import PdfReader                 # export PDF   (contrôle P1k)
except ImportError as _exc:                     # ModuleNotFoundError inclus
    print(f"\n=== NON EXÉCUTABLE : dépendance de test absente "
          f"« {getattr(_exc, 'name', _exc)} » ===")
    print("    Cette suite contrôle les exports Excel ET PDF : sans cette")
    print("    dépendance, des contrôles seraient SAUTÉS — un test incomplet")
    print("    n'est jamais un test vert.")
    print("    Installer : python -m pip install -r requirements-test.txt")
    sys.exit(4)                                 # jamais 0

# ── HORLOGE FIGÉE (v1.54, 21/09/2026) — DÉTERMINISME DU TEST ────────────────
# Le scénario est ancré à des heures fixes (05:56 → 10:45). Sans horloge figée,
# le verdict dépend de l'heure et de la date d'exécution. On fige l'instant
# APRÈS-MIDI du 18/09/2026, AVANT d'importer les modules applicatifs : tous ceux
# qui font `from .config import now_local` reçoivent alors CETTE horloge.
# AUCUNE attente métier n'est modifiée — seul le temps est injecté.
HEURE_FIGEE = datetime(2026, 9, 18, 17, 30, 0)
HEURE_FIGEE_S = HEURE_FIGEE.isoformat(sep=" ")


def _horloge_figee():
    return HEURE_FIGEE


import app.config as _config  # noqa: E402  — importé EN PREMIER, exprès
_config.now_local = _horloge_figee

import io  # noqa: E402

from sqlalchemy import delete, select  # noqa: E402

from app.config import now_local  # noqa: E402  (= _horloge_figee)
from app.database import SessionLocal
from app import engine
from app.models import (AuditLog, StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, Vehicule)
from app.reconciliation import reconcilier_trajets_valides
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.serializers import fusionner_snapshot, s_suivi, snapshot_canonique
from app.exporters import export_suivi_excel, export_suivi_pdf
from app.routers.operations import _suivis_filtres

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

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
db = SessionLocal()
jour = HEURE_FIGEE.date()      # v1.54 — ancré à l'horloge figée
base = datetime.combine(jour, datetime.min.time())

mzx = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF").order_by(
        Vehicule.plaque)).all()
print(f"Véhicules test : {', '.join(v.plaque for v in mzx[:4])}")
v4526, v5316, v9856, v0916 = mzx[0], mzx[1], mzx[2], mzx[3]

for v in (v4526, v5316, v9856, v0916):
    s = engine.ensure_suivi(db, v, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
db.commit()


def h(hh, mm, ss=0):
    return base.replace(hour=hh, minute=mm, second=ss)


def ajoute(veh, debut, fin, dist, source=StatutSourceTrajet.VALIDE,
           valid=StatutValidationTrajet.VALIDE, plateforme="MZONEX"):
    s = engine.ensure_suivi(db, veh, jour)
    n = (len(db.scalars(select(Trajet).where(Trajet.suivi_id == s.id)).all()))
    t = Trajet(suivi_id=s.id, numero=n + 1, heure_debut=debut, heure_fin=fin,
               statut_source=source, source_plateforme=plateforme,
               distance_km=dist, statut_validation=valid)
    db.add(t)
    db.commit()
    return t


def grille(veh):
    s = engine.ensure_suivi(db, veh, jour)
    db.expire_all()
    return s_suivi(engine.ensure_suivi(db, veh, jour)) if False else s_suivi(s)


def ligne(g, i=0):
    ts = g["trajets"] or []
    return ts[i] if i < len(ts) else None


# ── SECTIONS RÉELLEMENT EXÉCUTÉES : une section sautée doit être VISIBLE ────
SECTIONS_ATTENDUES = ("A", "B", "C", "D", "E")
sections_vues: list = []

try:
    # ================================================================
    sections_vues.append("A")
    print("\n[A] 4526TCC — chaîne EN COURS (jumeau vivant + doublon officiel)")
    # données écrites par les anciennes versions ce matin-là
    s = engine.ensure_suivi(db, v4526, jour)
    ajoute(v4526, h(5, 42, 15), h(5, 52, 43), 0.0,
           source=StatutSourceTrajet.PROVISOIRE,
           valid=StatutValidationTrajet.REJETE)
    jumeau = ajoute(v4526, h(5, 56, 18), None, None,
                    source=StatutSourceTrajet.PROVISOIRE,
                    valid=StatutValidationTrajet.EN_ATTENTE)
    ajoute(v4526, h(5, 56, 18), h(8, 21, 15), 46.689)     # doublon officiel
    # le camion ROULE maintenant (événement tout récent, vitesse > 0)
    v4526 = db.get(Vehicule, v4526.id)
    v4526.last_event_at = now_local() - timedelta(seconds=60)
    v4526.last_vitesse = 38.0
    db.commit()

    stats = reconcilier_trajets_valides(db, [
        {"vehicule_id": v4526.id, "gps_associe": v4526.gps_associe,
         "plaque": v4526.plaque, "debut": h(5, 56, 18), "fin": h(8, 21, 15),
         "distance_km": 46.689, "source": "MZONEX"}],
        maintenant=h(10, 45))

    check("aucune création (jumeau officiel déjà présent)", stats["crees"] == 0,
          f"stats={stats}")
    check("1 jumeau fusionné (balai)", stats["epures"] == 1, f"stats={stats}")
    g = grille(v4526)
    check("UNE seule ligne affichée", g["nb_trajets"] == 1,
          f"trajets={[(t['heure_debut'], t['heure_fin']) for t in g['trajets']]}")
    l = ligne(g, 0)
    check("début = premier trajet RÉEL 05:56:18 (manœuvre de tête ignorée §7)",
          l and l["heure_debut"] == h(5, 56, 18).isoformat(), f"{l and l['heure_debut']}")
    check("fin VIDE (en cours)", l and l["heure_fin"] is None)
    check("ligne PROVISOIRE (orange), jamais noire en roulant",
          l and l["statut_source"] == "PROVISOIRE")
    check("pas de pause après la ligne en cours", l and l["pause_apres_s"] == 0)

    # --- le camion s'arrête : le rapport final arrive (deux lignes portail,
    #     pauses 4 min < 20 → fusionnées par le pré-filtrage §5)
    v4526 = db.get(Vehicule, v4526.id)
    v4526.last_event_at = h(10, 13, 23)
    v4526.last_vitesse = 0.0
    db.commit()
    stats = reconcilier_trajets_valides(db, [
        {"vehicule_id": v4526.id, "gps_associe": v4526.gps_associe,
         "plaque": v4526.plaque, "debut": h(5, 56, 18), "fin": h(8, 21, 15),
         "distance_km": 46.689, "source": "MZONEX"},
        {"vehicule_id": v4526.id, "gps_associe": v4526.gps_associe,
         "plaque": v4526.plaque, "debut": h(8, 25, 31), "fin": h(10, 13, 23),
         "distance_km": 52.0, "source": "MZONEX"}],
        maintenant=h(10, 45))
    g = grille(v4526)
    l = ligne(g, 0)
    # Réalignement v1.31 (§0undecies E1, 24/08/2026 — prime sur AM-2 pour les
    # arrêts < 20 min) : la rupture 08:21:15→08:25:31 ne dure que 4 min 16 s →
    # la GRILLE affiche UNE seule ligne ; la BASE garde les DEUX trajets
    # (vérifié par les contrôles de réconciliation ci-dessus) et les compteurs
    # déduisent toujours l'arrêt (AM-1, vérifié plus bas).
    check("après clôture : UNE ligne affichée (E1 v1.31 : rupture 4:16 < 20 "
          "min fusionnée ; les 2 trajets réels restent en base)",
          g["nb_trajets"] == 1,
          f"trajets={[(t['heure_debut'], t['heure_fin']) for t in g['trajets']]}")
    l1a = ligne(g, 0)
    check("ligne fusionnée = 05:56:18 → 10:13:23 officielle "
          "(46,689 + 52 = 98,689 km, segments 2)",
          l1a and l1a["heure_debut"] == h(5, 56, 18).isoformat()
          and l1a["heure_fin"] == h(10, 13, 23).isoformat()
          and l1a["statut_source"] == "VALIDÉ"
          and abs((l1a["distance_km"] or 0) - 98.689) < 0.01
          and l1a["segments"] == 2,
          f"{l1a and (l1a['heure_debut'], l1a['heure_fin'], l1a['distance_km'])}")
    check("aucune pause affichée après la ligne fusionnée (fin de journée)",
          l1a and l1a["pause_apres_s"] == 0)
    # §2/§7 : la manœuvre de tête (05:42→05:52) est HORS amplitude réelle et
    # ne doit JAMAIS apparaître dans les compteurs
    s_a = engine.ensure_suivi(db, v4526, jour)
    engine.recalculer_temps(db, s_a, h(10, 45))
    db.commit()
    amplitude_a = int((h(10, 13, 23) - h(5, 56, 18)).total_seconds())
    check("TTJ = amplitude réelle 05:56:18→10:13:23 (manœuvre de tête exclue)",
          s_a.ttj_s == amplitude_a, f"{s_a.ttj_s} vs {amplitude_a}")

    # ================================================================
    sections_vues.append("B")
    print("\n[B] 5316TBU — chaîne terminée (jumeau périmé purgé)")
    jumeau_b = ajoute(v5316, h(6, 5, 34), None, None,
                      source=StatutSourceTrajet.PROVISOIRE,
                      valid=StatutValidationTrajet.EN_ATTENTE)
    for d, f, km in [(h(6, 5, 34), h(7, 28, 14), 37.64),
                     (h(7, 35, 59), h(7, 43, 26), 4.015),
                     (h(7, 45, 55), h(9, 27, 28), 48.68),
                     (h(9, 32, 43), h(9, 48, 14), 4.386)]:
        ajoute(v5316, d, f, km)
    v5316 = db.get(Vehicule, v5316.id)
    v5316.last_event_at = h(9, 48, 14)
    v5316.last_vitesse = 0.0
    db.commit()
    items_b = [{"vehicule_id": v5316.id, "gps_associe": v5316.gps_associe,
                "plaque": v5316.plaque, "debut": d, "fin": f, "distance_km": km,
                "source": "MZONEX"}
               for d, f, km in [(h(6, 5, 34), h(7, 28, 14), 37.64),
                                (h(7, 35, 59), h(7, 43, 26), 4.015),
                                (h(7, 45, 55), h(9, 27, 28), 48.68),
                                (h(9, 32, 43), h(9, 48, 14), 4.386)]]
    stats = reconcilier_trajets_valides(db, list(items_b), maintenant=h(10, 45))
    check("jumeau périmé purgé (camion arrêté)", stats["epures"] >= 1,
          f"stats={stats}")
    restants_b = [t for t in db.scalars(select(Trajet).join(
        SuiviJournalier).where(
            SuiviJournalier.vehicule_id == v5316.id,
            SuiviJournalier.date_jour == jour)).all()]
    # la BASE conserve les 4 fragments publiés (§0undecies E1 : affichage
    # seulement, rien n'est perdu en stockage)
    # v1.54 (P1) : la réconciliation ne SUPPRIME plus rien. Le jumeau périmé est
    # CONSERVÉ en base, écarté de l'affichage, marqué REJETE avec son motif et audité.
    # L'ancienne assertion (len(restants_b) == 4) encodait la purge PHYSIQUE de l'ère
    # v1.31 : elle comptait les survivants, pas la règle.
    ecartes_b = [t for t in restants_b
                 if t.statut_validation == StatutValidationTrajet.REJETE]
    officiels_b = [t for t in restants_b
                   if t.statut_validation != StatutValidationTrajet.REJETE]
    check("P1a : jumeau périmé CONSERVÉ en base (REJETE, motif DOUBLON_JUMEAU)",
          len(ecartes_b) == 1 and ecartes_b[0].motif_rejet == "DOUBLON_JUMEAU"
          and ecartes_b[0].heure_debut == h(6, 5, 34),
          f"{[(t.heure_debut, t.statut_validation, t.motif_rejet) for t in ecartes_b]}")
    check("P1b : les 4 fragments officiels sont conservés",
          len(officiels_b) == 4 and all(
              t.statut_source == StatutSourceTrajet.VALIDE for t in officiels_b),
          f"{[(t.heure_debut, t.heure_fin, t.statut_source) for t in officiels_b]}")
    check("P1c : un seul enregistrement NON écarté commence à 06:05:34 (pas de doublon)",
          sum(1 for t in officiels_b if t.heure_debut == h(6, 5, 34)) == 1,
          f"{[(t.heure_debut, t.statut_validation) for t in officiels_b]}")
    check("P1d : écartement AUDITÉ (AuditLog — trajet.jumeau_fusionne)",
          db.query(AuditLog).filter(
              AuditLog.action == "trajet.jumeau_fusionne").count() >= 1)
    s_b_cpt = engine.ensure_suivi(db, v5316, jour)
    engine.recalculer_temps(db, s_b_cpt, h(10, 45))
    db.commit()
    tcj_fragments = sum(int((t.heure_fin - t.heure_debut).total_seconds())
                        for t in officiels_b)
    check("P1e : TCJ = 4 fragments officiels seulement (jumeau exclu des compteurs)",
          s_b_cpt.tcj_s == tcj_fragments, f"{s_b_cpt.tcj_s} vs {tcj_fragments}")
    check("P1f : TTJ = amplitude réelle 06:05:34 → 09:48:14",
          s_b_cpt.ttj_s == int((h(9, 48, 14) - h(6, 5, 34)).total_seconds()),
          f"{s_b_cpt.ttj_s}")

    # ---- Projections d'affichage (exigence d'arbitrage du 21/09/2026) -------
    # Le jumeau REJETE est CONSERVÉ en base (P1a) et AUDITÉ (P1d), mais il doit
    # être ABSENT de toutes les projections actives : onglet Suivi, onglet
    # Historique (même fabrique de snapshot + fusion), exports Excel et PDF.
    # Les 4 trajets officiels, eux, restent présents et visibles (P1b/P1c/P1l).
    _s_b = engine.ensure_suivi(db, v5316, jour)
    _proj = s_suivi(_s_b)
    _lignes_proj = _proj.get("trajets") or []
    _ouvre = (h(6, 5, 34).isoformat(), None)          # signature du jumeau (sans fin)
    check("P1g : jumeau ABSENT de la projection Suivi active "
          "(1 seule séquence, aucune ligne ouverte)",
          _proj.get("nb_trajets") == 1 and len(_lignes_proj) == 1
          and (_lignes_proj[0].get("heure_debut"), _lignes_proj[0].get("heure_fin"))
          == (h(6, 5, 34).isoformat(), h(9, 48, 14).isoformat())
          and all((t.get("heure_debut"), t.get("heure_fin")) != _ouvre
                  for t in _lignes_proj)
          and all(t.get("heure_fin") is not None for t in _lignes_proj),
          f"{[(t.get('heure_debut'), t.get('heure_fin')) for t in _lignes_proj]}")

    _snap_b = snapshot_canonique(_s_b)                # fabrique d'archive v1.53
    _hist_b = fusionner_snapshot(dict(_snap_b))       # projection de l'Historique
    _lignes_hist = _hist_b.get("trajets") or []
    check("P1i : jumeau ABSENT de l'Historique actif "
          "(snapshot canonique + fusion : 1 seule séquence)",
          _hist_b.get("nb_trajets") == 1 and len(_lignes_hist) == 1
          and (_lignes_hist[0].get("heure_debut"), _lignes_hist[0].get("heure_fin"))
          == (h(6, 5, 34).isoformat(), h(9, 48, 14).isoformat())
          and all(t.get("heure_fin") is not None for t in _lignes_hist)
          and len(_snap_b.get("trajets") or []) == 4,  # les 4 officiels archivés
          f"{[(t.get('heure_debut'), t.get('heure_fin')) for t in _lignes_hist]}")

    # Source EXACTE des exports de l'onglet Suivi (routers/operations.py)
    _lignes_exp = _suivis_filtres(db, jour, None, None)
    _xls = export_suivi_excel(jour.strftime("%d/%m/%Y"), _lignes_exp, True)
    _wb = load_workbook(io.BytesIO(_xls))
    _rows_v = [r for _ws in _wb.worksheets for r in _ws.iter_rows(values_only=True)
               if r and r[0] == v5316.plaque]
    _cells_v = [str(c) for r in _rows_v for c in r if c is not None]
    check("P1j : jumeau ABSENT de l'export Excel "
          "(1 ligne, 06:05 → 09:48, aucun motif de rejet)",
          len(_rows_v) == 1 and _cells_v.count("06:05") == 1
          and _cells_v.count("09:48") == 1
          and not any(("REJET" in c.upper() or "DOUBLON" in c.upper()
                       or "EN COURS" in c.upper()) for c in _cells_v),
          f"{len(_rows_v)} ligne(s) — {_cells_v[:6]}")

    _pdf = export_suivi_pdf(jour.strftime("%d/%m/%Y"), _lignes_exp, True)
    _txt_pdf = "\n".join((pg.extract_text() or "")
                         for pg in PdfReader(io.BytesIO(_pdf)).pages)
    check("P1k : jumeau ABSENT de l'export PDF "
          "(06:05 → 09:48 une seule fois, aucun motif de rejet)",
          v5316.plaque in _txt_pdf and _txt_pdf.count("06:05") == 1
          and _txt_pdf.count("09:48") == 1
          and "REJET" not in _txt_pdf.upper() and "DOUBLON" not in _txt_pdf.upper(),
          f"{_txt_pdf.count('06:05')} x 06:05 / {_txt_pdf.count('09:48')} x 09:48")

    check("P1l : les 4 trajets officiels restent VISIBLES "
          "(règle de séquence : 4 segments en 1 seule ligne)",
          _proj.get("nb_trajets_valides_reels") == 4
          and _proj.get("nb_trajets") == 1
          and _lignes_proj[0].get("segments") == 4,
          f"{_proj.get('nb_trajets_valides_reels')} réels / "
          f"{_lignes_proj[0].get('segments')} segments")
    g = grille(v5316)
    l = ligne(g, 0)
    # Réalignement v1.31 : les ruptures inter-fragments (7:45, 2:29, 5:15)
    # sont toutes < 20 min → UNE ligne affichée 06:05:34 → 09:48:14
    check("UNE ligne noire affichée (E1 v1.31 : 4 fragments fusionnés) "
          "06:05:34 → 09:48:14, 94,721 km, segments 4",
          g["nb_trajets"] == 1 and l["heure_debut"] == h(6, 5, 34).isoformat()
          and l["heure_fin"] == h(9, 48, 14).isoformat()
          and abs((l["distance_km"] or 0) - 94.721) < 0.01
          and l["segments"] == 4,
          f"trajets={[(t['heure_debut'], t['heure_fin'], t['distance_km']) for t in g['trajets']]}")
    check("ligne OFFICIELLE (VALIDÉ)", l and l["statut_source"] == "VALIDÉ")
    check("plus de ligne « 06:05 en cours » orange avant",
          not any(t["heure_fin"] is None for t in g["trajets"]))

    # --- nouveau trajet après vraie pause → 2ᵉ ligne, ORANGE tant que la fin
    #     est constatée depuis < 20 min (horloge réelle du rendu)
    maintenant_b2 = now_local()
    debut2 = maintenant_b2 - timedelta(minutes=12)
    fin2 = maintenant_b2 - timedelta(minutes=5)
    v5316 = db.get(Vehicule, v5316.id)
    v5316.last_event_at = fin2
    db.commit()
    stats = reconcilier_trajets_valides(db, [
        {"vehicule_id": v5316.id, "gps_associe": v5316.gps_associe,
         "plaque": v5316.plaque, "debut": debut2, "fin": fin2,
         "distance_km": 1.786, "source": "MZONEX"}],
        maintenant=maintenant_b2)
    g = grille(v5316)
    l1 = ligne(g, 0)
    # Réalignement v1.31 : 4 fragments fusionnés en 1 ligne + le nouveau
    # trajet (vraie pause de plusieurs heures avant) = 2 lignes affichées
    l5 = ligne(g, 1)                       # le nouveau trajet (vraie pause avant)
    check("DEUX lignes désormais (E1 v1.31 : fragments fusionnés + la nouvelle)",
          g["nb_trajets"] == 2 and l5 is not None,
          f"trajets={[(t['heure_debut'], t['heure_fin']) for t in g['trajets']]}")
    pause_attendue = int((debut2 - h(9, 48, 14)).total_seconds())
    check("les pauses inter-fragments ont disparu de l'affichage (arrière-plan "
          "E1) ; la vraie pause ≥ 30 min reste AFFICHÉE après la ligne "
          "fusionnée", l1 and l1["pause_apres_s"] == pause_attendue
          and pause_attendue >= 1800,
          f"l1={l1 and l1['pause_apres_s']} vs {pause_attendue}")
    check("2ᵉ ligne ORANGE (fin constatée < 20 min)",
          l5 and l5["statut_source"] == "PROVISOIRE" and l5["heure_fin"] is not None,
          f"{l5 and (l5['statut_source'], l5['heure_fin'])}")
    check("1ʳᵉ ligne restée NOIRE", l1 and l1["statut_source"] == "VALIDÉ")
    g2 = grille(v5316)
    check("rendering idempotent (mêmes lignes au 2ᵉ appel)",
          [(t["heure_debut"], t["heure_fin"], t["statut_source"])
           for t in g2["trajets"]]
          == [(t["heure_debut"], t["heure_fin"], t["statut_source"])
              for t in g["trajets"]])

    # --- une ligne dont la fin est constatée ≥ 20 min est NOIRE (déjà prouvé
    #     par L1 à 09:48, ici re-vérifié sur la fin vieillie de l2)
    l2_vieillie_ts = maintenant_b2 - timedelta(minutes=25)
    s_b = engine.ensure_suivi(db, v5316, jour)
    dernier_b = db.scalars(select(Trajet).where(
        Trajet.suivi_id == s_b.id).order_by(Trajet.numero.desc())).first()
    dernier_b.heure_fin = l2_vieillie_ts
    dernier_b.heure_debut = l2_vieillie_ts - timedelta(minutes=7)
    db.commit()
    g = grille(v5316)
    l2 = ligne(g, 1)                       # la nouvelle (2ᵉ) ligne — E1 v1.31
    check("2ᵉ ligne NOIRE quand la fin a ≥ 20 min",
          l2 and l2["statut_source"] == "VALIDÉ",
          f"{l2 and l2['statut_source']}")

    # ================================================================
    sections_vues.append("C")
    print("\n[C] 9856TCD — manœuvres IGNORÉES (Référence v2 §5.1/§7)")
    ajoute(v9856, h(6, 4, 53), h(6, 13, 54), 0.0,
           source=StatutSourceTrajet.PROVISOIRE, valid=StatutValidationTrajet.REJETE)
    ajoute(v9856, h(7, 41, 4), h(7, 51, 12), 1.119)
    ajoute(v9856, h(8, 56, 16), h(8, 58, 36), 0.0,
           source=StatutSourceTrajet.PROVISOIRE, valid=StatutValidationTrajet.REJETE)
    ajoute(v9856, h(9, 8, 16), h(9, 15, 16), 1.467)
    ajoute(v9856, h(9, 20, 45), h(9, 22, 47), 0.188,
           source=StatutSourceTrajet.PROVISOIRE, valid=StatutValidationTrajet.REJETE)
    ajoute(v9856, h(9, 31, 31), h(9, 37, 40), 0.0,
           source=StatutSourceTrajet.PROVISOIRE, valid=StatutValidationTrajet.REJETE)
    ajoute(v9856, h(9, 46, 11), h(9, 51, 54), 0.044,
           source=StatutSourceTrajet.PROVISOIRE, valid=StatutValidationTrajet.REJETE)
    jumeau_c = ajoute(v9856, h(8, 56, 16), None, None,
                      source=StatutSourceTrajet.PROVISOIRE,
                      valid=StatutValidationTrajet.EN_ATTENTE)
    v9856 = db.get(Vehicule, v9856.id)
    v9856.last_event_at = h(9, 51, 54)
    v9856.last_vitesse = 0.0
    db.commit()
    nb_rejetes_avant = len(db.scalars(select(Trajet).join(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v9856.id,
        SuiviJournalier.date_jour == jour,
        Trajet.statut_validation == StatutValidationTrajet.REJETE)).all())
    stats = reconcilier_trajets_valides(db, [
        {"vehicule_id": v9856.id, "gps_associe": v9856.gps_associe,
         "plaque": v9856.plaque, "debut": h(9, 8, 16), "fin": h(9, 15, 16),
         "distance_km": 1.467, "source": "MZONEX"}],
        maintenant=h(10, 45))
    g = grille(v9856)
    check("2 lignes seulement (manœuvres cachées, §7)", g["nb_trajets"] == 2,
          f"trajets={[(t['heure_debut'], t['heure_fin']) for t in g['trajets']]}")
    l1, l2 = ligne(g, 0), ligne(g, 1)
    check("L1 = 07:41:04 → 07:51:12 noir",
          l1 and l1["heure_debut"] == h(7, 41, 4).isoformat()
          and l1["heure_fin"] == h(7, 51, 12).isoformat()
          and l1["statut_source"] == "VALIDÉ")
    pause_attendue = int((h(9, 8, 16) - h(7, 51, 12)).total_seconds())  # 1h17:04
    check("pause RÉELLE 1h17 après L1 (à travers les manœuvres ignorées §7)",
          l1 and l1["pause_apres_s"] == pause_attendue,
          f"{l1 and l1['pause_apres_s']} vs {pause_attendue}")
    check("L2 = 09:08:16 → 09:15:16 (fin = PREMIER ARRÊT réel §5.1, "
          "manœuvres de fin ignorées §7)",
          l2 and l2["heure_debut"] == h(9, 8, 16).isoformat()
          and l2["heure_fin"] == h(9, 15, 16).isoformat(),
          f"{l2 and (l2['heure_debut'], l2['heure_fin'])}")
    check("L2 noire (pause ≥ 20 min constatée derrière)",
          l2 and l2["statut_source"] == "VALIDÉ")
    check("pas de 3ᵉ ligne (jumeau 08:56 « en cours » écarté)",
          ligne(g, 2) is None)
    nb_rejetes_apres = len(db.scalars(select(Trajet).join(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v9856.id,
        SuiviJournalier.date_jour == jour,
        Trajet.statut_validation == StatutValidationTrajet.REJETE)).all())
    check("aucune ligne REJETÉE posée par le cycle officiel (§7 : IGNORER "
          "= ne pas créer de trajet)",
          nb_rejetes_apres == nb_rejetes_avant,
          f"{nb_rejetes_avant} → {nb_rejetes_apres}")
    s_c = engine.ensure_suivi(db, v9856, jour)
    engine.recalculer_temps(db, s_c, h(10, 45))
    db.commit()
    # v3 (AM-1/T1) : TTJ = AMPLITUDE BRUTE 07:41:04→09:15:16 (rien d'exclu) ;
    # arrêts = TOUT l'inter-lignes, spans de manœuvres compris (AM-6 : une
    # manœuvre est un arrêt) ; TCJ = conduite pure = 608 + 420 = 1028 s
    ttj_attendu = int((h(9, 15, 16) - h(7, 41, 4)).total_seconds())   # 5652 s
    pauses_nettes = pause_attendue                                    # 1h17:04
    tcj_attendu = ttj_attendu - pauses_nettes          # = 608 + 420 = 1028 s
    check("TTJ = amplitude BRUTE (T1, v3 : plus aucune exclusion)",
          s_c.ttj_s == ttj_attendu, f"{s_c.ttj_s} vs {ttj_attendu}")
    check("TCJ = TTJ − TOUS les arrêts (AM-1) = conduite pure 1028 s",
          s_c.tcj_s == tcj_attendu, f"{s_c.tcj_s} vs {tcj_attendu}")
    check("TCC = 0 (camion en pause valide)", s_c.tcc_s == 0,
          f"{s_c.tcc_s}")
    check("total arrêts = 1h17:04 (écart réel, span de la manœuvre interne "
          "compté comme arrêt — AM-6)",
          s_c.total_pause_s == pauses_nettes,
          f"{s_c.total_pause_s} vs {pauses_nettes}")

    # ================================================================
    sections_vues.append("D")
    print("\n[D] 0916TBV — manœuvre en tête + 3 trajets, pauses < 20 min")
    ajoute(v0916, h(5, 39, 36), h(5, 49, 11), 0.0,
           source=StatutSourceTrajet.PROVISOIRE, valid=StatutValidationTrajet.REJETE)
    ajoute(v0916, h(5, 53, 47), h(6, 34, 54), 13.539)
    ajoute(v0916, h(6, 42, 19), h(7, 52, 1), 27.689)
    jumeau_d = ajoute(v0916, h(5, 53, 47), None, None,
                      source=StatutSourceTrajet.PROVISOIRE,
                      valid=StatutValidationTrajet.EN_ATTENTE)
    v0916 = db.get(Vehicule, v0916.id)
    v0916.last_event_at = h(10, 13, 23)
    v0916.last_vitesse = 0.0
    db.commit()
    stats = reconcilier_trajets_valides(db, [
        {"vehicule_id": v0916.id, "gps_associe": v0916.gps_associe,
         "plaque": v0916.plaque, "debut": d, "fin": f, "distance_km": km,
         "source": "MZONEX"}
        for d, f, km in [(h(5, 53, 47), h(6, 34, 54), 13.539),
                         (h(6, 42, 19), h(7, 52, 1), 27.689),
                         (h(8, 8, 13), h(10, 13, 23), 43.625)]],
        maintenant=h(10, 45))
    g = grille(v0916)
    l = ligne(g, 0)
    # Réalignement v1.31 (§0undecies E1) : les ruptures 06:34:54→06:42:19
    # (7:25) et 07:52:01→08:08:13 (16:12) sont < 20 min → UNE ligne affichée ;
    # départ toujours ancré au 1er trajet réel 05:53:47 (manœuvre de tête
    # ignorée, AM-6)
    check("UNE ligne noire affichée (E1 v1.31 : 3 fragments fusionnés, "
          "ruptures 7:25 et 16:12 < 20 min), départ ancré au 1er trajet réel "
          "05:53:47 (manœuvre de tête ignorée)",
          g["nb_trajets"] == 1 and l["heure_debut"] == h(5, 53, 47).isoformat()
          and l["heure_fin"] == h(10, 13, 23).isoformat()
          and l["statut_source"] == "VALIDÉ",
          f"trajets={[(t['heure_debut'], t['heure_fin']) for t in g['trajets']]}")
    check("vieux fragments fusionnés/purgés (≥ 1 épuration)",
          stats["epures"] >= 1, f"stats={stats}")
    check("distance SOMMÉE sur la ligne fusionnée : 13,539 + 27,689 + 43,625 "
          "= 84,853 km (segments 3)",
          l and abs(l["distance_km"] - 84.853) < 0.01
          and l["segments"] == 3)

    # --- cycle suivant strictement idempotent
    stats2 = reconcilier_trajets_valides(db, [
        {"vehicule_id": v0916.id, "gps_associe": v0916.gps_associe,
         "plaque": v0916.plaque, "debut": d, "fin": f, "distance_km": km,
         "source": "MZONEX"}
        for d, f, km in [(h(5, 53, 47), h(6, 34, 54), 13.539),
                         (h(6, 42, 19), h(7, 52, 1), 27.689),
                         (h(8, 8, 13), h(10, 13, 23), 43.625)]],
        maintenant=h(10, 46))
    g2 = grille(v0916)
    check("cycle suivant : grille strictement identique",
          [(t["heure_debut"], t["heure_fin"], t["statut_source"], t["pause_apres_s"])
           for t in g2["trajets"]]
          == [(t["heure_debut"], t["heure_fin"], t["statut_source"], t["pause_apres_s"])
              for t in g["trajets"]])

    # ================================================================
    sections_vues.append("E")
    print("\n[E] Contrat grille strict — re-vérification globale des 4 lignes")
    for veh, nom in ((v4526, "4526"), (v5316, "5316"), (v9856, "9856"),
                     (v0916, "0916")):
        g = grille(veh)
        ts = g["trajets"]
        ok = True
        for i, t in enumerate(ts):
            if t["heure_fin"] is None and i != len(ts) - 1:
                ok = False                              # en cours non terminal
            if t["heure_fin"] is None and t["statut_source"] != "PROVISOIRE":
                ok = False
            if i > 0:
                prec = ts[i - 1]
                if prec["pause_apres_s"] not in (0,) and prec["pause_apres_s"] < 1800:
                    ok = False                          # v3 AM-2 : pause 1-29 min masquée (0)
                if prec["heure_fin"] is None or t["heure_debut"] <= prec["heure_fin"]:
                    ok = False                          # recouvrement / ordre cassé
        check(f"{nom} : contrat v3 respecté (croissant, pauses affichées ≥ 30 min "
              f"ou masquées, en cours uniquement en dernier)", ok,
              f"{[(t['heure_debut'], t['heure_fin'], t['statut_source']) for t in ts]}")


    # ── CONTRÔLE DE COMPLÉTUDE : aucune section silencieusement sautée ──────
    _manquantes = [x for x in SECTIONS_ATTENDUES if x not in sections_vues]
    if _manquantes:
        raise RuntimeError(f"section(s) non exécutée(s) : {_manquantes} — "
                           "le scénario est incomplet, il n'y a pas de verdict")
except BaseException as _exc:   # AUCUNE exception n'est masquée : ni import,
    # ni exécution, ni assertion. Un test interrompu n'est PAS un test vert.
    print(f"\n=== ABANDON : {type(_exc).__name__}: {_exc} ===", file=sys.stderr)
    print("=== AUCUN verdict pour cette suite : contrôles non exécutés ===",
          file=sys.stderr)
    raise                        # traceback + code de sortie NON NUL
finally:
    db.close()
    fichier = db_url.split("///")[-1]
    if fichier and os.path.exists(fichier):
        os.remove(fichier)

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
