"""Tests v1.18 — RÉFÉRENCE IA v2 (loi) + ARBITRAGES MÉTIER du 06/08/2026.

Vérifie VERBATIM les points tranchés par l'exploitant contre le document
« REFERENCE_IA_REGLES.md » (§0bis) :

  A · §5.1/§5.2 STRICT   : fin = PREMIER arrêt après un trajet valide ;
                           manœuvre < 0,3 km IGNORÉE PARTOUT (§7) — cible
                           0616TCC « 5:31 | 8:24 | Pause 1:24 | 9:48 | 9:56 ».
  B · §8.2/§8.3/§9       : bascule 01h00 PARTOUT — 00:59 → HIER, 01:00 pile →
                           AUJOURD'HUI ; pré-consolidation arrête à 01h00.
  C · §11.2              : règle CamtrackPro « en mouvement » SUPPRIMÉE —
                           valide dès 0,3 km de distance seule (5716TBS : R4
                           0,99 km / 10 min redevient valide).
  D · §6.1               : réservé aux flux de positions — SEUIL_VITESSE_ARRET
                           = 3 km/h effectif depuis le 14/08/2026 (§5.1/§12.1 +
                           §0ter E : arrêt dès v ≤ 3 ; 5-11 km/h = en route).
  + §12.1                : seuils constants inchangés (0,3 km / 20 / 30 min /
                           TCC 4h30 / TCJ 10h / TTJ 12h).
  + §1.3                 : TTJ = TCJ + arrêts (jamais deux fois la même
                           seconde : arrêts comptables NETS des manœuvres).

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v118.db" python3 test_reference_v118.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.config import (HEURE_BASCULE_JOUR, bascule_du, jour_attribution,
                        now_local)
from app.database import SessionLocal
from app import daily, engine
from app.chaines import Segment, construire_journee
from app.models import (AuditLog, HistoriqueJournalier,
                        StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, Vehicule)
from app.reconciliation import reconcilier_trajets_valides
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.serializers import s_suivi

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

ancre = now_local().replace(microsecond=0)
if ancre.hour < 7:
    ancre = ancre.replace(hour=15, minute=0, second=0)
jour = jour_attribution(ancre)
base = datetime.combine(jour, datetime.min.time())


def h(hh, mm, ss=0, ref=None):
    return (ref or base).replace(hour=hh, minute=mm, second=ss)


mzx = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF").order_by(
        Vehicule.plaque)).all()
ctp = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "CAMTRACKPRO", Vehicule.statut == "ACTIF").order_by(
        Vehicule.plaque)).all()
v0616, v5716, vbasc = mzx[0], (ctp[0] if ctp else mzx[1]), mzx[2]
print(f"Véhicules test : 0616={v0616.plaque} · 5716-like={v5716.plaque} · "
      f"bascule={vbasc.plaque} · jour={jour}")

nettoie_suivis = set()
for v in (v0616, v5716, vbasc):
    for j in (jour, jour - timedelta(days=1)):
        s = engine.ensure_suivi(db, v, j)
        db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
        nettoie_suivis.add(s.id)
db.execute(delete(AuditLog).where(AuditLog.username.like("test-v118%")))
db.commit()


def visibles(veh, jour_j=None):
    s = engine.ensure_suivi(db, veh, jour_j or jour)
    db.expire_all()
    return [t for t in db.scalars(select(Trajet).where(
        Trajet.suivi_id == s.id).order_by(Trajet.numero)).all()
        if t.statut_validation != StatutValidationTrajet.REJETE]


try:
    # ================================================================
    print("\n[B · §8.2/§8.3] Bascule de journée à 01h00 (arbitrage du 06/08)")
    # v3 AM-3/C1 (22/08/2026) : bascule 01h00 ABROGÉE — DATE pure partout
    check("v3 : HEURE_BASCULE_JOUR = 0 (minuit — la bascule horaire est "
          "abrogée)", HEURE_BASCULE_JOUR == 0, f"{HEURE_BASCULE_JOUR}")
    check("v3 : 00:15 → AUJOURD'HUI (DATE pure)",
          jour_attribution(h(0, 15)) == jour)
    check("v3 : 00:50 → AUJOURD'HUI (DATE pure)",
          jour_attribution(h(0, 50)) == jour)
    check("v3 : 00:59:59 → AUJOURD'HUI (DATE pure)",
          jour_attribution(h(0, 59, 59)) == jour)
    check("01:00:00 pile → AUJOURD'HUI (inchangé)",
          jour_attribution(h(1, 0, 0)) == jour)
    check("01:05 → AUJOURD'HUI (inchangé)", jour_attribution(h(1, 5)) == jour)
    check("v3 : bascule_du à 00:30 = minuit pile (minuit civil)",
          bascule_du(h(0, 30)) == h(0, 0),
          f"{bascule_du(h(0, 30))}")

    # ================================================================
    print("\n[B · §9 / §C.6] Pré-consolidation 01h00 : en cours arrêté, tout OFFICIEL")
    veille = jour - timedelta(days=1)
    sv_b = engine.ensure_suivi(db, vbasc, veille)
    t_ouvert = Trajet(suivi_id=sv_b.id, numero=1,
                      heure_debut=h(23, 40, ref=base - timedelta(days=1)),
                      heure_fin=None,
                      statut_source=StatutSourceTrajet.PROVISOIRE,
                      source_plateforme="MZONEX", distance_km=None,
                      statut_validation=StatutValidationTrajet.EN_ATTENTE)
    db.add(t_ouvert)
    db.commit()
    apres = h(0, 5)                      # 00:05 — après minuit (v3)
    db.expire_all()                      # session longue : relecture forcée
    n = daily.pre_consolider_veille(db, veille, maintenant=apres)
    db.expire_all()
    t = db.get(Trajet, t_ouvert.id)
    check("v3 : le « en cours » de la veille est ARRÊTÉ à 23:59:59 "
          "(consolidation v3, silence radio → clôture pile)",
          t.heure_fin == datetime(veille.year, veille.month, veille.day,
                                  23, 59, 59), f"{t.heure_fin}")
    check("marqué OFFICIEL (statut_source VALIDÉ) sur le jour de la VEILLE",
          t.statut_source == StatutSourceTrajet.VALIDE)
    check("distance inconnue ≠ 0 km → jugé VALIDE (on ne juge pas ce qu'on "
          "ne mesure pas, §6.2 phase 2)",
          t.statut_validation == StatutValidationTrajet.VALIDE,
          f"{t.statut_validation}")
    check("1 trajet officialisé, idempotent au second passage",
          n == 1 and daily.pre_consolider_veille(db, veille, maintenant=apres) == 0,
          f"n={n}")
    db.execute(delete(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == veille,
        HistoriqueJournalier.vehicule_id == vbasc.id))
    db.commit()
    daily.archiver_jour(db, veille)
    arch = db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == veille,
        HistoriqueJournalier.vehicule_id == vbasc.id))
    check("archive « Hier » 100 % OFFICIEL — 0 % provisoire (§9/§C.6)",
          arch is not None and "PROVISOIRE" not in str(arch.donnees or {}),
          f"{bool(arch)}")

    # ================================================================
    print("\n[A · §5.1/§5.2/§7] 0616TCC — cible VERBATIM de la référence")
    lignes0616 = [
        ("05:31:03", "08:24:38", 84.318),
        ("08:26:10", "08:28:05", 0.058), ("08:30:12", "08:31:40", 0.044),
        ("08:33:00", "08:36:22", 0.111), ("08:38:05", "08:40:19", 0.038),
        ("09:48:34", "09:56:51", 1.442),
    ]
    items0616 = [{"plaque": v0616.plaque,
                  "debut": datetime.fromisoformat(f"{jour.isoformat()} {d}"),
                  "fin": datetime.fromisoformat(f"{jour.isoformat()} {f}"),
                  "distance_km": km, "source": "MZONEX"}
                 for d, f, km in lignes0616]
    stats = reconcilier_trajets_valides(db, items0616, username="test-v118",
                                        maintenant=h(10, 30))
    tr = visibles(v0616)
    check("« Affichage correct : 5:31 | 8:24 | Pause 1:24 | 9:48 | 9:56 » "
          "(§5.2, verbatim)",
          len(tr) == 2
          and tr[0].heure_debut == h(5, 31, 3) and tr[0].heure_fin == h(8, 24, 38)
          and tr[0].pause_apres_s == int((h(9, 48, 34) - h(8, 24, 38)).total_seconds())
          and tr[1].heure_debut == h(9, 48, 34) and tr[1].heure_fin == h(9, 56, 51),
          f"{[(t.heure_debut, t.heure_fin, t.pause_apres_s) for t in tr]}")
    check("4 manœuvres ≥30 min ignorées : 4 rejets comptés, AUCUNE ligne posée "
          "(§7 : IGNORER = ne pas créer de trajet)",
          stats["rejets"] == 4
          and len(visibles(v0616)) == 2
          and db.scalar(select(Trajet).join(SuiviJournalier).where(
              SuiviJournalier.vehicule_id == v0616.id,
              SuiviJournalier.date_jour == jour,
              Trajet.statut_validation == StatutValidationTrajet.REJETE)
              .limit(1)) is None,
          f"rejets={stats['rejets']}")
    s_json = s_suivi(engine.ensure_suivi(db, v0616, jour), engine.get_seuils(db))
    check("grille = export = archive : 2 trajets, départ 05:31:03, pause « 1:24 »",
          s_json["nb_trajets"] == 2
          and (s_json["heure_depart"] or "").endswith("05:31:03")
          and s_json["trajets"][0]["pause_apres_s"] == 5036,
          f"{s_json['nb_trajets']}")
    ttj, tcj = s_json["ttj_s"], s_json["tcj_s"]
    check("§1.3 : TTJ = TCJ + arrêts (ici « 1:24 » exactement — aucune manœuvre "
          "dans les compteurs, §2)",
          ttj - tcj == 5036, f"ttj={ttj} tcj={tcj}")

    # ================================================================
    print("\n[C · §11.2] CamtrackPro : règle « en mouvement » SUPPRIMÉE (distance seule)")
    items5716 = [{"plaque": v5716.plaque,
                  "debut": datetime.fromisoformat(f"{jour.isoformat()} {d}"),
                  "fin": datetime.fromisoformat(f"{jour.isoformat()} {f}"),
                  "distance_km": km, "duree_mouvement_s": mv,
                  "source": "CAMTRACKPRO"}
                 for d, f, km, mv in
                 [("10:26:22", "13:21:08", 86.86, 10176),
                  ("13:34:53", "13:46:40", 0.99, 602)]]
    stats = reconcilier_trajets_valides(db, items5716, username="test-v118",
                                        maintenant=h(14, 30))
    tr = visibles(v5716)
    # v3 AM-2 : plus de fusion — 2 lignes : T1 (86,86 km) + R4 (0,99 km,
    # VALIDE à part entière par sa distance seule — §11.2 v2 conservé)
    check("R4 (0,99 km ≥ 0,3 / « en mouvement » 10 min) VALIDE sur SA ligne — "
          "v3 AM-2 : T1 et R4 gardent chacun leur ligne",
          stats["rejets"] == 0 and len(tr) == 2
          and tr[0].heure_debut == h(10, 26, 22)
          and tr[0].heure_fin == h(13, 21, 8)
          and abs((tr[0].distance_km or 0) - 86.86) < 1e-6
          and tr[1].heure_debut == h(13, 34, 53)
          and tr[1].heure_fin == h(13, 46, 40)
          and abs((tr[1].distance_km or 0) - 0.99) < 1e-6
          and tr[1].statut_validation == StatutValidationTrajet.VALIDE,
          f"rejets={stats['rejets']} "
          f"{[(t.heure_debut, t.heure_fin, t.distance_km) for t in tr]}")
    check("aucun audit « trajet.rejet_mouvement » (règle abrogée)",
          db.scalar(select(AuditLog).where(
              AuditLog.action == "trajet.rejet_mouvement").limit(1)) is None)

    # ================================================================
    print("\n[D · §5.1/§12.1 + §0ter E] Vitesse d'arrêt = 3 km/h (14/08/2026)")
    seuils = engine.get_seuils(db)
    check("SEUIL_VITESSE_ARRET = 3 (ParamétrageSeuil, admin-éditable §12.2 — "
          "arbitrage §0ter E du 14/08/2026 : avant 5 km/h)",
          float(seuils["SEUIL_VITESSE_ARRET"]) == 3.0)
    v0616.last_event_at = now_local()
    v0616.last_vitesse = 2.9
    db.commit()
    from app.serializers import etat_roulage
    roule, _ = etat_roulage(db.get(Vehicule, v0616.id), now_local(), seuils)
    check("vitesse 2,9 km/h → ARRÊTÉ (≤ 3 km/h)", roule is False)
    v0616.last_vitesse = 3.1
    db.commit()
    roule, _ = etat_roulage(db.get(Vehicule, v0616.id), now_local(), seuils)
    check("vitesse 3,1 km/h → ROULE (> 3 km/h)", roule is True)
    # cas utilisateur du 14/08/2026 : embouteillages — 5 à 11 km/h = EN ROUTE
    for vcas in (5.0, 7.0, 11.0):
        v0616.last_vitesse = vcas
        db.commit()
        roule, _ = etat_roulage(db.get(Vehicule, v0616.id), now_local(), seuils)
        check(f"embouteillage {vcas:.0f} km/h → ROULE (§0ter E)", roule is True)
    v0616.last_vitesse = 0.0
    db.commit()

    # ================================================================
    print("\n[§12.1] Seuils constants inchangés (jamais durcis sans validation)")
    check("distance 0,3 km · pause 20 min · pause TCC 30 min",
          abs(float(seuils["SEUIL_DISTANCE_MIN_TRAJET_KM"]) - 0.3) < 1e-9
          and float(seuils["DUREE_MIN_PAUSE_VALIDE"]) == 1200.0
          and float(seuils["SEUIL_PAUSE_COUPURE_TCC"]) == 1800.0)
    check("TCC_MAX 4h30 · TCJ_MAX 10h · TTJ_MAX 12h",
          float(seuils["SEUIL_TCC_MAX"]) == 16200.0
          and float(seuils["SEUIL_TCJ_MAX"]) == 36000.0
          and float(seuils["SEUIL_TTJ_MAX"]) == 43200.0)
    check("SEUIL_DUREE_MIN_MOUVEMENT_TRAJET = 15 min dormant (§6.1, réservé "
          "flux de positions — inactif sur les trajets)",
          float(seuils["SEUIL_DUREE_MIN_MOUVEMENT_TRAJET"]) == 900.0)

    # ================================================================
    print("\n[A · §7] Manœuvre seule dans une pause : n'interrompt JAMAIS l'arrêt")
    segs = [
        Segment(debut=h(15, 0), fin=h(16, 0), distance_km=10.0, rejete=False),
        Segment(debut=h(16, 20), fin=h(16, 24), distance_km=0.1, rejete=True),
        Segment(debut=h(16, 50), fin=h(17, 30), distance_km=8.0, rejete=False),
    ]
    jn = construire_journee(segs, maintenant=h(18, 0))
    check("2 lignes affichées, pause RÉELLE 50 min (16:00 → 16:50, à travers "
          "la manœuvre)",
          len(jn.lignes) == 2 and jn.lignes[0].pause_apres_s == 3000,
          f"{[(l.debut, l.fin, l.pause_apres_s) for l in jn.lignes]}")
    check("v3 AM-1/AM-6 : arrêts comptables = 50 min BRUTS (la manœuvre de "
          "4 min compte comme arrêt) ; exclu_s maintenu à 0 (champ legacy)",
          jn.total_pause_s == 3000 and jn.exclu_s == 0,
          f"pause={jn.total_pause_s} exclu={jn.exclu_s}")

finally:
    for sid in nettoie_suivis:
        db.execute(delete(Trajet).where(Trajet.suivi_id == sid))
    db.execute(delete(AuditLog).where(AuditLog.username.like("test-v118%")))
    db.commit()
    db.close()
    for cand in ("/tmp/test_v118.db", "/tmp/test_reference_v118.db"):
        try:
            os.unlink(cand)
        except OSError:
            pass

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
