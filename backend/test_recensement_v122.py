"""Tests v1.22 — ARBITRAGES LSS §0quinquies du 14/08/2026 (après-midi).

  D4 · RECENSEMENT des listes véhicules PUBLIÉES par les portails (listes
       déroulantes) à chaque synchronisation Niveau 2 :
         · parser de libellés MZoneX « 8076 TCB (LSS) » / CamtrackPro
           « 0826 TBS-MERCEDES -LPSA(LSS) » → plaque normalisée (garde plaque) ;
         · inconnu → fiche créée AVEC la plateforme du portail d'origine
           (extension D1) + alerte ⓘ + audit ;
         · connu sur l'autre plateforme → BASCULE automatique arbitrée
           (alerte ⓘ + audit `vehicule.plateforme_basculee`) ;
         · déjà rattaché → aucune action ; idempotent au rejeu ;
         · véhicule actif vu sur AUCUN portail → listé « absents »
           (boîtier muet / rattachement de groupe à faire côté portail) ;
         · libellé non-plaque → ignoré et tracé (D3), jamais créé.
  D5 · mots non-personnes (« garage », « dépôt »…) : jamais de fiche
       chauffeur ; liste surchargeable CONDUCTEUR_MOTS_IGNORES ; fiches déjà
       créées → passées INACTIVES au démarrage + alerte ⓘ (jamais supprimées).
  Correctif moteur (alignement R2, pas un changement de règle) : l'auto-
  réparation v1.17 ignore les « Début du trajet » ANCIENS (> 45 min sans fin
  ni mouvement = boîtier muet — fin du va-et-vient re-création/rejet manœuvre
  constaté en réel le 14/08 sur 7936TCB et 8806TCB) ; un début FRAIS est
  toujours ré-ingéré.

⏱️ Instants relatifs à l'horloge réelle (tolérants au fuseau).

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v122.db" python3 test_recensement_v122.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import timedelta

from sqlalchemy import delete, func, select

from app.config import (AGE_MAX_REPARATION_S, mots_ignores_conducteur,
                        now_local, plaque_depuis_libelle_portail)
from app.database import SessionLocal
from app import engine
from app.engine import (basculer_vehicule_plateforme, creer_conducteur_auto,
                        recenser_flotte)
from app.models import (Alerte, AuditLog, Conducteur, EvenementGPS,
                        SourceEvenement, StatutConducteur, StatutVehicule,
                        SuiviJournalier, Trajet, TypeAlerte, TypeEvenement,
                        Vehicule)
from app.scrapers import _reparer_debuts_sans_trajet
from app.seed import seed_si_vide
from app.main import migrer_schema, reparer_conducteurs_non_personnes

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

# ------------------------------------------------------------------ parser D4
print("\n[D4 · §0quinquies] Parser de libellés portails")
check("MZoneX « 8076 TCB (LSS) » → « 8076TCB »",
      plaque_depuis_libelle_portail("8076 TCB (LSS)") == "8076TCB")
check("CamtrackPro « 0826 TBS-MERCEDES -LPSA(LSS) » → « 0826TBS »",
      plaque_depuis_libelle_portail("0826 TBS-MERCEDES -LPSA(LSS)") == "0826TBS")
check("casse minuscule « 5616 tce-… » → « 5616TCE »",
      plaque_depuis_libelle_portail("5616 tce-CNHTC-LPSA(LSS)") == "5616TCE")
check("« 2066 TBP » nu → « 2066TBP »",
      plaque_depuis_libelle_portail(" 2066 TBP ") == "2066TBP")
check("« TOTAUX » / « LSS (LPSA) » / vide → refusés",
      plaque_depuis_libelle_portail("TOTAUX") == ""
      and plaque_depuis_libelle_portail("LSS (LPSA)") == ""
      and plaque_depuis_libelle_portail("") == "")

# ---------------------------------------------------------- recensement D4
print("\n[D4 · §0quinquies] Recensement : création, bascule, absents, D3")
# ardoise vierge (enfants d'abord) : les 52 véhicules seedés gêneraient le
# comptage « absents »
for modele in (EvenementGPS, Trajet, SuiviJournalier, Alerte, AuditLog,
               Vehicule):
    db.execute(delete(modele))
db.flush()

v_mzx = Vehicule(plaque="8076TCB", gps_associe="OBC-8076TCB",
                 plateforme_gps="MZONEX", statut=StatutVehicule.ACTIF)
v_mal = Vehicule(plaque="9999ZZZ", gps_associe="9999ZZZ",
                 plateforme_gps="MZONEX", statut=StatutVehicule.ACTIF)
v_abs = Vehicule(plaque="2066TBP", gps_associe="OBC-2066TBP",
                 plateforme_gps="MZONEX", statut=StatutVehicule.ACTIF)
# 2 vus de plus : sans eux MZX serait lu 2/3 (< 80 % → garde « partiel »
# v1.24 qui suspend la liste des absents)
v_x1 = Vehicule(plaque="1006TAA", gps_associe="1006TAA",
                plateforme_gps="MZONEX", statut=StatutVehicule.ACTIF)
v_x2 = Vehicule(plaque="1016TAA", gps_associe="1016TAA",
                plateforme_gps="MZONEX", statut=StatutVehicule.ACTIF)
db.add_all([v_mzx, v_mal, v_abs, v_x1, v_x2])
db.flush()

stats = recenser_flotte(db, {
    "MZONEX": ["8076 TCB (LSS)", "3596 TCX (LSS)",   # 1 connu + 1 NOUVEAU
               "1006 TAA (LSS)", "1016 TAA (LSS)"],  # 2 connus de plus
    "CAMTRACKPRO": ["9999 ZZZ-MERCEDES-LPSA(LSS)",     # connu mais MAL rangé
                    "TOTAUX"],                         # non-plaque (D3)
}, username="collecteur-test")

check("nouveau véhicule créé depuis la liste MZoneX (plateforme MZONEX)",
      stats["crees"] == 1 and db.scalar(select(Vehicule).where(
          Vehicule.plaque == "3596TCX",
          Vehicule.plateforme_gps == "MZONEX")) is not None)
check("alerte ⓘ « Nouveau véhicule » posée pour 3596TCX",
      db.scalar(select(func.count(Alerte.id)).where(
          Alerte.type == TypeAlerte.NOUVEAU_VEHICULE,
          Alerte.message.like("%3596TCX%"))) >= 1)
check("audit « vehicule.auto_cree » tracé",
      db.scalar(select(func.count(AuditLog.id)).where(
          AuditLog.action == "vehicule.auto_cree")) >= 1)
check("bascule automatique 9999ZZZ : MZONEX → CAMTRACKPRO (arbitrage D4)",
      stats["bascules"] == 1 and db.get(Vehicule, v_mal.id).plateforme_gps
      == "CAMTRACKPRO")
check("alerte ⓘ + audit « vehicule.plateforme_basculee » pour la bascule",
      db.scalar(select(func.count(Alerte.id)).where(
          Alerte.type == TypeAlerte.NOUVEAU_VEHICULE,
          Alerte.message.like("%9999ZZZ%bascul%"))) >= 1
      and db.scalar(select(func.count(AuditLog.id)).where(
          AuditLog.action == "vehicule.plateforme_basculee")) >= 1)
check("déjà rattachés : 8076TCB + 1006TAA + 1016TAA comptés, non retouchés",
      stats["deja"] == 3 and db.get(Vehicule, v_mal.id) is not None
      and db.get(Vehicule, v_mzx.id).plateforme_gps == "MZONEX")
check("D3 : « TOTAUX » ignoré (1 ignoré, aucune fiche TOTAUX)",
      stats["ignores"] == 1 and db.scalar(select(Vehicule).where(
          Vehicule.plaque == "TOTAUX")) is None)
check("absents : 2066TBP (actif, vu nulle part) signalé ; les vus exclus",
      stats["absents"] == ["2066TBP (MZONEX)"])

print("\n[D4 · §0quinquies] Idempotence au rejeu (cycle suivant)")
stats2 = recenser_flotte(db, {
    "MZONEX": ["8076 TCB (LSS)", "3596 TCX (LSS)",
               "1006 TAA (LSS)", "1016 TAA (LSS)"],
    "CAMTRACKPRO": ["9999 ZZZ-MERCEDES-LPSA(LSS)", "TOTAUX"],
}, username="collecteur-test")
check("rejeu : 0 création, 0 bascule, tous « déjà rattachés »",
      stats2["crees"] == 0 and stats2["bascules"] == 0 and stats2["deja"] == 5)
check("rejeu : aucune alerte NOUVEAU_VEHICULE supplémentaire",
      db.scalar(select(func.count(Alerte.id)).where(
          Alerte.type == TypeAlerte.NOUVEAU_VEHICULE)) == 2)
check("gardes bascule : même plateforme / plateforme invalide refusées",
      basculer_vehicule_plateforme(db, v_mzx, "MZONEX") is False
      and basculer_vehicule_plateforme(db, v_mzx, "INCONNU") is False)

# ------------------------------------------------------------------ D5
print("\n[D5 · §0quinquies] Mots non-personnes (chauffeurs)")
db.execute(delete(Alerte))
db.flush()
check("« Garage LSS 4 » ne crée AUCUNE fiche chauffeur",
      creer_conducteur_auto(db, "Garage LSS 4") is None
      and db.scalar(select(Conducteur).where(
          Conducteur.nom_prenom == "Garage LSS 4")) is None)
c = creer_conducteur_auto(db, "RABE Jean")
check("« RABE Jean » crée bien une fiche + alerte ⓘ",
      c is not None and db.scalar(select(func.count(Alerte.id)).where(
          Alerte.type == TypeAlerte.NOUVEAU_CONDUCTEUR)) == 1)
check("liste D5 connue (« garage » présent) et surchargeable",
      "garage" in mots_ignores_conducteur())
os.environ["CONDUCTEUR_MOTS_IGNORES"] = "zztop,depot"
check("surcharge CONDUCTEUR_MOTS_IGNORES respectée (« Garage LSS 4 » "
      "redevient créable ; « depot » toujours bloqué)",
      creer_conducteur_auto(db, "Garage LSS 4") is not None
      and creer_conducteur_auto(db, "DEPOT CENTRAL") is None)
os.environ.pop("CONDUCTEUR_MOTS_IGNORES")

print("\n[D5 · §0quinquies] Réparation au démarrage des fiches déjà créées")
g = Conducteur(nom_prenom="Garage LSS 2", prenom_usuel="Garage",
               matricule="AUTO-TSTGAR01", statut=StatutConducteur.ACTIF)
db.add(g)
db.commit()                       # la réparation ouvre sa PROPRE session
reparer_conducteurs_non_personnes()
db.expire_all()
g2 = db.scalar(select(Conducteur).where(Conducteur.matricule == "AUTO-TSTGAR01"))
check("« Garage LSS 2 » (ACTIF) → INACTIF au démarrage, fiche conservée",
      g2 is not None and g2.statut == StatutConducteur.INACTIF)
check("alerte ⓘ explicative posée + audit de désactivation",
      db.scalar(select(func.count(Alerte.id)).where(
          Alerte.type == TypeAlerte.NOUVEAU_CONDUCTEUR,
          Alerte.message.like("%Garage LSS 2%désactivée%"))) >= 1
      and db.scalar(select(func.count(AuditLog.id)).where(
          AuditLog.action == "conducteur.desactive_non_personne")) >= 1)
check("« RABE Jean » (vraie personne) reste ACTIF",
      db.scalar(select(Conducteur).where(
          Conducteur.nom_prenom == "RABE Jean",
          Conducteur.statut == StatutConducteur.ACTIF)) is not None)

# ------------------------------------------- correctif fraîcheur (v1.17 ↔ R2)
print("\n[§0quinquies] Auto-réparation : garde de fraîcheur (boîtier muet)")
for modele in (EvenementGPS, Trajet, SuiviJournalier):
    db.execute(delete(modele))
db.flush()
maintenant = now_local()


def vehicule_event(debut_il_y_a_s, immat):
    v = Vehicule(plaque=immat, gps_associe=immat,
                 plateforme_gps="MZONEX", statut=StatutVehicule.ACTIF)
    db.add(v)
    db.flush()
    db.add(EvenementGPS(
        vehicule_id=v.id, horodatage=maintenant - timedelta(
            seconds=debut_il_y_a_s),
        latitude=-18.94, longitude=48.21, vitesse=8.0, etat_moteur="ON",
        type_evenement=TypeEvenement.DEBUT_MOUVEMENT,
        source=SourceEvenement.MZONEX))
    db.flush()
    return v


def nb_trajets(v):
    return db.scalar(select(func.count(Trajet.id)).join(
        SuiviJournalier, Trajet.suivi_id == SuiviJournalier.id).where(
        SuiviJournalier.vehicule_id == v.id)) or 0


v_vieux = vehicule_event(2 * 3600, "7936TCB")     # boîtier muet (2 h, sans fin)
v_frais = vehicule_event(10 * 60, "0916TBU")      # vrai camion en route
rep = _reparer_debuts_sans_trajet(db, [v_vieux, v_frais],
                                  SourceEvenement.MZONEX)

check("début ANCIEN (> %ds) : NON ré-ingéré, aucune ligne fantôme"
      % AGE_MAX_REPARATION_S,
      nb_trajets(v_vieux) == 0)
check("début FRAIS (< %ds) : ré-ingéré, ligne « en cours » créée"
      % AGE_MAX_REPARATION_S,
      rep == 1 and nb_trajets(v_frais) == 1)
lig = db.scalar(select(Trajet).join(
    SuiviJournalier, Trajet.suivi_id == SuiviJournalier.id).where(
    SuiviJournalier.vehicule_id == v_frais.id))
check("la ligne créée est datée du vrai début et reste OUVERTE",
      lig is not None and lig.heure_fin is None
      and lig.heure_debut == maintenant - timedelta(seconds=600))

# ------------------------------------------------------------------ verdict
db.close()
url = os.environ.get("DATABASE_URL", "")
if url.startswith("sqlite:////tmp/"):
    try:
        os.remove(url.replace("sqlite:///", ""))
    except OSError:
        pass
print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===\n")
sys.exit(1 if R["ko"] else 0)
