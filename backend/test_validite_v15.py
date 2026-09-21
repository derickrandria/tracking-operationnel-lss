"""Tests v1.5 — conditions de validité des trajets et des pauses.
Réaligné v3 (AMÉLIORATIONS — arbitrages §0nonies du 22/08/2026) : TCJ = tous
arrêts déduits (AM-1) ; TTJ = amplitude brute (T1) ; fusion d'affichage des
pauses < 20 min abolie (AM-2 : chaque trajet publié garde SA ligne).

RÈGLE ABSOLUE vérifiée : un trajet INVALIDE (< 0,3 km) n'entre JAMAIS dans
le calcul TCC/TCJ/TTJ, n'est jamais créé en base via la validation Niveau 2 —
et sa durée est traitée comme un ARRÊT (AM-6).

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v15.db" python3 test_validite_v15.py
La base est SUPPRIMÉE à la fin (protection des données production)."""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.config import now_local
from app.database import SessionLocal
from app import engine
from app.models import (AuditLog, StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, Vehicule)
from app.reconciliation import (normaliser_valides, reconcilier_trajets_valides)
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
          "test (ex. sqlite:////tmp/test_v15.db) — jamais la base de production.")
    sys.exit(2)

seed_si_vide()
migrer_schema()
db = SessionLocal()
maintenant = now_local().replace(microsecond=0)
jour = maintenant.date()

veh = db.scalar(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF"))
veh_ct = db.scalar(select(Vehicule).where(
    Vehicule.plateforme_gps == "CAMTRACKPRO", Vehicule.statut == "ACTIF"))
veh_ct2 = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "CAMTRACKPRO", Vehicule.statut == "ACTIF",
    Vehicule.id != veh_ct.id)).first()
print(f"Véhicules test : MZX={veh.plaque} · CT1={veh_ct.plaque} · CT2={veh_ct2.plaque}")

nettoie_suivis, nettoie_trajets, nettoie_audits = set(), [], []
def suivre_suivi(s):
    nettoie_suivis.add(s.id)
    return s

# La base de test est seedée avec une journée de démo : pour mesurer le moteur
# sur des suivis VIERGES, on vide d'abord les trajets d'aujourd'hui des
# véhicules de test (base de test uniquement — jamais en production).
for v in (veh, veh_ct, veh_ct2):
    s = engine.ensure_suivi(db, v, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
db.commit()

base = datetime.combine(jour, datetime.min.time())
fin_journee = base + timedelta(hours=23, minutes=59)
L0, G0 = -18.90, 47.50

try:
    # ================================================================
    print("\n[§0] Seuils v1.5 présents et corrects")
    seuils = engine.get_seuils(db)
    check("DUREE_MIN_PAUSE_VALIDE = 20 min (1200 s)",
          float(seuils["DUREE_MIN_PAUSE_VALIDE"]) == 1200.0,
          str(seuils["DUREE_MIN_PAUSE_VALIDE"]))
    check("SEUIL_DISTANCE_MIN_TRAJET_KM = 0,3 km",
          abs(float(seuils["SEUIL_DISTANCE_MIN_TRAJET_KM"]) - 0.3) < 1e-9)
    check("SEUIL_DUREE_MIN_MOUVEMENT_TRAJET = 15 min dormant (900 s — §6.1, "
          "réservé aux flux de positions ; inactif sur les trajets, §11.2)",
          float(seuils["SEUIL_DUREE_MIN_MOUVEMENT_TRAJET"]) == 900.0)
    check("SEUIL_VITESSE_ARRET = 3 km/h (Référence v2 §5.1/§12.1 + arbitrage "
          "§0ter E du 14/08/2026 : embouteillages 5-11 km/h = en route)",
          float(seuils["SEUIL_VITESSE_ARRET"]) == 3.0)

    # ================================================================
    print("\n[CA-TCC-1/2/3/4 + §5.2] Moteur : fusion pause < 20 min + rejet manœuvre")
    suivi = suivre_suivi(engine.ensure_suivi(db, veh, jour))
    ev = lambda h, mi, vitesse, lat=None: engine.ingest_event(
        db, veh, base.replace(hour=h, minute=mi), L0 if lat is None else lat, G0,
        "Test", vitesse, "ON", publier=False)
    ev(8, 0, 50.0)                       # départ T1
    ev(8, 20, 50.0, L0 + 0.02)           # +2,2 km
    ev(8, 40, 50.0, L0 + 0.04)           # +2,2 km
    ev(9, 0, 0.0)                        # fermeture (T1 : 4,4 km)
    ev(9, 15, 50.0, L0 + 0.06)           # pause 15 min < 20 → FUSION (§5.2)
    ev(9, 35, 50.0, L0 + 0.08)           # +2,2 km (même trajet T1)
    ev(10, 0, 0.0)                       # fermeture T1 (≈ 8,9 km cumulés)
    ev(10, 30, 50.0)                     # pause 30 min ≥ 20 → T1 finalisé ; T2 s'ouvre
    ev(10, 40, 0.0)                      # T2 = manœuvre 0 km → fermé
    ev(11, 1, 50.0)                      # pause 21 min ≥ 20 → T2 finalisé → REJETE
    ev(11, 21, 50.0, L0 + 0.11)          # T3 : +3,3 km
    ev(11, 41, 0.0)                      # fermeture T3
    db.commit()

    sudo = db.get(SuiviJournalier, suivi.id)
    db.expire(sudo)
    tous = db.scalars(select(Trajet).where(
        Trajet.suivi_id == suivi.id).order_by(Trajet.numero)).all()
    nettoie_trajets += [t.id for t in tous]
    check("fusion §5.2 : 3 trajets seulement (pas de rupture à 15 min)",
          len(tous) == 3, f"total={len(tous)}")
    t1, t2, t3 = tous
    check("T1 = un seul trajet continu 08:00 → 10:00",
          t1.heure_debut == base.replace(hour=8) and
          t1.heure_fin == base.replace(hour=10))
    check("T1 finalisé VALIDE (8,9 km ≥ 0,3)",
          t1.statut_validation == StatutValidationTrajet.VALIDE,
          str(t1.statut_validation))
    check("T2 (manœuvre 0 km) finalisé REJETE",
          t2.statut_validation == StatutValidationTrajet.REJETE,
          str(t2.statut_validation))
    aud = db.scalars(select(AuditLog).where(
        AuditLog.action == "trajet.rejet_distance",
        AuditLog.entite_id == t2.id)).all()
    nettoie_audits += [a.id for a in aud]
    check("audit trajet.rejet_distance écrit pour T2", len(aud) == 1, str(len(aud)))

    # --- RÈGLE ABSOLUE (CA-TCC-1 : durée exacte attendue)
    engine.recalculer_temps(db, sudo, base.replace(hour=11, minute=41))
    check("TCC = durée de T3 seule (2400 s) — manœuvre exclue",
          sudo.tcc_s == 2400, f"tcc={sudo.tcc_s}")
    check("TCJ = 9600 s (12660 − 3060 arrêts nets — la manœuvre 600 s "
          "interne à la pause n'est pas déduite deux fois, §1.3)",
          sudo.tcj_s == 9600, f"tcj={sudo.tcj_s}")
    # v3 : T1 — TTJ = amplitude BRUTE (plus aucune exclusion) =
    # 08:00→11:41 = 3h41 = 13260 s ; les arrêts comptés = TTJ − TCJ,
    # manœuvre interne de 10 min COMPTE comme arrêt (AM-6)
    check("TTJ = 13260 s (amplitude BRUTE — v3 T1 : plus rien d'exclu)",
          sudo.ttj_s == 13260, f"ttj={sudo.ttj_s}")
    check("arrêts comptés = 3660 s (61 min d'écart réel + manœuvre interne "
          "de 10 min, comptée comme arrêt — v3 AM-1/AM-6)",
          sudo.total_pause_s == 3660, f"pause={sudo.total_pause_s}")
    check("heure_départ = 08:00 (pas celle de la manœuvre)",
          sudo.heure_depart == base.replace(hour=8))
    # la manœuvre REJETÉE n'apparaît pas dans la grille serialisée
    s_json = s_suivi(sudo, seuils)
    check("grille : T2 REJETÉ masqué (2 trajets affichés sur 3)",
          s_json["nb_trajets"] == 2 and len(s_json["trajets"]) == 2, str(s_json["nb_trajets"]))

    # ================================================================
    print("\n[CA-2] MZoneX validé 0 km ↔ provisoire → provisoire marqué REJETE")
    suivi_ct1 = suivre_suivi(engine.ensure_suivi(db, veh_ct, jour))
    prov = Trajet(suivi_id=suivi_ct1.id, numero=1,
                  heure_debut=base.replace(hour=7), heure_fin=base.replace(hour=7, minute=10),
                  statut_source=StatutSourceTrajet.PROVISOIRE,
                  source_plateforme="MZONEX")
    db.add(prov); db.commit(); nettoie_trajets.append(prov.id)
    stats = reconcilier_trajets_valides(db, [{
        "plaque": veh_ct.plaque, "debut": base.replace(hour=7),
        "fin": base.replace(hour=7, minute=10), "distance_km": 0.0,
        "source": "MZONEX"}], username="test-v15", maintenant=fin_journee)
    db.expire_all()
    p2 = db.get(Trajet, prov.id)
    check("1 rejet compté", stats["rejets"] == 1, str(stats))
    check("aucun trajet créé ni remplacé", stats["crees"] == 0 and stats["remplaces"] == 0)
    check("provisoire marqué REJETE (CA-2)",
          p2.statut_validation == StatutValidationTrajet.REJETE, str(p2.statut_validation))
    aud = db.scalars(select(AuditLog).where(
        AuditLog.action == "trajet.rejet_distance",
        AuditLog.entite_id == prov.id)).all()
    nettoie_audits += [a.id for a in aud]
    check("audit rejet rattaché au provisoire", len(aud) == 1, str(len(aud)))
    engine.recalculer_temps(db, suivi_ct1, base.replace(hour=8))
    db.refresh(suivi_ct1)
    check("TCJ/TCC = 0 malgré la présence du rejeté (règle absolue)",
          suivi_ct1.tcc_s == 0 and suivi_ct1.tcj_s == 0,
          f"tcc={suivi_ct1.tcc_s} tcj={suivi_ct1.tcj_s}")

    # ================================================================
    print("\n[CA-1 + §11.2 v2] CamtrackPro : manœuvre IGNORÉE sans rien poser ; "
          "règle « en mouvement » SUPPRIMÉE (arbitrage métier du 06/08)")
    nb_avant = db.query(Trajet).filter_by(suivi_id=suivi_ct1.id).count()
    stats = reconcilier_trajets_valides(db, [{
        "plaque": veh_ct.plaque, "debut": base.replace(hour=12),
        "fin": base.replace(hour=12, minute=9), "distance_km": 0.0,
        "duree_mouvement_s": 540, "source": "CAMTRACKPRO"}], username="test-v15", maintenant=fin_journee)
    nb_apres = db.query(Trajet).filter_by(suivi_id=suivi_ct1.id).count()
    check("CA-1 : aucune LIGNE valide créée (crees=0)", stats["crees"] == 0, str(stats))
    check("CA-1 : AUCUN enregistrement posé (Référence v2 §7 : IGNORER = "
          "ne pas créer de trajet)",
          nb_apres == nb_avant, f"{nb_avant}→{nb_apres}")
    check("rejet compté (manœuvre < 0,3 km)", stats["rejets"] == 1, str(stats))
    aud = db.scalars(select(AuditLog).where(
        AuditLog.action == "trajet.rejet_distance")).all()
    nettoie_audits += [a.id for a in aud]
    check("audit trajet.rejet_distance écrit pour la manœuvre CamtrackPro",
          any(a.details and a.details.get("debut", "").endswith("T12:00:00")
              for a in aud), str(len(aud)))
    stats = reconcilier_trajets_valides(db, [{
        "plaque": veh_ct.plaque, "debut": base.replace(hour=13),
        "fin": base.replace(hour=14, minute=10), "distance_km": 5.0,
        "duree_mouvement_s": 600, "source": "CAMTRACKPRO"}], username="test-v15", maintenant=fin_journee)
    db.expire_all()
    seg2 = db.scalars(select(Trajet).where(
        Trajet.suivi_id == suivi_ct1.id).order_by(
            Trajet.heure_debut.desc())).all()[0]
    check("§11.2 v2 : distance OK (5 km) → VALIDÉ même si « en mouvement » "
          "< 15 min (règle supprimée, arbitrage C du 06/08)",
          stats["rejets"] == 0 and stats["crees"] == 1
          and seg2.statut_validation == StatutValidationTrajet.VALIDE
          and seg2.statut_source == StatutSourceTrajet.VALIDE
          and seg2.heure_debut == base.replace(hour=13)
          and seg2.heure_fin == base.replace(hour=14, minute=10),
          str(stats))
    aud = db.scalars(select(AuditLog).where(
        AuditLog.action == "trajet.rejet_mouvement")).all()
    nettoie_audits += [a.id for a in aud]
    check("AUCUN audit trajet.rejet_mouvement (règle supprimée)",
          len(aud) == 0, str(len(aud)))

    # ================================================================
    print("\n[CA-3/CA-4/CA-5/CA-7] Fusion validés : 15 min fusionnés / 25 min gardées")
    suivi_ct2 = suivre_suivi(engine.ensure_suivi(db, veh_ct2, jour))
    stats = reconcilier_trajets_valides(db, [
        {"plaque": veh_ct2.plaque, "debut": base.replace(hour=8),
         "fin": base.replace(hour=9, minute=30), "distance_km": 45.0, "source": "CAMTRACKPRO"},
        {"plaque": veh_ct2.plaque, "debut": base.replace(hour=9, minute=45),   # +15 min
         "fin": base.replace(hour=11), "distance_km": 38.0, "source": "CAMTRACKPRO"},
        {"plaque": veh_ct2.plaque, "debut": base.replace(hour=13),
         "fin": base.replace(hour=14), "distance_km": 40.0, "source": "CAMTRACKPRO"},
        {"plaque": veh_ct2.plaque, "debut": base.replace(hour=14, minute=25),  # +25 min
         "fin": base.replace(hour=16), "distance_km": 50.0, "source": "CAMTRACKPRO"},
    ], username="test-v15", maintenant=fin_journee)
    tous2 = db.scalars(select(Trajet).where(
        Trajet.suivi_id == suivi_ct2.id).order_by(Trajet.numero)).all()
    nettoie_trajets += [t.id for t in tous2]
    fa, fb = (tous2 + [None, None])[:2]
    check("CA-3 v3 : la pause de 15 min ne fusionne PLUS — 2 lignes : "
          "08:00→09:30 et 09:45→11:00 (AM-2)",
          fa is not None and fa.heure_debut == base.replace(hour=8)
          and fa.heure_fin == base.replace(hour=9, minute=30)
          and fb is not None and fb.heure_debut == base.replace(hour=9, minute=45)
          and fb.heure_fin == base.replace(hour=11),
          f"{fa and fa.heure_debut}→{fa and fa.heure_fin} / "
          f"{fb and fb.heure_debut}→{fb and fb.heure_fin}")
    check("distances NON cumulées : 45 km et 38 km portées chacune par SA "
          "ligne (v3 AM-2)",
          fa is not None and abs((fa.distance_km or 0) - 45.0) < 1e-6
          and fb is not None and abs((fb.distance_km or 0) - 38.0) < 1e-6,
          str(fa and fa.distance_km))
    check("CA-4 v3 : au total 4 trajets (4 lignes publiées, aucune fusion)",
          len(tous2) == 4, f"total={len(tous2)}")
    check("CA-7 : tous VALIDÉ statut_source ET statut_validation",
          all(t.statut_source == StatutSourceTrajet.VALIDE and
              t.statut_validation == StatutValidationTrajet.VALIDE for t in tous2))
    stats2 = reconcilier_trajets_valides(db, [
        {"plaque": veh_ct2.plaque, "debut": base.replace(hour=8),
         "fin": base.replace(hour=9, minute=30), "distance_km": 45.0,
         "source": "CAMTRACKPRO"},
        {"plaque": veh_ct2.plaque, "debut": base.replace(hour=9, minute=45),
         "fin": base.replace(hour=11), "distance_km": 38.0,
         "source": "CAMTRACKPRO"},
        {"plaque": veh_ct2.plaque, "debut": base.replace(hour=13),
         "fin": base.replace(hour=14), "distance_km": 40.0,
         "source": "CAMTRACKPRO"},
        {"plaque": veh_ct2.plaque, "debut": base.replace(hour=14, minute=25),
         "fin": base.replace(hour=16), "distance_km": 50.0,
         "source": "CAMTRACKPRO"}],
        username="test-v15", maintenant=fin_journee)
    check("CA-5 : rejeu idempotent (aucun doublon, 4 lignes stables)",
          stats2["crees"] == 0 and stats2["remplaces"] == 0 and
          db.query(Trajet).filter_by(suivi_id=suivi_ct2.id).count() == 4)

    # ================================================================
    print("\n[CA-6] Trajet MZoneX EN COURS (sans fin) reste PROVISOIRE")
    suivi_ct1b = db.get(SuiviJournalier, suivi_ct1.id)
    ouvert = Trajet(suivi_id=suivi_ct1.id, numero=99,
                    heure_debut=maintenant - timedelta(minutes=30), heure_fin=None,
                    statut_source=StatutSourceTrajet.PROVISOIRE,
                    source_plateforme="MZONEX")
    db.add(ouvert); db.commit(); nettoie_trajets.append(ouvert.id)
    # v1.54 : les DEUX fenêtres dérivent de `maintenant` et sont disjointes par
    # construction (fin officielle 110 min avant maintenant, trajet en cours 30 min
    # avant maintenant → écart 80 min > seuil de 30 min). Le décor ne peut donc plus
    # recouvrir — ni jouxter — la ligne ouverte quelle que soit l'heure réelle
    # d'exécution : avant, la fenêtre officielle figée (15:00→15:40) absorbait le
    # trajet en cours créé à `maintenant − 30 min` (KO entre 15:30 et 16:10).
    # Valeurs attendues INCHANGÉES (PROVISOIRE + EN_ATTENTE) : c'est le décor qui
    # mesurait l'horloge, pas la règle.
    stats = reconcilier_trajets_valides(db, [{
        "plaque": veh_ct.plaque, "debut": maintenant - timedelta(minutes=150),
        "fin": maintenant - timedelta(minutes=110), "distance_km": 12.0,
        "source": "CAMTRACKPRO"}], username="test-v15", maintenant=fin_journee)
    db.expire_all()
    o2 = db.get(Trajet, ouvert.id)
    check("trajet en cours toujours PROVISOIRE (pas de validation prématurée)",
          o2.statut_source == StatutSourceTrajet.PROVISOIRE)
    check("trajet en cours toujours EN_ATTENTE (côté validité v1.5)",
          o2.statut_validation == StatutValidationTrajet.EN_ATTENTE,
          str(o2.statut_validation))

finally:
    # ---------------- nettoyage ----------------
    db.rollback()
    for tid in nettoie_trajets:
        db.execute(delete(Trajet).where(Trajet.id == tid))
    for aid in set(nettoie_audits):
        db.execute(delete(AuditLog).where(AuditLog.id == aid))
    for sid in nettoie_suivis:
        db.execute(delete(Trajet).where(Trajet.suivi_id == sid))
        db.execute(delete(SuiviJournalier).where(SuiviJournalier.id == sid))
    db.commit()
    db.close()

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
