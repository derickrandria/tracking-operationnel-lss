"""Tests v1.16 — RÉGRESSION « trajet en cours invisible » (retour métier
05/08/2026, captures 11:55 — 2736TCC et beaucoup d'autres camions).

Constat : un trajet COMMENCÉ (événement « Début du trajet » 11:37:44) mais
sans « Fin du trajet » publiée n'apparaissait PAS dans la grille, et le TCC
restait « — ». Deux causes, toutes deux corrigées en v1.16 :

  1. chaînes v1.13 : la chaîne OUVERTE était jugée sur la distance (< 0,3 km
     tant que le portail n'a pas publié la fin → cachée comme « manœuvre »).
     Règle v1.16 : une chaîne OUVERTE est TOUJOURS affichée en orange
     « en cours » ; le jugement manœuvre n'a lieu qu'à la CLÔTURE.
  2. l'état « en cours » exigeait un événement GPS frais (≤ 15 min) — or
     MZoneX n'émet RIEN entre « Début du trajet » et « Fin du trajet » :
     passé 15 min la ligne retombait. Règle v1.16 : l'ouverture en base EST
     le signal « en cours » (réparation auto au cycle Niveau 2 suivant si la
     fin a été manquée).

Rejoue la chronologie exacte du 2736TCC : 06:15:41 → 10:32:52 (officiel,
90,8 km), pause 1:05 (≥ 30 min → TCC remis à zéro), trajet en cours 11:37:44,
événement périmé (18 min), distance provisoire 0,002 km.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v116.db" python3 test_encours_v116.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import timedelta

from sqlalchemy import delete, select

from app.config import now_local
from app.database import SessionLocal
from app import engine
from app.chaines import (ETAT_EN_ATTENTE, ETAT_EN_COURS, ETAT_OFFICIEL,
                         Segment, construire_journee)
from app.models import (StatutSourceTrajet, StatutValidationTrajet, Trajet,
                        Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.serializers import journee_suivi, s_ligne
from app.exporters import _valeurs_suivi

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

# Ancrage « 11:55 » relatif à l'horloge réelle, en restant TOUJOURS dans la
# même journée logistique (scénario décalé si la remontée vers 06:15 croise
# minuit — les assertions sont faites avec `maintenant` injecté : zéro
# dépendance à l'heure d'exécution)
ancre = now_local().replace(microsecond=0)
if (ancre - timedelta(hours=5, minutes=40)).date() != ancre.date():
    ancre = ancre.replace(hour=13, minute=0, second=0)

t2_debut = ancre - timedelta(minutes=18)            # « 11:37:44 » — événement
# périmé à l'ancre (18 min > 15 min : l'ANCIEN code cachait déjà la ligne)
t1_fin = t2_debut - timedelta(minutes=65)           # « 10:32:52 » (pause 1:05)
t1_debut = t1_fin - timedelta(hours=4, minutes=17)  # « 06:15:41 »
jour = ancre.date()

veh = db.scalars(select(Vehicule).where(
    Vehicule.plateforme_gps == "MZONEX", Vehicule.statut == "ACTIF").order_by(
        Vehicule.plaque)).first()
print(f"Véhicule test : {veh.plaque} · jour={jour} · ancre={ancre:%H:%M:%S}")

s = engine.ensure_suivi(db, veh, jour)
db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
db.commit()


def ajoute(debut, fin, km, source, valid):
    db.add(Trajet(suivi_id=s.id, numero=0, heure_debut=debut, heure_fin=fin,
                  statut_source=source, source_plateforme="MZONEX",
                  distance_km=km, statut_validation=valid))
    db.commit()


def set_flotte(ts, vitesse):
    v = db.get(Vehicule, veh.id)
    v.last_event_at, v.last_vitesse = ts, vitesse
    db.commit()


def journee(maintenant):
    db.expire(s, ["trajets"])
    return journee_suivi(s, engine.get_seuils(db), maintenant=maintenant)


try:
    # ================================================================
    print("\n[1] REPLAY 2736TCC — trajet en cours 11:37, événement périmé (18 min)")
    ajoute(t1_debut, t1_fin, 90.832, StatutSourceTrajet.VALIDE,
           StatutValidationTrajet.VALIDE)          # T1 officiel (fusion §5)
    ajoute(t2_debut, None, 0.002, StatutSourceTrajet.PROVISOIRE,
           StatutValidationTrajet.EN_ATTENTE)      # T2 : 0,002 km seulement
    set_flotte(t2_debut, 0.0)   # AUCUN événement depuis le départ (MZoneX)

    j = journee(ancre)
    check("la ligne « en cours » est AFFICHÉE malgré 0,002 km et un dernier "
          "événement vieux de 18 min (régression 05/08)",
          len(j.lignes) == 2, f"{[(lg.debut, lg.fin, lg.etat) for lg in j.lignes]}")
    l1, l2 = j.lignes[0], j.lignes[1]
    check("T1 : ligne officielle 06:15 → 10:32 (noire)",
          l1.debut == t1_debut and l1.fin == t1_fin and l1.etat == ETAT_OFFICIEL)
    check("pause après T1 = écart réel 1:05 (3900 s)",
          l1.pause_apres_s == 3900, f"{l1.pause_apres_s}")
    check("T2 : début 11:37 immédiatement visible, fin None, état EN_COURS",
          l2.debut == t2_debut and l2.fin is None and l2.etat == ETAT_EN_COURS)
    sl2 = s_ligne(l2, 2)
    check("T2 sérialisée PROVISOIRE (orange à l'écran), heure_fin vide",
          sl2["statut_source"] == "PROVISOIRE" and sl2["heure_fin"] is None,
          f"{sl2}")

    engine.recalculer_temps(db, s, ancre)
    check("TCC = session courante depuis le début du trajet en cours "
          "(pause 1:05 ≥ 30 min → remise à zéro, puis 18:00 de conduite)",
          s.tcc_s == 18 * 60, f"tcc_s={s.tcc_s}")
    check("TCC non nul à l'écran (fini le « — » du 05/08)",
          s.tcc_s > 0)
    ttj_attendu = int((ancre - t1_debut).total_seconds())
    check("TTJ = amplitude totale (départ → maintenant)",
          s.ttj_s == ttj_attendu, f"{s.ttj_s} vs {ttj_attendu}")
    check("TCJ = TTJ − pause 1:05", s.tcj_s == ttj_attendu - 3900,
          f"{s.tcj_s}")

    # export : la cellule Fin du trajet en cours est VIDE (décision métier
    # 05/08 — la durée vit dans la colonne TCC)
    ligne_export = {"trajets": [s_ligne(l1, 1), sl2], "nb_trajets": 2}
    vals = _valeurs_suivi(ligne_export, detail=True, excel=False)
    # v1.44 (§0vicies decies N2 — réalignement déclaré) : indice relatif à
    # l'en-tête « Fin T2 » (le trajet en cours est sl2 ; Pos. 20h/22h décalent
    # les indices de +2).
    from app.exporters import _entetes_suivi as _ent_v144
    i_fin2 = _ent_v144(True).index("Fin T2")
    check("export : case Fin du trajet en cours VIDE (jamais « en cours »)",
          "en cours" not in [str(v) for v in vals] and vals[i_fin2] == "",
          f"{vals[i_fin2 - 3:i_fin2 + 3]}")

    # unitaire : l'état EN_COURS ne dépend PLUS de la fraîcheur GPS
    jm = construire_journee(
        [Segment(debut=t2_debut, fin=None, distance_km=0.001, rejete=False)],
        maintenant=ancre, roule=False, fin_substitution=None)
    check("construire_journee : ouvert vivant → EN_COURS même roule=False",
          len(jm.lignes) == 1 and jm.lignes[0].fin is None
          and jm.lignes[0].etat == ETAT_EN_COURS)

    # ================================================================
    print("\n[2] CLÔTURE INVALIDE (< 0,3 km) — la ligne disparaît (manœuvre)")
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
    db.commit()
    ajoute(t1_debut, t1_fin, 90.832, StatutSourceTrajet.VALIDE,
           StatutValidationTrajet.VALIDE)
    ajoute(t2_debut, t2_debut + timedelta(minutes=14), 0.10,
           StatutSourceTrajet.PROVISOIRE, StatutValidationTrajet.REJETE)
    plus_tard = ancre + timedelta(minutes=20)
    j2 = journee(plus_tard)
    check("après clôture à 0,10 km : la ligne est effacée (Addendum v1.9 §3)",
          len(j2.lignes) == 1 and j2.lignes[0].fin == t1_fin,
          f"{[(lg.debut, lg.fin) for lg in j2.lignes]}")
    engine.recalculer_temps(db, s, plus_tard)
    check("TCC = 0 après effacement (pause ≥ 30 min en cours depuis 10:32)",
          s.tcc_s == 0, f"tcc_s={s.tcc_s}")
    check("T1 reste la seule ligne (noire, aucun trou/orphélin)",
          j2.lignes[0].etat == ETAT_OFFICIEL)

    # ================================================================
    print("\n[3] CLÔTURE VALIDE (≥ 0,3 km) — orange 20 min, puis noire")
    db.execute(delete(Trajet).where(Trajet.suivi_id == s.id))
    db.commit()
    ajoute(t1_debut, t1_fin, 90.832, StatutSourceTrajet.VALIDE,
           StatutValidationTrajet.VALIDE)
    t3_debut, t3_fin = ancre + timedelta(minutes=30), ancre + timedelta(minutes=50)
    ajoute(t3_debut, t3_fin, 6.0, StatutSourceTrajet.PROVISOIRE,
           StatutValidationTrajet.EN_ATTENTE)
    j3 = journee(ancre + timedelta(minutes=55))
    check("ligne clôturée ≥ 0,3 km visible, orange EN_ATTENTE (< 20 min)",
          len(j3.lignes) == 2 and j3.lignes[1].etat == ETAT_EN_ATTENTE,
          f"{[(lg.debut, lg.etat) for lg in j3.lignes]}")
    j4 = journee(ancre + timedelta(minutes=71))
    check("la même ligne passe NOIRE une fois la pause ≥ 20 min constatée",
          j4.lignes[1].etat == ETAT_OFFICIEL)
    deb = [lg.debut for lg in j4.lignes]
    check("contrat grille respecté (débuts croissants, pause = écart réel)",
          deb == sorted(deb) and j4.lignes[0].pause_apres_s ==
          int((j4.lignes[1].debut - j4.lignes[0].fin).total_seconds()))

finally:
    db.close()
    fichier = db_url.split("///")[-1]
    if fichier and os.path.exists(fichier):
        os.remove(fichier)
    print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
    sys.exit(1 if R["ko"] else 0)
