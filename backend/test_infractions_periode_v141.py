# -*- coding: utf-8 -*-
"""Tests v1.41 — §0octies decies L1→L3 (arbitrages LSS du 27/08/2026) :
barre de défilement unifiée (L1), période Début/Fin des infractions (L2),
filtre « type d'infraction » (L3).

[A] L2 — enddatetime VERBATIM importé (franchissement de minuit) ;
[B] L2 — fin absente → « — » à l'écran et dans l'export (jamais d'invention) ;
[C] L2 — rattrapage des lignes déjà en base par l'upsert I5 (pas de doublon ;
     workflow I4 intact) ;
[D] L2 — export 12 colonnes, ordre dicté, Début/Fin formatés JJ/MM/AAAA HH:MM:SS ;
[E] L3 — filtre famille exact + endpoint /infractions/familles trié + exports
     héritant du filtre (écran = export §A.2) ;
[F] L1 — garde-fou code : toutes les grilles en défilement interne pleine
     hauteur (patron de l'onglet Suivi Journalier).

Exécution (TOUJOURS sur une base de test !) :
  DATABASE_URL="sqlite:////tmp/test_v141.db" SIM_ENABLE=0 python3 test_infractions_periode_v141.py
La base est SUPPRIMÉE à la fin (protection des données production).
"""
import io
import os
os.environ.setdefault("SIM_ENABLE", "0")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/test_v141.db")
os.environ["YMANE_ACTIVE"] = "1"
import sys
from datetime import date, time

from sqlalchemy import delete, func, select

_db_url = os.environ.get("DATABASE_URL", "")
assert "/tmp/" in _db_url, f"REFUS — DATABASE_URL hors /tmp : {_db_url!r}"

from app.database import SessionLocal
from app.models import (AuditLog, GraviteInfraction, Infraction,
                        SourceEvenement, TypeInfraction, Vehicule)
from app.seed import seed_si_vide
from app.main import migrer_schema
from app import ymane_import as yim
from app.serializers import s_infraction

R = {"ok": 0, "ko": 0}


def check(nom, cond, info=""):
    if cond:
        R["ok"] += 1
        print(f"  ✅ {nom}")
    else:
        R["ko"] += 1
        print(f"  ❌ {nom} {info}")


DB_FILE = "/tmp/test_v141.db"
try:                                                 # SUPPRESSION AVANT seed_si_vide
    os.remove(DB_FILE)
except OSError:
    pass
seed_si_vide()
migrer_schema()
db = SessionLocal()
plaque_seed = db.scalar(select(Vehicule.plaque).limit(1))
db.close()

# Échantillon RÉEL du portail (DailyRest, 23→24/08/2026 — exemple dicté de
# l'exploitant : seuil 9:00, repos 7.76 h) avec fin franchissant minuit.
LIGNE_REPOS = {
    "exceptionid": 6152407, "levellabel": "Alarm",
    "parameterlabel": "menu_daily_rest_time", "parameter": "DailyRest",
    "startdatetime": "2026-08-23 22:30:47", "enddatetime": "2026-08-24 06:16:23",
    "threshold": "9.00", "totalduration": "7.76",
    "startgps": "[49.1401,-18.4728]", "endgps": "[49.14,-18.4726]",
    "vehiclename": plaque_seed, "drivername": "Chauffeur Repos Test",
}
LIGNE_NUIT_SANS_FIN = {
    "exceptionid": 6151641, "levellabel": "Alert",
    "parameterlabel": "menu_night_driving", "parameter": "NightDrive",
    "startdatetime": "2026-08-23 01:21:59", "enddatetime": "",
    "threshold": "18:00:00 to 05:30:00", "totalduration": None,
    "startgps": "[47.522,-18.8832]",
    "vehiclename": plaque_seed, "drivername": "Chauffeur Nuit Test",
}
LIGNE_VITESSE = {
    "exceptionid": 6154029, "levellabel": "Alarm",
    "parameterlabel": "menu_speeding", "parameter": "Speeding",
    "startdatetime": "2026-08-26 13:03:59", "enddatetime": "2026-08-26 13:04:16",
    "threshold": "30.00", "maxvalue": "34", "totalduration": "0.004722",
    "startgps": "[48.27885,-18.95319]",
    "vehiclename": plaque_seed, "drivername": "Chauffeur Vitesse Test",
}


class ApiListe:
    def __init__(self, lignes):
        self._lignes = lignes
    def infractions_recentes(self, jour):
        return [dict(x) for x in self._lignes]


# -------------------------------------------------------------------- [A] fin verbatim
print("\n[A] L2 — fin verbatim Ym@ne (franchissement de minuit)")
st = yim.cycle_ymane_avec_alerte(api=ApiListe([LIGNE_REPOS]))
db = SessionLocal()
inf = db.scalar(select(Infraction).where(Infraction.ymane_id == "6152407"))
check("A1 import ok (1 nouvelle)", st.get("ok") and st.get("nouvelles") == 1, repr(st))
check("A1 début conservé = 23/08/2026 22:30:47",
      bool(inf) and inf.date_jour == date(2026, 8, 23) and inf.heure == time(22, 30, 47),
      repr((inf.date_jour, inf.heure)) if inf else "absente")
check("A1 FIN verbatim = 24/08/2026 06:16:23 (J+1, jamais devinée)",
      bool(inf) and inf.date_fin == date(2026, 8, 24) and inf.heure_fin == time(6, 16, 23),
      repr((inf.date_fin, inf.heure_fin)) if inf else "absente")
check("A1 seuil 9h (horaire, affichage I3) + validation NON_TRAITEE (I4 intact)",
      bool(inf) and inf.seuil_unite == "s" and inf.seuil_reference == 32400.0
      and inf.validation == "NON_TRAITEE")
s = s_infraction(inf)
check("A1 sérialiseur expose date_fin/heure_fin",
      s["date_fin"] == "2026-08-24" and s["heure_fin"] == "06:16:23")
db.close()

# ------------------------------------------------------------------ [B] fin absente → « — »
print("\n[B] L2 — fin absente : jamais d'invention")
yim.cycle_ymane_avec_alerte(api=ApiListe([LIGNE_NUIT_SANS_FIN]))
db = SessionLocal()
inf0 = db.scalar(select(Infraction).where(Infraction.ymane_id == "6151641"))
check("B1 fin absente → colonnes NULL", bool(inf0) and inf0.date_fin is None
      and inf0.heure_fin is None)
s0 = s_infraction(inf0) if inf0 else {}
from app.routers.surveillance import _lignes_infractions, _periode_txt, HEADERS_INF
check("B1 export « — » pour la fin, début présent",
      _periode_txt(s0.get("date_fin"), s0.get("heure_fin")) == "—"
      and _periode_txt(s0["date_jour"], s0["heure"]) == "23/08/2026 01:21:59")
db.close()

# ---------------------------------------------- [C] rattrapage upsert (I5) + I4 intact
print("\n[C] L2 — rattrapage des lignes anciennes par la relecture J-8→J")
db = SessionLocal()          # ancienne ligne « datée de la v1.40 » : sans fin
db.add(Infraction(date_jour=date(2026, 8, 26), heure=time(13, 3, 59),
                  vehicule_id=db.scalar(select(Vehicule.id).limit(1)),
                  type=TypeInfraction.EXCES_VITESSE,
                  gravite=GraviteInfraction.CRITIQUE,
                  source=SourceEvenement.MZONEX,
                  exterieure=True, niveau="ALARME", nom_ymane="Excès de Vitesse",
                  ymane_id="6154029", validation="VALIDE", observation="gardée"))
db.commit(); db.close()
st2 = yim.cycle_ymane_avec_alerte(api=ApiListe([LIGNE_VITESSE]))
db = SessionLocal()
inv = db.scalars(select(Infraction).where(Infraction.ymane_id == "6154029")).all()
check("C1 aucune nouvelle ligne (anti-doublon I5)", len(inv) == 1 and st2.get("nouvelles") == 0,
      f"lignes={len(inv)} stats={st2}")
check("C1 la ligne rattrapée : fin = 26/08/2026 13:04:16",
      bool(inv) and inv[0].date_fin == date(2026, 8, 26)
      and inv[0].heure_fin == time(13, 4, 16))
check("C1 workflow I4 jamais retouché (VALIDE + observation conservées)",
      bool(inv) and inv[0].validation == "VALIDE" and inv[0].observation == "gardée")
db.close()

# ------------------------------------------------------------- [D] export 12 colonnes
print("\n[D] L2/L3 — export : 12 colonnes, ordre dicté, formats")
check("D1 en-têtes = les 12 dictées (I3 amendé §0octies decies)",
      HEADERS_INF == ["Date", "Heure", "Immatriculation", "Chauffeur",
                      "Infraction", "Niveau", "Seuil", "Coordonnées GPS",
                      "Début de l'infraction", "Fin de l'infraction",
                      "Validation", "Observation"], repr(HEADERS_INF))
db = SessionLocal()
toutes = db.scalars(select(Infraction).where(Infraction.exterieure.is_(True))).all()
lignes = _lignes_infractions([s_infraction(i) for i in toutes])
l_repos = next((l for l in lignes if l[4] == "Temps de Repos Journalier"), None)
check("D1 ligne repos : Début « 23/08/2026 22:30:47 », Fin « 24/08/2026 06:16:23 »",
      bool(l_repos) and l_repos[8] == "23/08/2026 22:30:47"
      and l_repos[9] == "24/08/2026 06:16:23", repr(l_repos))
db.close()

# ---------------------------------------------------------------- [E] filtre famille L3
print("\n[E] L3 — filtre « type d'infraction » (écran = export)")
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)


def _jeton(u, p):
    r = client.post("/api/auth/login", json={"username": u, "password": p})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}

h = _jeton("tracking", "Tracking@2026")

rf = client.get("/api/infractions/familles", headers=h)
check("E1 /infractions/familles = familles présentes, triées, verbatim",
      rf.status_code == 200 and rf.json() == sorted(rf.json())
      and "Temps de Repos Journalier" in rf.json()
      and "Excès de Vitesse" in rf.json(), repr(rf.json()))

rq = client.get("/api/infractions?famille=Conduite de Nuit", headers=h).json()
check("E2 filtre famille exact : seule la famille demandée",
      rq["total"] == 1 and all(i["nom"] == "Conduite de Nuit" for i in rq["items"]),
      f"total={rq['total']}")
rq2 = client.get("/api/infractions?famille=Famille Inexistante", headers=h).json()
check("E2 famille inconnue → 0 ligne (aucune invention)", rq2["total"] == 0)

import openpyxl
rx = client.get("/api/infractions/export.xlsx?famille=" +
                "Temps de Repos Journalier", headers=h)
check("E3 export xlsx 200", rx.status_code == 200, f"code={rx.status_code}")
wb = openpyxl.load_workbook(io.BytesIO(rx.content))
feuille = wb.active
toutes = [list(r) for r in feuille.iter_rows(values_only=True) if any(r)]
i_entete = next(i for i, r in enumerate(toutes) if r[0] == "Date")
entetes = [v for v in toutes[i_entete] if v]
donnees = toutes[i_entete + 1:]
check("E3 export = 12 colonnes dictées", entetes == HEADERS_INF, repr(entetes))
check("E3 export filtré : 1 ligne, famille repos, Fin au J+1",
      len(donnees) == 1 and donnees[0][4] == "Temps de Repos Journalier"
      and donnees[0][9] == "24/08/2026 06:16:23",
      repr(donnees))
rp = client.get("/api/infractions/export.pdf?famille=Conduite de Nuit", headers=h)
check("E3 export pdf hérite du filtre (200)", rp.status_code == 200
      and len(rp.content) > 400)

# ---------------------------------------------------------------- [F] L1 garde-fou code
print("\n[F] L1 — barre horizontale toujours visible (patron Suivi Journalier)")
# RÉALIGNEMENT v1.42 (déclaré dans la loi, §0octies decies « Mise au point du
# 29/08/2026 ») : Historique et Parametres sont des pages MULTI-SECTIONS →
# défilement vertical DE PAGE + zones internes bornées ; elles sont désormais
# couvertes par test_historique_scroll_v142.py. Les 4 pages à UNE zone de
# données gardent le patron L1 plein écran, vérifié ici.
import pathlib
PAGES = {"Infractions.tsx", "Vehicules.tsx", "Conducteurs.tsx",
         "Conduite.tsx"}
base = pathlib.Path("/home/user/frontend/src/pages")
for nom in sorted(PAGES):
    src = (base / nom).read_text(encoding="utf-8")
    check(f"F grille interne pleine hauteur : {nom}",
          "flex h-full flex-col gap-4" in src
          and "contenuClasse" in src and "overflow-auto" in src,
          "patron absent")
src_suivi = (base / "Suivi.tsx").read_text(encoding="utf-8")
check("F référence Suivi inchangée (min-h-0 flex-1 overflow-auto)",
      "min-h-0 flex-1 overflow-auto" in src_suivi)
check("F aucune grille « overflow-x-auto » résiduelle hors conteneur L1",
      all("overflow-x-auto" not in (base / n).read_text(encoding="utf-8")
          for n in ("Infractions.tsx", "Vehicules.tsx", "Conducteurs.tsx")))

print(f"\n==== {R['ok']} OK / {R['ko']} KO ====")
try:
    os.remove(DB_FILE)
except OSError:
    pass
sys.exit(0 if R["ko"] == 0 else 1)
