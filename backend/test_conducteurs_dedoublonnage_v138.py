# -*- coding: utf-8 -*-
"""Tests v1.38 — §0sexies decies J1→J4 (arbitrages LSS du 27/08/2026) :
déduplication du référentiel chauffeurs.

Cause racine prouvée le 27/08 : `lower()` de SQLite ne plie que A-Z → la
détection d'un nom accentué (« MICHAËL Justin ») ne retrouvait JAMAIS la
fiche existante → création en rafale (référentiel réel gonflé à 2 118
fiches) et alertes « Nouveau chauffeur détecté » en série.

[A] J1 — collecteur : `creer_conducteur_auto` sur forme CANONIQUE (NFKD,
    accents écartés, minuscules) — « MICHAËL Justin » = « MICHAEL JUSTIN »
    = NFD = « Michaël  Justin » ; une fiche par chauffeur, idempotent ;
[B] J1 — écran : création/renommage manuels vers un nom équivalent → 409
    avec message clair ;
[C] J2/J3/J4 — réparation unique : gardien = fiche la plus complète sinon
    la plus ancienne ; références vives rebranchees ; archives NON
    retouchées (§A.2) ; suppression des doublons APRÈS consignation
    intégrale en audit (choix express de l'exploitant, exception gravée) ;
    alertes fantômes de la rafale clôturées ; marqueur idempotent ; index
    UNIQUE posé ;
[D] après réparation : le collecteur retrouve toujours le gardien.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v138.db" SIM_ENABLE=0 python3 test_conducteurs_dedoublonnage_v138.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
os.environ["YMANE_ACTIVE"] = "0"
import sys
from datetime import date, datetime, time

from sqlalchemy import delete, func, inspect, select

from app.database import SessionLocal
from app import engine
from app.config import normaliser_libelle
from app.models import (Alerte, AuditLog, Conducteur, GraviteAlerte,
                        GraviteInfraction, HistoriqueJournalier, Infraction,
                        Mission, StatutAlerte, StatutConducteur,
                        StatutSourceTrajet, StatutValidationTrajet,
                        SuiviJournalier, Trajet, TypeAlerte, TypeInfraction,
                        SourceEvenement, Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app.engine import creer_conducteur_auto
from app.reparation import MARQUEUR_V138, reparer_conducteurs_v138
from app.serializers import s_historique

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
    print("⛔ Sécurité : base de test uniquement (DATABASE_URL /tmp).")
    sys.exit(2)

if db_url.startswith("sqlite:///"):            # leçon « base /tmp périmée »
    try:                                       # SUPPRESSION AVANT seed_si_vide
        os.remove(db_url.replace("sqlite:///", "/", 1))
    except OSError:
        pass

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
db = SessionLocal()
for modele in (Alerte, AuditLog):
    db.execute(delete(modele))
db.commit()

NOM_NFD = "MICHAËL Justin".encode("utf-8").decode("utf-8")  # NFC
# forme NFD : E + trema COMBINANT (invisible à l'œil — c'est le piège)
NOM_NFD = "MICHA" + "Ë" + "L Justin"
import unicodedata
NOM_NFD = unicodedata.normalize("NFD", "MICHAËL Justin")


def _compter_alertes_nouveau_chauffeur():
    return db.scalar(select(func.count(Alerte.id)).where(
        Alerte.type == TypeAlerte.NOUVEAU_CONDUCTEUR)) or 0


# ---------------------------------------------------------------- [A] J1 collecteur
print("\n[A] J1 — anti-doublon collecteur sur forme CANONIQUE (casse+accents)")
db.execute(delete(Conducteur).where(
    Conducteur.nom_prenom.like("%ICHA%Justin%")))
db.commit()
n_alertes_avant = _compter_alertes_nouveau_chauffeur()
f1 = creer_conducteur_auto(db, "MICHAËL Justin")
db.commit()
check("Première détection « MICHAËL Justin » → fiche créée (matricule AUTO)",
      f1 is not None and f1.matricule.startswith("AUTO-"))
check("Forme canonique renseignée (NFKD, sans accent, minuscules)",
      f1.nom_normalise == "michael justin")
n_alertes_apres_creation = _compter_alertes_nouveau_chauffeur()
for variante in ("MICHAEL JUSTIN", "michaël justin", "Michaël  Justin",
                 NOM_NFD, "  MICHAËL   JUSTIN "):
    f_bis = creer_conducteur_auto(db, variante)
    db.commit()
    if f_bis is None or f_bis.id != f1.id:
        print(f"   ↳ variante non reconnue : {variante!r}")
        f_bis = None
        break
check("Toutes variantes reconnues (MAJ/min, doubles espaces, Unicode NFD "
      "invisible) → MÊME fiche, AUCUNE nouvelle",
      f_bis is not None and f_bis.id == f1.id)
check("Aucune alerte supplémentaire pour les variantes (1 seule alerte au "
      "total pour ce chauffeur)",
      _compter_alertes_nouveau_chauffeur() - n_alertes_avant == 1)

# Régression : même chauffeur soumis deux fois dans le même batch ne doit pas
# provoquer d'alias dupliqué avant flush/commit.
nom_dup = "RABEMIARAMONA Solofo Jean Michel"
f_dup_1 = creer_conducteur_auto(db, nom_dup)
f_dup_2 = creer_conducteur_auto(db, nom_dup)
db.commit()
check("Même nom dans le même batch → même conducteur, pas d'alias dupliqué",
      f_dup_1 is not None and f_dup_2 is not None and f_dup_1.id == f_dup_2.id,
      f"ids={f_dup_1 and f_dup_1.id} / {f_dup_2 and f_dup_2.id}")

f2 = creer_conducteur_auto(db, "RAKOTO Nirina Test")
db.commit()
check("Deux personnes DIFFÉRENTES → deux fiches distinctes",
      f2 is not None and f2.id != f1.id)
check("Lieu/clef ignorés intacts (D5) : « Garage LSS 2 » ne crée rien",
      creer_conducteur_auto(db, "Garage LSS 2") is None)

# -------------------------------------------------- [B] J1 écran (API)
print("\n[B] J1 — écran Conducteurs : borne anti-doublon manuel (409)")
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
rz = client.post("/api/auth/login",
                 json={"username": "admin", "password": "Admin@2026"})
h_admin = {"Authorization": f"Bearer {rz.json()['access_token']}"}
r1 = client.post("/api/conducteurs", headers=h_admin, json={
    "nom_prenom": "TESTÉF Zéguy", "prenom_usuel": "Zeguy"})
# ⚠ v1.54 (21/09/2026) — RECHERCHE RENDUE NON AMBIGUË. L'assertion métier est
# INCHANGÉE (201 + forme canonique en base) et même RENFORCÉE : on vérifie LA
# fiche renvoyée par l'API (son id), plus « une fiche portant ce matricule ».
# Motif du changement : la recherche par `Conducteur.matricule == matricule
# renvoyé` retombait sur `IS NULL` — donc sur un AUTRE conducteur (jeu de
# démonstration : 18 fiches à matricule NULL depuis la purge des codes fictifs
# « CHxxx ») — et le contrôle échouait à tort. La création MANUELLE sans
# matricule laisse NULL par conception : « AUTO-xxxxxxxx » est réservé à la
# DÉCOUVERTE automatique des chauffeurs (règle D2).
row_test = db.scalar(select(Conducteur).where(Conducteur.id == r1.json()["id"])) \
    if r1.status_code == 201 else None
check("Création manuelle « TESTÉF Zéguy » → 201, forme canonique renseignée "
      "en base", r1.status_code == 201 and row_test is not None
      and row_test.nom_normalise == "testef zeguy",
      f"{r1.status_code} {r1.text[:120]}")
# Contrôle ajouté (v1.54) : la création manuelle n'INVENTE pas de matricule.
# « AUTO-xxxxxxxx » marque une fiche CRÉÉE AUTOMATIQUEMENT par le moteur (D2) ;
# un opérateur qui saisit une fiche à l'écran n'en reçoit pas (matricule NULL,
# à compléter par l'exploitant) — c'est aussi le critère de qualité J2.
check("Création manuelle → aucun matricule inventé (NULL, pas d'AUTO-)",
      r1.status_code == 201 and row_test is not None
      and row_test.matricule is None and row_test.code_badge_mzonex is None,
      f"matricule={row_test and row_test.matricule!r}")
r2 = client.post("/api/conducteurs", headers=h_admin, json={
    "nom_prenom": "TESTEF ZEGUY", "prenom_usuel": "Zeguy"})
check("Re-création équivalente (sans accent, autre casse) → 409 clair",
      r2.status_code == 409 and "nom équivalent" in r2.text,
      f"{r2.status_code}")
cid1 = r1.json()["id"]
r3 = client.patch(f"/api/conducteurs/{f1.id}", headers=h_admin,
                  json={"nom_prenom": "TESTÈF Zeguy"})
check("Renommage vers un nom déjà pris (≈) → 410... non : 409, pas de "
      "double fiche", r3.status_code == 409, f"{r3.status_code}")

# --------------------------------------- [C] réparation J2/J3/J4
print("\n[C] Réparation unique — gardien, rebranchements, audit, §A.2")
# scénario « champ de ruines » reproduisant le cas réel MICHAËL Justin
while True:  # repartir propre sur notre groupe de test
    db.execute(delete(Conducteur).where(
        func.lower(Conducteur.nom_prenom).like("%micha%justin%")))
    db.commit()
    break
ancien = Conducteur(nom_prenom="MICHAËL Justin", prenom_usuel="Justin",
                    matricule="AUTO-ANCIEN01",
                    nom_normalise="michael justin",
                    statut=StatutConducteur.ACTIF,
                    date_creation=datetime(2026, 8, 20, 8, 0))
complet = Conducteur(nom_prenom="MICHAEL JUSTIN", prenom_usuel="JUSTIN",
                     matricule="AUTO-C0MPLET1", telephone="034 00 000 00",
                     nom_normalise="michael  justin",   # clé DÉRIVÉE (double
                     # espace interne — bug du stockage, constaté au fumier)
                     statut=StatutConducteur.ACTIF,
                     date_creation=datetime(2026, 8, 22, 9, 0))
clone_nfd = Conducteur(nom_prenom=NOM_NFD, prenom_usuel="Justin",
                       matricule="AUTO-CL0NE00A",
                       nom_normalise=None,          # jamais rempli (ancien code)
                       statut=StatutConducteur.ACTIF,
                       date_creation=datetime(2026, 8, 22, 10, 0))
db.add_all([ancien, complet, clone_nfd])
db.flush()
v_8076 = db.scalar(select(Vehicule).where(Vehicule.plaque == "8076TCB"))
v_8076.conducteur_actuel_id = ancien.id          # le gardien attendu aura le véhicule
suivi = SuiviJournalier(date_jour=date(2026, 8, 26), vehicule_id=v_8076.id,
                        conducteur_id=clone_nfd.id)
db.add(suivi)
db.flush()
traj = Trajet(suivi_id=suivi.id, numero=1,
              heure_debut=datetime(2026, 8, 26, 7, 0),
              heure_fin=datetime(2026, 8, 26, 8, 0),
              statut_source=StatutSourceTrajet.VALIDE,
              statut_validation=StatutValidationTrajet.VALIDE,
              distance_km=12.5, conducteur_badge_id=clone_nfd.id)
mission = Mission(date_jour=date(2026, 8, 26), vehicule_id=v_8076.id,
                  conducteur_id=complet.id)
inf = Infraction(date_jour=date(2026, 8, 26), heure=time(7, 30),
                 vehicule_id=v_8076.id, conducteur_id=complet.id,
                 type=TypeInfraction.EXCES_VITESSE,
                 gravite=GraviteInfraction.MOYENNE,
                 source=SourceEvenement.MZONEX, exterieure=True,
                 validation="NON_TRAITEE")
al = Alerte(type=TypeAlerte.NOUVEAU_CONDUCTEUR,
            gravite=GraviteAlerte.INFORMATION, conducteur_id=ancien.id,
            message="Nouveau chauffeur détecté : MICHAËL Justin",
            statut=StatutAlerte.NOUVELLE)
al_vue = Alerte(type=TypeAlerte.NOUVEAU_CONDUCTEUR,
                gravite=GraviteAlerte.INFORMATION, conducteur_id=ancien.id,
                message="Nouveau chauffeur détecté : MICHAËL Justin (bis)",
                statut=StatutAlerte.VUE)
hist = HistoriqueJournalier(date_jour=date(2026, 1, 15), annee=2026, mois=1,
                            vehicule_id=v_8076.id, conducteur_id=clone_nfd.id,
                            donnees={"plaque": "8076TCB",
                                     "conducteur": "MICHAËL Justin"})
db.add_all([traj, mission, inf, al, al_vue, hist])
# gardien attendu : « ancien » a le VÉHICULE (poids 1000) — ni « complet »
# (téléphone) ni le plus récent ne le coiffent
db.commit()
nb_avant = db.scalar(select(func.count(Conducteur.id))) or 0
stats = reparer_conducteurs_v138(db)
db.expire_all()                    # le bulk-update contourne l'identity map
nb_apres = db.scalar(select(func.count(Conducteur.id))) or 0
check("Groupe MICHAËL/MICHAËL/NFD reconnu : 2 doublons supprimés, 1 gardien",
      stats["groupes"] >= 1 and stats["supprimees"] >= 2
      and nb_apres == nb_avant - stats["supprimees"], str(stats))
gardien = db.scalar(select(Conducteur).where(
    Conducteur.nom_normalise == "michael justin"
    ).order_by(Conducteur.date_creation))
check("J2 : gardien = fiche la plus complète (celle AVEC le véhicule "
      "affecté), pas simplement la plus récente",
      gardien is not None and gardien.id == ancien.id,
      f"gardien={gardien and gardien.nom_prenom}")
check("J1 : la clé stockée est RECALCULÉE propre pour tout survivant "
      "(dérive « double espace » corrigée)",
      gardien.nom_normalise == "michael justin")
vivant = db.scalar(select(Vehicule).where(Vehicule.plaque == "8076TCB"))
suv = db.get(SuiviJournalier, suivi.id)
trj = db.get(Trajet, traj.id)
mis = db.get(Mission, mission.id)
infr = db.get(Infraction, inf.id)
check("J2 : références vives rebranchements → véhicule, suivi, badge de "
      "trajet (tous repointent le gardien)",
      vivant.conducteur_actuel_id == gardien.id
      and suv.conducteur_id == gardien.id
      and trj.conducteur_badge_id == gardien.id)
check("J2 : mission et infraction rebranchees aussi",
      mis.conducteur_id == gardien.id and infr.conducteur_id == gardien.id)
aud_sup = db.scalars(select(AuditLog).where(
    AuditLog.action == "conducteur.doublon_supprime")).all()
check("J3 : chaque fiche supprimée est CONSIGNÉE intégralement (snapshot "
      "nom, matricule, téléphone, gardien, références)",
      len(aud_sup) >= 2 and all(a.details.get("fiche_supprimee", {}).get("id")
                                for a in aud_sup)
      and all(a.details.get("gardien", {}).get("matricule")
              for a in aud_sup), f"audits={len(aud_sup)}")
check("J3 : fiches doublons réellement ABSENTES (exception express "
      "exploitant §0sexies decies, jamais d'orphelins)",
      db.get(Conducteur, clone_nfd.id) is None
      and db.get(Conducteur, complet.id) is None
      and db.get(Conducteur, ancien.id) is not None)
hmarque = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == MARQUEUR_V138)) or 0
stats2 = reparer_conducteurs_v138(db)
check("Marqueur posé + 2e passage sans groupe → no-op total (idempotent)",
      hmarque >= 1 and stats2.get("deja_fait") is True
      and stats2["supprimees"] == 0, str(stats2))

# [C2] une fiche à clé DÉRIVÉE (« double espace ») surgissant APRÈS la
# purge est rattrapée par la passe rejouée — jamais de zombie (mise au
# point gravée du 27/08)
zomb = Conducteur(nom_prenom="MICHAEL  JUSTIN", prenom_usuel="Justin",
                  matricule="AUTO-Z0MBI001", nom_normalise="michael  justin",
                  statut=StatutConducteur.ACTIF)
db.add(zomb)
db.commit()
nb_avant_z = db.scalar(select(func.count(Conducteur.id))) or 0
nb_aud_avant = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "conducteur.doublon_supprime")) or 0
stats3 = reparer_conducteurs_v138(db)
db.expire_all()
nb_aud_apres = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "conducteur.doublon_supprime")) or 0
hmarque2 = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == MARQUEUR_V138)) or 0
nb_apres_z = db.scalar(select(func.count(Conducteur.id))) or 0
check("Passe rejouée : clé dérivée post-marqueur fusionnée (supprimée + "
      "consignée), gardien inchangé — JAMAIS de zombie",
      stats3.get("deja_fait") is True and stats3["supprimees"] == 1
      and db.get(Conducteur, zomb.id) is None
      and nb_aud_apres == nb_aud_avant + 1
      and nb_apres_z == nb_avant_z - 1
      and db.get(Conducteur, gardien.id) is not None, str(stats3))
check("Marqueur NON dupliqué (purge initiale unique) ; J4 non rejouée",
      hmarque2 == hmarque and stats3["alertes_closes"] == 0)
h_arch = db.get(HistoriqueJournalier, hist.id)
check("§A.2 : la ligne d'ARCHIVE n'est JAMAIS réécrite (FK d'origine "
      "conservée, affichage = texte du snapshot)",
      h_arch is not None and h_arch.conducteur_id == clone_nfd.id
      and s_historique(h_arch)["conducteur"] == "MICHAËL Justin")
al1, al2 = db.get(Alerte, al.id), db.get(Alerte, al_vue.id)
check("J4 : alertes fantômes « Nouveau chauffeur » NOUVELLE et VUE → "
      "TRAITEE (écran propre d'un coup)",
      al1.statut == StatutAlerte.TRAITEE
      and al2.statut == StatutAlerte.TRAITEE)
idx = {i["name"] for i in inspect(db.get_bind()).get_indexes("conducteurs")}
check("J1 : index UNIQUE ux_conducteurs_nom_normalise posé (défense en "
      "profondeur, plus aucun doublon possible en base)",
      "ux_conducteurs_nom_normalise" in idx, str(idx))
check("Alerte récapitulative de réparation émise (traçabilité exploitant)",
      db.scalar(select(func.count(Alerte.id)).where(
          Alerte.type == TypeAlerte.REPARATION_DONNEES,
          Alerte.message.like("%fiche(s) en doublon%"))) >= 1)

# --------------------------------------------------- [D] collecteur après réparation
print("\n[D] Collecteur APRÈS réparation : retrouve toujours le gardien")
avant = db.scalar(select(func.count(Conducteur.id))) or 0
fbad = creer_conducteur_auto(db, "MICHAEL  JUSTIN")
db.commit()
check("Détection d'une variante du gardien → fiche existante rendue, "
      "zéro création", fbad is not None and fbad.id == gardien.id
      and db.scalar(select(func.count(Conducteur.id))) == avant)

print(f"\n{'=' * 64}\n===== test_conducteurs_dedoublonnage_v138 : {R['ok']} OK / "
      f"{R['ko']} KO =====\n{'=' * 64}")
db.close()
try:
    if db_url.startswith("sqlite:///"):
        os.remove(db_url.replace("sqlite:///", "/", 1))
        print("Base de test supprimée.")
except OSError:
    pass
sys.exit(1 if R["ko"] else 0)
