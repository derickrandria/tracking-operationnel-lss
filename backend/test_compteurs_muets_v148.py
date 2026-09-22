"""Tests v1.48 — COMPTEURS BORNÉS À LA DERNIÈRE PREUVE + BADGE HONNÊTE.

Constat exploitant du 17/09/2026 (captures du Suivi Journalier, panne MZoneX
depuis 08:43) : 17 camions affichaient « TCC 0:02 · TCJ 3:58 · TTJ 3:58 » à
12:42 alors que leur dernier mouvement connu datait de 08:43 (3:58 = temps
écoulé depuis le dernier signal, pas une durée de conduite). Sans borne, ces
valeurs auraient couru jusqu'à minuit.

Deux corrections vérifiées ici :

  1. BORNAGE (chaines.fin_bornee_ouverte) — la ligne OUVERTE reste « en cours »
     à l'écran (v1.16 : aucun changement, un vrai long trajet ne disparaît
     pas), mais ses COMPTEURS se figent à `min(maintenant, trace + 30 min)`.
     Écran = export = archive = mesure (le moteur utilise la même borne).
     Monotone : la trace ne recule jamais → aucun retour en arrière.
  2. BADGE HONNÊTE — `scrapers.sources_en_echec()` expose les portails dont la
     DERNIÈRE tentative a échoué (la réussite remet `derniere_erreur` à None),
     `s_suivi` porte `plateforme_gps`, et `/api/suivi` transporte la liste :
     l'écran peut alors dire « source MZONEX en panne — collecte interrompue »
     au lieu de « boîtier muet — données en transit » (qui suppose à tort une
     zone sans réseau et une remontée automatique).

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v148.db" python3 test_compteurs_muets_v148.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from app import engine
from app.chaines import ETAT_EN_COURS, Segment, construire_journee
from app.config import now_local
from app.database import SessionLocal
from app.main import migrer_schema
from app.models import (StatutCamion, StatutSourceTrajet, StatutValidationTrajet,
                        StatutVehicule, SuiviJournalier, Trajet, Vehicule)
from app.seed import seed_si_vide
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

SEUILS = {"DUREE_MIN_PAUSE_VALIDE": 1200.0,
          "SEUIL_DISTANCE_MIN_TRAJET_KM": 0.3,
          "SEUIL_PAUSE_COUPURE_TCC": 1800.0,
          "SEUIL_GPS_HORS_LIGNE": 1800.0,
          "SEUIL_VITESSE_ARRET": 3.0}

try:
    aujour = now_local().date()
    base = datetime.combine(aujour, datetime.min.time())

    def vehicule(plaque, trace, vitesse=0.0, portail="MZONEX"):
        v = Vehicule(plaque=plaque, statut=StatutVehicule.ACTIF,
                     plateforme_gps=portail, gps_associe=f"OBC-{plaque}",
                     last_event_at=trace, last_vitesse=vitesse,
                     last_lat=-18.9, last_lng=47.5)
        db.add(v)
        db.flush()
        return v

    def suivi(v, debut, fin=None, km=42.0):
        s = SuiviJournalier(date_jour=aujour, vehicule_id=v.id,
                            statut_camion=StatutCamion.CHARGE)
        db.add(s)
        db.flush()
        db.add(Trajet(suivi_id=s.id, numero=1, heure_debut=debut, heure_fin=fin,
                      distance_km=km, statut_source=StatutSourceTrajet.PROVISOIRE,
                      statut_validation=StatutValidationTrajet.EN_ATTENTE))
        db.commit()
        return s

    # =====================================================================
    print("\n[1] LIGNE OUVERTE + BOÎTIER MUET (scénario 17/09 : 08:43 → 12:42)")
    debut = base + timedelta(hours=8, minutes=43)
    v = vehicule("1480TST", trace=debut, vitesse=0.0)
    s = suivi(v, debut)

    quatre_heures = debut + timedelta(hours=3, minutes=58)
    j = journee_suivi = __import__("app.serializers", fromlist=["x"]).journee_suivi(
        s, SEUILS, maintenant=quatre_heures)
    ligne = j.lignes[0]
    check("la ligne reste « EN COURS » à l'écran (v1.16 préservé)",
          ligne.etat == ETAT_EN_COURS and ligne.fin is None,
          f"etat={ligne.etat} fin={ligne.fin}")
    check("TCJ borné : 30 min de présomption, PAS 3h58",
          j.tcj_s == 1800, f"{j.tcj_s}s")
    check("TTJ borné de même (amplitude jusqu'à la dernière preuve)",
          j.ttj_s == 1800, f"{j.ttj_s}s")

    six_heures = debut + timedelta(hours=6)
    j2 = __import__("app.serializers", fromlist=["x"]).journee_suivi(
        s, SEUILS, maintenant=six_heures)
    check("MONOTONE : consulter 2 h plus tard ne gonfle plus rien",
          (j2.tcj_s, j2.ttj_s) == (j.tcj_s, j.ttj_s),
          f"{j2.tcj_s}s / {j2.ttj_s}s")

    check("travail_s de la ligne borné lui aussi",
          ligne.travail_s == 1800, f"{ligne.travail_s}")

    # =====================================================================
    print("\n[2] BOÎTIER VIVANT (aucune régression de la règle v1.16)")
    v2 = vehicule("1481TST", trace=base + timedelta(hours=10), vitesse=48.0,
                  portail="CAMTRACKPRO")
    debut2 = base + timedelta(hours=9)
    s2 = suivi(v2, debut2)
    maintenant2 = base + timedelta(hours=10, minutes=30)
    j3 = __import__("app.serializers", fromlist=["x"]).journee_suivi(
        s2, SEUILS, maintenant=maintenant2)
    check("boîtier frais : la ligne ouverte compte jusqu'à maintenant",
          j3.tcj_s == 5400, f"{j3.tcj_s}s (attendu 5400)")

    # =====================================================================
    print("\n[3] MOTEUR = MÊME BORNE (écran = archive = mesure)")
    engine.recalculer_temps(db, s, quatre_heures)
    db.commit()          # recalculer_temps ne commit pas (contrat des appelants)
    db.refresh(s)
    check("engine.recalculer_temps borne le TCJ stocké",
          s.tcj_s == 1800 and s.ttj_s == 1800, f"{s.tcj_s}s / {s.ttj_s}s")

    # =====================================================================
    print("\n[4] BADGE HONNÊTE — la panne de source est nommée")
    from app import scrapers
    from app.scrapers import sources_en_echec
    check("aucune source en échec au départ", sources_en_echec() == [],
          f"{sources_en_echec()}")
    scrapers._etat_collecte_erreur("MZONEX", RuntimeError(
        'ErreurAuthMZoneX: connexion SSO refusée (HTTP 400) — réponse '
        'portail : \'<html>…\' cookies=[\'idsrv.session\']'))
    check("échec MZONEX → la source est nommée",
          sources_en_echec() == ["MZONEX"], f"{sources_en_echec()}")
    scrapers._etat_collecte_fin("MZONEX", 12)
    check("une réussite efface l'échec (derniere_erreur → None)",
          sources_en_echec() == [], f"{sources_en_echec()}")

    payload = s_suivi(s, SEUILS)
    check("s_suivi porte `plateforme_gps` (l'écran peut cibler le badge)",
          payload.get("plateforme_gps") == "MZONEX",
          f"{payload.get('plateforme_gps')}")

    # =====================================================================
    print("\n[5] SIGNATURE BAS NIVEAU (fonction pure)")
    from app.chaines import fin_bornee_ouverte
    check("trace inconnue → comportement d'avant (maintenant)",
          fin_bornee_ouverte(debut, quatre_heures, None, 1800) == quatre_heures)
    check("trace + seuil < maintenant → borné",
          fin_bornee_ouverte(debut, quatre_heures, debut, 1800)
          == debut + timedelta(seconds=1800))
    check("trace fraîche → maintenant (présomption légitime)",
          fin_bornee_ouverte(debut, debut + timedelta(minutes=10), debut, 1800)
          == debut + timedelta(minutes=10))
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

print(f"\n=== RÉSULTAT v1.48 : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
