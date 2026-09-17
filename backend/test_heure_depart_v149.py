"""Tests v1.49 — L'HEURE DE DÉPART VIENT D'UN FAIT, JAMAIS D'UNE PHOTO.

Captures du 17/09/2026 (Suivi Journalier + portail MZoneX côte à côte) :

  2746TCC — MZoneX publie un trajet 14:08:56 → 15:47:18 ; la plateforme
            affichait « Heure de départ 15:32 ».
  0916TBV — MZoneX ne montre que 4 événements moteur (06:20→06:31, 0 km/h,
            odomètre 496591,0 figé) : le camion n'a PAS bougé ; la plateforme
            lui comptait « départ 08:43 · TCC 0:02 · TCJ 7:46 · TTJ 7:46 ».

Deux défauts distincts, tous deux prouvés ici :

  [TD-100] UNE PHOTO N'EST PAS UN ÉVÉNEMENT.
           `dernieres_positions()` (endpoint Vehicles = « dernière position
           connue ») est ingéré comme un point GPS ordinaire. Or
           `ingest_event` ouvre une ligne dès que vitesse > 3 km/h et la date
           à l'horodatage du point : la photo prise PENDANT un trajet devient
           donc l'heure de départ. 15:32 = l'heure à laquelle le portail a
           rafraîchi sa photo, pas celle du départ (14:08:56).
           → corrigé : marqueur `observation`, qui met à jour la trace et
           l'état observé (gps_age_s, badge, carte) mais sort AVANT la machine
           à états : aucune ouverture, aucune clôture, aucune soudure.

  [TD-110] UNE TRACE FABRIQUÉE PORTE TOUT LE RESTE.
           `_synchroniser_dernier_point_mzonex` (appelé à chaque timeout N1
           MZoneX) forçait `last_event_at = maintenant - 2 min` et écrivait un
           événement inventé « il y a 2 minutes » — « afin de lever le repère
           boîtier muet ». Effets : (a) le badge « boîtier muet » ne pouvait
           jamais s'afficher ; (b) le bornage v1.48 des compteurs était
           neutralisé (trace toujours fraîche) ; (c) R2
           (`rattraper_ouvertures` : signal ≤ 15 min + vitesse > 3 km/h)
           croyait à un roulage en cours et ROUVRAIT une ligne datée du
           dernier événement connu → départs fantômes 08:43 / 15:32 ;
           (d) ces événements, sans clé d'idempotence, s'empilaient.
           → corrigé : le portail muet est COMPTÉ (`portail_muet`), jamais
           inventé.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v149.db" python3 test_heure_depart_v149.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import datetime, timedelta

from app import engine
engine.PUBLISH_ENABLED["on"] = False

from app import scrapers
from app.config import now_local
from app.database import SessionLocal
from app.main import migrer_schema
from app.models import (EvenementGPS, SourceEvenement, StatutVehicule,
                        StatutValidationTrajet, SuiviJournalier, Trajet, Vehicule)
from app.seed import seed_si_vide
from app.serializers import s_suivi
from app.engine import get_seuils, ingest_event, rattraper_ouvertures

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
AUJ = now_local().date()
BASE = datetime.combine(AUJ, datetime.min.time())
SEUILS = get_seuils(db)


def vierge(plaque):
    """Véhicule du parc remis à l'état « rien reçu aujourd'hui »."""
    v = db.query(Vehicule).filter_by(plaque=plaque).one()
    for s in db.query(SuiviJournalier).filter_by(vehicule_id=v.id,
                                                 date_jour=AUJ):
        for t in list(s.trajets):
            db.delete(t)
        db.delete(s)
    db.query(EvenementGPS).filter_by(vehicule_id=v.id).delete()
    v.last_lat = v.last_lng = v.last_event_at = None
    v.last_vitesse = 0.0
    db.commit()
    return v


def photo(v, h, mn, vitesse, sec=0, lat=-18.9, lng=47.5):
    """Exactement ce que produit `dernieres_positions()` (endpoint Vehicles)."""
    return ingest_event(db, v, BASE + timedelta(hours=h, minutes=mn, seconds=sec),
                        lat, lng, None, vitesse, "ON", None,
                        source=SourceEvenement.MZONEX, observation=True)


def trajets_du(v):
    s = db.query(SuiviJournalier).filter_by(vehicule_id=v.id, date_jour=AUJ).first()
    return (sorted(s.trajets, key=lambda t: t.numero) if s else [])


print("\n═══ [TD-100] une PHOTO n'ouvre ni ne ferme de ligne ═══")
vA = vierge("2746TCC")
photo(vA, 15, 32, 40.0)          # photo prise pendant le trajet réel
db.commit()
tr = trajets_du(vA)
check("aucune ligne créée par une photo (15:32, 40 km/h)",
      len(tr) == 0, f"→ {len(tr)} ligne(s) : "
      f"{[(t.heure_debut.strftime('%H:%M'), t.heure_fin) for t in tr]}")
check("aucune heure de départ inventée", vA.last_event_at is not None)
check("MAIS l'état observé est bien actualisé (trace, badge, carte)",
      vA.last_event_at == BASE + timedelta(hours=15, minutes=32)
      and abs((vA.last_vitesse or 0) - 40.0) < 0.01,
      f"last_event_at={vA.last_event_at} v={vA.last_vitesse}")
ev_obs = db.query(EvenementGPS).filter_by(vehicule_id=vA.id).count()
check("la photo reste dans la trace (audit, gps_age_s)", ev_obs == 1,
      f"→ {ev_obs} événement(s)")

print("\n═══ [LD-100] un ÉVÉNEMENT réel ouvre la ligne, à SA date ═══")
vB = vierge("5156TBV")
ingest_event(db, vB, BASE + timedelta(hours=14, minutes=8, seconds=56), -18.9,
             47.5, None, 45.0, "ON", None, source=SourceEvenement.MZONEX)
db.commit()
tr = trajets_du(vB)
check("un « Début du trajet » (14:08:56, 45 km/h) ouvre la ligne",
      len(tr) == 1 and tr[0].heure_debut == BASE + timedelta(hours=14, minutes=8, seconds=56),
      f"→ {[(t.heure_debut, t.heure_fin) for t in tr]}")

print("\n═══ [TD-101] photo APRÈS un vrai départ : la ligne garde sa date ═══")
photo(vB, 15, 32, 40.0)          # la photo ne doit rien réécrire
db.commit()
tr = trajets_du(vB)
check("la photo n'a ni déplacé ni dupliqué la ligne",
      len(tr) == 1 and tr[0].heure_debut == BASE + timedelta(hours=14, minutes=8, seconds=56),
      f"→ {[(t.heure_debut.strftime('%H:%M:%S'), t.heure_fin) for t in tr]}")
jB = s_suivi(db.query(SuiviJournalier).filter_by(vehicule_id=vB.id,
                                                 date_jour=AUJ).first(), SEUILS)
check("« Heure de départ » affichée = 14:08:56 (jamais 15:32)",
      jB["trajets"] and jB["trajets"][0]["heure_debut"].endswith("14:08:56"),
      f"→ {jB['trajets'][0]['heure_debut'] if jB['trajets'] else None}")

print("\n═══ [TD-110] le portail muet est compté, jamais inventé ═══")
vC = vierge("0916TBV")
photo(vC, 8, 43, 5.0)            # dernier signal réel : 08:43
db.commit()
avant_ts, avant_ev = vC.last_event_at, db.query(EvenementGPS).filter_by(
    vehicule_id=vC.id).count()


class FauxApi:
    """Portail qui répond, mais ne publie rien pour ce véhicule."""
    def dernieres_positions(self):
        return []


os.environ["MZONEX_USER"] = "test"
_vrai_active, _vraie_api = scrapers._mzonex_api_active, None
scrapers._mzonex_api_active = lambda: True
import app.api_mzonex as _mz
_vraie_api = _mz.ApiMZoneX
_mz.ApiMZoneX = FauxApi
try:
    res = scrapers._synchroniser_dernier_point_mzonex()
finally:
    scrapers._mzonex_api_active = _vrai_active
    _mz.ApiMZoneX = _vraie_api

db.expire_all()
vC = db.query(Vehicule).filter_by(plaque="0916TBV").one()
apres_ev = db.query(EvenementGPS).filter_by(vehicule_id=vC.id).count()
check("aucun événement fabriqué « il y a 2 minutes »",
      apres_ev == avant_ev, f"→ {avant_ev} puis {apres_ev}")
check("l'horodatage de communication n'est plus réécrit",
      vC.last_event_at == avant_ts,
      f"→ {avant_ts} puis {vC.last_event_at}")
check("le silence est MESURÉ et remonté (portail_muet)",
      res.get("portail_muet", 0) >= 1 and "0916TBV" in res.get(
          "portail_muet_plaques", []), f"→ {res}")
check("statut PARTIEL (un portail muet n'est pas un succès)",
      res.get("statut") == "PARTIEL", f"→ {res.get('statut')}")

print("\n═══ [LD-101] R2 : un signal périmé ne rouvre aucune ligne ═══")
stats = rattraper_ouvertures()
db.expire_all()
tr = trajets_du(db.query(Vehicule).filter_by(plaque="0916TBV").one())
check("signal de 08:43 (périmé) → aucune ligne rattrapée",
      len(tr) == 0 and stats.get("creees", 0) == 0,
      f"→ {len(tr)} ligne(s), stats={stats}")

print("\n═══ [LD-102] R2 : un roulage RÉEL ET FRAIS ouvre toujours (R2 intact) ═══")
vD = vierge("5186TBV")
maintenant = now_local()
debut_ev = maintenant - timedelta(minutes=6)
db.add(EvenementGPS(vehicule_id=vD.id, horodatage=debut_ev, latitude=-18.9,
                    longitude=47.5, adresse=None, vitesse=48.0, etat_moteur="ON",
                    type_evenement=engine.TypeEvenement.DEBUT_MOUVEMENT,
                    source=SourceEvenement.MZONEX))
vD.last_event_at = maintenant - timedelta(minutes=2)
vD.last_vitesse = 48.0
db.commit()
stats = rattraper_ouvertures()
db.expire_all()
tr = trajets_du(db.query(Vehicule).filter_by(plaque="5186TBV").one())
check("roulage frais sans ligne → ligne ouverte, datée du vrai début (R2)",
      len(tr) == 1 and abs((tr[0].heure_debut - debut_ev).total_seconds()) < 1,
      f"→ {[(t.heure_debut.strftime('%H:%M:%S'), t.heure_fin) for t in tr]}")

print(f"\n{'=' * 62}\n  RÉSULTAT : {R['ok']} OK / {R['ko']} KO\n{'=' * 62}")
db.close()
try:
    if "/tmp/" in db_url:
        os.remove(db_url.replace("sqlite:///", ""))
        print("  (base de test supprimée)")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
