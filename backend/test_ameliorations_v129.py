"""Tests v1.29 — AMÉLIORATIONS v3 (§0nonies, arbitrages LSS du 22/08/2026).

  G1  AM-1 : TCJ = (now − départ) − TOUS les arrêts, toute durée.

RÉALIGNÉ v1.34 (§0quaterdecies H1/H2, 25/08/2026) : TCC = chrono de session,
arrêts < 30 min INCLUS. G5 : TCC 05:00→11:00 = 6:00 (span manœuvre inclus) ;
G8 : TCC figé à la consolidation = 39:59 (chrono jusqu'à la coupure 23:59:59,
le camion roulait encore à minuit) — l'amorce R1 suit (±3 s).
  G2  AM-2 : un trajet valide = une ligne (fusion d'affichage abolie) ;
      pauses affichées seulement ≥ 30 min ; gap brut conservé ; manœuvre
      clôturée masquée mais CONSERVÉE en base (R2).
  G3  T1 : TTJ = amplitude brute.
  G4  AM-6 : manœuvre d'ouverture de journée ignorée — départ = 1er
      mouvement valide.
  G5  AM-6 : mini-manœuvre en route = arrêt déduit du TCJ (TCC non coupé).
  G6  AM-6 : manœuvre ≥ 30 min = pause qui COUPE le TCC.
  G7  AM-3 : consolidation — en-cours fermé au dernier signal prouvé, sinon
      23:59:59 ; verdicts tranchés, OFFICIEL ; idempotent.
  G8  AM-3/C1 : TCC à travers minuit (R1) + split d'écriture portail.
  G9  AM-4 : catch-up (données mises en scène) — comble, archive, idempotent ;
      source absente → rien d'écrit ; jour vide des deux côtés → rien.
  G10 AM-5 : dépassement TCC / excès de vitesse → ALERTE, nulle Infraction
      locale ; /api/infractions ne rend que l'externe (vide pour l'instant).
  G11 T4 : HEURE_PRE_CONSOLIDATION = 86399 s paramétrable.

Exécution (base de test isolée, SUPPRIMÉE à la fin) :
  DATABASE_URL="sqlite:////tmp/test_v129.db" SIM_ENABLE=0 python3 test_ameliorations_v129.py
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.chaines import Segment, construire_journee
from app.config import jour_attribution
from app.database import SessionLocal
from app import daily, engine
from app.models import (Alerte, AuditLog, EvenementGPS, HistoriqueJournalier,
                        Infraction, StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, TypeAlerte, TypeEvenement,
                        Vehicule)
from app.reconciliation import _spliter_minuit
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

# Jours de test PROPRES (juin 2026 — avant l'historique de démo du seed) ;
# l'AM-4 (G9) balaye depuis `premier` suivi → on garde la carte en tête.
J1, J2, J3 = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)
J4, J5, J6 = date(2026, 6, 4), date(2026, 6, 5), date(2026, 6, 8)


def T(jour, h1, m1, h2=None, m2=None, km=5.0, s1=0,
      statut=StatutValidationTrajet.VALIDE):
    d0 = datetime(jour.year, jour.month, jour.day, h1, m1, s1)
    fin = None if h2 is None else datetime(jour.year, jour.month, jour.day,
                                           h2, m2, s1)
    return Segment(debut=d0, fin=fin, distance_km=km,
                   rejete=(statut == StatutValidationTrajet.REJETE))


def ajoute_trajet(suivi, numero, jour, h1, m1, h2=None, m2=None, km=5.0,
                  s1=0, statut=StatutValidationTrajet.VALIDE,
                  source=StatutSourceTrajet.VALIDE):
    t = Trajet(suivi_id=suivi.id, numero=numero,
               heure_debut=datetime(jour.year, jour.month, jour.day, h1, m1, s1),
               heure_fin=(None if h2 is None else datetime(
                   jour.year, jour.month, jour.day, h2, m2, s1)),
               distance_km=km, statut_source=source, statut_validation=statut,
               source_plateforme="MZONEX")
    db.add(t)
    return t


V = db.scalar(select(Vehicule).where(Vehicule.plaque == "0906TBV"))
assert V, "seed : 0906TBV introuvable"

# ------------------------------------------------------------------ G1..G3
print("\n[G1-G3] AM-1 compteurs · AM-2 affichage · T1 amplitude")
# Exemple consolidé : 05:33→07:10 (40km), pause 0:15, 07:25→10:00 (50km),
# pause 0:35, 10:35→12:00 (30km), + manœuvre masquée 12:10→12:20 (0,2 km)
lignes_src = [T(J1, 5, 33, 7, 10, 40.0), T(J1, 7, 25, 10, 0, 50.0),
              T(J1, 10, 35, 12, 0, 30.0),
              T(J1, 12, 10, 12, 20, 0.2,
                statut=StatutValidationTrajet.REJETE)]
j = construire_journee(lignes_src, maintenant=datetime(2026, 6, 1, 12, 30),
                       pause_affichee_min=1800)
check("AM-2 : 3 lignes (chaque trajet valide = SA ligne ; manœuvre masquée)",
      len(j.lignes) == 3, f"lignes={len(j.lignes)}")
check("AM-1 : TCJ = 1:37 + 2:35 + 1:25 = 5:37 (tous arrêts déduits)",
      j.tcj_s == (97 + 155 + 85) * 60, f"tcj={j.tcj_s}")
check("T1 : TTJ = 12:00 − 5:33 = 6:27 amplitude brute ; Σ arrêts = 0:50",
      j.ttj_s == (6 * 60 + 27) * 60 and j.total_pause_s == 50 * 60,
      f"ttj={j.ttj_s} pauses={j.total_pause_s}")
check("AM-2 : pause 0:15 MASQUÉE (cellule vide) ; pause 0:35 AFFICHÉE ; "
      "gaps bruts conservés pour les compteurs",
      j.lignes[0].pause_apres_s == 0 and j.lignes[0].gap_brut_s == 900
      and j.lignes[1].pause_apres_s == 2100 and j.lignes[1].gap_brut_s == 2100)

s1 = engine.ensure_suivi(db, V, J1)
for n, (d1, f1, km, val) in enumerate([
        (T(J1, 12, 10, 12, 20, 0.2, statut=StatutValidationTrajet.REJETE),
         None, 0.2, StatutValidationTrajet.REJETE)], start=90):
    ajoute_trajet(s1, n, J1, 12, 10, 12, 20, 0.2,
                  statut=val)
db.commit()
nb_base = db.scalar(select(func.count(Trajet.id)).where(
    Trajet.suivi_id == s1.id))
check("R2 : la manœuvre 0,2 km RESTE en base (audit) — 1 ligne stockée",
      nb_base == 1, f"n={nb_base}")

# ------------------------------------------------------------------ G4
print("\n[G4] AM-6 : départ = 1er mouvement valide (manœuvre 5:00 ignorée)")
s2 = engine.ensure_suivi(db, V, J2)
ajoute_trajet(s2, 1, J2, 5, 0, 5, 20, 0.29,
              statut=StatutValidationTrajet.REJETE)
ajoute_trajet(s2, 2, J2, 5, 21, 7, 0, 9.0)
db.commit()
engine.recalculer_temps(db, s2, datetime(2026, 6, 2, 7, 0))
check("AM-6 : départ 05:21 ; TTJ = 1:39 pile (les 21 min de manœuvre "
      "jamais comptabilisées)",
      s2.heure_depart == datetime(2026, 6, 2, 5, 21)
      and s2.ttj_s == 99 * 60, f"depart={s2.heure_depart} ttj={s2.ttj_s}")

# ------------------------------------------------------------------ G5
print("\n[G5] AM-6 : mini-manœuvre 10 min en route → déduite du TCJ")
s3 = engine.ensure_suivi(db, V, J3)
ajoute_trajet(s3, 1, J3, 5, 0, 9, 0, 60.0)
ajoute_trajet(s3, 2, J3, 9, 0, 9, 10, 0.25,
              statut=StatutValidationTrajet.REJETE)
ajoute_trajet(s3, 3, J3, 9, 10, 11, 0, 30.0)
db.commit()
engine.recalculer_temps(db, s3, datetime(2026, 6, 3, 11, 0))
check("AM-6 : TCJ = 4:00 + 1:50 = 5:50 (span de manœuvre déduit) ; TCC chrono "
      "= 6:00 (§0quaterdecies H1 : l'arrêt plein de 10 min est INCLUS)",
      s3.tcj_s == 350 * 60 and s3.tcc_s == 360 * 60,
      f"tcj={s3.tcj_s} tcc={s3.tcc_s}")

# ------------------------------------------------------------------ G6
print("\n[G6] AM-6 : manœuvre 35 min = pause qui COUPE le TCC")
s4 = engine.ensure_suivi(db, V, J4)
ajoute_trajet(s4, 1, J4, 5, 0, 8, 0, 40.0)
ajoute_trajet(s4, 2, J4, 8, 0, 8, 35, 0.2,
              statut=StatutValidationTrajet.REJETE)
ajoute_trajet(s4, 3, J4, 8, 35, 9, 30, 10.0)
db.commit()
engine.recalculer_temps(db, s4, datetime(2026, 6, 4, 9, 30))
check("AM-6 : TCJ = 3:55 ; TCC coupé au bout de 35 min d'arrêt-manœuvre "
      "→ repart à 0:55 sur la 2ᵉ session",
      s4.tcj_s == 235 * 60 and s4.tcc_s == 55 * 60,
      f"tcj={s4.tcj_s} tcc={s4.tcc_s}")

# ------------------------------------------------------------------ G7
print("\n[G7] AM-3/C1 : consolidation — split au DERNIER SIGNAL prouvé")
s5 = engine.ensure_suivi(db, V, J5)
db.add(EvenementGPS(vehicule_id=V.id,
                    horodatage=datetime(2026, 6, 5, 23, 58),
                    type_evenement=TypeEvenement.DEBUT_MOUVEMENT,
                    latitude=-18.9, longitude=47.6, vitesse=40.0))
ajoute_trajet(s5, 1, J5, 23, 30, None, None, 8.0,
              statut=StatutValidationTrajet.EN_ATTENTE,
              source=StatutSourceTrajet.PROVISOIRE)
db.commit()
daily.consolider_jour(db, J5)
db.expire_all()
t5 = db.scalars(select(Trajet).where(Trajet.suivi_id == s5.id)).all()[0]
check("AM-3 : fin = 23:58 (dernier signal prouvé, §10) ; OFFICIEL + "
      "VALIDE + audit ; 2ᵉ appel = 0 (idempotent)",
      t5.heure_fin == datetime(2026, 6, 5, 23, 58)
      and t5.statut_source == StatutSourceTrajet.VALIDE
      and t5.statut_validation == StatutValidationTrajet.VALIDE
      and daily.consolider_jour(db, J5) == 0,
      f"fin={t5.heure_fin} rerun={daily.consolider_jour(db, J5)}")

s6 = engine.ensure_suivi(db, V, date(2026, 6, 6))
ajoute_trajet(s6, 1, date(2026, 6, 6), 23, 40, None, None, 2.0,
              statut=StatutValidationTrajet.EN_ATTENTE,
              source=StatutSourceTrajet.PROVISOIRE)
db.commit()
daily.consolider_jour(db, date(2026, 6, 6))
db.expire_all()
t6 = db.scalars(select(Trajet).where(Trajet.suivi_id == s6.id)).all()[0]
check("AM-3 : silence radio (aucun signal du jour) → clôture à 23:59:59 "
      "pile", t6.heure_fin == datetime(2026, 6, 6, 23, 59, 59),
      f"fin={t6.heure_fin}")

# ------------------------------------------------------------------ G8
print("\n[G8] AM-3/C1 : R1 (TCC traverse minuit) + split d'écriture portail")
s8 = engine.ensure_suivi(db, V, J6)
db.add(EvenementGPS(vehicule_id=V.id,
                    horodatage=datetime(2026, 6, 8, 23, 59),
                    type_evenement=TypeEvenement.DEBUT_MOUVEMENT,
                    latitude=-18.9, longitude=47.6, vitesse=40.0))
ajoute_trajet(s8, 1, J6, 23, 20, None, None, 3.0,
              statut=StatutValidationTrajet.EN_ATTENTE,
              source=StatutSourceTrajet.PROVISOIRE)
db.commit()
daily.consolider_jour(db, J6)     # fige tcc_s au chrono 23:20→23:59:59
db.expire_all()
tcc_veille = db.get(SuiviJournalier, s8.id).tcc_s
check("R1 préparation : la veille s'est close à la coupure 23:59:59 avec "
      "TCC = 39:59 (§0quaterdecies H1 : chrono jusqu'à la coupure — le camion "
      "roulait encore à minuit)", tcc_veille == 39 * 60 + 59,
      f"tcc_veille={tcc_veille}")
J7 = date(2026, 6, 9)
s9 = engine.ensure_suivi(db, V, J7)
ajoute_trajet(s9, 1, J7, 0, 0, None, None, None, s1=12,
              statut=StatutValidationTrajet.EN_ATTENTE,
              source=StatutSourceTrajet.PROVISOIRE)
db.commit()
engine.recalculer_temps(db, s9, datetime(2026, 6, 9, 0, 30))
tcc_attendu = tcc_veille + (29 * 60 + 48)
check("R1 : le TCC est amorcé au TCC figé de la veille (39:00) et continue "
      "(+ 0:29:48)",
      abs(s9.tcc_s - tcc_attendu) <= 3,
      f"tcc_j={s9.tcc_s} attendu={tcc_attendu}")

b = _spliter_minuit([{"plaque": "X", "debut": datetime(2026, 6, 8, 23, 30),
                      "fin": datetime(2026, 6, 9, 0, 30),
                      "distance_km": 42.0, "conducteur": "N",
                      "source": "MZONEX", "exc_vitesse": 3}])
check("AM-3 split d'écriture : A jour J [23:30→23:59:59, km+compteurs "
      "conservés] · B jour J+1 [00:00→00:30, non mesuré, badge suivi]",
      len(b) == 2 and b[0]["fin"] == datetime(2026, 6, 8, 23, 59, 59)
      and b[0]["distance_km"] == 42.0 and b[0]["exc_vitesse"] == 3
      and b[1]["debut"] == datetime(2026, 6, 9, 0, 0)
      and b[1]["distance_km"] is None and b[1]["conducteur"] == "N"
      and b[1].get("suite_minuit") is True)
check("AM-3 : jamais de split pour un trajet « ouvert » (fait à la "
      "consolidation) ni un trajet clôturé le même jour",
      _spliter_minuit([{"debut": datetime(2026, 6, 8, 23, 30), "fin": None,
                        "ouvert": True}])[0]["fin"] is None
      and len(_spliter_minuit([{"debut": datetime(2026, 6, 8, 12, 0),
                                "fin": datetime(2026, 6, 8, 13, 0)}])) == 1)

# ------------------------------------------------------------------ G9
print("\n[G9] AM-4 : catch-up au démarrage (mise en scène)")
J_MQ = date(2026, 6, 10)      # jour manquant (rien en base)
J_VI = date(2026, 6, 11)      # jour vide partout
J_SA = date(2026, 6, 12)      # jour sans source accessible
V2 = db.scalar(select(Vehicule).where(Vehicule.plaque == "0826TBS"))

# v1.51 — la couture d'injection a changé : le moteur de rattrapage lit les
# sources lui-même (`rattrapage.lecture_reelle`) pour distinguer « source vide
# confirmée » de « source indisponible » (exigence 11). On injecte donc un
# relevé PAR SOURCE ; les assertions de G9 sont inchangées.
import app.rattrapage as rat
sauve_f = rat.lecture_reelle
try:
    def faux_lecture(jour):
        vide = lambda nom: rat.LectureSource(nom, rat.VIDE_CONFIRMEE)
        ym = lambda: rat.LectureSource("YMANE", rat.DISPONIBLE)
        if jour == J_MQ:
            items = [{"plaque": "0826TBS",
                      "debut": datetime(2026, 6, 10, 6, 0),
                      "fin": datetime(2026, 6, 10, 9, 0),
                      "distance_km": 120.0,
                      "conducteur": "ANDRIAMAMPIANINA Lahatra Faneva Omega",
                      "source": "CAMTRACKPRO"},
                     {"plaque": "0826TBS",
                      "debut": datetime(2026, 6, 10, 10, 0),
                      "fin": datetime(2026, 6, 10, 12, 30),
                      "distance_km": 80.0,
                      "conducteur": "ANDRIAMAMPIANINA Lahatra Faneva Omega",
                      "source": "CAMTRACKPRO"}]
            return rat.LectureJour(jour, {
                "MZONEX": rat.LectureSource("MZONEX", rat.DISPONIBLE, items=items),
                "CAMTRACKPRO": rat.LectureSource("CAMTRACKPRO", rat.DISPONIBLE, items=[]),
                "YMANE": ym()})
        if jour == J_SA:
            return rat.LectureJour(jour, {
                "MZONEX": rat.LectureSource("MZONEX", rat.INDISPONIBLE,
                                            erreur="TimeoutError"),
                "CAMTRACKPRO": rat.LectureSource("CAMTRACKPRO", rat.INDISPONIBLE,
                                                 erreur="HTTP 503"),
                "YMANE": ym()})
        return rat.LectureJour(jour, {"MZONEX": vide("MZONEX"),
                                      "CAMTRACKPRO": vide("CAMTRACKPRO"),
                                      "YMANE": ym()})

    rat.lecture_reelle = faux_lecture
    rep = daily.rattraper_consolidation(cible_hier=J_SA)
    db.expire_all()
    arch = db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == J_MQ,
        HistoriqueJournalier.vehicule_id == V2.id))
    check("AM-4 : jour manquant consolidé + ARCHIVÉ avec les données réelles "
          "(2 trajets publiés)",
          arch is not None and (arch.donnees or {}).get("nb_trajets") == 2,
          f"arch={arch is not None}")
    n_arch_av = db.scalar(select(func.count(HistoriqueJournalier.id)))
    rep2 = daily.rattraper_consolidation(cible_hier=J_SA)
    check("AM-4 idempotent : second passage → archive non réécrite (§A.2)",
          db.scalar(select(func.count(HistoriqueJournalier.id))) == n_arch_av
          and J_MQ.isoformat() not in rep2["détails"])
    arch_sa = db.scalar(select(func.count(HistoriqueJournalier.id)).where(
        HistoriqueJournalier.date_jour == J_SA))
    audit_sa = db.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.action == "jour.archive_refusee"))
    check("AM-4 garde-fou R5 (v1.51) : source INDISPONIBLE → AUCUNE archive, "
          "jour journalisé + alerte et en attente de reprise",
          (arch_sa or 0) == 0 and audit_sa >= 1
          and J_SA.isoformat() in rep["jours_sautés"]
          and J_SA.isoformat() in rep["en_attente_source"],
      f"arch={arch_sa} audit={audit_sa} sautes={rep['jours_sautés']} "
      f"attente={rep['en_attente_source']}")
    arch_vi = db.scalar(select(func.count(HistoriqueJournalier.id)).where(
        HistoriqueJournalier.date_jour == J_VI))
    check("AM-4 : jour vide des deux côtés (plateforme éteinte, portails "
          "muets) → RIEN n'est inventé", (arch_vi or 0) == 0)
finally:
    rat.lecture_reelle = sauve_f

# ------------------------------------------------------------------ G10
print("\n[G10] AM-5/C3 : zéro écriture locale dans Infraction")
av_inf = db.scalar(select(func.count(Infraction.id)))
s10 = engine.ensure_suivi(db, V, date(2026, 6, 13))
s10.tcc_s = 17000                                   # > SEUIL_TCC_MAX
engine._verifier_temps(db, s10, V, engine.get_seuils(db),
                       datetime(2026, 6, 13, 12, 0))
al_tcc = db.scalar(select(Alerte).where(
    Alerte.type == TypeAlerte.TCC_DEPASSE, Alerte.vehicule_id == V.id))
check("AM-5 : dépassement TCC → ALERTE dans l'onglet Alertes, AUCUNE "
      "Infraction locale écrite",
      db.scalar(select(func.count(Infraction.id))) == av_inf
      and al_tcc is not None)

engine.ingest_event(db, V, datetime(2026, 6, 13, 12, 5), -18.9, 47.6,
                    "RN2", 130.0, "ON", publier=False)
db.commit()
al_vit = db.scalar(select(func.count(Alerte.id)).where(
    Alerte.type == TypeAlerte.EXCES_VITESSE, Alerte.vehicule_id == V.id))
check("AM-5 : excès de vitesse 130 km/h → ALERTE, toujours AUCUNE "
      "Infraction locale",
      al_vit >= 1 and db.scalar(select(func.count(Infraction.id))) == av_inf)

from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
resp_inf = None
tok = client.post("/api/auth/login",
                  json={"username": "admin", "password": "Admin@2026"})
if tok.status_code == 200:
    h = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    r = client.get("/api/infractions", headers=h)
    resp_inf = (r.status_code, r.json().get("total"))
check("AM-5 : GET /api/infractions → 200, total = 0 (vitre en attente de "
      "source externe)", resp_inf == (200, 0), str(resp_inf))

# ------------------------------------------------------------------ G11
print("\n[G11] T4 : HEURE_PRE_CONSOLIDATION = 23:59:59 (paramétrable §12.2)")
s_all = engine.get_seuils(db)
check("T4 : 86399 s présentes · jour_attribution = DATE pure (00:15 → le "
      "même jour civil, bascule 01h00 abrogée)",
      int(s_all.get("HEURE_PRE_CONSOLIDATION", -1)) == 86399
      and jour_attribution(datetime(2026, 6, 13, 0, 15)) == date(2026, 6, 13))

# ---------------------------------------------------------------- nettoyage
db.close()
for cand in ("/tmp/test_v129.db",):
    try:
        os.unlink(cand)
    except OSError:
        pass

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
