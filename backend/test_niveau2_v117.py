"""Tests v1.17/v1.18 — CHAÎNES OFFICIELLES + AUTO-RÉPARATION Niveau 1.

ATTENDUS ALIGNÉS RÉFÉRENCE v2 / arbitrages métier du 06/08 (v1.18 — supersèdent
la soudure v1.13 et l'extension de fin v1.17) :

  §1  0616TCD — exemple §5.2 DE LA LOI rejoué à l'identique (15 lignes
      officielles) : l'affichage correct est VERBATIM la référence :
      « 5:31 | 8:24 | Pause 1:24 | 9:48 | 9:56 ». Fin = PREMIER arrêt après
      le trajet valide (§5.1) ; les 4 manœuvres 08:26→08:40 (et toutes les
      autres) sont IGNORÉES PARTOUT (§7) : ne soudent plus, ne prolongent
      plus, n'interrompent pas la pause, AUCUN segment posé en base ; les
      manœuvres ne comptent jamais (§2) : distances = valeurs portail des
      seuls segments réels (84,318 / 1,442 km).
  §2  4886TBU — manœuvre de tête de journée (05:21:26 → 05:42:26, 0,021 km)
      IGNORÉE : la ligne en base ET l'ÉCRAN démarrent au premier trajet réel
      (05:55:27) ; le trajet « en cours » de 11:00:17 apparaît orange avec
      Fin vide. À la clôture, s'il s'avère être une manœuvre (0 km), la
      ligne orange est effacée (décision métier du 05/08 confirmée, §3.1).
  §3  Auto-réparation Niveau 1 : un « Début du trajet » CONNU de
      l'anti-rejeu mais resté SANS trajet (création manquée lors d'une
      passe défectueuse) n'était jamais ré-essayé → trajet en cours
      invisible (2736TCC 11:37:44, 4886TBU 11:00:17). v1.17 : le dernier
      début sans fin ni trajet couvrant est ré-ingéré une fois par passe,
      jusqu'à couverture (idempotent).
      v1.24 (§0quinquies, correctif 14/08 PM) : la réparation exige une
      PREUVE DE MOUVEMENT — dernière position connue du camion en roulage
      (vitesse > seuil d'arrêt, signal ≤ 20 min, condition R2 §0quater).
      Un « Début » seul à 0 km/h (blip de contact, 0926TBV / 8076TCB le
      14/08) ne recrée PLUS de ligne fantôme ; un camion réellement en
      route (point POSITION frais à 25 km/h) est toujours réparé.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v117.db" python3 test_niveau2_v117.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app.config import jour_attribution, now_local
from app.database import SessionLocal
from app import engine
from app.models import (AuditLog, EvenementGPS, SourceEvenement,
                        StatutSourceTrajet, StatutValidationTrajet, Trajet,
                        TypeEvenement, Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.reconciliation import reconcilier_trajets_valides
from app.serializers import journee_suivi, s_ligne, s_suivi
from app.scrapers import MZoneXCollector

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

# Ancrage dans la journée logistique courante (scénario décalé à 15h si la
# suite tourne tôt le matin — les assertions utilisent `maintenant` injecté)
ancre = now_local().replace(microsecond=0)
if ancre.hour < 7:
    ancre = ancre.replace(hour=15, minute=0, second=0)
jour = jour_attribution(ancre)
base = datetime.combine(jour, datetime.min.time())


def h(hh, mm, ss=0):
    return base.replace(hour=hh, minute=mm, second=ss)


mzx = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF").order_by(
        Vehicule.plaque)).all()
v0616, v4886, vtrap, vterm, vblip = mzx[0], mzx[1], mzx[2], mzx[3], mzx[4]
print(f"Véhicules test : 0616={v0616.plaque} · 4886={v4886.plaque} · "
      f"trap={vtrap.plaque} · term={vterm.plaque} · blip={vblip.plaque} · "
      f"jour={jour}")

nettoie_suivis = set()
for v in (v0616, v4886, vtrap, vterm, vblip):
    s = engine.ensure_suivi(db, v, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
    nettoie_suivis.add(s.id)
db.execute(delete(AuditLog).where(AuditLog.username.like("test-v117%")))
db.commit()


def visibles(veh, jour_j=None):
    s = engine.ensure_suivi(db, veh, jour_j or jour)
    db.expire_all()
    return [t for t in db.scalars(select(Trajet).where(
        Trajet.suivi_id == s.id).order_by(Trajet.numero)).all()
        if t.statut_validation != StatutValidationTrajet.REJETE]


def rejetes(veh):
    s = engine.ensure_suivi(db, veh, jour)
    db.expire_all()
    return [t for t in db.scalars(select(Trajet).where(
        Trajet.suivi_id == s.id).order_by(Trajet.numero)).all()
        if t.statut_validation == StatutValidationTrajet.REJETE]


try:
    # ================================================================
    print("\n[§1 · 0616TCD] Rejeu de la capture « Trajets » (15 lignes, 14:20)")
    lignes0616 = [
        ("05:31:03", "08:24:38", 84.318),
        ("08:26:10", "08:28:05", 0.058), ("08:30:12", "08:31:40", 0.044),
        ("08:33:00", "08:36:22", 0.111), ("08:38:05", "08:40:19", 0.038),
        ("09:48:34", "09:56:51", 1.442),
        ("10:01:47", "10:02:44", 0.005), ("10:17:08", "10:20:23", 0.124),
        ("10:21:10", "10:21:27", 0.011), ("10:32:19", "10:35:07", 0.072),
        ("11:21:56", "11:24:49", 0.012),
        ("12:00:40", "12:03:38", 0.031), ("12:10:44", "12:12:35", 0.058),
        ("13:46:37", "13:48:53", 0.0), ("13:59:16", "14:00:47", 0.0),
    ]
    items0616 = [{"plaque": v0616.plaque,
                  "debut": datetime.fromisoformat(f"{jour.isoformat()} {d}"),
                  "fin": datetime.fromisoformat(f"{jour.isoformat()} {f}"),
                  "distance_km": km, "source": "MZONEX"}
                 for d, f, km in lignes0616]
    stats = reconcilier_trajets_valides(db, items0616, username="test-v117",
                                        maintenant=h(14, 22, 0))
    tr = visibles(v0616)
    check("2 lignes affichées (T1, T2) — les manœuvres ne forment jamais une ligne",
          len(tr) == 2, f"n={len(tr)}")
    check("13 manœuvres rejetées (4 + 4 soudées, 1 + 2 + 2 isolées)",
          stats["rejets"] == 13 and stats["crees"] == 2, str(stats))
    t1, t2 = tr
    check("T1 = 05:31:03 → 08:24:38 VALIDÉ — §5.1/§5.2 VERBATIM : fin = PREMIER "
          "arrêt après le trajet valide (les 4 manœuvres 08:26→08:40 sont ignorées)",
          t1.heure_debut == h(5, 31, 3) and t1.heure_fin == h(8, 24, 38)
          and t1.statut_source == StatutSourceTrajet.VALIDE
          and t1.statut_validation == StatutValidationTrajet.VALIDE,
          f"{t1.heure_debut}→{t1.heure_fin}")
    check("T1 distance = 84,318 km (valeur portail — la manœuvre ne compte jamais, §2)",
          abs((t1.distance_km or 0) - 84.318) < 1e-6, str(t1.distance_km))
    check("pause T1 = 1h23m56s « 1:24 » §5.2 (08:24:38 → 09:48:34, ≥ 20 min → 2 lignes)",
          t1.pause_apres_s == 5036, str(t1.pause_apres_s))
    check("T2 = 09:48:34 → 09:56:51 VALIDÉ — §5.2 « Affichage correct : "
          "5:31 | 8:24 | 1:24 | 9:48 | 9:56 », verbatim",
          t2.heure_debut == h(9, 48, 34) and t2.heure_fin == h(9, 56, 51)
          and t2.statut_source == StatutSourceTrajet.VALIDE,
          f"{t2.heure_debut}→{t2.heure_fin}")
    check("T2 distance = 1,442 km (valeur portail du seul segment réel)",
          abs((t2.distance_km or 0) - 1.442) < 1e-6, str(t2.distance_km))
    check("AUCUN segment posé (Référence v2 §7 : IGNORER = ne pas créer de "
          "trajet — les 13 manœuvres ne laissent aucune ligne)",
          len(rejetes(v0616)) == 0, f"n={len(rejetes(v0616))}")

    stats2 = reconcilier_trajets_valides(db, items0616, username="test-v117",
                                         maintenant=h(14, 23, 0))
    check("rejeu idempotent : 2 ignorés, 0 créé, toujours 0 segment posé, "
          "2 lignes en base uniquement",
          stats2["ignores"] == 2 and stats2["crees"] == 0
          and len(visibles(v0616)) == 2 and len(rejetes(v0616)) == 0,
          str(stats2))

    s_json = s_suivi(engine.ensure_suivi(db, v0616, jour), engine.get_seuils(db))
    check("grille : 2 trajets, départ officiel 05:31:03",
          s_json["nb_trajets"] == 2
          and (s_json["heure_depart"] or "").endswith("05:31:03"),
          f"nb={s_json['nb_trajets']} depart={s_json['heure_depart']}")
    amplitude_0616 = int((h(9, 56, 51) - h(5, 31, 3)).total_seconds())  # 15948
    check("compteurs : TCC = 0 (pause ≥ 30 min constatée), TTJ = amplitude "
          "05:31:03→09:56:51, TTJ − TCJ = arrêts « 1:24 » §1.3 (les manœuvres "
          "n'entrent JAMAIS dans TCC/TCJ/TTJ — §2)",
          s_json["tcc_s"] == 0 and s_json["ttj_s"] == amplitude_0616
          and (s_json["ttj_s"] - s_json["tcj_s"]) == 5036
          and 3 * 3600 <= s_json["tcj_s"] < 4 * 3600,
          f"tcc={s_json['tcc_s']} tcj={s_json['tcj_s']} ttj={s_json['ttj_s']}")

    # ================================================================
    print("\n[§2 · 4886TBU] Manœuvre de tête + trajet « en cours » (capture 14:23)")
    items4886 = [
        {"plaque": v4886.plaque, "debut": h(5, 21, 26), "fin": h(5, 42, 26),
         "distance_km": 0.021, "source": "MZONEX"},
        {"plaque": v4886.plaque, "debut": h(5, 55, 27), "fin": h(10, 3, 25),
         "distance_km": 38.4, "source": "MZONEX"},
        {"plaque": v4886.plaque, "debut": h(11, 0, 17), "fin": h(11, 2, 44),
         "distance_km": None, "source": "MZONEX"},
    ]
    stats = reconcilier_trajets_valides(db, items4886, username="test-v117",
                                        maintenant=h(11, 10, 30))
    tr = visibles(v4886)
    check("1 trajet VALIDÉ + 1 « en cours » importé (ouverts=1), manœuvre rejetée",
          stats["crees"] == 1 and stats["ouverts"] == 1 and stats["rejets"] == 1
          and len(tr) == 2, str(stats))
    t1, ouvert = tr
    check("ligne du matin = 05:55:27 → 10:03:25 (38,4 km) — la manœuvre de tête "
          "ne décale PAS le début de la ligne retenue (0916TBV)",
          t1.heure_debut == h(5, 55, 27) and t1.heure_fin == h(10, 3, 25)
          and abs((t1.distance_km or 0) - 38.4) < 1e-6,
          f"{t1.heure_debut}→{t1.heure_fin}")
    check("trajet « en cours » = début 11:00:17 orange (PROVISOIRE + EN_ATTENTE) — "
          "la ligne manquante de la capture",
          ouvert.heure_debut == h(11, 0, 17)
          and ouvert.statut_source == StatutSourceTrajet.PROVISOIRE
          and ouvert.statut_validation == StatutValidationTrajet.EN_ATTENTE,
          f"{ouvert.statut_source}/{ouvert.statut_validation}")

    jn = journee_suivi(engine.ensure_suivi(db, v4886, jour),
                       engine.get_seuils(db), maintenant=h(11, 10, 30))
    lignes_j = [s_ligne(lg, i) for i, lg in enumerate(jn.lignes, start=1)]
    check("ÉCRAN : la manœuvre 05:21:26 est IGNORÉE (§7) — le départ AFFICHÉ "
          "= premier trajet RÉEL 05:55:27 (elle n'interrompt plus rien)",
          len(lignes_j) == 2 and lignes_j[0]["heure_debut"].endswith("05:55:27")
          and lignes_j[0]["heure_fin"].endswith("10:03:25"),
          f"{[(l['heure_debut'], l['heure_fin']) for l in lignes_j]}")
    check("ÉCRAN : ligne 2 « en cours » orange (PROVISOIRE), pause jamais < 20 min",
          lignes_j[1]["heure_debut"].endswith("11:00:17")
          and lignes_j[1]["statut_source"] == "PROVISOIRE"
          and lignes_j[0]["pause_apres_s"] >= 1200,
          f"pause={lignes_j[0]['pause_apres_s']}")

    # clôture : la fin du « en cours » s'avère être une manœuvre (0,0 km) → effacée
    items_cl = [dict(it) for it in items4886]
    items_cl[2]["distance_km"] = 0.0
    stats = reconcilier_trajets_valides(db, items_cl, username="test-v117",
                                        maintenant=h(11, 30, 0))
    db.expire_all()
    tr = visibles(v4886)
    check("à la clôture, le « en cours » s'avère une manœuvre (0,0 km) → ligne "
          "orange EFFACÉE (décision métier 05/08), le trajet du matin intact",
          len(tr) == 1 and tr[0].heure_debut == h(5, 55, 27),
          f"n={len(tr)} rejets={stats['rejets']}")

    # ================================================================
    print("\n[§3] Auto-réparation N1 : « Début du trajet » connu de l'anti-rejeu "
          "mais sans trajet\n     (piège 2736TCC 11:37:44 / 4886TBU 11:00:17)")
    maintenant_reel = now_local().replace(microsecond=0)
    ev_ts = maintenant_reel - timedelta(minutes=35)     # « Début » il y a 35 min
    ev_fini = maintenant_reel - timedelta(minutes=60)   # cas témoin terminé
    arret_ts = maintenant_reel - timedelta(minutes=50)
    # v1.24 — la garde de mouvement exige une PREUVE de roulage récent
    # (§0quater R2) : le piège historique est un camion qui ROULAIT → ajout
    # d'un point POSITION frais à 25 km/h ; vblip (témoin inverse) n'en a pas.
    mouv_ts = maintenant_reel - timedelta(minutes=5)
    for veh, ts, te, vit in (
            (vtrap, ev_ts, TypeEvenement.DEBUT_MOUVEMENT, 0.0),
            (vtrap, mouv_ts, TypeEvenement.POSITION, 25.0),
            (vblip, ev_ts, TypeEvenement.DEBUT_MOUVEMENT, 0.0),
            (vterm, ev_fini, TypeEvenement.DEBUT_MOUVEMENT, 0.0),
            (vterm, arret_ts, TypeEvenement.ARRET, 0.0)):
        db.add(EvenementGPS(vehicule_id=veh.id, horodatage=ts,
                            latitude=-18.9149, longitude=47.5327,
                            adresse="RN7, Antsirabe II", vitesse=vit,
                            etat_moteur="ON", type_evenement=te,
                            source=SourceEvenement.MZONEX))
    db.commit()
    jour_ev = jour_attribution(ev_ts)
    s_avant = engine.ensure_suivi(db, vtrap, jour_ev)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s_avant.id))
    nettoie_suivis.add(s_avant.id)
    s_temoin = engine.ensure_suivi(db, vterm, jour_attribution(ev_fini))
    db.execute(delete(Trajet).where(Trajet.suivi_id == s_temoin.id))
    nettoie_suivis.add(s_temoin.id)
    s_blip = engine.ensure_suivi(db, vblip, jour_ev)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s_blip.id))
    nettoie_suivis.add(s_blip.id)
    db.commit()

    coll = MZoneXCollector()
    points_rejeu = [
        {"gps_associe": vtrap.gps_associe or vtrap.plaque, "horodatage": ev_ts,
         "lat": -18.9149, "lng": 47.5327, "adresse": "RN7, Antsirabe II",
         "vitesse": 0.0, "moteur": "ON",
         "type_evenement": TypeEvenement.DEBUT_MOUVEMENT},
        {"gps_associe": vtrap.gps_associe or vtrap.plaque,
         "horodatage": mouv_ts,
         "lat": -18.9149, "lng": 47.5327, "adresse": "RN7, Antsirabe II",
         "vitesse": 25.0, "moteur": "ON",
         "type_evenement": TypeEvenement.POSITION},
        {"gps_associe": vblip.gps_associe or vblip.plaque, "horodatage": ev_ts,
         "lat": -18.9149, "lng": 47.5327, "adresse": "RN7, Antsirabe II",
         "vitesse": 0.0, "moteur": "ON",
         "type_evenement": TypeEvenement.DEBUT_MOUVEMENT},
        {"gps_associe": vterm.gps_associe or vterm.plaque, "horodatage": ev_fini,
         "lat": -18.9149, "lng": 47.5327, "adresse": "RN7, Antsirabe II",
         "vitesse": 0.0, "moteur": "ON",
         "type_evenement": TypeEvenement.DEBUT_MOUVEMENT},
        {"gps_associe": vterm.gps_associe or vterm.plaque, "horodatage": arret_ts,
         "lat": -18.9149, "lng": 47.5327, "adresse": "RN7, Antsirabe II",
         "vitesse": 0.0, "moteur": "OFF",
         "type_evenement": TypeEvenement.ARRET},
    ]
    n1 = coll.inserer(points_rejeu)          # 100 % anti-rejeu → réparation seule
    db.expire_all()
    tr = visibles(vtrap, jour_ev)
    check("anti-rejeu court-circuité mais trajet « en cours » RECRÉÉ (auto-réparation)",
          n1 == 1 and len(tr) == 1
          and tr[0].heure_debut == ev_ts
          and tr[0].heure_fin is None
          and tr[0].statut_validation == StatutValidationTrajet.EN_ATTENTE,
          f"inseres={n1} n={len(tr)} "
          f"{[(t.heure_debut, t.heure_fin) for t in tr]}")
    id_rep = tr[0].id if tr else None
    check("cas témoin : « Fin du trajet » postérieure → PAS de réparation "
          "(les trajets terminés relèvent du Niveau 2)",
          len(visibles(vterm, jour_attribution(ev_fini))) == 0)
    check("témoin inverse (v1.24) : « Début » seul à 0 km/h, SANS preuve de "
          "roulage récent (blip de contact 0926TBV/8076TCB du 14/08) → PAS "
          "de ligne fantôme recréée (garde de mouvement §0quinquies)",
          len(visibles(vblip, jour_ev)) == 0,
          f"blip n={len(visibles(vblip, jour_ev))}")

    n2 = coll.inserer(points_rejeu)          # passe suivante : couvert → rien
    db.expire_all()
    tr2 = visibles(vtrap, jour_ev)
    check("idempotent : couverture retrouvée, pas de doublon à la passe suivante",
          n2 == 0 and len(tr2) == 1 and tr2[0].id == id_rep,
          f"inseres={n2} n={len(tr2)}")

    # ================================================================
    print()
finally:
    for sid in nettoie_suivis:
        db.execute(delete(Trajet).where(Trajet.suivi_id == sid))
    db.execute(delete(EvenementGPS).where(
        EvenementGPS.vehicule_id.in_([vtrap.id, vterm.id, vblip.id])))
    db.execute(delete(AuditLog).where(AuditLog.username.like("test-v117%")))
    db.commit()
    db.close()
    for cand in ("/tmp/test_v117.db",):
        try:
            os.unlink(cand)
        except OSError:
            pass

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
