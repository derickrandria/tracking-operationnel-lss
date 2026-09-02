"""Tests v1.9 — « dernière ligne du rapport = EN COURS » (retour métier 01/08/2026).

Rejoue EXACTEMENT les deux captures métier :

1) MZoneX — 3046 TBS (RAZAFITSIHOARANA) : 10 lignes dont 7 manœuvres < 0,3 km.
   Attendu : T1 = 08:01:33 → 08:13:12 (1,1 km) · T2 = 09:49:00 → 10:14:55
   (1,137 + 0,337 = 1,474 km — fusion car pause 10:04:35 → 10:12:11 < 20 min) ;
   la dernière ligne (11:19 → 11:25:52, 0 km) reste « EN COURS » puis est
   REJETÉE à sa clôture (pause ≥ 20 min constatée).

2) CamtrackPro — 5626 TCE-SINOTRUK : 6 lignes invalides + 1 ligne encore
   EN MOUVEMENT (début 11:04:52, fin affichée 11:26:51 = position connue, PAS
   une fin officielle). Attendu : importée PROVISOIRE / EN_ATTENTE avec fin
   PROVISOIRE, jamais déclarée terminée ; mise à jour tant qu'elle grandit ;
   VALIDÉE seulement quand une pause ≥ 20 min est constatée derrière.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v19.db" python3 test_ouverture_v19.py
La base est SUPPRIMÉE à la fin (protection des données production)."""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.database import SessionLocal
from app import engine
from app.models import (AuditLog, StatutSourceTrajet, StatutValidationTrajet,
                        Trajet, Vehicule)
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

seed_si_vide()
migrer_schema()
db = SessionLocal()
jour = datetime.now().date()
base = datetime.combine(jour, datetime.min.time())

mzx = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF")).all()
ctp = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "CAMTRACKPRO", Vehicule.statut == "ACTIF")).all()
v3046, v5626, vlife, v5716 = mzx[0], ctp[0], mzx[1], ctp[1]
print(f"Véhicules test : MZX1={v3046.plaque} · CT={v5626.plaque} "
      f"· MZX2={vlife.plaque} · CT2={v5716.plaque}")

nettoie_suivis, nettoie_trajets = set(), []
for v in (v3046, v5626, vlife, v5716):
    s = engine.ensure_suivi(db, v, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
    nettoie_suivis.add(s.id)
db.commit()


def visibles(veh):
    s = engine.ensure_suivi(db, veh, jour)
    db.expire_all()
    return [t for t in db.scalars(select(Trajet).where(
        Trajet.suivi_id == s.id).order_by(Trajet.numero)).all()
        if t.statut_validation != StatutValidationTrajet.REJETE]


def h(hh, mm, ss=0):
    return base.replace(hour=hh, minute=mm, second=ss)


try:
    # ================================================================
    print("\n[MZoneX 3046 TBS] Rejeu exact de la capture (10 lignes)")
    lignes = [
        ("06:42:50", "07:02:57", 0.005), ("08:01:33", "08:13:12", 1.1),
        ("09:46:37", "09:48:43", 0.0),   ("09:49:00", "10:04:35", 1.137),
        ("10:05:03", "10:09:59", 0.018), ("10:12:11", "10:14:55", 0.337),
        ("10:18:18", "10:20:59", 0.182), ("10:30:06", "10:33:55", 0.102),
        ("10:42:36", "10:48:29", 0.006), ("11:19:13", "11:25:52", 0.0),
    ]
    items = [{"plaque": v3046.plaque,
              "debut": datetime.fromisoformat(f"{jour.isoformat()} {d}"),
              "fin": datetime.fromisoformat(f"{jour.isoformat()} {f}"),
              "distance_km": km, "source": "MZONEX"} for d, f, km in lignes]
    stats = reconcilier_trajets_valides(db, items, username="test-v19",
                                        maintenant=h(11, 31, 30))
    check("6 rejets (manœuvres < 0,3 km ; 10ᵉ ligne encore en cours, non jugée)",
          stats["rejets"] == 6, str(stats))
    check("1 ligne « en cours » importée (ouverts=1)", stats["ouverts"] == 1, str(stats))
    tr = visibles(v3046)
    # v3 AM-2 : les 3 trajets valides publiés gardent chacun leur ligne
    check("4 lignes visibles : 3 trajets publiés + 1 en cours (v3 AM-2), "
          "les 6 rejetés masqués", len(tr) == 4, f"n={len(tr)}")
    t1, t2, t3 = tr[0], tr[1], tr[2]
    check("T1 = 08:01:33 → 08:13:12 (1,1 km) VALIDÉ",
          t1.heure_debut == h(8, 1, 33) and t1.heure_fin == h(8, 13, 12)
          and abs((t1.distance_km or 0) - 1.1) < 1e-6
          and t1.statut_source == StatutSourceTrajet.VALIDE
          and t1.statut_validation == StatutValidationTrajet.VALIDE,
          f"{t1.heure_debut}→{t1.heure_fin} {t1.distance_km}")
    # v1.18 (Référence v2 §5.1/§7, arbitrage métier du 06/08 — supersède
    # v1.17) : la ligne court jusqu'à la fin du DERNIER TRAJET RÉEL — les
    # manœuvres de fin (10:18:18→10:48:29) ne la prolongent plus ; le début
    # reste celui du premier trajet réel (la manœuvre de tête ne décale rien).
    check("T2 = 09:49:00 → 10:04:35, T3 = 10:12:11 → 10:14:55 — chacune sa "
          "ligne (v3 AM-2, fusion abolie)",
          t2.heure_debut == h(9, 49, 0) and t2.heure_fin == h(10, 4, 35)
          and t3.heure_debut == h(10, 12, 11) and t3.heure_fin == h(10, 14, 55),
          f"{t2.heure_debut}→{t2.heure_fin} / {t3.heure_debut}→{t3.heure_fin}")
    check("distances publiées T2 = 1,137 km · T3 = 0,337 km (chacune la "
          "sienne)", abs((t2.distance_km or 0) - 1.137) < 1e-6
          and abs((t3.distance_km or 0) - 0.337) < 1e-6)
    # pause « détail » = prochaine ligne AFFICHÉE (trajet ≥ 0,3 km) ; la grille
    # (lecture chaînée) montre l'écart réel jusqu'à la manœuvre 09:46:37 (1h33m25s)
    check("pause officielle T1 = 1h35m48s (08:13:12 → 09:49:00, prochaine ligne affichée)",
          t1.pause_apres_s == 5748, str(t1.pause_apres_s))
    t4 = tr[3]
    check("ligne 10 EN COURS : début 11:19:13, fin PROVISOIRE 11:25:52, "
          "PROVISOIRE + EN_ATTENTE (jamais déclarée terminée)",
          t4.heure_debut == h(11, 19, 13) and t4.heure_fin == h(11, 25, 52)
          and t4.statut_source == StatutSourceTrajet.PROVISOIRE
          and t4.statut_validation == StatutValidationTrajet.EN_ATTENTE,
          f"{t4.statut_source}/{t4.statut_validation} fin={t4.heure_fin}")

    stats = reconcilier_trajets_valides(db, items, username="test-v19",
                                        maintenant=h(11, 31, 30))
    check("rejeu idempotent : 3 ignorés (T1/T2/T3 inchangés), 0 créé, "
          "4 visibles", stats["ignores"] == 3 and stats["crees"] == 0
          and len(visibles(v3046)) == 4, str(stats))

    # clôture : à 11:55 la pause derrière 11:25:52 dépasse 20 min → jugée
    stats = reconcilier_trajets_valides(db, items, username="test-v19",
                                        maintenant=h(11, 55, 0))
    tr = visibles(v3046)
    check("à 11:55 (pause ≥ 20 min) la manœuvre en cours 0 km est REJETÉE "
          "→ seuls les 3 trajets publiés restent affichés (7 rejets ce cycle)",
          len(tr) == 3 and stats["rejets"] == 7, f"n={len(tr)} rejets={stats['rejets']}")
    s_json = s_suivi(engine.ensure_suivi(db, v3046, jour), engine.get_seuils(db))
    # Réalignement v1.31 (§0undecies E1, 24/08/2026 — prime sur AM-2 pour les
    # arrêts < 20 min) : la coupure T2→T3 ne dure que 7 min 36 s → la GRILLE
    # affiche 2 LIGNES (T2+T3 fusionnés ; la base en garde 3, cf. ci-dessus).
    check("grille/export : 3 trajets réels affichés en 2 LIGNES (E1 v1.31 : "
          "coupure T1→T2 ≈ 1h36 ≥ 20 min, T2→T3 = 7:36 < 20 min fusionnée), "
          "départ officiel 08:01:33 (pas la manœuvre 06:42)",
          s_json["nb_trajets"] == 2
          and (s_json["heure_depart"] or "").endswith("08:01:33")
          and (s_json["trajets"][1]["heure_debut"] or "").endswith("09:49:00")
          and (s_json["trajets"][1]["heure_fin"] or "").endswith("10:14:55")
          and abs((s_json["trajets"][1]["distance_km"] or 0) - 1.474) < 1e-6,
          f"nb={s_json['nb_trajets']} "
          f"lignes={[(t['heure_debut'], t['heure_fin']) for t in s_json['trajets']]}")

    # ================================================================
    print("\n[CamtrackPro 5626] Rejeu exact de la capture (7 lignes, véhicule EN MOUVEMENT)")
    lignes_ct = [
        ("05:10:36", "05:31:51", 0.11, 160), ("05:41:14", "05:58:14", 0.27, 300),
        ("06:11:19", "06:11:29", 0.00, 1),   ("09:15:27", "09:17:37", 0.00, 0),
        ("09:37:08", "09:44:29", 0.15, 170), ("10:56:11", "10:57:51", 0.00, 0),
        ("11:04:52", "11:26:51", 7.74, 1251),   # « en mouvement 0:20:51 »
    ]
    items_ct = [{"plaque": v5626.plaque,
                 "debut": datetime.fromisoformat(f"{jour.isoformat()} {d}"),
                 "fin": datetime.fromisoformat(f"{jour.isoformat()} {f}"),
                 "distance_km": km, "duree_mouvement_s": mv,
                 "source": "CAMTRACKPRO"} for d, f, km, mv in lignes_ct]
    stats = reconcilier_trajets_valides(db, items_ct, username="test-v19",
                                        maintenant=h(11, 32, 0))
    check("6 rejets (distance / « en mouvement » insuffisants)", stats["rejets"] == 6,
          str(stats))
    check("aucun trajet VALIDÉ tant que le véhicule roule (crees=0, remplaces=0)",
          stats["crees"] == 0 and stats["remplaces"] == 0, str(stats))
    tr = visibles(v5626)
    check("1 seul trajet affiché : 11:04:52 → fin PROVISOIRE 11:26:51 (7,74 km)",
          len(tr) == 1 and tr[0].heure_debut == h(11, 4, 52)
          and tr[0].heure_fin == h(11, 26, 51)
          and abs((tr[0].distance_km or 0) - 7.74) < 1e-6,
          f"{[(t.heure_debut, t.heure_fin) for t in tr]}")
    check("statut PROVISOIRE + EN_ATTENTE (jamais déclaré terminé)",
          tr[0].statut_source == StatutSourceTrajet.PROVISOIRE
          and tr[0].statut_validation == StatutValidationTrajet.EN_ATTENTE,
          f"{tr[0].statut_source}/{tr[0].statut_validation}")
    id_encours = tr[0].id

    # grandit toujours à la sync suivante → MÊME enregistrement mis à jour
    items_ct[-1]["fin"] = h(11, 44, 32)
    items_ct[-1]["distance_km"] = 12.05
    items_ct[-1]["duree_mouvement_s"] = 2400
    stats = reconcilier_trajets_valides(db, items_ct, username="test-v19",
                                        maintenant=h(11, 45, 0))
    tr = visibles(v5626)
    check("croissance : MÊME trajet (pas de doublon), fin PROVISOIRE 11:44:32, 12,05 km",
          len(tr) == 1 and tr[0].id == id_encours
          and tr[0].heure_fin == h(11, 44, 32)
          and abs((tr[0].distance_km or 0) - 12.05) < 1e-6
          and tr[0].statut_validation == StatutValidationTrajet.EN_ATTENTE,
          f"fin={tr[0].heure_fin} km={tr[0].distance_km}")

    # le véhicule s'arrête : 20 min après, la fin du rapport devient OFFICIELLE
    items_ct[-1]["fin"] = h(11, 26, 51)
    items_ct[-1]["distance_km"] = 7.74
    items_ct[-1]["duree_mouvement_s"] = 1251
    stats = reconcilier_trajets_valides(db, items_ct, username="test-v19",
                                        maintenant=h(12, 10, 0))
    tr = visibles(v5626)
    check("clôture (pause ≥ 20 min constatée) : fin OFFICIELLE 11:26:51, VALIDÉ",
          stats["remplaces"] == 1 and len(tr) == 1
          and tr[0].heure_fin == h(11, 26, 51)
          and tr[0].statut_source == StatutSourceTrajet.VALIDE
          and tr[0].statut_validation == StatutValidationTrajet.VALIDE,
          f"{stats} fin={tr[0].heure_fin} {tr[0].statut_validation}")

    # ================================================================
    print("\n[Cycle de vie MZoneX] ouvert → validé → RÉOUVERT (fusion < 20 min) → revalidé")
    A = {"plaque": vlife.plaque, "debut": h(9, 0, 0), "fin": h(9, 30, 0),
         "distance_km": 5.0, "source": "MZONEX"}
    stats = reconcilier_trajets_valides(db, [dict(A)], username="test-v19",
                                        maintenant=h(9, 31, 0))
    tr = visibles(vlife)
    check("09:31 — ligne seule encore chaude → PROVISOIRE / EN_ATTENTE",
          stats["ouverts"] == 1 and len(tr) == 1
          and tr[0].statut_validation == StatutValidationTrajet.EN_ATTENTE, str(stats))
    id_a = tr[0].id
    stats = reconcilier_trajets_valides(db, [dict(A)], username="test-v19",
                                        maintenant=h(9, 55, 0))
    db.expire_all()
    t = db.get(Trajet, id_a)
    check("09:55 — pause ≥ 20 min derrière 09:30 → fin OFFICIELLE, VALIDÉ",
          stats["remplaces"] == 1 and t.statut_source == StatutSourceTrajet.VALIDE
          and t.heure_fin == h(9, 30, 0), f"{stats} {t.heure_fin}")

    B = {"plaque": vlife.plaque, "debut": h(9, 40, 0), "fin": h(10, 4, 50),
         "distance_km": 4.2, "source": "MZONEX"}
    stats = reconcilier_trajets_valides(db, [dict(A), dict(B)], username="test-v19",
                                        maintenant=h(10, 5, 0))
    db.expire_all()
    t = db.get(Trajet, id_a)
    # v3 AM-2 : la reprise 09:40 n'est PLUS soudée — elle vit sa propre
    # ligne ; A reste clôturée à 09:30 (VALIDÉ, conservée), rien ne bouge
    check("10:05 — v3 : A intacte (09:00→09:30 VALIDÉ conservé) et B ouvre "
          "SA ligne (09:40→10:04:50, PROVISOIRE) — aucune fusion, aucune "
          "réouverture",
          t.id == id_a and t.heure_fin == h(9, 30, 0)
          and t.statut_source == StatutSourceTrajet.VALIDE
          and abs((t.distance_km or 0) - 5.0) < 1e-6,
          f"{t.heure_fin} {t.distance_km} {t.statut_validation}")
    tr_c = visibles(vlife)
    check("v3 : 2 lignes visibles — A validée puis B en cours (fusion "
          "< 20 min abolie)",
          len(tr_c) == 2
          and tr_c[1].heure_debut == h(9, 40, 0)
          and tr_c[1].heure_fin == h(10, 4, 50)
          and tr_c[1].statut_validation == StatutValidationTrajet.EN_ATTENTE,
          f"{[(t.heure_debut, t.heure_fin) for t in tr_c]}")
    check("v3 : AUCUN audit « trajet.reouverture » (mécanisme aboli)",
          db.scalar(select(AuditLog).where(AuditLog.action == "trajet.reouverture",
                                           AuditLog.entite_id == id_a)) is None)
    stats = reconcilier_trajets_valides(db, [dict(A), dict(B)], username="test-v19",
                                        maintenant=h(10, 30, 0))
    db.expire_all()
    t = db.get(Trajet, id_a)
    tb = [x for x in visibles(vlife) if x.heure_debut == h(9, 40, 0)]
    check("10:30 — v3 : B officialisée seule (09:40 → 10:04:50 VALIDÉ, "
          "4,2 km) — A inchangée 09:00→09:30",
          len(tb) == 1 and tb[0].statut_source == StatutSourceTrajet.VALIDE
          and tb[0].statut_validation == StatutValidationTrajet.VALIDE
          and db.get(Trajet, id_a).heure_fin == h(9, 30, 0),
          f"{[(x.heure_debut, x.heure_fin, x.statut_validation) for x in visibles(vlife)]}")

    # donnée officielle tardive plus complète → mise à jour du MÊME VALIDÉ
    A2 = dict(A); A2["fin"] = h(10, 14, 55); A2["distance_km"] = 9.35
    stats = reconcilier_trajets_valides(db, [A2], username="test-v19",
                                        maintenant=h(11, 5, 0))
    db.expire_all()
    t = db.get(Trajet, id_a)
    check("VALIDÉ mis à jour (fin/distance étendues), sans doublon ni nouvelle ligne",
          stats["maj"] == 1 and t.heure_fin == h(10, 14, 55)
          and abs((t.distance_km or 0) - 9.35) < 1e-6,
          f"{stats} {t.heure_fin}")
    stats = reconcilier_trajets_valides(db, [A2], username="test-v19",
                                        maintenant=h(11, 6, 0))
    check("rejeu strictement idempotent (ignores=1, maj=0)",
          stats["ignores"] == 1 and stats["maj"] == 0, str(stats))

    # ================================================================
    print("\n[v1.10 · 0916 TBV] Guérison : lignes du matin absorbées sans doublon")
    print("  (écran réel : T1 09:07→10:14 erroné + T2 ouvert ; journal officiel complet)")
    s = engine.ensure_suivi(db, v3046, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id)); db.commit()
    # état erroné constaté sur vos captures (écrit par l'ancien flux plafonné)
    e1 = Trajet(suivi_id=s.id, numero=1, heure_debut=h(6, 7, 52), heure_fin=h(7, 14, 6),
                statut_source=StatutSourceTrajet.VALIDE, source_plateforme="MZONEX",
                distance_km=31.982, statut_validation=StatutValidationTrajet.VALIDE)
    e2 = Trajet(suivi_id=s.id, numero=2, heure_debut=h(7, 51, 46),
                heure_fin=h(8, 0, 0), statut_source=StatutSourceTrajet.PROVISOIRE,
                source_plateforme="MZONEX", distance_km=12.345,
                statut_validation=StatutValidationTrajet.EN_ATTENTE)
    db.add_all([e1, e2]); db.commit()
    id_err1 = e1.id
    lignes0916 = [
        ("02:28:05", "02:33:23", 0.0), ("02:53:09", "03:52:19", 24.115),
        ("04:12:35", "04:32:54", 6.259), ("04:52:40", "06:04:35", 33.794),
        ("06:07:52", "07:14:06", 31.982), ("07:51:46", "08:26:08", 12.345),
        ("08:27:53", "10:13:15", 43.206),
    ]
    items0916 = [{"plaque": v3046.plaque,
                  "debut": datetime.fromisoformat(f"{jour.isoformat()} {d}"),
                  "fin": datetime.fromisoformat(f"{jour.isoformat()} {f}"),
                  "distance_km": km, "source": "MZONEX"}
                 for d, f, km in lignes0916]
    stats = reconcilier_trajets_valides(db, items0916, username="test-v110",
                                        maintenant=h(10, 20, 0))
    tr = visibles(v3046)
    # v3 AM-2 (arbitrage LSS 22/08/2026) : PLUS de fusion d'affichage — les
    # 6 trajets valides publiés gardent CHACUN leur ligne ; la manœuvre
    # reste rejetée (invariant §2/§7 conservé)
    check("manœuvre 0 km rejetée ; 3 lignes publiées créées + l'ancienne "
          "ouverte rapprochée (remplaces) + la nouvelle encore en cours",
          stats["rejets"] == 1 and stats["crees"] == 3 and stats["ouverts"] == 1
          and stats["remplaces"] == 1, str(stats))
    check("6 lignes visibles = 6 trajets publiés (fusion abolie, v3)",
          len(tr) == 6, f"n={len(tr)}")
    a, b, c = tr[0], tr[1], tr[2]
    check("T1 officiel = 02:53:09 → 03:52:19 (24,115 km) VALIDÉ",
          a.heure_debut == h(2, 53, 9) and a.heure_fin == h(3, 52, 19)
          and abs((a.distance_km or 0) - 24.115) < 1e-6)
    check("T2 = 04:12:35 → 04:32:54 (6,259 km) — sa propre ligne (v3 AM-2)",
          b.heure_debut == h(4, 12, 35) and b.heure_fin == h(4, 32, 54)
          and abs((b.distance_km or 0) - 6.259) < 1e-6,
          f"{b.heure_debut}→{b.heure_fin} {b.distance_km}")
    check("T3 = 04:52:40 → 06:04:35 (33,794 km)",
          c.heure_debut == h(4, 52, 40) and c.heure_fin == h(6, 4, 35)
          and abs((c.distance_km or 0) - 33.794) < 1e-6)
    check("T4 : l'ancienne ligne de l'état erroné RAPPROCHÉE (même plage, "
          "id conservé) 06:07:52 → 07:14:06",
          tr[3].id == id_err1 and tr[3].heure_debut == h(6, 7, 52)
          and tr[3].heure_fin == h(7, 14, 6)
          and abs((tr[3].distance_km or 0) - 31.982) < 1e-6)
    check("pauses réelles brutes : 20m16s après T1, 37m40s après T4",
          a.pause_apres_s == 1216 and tr[3].pause_apres_s == 2260,
          f"{a.pause_apres_s}/{tr[3].pause_apres_s}")
    check("T5 rapprochée = 07:51:46 → 08:26:08 (12,345 km) VALIDÉ ; "
          "T6 = ligne en cours 08:27:53 → fin PROVISOIRE 10:13:15 (43,206 km)",
          tr[4].heure_debut == h(7, 51, 46) and tr[4].heure_fin == h(8, 26, 8)
          and tr[5].heure_debut == h(8, 27, 53)
          and tr[5].heure_fin == h(10, 13, 15)
          and tr[5].statut_validation == StatutValidationTrajet.EN_ATTENTE,
          f"{[(t.heure_debut, t.heure_fin) for t in tr]}")
    stats = reconcilier_trajets_valides(db, items0916, username="test-v110",
                                        maintenant=h(10, 21, 0))
    check("rejeu idempotent : toujours 6 lignes publiées, 0 créé",
          stats["crees"] == 0 and len(visibles(v3046)) == 6, str(stats))
    aud = db.scalar(select(AuditLog).where(AuditLog.action == "trajet.fusion_historique"))
    check("v3 : AUCUNE « fusion_historique » — chaque trajet publié garde sa "
          "ligne", aud is None)

    # ================================================================
    print("\n[v1.10 · 4296 TCC] Guérison : le « trajet géant » redevient les 2 vrais trajets")
    s = engine.ensure_suivi(db, v5626, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id)); db.commit()
    geant = Trajet(suivi_id=s.id, numero=1, heure_debut=h(5, 44, 8),
                   heure_fin=h(12, 58, 30), statut_source=StatutSourceTrajet.VALIDE,
                   source_plateforme="CAMTRACKPRO", distance_km=84.5,
                   statut_validation=StatutValidationTrajet.VALIDE)
    db.add(geant); db.commit(); id_geant = geant.id
    items4296 = [
        {"plaque": v5626.plaque, "debut": h(5, 44, 8), "fin": h(8, 29, 50),
         "distance_km": 14.60, "duree_mouvement_s": 6466, "source": "CAMTRACKPRO"},
        {"plaque": v5626.plaque, "debut": h(9, 9, 35), "fin": h(12, 58, 30),
         "distance_km": 69.90, "duree_mouvement_s": 12654, "source": "CAMTRACKPRO"},
    ]
    stats = reconcilier_trajets_valides(db, items4296, username="test-v110",
                                        maintenant=h(13, 40, 0))
    tr = visibles(v5626)
    check("2 vrais trajets affichés (le géant corrigé + T2 créé)",
          len(tr) == 2 and stats["maj"] == 1 and stats["crees"] == 1, f"{stats} n={len(tr)}")
    a, b = tr
    check("T1 corrigé EN PLACE : 05:44:08 → 08:29:50 (14,60 km), même enregistrement",
          a.id == id_geant and a.heure_fin == h(8, 29, 50)
          and abs((a.distance_km or 0) - 14.60) < 1e-6,
          f"{a.heure_fin} {a.distance_km}")
    check("T2 = 09:09:35 → 12:58:30 (69,90 km) séparé (pause 39m45s ≥ 20 min)",
          b.heure_debut == h(9, 9, 35) and b.heure_fin == h(12, 58, 30)
          and a.pause_apres_s == 2385,
          f"{b.heure_debut}→{b.heure_fin} pause={a.pause_apres_s}")

    # ================================================================
    print("\n[v1.10 · 5716 TBS] Guérison : géant EN COURS rejeté, vrais trajets créés")
    s = engine.ensure_suivi(db, v5716, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id)); db.commit()
    geant2 = Trajet(suivi_id=s.id, numero=1, heure_debut=h(5, 5, 10),
                    heure_fin=h(13, 37, 0), statut_source=StatutSourceTrajet.PROVISOIRE,
                    source_plateforme="CAMTRACKPRO", distance_km=7.0,
                    statut_validation=StatutValidationTrajet.EN_ATTENTE)
    db.add(geant2); db.commit()
    lignes5716 = [
        ("05:05:10", "05:09:50", 0.03, 50), ("05:29:39", "09:47:54", 103.58, 14815),
        ("10:26:22", "13:21:08", 86.86, 10176), ("13:34:53", "13:46:40", 0.99, 602),
    ]
    items5716 = [{"plaque": v5716.plaque,
                  "debut": datetime.fromisoformat(f"{jour.isoformat()} {d}"),
                  "fin": datetime.fromisoformat(f"{jour.isoformat()} {f}"),
                  "distance_km": km, "duree_mouvement_s": mv,
                  "source": "CAMTRACKPRO"} for d, f, km, mv in lignes5716]
    stats = reconcilier_trajets_valides(db, items5716, username="test-v110",
                                        maintenant=h(13, 47, 0))
    tr = visibles(v5716)
    db.expire_all()
    geant2b = db.get(Trajet, geant2.id)
    check("géant fantôme (début = manœuvre 0,03 km) marqué REJETÉ, masqué",
          geant2b.statut_validation == StatutValidationTrajet.REJETE
          and all(t.id != geant2.id for t in tr))
    check("T1 = 05:29:39 → 09:47:54 (103,58 km) VALIDÉ",
          any(t.heure_debut == h(5, 29, 39) and t.heure_fin == h(9, 47, 54)
              and abs((t.distance_km or 0) - 103.58) < 1e-6 for t in tr))
    check("v3 : T2 VALIDÉ 10:26:22 → 13:21:08 (86,86 km, pause ≥ 20 min "
          "constatée) et R4 GARDE SA LIGNE 13:34:53 → 13:46:40 EN_ATTENTE "
          "(fusion abolie)",
          any(t.heure_debut == h(10, 26, 22) and t.heure_fin == h(13, 21, 8)
              and t.statut_validation == StatutValidationTrajet.VALIDE for t in tr)
          and any(t.heure_debut == h(13, 34, 53) and t.heure_fin == h(13, 46, 40)
              and t.statut_validation == StatutValidationTrajet.EN_ATTENTE for t in tr),
          f"{[(t.heure_debut, t.heure_fin, t.statut_validation) for t in tr]}")
    # clôture v1.18 (arbitrage C du 06/08 — règle « en mouvement » SUPPRIMÉE,
    # §11.2 v2 : distance seule) : R4 (0,99 km ≥ 0,3) est VALIDE ; la pause de
    # 13m45s < 20 min soude R4 à T2 → la fin officielle de T2 RESTE 13:46:40
    stats = reconcilier_trajets_valides(db, items5716, username="test-v110",
                                        maintenant=h(14, 10, 0))
    tr = visibles(v5716)
    check("v3 clôture : R4 VALIDE à part entière (0,99 km, sa ligne) — 3 "
          "lignes validées : T1, T2 (10:26:22 → 13:21:08, 86,86 km) et R4",
          len(tr) == 3 and any(
              t.heure_debut == h(10, 26, 22) and t.heure_fin == h(13, 21, 8)
              and abs((t.distance_km or 0) - 86.86) < 1e-6
              and t.statut_validation == StatutValidationTrajet.VALIDE for t in tr)
          and any(t.heure_debut == h(13, 34, 53) and t.heure_fin == h(13, 46, 40)
              and abs((t.distance_km or 0) - 0.99) < 1e-6
              and t.statut_validation == StatutValidationTrajet.VALIDE for t in tr),
          f"{[(t.heure_fin, t.distance_km, t.statut_validation) for t in tr]}")

    # ================================================================
    print("\n[v1.11 · 0916/4736/7946] Balai anti-fantômes : doublons, "
          "sous-intervalles et horaires « fin avant début » purgés")
    print("  (rejoue vos écrans de 16:13 : T4 imbriquée, doublons mêmes fins, "
          "spans à l'envers — le trajet moteur VIVANT est épargné)")
    s = engine.ensure_suivi(db, v3046, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id)); db.commit()
    # journée officielle déjà correcte + VÉRITABLES fantômes des captures
    graines = [
        # A/B/C : les 3 vrais trajets (valeurs officielles du portail)
        dict(debut=h(2, 53, 9),  fin=h(3, 52, 19),  km=24.115, src=StatutSourceTrajet.VALIDE),
        dict(debut=h(4, 12, 35), fin=h(7, 14, 6),   km=72.035, src=StatutSourceTrajet.VALIDE),
        dict(debut=h(7, 51, 46), fin=h(10, 13, 15), km=55.551, src=StatutSourceTrajet.VALIDE),
        # fantôme 1 : T4 imbriquée du 0916 (08:27:53→10:13:15 ⊂ C, même fin)
        dict(debut=h(8, 27, 53), fin=h(10, 13, 15), km=43.206, src=StatutSourceTrajet.VALIDE),
        # fantôme 2 : horaire corrompu « fin avant début » (0936/7946/0926)
        dict(debut=h(15, 33, 0), fin=h(14, 57, 0),  km=2.5,    src=StatutSourceTrajet.VALIDE),
        # fantôme 3 : ancien trajet moteur PÉRIMÉ contenu dans B (4736)
        dict(debut=h(5, 40, 0),  fin=h(6, 30, 0),   km=9.0,
             src=StatutSourceTrajet.PROVISOIRE),
    ]
    # trajet moteur VIVANT (fin provisoire fraîche) : doit être ÉPARGNÉ
    vivant = dict(debut=h(16, 5, 0), fin=h(16, 11, 0), km=4.2,
                  src=StatutSourceTrajet.PROVISOIRE)
    gardes = []
    for i, g in enumerate(graines, start=1):
        t = Trajet(suivi_id=s.id, numero=i, heure_debut=g["debut"],
                   heure_fin=g["fin"], statut_source=g["src"],
                   source_plateforme="MZONEX", distance_km=g["km"],
                   statut_validation=(StatutValidationTrajet.VALIDE
                                      if g["src"] == StatutSourceTrajet.VALIDE
                                      else StatutValidationTrajet.EN_ATTENTE))
        db.add(t); gardes.append(t)
    t_vivant = Trajet(suivi_id=s.id, numero=99, heure_debut=vivant["debut"],
                      heure_fin=vivant["fin"], statut_source=StatutSourceTrajet.PROVISOIRE,
                      source_plateforme="MZONEX", distance_km=vivant["km"],
                      statut_validation=StatutValidationTrajet.EN_ATTENTE)
    db.add(t_vivant); db.commit()
    id_fantomes = [gardes[3].id, gardes[4].id, gardes[5].id]
    id_vivant = t_vivant.id
    # les lignes officielles reviennent au cycle (7 trajets portail, manœuvre
    # 0 km rejetée en amont) — maintenant TOUTES clôturées (16:12)
    lignes1111 = [
        ("02:53:09", "03:52:19", 24.115), ("04:12:35", "04:32:54", 6.259),
        ("04:52:40", "06:04:35", 33.794), ("06:07:52", "07:14:06", 31.982),
        ("07:51:46", "08:26:08", 12.345), ("08:27:53", "10:13:15", 43.206),
    ]
    items1111 = [{"plaque": v3046.plaque,
                  "debut": datetime.fromisoformat(f"{jour.isoformat()} {d}"),
                  "fin": datetime.fromisoformat(f"{jour.isoformat()} {f}"),
                  "distance_km": km, "source": "MZONEX"}
                 for d, f, km in lignes1111]
    stats = reconcilier_trajets_valides(db, items1111, username="test-v111",
                                        maintenant=h(16, 12, 0))
    tr = visibles(v3046)
    # v3 AM-2 : chaque fragment publié garde sa ligne → les « vrais trajets »
    # sont les 6 lignes officielles ; le fantôme T4 imbriqué (08:27:53→
    # 10:13:15) coïncide avec le trajet publié id 6 → il devient ligne
    # officielle de droit ; le corrompu (fin avant début) est purgé et le
    # moteur périmé (contenu dans le trajet officiel 2) aussi
    check("v3 : corrompu + périmé purgés ; le fantôme T4 (== ligne publiée "
          "08:27:53→10:13:15) devient officiel",
          stats["epures"] + stats["corrections"] >= 2, str(stats))
    check("v3 : 7 lignes = 6 officielles de droit + le trajet moteur VIVANT "
          "(épargné)", len(tr) == 7 and any(t.id == id_vivant for t in tr),
          f"n={len(tr)} vivant={any(t.id == id_vivant for t in tr)}")
    check("v3 : le corrompu (fin avant début) et le périmé ne subsistent pas",
          gardes[4].id not in [t.id for t in tr]
          and gardes[5].id not in [t.id for t in tr])
    check("numérotation recalée 1..7",
          [t.numero for t in tr] == [1, 2, 3, 4, 5, 6, 7],
          str([t.numero for t in tr]))
    check("pause réelle après la ligne 07:14:06 recalculée = 37m40s (2260 s)",
          tr[3].pause_apres_s == 2260, str(tr[3].pause_apres_s))
    aud_e = [a for a in db.scalars(select(AuditLog).where(
        AuditLog.action.in_(["trajet.orphelin_purge", "trajet.structure_corrige"]),
        AuditLog.entite_id.in_(id_fantomes[1:]))).all()]
    check("v3 : purges du corrompu + périmé tracées en audit",
          len(aud_e) >= 1, str([a.action for a in aud_e]))
    stats = reconcilier_trajets_valides(db, items1111, username="test-v111",
                                        maintenant=h(16, 13, 0))
    check("cycle suivant idempotent : grille inchangée (7 lignes)",
          stats["epures"] == 0 and len(visibles(v3046)) == 7, str(stats))

    # ----------------------------------------------------------------
    print("\n[v1.11 · 0926/8086] Doublon même horaire + même début + corrompu")
    s = engine.ensure_suivi(db, vlife, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id)); db.commit()
    graines2 = [
        dict(debut=h(7, 15, 0),  fin=h(8, 12, 0),  km=10.0),
        dict(debut=h(9, 7, 0),   fin=h(9, 28, 0),  km=5.0),
        dict(debut=h(11, 42, 0), fin=h(12, 17, 0), km=8.0),
    ]
    for i, g in enumerate(graines2, start=1):
        db.add(Trajet(suivi_id=s.id, numero=i, heure_debut=g["debut"],
                      heure_fin=g["fin"], statut_source=StatutSourceTrajet.VALIDE,
                      source_plateforme="MZONEX", distance_km=g["km"],
                      statut_validation=StatutValidationTrajet.VALIDE))
    # fantômes : doublon exact du T3, même début raccourci (T1), corrompu (T4)
    db.add(Trajet(suivi_id=s.id, numero=4, heure_debut=h(11, 42, 0),
                  heure_fin=h(12, 17, 0), statut_source=StatutSourceTrajet.PROVISOIRE,
                  source_plateforme="MZONEX", distance_km=8.0,
                  statut_validation=StatutValidationTrajet.EN_ATTENTE))
    db.add(Trajet(suivi_id=s.id, numero=5, heure_debut=h(7, 15, 0),
                  heure_fin=h(7, 50, 0), statut_source=StatutSourceTrajet.VALIDE,
                  source_plateforme="MZONEX", distance_km=6.0,
                  statut_validation=StatutValidationTrajet.VALIDE))
    db.add(Trajet(suivi_id=s.id, numero=6, heure_debut=h(13, 6, 0),
                  heure_fin=h(12, 17, 0), statut_source=StatutSourceTrajet.VALIDE,
                  source_plateforme="MZONEX", distance_km=1.0,
                  statut_validation=StatutValidationTrajet.VALIDE))
    db.commit()
    items2 = [{"plaque": vlife.plaque,
               "debut": datetime.fromisoformat(f"{jour.isoformat()} {d}"),
               "fin": datetime.fromisoformat(f"{jour.isoformat()} {f}"),
               "distance_km": km, "source": "MZONEX"}
              for d, f, km in [("07:15:00", "08:12:00", 10.0),
                               ("09:07:00", "09:28:00", 5.0),
                               ("11:42:00", "12:17:00", 8.0)]]
    stats = reconcilier_trajets_valides(db, items2, username="test-v111",
                                        maintenant=h(16, 15, 0))
    tr = visibles(vlife)
    check("v3 : doublon même horaire + même-début raccourci + corrompu — "
          "3 purgés (épures + corrections structure)",
          stats["epures"] + stats["corrections"] == 3, str(stats))
    check("affichage sain : exactement les 3 trajets officiels",
          len(tr) == 3
          and [t.heure_debut for t in tr] == [h(7, 15, 0), h(9, 7, 0), h(11, 42, 0)],
          f"{[(t.heure_debut, t.heure_fin) for t in tr]}")
    check("pauses recalculées : 55 min (3300 s) puis 2h14 (8040 s)",
          tr[0].pause_apres_s == 3300 and tr[1].pause_apres_s == 8040,
          f"{tr[0].pause_apres_s}/{tr[1].pause_apres_s}")

    # ----------------------------------------------------------------
    print("\n[v1.12 · CONTRAT GRILLE STRICT] fusion < 20 min, recouvrement, "
          "résidus face à l'officiel — l'officiel JAMAIS muté")
    s = engine.ensure_suivi(db, v5716, jour)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id)); db.commit()
    # débris : d1/d2 (pause 10 min → FUSION) · d3 gardé · d4 recouvrant d3 ·
    # d6 empiète sur l'officiel · d5 démarre 10 min après l'officiel
    graines3 = [dict(d=h(7, 0, 0),  f=h(8, 0, 0),  km=10.0),
                dict(d=h(8, 10, 0), f=h(9, 0, 0),  km=5.0),
                dict(d=h(9, 30, 0), f=h(9, 50, 0), km=2.0, stale_pause=999),
                dict(d=h(9, 40, 0), f=h(9, 45, 0), km=1.0),
                dict(d=h(11, 50, 0), f=h(12, 30, 0), km=4.0),
                dict(d=h(13, 10, 0), f=h(13, 40, 0), km=8.0)]
    for i, g in enumerate(graines3, start=1):
        db.add(Trajet(suivi_id=s.id, numero=i, heure_debut=g["d"],
                      heure_fin=g["f"],
                      pause_apres_s=g.get("stale_pause", 0),
                      statut_source=StatutSourceTrajet.VALIDE,
                      source_plateforme="CAMTRACKPRO", distance_km=g["km"],
                      statut_validation=StatutValidationTrajet.VALIDE))
    db.commit()
    stats = reconcilier_trajets_valides(
        db, [{"plaque": v5716.plaque,
              "debut": h(12, 0, 0), "fin": h(13, 0, 0), "distance_km": 40.0,
              "duree_mouvement_s": 2400, "source": "CAMTRACKPRO"}],
        username="test-v112", maintenant=h(16, 45, 0))
    tr = visibles(v5716)
    # v3 AM-2 : d1/d2 ne fusionnent PLUS — débris d1 et d2 gardent chacun
    # leur ligne ; seules les purges anti-doublon (recouvrement d4, résidus
    # d6 + d5 face à l'officiel) demeurent
    check("v3 : 1 fusion en MOINS — 3 corrections de structure (recouvrement "
          "+ 2 résidus), officiel créé",
          stats["corrections"] == 3 and stats["crees"] == 1, str(stats))
    check("v3 : grille = 4 lignes strictement croissantes (d1, d2, d3, "
          "officiel) — chaque trajet sa ligne",
          len(tr) == 4
          and all(tr[k].heure_fin < tr[k + 1].heure_debut for k in range(3)),
          f"{[(t.heure_debut, t.heure_fin) for t in tr]}")
    check("v3 : d1 (07:00→08:00, 10 km) et d2 (08:10→09:00, 5 km) NON "
          "fusionnés, chacun sa ligne",
          tr[0].heure_fin == h(8, 0, 0) and abs((tr[0].distance_km or 0) - 10.0) < 1e-6
          and tr[1].heure_debut == h(8, 10, 0) and tr[1].heure_fin == h(9, 0, 0)
          and abs((tr[1].distance_km or 0) - 5.0) < 1e-6,
          f"{tr[0].heure_fin} {tr[0].distance_km} / {tr[1].heure_fin} {tr[1].distance_km}")
    check("ligne OFFICIELLE intacte (jamais mutée) : 12:00 → 13:00, 40 km",
          tr[3].heure_debut == h(12, 0, 0) and tr[3].heure_fin == h(13, 0, 0)
          and abs((tr[3].distance_km or 0) - 40.0) < 1e-6)
    check("pauses réelles brutes recalculées : 10 min (600 s), 30 min, "
          "2h10 ; dernière = 0",
          [t.pause_apres_s for t in tr] == [600, 1800, 7800, 0],
          f"{[t.pause_apres_s for t in tr]}")
    aud_c = [a for a in db.scalars(select(AuditLog).where(
        AuditLog.action == "trajet.structure_corrige")).all()]
    check("v3 : corrections tracées (0 fusion abolie, 1 recouvrement, "
          "résidus)",
          sum(1 for a in aud_c
              if (a.details or {}).get("action") == "fusion_pause_courte") == 0
          and sum(1 for a in aud_c
                  if (a.details or {}).get("action") == "purge_recouvrement") >= 1
          and sum(1 for a in aud_c
                  if (a.details or {}).get("action") == "purge_residu") >= 1,
          str([(a.details or {}).get("action") for a in aud_c]))
    stats = reconcilier_trajets_valides(
        db, [{"plaque": v5716.plaque,
              "debut": h(12, 0, 0), "fin": h(13, 0, 0), "distance_km": 40.0,
              "duree_mouvement_s": 2400, "source": "CAMTRACKPRO"}],
        username="test-v112", maintenant=h(16, 46, 0))
    check("cycle suivant idempotent : 0 correction, grille inchangée (4 lignes)",
          stats["corrections"] == 0 and len(visibles(v5716)) == 4, str(stats))

finally:
    # ---------------- nettoyage ----------------
    db.rollback()
    for tid in nettoie_trajets:
        db.execute(delete(Trajet).where(Trajet.id == tid))
    for sid in nettoie_suivis:
        db.execute(delete(Trajet).where(Trajet.suivi_id == sid))
        db.execute(delete(AuditLog).where(AuditLog.entite == "trajet"))
        from app.models import SuiviJournalier
        db.execute(delete(SuiviJournalier).where(SuiviJournalier.id == sid))
    db.commit()
    db.close()

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
