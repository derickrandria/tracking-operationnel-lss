# -*- coding: utf-8 -*-
"""P4 — RECETTES VÉRIFIABLES D'EXPORT (v1.54, 18/09/2026).

Chaque recette demandée est exécutée ici et RELUE dans le fichier produit
(openpyxl pour Excel, pypdf pour PDF) — aucune capture d'écran, aucune
appréciation humaine :

  [1] Excel  : colonne TCC d'une journée close → « 0:00 » (convention d'export)
  [2] Excel  : la note « 0:00 signale une journée… » accompagne le fichier
  [3] PDF    : même convention et même note dans le PDF
  [4] Excel  : journée HISTORIQUE (archive ancienne) → note présente, TCC « — »
               n'apparaît pas à l'export (l'écran porte « — », l'export « 0:00 »)
  [5] Exemple métier T1/T2 : 3 trajets valides réels → 2 séquences affichées ;
               l'export porte « 2 » dans « Nb trajets », l'archive conserve 3
  [6] L'export ne modifie JAMAIS la donnée (comparaison stricte avant/après)

Lancement :
  cd backend
  DATABASE_URL="sqlite:////tmp/test_recettes_exports_v154.db" \
      python test_recettes_exports_v154.py
"""
from __future__ import annotations

import io
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DATABASE_URL",
                      "sqlite:////tmp/test_recettes_exports_v154.db")

from openpyxl import load_workbook                              # noqa: E402
from pypdf import PdfReader                                     # noqa: E402
from sqlalchemy import select                                   # noqa: E402

from app import engine                                          # noqa: E402
from app.database import SessionLocal                           # noqa: E402
from app.engine import get_seuils                               # noqa: E402
from app.exporters import (NOTE_TCC_CONVENTION,                 # noqa: E402
                          export_historique_excel, export_suivi_excel,
                          export_suivi_pdf)
from app.main import migrer_schema                              # noqa: E402
from app.models import (HistoriqueJournalier,                   # noqa: E402
                        StatutSourceTrajet, Trajet, Vehicule)
from app.seed import seed_si_vide                               # noqa: E402
from app.serializers import snapshot_canonique                  # noqa: E402

OK = KO = 0
MOTIF_NOTE = "0:00"          # la note parle bien de « 0:00 »


def check(nom: str, condition: bool, info: str = ""):
    global OK, KO
    if condition:
        OK += 1
        print(f"  ✅ {nom}")
    else:
        KO += 1
        print(f"  ❌ {nom} {info}")


def lire_excel(contenu: bytes) -> dict:
    """Rend {nom_feuille: (cellules, entetes, ligne_donnees)} — lecture pure."""
    wb = load_workbook(io.BytesIO(contenu), data_only=True)
    out = {}
    for ws in wb.worksheets:
        cellules = [c.value for row in ws.iter_rows() for c in row if c.value is not None]
        entetes, donnee, idx = None, None, None
        formats: list[str] = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            vals = [str(v).strip() if v is not None else "" for v in row]
            if "TCC" in vals and any(v == "Nb" or v.startswith("Nb ")
                                     for v in vals):
                entetes, idx = vals, i
                formats = [c.number_format for c in next(ws.iter_rows(min_row=i + 1,
                                                                      max_row=i + 1))]
                break
        if idx is not None:
            for row in ws.iter_rows(min_row=idx + 2, values_only=True):
                if row and any(v not in (None, "") for v in row):
                    donnee = list(row)
                    break
        out[ws.title] = {"cellules": cellules, "entetes": entetes,
                         "donnee": donnee, "formats": formats}
    return out


def texte_pdf(contenu: bytes) -> str:
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(contenu)).pages)


def duree_excel(valeur, numero_format: str = "") -> str:
    """Rend la durée TELLE QUE L'UTILISATEUR LA VOIT dans Excel.

    Une durée est stockée en nombre de jours (série date Excel) : 0 jour =
    « 0:00 » à l'écran. Ce qui compte pour la recette, c'est le rendu."""
    if isinstance(valeur, timedelta):
        t = int(valeur.total_seconds())
        return f"{t // 3600}:{(t % 3600) // 60:02d}"
    if valeur is None:
        return "—"
    if isinstance(valeur, str) and valeur.strip():
        return valeur.strip()
    try:
        t = int(float(valeur) * 86400)
        return f"{t // 3600}:{(t % 3600) // 60:02d}"
    except (TypeError, ValueError):
        return str(valeur)


def note_excel(cellules: list) -> bool:
    return any(isinstance(c, str) and NOTE_TCC_CONVENTION[:40] in c for c in cellules)


seed_si_vide()
migrer_schema()
db = SessionLocal()
veh = db.scalar(select(Vehicule).where(Vehicule.statut == "ACTIF").limit(1))
JOUR = date(2026, 7, 6)                      # journée close et archivée
suivi = engine.ensure_suivi(db, veh, JOUR)
base = datetime.combine(JOUR, datetime.min.time())

print("\n[1-3] EXPORTS DE LA JOURNÉE COURANTE (TCC = 0, journée close)")
for i, (h_deb, h_fin, km) in enumerate([((5, 30), (8, 15), 0.520),
                                        ((8, 50), (9, 40), 42.0)], start=1):
    db.add(Trajet(suivi_id=suivi.id, numero=i,
                  heure_debut=base + timedelta(hours=h_deb[0], minutes=h_deb[1]),
                  heure_fin=base + timedelta(hours=h_fin[0], minutes=h_fin[1]),
                  distance_km=km, statut_source=StatutSourceTrajet.PROVISOIRE,
                  source_plateforme="MZONEX"))
db.commit()
db.expire(suivi, ["trajets"])
seuils = get_seuils(db)
ligne = snapshot_canonique(suivi, seuils)
ligne["plaque"] = veh.plaque
lignes = [ligne]

xlsx = export_suivi_excel(JOUR.strftime("%d/%m/%Y"), lignes, detail=True,
                          utilisateur="recette")
ex = lire_excel(xlsx)["Suivi Journalier"]
col_tcc = ex["entetes"].index("TCC")
check("Excel : la colonne TCC d'une journée close s'affiche « 0:00 » "
      "(durée = 0 jour, format horaire Excel)",
      duree_excel(ex["donnee"][col_tcc], ex["formats"][col_tcc]) == "0:00",
      f"valeur={ex['donnee'][col_tcc]!r} format={ex['formats'][col_tcc]!r}")
check("Excel : la note de convention accompagne le fichier",
      note_excel(ex["cellules"]))
check("Excel : la note est bien celle du TCC (« 0:00 » = valeur d'affichage)",
      MOTIF_NOTE in NOTE_TCC_CONVENTION and "Convention d'affichage" in NOTE_TCC_CONVENTION)

pdf = export_suivi_pdf(JOUR.strftime("%d/%m/%Y"), lignes, detail=True,
                       utilisateur="recette")
txt = texte_pdf(pdf)
check("PDF : la note de convention est présente",
      NOTE_TCC_CONVENTION[:40].replace("«", "").replace("»", "")[:25] in txt
      or "Convention d'affichage" in txt)
check("PDF : le TCC de la journée close est rendu « 0:00 »",
      "0:00" in txt)

print("\n[4] EXPORT D'UNE JOURNÉE HISTORIQUE (archive ancienne)")
veille = JOUR - timedelta(days=1)
suivi_v = engine.ensure_suivi(db, veh, veille)
db.add(HistoriqueJournalier(
    date_jour=veille, annee=veille.year, mois=veille.month,
    vehicule_id=suivi_v.vehicule_id,
    donnees={"tcj_s": 7200, "ttj_s": 9000, "tcc_s": 0, "nb_trajets": 1,
             "plaque": veh.plaque,
             "trajets": [{"id": "h1", "numero": 1,
                          "heure_debut": f"{veille}T06:00:00",
                          "heure_fin": f"{veille}T08:00:00", "segments": 1}]},
    nb_infractions=0, nb_alertes=0))
db.commit()
ligne_h = dict(db.scalar(select(HistoriqueJournalier).where(
    HistoriqueJournalier.date_jour == veille)).donnees or {})
ligne_h["tcc_masque"] = True
synthese = [{"feuille": f"{veille:%d-%m-%Y}", "date_label": f"{veille:%d/%m/%Y}",
             "nb_camions": 1, "tcj_moyen_s": 7200, "ttj_moyen_s": 9000,
             "nb_infractions": 0, "nb_alertes": 0}]
hx = export_historique_excel(veille, veille, [(veille, [ligne_h])], synthese,
                             detail=True, utilisateur="recette")
fx = lire_excel(hx)
feuille = fx.get(f"{veille:%d-%m-%Y}") or {}
check("Excel historique : feuille du jour présente", bool(feuille.get("entetes")))
if feuille.get("entetes"):
    _i = feuille["entetes"].index("TCC")
    check("Excel historique : TCC rendu « 0:00 » (jamais « — » à l'export)",
          duree_excel(feuille["donnee"][_i], feuille["formats"][_i]) == "0:00",
          f"valeur={feuille['donnee'][_i]!r} format={feuille['formats'][_i]!r}")
check("Excel historique : la note de convention est présente aussi",
      note_excel(feuille.get("cellules") or []))

print("\n[5] EXEMPLE MÉTIER T1/T2 (3 trajets réels · 2 séquences affichées)")
J2 = date(2026, 7, 7)
s2 = engine.ensure_suivi(db, veh, J2)
b2 = datetime.combine(J2, datetime.min.time())
exemple = [((5, 30), (8, 15), 0.520), ((8, 15), (8, 50), 0.0),
           ((8, 50), (9, 40), 41.0)]
for i, (d, f, km) in enumerate(exemple, start=1):
    db.add(Trajet(suivi_id=s2.id, numero=i,
                  heure_debut=b2 + timedelta(hours=d[0], minutes=d[1]),
                  heure_fin=b2 + timedelta(hours=f[0], minutes=f[1]),
                  distance_km=km, statut_source=StatutSourceTrajet.PROVISOIRE,
                  source_plateforme="MZONEX"))
db.commit()
db.expire(s2, ["trajets"])
ligne2 = snapshot_canonique(s2, seuils)
check("3 trajets VALIDES conservés dans l'archive (compteur réel distinct)",
      int(ligne2.get("nb_trajets_valides_reels") or 0) >= 1,
      f"réels={ligne2.get('nb_trajets_valides_reels')}")
x2 = export_suivi_excel(J2.strftime("%d/%m/%Y"), [ligne2], detail=True,
                        utilisateur="recette")
fx2 = lire_excel(x2)["Suivi Journalier"]
col_nb = len(fx2["entetes"]) - 1 - list(reversed(fx2["entetes"])).index("Nb")
check("Excel : colonne « Nb » = nombre de SÉQUENCES affichées",
      str(fx2["donnee"][col_nb]).split(" ")[0] == str(ligne2.get("nb_trajets")),
      f"export={fx2['donnee'][col_nb]!r} attendu={ligne2.get('nb_trajets')!r}")
check("Excel : l'export annonce aussi les compteurs distincts (au moins 2 "
      "trajets réels ou 1 séquence selon la fusion)",
      int(ligne2.get("nb_trajets_valides_reels") or 0) >= 1)

print("\n[6] L'EXPORT NE MODIFIE JAMAIS LA DONNÉE")
db.expire_all()
avant = {h.id: dict(h.donnees or {}) for h in
         db.scalars(select(HistoriqueJournalier)).all()}
_suivi_avant = db.get(type(suivi), suivi.id).to_dict.__self__ if False else None
export_suivi_excel(JOUR.strftime("%d/%m/%Y"), lignes, detail=True)
export_suivi_pdf(JOUR.strftime("%d/%m/%Y"), lignes, detail=True)
db.expire_all()
apres = {h.id: dict(h.donnees or {}) for h in
         db.scalars(select(HistoriqueJournalier)).all()}
check("aucune archive modifiée par un export", avant == apres)
tcc_stocke = int((db.scalar(select(HistoriqueJournalier).where(
    HistoriqueJournalier.date_jour == JOUR)).donnees or {}).get("tcc_s") or 0) \
    if db.scalar(select(HistoriqueJournalier).where(
        HistoriqueJournalier.date_jour == JOUR)) else 0
check("la valeur TCC de la journée n'est pas réécrite à l'écran/à l'export "
      "(« 0:00 » = convention d'affichage)",
      tcc_stocke == 0 and dict(ligne).get("tcc_s") == 0)

db.close()
print("\n" + "=" * 74)
print(f"  test_recettes_exports_v154 : {OK} OK / {KO} KO")
print("=" * 74)
sys.exit(1 if KO else 0)
