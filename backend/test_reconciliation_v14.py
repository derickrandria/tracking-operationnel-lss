"""Tests Addendum v1.4 — stratégie hybride MZoneX (critères 16 à 21 + idempotence).
Exécution : python3 test_reconciliation_v14.py   (depuis backend/, SIM_ENABLE=0)
Nettoie après lui toutes les données de test créées."""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.config import now_local
from app.database import SessionLocal
from app import engine
from app.models import (Alerte, AuditLog, HistoriqueJournalier,
                        StatutSourceTrajet, SuiviJournalier, Trajet, Vehicule)
from app.reconciliation import (normaliser_valides, reconcilier_trajets_valides,
                                _synchroniser_archive)
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

seed_si_vide()
migrer_schema()
db = SessionLocal()
maintenant = now_local().replace(microsecond=0)
jour = maintenant.date()
hier = jour - timedelta(days=1)

veh = db.scalar(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF"))
veh_ct = db.scalar(select(Vehicule).where(
    Vehicule.plateforme_gps == "CAMTRACKPRO", Vehicule.statut == "ACTIF"))
print(f"Véhicule test MZONEX : {veh.plaque} · CAMTRACKPRO : {veh_ct.plaque}")

suivi_test = dict(veh_id=veh.id, jour=jour)
nettoie_trajets, nettoie_audits, nettoie_archives = [], [], []

try:
    # ---------------------------------------------------------------- setup
    suivi = engine.ensure_suivi(db, veh, jour)
    base = datetime.combine(jour, datetime.min.time()).replace(hour=6)
    fin_journee = base.replace(hour=23, minute=59)
    t1 = Trajet(suivi_id=suivi.id, numero=1, heure_debut=base,
                heure_fin=base + timedelta(minutes=50), pause_apres_s=1800,
                statut_source=StatutSourceTrajet.PROVISOIRE, source_plateforme="MZONEX")
    t2 = Trajet(suivi_id=suivi.id, numero=2, heure_debut=base + timedelta(minutes=80),
                heure_fin=base + timedelta(minutes=140),
                statut_source=StatutSourceTrajet.PROVISOIRE, source_plateforme="MZONEX")
    db.add_all([t1, t2]); db.commit()
    nettoie_trajets += [t1.id, t2.id]
    nb_avant = db.scalar(select(type(t1)).where(Trajet.suivi_id == suivi.id).with_only_columns(Trajet.id))
    n_avant = len(nettoie_trajets)

    print("\n[16-17] Rapprochement ±tolérance → remplacement sans doublon + VALIDÉ")
    stats = reconcilier_trajets_valides(db, [{
        "vehicule_id": veh.id, "debut": base + timedelta(seconds=45),       # +45 s (< 2 min)
        "fin": base + timedelta(minutes=50, seconds=30), "distance_km": 38.4,
        "source": "MZONEX"}], username="test", maintenant=fin_journee)
    t1b = db.get(Trajet, t1.id)
    check("1 trajet remplacé", stats["remplaces"] == 1, str(stats))
    check("aucun trajet créé", stats["crees"] == 0)
    check("statut passé à VALIDÉ", t1b.statut_source == StatutSourceTrajet.VALIDE)
    check("heure_debut remplacée par la valeur officielle",
          t1b.heure_debut == base + timedelta(seconds=45))
    check("distance officielle enregistrée", abs((t1b.distance_km or 0) - 38.4) < 1e-6)
    total = db.query(Trajet).filter_by(suivi_id=suivi.id).count()
    check("pas de doublon (2 trajets)", total == 2, f"total={total}")

    print("\n[19] Recalcul automatique TCC/TCJ/TTJ après validation")
    db.refresh(suivi)
    check("heure_depart du suivi recalée", suivi.heure_depart == base + timedelta(seconds=45))

    print("\n[21] Divergence > 10 min : auditée, remplacement maintenu")
    stats = reconcilier_trajets_valides(db, [{
        "vehicule_id": veh.id, "debut": base + timedelta(minutes=80, seconds=30),
        "fin": base + timedelta(minutes=140) + timedelta(minutes=12),        # +12 min > 10
        "distance_km": 51.0, "source": "MZONEX"}], username="test", maintenant=fin_journee)
    check("remplacement effectué malgré l'écart", stats["remplaces"] == 1, str(stats))
    div = db.scalar(select(AuditLog).where(AuditLog.action == "trajet.divergence"))
    check("divergence journalisée (audit)", div is not None)
    t2b = db.get(Trajet, t2.id)
    check("VALIDÉ tout de même (priorité au validé)",
          t2b.statut_source == StatutSourceTrajet.VALIDE)

    print("\n[17] Idempotence : même validation rejouée → ignorée, pas de doublon")
    stats = reconcilier_trajets_valides(db, [{
        "vehicule_id": veh.id, "debut": base + timedelta(seconds=50),
        "fin": base + timedelta(minutes=50), "source": "MZONEX"}], username="test", maintenant=fin_journee)
    check("aucune création", stats["crees"] == 0 and stats["remplaces"] == 0, str(stats))
    total = db.query(Trajet).filter_by(suivi_id=suivi.id).count()
    check("toujours 2 trajets", total == 2, f"total={total}")

    print("\n[§2.4 sinon] Trajet validé SANS équivalent provisoire → création + anomalie")
    stats = reconcilier_trajets_valides(db, [{
        "vehicule_id": veh.id, "debut": base + timedelta(hours=6),
        "fin": base + timedelta(hours=6, minutes=40), "distance_km": 29.9,
        "source": "MZONEX"}], username="test", maintenant=fin_journee)
    check("créé", stats["crees"] == 1, str(stats))
    an = db.scalar(select(AuditLog).where(AuditLog.action == "trajet.valide_sans_provisoire"))
    check("anomalie journalisée", an is not None)
    t_new = db.scalar(select(Trajet).where(Trajet.suivi_id == suivi.id,
                                           Trajet.statut_source == StatutSourceTrajet.VALIDE,
                                           Trajet.numero == 3))
    check("trajet créé VALIDÉ numéroté 3", t_new is not None)
    if t_new:
        nettoie_trajets.append(t_new.id)

    print("\n[16/§5] Statut à la création par ingest_event (camtrack direct VALIDÉ)")
    r1 = engine.ingest_event(db, veh_ct, maintenant - timedelta(minutes=3),
                             -18.90, 47.52, "Test CT", 42.0, "ON",
                             source=__import__("app.models", fromlist=["SourceEvenement"]).SourceEvenement.SIMULATEUR,
                             publier=False)
    s_ct = engine.ensure_suivi(db, veh_ct, jour)
    tct = db.scalar(select(Trajet).where(Trajet.suivi_id == s_ct.id).order_by(Trajet.numero.desc()))
    check("trajet CamtrackPro créé directement VALIDÉ",
          tct is not None and tct.statut_source == StatutSourceTrajet.VALIDE)
    if tct: nettoie_trajets.append(tct.id)
    # v1.5 : pour créer un NOUVEAU trajet, l'événement doit suivre d'au moins
    # 20 min la fin du précédent (sinon : pause < 20 min = fusion, §1.2)
    db.refresh(suivi)
    fins = [t.heure_fin for t in suivi.trajets if t.heure_fin]
    fin_derniere = max(fins) if fins else (maintenant - timedelta(hours=1))
    ts_nouveau = max(maintenant - timedelta(minutes=2),
                     fin_derniere + timedelta(minutes=25))
    r2 = engine.ingest_event(db, veh, ts_nouveau,
                             -18.91, 47.53, "Test MZX", 38.0, "ON",
                             source=__import__("app.models", fromlist=["SourceEvenement"]).SourceEvenement.SIMULATEUR,
                             publier=False)
    db.refresh(suivi)
    dernier = sorted(suivi.trajets, key=lambda t: t.numero)[-1]
    check("trajet MZoneX créé PROVISOIRE", dernier.statut_source == StatutSourceTrajet.PROVISOIRE)
    nettoie_trajets.append(dernier.id)

    print("\n[20] Trajet à cheval sur minuit : fenêtre de réconciliation sur la veille archivée")
    # le seed a peut-être déjà archivé ce véhicule pour hier → on retire
    # l'archive existante pour tester un cas isolé
    db.execute(delete(HistoriqueJournalier).where(
        HistoriqueJournalier.vehicule_id == veh.id,
        HistoriqueJournalier.date_jour == hier))
    db.execute(delete(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == veh.id, SuiviJournalier.date_jour == hier))
    db.commit()
    suivi_h = SuiviJournalier(date_jour=hier, vehicule_id=veh.id,
                              conducteur_id=veh.conducteur_actuel_id)
    db.add(suivi_h); db.flush(); db.refresh(suivi_h, attribute_names=["trajets"])
    debut_h = datetime.combine(hier, datetime.min.time()).replace(hour=23, minute=30)
    th = Trajet(suivi_id=suivi_h.id, numero=1, heure_debut=debut_h,
                heure_fin=debut_h + timedelta(minutes=45),
                statut_source=StatutSourceTrajet.PROVISOIRE, source_plateforme="MZONEX")
    db.add(th); db.commit()
    engine.recalculer_temps(db, suivi_h, debut_h + timedelta(minutes=45)); db.commit()
    arch = HistoriqueJournalier(date_jour=hier, annee=hier.year, mois=hier.month,
                                vehicule_id=veh.id, conducteur_id=veh.conducteur_actuel_id,
                                donnees=s_suivi(suivi_h), nb_infractions=0, nb_alertes=0)
    db.add(arch); db.commit()
    nettoie_archives.append(arch.id); nettoie_trajets.append(th.id)

    # RÉALIGNEMENT v1.43 (déclaré, §0nonies decies M1 du 29/08/2026) : J-1 →
    # J-7 sont TOUJOURS réconciliables (relecture des jours passés) — la
    # règle historique « J-1 + 2 h » est devenue un cas particulier englobé
    stats = reconcilier_trajets_valides(db, [{
        "vehicule_id": veh.id, "debut": debut_h + timedelta(seconds=30),
        "fin": debut_h + timedelta(minutes=47), "source": "MZONEX"}], username="test", maintenant=fin_journee)
    check("J-1 toujours réconciliable (M1) → remplacé",
          stats["remplaces"] == 1, str(stats))

    # fenêtre élargie (test) → validé + archive JSON resynchronisée
    seuil = db.scalar(select(__import__("app.models", fromlist=["ParametrageSeuil"]).ParametrageSeuil)
                      .where(__import__("app.models", fromlist=["ParametrageSeuil"]).ParametrageSeuil.cle == "FENETRE_RECONCILIATION_APRES_MINUIT"))
    vieille_valeur = seuil.valeur
    seuil.valeur = 48 * 3600   # test : 48 h
    db.commit(); engine.invalider_cache_seuils()
    stats = reconcilier_trajets_valides(db, [{
        "vehicule_id": veh.id, "debut": debut_h + timedelta(seconds=30),
        "fin": debut_h + timedelta(minutes=47), "distance_km": 22.5,
        "source": "MZONEX"}], username="test", maintenant=fin_journee)
    check("2ᵉ relecture idempotente (M1) → aucune nouvelle ligne, aucune mutation",
          stats["crees"] == 0 and stats["remplaces"] == 0
          and stats["ignores"] >= 1, str(stats))
    db.refresh(arch)
    tj = (arch.donnees or {}).get("trajets") or []
    check("archive JSON resynchronisée (VALIDÉ)",
          tj and tj[0].get("statut_source") == "VALIDÉ",
          str(tj[:1]))
    check("heure debut archivée = valeur officielle",
          tj and tj[0].get("heure_debut", "").startswith(debut_h.isoformat()[:14]))
    seuil.valeur = vieille_valeur
    db.commit(); engine.invalider_cache_seuils()

    print("\n[§2.5] Nouveaux paramètres exposés (éditables dans Paramètres)")
    seuils = engine.get_seuils(db)
    for cle in ("FREQUENCE_SYNC_TRAJETS_VALIDES", "SEUIL_TOLERANCE_RAPPROCHEMENT_TRAJET",
                "SEUIL_DIVERGENCE_TRAJET", "FENETRE_RECONCILIATION_APRES_MINUIT"):
        check(cle, cle in seuils)

    print("\n[simulateur] Planification + validation retardée Niveau 2 (démo)")
    from app.simulator import (VALIDATIONS_EN_ATTENTE, planifier_validation_simulee,
                               traiter_validations_echues)
    t_sim = Trajet(suivi_id=suivi.id, numero=99, heure_debut=base + timedelta(hours=10),
                   heure_fin=base + timedelta(hours=10, minutes=40),
                   statut_source=StatutSourceTrajet.PROVISOIRE, source_plateforme="MZONEX")
    db.add(t_sim); db.commit()
    nettoie_trajets.append(t_sim.id)
    planifier_validation_simulee(db, veh, suivi, t_sim, base + timedelta(hours=10, minutes=40))
    check("validation planifiée", any(v["trajet_id"] == t_sim.id for v in VALIDATIONS_EN_ATTENTE))
    # horaire ancré à 18:00 (≫ 16:40 + 20 min de pause) : quel que soit
    # l'instant où la suite tourne, la clôture est due et jugée — v1.11
    n_val = traiter_validations_echues(db, base + timedelta(hours=12))
    db.refresh(t_sim)
    check("validation appliquée à l'échéance", n_val == 1 and
          t_sim.statut_source == StatutSourceTrajet.VALIDE, f"n_val={n_val}")
finally:
    # ------------------------------------------------------------- nettoyage
    for tid in nettoie_trajets:
        db.execute(delete(Trajet).where(Trajet.id == tid))
    for aid in nettoie_archives:
        db.execute(delete(HistoriqueJournalier).where(HistoriqueJournalier.id == aid))
    db.execute(delete(AuditLog).where(AuditLog.username.in_(["test", "simulateur-niveau2"])))
    db.execute(delete(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == veh.id, SuiviJournalier.date_jour == hier))
    db.commit()
    engine.recalculer_temps(db, suivi, maintenant); db.commit()
    db.close()

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
