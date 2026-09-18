"""Tests v1.30 — §0decies (arbitrages LSS du 24/08/2026) : réparation embarquée
des journées abîmées par l'ancienne bascule 01h00 + renfort D4 du cycle minuit.

Cas rejoués (véridiques, captures de l'exploitant à l'appui) :
 - fin FORCÉE à 01:00 masquée + fins réelles restaurées (4876TBU 21/08) ;
 - trajet du milieu PERDU réinséré (0906TBV 20/08, 10:19→13:44) ;
 - fusion d'affichage de l'ère v1.28 DÉCOMPOSÉE (2 lignes propres, AM-2) ;
 - ligne géante « 05:23 → 01:00 » (TCJ 19:36 fantôme) guérie (0916TBV) ;
 - split minuit AM-3 : segment B posé au J+1 et NON masqué à la passe J+1 ;
 - ligne « en cours » résiduelle guérie ; fantôme sans source masqué (conservé) ;
 - portail injoignable → journée sautée (R5), reprise au démarrage suivant ;
 - idempotence stricte ; journée conforme INTACTE (archive non réécrite) ;
 - D4 : le cycle de minuit relit les portails avant de figer la veille.

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v130.db" SIM_ENABLE=0 python3 test_reparation_v130.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import os
os.environ.setdefault("SIM_ENABLE", "0")
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select

from app.serializers import s_historique
from app.database import SessionLocal
from app import daily, engine, reparation
from app.engine import get_seuils
from app.models import (Alerte, AuditLog, EvenementGPS, HistoriqueJournalier,
                        Infraction, Mission, StatutSourceTrajet,
                        StatutValidationTrajet, SuiviJournalier, Trajet,
                        TypeAlerte, Vehicule)
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
    print("⛔ Sécurité : base de test uniquement (DATABASE_URL /tmp).")
    sys.exit(2)

engine.PUBLISH_ENABLED["on"] = False
seed_si_vide()
migrer_schema()
db = SessionLocal()

# ardoise vierge : l'historique de démonstration du seed est purgé (déterminisme)
for modele in (Trajet, EvenementGPS, HistoriqueJournalier, SuiviJournalier,
               Alerte, AuditLog, Infraction, Mission):
    db.execute(delete(modele))
db.commit()

J0, J1, J2, J3 = (date(2026, 8, 19), date(2026, 8, 20),
                  date(2026, 8, 21), date(2026, 8, 22))


def dt(jour, hh, mm, ss=0):
    return datetime(jour.year, jour.month, jour.day, hh, mm, ss)


def veh(plaque):
    return db.scalar(select(Vehicule).where(Vehicule.plaque == plaque))


def suivi_de(v, jour):
    s = db.scalar(select(SuiviJournalier).where(
        SuiviJournalier.vehicule_id == v.id,
        SuiviJournalier.date_jour == jour))
    if s is None:
        s = engine.ensure_suivi(db, v, jour)
    return s


def poser_ligne(v, jour, debut, fin, dist,
                validation=StatutValidationTrajet.VALIDE,
                source=StatutSourceTrajet.VALIDE, plateforme="CAMTRACKPRO"):
    s = suivi_de(v, jour)
    n = db.scalar(select(func.count(Trajet.id)).where(
        Trajet.suivi_id == s.id)) or 0
    t = Trajet(suivi_id=s.id, numero=n + 1, heure_debut=debut, heure_fin=fin,
               statut_source=source, source_plateforme=plateforme,
               distance_km=dist, statut_validation=validation)
    db.add(t)
    db.flush()
    return t


def lignes_affichees(v, jour):
    s = suivi_de(v, jour)
    return list(db.scalars(select(Trajet).where(
        Trajet.suivi_id == s.id,
        Trajet.statut_validation != StatutValidationTrajet.REJETE)
        .order_by(Trajet.heure_debut)).all())


def it(plaque, debut, fin, dist, conducteur=None, source="CAMTRACKPRO"):
    return {"gps_associe": f"OBC-{plaque}", "plaque": plaque, "source": source,
            "debut": debut, "fin": fin, "distance_km": dist,
            "conducteur": conducteur}


# ======================================================================
# CONSTRUCTION DES JOURNÉES « BLESSÉES » (état laissé par la v1.28)
# ======================================================================
v0826, v0906 = veh("0826TBS"), veh("0906TBV")
v0916, v5716, v3076 = veh("0916TBV"), veh("5716TBS"), veh("3076TBS")
v4876, v0926 = veh("4876TBU"), veh("0926TBV")

# J0 (19/08) 0826TBS — journée SAINE (témoin : ne doit pas bouger d'un octet)
poser_ligne(v0826, J0, dt(J0, 5, 30), dt(J0, 9, 30), 30.0)
poser_ligne(v0826, J0, dt(J0, 10, 0), dt(J0, 12, 0), 20.0)
daily.archiver_jour(db, J0)
arch_j0_avant = {h.id: dict(h.donnees or {}) for h in db.scalars(
    select(HistoriqueJournalier).where(HistoriqueJournalier.date_jour == J0))}

# J1 (20/08) 0906TBV — le TRAJET DU MILIEU est perdu (2 lignes au lieu de 3)
poser_ligne(v0906, J1, dt(J1, 5, 45, 59), dt(J1, 9, 44, 24), 31.56)
poser_ligne(v0906, J1, dt(J1, 13, 58, 51), dt(J1, 14, 30, 54), 10.47)
daily.archiver_jour(db, J1)

# J1 (20/08) 5716TBS — trajet franchissant minuit resté ENTIER (ère v1.28)
poser_ligne(v5716, J1, dt(J1, 23, 30), dt(J2, 0, 30), 15.0)

# J1 (20/08) 3076TBS — ligne FANTÔME sans source portail (déchet du correctif)
poser_ligne(v3076, J1, dt(J1, 8, 0), dt(J1, 9, 0), 12.0)

# J2 (21/08) 4876TBU — captures réelles : fusion du matin + fin FORCÉE 01:00
poser_ligne(v4876, J2, dt(J2, 9, 29), dt(J2, 14, 0), 109.3)      # fusion v1.28
poser_ligne(v4876, J2, dt(J2, 14, 35), dt(J2, 16, 41), 61.1)
poser_ligne(v4876, J2, dt(J2, 17, 2), dt(J2, 1, 0) + timedelta(days=1), 47.9)

# J2 (21/08) 0916TBV — ligne géante « 05:23 → 01:00 » (TCJ 19:36 fantôme)
poser_ligne(v0916, J2, dt(J2, 5, 23), dt(J2, 1, 0) + timedelta(days=1), 88.8)
daily.archiver_jour(db, J2)
db.commit()

# J3 (22/08) 0826TBS — journée vécue normalement MAIS le trajet du soir n'est
# pas encore arrivé (servira au test D4 : le cycle de minuit relit les portails)
poser_ligne(v0826, J3, dt(J3, 8, 0), dt(J3, 11, 0), 25.0)
db.commit()

# ======================================================================
# PORTAILS SIMULÉS — la vérité R5 (telle que relue par AM-4/réparation)
# ======================================================================
PORTAIL_J0 = [it("0826TBS", dt(J0, 5, 30), dt(J0, 9, 30), 30.0, source="MZONEX"),
              it("0826TBS", dt(J0, 10, 0), dt(J0, 12, 0), 20.0, source="MZONEX"),
              it("XXXXXX", dt(J0, 6, 0), dt(J0, 7, 0), 10.0, source="MZONEX")]
PORTAIL_J1 = [
    it("0906TBV", dt(J1, 5, 45, 59), dt(J1, 9, 44, 24), 31.56, "BEZAKA Valerien"),
    it("0906TBV", dt(J1, 10, 19, 6), dt(J1, 13, 44, 52), 10.33, "BEZAKA Valerien"),
    it("0906TBV", dt(J1, 13, 58, 51), dt(J1, 14, 30, 54), 10.47, "BEZAKA Valerien"),
    it("5716TBS", dt(J1, 23, 30), dt(J2, 0, 30), 15.0, source="MZONEX"),
]
PORTAIL_J2 = [
    it("4876TBU", dt(J2, 9, 29, 53), dt(J2, 13, 30, 39), 107.0, "JACKY", "MZONEX"),
    it("4876TBU", dt(J2, 13, 47, 55), dt(J2, 14, 0, 1), 2.302, "JACKY", "MZONEX"),
    it("4876TBU", dt(J2, 14, 35, 26), dt(J2, 16, 41, 20), 61.139, "JACKY", "MZONEX"),
    it("4876TBU", dt(J2, 16, 57, 11), dt(J2, 18, 58, 6), 47.915, "JACKY", "MZONEX"),
    it("4876TBU", dt(J2, 19, 36, 37), dt(J2, 20, 38, 36), 35.834, "JACKY", "MZONEX"),
    it("4876TBU", dt(J2, 9, 16, 0), dt(J2, 9, 18, 34), 0.047, "JACKY", "MZONEX"),
    it("0916TBV", dt(J2, 5, 23), dt(J2, 6, 10), 22.0, "RAMAHALEO"),
]
# le soir se termine à 19:45 : assez tôt pour être un trajet CLÔTURÉ au
# portail quand le cycle de minuit re-lit la veille (D4)
PORTAIL_J3 = [it("0826TBS", dt(J3, 8, 0), dt(J3, 11, 0), 25.0, source="MZONEX"),
              it("0826TBS", dt(J3, 18, 30), dt(J3, 19, 45), 18.0, source="MZONEX"),
              it("0926TBV", dt(J3, 10, 0), dt(J3, 11, 15), 2.0, source="MZONEX")]
PORTAILS = {J0: PORTAIL_J0, J1: PORTAIL_J1, J2: PORTAIL_J2, J3: PORTAIL_J3}


def relire(jour):
    return PORTAILS.get(jour, []), []


# ------------------------------------------------------------------ D4 d'abord
print("\n== D4 — le cycle de minuit relit les portails AVANT de figer la veille ==\n")
daily._trajets_reels_du_jour = relire
# véhicule 0926TBV : ligne « en cours » résiduelle ouverte à 10:00
poser_ligne(v0926, J3, dt(J3, 10, 0), None, None,
            validation=StatutValidationTrajet.EN_ATTENTE,
            source=StatutSourceTrajet.PROVISOIRE)
db.commit()
daily.executer_cycle_quotidien(J3, J3 + timedelta(days=1))
lignes_j3 = lignes_affichees(v0826, J3)
check("D4-1 : trajet du soir 18:30→19:45 récupéré à minuit",
      len(lignes_j3) == 2 and lignes_j3[-1].heure_debut == dt(J3, 18, 30)
      and lignes_j3[-1].heure_fin == dt(J3, 19, 45),
      f"lignes={[(t.heure_debut, t.heure_fin) for t in lignes_j3]}")
l926 = lignes_affichees(v0926, J3)
check("D4-2 : ligne « en cours » résiduelle refermée à la vraie fin (11:15)",
      len(l926) == 1 and l926[0].heure_fin == dt(J3, 11, 15),
      f"{[(t.heure_debut, t.heure_fin) for t in l926]}")


def _pannes(jour):
    return [], ["MZONEX (Timeout)", "CAMTRACKPRO (HTTP 503)"]


daily._trajets_reels_du_jour = _pannes
daily.executer_cycle_quotidien(J3, J3 + timedelta(days=1))
# v1.51 (exigence 5) — la dérogation D4 « minuit n'attend pas » est REMPLACÉE :
# la journée se ferme (23:59:59) mais l'archive est DIFFÉRÉE tant qu'une source
# bloquante n'a pas répondu. L'audit porte le nouveau nom.
note = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "cycle_minuit.archive_differee")) or 0
ancien = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "cycle_minuit.relecture_partielle")) or 0
check("D4-3 : portails en panne → consolidation sur la base, archive DIFFÉRÉE "
      "+ audit explicite (v1.51 exigence 5)",
      note >= 1 and ancien == 0, f"audits={note} anciens={ancien}")

# fermeture propre du 23/08 → 24/08 (miroir de la production : suivis du 23
# créés par le cycle précédent ; ici journée vide des deux côtés)
daily._trajets_reels_du_jour = lambda j: ([], [])
daily.executer_cycle_quotidien(J3 + timedelta(days=1), J3 + timedelta(days=2))

# ================================================================= réparation
print("\n== Réparation embarquée v1.30 (§0decies D1→D3) ==\n")


def _rel1(jour):            # passage 1 : le 20/08 est injoignable (R5)
    if jour == J1:
        return [], ["MZONEX (Timeout)"]
    return relire(jour)


reparation._relecture_portails = _rel1
reparation.executer_reparation_v130()

# --- J0 sain : intact ------------------------------------------------------------
arch_j0_apres = {h.id: dict(h.donnees or {}) for h in db.scalars(
    select(HistoriqueJournalier).where(HistoriqueJournalier.date_jour == J0))}
check("R1a : journée saine 19/08 — archives INTACTES (aucune réécriture)",
      arch_j0_avant == arch_j0_apres, "archive modifiée !")
conf_j0 = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "jour.verifie_conforme_v130")) or 0
check("R1b : marqueur « conforme » posé pour le 19/08", conf_j0 >= 1)
check("R1c : véhicule inconnu du portail (OBC-XXXXXX) — aucune fiche créée",
      veh("XXXXXX") is None)
inc = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "jour.repare_v130_vehicules_inconnus")) or 0
check("R1d : identifiant inconnu journalisé", inc >= 1)

# --- J1 sauté (portail en panne) : rien touché ----------------------------------
j1_marque = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "jour.repare_v130",
    AuditLog.details.like(f'%"{J1.isoformat()}"%'))) or 0
trajet_milieu = db.scalar(select(func.count(Trajet.id)).where(
    Trajet.suivi_id == suivi_de(v0906, J1).id,
    Trajet.heure_debut == dt(J1, 10, 19, 6))) or 0
check("R2a : 20/08 sauté tant que le portail est en panne (R5) — trajet du milieu "
      "PAS encore inséré, pas de marqueur jour",
      j1_marque == 0 and trajet_milieu == 0,
      f"marque={j1_marque}, t_milieu={trajet_milieu}")
glob = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "reparation_v130.terminee")) or 0
check("R2b : PAS de marqueur global tant qu'une journée manque de source (R5/D3)",
      glob == 0)

# --- J2 réparée (21/08) ----------------------------------------------------------
l4876 = lignes_affichees(v4876, J2)
check("R3a : 4876TBU — fin forcée 01:00 supprimée de l'écran, 5 lignes réelles",
      len(l4876) == 5 and all(t.heure_fin.date() == J2 for t in l4876),
      f"{[(t.heure_debut, t.heure_fin) for t in l4876]}")
reelles = [(t.heure_debut, t.heure_fin) for t in l4876]
fins_reelles = [f for _, f in reelles]
check("R3b : fins exactes du portail (fusion v1.28 décomposée, soirée retrouvée "
      "; ligne conforme conservée dans la tolérance ±2 min)",
      fins_reelles[0] == dt(J2, 13, 30, 39)
      and fins_reelles[1] == dt(J2, 14, 0, 1)
      and abs((fins_reelles[2] - dt(J2, 16, 41, 20)).total_seconds()) <= 120
      and fins_reelles[3] == dt(J2, 18, 58, 6)
      and fins_reelles[4] == dt(J2, 20, 38, 36), f"{reelles}")
s4876 = suivi_de(v4876, J2)
db.refresh(s4876)
tcj_lignes = int(sum((t.heure_fin - t.heure_debut).total_seconds()
                     for t in l4876))
check("R3c : TCJ recalculé = somme des vraies durées des lignes (AM-1) — le "
      "« 01:00 » ne gonfle plus rien",
      s4876.tcj_s == tcj_lignes and s4876.ttj_s >= s4876.tcj_s,
      f"tcj_s={s4876.tcj_s} attendu {tcj_lignes}")
toutes_lignes_4876 = list(db.scalars(select(Trajet).where(
    Trajet.suivi_id == s4876.id)).all())
masquees = [t for t in toutes_lignes_4876
            if t.statut_validation == StatutValidationTrajet.REJETE]
check("R3d : ligne fautive 17:02→01:00 CONSERVÉE en base, masquée (jamais effacée)",
      len(toutes_lignes_4876) == 6 and len(masquees) == 1
      and masquees[0].heure_fin == dt(J2, 1, 0) + timedelta(days=1),
      f"total={len(toutes_lignes_4876)} masquées={len(masquees)}")
l0916 = lignes_affichees(v0916, J2)
check("R3e : 0916TBV — géant 05:23→01:00 guéri à 05:23→06:10 (TCJ 19:36 → réel)",
      len(l0916) == 1 and l0916[0].heure_fin == dt(J2, 6, 10),
      f"{[(t.heure_debut, t.heure_fin) for t in l0916]}")
check("R3f : la manœuvre 0,047 km n'a PAS créé de ligne (§7/AM-2)",
      not any(t.heure_debut == dt(J2, 9, 16, 0) for t in l4876))
audit_masque = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "trajet.repare_v130_masque")) or 0
audit_corrige = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "trajet.repare_v130_corrige")) or 0
check("R3g : audits de masquage ET de guérison présents",
      audit_masque >= 1 and audit_corrige >= 1,
      f"masque={audit_masque} corrige={audit_corrige}")
arch_j2 = {h.vehicule_id: dict(h.donnees or {}) for h in db.scalars(
    select(HistoriqueJournalier).where(HistoriqueJournalier.date_jour == J2))}
a4876 = arch_j2.get(v4876.id, {})
# v1.53 (18/09/2026) — OÙ LIRE LES « 3 LIGNES FUSIONNÉES » : l'archive STOCKÉE
# conserve désormais les trajets BRUTS (arbitrage LSS « tous les trajets valides
# restent conservés individuellement en base ; les séquences d'affichage sont
# calculées séparément ») et la projection d'affichage (3 lignes) est produite à
# la LECTURE. L'intention de R3h est inchangée : plus aucune fin « 01:00 », les
# fins réelles apparaissent sur la vue d'écran ; on lit donc la vue d'affichage
# — et on vérifie EN PLUS que la base en garde bien 5 (aucun trajet perdu).
_h4876 = db.scalar(select(HistoriqueJournalier).where(
    HistoriqueJournalier.date_jour == J2,
    HistoriqueJournalier.vehicule_id == v4876.id))
fins_stockees = [t.get("heure_fin") for t in (a4876.get("trajets") or [])]
fins_archives = [t.get("heure_fin")
                 for t in (s_historique(_h4876, detail=True, seuils=get_seuils(db))
                           ["donnees"]["trajets"] or [])]
# Réalignement v1.31 (§0undecies E1, 24/08/2026 — prime sur AM-2 pour les
# arrêts < 20 min) : les pauses 13:30:39→13:47:55 (17 min) et 16:41:20→
# 16:57:11 (16 min) sont < 20 min → l'écran affiche 3 LIGNES fusionnées ;
# la BASE garde les 5 trajets réels (vérifié par R3c/R3f).
check("R3h : archive du 21/08 régénérée — plus AUCUNE fin « 01:00 », fins "
      "réelles (E1 v1.31 : 3 lignes fusionnées, fins 14:00:01 / 18:58:06 / "
      "20:38:36)", len(fins_archives) == 3
      and fins_archives == ["2026-08-21T14:00:01", "2026-08-21T18:58:06",
                            "2026-08-21T20:38:36"],
      f"affichage={fins_archives}")
check("R3h bis v1.53 — la BASE conserve bien les 5 trajets réels (aucune "
      "fusion stockée) et aucune fin « 01:00 »",
      len(fins_stockees) == 5
      and not any((f or "").endswith("T01:00:00") or (f or "").endswith("T01:00")
                  for f in fins_stockees),
      f"bruts={fins_stockees}")
reecr = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "jour.archive_reecrite_v130")) or 0
check("R3i : journal AVANT/APRÈS de la réécriture d'archive présent", reecr >= 1)

# --- passage 2 : le 20/08 redevient joignable → réparé, marqueur global ---------
reparation._relecture_portails = relire
reparation.executer_reparation_v130()
l0906 = lignes_affichees(v0906, J1)
check("R4a : 0906TBV 20/08 — le trajet perdu 10:19→13:44 est REINSÉRÉ (3 lignes)",
      len(l0906) == 3 and l0906[1].heure_debut == dt(J1, 10, 19, 6)
      and l0906[1].heure_fin == dt(J1, 13, 44, 52)
      and abs((l0906[1].distance_km or 0) - 10.33) < 0.01,
      f"{[(t.heure_debut, t.heure_fin) for t in l0906]}")
check("R4b : conducteur du trajet retrouvé (badge portail) renseigné",
      bool(l0906[1].conducteur_badge), f"{l0906[1].conducteur_badge!r}")
split_a = db.scalars(select(Trajet).where(
    Trajet.suivi_id == suivi_de(v5716, J1).id,
    Trajet.statut_validation != StatutValidationTrajet.REJETE)).all()
seg_b = db.scalars(select(Trajet).where(
    Trajet.suivi_id == suivi_de(v5716, J2).id)).all()
check("R4c : trajet 23:30→00:30 re-splité AM-3 (A à 23:59:59 au 20, B au 21 "
      "conservé et NON masqué)",
      len(split_a) == 1 and split_a[0].heure_fin == dt(J1, 23, 59, 59)
      and len(seg_b) == 1 and seg_b[0].suite_minuit
      and seg_b[0].heure_debut == dt(J2, 0, 0)
      and seg_b[0].heure_fin == dt(J2, 0, 30)
      and seg_b[0].statut_validation != StatutValidationTrajet.REJETE,
      f"A={[(t.heure_debut, t.heure_fin) for t in split_a]} "
      f"B={[(t.heure_debut, t.heure_fin, t.suite_minuit) for t in seg_b]}")
l3076affichees = lignes_affichees(v3076, J1)
l3076toutes = list(db.scalars(select(Trajet).where(
    Trajet.suivi_id == suivi_de(v3076, J1).id)).all())
check("R4d : fantôme 3076TBS sans source portail — masqué MAIS conservé",
      len(l3076affichees) == 0 and len(l3076toutes) == 1
      and l3076toutes[0].statut_validation == StatutValidationTrajet.REJETE)
glob = db.scalar(select(func.count(AuditLog.id)).where(
    AuditLog.action == "reparation_v130.terminee")) or 0
check("R4e : marqueur GLOBAL posé une fois tout à jour (D3)", glob >= 1)
alertes_rep = db.scalar(select(func.count(Alerte.id)).where(
    Alerte.type == TypeAlerte.REPARATION_DONNEES)) or 0
check("R4f : alertes récapitulatives visibles dans l'onglet Alertes (par journée "
      "réparée + récapitulatif global)", alertes_rep >= 3, f"{alertes_rep}")

# --- idempotence ----------------------------------------------------------------
nb_trajets = db.scalar(select(func.count(Trajet.id))) or 0
nb_audits = db.scalar(select(func.count(AuditLog.id))) or 0
nb_alertes = db.scalar(select(func.count(Alerte.id))) or 0
rep_c = reparation.executer_reparation_v130()
check("R5a : 3ᵉ passage = statut « deja_faite », zéro nouvelle écriture",
      rep_c.get("statut") == "deja_faite"
      and nb_trajets == (db.scalar(select(func.count(Trajet.id))) or 0)
      and nb_audits == (db.scalar(select(func.count(AuditLog.id))) or 0)
      and nb_alertes == (db.scalar(select(func.count(Alerte.id))) or 0))
l4876_bis = lignes_affichees(v4876, J2)
check("R5b : grille du 21/08 stable après re-exécution",
      [(t.heure_debut, t.heure_fin) for t in l4876_bis] == reelles)

db.close()
try:
    os.remove("/tmp/test_v130.db")
except OSError:
    pass
print(f"\n===== test_reparation_v130 : {R['ok']} OK / {R['ko']} KO =====")
sys.exit(1 if R["ko"] else 0)
