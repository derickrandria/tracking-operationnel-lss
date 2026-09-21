"""Tests v1.51 — RATTRAPAGE & ARCHIVAGE : les 13 exigences du 18/09/2026.

Ces tests ne touchent NI les portails NI une base de production : les sources
sont INJECTÉES (`lecteur=`), la base est un fichier `/tmp` supprimé à la fin.

  1. une journée = une transaction isolée ....... T_E2E / T_ECHEC_MILIEU
  2. une erreur n'arrête pas les suivantes ..... T_ECHEC_MILIEU
  3. l'échec est consigné hors transaction ..... T_ECHEC_MILIEU / T_PANNE_MZONEX
  4. alerte créée OU mise à jour ............... T_ALERTE_UPSERT
  5. source indisponible ⇒ aucune archive
     partielle ................................ T_PANNE_MZONEX / T_PANNE_CAMTRACKPRO
                                                 / T_PAGINATION / T_PAS_ARCHIVE_PARTIELLE
  6. jour archivé jamais réécrit sans accord ... T_DOUBLE_EXECUTION / T_REECRITURE
  7. relancé au démarrage ET périodiquement .... T_PERIODIQUE
  8. idempotence ............................... T_DOUBLE_EXECUTION
  9. deux workers : exclusion mutuelle ......... T_VERROU / T_REDEMARRAGE
 10. PROVISOIRE / EN_ATTENTE / REJETE / VALIDE .. T_PAS_DE_PROVISOIRE
 11. « vide confirmée » ≠ « indisponible » .... T_VIDE_CONFIRME / T_PANNE_*
 12. Ym@ne non bloquante ...................... T_PANNE_YMANE
 13. simulateur jamais silencieux ............. T_SIMULATEUR

Scénarios demandés : panne MZoneX, panne CamtrackPro, panne Ym@ne, pagination
incomplète, erreur au milieu d'une série, redémarrage pendant une consolidation,
double exécution, absence d'archive partielle, absence de PROVISOIRE en archive.

Exécution (TOUJOURS sur une base de test) :
  DATABASE_URL="sqlite:////tmp/test_v151.db" python3 test_rattrapage_archivage_v151.py
"""
import json
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select

from app import engine
from app.api_mzonex import ErreurApiMZoneX
from app.config import now_local
from app.database import SessionLocal
from app.main import migrer_schema
from app.models import (Alerte, AuditLog, HistoriqueJournalier, StatutAlerte,
                        StatutSourceTrajet, StatutValidationTrajet, StatutVehicule,
                        SuiviJournalier, TraitementJournee, Trajet, Vehicule)
from app.rattrapage import (DISPONIBLE, INDISPONIBLE, NON_CONFIGUREE, VIDE_CONFIRMEE,
                            LectureJour, LectureSource, acquerir_verrou_jour,
                            marqueur_alerte, rattraper_journees, traiter_jour)
from app.seed import seed_si_vide

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

# ardoise vierge (déterminisme) : on ne garde que le référentiel véhicules
for modele in (Trajet, HistoriqueJournalier, SuiviJournalier, Alerte, AuditLog,
               TraitementJournee):
    db.execute(delete(modele))
db.commit()

VEHS = list(db.scalars(select(Vehicule).where(
    Vehicule.statut == StatutVehicule.ACTIF).limit(2)).all())
if len(VEHS) < 2:
    VEHS = list(db.scalars(select(Vehicule).limit(2)).all())
V1, V2 = VEHS[0], VEHS[1]

# journées de travail : passées, sans conflit avec « aujourd'hui »
AUJOUR = date.today()
J1, J2, J3 = (AUJOUR - timedelta(days=6), AUJOUR - timedelta(days=5),
              AUJOUR - timedelta(days=4))


def dt(jour, hh, mm=0):
    return datetime(jour.year, jour.month, jour.day, hh, mm)


def suivi_de(v, jour):
    s = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v.id,
        SuiviJournalier.date_jour == jour))
    return s or engine.ensure_suivi(db, v, jour)


def poser_ligne(v, jour, debut, fin, dist, *,
                source=StatutSourceTrajet.PROVISOIRE,
                validation=StatutValidationTrajet.EN_ATTENTE, plateforme="MZONEX"):
    s = suivi_de(v, jour)
    n = db.scalar(select(func.count(Trajet.id)).where(Trajet.suivi_id == s.id)) or 0
    t = Trajet(suivi_id=s.id, numero=n + 1, heure_debut=debut, heure_fin=fin,
               statut_source=source, source_plateforme=plateforme,
               distance_km=dist, statut_validation=validation)
    db.add(t)
    db.flush()
    db.commit()          # jamais de transaction ouverte pendant qu'un autre
    return t             # worker écrit (leçon v1.50 : « database is locked »)


def sources(mz=None, cp=None, ym=None):
    """Relevé de sources factice — jamais un portail réel."""
    return {
        "MZONEX": mz or LectureSource("MZONEX", VIDE_CONFIRMEE,
                                      detail="0 trajet publié (jour calme)"),
        "CAMTRACKPRO": cp or LectureSource("CAMTRACKPRO", VIDE_CONFIRMEE,
                                           detail="0 trajet (jour calme)"),
        "YMANE": ym or LectureSource("YMANE", DISPONIBLE,
                                     detail="non bloquante (observations)"),
    }


def lecteur_fixe(rel):
    return lambda jour: LectureJour(jour=jour, sources=dict(rel))


def nb_archives(jour):
    db.expire_all()                    # les traitements écrivent dans d'autres sessions
    return db.scalar(select(func.count(HistoriqueJournalier.id)).where(
        HistoriqueJournalier.date_jour == jour)) or 0


def audit(action, jour=None):
    db.expire_all()
    q = select(func.count(AuditLog.id)).where(AuditLog.action == action)
    if jour is not None:
        q = q.where(AuditLog.details.like(f'%"{jour.isoformat()}"%'))
    return db.scalar(q) or 0


def alerte_ouverte(jour):
    db.expire_all()
    return db.scalar(select(Alerte).where(
        Alerte.message.like(f"%{marqueur_alerte(jour)}%"),
        Alerte.statut.in_((StatutAlerte.NOUVELLE, StatutAlerte.VUE))))


def etat_jour(jour):
    db.expire_all()
    return db.get(TraitementJournee, jour)


def reinitialiser(*jours):
    """Remet à zéro l'état des journées testées (entre deux scénarios)."""
    db.execute(delete(Trajet))
    db.execute(delete(HistoriqueJournalier))
    db.execute(delete(SuiviJournalier))
    db.execute(delete(Alerte))
    db.execute(delete(AuditLog))
    db.execute(delete(TraitementJournee))
    db.commit()


# ============================================================================
print("\n[1] PANNES DE SOURCE — une source bloquante indisponible ⇒ AUCUNE archive")
# ============================================================================
reinitialiser()
poser_ligne(V1, J1, dt(J1, 6, 0), dt(J1, 9, 0), 40.0)

res = traiter_jour(J1, lecteur=lecteur_fixe(sources(
    mz=LectureSource("MZONEX", INDISPONIBLE, erreur="ErreurApiMZoneX: HTTP 503",
                     categorie="portail"))))
check("panne MZoneX : la journée n'est PAS archivée", nb_archives(J1) == 0,
      f"→ {nb_archives(J1)} archive(s)")
check("panne MZoneX : statut REFUSE_SOURCE", res["statut"] == "REFUSE_SOURCE", f"→ {res}")
check("panne MZoneX : audit 'jour.archive_refusee' écrit", audit("jour.archive_refusee", J1) == 1)
check("panne MZoneX : alerte visible ouverte", alerte_ouverte(J1) is not None)
etat_j1 = etat_jour(J1)
check("panne MZoneX : jour marqué EN_ATTENTE_SOURCE",
      etat_j1 is not None and etat_j1.statut == "EN_ATTENTE_SOURCE",
      f"→ {etat_j1.statut if etat_j1 else None}")
s_apres = db.scalar(select(SuiviJournalier).where(
    SuiviJournalier.date_jour == J1, SuiviJournalier.vehicule_id == V1.id))
check("panne MZoneX : la journée est quand même CONSOLIDÉE (23:59:59)",
      s_apres is not None and all(
          t.statut_source == StatutSourceTrajet.VALIDE for t in db.scalars(
              select(Trajet).where(Trajet.suivi_id == s_apres.id))))

reinitialiser()
poser_ligne(V1, J1, dt(J1, 6, 0), dt(J1, 9, 0), 40.0)
res = traiter_jour(J1, lecteur=lecteur_fixe(sources(
    cp=LectureSource("CAMTRACKPRO", INDISPONIBLE,
                     erreur="TimeoutError: délai Wialon dépassé",
                     categorie="portail"))))
check("panne CamtrackPro : aucune archive partielle", nb_archives(J1) == 0)
check("panne CamtrackPro : statut REFUSE_SOURCE + source nommée",
      res["statut"] == "REFUSE_SOURCE" and "CAMTRACKPRO" in res["raison"], f"→ {res['raison']}")

reinitialiser()
poser_ligne(V1, J1, dt(J1, 6, 0), dt(J1, 9, 0), 40.0)
res = traiter_jour(J1, lecteur=lecteur_fixe(sources(
    cp=LectureSource("CAMTRACKPRO", NON_CONFIGUREE, detail="jeton absent"))))
check("source NON_CONFIGURÉE : traitée comme non confirmée (pas d'archive)",
      res["statut"] == "REFUSE_SOURCE" and nb_archives(J1) == 0)

# ============================================================================
print("\n[2] PAGINATION INCOMPLÈTE — lot tronqué = source non confirmée")
# ============================================================================
reinitialiser()
poser_ligne(V1, J1, dt(J1, 6, 0), dt(J1, 9, 0), 40.0)
exc = ErreurApiMZoneX("pagination incomplète pour /Events (18000 lignes, plafond atteint)")
res = traiter_jour(J1, lecteur=lecteur_fixe(sources(
    mz=LectureSource("MZONEX", INDISPONIBLE, erreur=f"{type(exc).__name__}: {exc}",
                     categorie="portail"))))
check("pagination incomplète : aucune archive écrite", nb_archives(J1) == 0)
check("pagination incomplète : statut REFUSE_SOURCE", res["statut"] == "REFUSE_SOURCE")
d = db.scalar(select(AuditLog).where(AuditLog.action == "jour.archive_refusee"))
check("pagination incomplète : motif conservé dans l'audit",
      d is not None and "pagination incomplète" in json.dumps(d.details, ensure_ascii=False))

# ============================================================================
print("\n[3] PANNE Ym@ne — source NON bloquante (exigence 12)")
# ============================================================================
reinitialiser()
poser_ligne(V1, J1, dt(J1, 6, 0), dt(J1, 9, 0), 40.0)
res = traiter_jour(J1, lecteur=lecteur_fixe(sources(
    ym=LectureSource("YMANE", INDISPONIBLE, erreur="ApiYmane: HTTP 401",
                     categorie="portail"))))
check("panne Ym@ne : les trajets SONT archivés (source non bloquante)",
      res["statut"] == "ARCHIVE" and nb_archives(J1) == 1, f"→ {res}")
check("panne Ym@ne : l'état est quand même consigné",
      res["sources"].get("YMANE") == "INDISPONIBLE", f"→ {res['sources']}")
etat_j1 = etat_jour(J1)
check("panne Ym@ne : le détail des sources est conservé en base",
      etat_j1 is not None and (etat_j1.sources_etat or {}).get("YMANE", {}).get("etat") == "INDISPONIBLE")

# ============================================================================
print("\n[4] SOURCE VIDE CONFIRMÉE ≠ SOURCE INDISPONIBLE (exigence 11)")
# ============================================================================
reinitialiser()
res = traiter_jour(J2, lecteur=lecteur_fixe(sources()))     # 0 suivi, sources OK
check("jour réellement vide (toutes sources ont répondu 0) : VIDE_CONFIRME",
      res["statut"] == "VIDE_CONFIRME", f"→ {res}")
check("jour vide confirmé : aucune archive inventée", nb_archives(J2) == 0)
check("jour vide confirmé : marqué TERMINE (pas de relecture à chaque boot)",
      (etat_jour(J2) or TraitementJournee()).statut == "TERMINE")
check("jour vide confirmé : audit 'jour.vide_confirme'", audit("jour.vide_confirme", J2) == 1)

# ============================================================================
print("\n[5] ARCHIVE COMPLÈTE, IDEMPOTENTE ET SANS PROVISOIRE (exigences 6, 8, 10)")
# ============================================================================
reinitialiser()
poser_ligne(V1, J3, dt(J3, 6, 0), dt(J3, 9, 30), 48.0)     # sera VALIDE
poser_ligne(V1, J3, dt(J3, 10, 0), dt(J3, 10, 4), 0.1)     # < 0,3 km → REJETE
poser_ligne(V2, J3, dt(J3, 7, 0), dt(J3, 12, 0), 90.0)
db.commit()

check("AVANT : refus d'archiver un jour contenant du PROVISOIRE",
      db.scalar(select(func.count(HistoriqueJournalier.id)).where(
          HistoriqueJournalier.date_jour == J3)) == 0)
from app.daily import archiver_jour                                # noqa: E402
nb = archiver_jour(db, J3)
db.commit()
check("garde-fou : archiver_jour renvoie 0 (aucune ligne écrite)", nb == 0, f"→ {nb}")
check("garde-fou : aucune archive créée", nb_archives(J3) == 0)
check("garde-fou : audit 'archive.refusee_provisoire'", audit("archive.refusee_provisoire", J3) == 1)

res = traiter_jour(J3, lecteur=lecteur_fixe(sources()))
check("après consolidation : la journée est archivée", res["statut"] == "ARCHIVE"
      and nb_archives(J3) == 2, f"→ {res}")
trajets_j3 = list(db.scalars(select(Trajet).join(
    SuiviJournalier, Trajet.suivi_id == SuiviJournalier.id).where(
        SuiviJournalier.date_jour == J3)))
statuts = {t.statut_validation for t in trajets_j3}
sources_t = {t.statut_source for t in trajets_j3}
check("statuts distincts : plus aucun PROVISOIRE en base",
      StatutSourceTrajet.PROVISOIRE not in sources_t, f"→ {sources_t}")
check("statuts distincts : VALIDE et REJETE coexistent (jamais fusionnés)",
      statuts == {StatutValidationTrajet.VALIDE, StatutValidationTrajet.REJETE},
      f"→ {statuts}")
snapshots = [json.dumps(h.donnees or {}, ensure_ascii=False) for h in db.scalars(
    select(HistoriqueJournalier).where(HistoriqueJournalier.date_jour == J3))]
check("aucune donnée PROVISOIRE ni EN_ATTENTE dans les archives officielles",
      all("PROVISOIRE" not in s and "EN_ATTENTE" not in s for s in snapshots))
check("l'archive V2 existe aussi (les 2 véhicules du jour sont figés)",
      len(snapshots) == 2)

# double exécution : idempotence stricte (exigence 8) + non-réécriture (6)
avant = [h.id for h in db.scalars(select(HistoriqueJournalier).where(
    HistoriqueJournalier.date_jour == J3))]
res2 = traiter_jour(J3, lecteur=lecteur_fixe(sources()))
apres = [h.id for h in db.scalars(select(HistoriqueJournalier).where(
    HistoriqueJournalier.date_jour == J3))]
check("double exécution : statut DEJA_ARCHIVE", res2["statut"] == "DEJA_ARCHIVE", f"→ {res2}")
check("double exécution : aucune ligne d'archive recréée", avant == apres)
check("double exécution : aucune alerte créée", alerte_ouverte(J3) is None)

# ============================================================================
print("\n[6] RÉÉCRITURE D'ARCHIVE — jamais silencieuse (exigence 6)")
# ============================================================================
from app.daily import recalculer_archives_journee                      # noqa: E402
res_refus = recalculer_archives_journee(J3, db=db, rattraper_portail=False)
check("recalcul sans autorisation : REFUSÉE", res_refus.get("statut") == "REFUSEE", f"→ {res_refus}")
check("recalcul sans autorisation : archives intactes",
      [h.id for h in db.scalars(select(HistoriqueJournalier).where(
          HistoriqueJournalier.date_jour == J3))] == apres)
check("recalcul sans autorisation : audit 'archive.reecriture_refusee'",
      audit("archive.reecriture_refusee", J3) == 1)
res_ok = recalculer_archives_journee(J3, db=db, rattraper_portail=False,
                                     autoriser_reecriture=True, motif="test_v151")
check("recalcul autorisé : OK + tracé", res_ok.get("statut") == "OK"
      and audit("archive.reecriture_autorisee", J3) == 1, f"→ {res_ok}")

# ============================================================================
print("\n[7] ERREUR AU MILIEU D'UNE SÉRIE — les suivantes continuent (exigences 1-3)")
# ============================================================================
reinitialiser()
for j in (J1, J2, J3):
    poser_ligne(V1, j, dt(j, 6, 0), dt(j, 9, 0), 40.0)
db.commit()


def lecteur_avec_panne(jour):
    if jour == J2:
        raise RuntimeError("panne simulée de la journée du milieu")
    return LectureJour(jour=jour, sources=sources())


rap = rattraper_journees(cible_hier=J3, lecteur=lecteur_avec_panne)
check("série : J1 archivé malgré l'échec de J2", nb_archives(J1) == 1)
check("série : J2 en échec (aucune archive)", nb_archives(J2) == 0)
check("série : J3 TRAITÉ APRÈS l'échec de J2 (exigence 2)", nb_archives(J3) == 1,
      f"→ {rap}")
check("série : J2 consigné ECHEC en base",
      (etat_jour(J2) or TraitementJournee()).statut == "ECHEC")
check("série : audit 'jour.echec_traitement' dans une transaction séparée",
      audit("jour.echec_traitement", J2) == 1)
check("série : alerte visible pour J2", alerte_ouverte(J2) is not None)
check("série : rapport complet (recus/echecs/détails)",
      rap["recus"] == 3 and rap["echecs"] == [J2.isoformat()]
      and set(rap["détails"]) == {J1.isoformat(), J2.isoformat(), J3.isoformat()},
      f"→ recus={rap['recus']} echecs={rap['echecs']}")

# ============================================================================
print("\n[8] REDÉMARRAGE PENDANT UNE CONSOLIDATION + DOUBLE WORKER (exigences 6, 9)")
# ============================================================================
reinitialiser()
poser_ligne(V1, J1, dt(J1, 6, 0), dt(J1, 9, 0), 40.0)
db.commit()
# verrou FRAIS d'un autre worker : je ne dois PAS traiter la journée
acquerir_verrou_jour(J1, proprietaire="autre-worker:999")
res = traiter_jour(J1, lecteur=lecteur_fixe(sources()))
check("verrou frais : journée laissée à l'autre worker", res["statut"] == "VERROUILLE", f"→ {res}")
check("verrou frais : aucune archive écrite par moi", nb_archives(J1) == 0)

# worker mort : verrou périmé → reprise autorisée
ligne = etat_jour(J1)
ligne.maj = now_local() - timedelta(seconds=7200)          # 2 h sans nouvelles
db.commit()
ok, raison = acquerir_verrou_jour(J1)
check("verrou périmé : reprise autorisée (worker mort)", ok and raison == "repris",
      f"→ {ok}/{raison}")
# le worker « reprenant » meurt à son tour : le verrou redevient orphelin
db.expire_all()
ligne = etat_jour(J1)
ligne.statut = "EN_COURS"
ligne.maj = now_local() - timedelta(seconds=7200)
db.commit()
res = traiter_jour(J1, lecteur=lecteur_fixe(sources()))
check("redémarrage : la journée finit archivée malgré le verrou orphelin",
      res["statut"] == "ARCHIVE" and nb_archives(J1) == 1, f"→ {res}")
check("redémarrage : le compteur de tentatives a été incrémenté",
      (etat_jour(J1) or TraitementJournee()).tentatives >= 2)

# ============================================================================
print("\n[9] MODE SIMULATEUR — jamais activé silencieusement (exigence 13)")
# ============================================================================
reinitialiser()
poser_ligne(V1, J1, dt(J1, 6, 0), dt(J1, 9, 0), 40.0,
            source=StatutSourceTrajet.VALIDE, validation=StatutValidationTrajet.VALIDE,
            plateforme="SIMULATEUR")
db.commit()
nb = archiver_jour(db, J1)
db.commit()
check("trajet SIMULATEUR : archive refusée", nb == 0 and nb_archives(J1) == 0)
check("trajet SIMULATEUR : audit 'archive.refusee_simulateur'",
      audit("archive.refusee_simulateur", J1) == 1)
from app.config import SIMULATEUR_ARCHIVE_AUTORISE                     # noqa: E402
check("trajet SIMULATEUR : l'autorisation explicite est bien requise",
      SIMULATEUR_ARCHIVE_AUTORISE is False)

# ============================================================================
print("\n[10] RELANCE PÉRIODIQUE (exigence 7)")
# ============================================================================
import inspect                                                          # noqa: E402
import app.rattrapage as rat                                           # noqa: E402
import app.main as main_mod                                            # noqa: E402
src_main = inspect.getsource(main_mod.lifespan)
check("la boucle périodique est branchée au démarrage",
      "boucle_rattrapage_periodique" in src_main)
check("la boucle périodique existe et est asynchrone",
      inspect.iscoroutinefunction(rat.boucle_rattrapage_periodique))
check("le rattrapage du démarrage passe par le nouveau moteur",
      "rattraper_journees" in inspect.getsource(
          __import__("app.daily", fromlist=["daily"]).rattraper_consolidation))

# ============================================================================
print(f"\n{'=' * 66}\n  RÉSULTAT : {R['ok']} OK / {R['ko']} KO\n{'=' * 66}")
db.close()
try:
    if "/tmp/" in db_url:
        chemin = db_url.split("sqlite:///")[-1]
        for suffixe in ("", "-wal", "-shm"):
            try:
                os.remove(chemin + suffixe)
            except OSError:
                pass
        print("  (base de test supprimée)")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
