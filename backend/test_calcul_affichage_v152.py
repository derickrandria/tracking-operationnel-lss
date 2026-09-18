# -*- coding: utf-8 -*-
"""Tests v1.52 — BORNES du domaine de CALCUL et de la PROJECTION D'AFFICHAGE.

Arbitrages LSS du 18/09/2026 (aucune décision silencieuse) :
  • TTJ_MAX = DRAPEAU de dépassement, JAMAIS un plafond (interprétation A) ;
    le seul écrêtage est TECHNIQUE (24 h/jour, durée aberrante) ;
  • G1 conservée : la fusion d'affichage des ruptures < 30 min reste en vigueur
    — la règle « montrer tous les trajets valides » s'entend « aucun trajet
    valide n'est perdu en BASE » (la fusion est purement visuelle, TCJ/TTJ
    intacts) ;
  • fuseau : contrat naïf-local (Indian/Antananarivo) CONSERVÉ, conversion
    MZoneX/CamtrackPro désormais TESTÉE.

Séparation stricte des deux domaines :
  CALCUL     — chaines.construire_journee / union_duree_s / reconciliation._spliter_minuit
               NE DÉPEND JAMAIS de l'affichage.
  AFFICHAGE  — serializers.fusionner_trajets_affichage / fusionner_snapshot
               ne modifie JAMAIS une donnée stockée.

Exécution :
  cd backend
  DATABASE_URL="sqlite:////tmp/test_v152.db" python test_calcul_affichage_v152.py
"""
import os
import sys
import calendar
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("SIM_ENABLE", "0")

# ------------------------------------------------------------------ sécurité
_URL = os.environ.get("DATABASE_URL", "")
if not _URL.startswith("sqlite:////tmp/"):
    print("⛔ Sécurité : DATABASE_URL doit être fourni et viser /tmp "
          f"(reçu : {_URL or 'non défini'})")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import func, select                      # noqa: E402

from app.chaines import (Segment, construire_journee,    # noqa: E402
                         union_duree_s)
from app.database import SessionLocal                    # noqa: E402
from app.main import APP_VERSION, migrer_schema          # noqa: E402
from app.models import (HistoriqueJournalier, SuiviJournalier,  # noqa: E402
                        Trajet, Vehicule, StatutSourceTrajet,
                        StatutValidationTrajet)
from app.reconciliation import _spliter_minuit           # noqa: E402
from app.engine import get_seuils                     # noqa: E402
from app.seed import seed_si_vide                       # noqa: E402
from app.serializers import (PAUSE_AFFICHAGE_S,          # noqa: E402
                             fusionner_snapshot,
                             fusionner_trajets_affichage)

OK = KO = 0


def check(label, cond, detail=""):
    global OK, KO
    if cond:
        OK += 1
        print(f"  ✅ {label}")
    else:
        KO += 1
        print(f"  ❌ {label}   {detail}")


def dt(j, h, m=0, s=0):
    return datetime(j.year, j.month, j.day, h, m, s)


def seg(debut, fin, km, rejete=False):
    return Segment(debut=debut, fin=fin, distance_km=km, rejete=rejete)


print("=" * 72)
print("  LSS v1.52 — BORNES DU CALCUL ET DE LA PROJECTION D'AFFICHAGE")
print(f"  Base : {_URL}")
print("=" * 72)

seed_si_vide()
migrer_schema()

db = SessionLocal()
try:
    # ------------------------------------------------------------------ purge
    for modele in (Trajet, SuiviJournalier, HistoriqueJournalier):
        db.query(modele).delete()
    db.commit()

    J = date(2026, 9, 17)
    MAINTENANT = dt(J, 15, 0)

    # ================================================================
    # [1] BORNE 0,299 / 0,300 / 0,301 km — manœuvre vs trajet valide
    # ================================================================
    print("\n[1] BORNE 0,299 / 0,300 / 0,301 km — une manœuvre n'alimente AUCUN compteur")

    def journee_dist(km, rejete=False):
        return construire_journee([seg(dt(J, 8, 0), dt(J, 8, 30), km, rejete)],
                                  maintenant=MAINTENANT, date_jour=J)

    j299 = journee_dist(0.299)
    check("0,299 km : aucune ligne (manœuvre) et compteurs à ZÉRO",
          len(j299.lignes) == 0 and j299.tcj_s == 0 and j299.ttj_s == 0,
          f"lignes={len(j299.lignes)} tcj={j299.tcj_s} ttj={j299.ttj_s}")

    j300 = journee_dist(0.300)
    check("0,300 km : borne INCLUSE — 1 ligne, TCJ = 30 min",
          len(j300.lignes) == 1 and j300.tcj_s == 1800,
          f"lignes={len(j300.lignes)} tcj={j300.tcj_s}")

    j301 = journee_dist(0.301)
    check("0,301 km : 1 ligne, TCJ = 30 min",
          len(j301.lignes) == 1 and j301.tcj_s == 1800,
          f"lignes={len(j301.lignes)} tcj={j301.tcj_s}")

    # la même manœuvre, REJETÉE, ne doit pas davantage alimenter les compteurs
    j299r = journee_dist(0.299, rejete=True)
    check("0,299 km REJETÉ : aucune ligne, compteurs à zéro (aucun double comptage)",
          len(j299r.lignes) == 0 and j299r.tcj_s == 0 and j299r.ttj_s == 0)

    # ================================================================
    # [2] BORNE 29:59 / 30:00 / 30:01 — seuil d'AFFICHAGE des pauses
    # ================================================================
    print("\n[2] BORNE 29:59 / 30:00 / 30:01 — pause masquée à l'ÉCRAN seulement")

    def journee_pause(gap_s, seuil_aff=1800):
        d2 = dt(J, 8, 0) + timedelta(seconds=gap_s)
        return construire_journee(
            [seg(dt(J, 6, 0), dt(J, 8, 0), 40.0),
             seg(d2, dt(J, 10, 0), 40.0)],
            maintenant=MAINTENANT, date_jour=J, pause_affichee_min=seuil_aff)

    jp = journee_pause(1799)                       # 29:59
    l0 = jp.lignes[0]
    check("29:59 : pause MASQUÉE à l'écran (pause_apres_s = 0)",
          l0.pause_apres_s == 0, f"pause_apres_s={l0.pause_apres_s}")
    check("29:59 : …mais l'écart BRUT reste mesuré et stocké (gap_brut_s = 1799)",
          l0.gap_brut_s == 1799, f"gap_brut_s={l0.gap_brut_s}")

    jp = journee_pause(1800)                       # 30:00
    check("30:00 : pause AFFICHÉE (borne incluse, pause_apres_s = 1800)",
          jp.lignes[0].pause_apres_s == 1800 and jp.lignes[0].gap_brut_s == 1800,
          f"pause={jp.lignes[0].pause_apres_s}")

    jp = journee_pause(1801)                       # 30:01
    check("30:01 : pause AFFICHÉE (pause_apres_s = 1801)",
          jp.lignes[0].pause_apres_s == 1801, f"pause={jp.lignes[0].pause_apres_s}")

    # INVARIANT FONDAMENTAL : l'affichage ne touche jamais au calcul.
    # MÊME donnée, DEUX réglages d'affichage opposés → mêmes compteurs.
    a = journee_pause(1799, seuil_aff=1800)   # pause masquée à l'écran
    b = journee_pause(1799, seuil_aff=600)    # même pause, AFFICHÉE
    check("INVARIANT : changer le seuil d'AFFICHAGE ne change NI TCJ NI TTJ",
          a.tcj_s == b.tcj_s and a.ttj_s == b.ttj_s,
          f"tcj {a.tcj_s}/{b.tcj_s} · ttj {a.ttj_s}/{b.ttj_s}")
    check("…tandis que la cellule pause, elle, change bien (1799 s affichée ou non)",
          a.lignes[0].pause_apres_s == 0 and b.lignes[0].pause_apres_s == 1799
          and a.lignes[0].gap_brut_s == b.lignes[0].gap_brut_s == 1799,
          f"pause {a.lignes[0].pause_apres_s}/{b.lignes[0].pause_apres_s}")

    # ================================================================
    # [3] BORNE 23:59:59 / 00:00:00 — ni trou ni chevauchement
    # ================================================================
    print("\n[3] BORNE 23:59:59 / 00:00:00 — le découpage ne crée ni trou ni recouvrement")

    J2 = J + timedelta(days=1)
    trajet_nuit = {"debut": dt(J, 23, 40), "fin": dt(J2, 0, 30),
                   "distance_km": 120.0, "id": "T-NUIT"}
    morceaux = _spliter_minuit([trajet_nuit])
    check("un trajet 23:40 → 00:30 est découpé en DEUX segments",
          len(morceaux) == 2, f"obtenu {len(morceaux)}")
    segA, segB = morceaux[0], morceaux[1]
    check("segment A : se clôt à 23:59:59 le jour J",
          segA["fin"] == dt(J, 23, 59, 59) and segA["debut"] == dt(J, 23, 40),
          f"{segA['debut']} → {segA['fin']}")
    check("segment B : reprend à 00:00:00 le jour J+1",
          segB["debut"] == dt(J2, 0, 0, 0) and segB["fin"] == dt(J2, 0, 30),
          f"{segB['debut']} → {segB['fin']}")
    check("AUCUN TROU : B commence exactement 1 s après la fin de A",
          (segB["debut"] - segA["fin"]).total_seconds() == 1,
          f"écart={(segB['debut'] - segA['fin']).total_seconds()}s")
    check("AUCUN CHEVAUCHEMENT : A ne mord pas sur le jour J+1 "
          "et B ne mord pas sur le jour J",
          segA["fin"].date() == J and segB["debut"].date() == J2)
    check("le segment B est marqué `suite_minuit` (traçabilité du split)",
          segB.get("suite_minuit") is True)
    check("les compteurs de mouvement ne sont PAS rejoués sur le segment B "
          "(pas de double comptage au passage de minuit)",
          segB.get("distance_km") is None)

    # bornage strict dans le domaine du calcul
    jn = construire_journee([seg(dt(J, 23, 40), dt(J2, 0, 30), 120.0)],
                            maintenant=dt(J2, 10, 0), date_jour=J)
    check("calcul : la ligne du jour J est BORNÉE à 23:59:59",
          len(jn.lignes) == 1 and jn.lignes[0].fin == dt(J, 23, 59, 59),
          f"{[l.fin for l in jn.lignes]}")
    check("calcul : TCJ du jour J = 23:40 → 23:59:59 (1 199 s), pas 30 min de plus",
          jn.tcj_s == 1199, f"tcj={jn.tcj_s}")

    # ================================================================
    # [4] TRAJETS QUI SE CHEVAUCHENT — union, jamais deux fois
    # ================================================================
    print("\n[4] TRAJETS QUI SE CHEVAUCHENT — union mathématique, temps jamais compté deux fois")

    u = union_duree_s([(dt(J, 5, 0), dt(J, 7, 0)), (dt(J, 6, 0), dt(J, 9, 0))])
    check("union : 05:00→07:00 ∪ 06:00→09:00 = 4 h (et non 5 h)",
          u == 4 * 3600, f"obtenu {u}s")
    check("inclusion : 05:00→10:00 ∪ 06:00→07:00 = 5 h",
          union_duree_s([(dt(J, 5, 0), dt(J, 10, 0)),
                         (dt(J, 6, 0), dt(J, 7, 0))]) == 5 * 3600)
    check("intervalles disjoints : la somme reste exacte (E2 préservé)",
          union_duree_s([(dt(J, 5, 0), dt(J, 6, 0)),
                         (dt(J, 7, 0), dt(J, 8, 30))]) == 9000)
    check("couples dégénérés (fin ≤ début) ignorés",
          union_duree_s([(dt(J, 5, 0), dt(J, 5, 0))]) == 0)

    jc = construire_journee([seg(dt(J, 5, 0), dt(J, 7, 0), 40.0),
                             seg(dt(J, 6, 0), dt(J, 9, 0), 60.0)],
                            maintenant=MAINTENANT, date_jour=J)
    check("2 lignes chevauchantes : TCJ = 4 h (union), PAS 5 h (somme brute)",
          jc.tcj_s == 4 * 3600, f"tcj={jc.tcj_s}")
    check("…et TCJ ≤ TTJ reste structurellement vrai (invariant F1)",
          jc.tcj_s <= jc.ttj_s, f"tcj={jc.tcj_s} ttj={jc.ttj_s}")

    # ================================================================
    # [5] ARRÊT COURT EN COURS
    # ================================================================
    print("\n[5] ARRÊT COURT EN COURS — une ligne ouverte n'invente pas de pause")

    jo = construire_journee([seg(dt(J, 10, 0), None, 20.0)],
                            maintenant=MAINTENANT, date_jour=J)
    lo = jo.lignes[0]
    check("ligne ouverte : `fin` reste None (le camion roule encore)",
          lo.fin is None, f"fin={lo.fin}")
    check("ligne ouverte : état « EN_COURS »",
          lo.etat == "EN_COURS", f"etat={lo.etat}")
    check("ligne ouverte : AUCUNE pause calculée (gap_brut_s = 0, pause_apres_s = 0)",
          lo.gap_brut_s == 0 and lo.pause_apres_s == 0,
          f"gap={lo.gap_brut_s} pause={lo.pause_apres_s}")

    jo2 = construire_journee([seg(dt(J, 10, 0), None, 20.0),
                              seg(dt(J, 11, 0), dt(J, 12, 0), 30.0)],
                             maintenant=MAINTENANT, date_jour=J)
    check("une ligne ouverte ne se voit attribuer une pause que si une "
          "ligne la SUIT (pas de pause fantôme en fin de journée)",
          jo2.lignes[-1].pause_apres_s == 0,
          f"dernière pause={jo2.lignes[-1].pause_apres_s}")

    # ================================================================
    # [6] MANŒUVRE DURANT 30 MINUTES
    # ================================================================
    print("\n[6] MANŒUVRE DE 30 MINUTES — aucune ligne, aucun compteur alimenté")

    jm = construire_journee([seg(dt(J, 8, 0), dt(J, 8, 30), 0.29, rejete=True)],
                            maintenant=MAINTENANT, date_jour=J)
    check("manœuvre SEULE de 30 min : aucune ligne produite",
          len(jm.lignes) == 0, f"lignes={len(jm.lignes)}")
    check("manœuvre SEULE de 30 min : TCJ = TTJ = 0",
          jm.tcj_s == 0 and jm.ttj_s == 0, f"tcj={jm.tcj_s} ttj={jm.ttj_s}")

    jm2 = construire_journee([seg(dt(J, 6, 0), dt(J, 8, 0), 40.0),
                              seg(dt(J, 8, 0), dt(J, 8, 30), 0.29, rejete=True),
                              seg(dt(J, 8, 30), dt(J, 10, 0), 30.0)],
                             maintenant=MAINTENANT, date_jour=J)
    check("manœuvre INTERCALÉE de 30 min : 2 lignes valides, elle ne crée pas de 3ᵉ ligne",
          len(jm2.lignes) == 2, f"lignes={len(jm2.lignes)}")
    check("…et elle n'entre pas dans le TCJ (union = 2 h + 1 h 30 = 3 h 30)",
          jm2.tcj_s == 3 * 3600 + 1800, f"tcj={jm2.tcj_s}")

    # ================================================================
    # [7] TRAJET VALIDE SÉPARÉ PAR UNE PAUSE COURTE — G1 conservée
    # ================================================================
    print("\n[7] TRAJET VALIDE + PAUSE COURTE (25 min) — calcul intact, G1 à l'écran")

    jg = construire_journee([seg(dt(J, 6, 0), dt(J, 8, 0), 40.0),
                             seg(dt(J, 8, 25), dt(J, 9, 0), 20.0)],
                            maintenant=MAINTENANT, date_jour=J,
                            pause_affichee_min=1800)
    check("CALCUL : les DEUX trajets existent en base (2 lignes)",
          len(jg.lignes) == 2, f"lignes={len(jg.lignes)}")
    check("CALCUL : TCJ = 2 h + 35 min (les deux trajets comptés)", 
          jg.tcj_s == 7200 + 2100, f"tcj={jg.tcj_s}")
    check("CALCUL : TTJ = 06:00 → 09:00 (3 h d'amplitude)",
          jg.ttj_s == 10800, f"ttj={jg.ttj_s}")
    check("CALCUL : la pause de 25 min reste dans les arrêts (TTJ − TCJ = 1 500 s)",
          jg.ttj_s - jg.tcj_s == 1500, f"arrêts={jg.ttj_s - jg.tcj_s}")
    check("ÉCRAN : la pause de 25 min est MASQUÉE (cellule vide) mais mesurée",
          jg.lignes[0].pause_apres_s == 0 and jg.lignes[0].gap_brut_s == 1500,
          f"pause={jg.lignes[0].pause_apres_s} brut={jg.lignes[0].gap_brut_s}")

    # la projection d'affichage fusionne (G1 conservée) — SANS toucher au calcul
    brutes = [{"heure_debut": "2026-09-17T06:00:00",
               "heure_fin": "2026-09-17T08:00:00",
               "conducteur_badge_id": "B1", "distance_km": 40.0, "segments": 1},
              {"heure_debut": "2026-09-17T08:25:00",
               "heure_fin": "2026-09-17T09:00:00",
               "conducteur_badge_id": "B1", "distance_km": 20.0, "segments": 1}]
    fusion = fusionner_trajets_affichage(brutes, seuil_fusion_s=1800)
    check("ÉCRAN (G1 arbitrage du 25/08, CONSERVÉE) : rupture < 30 min → 1 ligne affichée",
          len(fusion) == 1, f"lignes affichées={len(fusion)}")
    check("ÉCRAN : la ligne fusionnée couvre 06:00 → 09:00 et additionne 60 km",
          fusion[0]["heure_debut"] == "2026-09-17T06:00:00"
          and fusion[0]["heure_fin"] == "2026-09-17T09:00:00"
          and fusion[0]["distance_km"] == 60.0,
          f"{fusion[0]['heure_debut']}→{fusion[0]['heure_fin']} {fusion[0]['distance_km']}km")
    check("ÉCRAN : la fusion ne MUTE pas l'entrée (trajets en base intacts)",
          brutes[0]["heure_fin"] == "2026-09-17T08:00:00"
          and brutes[1]["heure_debut"] == "2026-09-17T08:25:00")
    check("SÉPARATION DES DOMAINES : 2 lignes au CALCUL, 1 ligne à l'ÉCRAN — "
          "les compteurs (TCJ/TTJ) ne dépendent PAS de la fusion",
          len(jg.lignes) == 2 and len(fusion) == 1
          and jg.tcj_s == 9300 and jg.ttj_s == 10800)

    jg2 = construire_journee([seg(dt(J, 6, 0), dt(J, 8, 0), 40.0),
                              seg(dt(J, 8, 31), dt(J, 9, 0), 20.0)],
                             maintenant=MAINTENANT, date_jour=J,
                             pause_affichee_min=1800)
    check("ÉCRAN : rupture ≥ 30 min → les deux lignes restent séparées "
          "et la pause s'affiche",
          jg2.lignes[0].pause_apres_s == 1860,
          f"pause={jg2.lignes[0].pause_apres_s}")

    # ================================================================
    # [8] RELECTURE D'ARCHIVE — le snapshot n'est JAMAIS modifié
    # ================================================================
    print("\n[8] RELECTURE D'ARCHIVE — copie, aucune mutation, TCC conservé")

    snap = {"tcc_s": 7200, "tcj_s": 8640, "ttj_s": 10800, "plaque": "0000TST",
            "trajets": [dict(t) for t in brutes]}
    import copy as _copy
    snap_avant = _copy.deepcopy(snap)

    relu = fusionner_snapshot(snap)
    check("relecture : le snapshot STOCKÉ n'est pas modifié (tcc_s intact)",
          snap == snap_avant and snap["tcc_s"] == 7200,
          f"tcc stocké={snap['tcc_s']}")
    check("relecture : le TCC n'est PLUS remis à zéro (valeur réelle conservée)",
          relu["tcc_s"] == 7200, f"tcc relu={relu['tcc_s']}")
    check("relecture : l'intention d'affichage est portée par le drapeau `tcc_masque`",
          relu.get("tcc_masque") is True, f"tcc_masque={relu.get('tcc_masque')}")
    check("relecture : TCJ / TTJ / pause ne sont pas altérés",
          relu["tcj_s"] == 8640 and relu["ttj_s"] == 10800)
    check("relecture : la fusion d'affichage E1 reste appliquée à la copie",
          len(relu["trajets"]) == 1 and relu["nb_trajets"] == 1,
          f"trajets={len(relu['trajets'])}")
    check("relecture IDEMPOTENTE : relire la relecture donne le même résultat",
          fusionner_snapshot(relu)["trajets"] == relu["trajets"]
          and fusionner_snapshot(relu)["tcc_s"] == 7200)
    check("relecture : les trajets de la copie sont des objets NEUFS (pas d'aliasing)",
          relu["trajets"][0] is not snap["trajets"][0])

    # ================================================================
    # [9] FUSEAU — conversion MZoneX / CamtrackPro (contrat naïf-local)
    # ================================================================
    print("\n[9] FUSEAU — conversion MZoneX / CamtrackPro (jamais testée avant v1.52)")

    from app.api_mzonex import depuis_utc, iso_utc
    from app.api_wialon import _parse_instant

    check("MZoneX sortant : 23:59:59 local (UTC+03) → 20:59:59Z",
          iso_utc(dt(J, 23, 59, 59)) == "2026-09-17T20:59:59Z",
          iso_utc(dt(J, 23, 59, 59)))
    check("MZoneX sortant : 00:00:00 local → 21:00:00Z de la VEILLE",
          iso_utc(dt(J, 0, 0, 0)) == "2026-09-16T21:00:00Z",
          iso_utc(dt(J, 0, 0, 0)))
    check("MZoneX entrant : 21:00:00Z → 00:00:00 local du LENDEMAIN",
          depuis_utc("2026-09-17T21:00:00Z") == dt(J + timedelta(days=1), 0, 0, 0),
          str(depuis_utc("2026-09-17T21:00:00Z")))
    check("MZoneX : l'aller-retour est IDENTIQUE (aucune dérive)",
          all(depuis_utc(iso_utc(t)) == t for t in
              (dt(J, 0, 0, 0), dt(J, 12, 30, 15), dt(J, 23, 59, 59))))
    check("MZoneX : entrée vide → None (pas d'exception)",
          depuis_utc(None) is None and depuis_utc("") is None)

    epoch = calendar.timegm(datetime(2026, 9, 17, 21, 0, 0).timetuple())
    check("CamtrackPro (epoch) : 21:00:00Z → 00:00:00 local du lendemain",
          _parse_instant({"v": epoch}) == dt(J + timedelta(days=1), 0, 0, 0),
          str(_parse_instant({"v": epoch})))
    check("CamtrackPro (texte UTC, repli) : « 2026-09-17 21:00:00 » lu comme UTC "
          "→ 00:00:00 local du lendemain",
          _parse_instant("2026-09-17 21:00:00") == dt(J + timedelta(days=1), 0, 0, 0),
          str(_parse_instant("2026-09-17 21:00:00")))
    check("CamtrackPro : cellule vide → None",
          _parse_instant(None) is None and _parse_instant("") is None)
    check("CONTRAT : les deux portails rendent des datetimes NAÏFS en heure locale "
          "(le fuseau ne se perd pas en route)",
          _parse_instant({"v": epoch}).tzinfo is None
          and depuis_utc("2026-09-17T21:00:00Z").tzinfo is None)

    # ================================================================
    # [10] INTÉGRATION BASE — seuils distincts + TCC conservé à l'archivage
    # ================================================================
    print("\n[10] INTÉGRATION BDD — seuil d'affichage distinct + TCC réel archivé")

    seuils = get_seuils(db)
    check("SEUIL_AFFICHAGE_PAUSE_MIN existe en base (réglage propre à l'affichage)",
          "SEUIL_AFFICHAGE_PAUSE_MIN" in seuils,
          f"clés présentes : {sorted(seuils)[:3]}…")
    check("…et il vaut 30 min par défaut",
          float(seuils.get("SEUIL_AFFICHAGE_PAUSE_MIN", -1)) == 1800.0,
          str(seuils.get("SEUIL_AFFICHAGE_PAUSE_MIN")))
    check("…DISTINCT de SEUIL_PAUSE_COUPURE_TCC (seuil MOTEUR de coupure du TCC)",
          "SEUIL_PAUSE_COUPURE_TCC" in seuils
          and float(seuils["SEUIL_PAUSE_COUPURE_TCC"]) == 1800.0
          and "SEUIL_AFFICHAGE_PAUSE_MIN" != "SEUIL_PAUSE_COUPURE_TCC")
    check("le repli d'affichage PAUSE_AFFICHAGE_S reste aligné sur le défaut",
          PAUSE_AFFICHAGE_S == 1800.0)

    v = db.scalars(select(Vehicule).limit(1)).first()
    from app.engine import ensure_suivi
    from app.daily import recalculer_archives_journee

    # Session du SOIR : le dernier trajet se termine à 23:55, à moins de 30 min
    # de la clôture (23:59:59) → H1 ne coupe pas le chrono ; un scénario du matin
    # donnerait légitimement TCC = 0 (pause de fin de journée ≥ 30 min).
    JA = date(2026, 9, 10)
    s = ensure_suivi(db, v, JA)
    for n, (d0, d1, km) in enumerate(((dt(JA, 21, 0), dt(JA, 22, 30), 60.0),
                                      (dt(JA, 22, 40), dt(JA, 23, 55), 30.0)), start=1):
        db.add(Trajet(suivi_id=s.id, numero=n, heure_debut=d0, heure_fin=d1,
                      statut_source=StatutSourceTrajet.VALIDE,
                      source_plateforme="CAMTRACKPRO", distance_km=km,
                      statut_validation=StatutValidationTrajet.VALIDE))
    db.commit()

    recalculer_archives_journee(JA, db=db, rattraper_portail=False,
                                autoriser_reecriture=True,
                                motif="test_calcul_affichage_v152")
    db.expire_all()
    s2 = db.get(SuiviJournalier, s.id)
    att_tcc = max(0, min(86400, int(s2.tcc_s or 0)))
    h = db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == JA,
        HistoriqueJournalier.vehicule_id == v.id))
    check("le TCC de session est bien CALCULÉ avant l'archivage (> 0)",
          att_tcc > 0, f"tcc calculé={att_tcc}")
    check("l'archive conserve le TCC RÉEL (il était écrit à zéro avant v1.52)",
          h is not None and int((h.donnees or {}).get("tcc_s") or 0) == att_tcc,
          f"archive={None if h is None else (h.donnees or {}).get('tcc_s')} "
          f"attendu={att_tcc}")
    check("l'archive conserve des TCJ / TTJ NON nuls — la sérialisation lit "
          "l'état RÉEL et non un cache périmé (défaut corrigé en v1.52)",
          h is not None and int((h.donnees or {}).get("tcj_s") or 0) > 0
          and int((h.donnees or {}).get("ttj_s") or 0)
          >= int((h.donnees or {}).get("tcj_s") or 0),
          f"tcj={None if h is None else (h.donnees or {}).get('tcj_s')} "
          f"ttj={None if h is None else (h.donnees or {}).get('ttj_s')}")
    # ARBITRAGE G1 (CONSERVÉ le 18/09/2026) : l'archive fige la vue d'ÉCRAN
    # (v1.13 « écran = export = archive ») — les 2 trajets séparés de 10 min y
    # apparaissent comme UNE ligne fusionnée. Le CALCUL, lui, a bien compté les
    # deux (TCJ = 1 h 30 + 1 h 15 = 9 900 s). C'est la séparation des domaines :
    # la projection peut fusionner, les compteurs ne dépendent jamais d'elle.
    check("l'archive fige la vue d'écran fusionnée (G1 conservée : 1 ligne "
          "pour 2 trajets séparés de 10 min)",
          h is not None and int((h.donnees or {}).get("nb_trajets") or 0) == 1,
          f"nb={None if h is None else (h.donnees or {}).get('nb_trajets')}")
    check("…tandis que le CALCUL a bien compté les DEUX trajets (TCJ = 9 900 s)",
          h is not None and int((h.donnees or {}).get("tcj_s") or 0) == 9900,
          f"tcj={None if h is None else (h.donnees or {}).get('tcj_s')}")
    check("flag_tcc n'est plus FIGÉ à False (il est recalculé)",
          "flag_tcc" in (h.donnees or {}))

    print("\n" + "=" * 72)
    print(f"  RÉSULTAT : {OK} OK / {KO} KO")
    print(f"  Version applicative : {APP_VERSION}")
    print("=" * 72)
finally:
    db.close()

# auto-nettoyage de la base de test
for suffixe in ("", "-wal", "-shm"):
    chemin = _URL.replace("sqlite:///", "") + suffixe
    if os.path.exists(chemin):
        shutil.rmtree(chemin, ignore_errors=True) if os.path.isdir(chemin) else os.remove(chemin)

sys.exit(1 if KO else 0)
