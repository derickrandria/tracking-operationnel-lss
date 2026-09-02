"""Tests v1.20 — ARBITRAGES LSS §0quater du 14/08/2026 (« trajet en cours »).

  R1 · ligne « en cours » VIVANTE (sans fin) JAMAIS rejetée pour une manœuvre
       officielle au même début — verdict rendu à la clôture (§5.1) ; la
       garde anti-géants reste pleine sur les lignes CLÔTURÉES.
  R2 · rattrapage d'ouverture à chaque cycle : camion en roulage récent sans
       ligne « en cours » → (ré)ouverture datée du vrai DÉMARRAGE.
  R3 · TCC provisoire toujours calculé depuis la ligne en cours (ici : après
       (ré)ouverture R2, recalcul immédiat).
  O1 · pré-alerte « pause non prise » à 4h00 (SEUIL_TCC_PREALERTE),
       paramétrable §12.2 (avant : 85 % du seuil = 3h49:30).

⏱️ Tous les instants sont SYNTHÉTIQUES et passés en paramètre (`maintenant=`)
   — aucune dépendance à l'horloge réelle, jour et nuit.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v120.db" python3 test_zeroquater_v120.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.config import jour_attribution, now_local
from app.database import SessionLocal
from app import engine
from app.models import (Alerte, AuditLog, EvenementGPS,
                        StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, TypeAlerte, TypeEvenement,
                        Vehicule)
from app.reconciliation import reconcilier_trajets_valides
from app.seed import seed_si_vide
from app.main import migrer_schema

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

jour = jour_attribution(now_local())
base = datetime.combine(jour, datetime.min.time())


def h(hh, mm, ss=0):
    return base.replace(hour=hh, minute=mm, second=ss)


MAINTENANT = h(11, 0)          # instant synthétique de toute la suite

mz = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF").order_by(
        Vehicule.plaque)).all()
ctp = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "CAMTRACKPRO", Vehicule.statut == "ACTIF").order_by(
        Vehicule.plaque)).all()
vR1, vR2a, vR2b, vR2c, vR2d = mz[0], mz[1], mz[2], mz[3], mz[4]
vCTP = ctp[0]
print(f"Véhicules : R1={vR1.plaque} R2a={vR2a.plaque} R2b={vR2b.plaque} "
      f"R2c={vR2c.plaque} R2d={vR2d.plaque} CTP={vCTP.plaque} jour={jour}")

TOUS = [vR1, vR2a, vR2b, vR2c, vR2d, vCTP]
for v in TOUS:
    s = engine.ensure_suivi(db, v, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
    db.execute(delete(EvenementGPS).where(EvenementGPS.vehicule_id == v.id))
db.execute(delete(Alerte).where(Alerte.vehicule_id.in_([v.id for v in TOUS])))
db.execute(delete(AuditLog).where(AuditLog.action.like("trajet.%")))
db.commit()


def ligne_en_cours(v):
    s = engine.ensure_suivi(db, v, jour)
    db.expire_all()
    return [t for t in db.scalars(select(Trajet).where(
        Trajet.suivi_id == s.id).order_by(Trajet.numero)).all()
        if t.heure_fin is None
        and t.statut_validation != StatutValidationTrajet.REJETE]


def audits(action, entite_id):
    return db.scalars(select(AuditLog).where(
        AuditLog.action == action,
        AuditLog.entite_id == entite_id)).all()


def rendre_roulant(v, vitesse=40.0, quand=None):
    v.last_event_at = quand or (MAINTENANT - timedelta(seconds=90))
    v.last_vitesse = vitesse
    db.commit()


def rendre_arrete(v):
    v.last_event_at = MAINTENANT - timedelta(seconds=90)
    v.last_vitesse = 0.0
    db.commit()


def nouveau_ouvert(v, debut, statut_val=StatutValidationTrajet.EN_ATTENTE):
    s = engine.ensure_suivi(db, v, jour)
    t = Trajet(suivi_id=s.id, numero=1, heure_debut=debut, heure_fin=None,
               statut_source=StatutSourceTrajet.PROVISOIRE,
               statut_validation=statut_val, source_plateforme="MZONEX")
    db.add(t)
    db.commit()
    return t


try:
    # ================================================================
    print("\n[R1 · §0quater] Véto : ligne « en cours » jamais tuée par une manœuvre")
    t_ouvert = nouveau_ouvert(vR1, h(7, 0))          # manœuvre 07:00 devenue vraie route
    manoeuvre = {"vehicule_id": vR1.id, "gps_associe": vR1.gps_associe,
                 "plaque": vR1.plaque, "debut": h(7, 0), "fin": h(7, 2),
                 "distance_km": 0.1, "source": "MZONEX"}
    stats = reconcilier_trajets_valides(db, [manoeuvre], maintenant=MAINTENANT)
    db.expire_all()
    t_apres = db.get(Trajet, t_ouvert.id)
    check("ligne OUVERTE au même début qu'une manœuvre → CONSERVÉE (R1)",
          t_apres.statut_validation != StatutValidationTrajet.REJETE,
          f"statut={t_apres.statut_validation}")
    check("audit « trajet.veto_rejet_en_cours » tracé (§3.2)",
          len(audits("trajet.veto_rejet_en_cours", t_ouvert.id)) == 1)

    # garde anti-géants intacte sur ligne CLÔTURÉE au même début
    vR1b = mz[5]
    s = engine.ensure_suivi(db, vR1b, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
    db.execute(delete(AuditLog).where(AuditLog.action == "trajet.rejet_distance"))
    t_clos = Trajet(suivi_id=s.id, numero=1, heure_debut=h(7, 0),
                    heure_fin=h(7, 2), distance_km=0.1,
                    statut_source=StatutSourceTrajet.PROVISOIRE,
                    statut_validation=StatutValidationTrajet.EN_ATTENTE,
                    source_plateforme="MZONEX")
    db.add(t_clos)
    db.commit()
    reconcilier_trajets_valides(
        db, [{"vehicule_id": vR1b.id, "gps_associe": vR1b.gps_associe,
              "plaque": vR1b.plaque, "debut": h(7, 0), "fin": h(7, 2),
              "distance_km": 0.1, "source": "MZONEX"}], maintenant=MAINTENANT)
    db.expire_all()
    t_apres = db.get(Trajet, t_clos.id)
    check("ligne CLÔTURÉE au même début qu'une manœuvre → REJETÉE (garde intacte)",
          t_apres.statut_validation == StatutValidationTrajet.REJETE,
          f"statut={t_apres.statut_validation}")

    # ================================================================
    print("\n[R2 · §0quater] Rattrapage — réouverture d'une ligne tuée en vol")
    victime = nouveau_ouvert(vR2a, h(8, 15),
                             statut_val=StatutValidationTrajet.REJETE)
    rendre_roulant(vR2a)
    st = engine.rattraper_ouvertures(maintenant=MAINTENANT)
    db.expire_all()
    t_apres = db.get(Trajet, victime.id)
    check("ligne rejetée-ouverte → réouverte EN_ATTENTE",
          t_apres.statut_validation == StatutValidationTrajet.EN_ATTENTE,
          f"statut={t_apres.statut_validation}")
    check("début d'origine CONSERVÉ (TCC exact) — 08:15",
          t_apres.heure_debut == h(8, 15), f"debut={t_apres.heure_debut}")
    check("toujours « en cours » (fin vide)",
          t_apres.heure_fin is None)
    # §0duodecies F2 (25/08/2026) : l'action d'audit de réactivation est
    # renommée « trajet.reactivation » (fusion R1+F2) — la règle R1 elle-même
    # (réouverture, début conservé) est inchangée et vérifiée ci-dessus
    check("audit « trajet.reactivation » tracé (renommage §0duodecies F2)",
          len(audits("trajet.reactivation", victime.id)) == 1)
    check("compteur réouvertures = 1", st["reouvertes"] == 1, f"stats={st}")

    # ================================================================
    print("\n[R2 · §0quater] Rattrapage — création sans aucune ligne existante")
    rendre_roulant(vR2b)
    db.add(EvenementGPS(vehicule_id=vR2b.id, type_evenement=TypeEvenement.DEBUT_MOUVEMENT,
                        horodatage=h(8, 0), latitude=-18.9, longitude=47.5,
                        vitesse=30.0))
    db.commit()
    st = engine.rattraper_ouvertures(maintenant=MAINTENANT)
    lignes = ligne_en_cours(vR2b)
    check("ligne « en cours » créée (camion roulant sans ligne)",
          len(lignes) == 1, f"lignes={[(t.heure_debut, t.heure_fin) for t in lignes]}")
    check("datée du vrai DÉMARRAGE (08:00, dernier DÉBUT_MOUVEMENT du jour)",
          lignes and lignes[0].heure_debut == h(8, 0),
          f"debut={lignes and lignes[0].heure_debut}")
    check("statut PROVISOIRE / EN_ATTENTE (§6.2 phase 1)",
          lignes and lignes[0].statut_source == StatutSourceTrajet.PROVISOIRE
          and lignes[0].statut_validation == StatutValidationTrajet.EN_ATTENTE)
    check("audit « trajet.ouverture_rattrapage » tracé",
          lignes and len(audits("trajet.ouverture_rattrapage", lignes[0].id)) == 1)

    print("\n[R2 · gardes] à l'arrêt / signal périmé / CamtrackPro / idempotence")
    rendre_arrete(vR2c)
    engine.rattraper_ouvertures(maintenant=MAINTENANT)
    check("camion À L'ARRÊT → aucune ligne créée",
          ligne_en_cours(vR2c) == [])
    vR2c.last_event_at = MAINTENANT - timedelta(minutes=30)   # signal périmé
    vR2c.last_vitesse = 50.0
    db.commit()
    engine.rattraper_ouvertures(maintenant=MAINTENANT)
    check("signal > 15 min → aucune ligne créée (prudence)",
          ligne_en_cours(vR2c) == [])
    rendre_roulant(vCTP)
    engine.rattraper_ouvertures(maintenant=MAINTENANT)
    check("CamtrackPro exclu (borne §5) → aucune ligne créée",
          ligne_en_cours(vCTP) == [])
    avant = len(ligne_en_cours(vR2b))
    engine.rattraper_ouvertures(maintenant=MAINTENANT)
    check("idempotent : 2ᵉ passage → aucune ligne supplémentaire",
          len(ligne_en_cours(vR2b)) == avant)

    # ================================================================
    print("\n[R3 · §0quater] TCC provisoire vivant dès la (ré)ouverture")
    # vR2b roule depuis 08:00 sans arrêt → TCC ≈ 3h à 11:00 (maintenant synth.)
    s = engine.ensure_suivi(db, vR2b, jour)
    engine.recalculer_temps(db, s, MAINTENANT)
    db.expire_all()
    s = db.get(SuiviJournalier, s.id)
    check("TCC provisoire ≈ 3h00 (début 08:00 → 11:00, sans arrêt)",
          s and abs((s.tcc_s or 0) - 3 * 3600) < 300,
          f"tcc_s={s and s.tcc_s}")

    # ================================================================
    print("\n[O1 · §0quater] Pré-alerte « pause non prise » à 4h00")
    seuils = engine.get_seuils(db)
    check("SEUIL_TCC_PREALERTE = 14400 s (4h00, admin-éditable §12.2)",
          float(seuils.get("SEUIL_TCC_PREALERTE", -1)) == 14400.0,
          f"valeur={seuils.get('SEUIL_TCC_PREALERTE')}")
    s = engine.ensure_suivi(db, vR2d, jour)
    s.tcc_s = 3 * 3600 + 50 * 60      # 3h50 — SOUS la pré-alerte 4h00
    db.commit()
    engine._verifier_temps(db, s, vR2d, seuils, MAINTENANT)
    n = db.scalar(select(Alerte).where(
        Alerte.vehicule_id == vR2d.id,
        Alerte.type == TypeAlerte.PAUSE_NON_PRISE))
    check("3h50 → AUCUNE pré-alerte (avant c'était 3h49:30)",
          n is None)
    s.tcc_s = 4 * 3600 + 5 * 60       # 4h05 — pré-alerte
    db.commit()
    engine._verifier_temps(db, s, vR2d, seuils, MAINTENANT)
    a = db.scalar(select(Alerte).where(
        Alerte.vehicule_id == vR2d.id,
        Alerte.type == TypeAlerte.PAUSE_NON_PRISE))
    check("4h05 → pré-alerte posée",
          a is not None and "Pré-alerte TCC" in (a.message or ""),
          f"message={a and a.message}")
    check("idempotente : pas de doublon d'alerte au cycle suivant",
          a is not None and db.scalar(
              select(__import__("sqlalchemy").func.count(Alerte.id)).where(
                  Alerte.vehicule_id == vR2d.id,
                  Alerte.type == TypeAlerte.PAUSE_NON_PRISE)) == 1)

finally:
    db.close()
    import app.database as d
    d.engine.dispose()
    if db_url.startswith("sqlite") and "/tmp/" in db_url:
        for p in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(db_url.replace("sqlite:///", "") + p)
            except OSError:
                pass

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
