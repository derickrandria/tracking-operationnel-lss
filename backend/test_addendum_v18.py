"""Tests v1.14/v1.18 — attribution au jour du début, pré-consolidation,
durée temps réel (CA-1 … CA-6). Alignés Référence v2 §8/§9 (ARBITRAGE B du
06/08/2026 : bascule à 01h00 PARTOUT — supersède la v1.14 qui disait 02h00).

Règle appliquée (§4.2 + §5 du document, cohérente avec CA-1/CA-2) :
  jour d'un trajet = DATE(heure_debut), SAUF début entre 00h00 et 01h59 → VEILLE.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v18.db" python3 test_addendum_v18.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app.config import jour_attribution, now_local
from app.database import SessionLocal
from app import daily, engine
from app.models import (HistoriqueJournalier, StatutSourceTrajet,
                        StatutValidationTrajet, SuiviJournalier, Trajet,
                        Vehicule)
from app.reconciliation import reconcilier_trajets_valides
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.serializers import ETAT_OFFICIEL, journee_suivi, s_suivi
from app.exporters import _fmt_duree_txt, _valeurs_suivi

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

veille = now_local().date() - timedelta(days=1)
jour = now_local().date()
bascule = datetime.combine(jour, datetime.min.time())                    # minuit pile (v3)
cloture_veille = datetime.combine(veille, datetime.min.time()) + timedelta(seconds=86399)  # 23:59:59
apres_bascule = bascule + timedelta(minutes=30)


def h(hh, mm, ss=0, ref=jour):
    return datetime(ref.year, ref.month, ref.day, hh, mm, ss)


veh = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF").order_by(
        Vehicule.plaque)).first()
print(f"Véhicule test : {veh.plaque} · veille={veille} · jour={jour}")

for j in (veille, jour):
    s = engine.ensure_suivi(db, veh, j)
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
db.commit()


def trajets_du(j):
    s = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == veh.id, SuiviJournalier.date_jour == j))
    return [] if s is None else list(db.scalars(select(Trajet).where(
        Trajet.suivi_id == s.id).order_by(Trajet.numero)).all())


try:
    # ================================================================
    print("\n[CA-1] v3 AM-3/C1 : split minuit (23h40 veille → 00h50 aujourd'hui)")
    engine.ingest_event(db, veh, h(23, 40, ref=veille), -18.88, 47.51,
                        "Base LSS", 42.0, "ON")
    engine.ingest_event(db, veh, h(23, 55, ref=veille), -18.86, 47.52,
                        "RN2 pk 4", 55.0, "ON")
    ts = trajets_du(veille)
    check("trajet ouvert sur la veille avant minuit",
          len(ts) == 1 and ts[0].heure_debut == h(23, 40, ref=veille)
          and ts[0].heure_fin is None)
    # la consolidation de la veille (23:59:59, v3) SPLIT l'en-cours : la fin
    # = dernier signal prouvé (23:55) — jamais au-delà du silence radio
    n_v3 = daily.consolider_jour(db, veille, maintenant=apres_bascule)
    ts = trajets_du(veille)
    check("v3 : segment A fermé au dernier signal 23:55 et OFFICIALISÉ",
          len(ts) == 1 and ts[0].heure_fin == h(23, 55, ref=veille)
          and ts[0].statut_source == StatutSourceTrajet.VALIDE
          and n_v3 == 1, f"{[(t.heure_debut, t.heure_fin) for t in ts]} n={n_v3}")
    # 00h30 (jour civil SUIVANT) : le trajet CONTINUE → segment B d'aujourd'hui
    engine.ingest_event(db, veh, h(0, 30), -18.84, 47.54, "RN2 pk 18", 48.0, "ON")
    check("v3 : segment B ouvert AUJOURD'HUI au 1er signal après minuit",
          [t.heure_debut for t in trajets_du(jour)]
          == [h(0, 30)], f"{[(t.heure_debut, t.heure_fin) for t in trajets_du(jour)]}")
    engine.ingest_event(db, veh, h(0, 50), -18.84, 47.54, "Dépôt", 0.0, "OFF")
    check("v3 : arrêt 00:50 enregistré sur le segment B (aujourd'hui)",
          [t.heure_fin for t in trajets_du(jour)] == [h(0, 50)])
    check("jour_attribution(23h40 veille) = veille (DATE pure, v3)",
          jour_attribution(h(23, 40, ref=veille)) == veille)
    check("v3 : jour_attribution(00h15) = AUJOURD'HUI (bascule 01h abrogée)",
          jour_attribution(h(0, 15)) == jour)

    # --- la ligne officielle du portail (23:40 → 00:30), publiée alors que
    #     le camion roule toujours (ouvert, à cheval) : v3 C1 la re-pointe à
    #     minuit = segment B d'aujourd'hui ; la veille consolidée n'est
    #     JAMAIS rouverte
    stats = reconcilier_trajets_valides(db, [
        {"vehicule_id": veh.id, "gps_associe": veh.gps_associe, "plaque": veh.plaque,
         "debut": h(23, 40, ref=veille), "fin": h(0, 30), "distance_km": 24.5,
         "source": "MZONEX"}], maintenant=apres_bascule)
    ts, tj = trajets_du(veille), trajets_du(jour)
    check("v3 : la VEILLE reste consolidée à son dernier signal 23:55 "
          "(jamais rouverte)",
          len(ts) == 1 and ts[0].heure_fin == h(23, 55, ref=veille)
          and ts[0].statut_source == StatutSourceTrajet.VALIDE,
          f"veille={[(t.heure_debut, t.heure_fin, t.statut_source) for t in ts]}")
    check("v3 : le segment B vit aujourd'hui à partir de minuit pile",
          all(t.heure_debut.date() == jour for t in tj)
          and min(t.heure_debut for t in tj) == h(0, 0),
          f"jour={[(t.heure_debut, t.heure_fin) for t in tj]}")

    # ================================================================
    print("\n[CA-2] Attribution : 02h15 → 03h00 = AUJOURD'HUI (DATE pure)")
    db.execute(delete(Trajet).where(Trajet.suivi_id.in_(
        [x.id for x in (db.scalars(select(SuiviJournalier).where(
            SuiviJournalier.vehicule_id == veh.id,
            SuiviJournalier.date_jour == jour)).all())])))
    db.commit()
    check("jour_attribution(02h15) = aujourd'hui (DATE pure, v3)",
          jour_attribution(h(2, 15)) == jour)
    stats = reconcilier_trajets_valides(db, [
        {"vehicule_id": veh.id, "gps_associe": veh.gps_associe, "plaque": veh.plaque,
         "debut": h(2, 15), "fin": h(3, 0), "distance_km": 12.0,
         "source": "MZONEX"}], maintenant=apres_bascule)
    check("trajet 02h15 rangé sur AUJOURD'HUI",
          len(trajets_du(jour)) == 1, f"{trajets_du(jour)}")

    # ================================================================
    print("\n[CA-3] v3 : consolidation 23:59:59 — en cours arrêtés, tout OFFICIEL")
    # §9 : rajoute deux trajets de nuit (veille) + un EN COURS à 00h40
    def aj(j, d, f, km, source=StatutSourceTrajet.VALIDE,
           valid=StatutValidationTrajet.VALIDE):
        s = engine.ensure_suivi(db, veh, j)
        db.add(Trajet(suivi_id=s.id, numero=len(trajets_du(j)) + 1,
                      heure_debut=d, heure_fin=f, statut_source=source,
                      source_plateforme="MZONEX", distance_km=km,
                      statut_validation=valid))
        db.commit()
    # provisoire Niveau 1 clôturé mais pas encore confirmé par le portail
    # (EN_ATTENTE de jugement — cas courant d'une fin de soirée)
    aj(veille, h(22, 30, ref=veille), h(23, 5, ref=veille), None,
       source=StatutSourceTrajet.PROVISOIRE,
       valid=StatutValidationTrajet.EN_ATTENTE)
    aj(veille, h(23, 58, ref=veille), None, None,
       source=StatutSourceTrajet.PROVISOIRE,
       valid=StatutValidationTrajet.EN_ATTENTE)     # EN COURS à minuit
    check("v3 : la ligne 23:58 ouverte est attachée à la veille (DATE pure)",
          len(trajets_du(veille)) == 3, f"{len(trajets_du(veille))}")
    check("01h05 → aujourd'hui (DATE pure, v3)",
          jour_attribution(h(1, 5)) == jour)

    n = daily.pre_consolider_veille(db, veille, maintenant=apres_bascule)
    ts = trajets_du(veille)
    check("consolidation v3 : tous les trajets sans état final sont "
          "officialisés (≥ 2)", n >= 2, f"{n}")
    check("v3 : le « en cours » de 23:58 est fermé au dernier signal prouvé "
          "de la veille, BORNÉ à son début (§10 : rien d'inventé)",
          ts and ts[-1].heure_fin == h(23, 58, ref=veille),
          f"{ts[-1].heure_fin} vs 23:58")
    check("tous les trajets de la veille : statut_source VALIDÉ",
          all(t.statut_source == StatutSourceTrajet.VALIDE for t in ts))
    check("plus aucun EN_ATTENTE sur la veille",
          all(t.statut_validation != StatutValidationTrajet.EN_ATTENTE for t in ts))
    n2 = daily.pre_consolider_veille(db, veille, maintenant=apres_bascule) if False else daily.pre_consolider_veille(db, veille, maintenant=apres_bascule)
    check("consolidation idempotente (2ᵉ passage : 0)", n2 == 0, f"{n2}")

    # ================================================================
    print("\n[CA-4] Historique de la veille : 100 % OFFICIEL, 0 % PROVISOIRE")
    s_v = engine.ensure_suivi(db, veh, veille)
    db.expire(s_v, ["trajets"])          # session longue : relecture forcée
    jn = journee_suivi(s_v, maintenant=apres_bascule)
    check("toutes les lignes de la veille rendues OFFICIELLES",
          all(lg.etat == ETAT_OFFICIEL for lg in jn.lignes))
    # le seed de démonstration a déjà archivé la veille pour ce véhicule :
    # on retire cette archive de test pour vérifier l'archivage réel
    db.execute(delete(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == veille,
        HistoriqueJournalier.vehicule_id == veh.id))
    db.commit()
    nb = daily.archiver_jour(db, veille)
    check("archivage effectué (1 ligne)", nb == 1, f"{nb}")
    arch = db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == veille,
        HistoriqueJournalier.vehicule_id == veh.id))
    check("archive trouvée",
          arch is not None)
    tj = (arch.donnees or {}).get("trajets", [])
    check("archive : 0 % PROVISOIRE (CA-4)",
          all(t["statut_source"] == "VALIDÉ" for t in tj) and len(tj) > 0,
          f"{[(t['heure_debut'], t['statut_source']) for t in tj]}")
    check("archive : trajet à cheval (23:40 → 00:30) présent dans la veille",
          any(t["heure_debut"].startswith(h(23, 40, ref=veille).isoformat()[:16])
              for t in tj), f"{[(t['heure_debut'], t['heure_fin']) for t in tj]}")

    # ================================================================
    print("\n[CA-5/CA-6] Colonne Fin vide (trajet en cours) + formats (HH:MM / H:MM)")
    # purge des lignes des CA précédents pour isoler le cas (journée propre)
    for s_jx in db.scalars(select(SuiviJournalier).where(
            SuiviJournalier.vehicule_id == veh.id,
            SuiviJournalier.date_jour == jour)).all():
        db.execute(delete(Trajet).where(Trajet.suivi_id == s_jx.id))
    db.commit()
    db.expire_all()
    # trajet EN COURS aujourd'hui (camion qui roule) → fin vide côté API ET
    # côté export (Addendum v1.9 §3.3 + décision métier 05/08 : la durée de
    # conduite courante vit déjà dans la colonne TCC) — jamais le texte
    # « en cours »
    aj(jour, h(5, 30), None, None,
       source=StatutSourceTrajet.PROVISOIRE,
       valid=StatutValidationTrajet.EN_ATTENTE)
    # la purge SQL ci-dessus court-circuite le moteur : recalage manuel des
    # colonnes stockées du jour (heure_depart & compteurs) avant la lecture
    _s_x = engine.ensure_suivi(db, veh, jour)
    engine.recalculer_temps(db, _s_x, now_local())
    db.commit()
    veh2 = db.get(Vehicule, veh.id)
    veh2.last_event_at = now_local() - timedelta(seconds=30)
    veh2.last_vitesse = 45.0
    db.commit()
    s_j = engine.ensure_suivi(db, veh, jour)
    db.expire(s_j, ["trajets"])          # session longue : relecture forcée
    l_api = s_suivi(s_j)["trajets"][-1]
    check("API : fin vide pour le trajet en cours (début orange à l'écran)",
          l_api["heure_fin"] is None and l_api["statut_source"] == "PROVISOIRE",
          f"{l_api}")
    db.expire(s_j, ["trajets"])
    vals = _valeurs_suivi(s_suivi(s_j), detail=True, excel=False)
    check("export : aucune cellule « en cours » textuelle",
          "en cours" not in [str(v) for v in vals], f"{vals[:20]}")
    # case « Fin » du trajet en cours (T2) = VIDE (Addendum v1.9 §3.3 +
    # décision métier 05/08 : la durée n'est plus répétée ici — elle est
    # dans la colonne TCC ; v1.15 : indice décalé de +3 par les colonnes
    # TCC/TCJ/TTJ de l'Addendum v1.9 §4, placées avant les trajets)
    vtxt = [str(v) for v in vals]
    fin_t2 = "«absent»"
    if "05:30" in vtxt:
        i05 = vtxt.index("05:30")
        fin_t2 = str(vals[i05 + 1]) if len(vals) > i05 + 1 else "«absent»"
    check("export : colonne Fin VIDE pour le trajet en cours (durée = TCC)",
          fin_t2 == "", f"fin_t2={fin_t2!r} — {vals[17:26]}")
    check("format durée H:MM (9240 s → « 2:34 », CA-6)",
          _fmt_duree_txt(9240) == "2:34")
    check("format durée H:MM (2100 s → « 0:35 », CA-6)",
          _fmt_duree_txt(2100) == "0:35")
    check("format durée au-delà de 24 h (26:05)",
          _fmt_duree_txt(93900) == "26:05")

except BaseException as _exc:   # AUCUNE exception n'est masquée : ni import,
    # ni exécution, ni assertion. Un test interrompu n'est PAS un test vert.
    print(f"\n=== ABANDON : {type(_exc).__name__}: {_exc} ===",
          file=sys.stderr)
    print("=== AUCUN verdict pour cette suite : contrôles non exécutés ===",
          file=sys.stderr)
    raise                        # traceback + code de sortie NON NUL
finally:
    db.close()
    fichier = db_url.split("///")[-1]
    if fichier and os.path.exists(fichier):
        os.remove(fichier)

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
