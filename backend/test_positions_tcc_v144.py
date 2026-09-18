# -*- coding: utf-8 -*-
"""Tests v1.44 — §0vicies decies N1→N3 (arbitrages LSS du 31/08/2026) :
TCC masqué hors temps réel (« 0:00 ») & positions automatiques 18h/20h/22h.

[A] Modèle & migration : 2 colonnes sur base EXISTANTE (ALTER idempotent) ;
[B] N1 — masquage TCC : lecture seule (jamais réécrit), partout sauf le jour
    en cours (Suivi passé, Historique liste/grille, synthèse, exports).
    v1.52 (18/09/2026) — RÉVISION : le masquage n'ÉCRASE plus `tcc_s` ; il est
    porté par le drapeau d'affichage `tcc_masque` (valeur vérifiable en base,
    dans les archives et les exports techniques) ;
[C] N2 — auto 18h/20h/22h : dernière position connue SANS limite d'âge
    (sémantique « position affichée au portail »), réécriture jusqu'à minuit,
    colonnes manuelles 08h→16h jamais touchées ;
[D] N3 — rattrapage J-1→J-7 : cellules VIDES seulement (jamais d'écrasement),
    audit `suivi.position_rattrapee` + régénération d'archive auditée ;
[E] Exports : Pos. 20h/22h dans Excel/PDF (formats 27 colonnes compact ;
    indices provisoires décalés), cohérence lignes/en-têtes ;
[F] Garde-fou : loi gravée avant codage, version 1.44, régressions M1-M4.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v144.db" SIM_ENABLE=0 python3 test_positions_tcc_v144.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_v144.db")
import sys
from datetime import date, datetime, timedelta

DB_FILE = os.environ["DATABASE_URL"].replace("sqlite:////", "/")
if not os.environ["DATABASE_URL"].startswith("sqlite:////tmp/"):  # garde §0sexies
    print("REFUS : DATABASE_URL doit pointer une base /tmp (jamais production)")
    sys.exit(2)
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

R = {"ok": 0, "ko": 0}


def check(nom: str, cond: bool, detail: str = "") -> None:
    if cond:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} — {detail}")


from sqlalchemy import inspect, select, text

from app.database import SessionLocal, engine as _engine
from app.engine import (auto_positions_horaires, prefill_positions_gps,
                        rattraper_positions_horaires)
from app.main import APP_VERSION, migrer_schema
from app.models import (AuditLog, EvenementGPS, HistoriqueJournalier,
                        SuiviJournalier, Vehicule)
from app.reconciliation import _synchroniser_archive
from app.seed import seed_si_vide
from app.serializers import fusionner_snapshot, s_historique, s_suivi
import app.exporters as expo

AUJ = date.today()


def vehicule(db):
    return db.scalars(select(Vehicule).order_by(Vehicule.plaque)).first()


def mk_suivi(db, vid, jour: date, **kw) -> SuiviJournalier:
    s = SuiviJournalier(date_jour=jour, vehicule_id=vid, **kw)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def mk_event(db, vid, ts: datetime, adresse: str | None = None,
             lat: float = -18.9, lng: float = 47.5) -> EvenementGPS:
    ev = EvenementGPS(vehicule_id=vid, horodatage=ts, latitude=lat,
                      longitude=lng, adresse=adresse)
    db.add(ev)
    db.commit()
    return ev


def get_arch(db, jour: date, vid: str):
    return db.scalars(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == jour,
        HistoriqueJournalier.vehicule_id == vid)).first()


def ligne_fake(**kw):
    l = {"plaque": "0000TST", "description": "Test", "conducteur": {},
         "situation": "—", "statut_camion": "—", "tcc_s": 0,
         "position_20h": "Depot Soir", "position_22h": "Garage Nuit",
         "trajets": []}
    l.update(kw)
    return l


db = SessionLocal()
try:
    seed_si_vide()
    db.expire_all()
    v = vehicule(db)

    print("\n[A] Modèle, migration sur base existante, sérialisation")
    cols = {c["name"] for c in inspect(_engine).get_columns("suivi_journalier")}
    check("A1 modèle : position_20h & position_22h créées (base neuve)",
          {"position_20h", "position_22h"} <= cols)
    # Simule la base de l'exploitant (v1.43 sans les colonnes) → migration
    with _engine.begin() as cx:
        cx.execute(text("ALTER TABLE suivi_journalier DROP COLUMN position_20h"))
        cx.execute(text("ALTER TABLE suivi_journalier DROP COLUMN position_22h"))
    migrer_schema()
    cols2 = {c["name"] for c in inspect(_engine).get_columns("suivi_journalier")}
    check("A2 migration v1.44 : colonnes ajoutées sur base existante (ALTER)",
          {"position_20h", "position_22h"} <= cols2)
    migrer_schema()   # idempotent : second passage sans erreur
    check("A3 migration idempotente (double passage sans erreur)", True)
    s0 = mk_suivi(db, v.id, AUJ, tcc_s=7200)
    sl = s_suivi(db.get(SuiviJournalier, s0.id))
    check("A4 s_suivi expose position_20h/position_22h (None au départ)",
          "position_20h" in sl and "position_22h" in sl
          and sl["position_20h"] is None)

    print("\n[B] N1 — TCC masqué « 0:00 » partout sauf le jour en cours")
    snap = {"tcc_s": 7200, "trajets": [], "plaque": "0000TST"}
    masque = fusionner_snapshot(snap)
    # ── v1.52 (18/09/2026) — RÉVISION DE N1 (arbitrage LSS ; SPEC_RULES_v3
    # prime sur les addenda subordonnés). L'addendum du 31/08 demandait
    # d'ÉCRASER `tcc_s` à zéro hors temps réel : une valeur calculée était
    # détruite à la lecture, ce qui contredit la règle « le sérialiseur ne
    # remet JAMAIS `tcc_s` à zéro » et prive l'audit de la contre-vérification.
    # Le MÊME effet VISUEL est obtenu par le drapeau `tcc_masque` : l'écran
    # (Historique.tsx, GrilleSuivi.tsx) et l'export affichent toujours « 0:00 ».
    check("B1 fusionner_snapshot : valeur TCC CONSERVÉE (7200) + drapeau "
          "d'affichage `tcc_masque` (masquage déplacé de la donnée vers le rendu)",
          masque["tcc_s"] == 7200 and masque.get("tcc_masque") is True,
          f"tcc={masque['tcc_s']} masque={masque.get('tcc_masque')}")
    check("B2 …sans jamais muter le snapshot stocké (§A.2 — archive intacte)",
          snap["tcc_s"] == 7200)
    jour_h = AUJ - timedelta(days=1)
    sh = mk_suivi(db, v.id, jour_h, tcc_s=5400)
    h = get_arch(db, jour_h, v.id)          # la base démo archive déjà J-6→J-1
    if h is None:
        h = HistoriqueJournalier(date_jour=jour_h, annee=jour_h.year,
                                 mois=jour_h.month, vehicule_id=v.id,
                                 donnees={}, nb_infractions=0, nb_alertes=0)
        db.add(h)
    h.donnees = s_suivi(sh)                 # snapshot contrôlé (tcc 1:30 stocké)
    db.commit()
    db.refresh(h)
    arch = s_historique(h)
    check("B3 archive lue (s_historique) : valeur 1:30 CONSERVÉE + drapeau "
          "d'affichage levé (le rendu écran/export reste « 0:00 »)",
          arch["tcc_s"] == 5400 and arch.get("tcc_masque") is True
          and (h.donnees or {}).get("tcc_s") == 5400,
          f"tcc={arch['tcc_s']} masque={arch.get('tcc_masque')}")
    from app.routers.historique import _lignes_export
    lig = _lignes_export([h])[0]
    check("B4 synthèse mensuelle (export) : colonne TCC = « 0:00 »",
          lig[10] == "0:00", f"obtenu {lig[10]!r}")
    from app.routers.operations import (_masquer_tcc_si_jour_passe,
                                        _suivis_filtres)
    lignes_j = [dict(s_suivi(s0))]
    _masquer_tcc_si_jour_passe(lignes_j, AUJ)
    check("B5 jour EN COURS : chrono TCC vivant conservé (2:00 affiché)",
          lignes_j[0]["tcc_s"] == 7200)
    lignes_h = _suivis_filtres(db, jour_h, None, None)
    cible = [l for l in lignes_h if l["vehicule_id"] == v.id]
    check("B6 export Suivi d'un jour PASSÉ : valeur CONSERVÉE + drapeau "
          "`tcc_masque` (le rendu export reste « 0:00 » — cf. B4)",
          bool(cible) and all(l["tcc_s"] == 5400 and l.get("tcc_masque")
                             for l in cible),
          f"{[(l['tcc_s'], l.get('tcc_masque')) for l in cible]}")
    check("B7 s_historique détail : la fusion d'affichage E1 reste appliquée "
          "(régression v1.31)", "donnees" in s_historique(h, detail=True))

    print("\n[C] N2 — positions automatiques 18h/20h/22h (jour en cours)")
    mk_event(db, v.id, datetime.combine(AUJ, datetime.min.time()).replace(hour=5),
             "Dépôt A")
    mk_event(db, v.id, datetime.combine(AUJ, datetime.min.time()).replace(hour=17),
             "Station B")
    mk_event(db, v.id, datetime.combine(AUJ, datetime.min.time()).replace(hour=19, minute=40),
             "Tana Est")
    now1730 = datetime.combine(AUJ, datetime.min.time()).replace(hour=17, minute=30)
    check("C1 avant 18h : rien n'est rempli automatiquement",
          auto_positions_horaires(db, maintenant=now1730) == 0
          and s0.position_18h is None)
    db.expire_all()
    now19 = datetime.combine(AUJ, datetime.min.time()).replace(hour=19)
    auto_positions_horaires(db, maintenant=now19)
    db.expire_all()
    s0 = db.get(SuiviJournalier, s0.id)
    check("C2 à 19h : 18h = dernière position ≤ 18:00 (« Station B »)",
          s0.position_18h == "Station B" and s0.position_20h is None,
          f"{s0.position_18h!r} / {s0.position_20h!r}")
    now21 = datetime.combine(AUJ, datetime.min.time()).replace(hour=21)
    auto_positions_horaires(db, maintenant=now21)
    db.expire_all()
    s0 = db.get(SuiviJournalier, s0.id)
    check("C3 à 21h : 20h = « Tana Est » (19:40), 18h conservée",
          s0.position_20h == "Tana Est" and s0.position_18h == "Station B")
    late = EvenementGPS(vehicule_id=v.id, horodatage=datetime.combine(
        AUJ, datetime.min.time()).replace(hour=17, minute=58),
        latitude=-18.8, longitude=47.6, adresse="Brickaville RN2")
    db.add(late); db.commit()
    auto_positions_horaires(db, maintenant=now21)
    db.expire_all()
    s0 = db.get(SuiviJournalier, s0.id)
    check("C4 boîtier muet : la cellule 18h est RÉÉCRITE avec la meilleure "
          "donnée (« Brickaville RN2 », 17:58) tant que le jour n'est pas verrouillé",
          s0.position_18h == "Brickaville RN2", f"{s0.position_18h!r}")
    s0.position_08h = "Saisie main 8h"
    db.commit()
    auto_positions_horaires(db, maintenant=now21)
    db.expire_all()
    check("C5 colonnes MANUELLES (08h→16h) jamais touchées par l'auto",
          db.get(SuiviJournalier, s0.id).position_08h == "Saisie main 8h")
    v2 = db.scalars(select(Vehicule).where(Vehicule.id != v.id)
                    .order_by(Vehicule.plaque)).first()
    _v2_suivis = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v2.id, SuiviJournalier.date_jour == AUJ)).all()
    sv2 = _v2_suivis[0] if _v2_suivis else mk_suivi(db, v2.id, AUJ)
    mk_event(db, v2.id, datetime.combine(AUJ, datetime.min.time()).replace(hour=16, minute=50))
    auto_positions_horaires(db, maintenant=now21)
    db.expire_all()
    check("C6 sans libellé de zone : repli sur les coordonnées « lat,lng » (E4c)",
          db.get(SuiviJournalier, sv2.id).position_18h == "-18.9000,47.5000",
          f"{db.get(SuiviJournalier, sv2.id).position_18h!r}")
    check("C7 pré-remplissage manuel (bouton) : couvre aussi 20h/22h (aide)",
          prefill_positions_gps(db, AUJ, jusqu_a=datetime.combine(
              AUJ, datetime.min.time()).replace(hour=23)) >= 1)
    db.expire_all()
    check("C8 pré-remplissage : la cellule 22h du bouton est renseignée",
          db.get(SuiviJournalier, sv2.id).position_22h is not None)

    print("\n[D] N3 — rattrapage des jours passés (cellules VIDES seulement)")
    j3 = AUJ - timedelta(days=3)
    sj3 = mk_suivi(db, v.id, j3, position_20h="MANUEL 20h")
    mk_event(db, v.id, datetime.combine(j3, datetime.min.time()).replace(hour=17, minute=50),
             "Dépôt C")
    mk_event(db, v.id, datetime.combine(j3, datetime.min.time()).replace(hour=21, minute=10),
             "Garage D")
    j9 = AUJ - timedelta(days=9)
    sj9 = mk_suivi(db, v.id, j9)
    mk_event(db, v.id, datetime.combine(j9, datetime.min.time()).replace(hour=17, minute=30),
             "Ancien J-9")
    adv = datetime.combine(AUJ, datetime.min.time()).replace(hour=23)
    ids = rattraper_positions_horaires(db, maintenant=adv, jours=7)
    db.expire_all()
    sj3 = db.get(SuiviJournalier, sj3.id)
    check("D1 J-3 : cellule 18h VIDE complétée (« Dépôt C »)",
          sj3.position_18h == "Dépôt C", f"{sj3.position_18h!r}")
    check("D2 J-3 : cellule 20h DÉJÀ REMPLIE jamais écrasée (« MANUEL 20h »)",
          sj3.position_20h == "MANUEL 20h")
    check("D3 J-3 : cellule 22h VIDE complétée (« Garage D »)",
          sj3.position_22h == "Garage D")
    check("D4 J-9 hors fenêtre J-7 : non complété",
          db.get(SuiviJournalier, sj9.id).position_18h is None)
    check("D5 rattrapage retourne les ids des lignes modifiées",
          sj3.id in ids and sj9.id not in ids)
    aud = db.scalars(select(AuditLog).where(
        AuditLog.action == "suivi.position_rattrapee").order_by(AuditLog.id)).all()
    check("D6 audit `suivi.position_rattrapee` inscrit (cellules détaillées)",
          bool(aud) and "position_18h" in str(aud[-1].details))
    n_aud_avant = db.query(AuditLog).count()
    ids2 = rattraper_positions_horaires(db, maintenant=adv, jours=7)
    check("D7 rattrapage idempotent : second appel = 0 ligne, 0 audit nouveau",
          ids2 == [] and db.query(AuditLog).count() == n_aud_avant)
    # Régénération d'archive via le pipeline M1 (positions incluses)
    hj3 = get_arch(db, j3, v.id)
    if hj3 is None:
        hj3 = HistoriqueJournalier(date_jour=j3, annee=j3.year, mois=j3.month,
                                   vehicule_id=v.id, donnees={},
                                   nb_infractions=0, nb_alertes=0)
        db.add(hj3)
    hj3.donnees = {"plaque": "X"}           # archive dépourvue de positions
    db.commit()
    db.refresh(hj3)
    ok_sync = _synchroniser_archive(db, sj3)
    db.commit()
    db.expire_all()
    hj3 = get_arch(db, j3, v.id)
    arch = db.scalars(select(AuditLog).where(
        AuditLog.action == "archive.raffraichie").order_by(AuditLog.id)).all()
    check("D8 archive d'un jour rattrapé : positions recopiées dans le "
          "snapshot (écran = archive)", ok_sync
          and hj3.donnees.get("position_18h") == "Dépôt C")
    check("D9 …avec audit `archive.raffraichie` signalant les positions (N3)",
          bool(arch) and arch[-1].details.get("positions") is True)

    print("\n[E] Exports — Pos. 20h/22h, formats et cohérence")
    e_c = expo._entetes_suivi(False)
    e_d = expo._entetes_suivi(True)
    check("E1 en-têtes compact : 27 colonnes (25 + Pos. 20h/22h)",
          len(e_c) == 27, f"{len(e_c)}")
    check("E2 ordre : …Pos. 18h, Pos. 20h, Pos. 22h, TCC…",
          e_c.index("Pos. 18h") + 1 == e_c.index("Pos. 20h")
          and e_c.index("Pos. 22h") + 1 == e_c.index("TCC"))
    check("E3 en-têtes détaillées : 51 → 53 colonnes (amendement N2 déclaré)",
          len(e_d) == 53, f"{len(e_d)}")
    vals = expo._valeurs_suivi(ligne_fake(), False, False)
    check("E4 ligne = en-têtes (cohérence §3.3) et positions 20h/22h rendues",
          len(vals) == len(e_c) and "Depot Soir" in vals
          and "Garage Nuit" in vals)
    vals_d = expo._valeurs_suivi(ligne_fake(), True, False)
    check("E5 ligne détaillée = en-têtes détaillées", len(vals_d) == len(e_d))
    x = expo.export_suivi_excel("31/08/2026", [ligne_fake()], True, "tests")
    check("E6 export Excel généré (octets) avec les nouvelles colonnes",
          isinstance(x, bytes) and len(x) > 2000)
    p = expo.export_suivi_pdf("31/08/2026", [ligne_fake()], True, "tests")
    check("E7 export PDF généré (octets) avec les nouvelles colonnes",
          isinstance(p, bytes) and len(p) > 2000)
    check("E8 durée texte masquée : « 0:00 » (format H:MM existant)",
          expo._fmt_duree_txt(0) == "0:00")

    print("\n[F] Garde-fou — loi, version, front, régressions")
    loi = open("/home/user/REFERENCE_IA_REGLES.md", encoding="utf-8").read()
    check("F1 loi §0vicies decies gravée AVANT codage (N1→N3, mandants)",
          "0vicies decies" in loi and "« 0:00 »" in loi
          and "position_rattrapee" in loi and "Pos. 20h" in loi)
    # v1.45 (réalignement déclaré) : la version peut être ≥ 1.44 (suite de la
    # plateforme) — on vérifie le palier minimal, pas l'égalité figée.
    def _v_tuple(ver: str):
        try:
            return tuple(int(p) for p in ver.split(".")[:2])
        except ValueError:
            return (0, 0)
    import re as _re
    check("F2 version ≥ 1.44 (main)", _v_tuple(APP_VERSION) >= (1, 44),
          APP_VERSION)
    login = open("/home/user/frontend/src/pages/Login.tsx", encoding="utf-8").read()
    grille = open("/home/user/frontend/src/components/GrilleSuivi.tsx",
                  encoding="utf-8").read()
    m = _re.search(r"v1\.(\d+)", login)
    check("F3 front : Login v1.4x (≥ 44), grille 8 relevés (20h/22h), prop "
          "masquerTCC", bool(m) and int(m.group(1)) >= 44
          and '"20h"' in grille and '"22h"' in grille
          and "masquerTCC" in grille)
    types = open("/home/user/frontend/src/types.ts", encoding="utf-8").read()
    check("F4 types : position_20h / position_22h déclarés",
          "position_20h" in types and "position_22h" in types)
    scrapers_src = open("/home/user/backend/app/scrapers.py",
                        encoding="utf-8").read()
    check("F5 régressions : M1 (relecture J-7) et M4 (boîtiers muets) intacts",
          "RELECTURE_TRAJETS_JOURS" in scrapers_src
          and "auditer_boitiers_muets" in scrapers_src)
    check("F6 crochets N2/N3 câblés dans le collecteur",
          "auto_positions_horaires" in scrapers_src
          and "rattraper_positions_horaires" in scrapers_src)
finally:
    db.close()

total = R["ok"] + R["ko"]
print(f"\nRÉSULTAT : {R['ok']} OK / {R['ko']} KO (total {total})")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)      # auto-nettoyage : jamais de base de test résiduelle
sys.exit(1 if R["ko"] else 0)
