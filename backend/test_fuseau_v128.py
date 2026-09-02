"""Tests v1.28 — Rectificatif fuseau CamtrackPro (§0octies C1, arbitrage LSS
du 20/08/2026 : « correction automatique +3h »).

Contexte mesuré le 20/08/2026 (capture de preuve LSS + lecture API directe) :
le serveur Wialon rend les textes horaires du rapport « Detail Trajet » en
**UTC** (« 2026-08-20 02:24:44 » / epoch « v » 1787192684) alors que le
portail affiche 05:24:44 — décalage exact +3h00, constant. La v1.26 a donc
stocké les trajets N2 CamtrackPro du 20/08 avec 3 h de retard.

  F1  Parseur : epoch « v » prioritaire → 05:24:44 local exact ; texte seul
      relu comme UTC → 07:39:08 local ; fin vide → None ; texte incohérent
      avec « v » → « v » gagne (source de vérité).
  F2  Migration UNIQUE : lignes CAMTRACKPRO ≥ borne décalées de +3h00 à la
      seconde (début ET fin, fin vide épargnée) ; si la bascule 01h00 (§8.1)
      change le jour d'attribution → rattachement au suivi du bon jour ;
      marqueur d'audit inscrit dans la MÊME transaction.
  F3  Ce qui ne bouge PAS : lignes CAMTRACKPRO < borne (repli écran, heures
      déjà locales) ; lignes MZONEX ; 2ᵉ appel = 0 correction (marqueur).
  F4  Conséquence métier : la ligne corrigée se rapporte désormais à l'item
      officiel corrigé (± tolérance §SEUIL_TOLERANCE_RAPPROCHEMENT_TRAJET) —
      aucun doublon au prochain cycle (écran = export = archive §A.2).

Exécution (base de test isolée, SUPPRIMÉE à la fin) :
  DATABASE_URL="sqlite:////tmp/test_v128.db" SIM_ENABLE=0 python3 test_fuseau_v128.py
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.config import TZ, jour_attribution
from app.database import SessionLocal
from app import engine
from app.models import (AuditLog, StatutSourceTrajet, StatutValidationTrajet,
                        Trajet, Vehicule)
from app.api_wialon import item_depuis_ligne_rapport
from app.reconciliation import _trouver_valide_par_debut
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

# Le rectificatif suppose le fuseau métier UTC+3 (Antananarivo, jamais DST) :
# sans lui, les attentes « à la seconde » du constat 20/08 sont hors sujet.
decal = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc).astimezone(
    TZ).utcoffset()
if decal != timedelta(hours=3):
    print(f"⚠️  Test conçu pour le fuseau métier UTC+3 (mesuré {decal}) — "
          "relancez sans APP_TZ exotique (la batterie le fait par défaut).")
    sys.exit(2)

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
db = SessionLocal()

# ---------------------------------------------------------------- F1
print("\n[F1] Parseur (rectificatif §0octies C1) — epoch « v » vérité, "
      "texte relu UTC")
ligne_reelle = [{"t": "2026-08-20 02:24:44", "v": 1787192684},
                {"t": "GFC-Brickaville-RN2"},
                {"t": "2026-08-20 04:39:08"},
                {"t": "Antsampanana"}, "2:14:24", "0:03:12", "2:11:12",
                "23.04 km", {"t": "31 km/h"}, {"t": "45 km/h"},
                "0:05:01", "ANDRIAMAMPIANINA Lahatra Faneva Omega"]
it = item_depuis_ligne_rapport("0826 TBS-MERCEDES -LPSA(LSS)", ligne_reelle)
check("epoch « v » dict → HEURE LOCALE 05:24:44 (= portail, à la seconde)",
      it["debut"] == datetime(2026, 8, 20, 5, 24, 44),
      str(it["debut"]))
check("texte seul « 04:39:08 » relu UTC → 07:39:08 local (fin exacte)",
      it["fin"] == datetime(2026, 8, 20, 7, 39, 8), str(it["fin"]))
lig_v_gagne = [{"t": "2999-01-01 00:00:00", "v": 1787192684}] + [""] * 11
check("texte incohérent avec « v » : « v » GAGNE (fuseau d'affichage "
      "serveur sans prise)", item_depuis_ligne_rapport(
          "0826 TBS-MERCEDES -LPSA(LSS)", lig_v_gagne)["debut"]
      == datetime(2026, 8, 20, 5, 24, 44))
lig_txt = ["2026-08-20 02:24:44"] + [""] * 10 + ["X"]
it_txt = item_depuis_ligne_rapport("0826 TBS-MERCEDES -LPSA(LSS)", lig_txt)
check("cellule DÉBUT texte seul (repli) → UTC → local ; fin « X » → None "
      "(trajet en cours toléré)",
      it_txt["debut"] == datetime(2026, 8, 20, 5, 24, 44)
      and it_txt["fin"] is None)

# ---------------------------------------------------------------- F2
print("\n[F2] Migration unique +3h (début/fin, rattachement §8.1, audit)")
v = db.scalar(select(Vehicule).where(Vehicule.plaque == "0826TBS"))
assert v is not None, "seed : 0826TBS introuvable"
s20 = engine.ensure_suivi(db, v, date(2026, 8, 20))
s19 = engine.ensure_suivi(db, v, date(2026, 8, 19))
# Cas réel du matin : stocké UTC 02:24:44 (05:24:44 local) sur le suivi du 20
t1 = Trajet(suivi_id=s20.id, numero=1,
            heure_debut=datetime(2026, 8, 20, 2, 24, 44),
            heure_fin=datetime(2026, 8, 20, 4, 39, 8),
            statut_source=StatutSourceTrajet.VALIDE,
            statut_validation=StatutValidationTrajet.VALIDE,
            source_plateforme="CAMTRACKPRO", distance_km=23.04)
# Trajet de nuit stocké 19/08 22:30 UTC (= 20/08 01:30 local) : la v1.26
# l'avait rattaché au suivi du 19 — la correction doit le RATTACHER au 20.
t2 = Trajet(suivi_id=s19.id, numero=1,
            heure_debut=datetime(2026, 8, 19, 22, 30, 0),
            heure_fin=None,                       # « en cours » au moment du vol
            statut_source=StatutSourceTrajet.VALIDE,
            statut_validation=StatutValidationTrajet.VALIDE,
            source_plateforme="CAMTRACKPRO", distance_km=8.2)
# Garde-fous : ligne CAMTRACKPRO ancienne (écran, heure déjà locale) et
# ligne MZONEX dans la fenêtre — ne doivent JAMAIS bouger.
t3 = Trajet(suivi_id=s20.id, numero=2,
            heure_debut=datetime(2026, 8, 5, 10, 0, 0),
            heure_fin=datetime(2026, 8, 5, 11, 0, 0),
            source_plateforme="CAMTRACKPRO", distance_km=12.0)
t4 = Trajet(suivi_id=s20.id, numero=3,
            heure_debut=datetime(2026, 8, 20, 2, 0, 0),
            heure_fin=datetime(2026, 8, 20, 3, 0, 0),
            source_plateforme="MZONEX", distance_km=30.0)
db.add_all([t1, t2, t3, t4])
db.commit()
n = engine.corriger_fuseau_camtrackpro(db)
db.expire_all()
check("2 lignes fautives décalées (fenêtre 20/08 seule)",
      n == 2, f"n={n}")
check("t1 : 02:24:44 → 05:24:44 et 04:39:08 → 07:39:08, à la seconde",
      t1.heure_debut == datetime(2026, 8, 20, 5, 24, 44)
      and t1.heure_fin == datetime(2026, 8, 20, 7, 39, 8))
check("t1 toujours sur le suivi du 20/08", t1.suivi_id == s20.id)
check("t2 « en cours » : fin None épargnée, début 22:30 → 20/08 01:30",
      t2.heure_fin is None
      and t2.heure_debut == datetime(2026, 8, 20, 1, 30, 0))
check("t2 RATTACHÉ au suivi du bon jour (bascule 01h00 §8.1 franchie)",
      t2.suivi_id == s20.id
      and jour_attribution(t2.heure_debut) == date(2026, 8, 20),
      f"suivi_id={t2.suivi_id}")
audit = db.scalar(select(AuditLog).where(
    AuditLog.action == engine.MARQUEUR_CORRECTIF_FUSEAU))
check("marqueur d'audit UNIQUE inscrit (corrigées=2, rattachées=1)",
      audit is not None and audit.details.get("trajets_corriges") == 2
      and audit.details.get("rattaches_au_bon_jour") == 1)

# ---------------------------------------------------------------- F3
print("\n[F3] Ce qui ne bouge PAS")
check("CAMTRACKPRO ancien (écran, heure déjà locale) INTACT",
      t3.heure_debut == datetime(2026, 8, 5, 10, 0, 0)
      and t3.heure_fin == datetime(2026, 8, 5, 11, 0, 0))
check("MZONEX (déjà juste, autre parseur) INTACT même dans la fenêtre",
      t4.heure_debut == datetime(2026, 8, 20, 2, 0, 0)
      and t4.heure_fin == datetime(2026, 8, 20, 3, 0, 0))
nb_avant = db.scalar(select(
    engine.func.count(AuditLog.id)).where(        # func via module engine
        AuditLog.action == engine.MARQUEUR_CORRECTIF_FUSEAU)) or 0
n2 = engine.corriger_fuseau_camtrackpro(db)
db.expire_all()
nb_apres = db.scalar(select(
    engine.func.count(AuditLog.id)).where(
        AuditLog.action == engine.MARQUEUR_CORRECTIF_FUSEAU)) or 0
check("2ᵉ appel : 0 correction, marqueur inchangé, heures stables "
      "(idempotence prouvée)",
      n2 == 0 and nb_avant == 1 and nb_apres == 1
      and t1.heure_debut == datetime(2026, 8, 20, 5, 24, 44),
      f"n2={n2} avant={nb_avant} apres={nb_apres}")

# ---------------------------------------------------------------- F4
print("\n[F4] Après correction, la ligne se rapporte à l'item officiel "
      "corrigé (± tolérance) — aucun doublon venant (§A.2)")
trouve = _trouver_valide_par_debut([t1], datetime(2026, 8, 20, 5, 24, 44),
                                   tolerance=120.0)
check("ligne corrigée retrouvée par le rapprochement ± 2 min → le prochain "
      "cycle MET À JOUR au lieu de dupliquer", trouve is t1)

# ---------------------------------------------------------------- nettoyage
db.close()
for cand in ("/tmp/test_v128.db",):
    try:
        os.unlink(cand)
    except OSError:
        pass

print(f"\n=== RÉSULTAT : {R['ok']} OK / {R['ko']} KO ===")
sys.exit(1 if R["ko"] else 0)
