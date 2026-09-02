"""Collecteur de données GPS (§10) — source SIMULATEUR.

MZoneX et CamtrackPro ne fournissant aucune API, la production utilisera le
scraper Playwright (`scrapers.py`). En attendant les identifiants, ce
simulateur reproduit fidèlement le flux : chaque camion suit un scénario
logistique réaliste (BASE → raffinerie TMT → dépôt → retour), émet des
événements GPS horodatés (position, vitesse, état moteur, événements OBC)
qui alimentent le moteur de calcul (§7) via la même entrée `ingest_event()`
que le scraper réel.

Scénarios couverts : mission simple Tana, mission Moramanga, double mission
(cas MMG §6.3), livraison Antsirabe/Fianarantsoa, repos, conduite à risque
(excès de vitesse, freinages brusques), départ tardif, boîtier GPS muet.
"""
import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import select

from .config import ROUTES, now_local
from .database import SessionLocal
from .engine import appliquer_champs_suivi, ensure_suivi, ingest_event
from .models import (EvenementGPS, SourceEvenement, TypeEvenement, Vehicule)

log = logging.getLogger("lss.sim")


# ------------------------------------------------------------------ helpers
def _longueur_route(points) -> float:
    from .engine import haversine_km
    return sum(haversine_km(points[i][1], points[i][2], points[i + 1][1], points[i + 1][2])
               for i in range(len(points) - 1))


def _inverser(route):
    return [(nom, lat, lon) for nom, lat, lon in reversed(route)]


# ------------------------------------------------------------------ camion simulé
class CamionSim:
    def __init__(self, vehicule_id: str, rng: random.Random, etapes: list,
                 debut: datetime, agressif: bool = False,
                 coupure_gps: datetime | None = None):
        self.vehicule_id = vehicule_id
        self.rng = rng
        self.etapes = list(etapes)
        self.etape = None
        self.ts = debut
        self.agressif = agressif
        self.coupure_gps = coupure_gps
        self.route = None
        self.km_parcourus = 0.0
        self.km_total = 0.0
        self.vitesse_cible = 55.0
        self.fin_etape = None
        self.pos = None
        self._prev_ts = None

    def avancer_jusqua(self, horizon: datetime, db, vehicule: Vehicule, sec: bool = False):
        """Génère les événements jusqu'à `horizon`.

        sec=True : avance « à sec » (aucune écriture) — utilisé au redémarrage
        pour rattraper l'heure courante sans dupliquer les événements déjà en base.
        """
        self._sec = sec
        garde = 0
        while self.ts <= horizon and garde < 6000:
            garde += 1
            if self.coupure_gps and self.ts >= self.coupure_gps:
                self.ts = horizon + timedelta(days=1)
                break
            if self.etape is None:
                if not self.etapes:
                    self.ts = horizon + timedelta(days=1)
                    break
                if sec:
                    self.etape = self.etapes.pop(0)
                    self._tick_sec()
                else:
                    self._demarrer_etape(self.etapes.pop(0), db, vehicule)
            elif sec:
                self._tick_sec()
            else:
                self._tick_etape(db, vehicule)
        self._sec = False

    def _tick_sec(self):
        """Consomme l'étape courante sans aucune écriture en base."""
        e = self.etape
        if e is None:
            return
        if e["op"] == "set":
            self.etape = None
            self.ts += timedelta(minutes=3)
        elif e["op"] == "drive":
            pts = e["points"]
            duree_h = _longueur_route(pts) / max(25.0, e.get("vitesse", 55.0))
            self.pos = (pts[-1][1], pts[-1][2])
            self.km_parcourus = 0.0
            self.etape = None
            self.ts += timedelta(hours=duree_h, minutes=self.rng.randint(2, 6))
        else:  # stop
            self.ts += timedelta(minutes=e["minutes"] + 5)
            self.etape = None

    def _demarrer_etape(self, etape: dict, db, vehicule: Vehicule):
        # L'étape n'est marquée « en cours » qu'UNE FOIS toute sa préparation
        # terminée : si une erreur survient entre-temps (ex. accès base sous
        # Windows/SQLite), on ne laisse jamais un état incohérent du type
        # « étape drive active mais route None » (cf. TypeError route[-1]).
        op = etape["op"]
        suivi = ensure_suivi(db, vehicule, self.ts.date())
        if op == "set":
            self._emettre(db, vehicule,
                          vehicule.last_lat if vehicule.last_lat is not None else -18.8792,
                          vehicule.last_lng if vehicule.last_lng is not None else 47.5079,
                          vehicule.last_adresse or "Base LSS — Antananarivo",
                          0, "ON", None)
            appliquer_champs_suivi(db, suivi, etape.get("champs", {}), ts=self.ts)
            self.etape = None
            self.ts += timedelta(seconds=self.rng.randint(60, 240))
        elif op == "drive":
            self.route = etape["points"]
            self.km_parcourus = 0.0
            self.km_total = max(1.0, _longueur_route(self.route))
            self.vitesse_cible = etape.get("vitesse", self.rng.randint(52, 62))
            if etape.get("champs"):
                appliquer_champs_suivi(db, suivi, etape["champs"], ts=self.ts)
            if self.pos is None:
                self.pos = (self.route[0][1], self.route[0][2])
            self._prev_ts = self.ts
            self._emettre(db, vehicule, *self._position_actuelle(), self.vitesse_cible, "ON", None)
            self.ts += timedelta(seconds=self.rng.randint(150, 330))
            self.etape = etape
        elif op == "stop":
            self.fin_etape = self.ts + timedelta(minutes=etape["minutes"])
            self._emettre(db, vehicule, *self._position_actuelle(), 0,
                          etape.get("moteur", "OFF"), None)
            if etape.get("champs"):
                appliquer_champs_suivi(db, suivi, etape["champs"], ts=self.ts)
            self.ts += timedelta(seconds=self.rng.randint(420, 900))
            self.etape = etape

    def _tick_etape(self, db, vehicule: Vehicule):
        etape = self.etape
        if etape["op"] == "stop":
            if self.ts >= self.fin_etape:
                self.etape = None
                self.ts = self.fin_etape + timedelta(seconds=self.rng.randint(30, 120))
            else:
                self._emettre(db, vehicule, *self._position_actuelle(), 0,
                              etape.get("moteur", "OFF"), None)
                self.ts += timedelta(seconds=self.rng.randint(420, 900))
            return
        # Garde-fou : une étape « drive » sans route (état orphelin après une
        # erreur d'écriture base lors du démarrage de l'étape) est abandonnée
        # proprement au lieu de planter sur route[-1] (TypeError NoneType).
        if not self.route:
            self.etape = None
            self.km_parcourus = 0.0
            self.ts += timedelta(seconds=120)
            return
        # conduite
        prev = self._prev_ts or self.ts
        dt_h = max(0.0, (self.ts - prev).total_seconds() / 3600.0)
        self._prev_ts = self.ts
        self.km_parcourus += self.vitesse_cible * dt_h

        vitesse = max(18.0, self.vitesse_cible + self.rng.uniform(-9, 7))
        type_force = None
        if self.agressif:
            tirage = self.rng.random()
            if tirage < 0.045:
                vitesse = self.rng.uniform(93, 118)          # excès de vitesse
            elif tirage < 0.075:
                type_force = TypeEvenement.FREINAGE_BRUSQUE
                vitesse *= 0.45
            elif tirage < 0.10:
                type_force = TypeEvenement.ACCELERATION_BRUSQUE
                vitesse = min(90.0, vitesse * 1.35)

        if self.km_parcourus >= self.km_total:
            self.pos = (self.route[-1][1], self.route[-1][2])
            nom = self.route[-1][0]
            self._emettre(db, vehicule, self.pos[0], self.pos[1], nom, 0, "ON", None)
            self.etape = None
            self.km_parcourus = 0.0
            self.ts += timedelta(seconds=self.rng.randint(45, 180))
            return

        self._emettre(db, vehicule, *self._position_actuelle(), vitesse, "ON", type_force)
        self.ts += timedelta(seconds=self.rng.randint(150, 330))

    def _position_actuelle(self):
        """Interpole la position le long de la route courante."""
        from .engine import haversine_km
        if not self.route:
            lat, lon = self.pos or (-18.8792, 47.5079)
            return lat, lon, "Base LSS — Antananarivo"
        reste = self.km_parcourus
        pts = self.route
        for i in range(len(pts) - 1):
            nom1, lat1, lon1 = pts[i]
            nom2, lat2, lon2 = pts[i + 1]
            seg = haversine_km(lat1, lon1, lat2, lon2)
            if reste <= seg:
                t = 0.0 if seg == 0 else reste / seg
                lat = lat1 + (lat2 - lat1) * t
                lon = lon1 + (lon2 - lon1) * t
                pk = int(self.km_parcourus)
                label = (f"RN · PK {pk} (après {nom1})" if t < 0.5
                         else f"RN · PK {pk} (avant {nom2})")
                self.pos = (lat, lon)
                return lat, lon, label
            reste -= seg
        self.pos = (pts[-1][1], pts[-1][2])
        return pts[-1][1], pts[-1][2], pts[-1][0]

    def _emettre(self, db, vehicule, lat, lon, adresse, vitesse, moteur, type_force):
        ingest_event(db, vehicule, self.ts, round(lat, 6), round(lon, 6), adresse,
                     round(float(vitesse), 1), moteur, type_force,
                     SourceEvenement.SIMULATEUR, publier=True)


# ------------------------------------------------------------------ scénarios
def _champs_produit(rng, depot, ot):
    from .config import DISTRIBUTEURS, PRODUITS
    return {"produit": rng.choice(PRODUITS), "distributeur": rng.choice(DISTRIBUTEURS),
            "depot_recepteur": depot, "numero_ot": f"OT-{now_local():%Y%m%d}-{ot:04d}"}


def scenario_tana(rng, ot, agressif=False, pause_reglementaire=True):
    """BASE → TMT (chargement) → dépôt DABI (Tana) → retour base."""
    ch = _champs_produit(rng, "DABI", ot)
    aller = []
    if pause_reglementaire:
        aller = [
            {"op": "drive", "points": ROUTES["TANA_MMG"], "vitesse": rng.randint(54, 60)},
            {"op": "stop", "minutes": rng.randint(20, 35), "moteur": "OFF"},
            {"op": "drive", "points": ROUTES["MMG_TMT"], "vitesse": rng.randint(52, 58)},
        ]
    else:
        # trajet long d'une seule traite à vitesse de citerne chargée (~45 km/h) :
        # la conduite continue dépasse alors 4h30 → infraction DEPASSEMENT_TCC
        aller = [{"op": "drive", "points": ROUTES["TANA_MMG"] + ROUTES["MMG_TMT"][1:],
                  "vitesse": rng.randint(44, 49)}]
    return [
        {"op": "set", "champs": {**ch, "situation": "En transit pour chargement",
                                  "statut_camion": "VIDE"}},
        *aller,
        {"op": "stop", "minutes": rng.randint(14, 25), "moteur": "OFF",
         "champs": {"situation": "En attente de chargement"}},
        {"op": "stop", "minutes": rng.randint(30, 45), "moteur": "OFF",
         "champs": {"situation": "En cours de chargement"}},
        {"op": "set", "champs": {"situation": "En transit pour livraison TMT-TANA",
                                  "statut_camion": "CHARGÉ"}},
        {"op": "drive", "points": _inverser(ROUTES["MMG_TMT"]), "vitesse": rng.randint(50, 57)},
        {"op": "stop", "minutes": rng.randint(18, 28), "moteur": "OFF"},
        {"op": "drive", "points": _inverser(ROUTES["TANA_MMG"]), "vitesse": rng.randint(50, 57)},
        {"op": "drive", "points": ROUTES["TANA_DABI"], "vitesse": 38},
        {"op": "stop", "minutes": rng.randint(12, 20), "moteur": "ON",
         "champs": {"situation": "En attente de déchargement"}},
        {"op": "stop", "minutes": rng.randint(25, 40), "moteur": "OFF",
         "champs": {"situation": "En cours de déchargement"}},
        {"op": "set", "champs": {"situation": "En attente bon après déchargement ABI",
                                  "statut_camion": "LIBRE"}},
        {"op": "drive", "points": _inverser(ROUTES["TANA_DABI"]), "vitesse": 35,
         "champs": {"situation": "Retour Base après déchargement ABI"}},
        {"op": "stop", "minutes": 600, "moteur": "OFF",
         "champs": {"situation": "Repos chauffeur"}},
    ]


def scenario_mmg(rng, ot):
    """BASE → TMT → DMMG (Moramanga). Le camion reste à MMG le soir."""
    ch = _champs_produit(rng, "DMMG", ot)
    return [
        {"op": "set", "champs": {**ch, "situation": "En transit pour chargement",
                                  "statut_camion": "VIDE"}},
        {"op": "drive", "points": ROUTES["TANA_MMG"], "vitesse": rng.randint(54, 60)},
        {"op": "stop", "minutes": rng.randint(18, 30), "moteur": "OFF"},
        {"op": "drive", "points": ROUTES["MMG_TMT"], "vitesse": rng.randint(52, 58)},
        {"op": "stop", "minutes": rng.randint(12, 20), "moteur": "OFF",
         "champs": {"situation": "En attente de chargement"}},
        {"op": "stop", "minutes": rng.randint(30, 40), "moteur": "OFF",
         "champs": {"situation": "En cours de chargement"}},
        {"op": "set", "champs": {"situation": "En transit pour livraison TMT-MMG",
                                  "statut_camion": "CHARGÉ"}},
        {"op": "drive", "points": _inverser(ROUTES["MMG_TMT"]), "vitesse": rng.randint(52, 58)},
        {"op": "stop", "minutes": rng.randint(12, 18), "moteur": "ON",
         "champs": {"situation": "En attente de déchargement"}},
        {"op": "stop", "minutes": rng.randint(25, 35), "moteur": "OFF",
         "champs": {"situation": "En cours de déchargement"}},
        {"op": "set", "champs": {"situation": "En attente bon après déchargement MMG",
                                  "statut_camion": "LIBRE"}},
        {"op": "stop", "minutes": 480, "moteur": "OFF",
         "champs": {"situation": "Repos chauffeur"}},
    ]


def scenario_mmg_double(rng, ot):
    """Cas Moramanga §6.3 : Tana→MMG (mission 1) puis MMG→TMT→Tana (mission 2)
    — deux boucles logistiques, deux missions détectées automatiquement."""
    ch1 = _champs_produit(rng, "DMMG", ot)
    ch2 = _champs_produit(rng, "DABI", ot + 1000)
    return [
        {"op": "set", "champs": {**ch1, "situation": "En cours de chargement",
                                  "statut_camion": "VIDE"}},
        {"op": "stop", "minutes": rng.randint(30, 40), "moteur": "OFF"},
        {"op": "set", "champs": {"situation": "En transit pour livraison TMT-MMG",
                                  "statut_camion": "CHARGÉ"}},
        {"op": "drive", "points": ROUTES["TANA_MMG"], "vitesse": rng.randint(52, 58)},
        {"op": "stop", "minutes": rng.randint(12, 18), "moteur": "ON",
         "champs": {"situation": "En attente de déchargement"}},
        {"op": "stop", "minutes": rng.randint(25, 35), "moteur": "OFF",
         "champs": {"situation": "En cours de déchargement"}},
        {"op": "set", "champs": {"situation": "En attente bon après déchargement MMG",
                                  "statut_camion": "LIBRE"}},
        # ---- deuxième boucle : nouvelle mission sans retour à la base ----
        {"op": "set", "champs": {**ch2, "situation": "En transit pour chargement",
                                  "statut_camion": "VIDE"}},
        {"op": "drive", "points": ROUTES["MMG_TMT"], "vitesse": rng.randint(52, 58)},
        {"op": "stop", "minutes": rng.randint(15, 22), "moteur": "OFF",
         "champs": {"situation": "En attente de chargement"}},
        {"op": "stop", "minutes": rng.randint(30, 40), "moteur": "OFF",
         "champs": {"situation": "En cours de chargement"}},
        {"op": "set", "champs": {"situation": "En transit pour livraison TMT-TANA",
                                  "statut_camion": "CHARGÉ"}},
        {"op": "drive", "points": _inverser(ROUTES["MMG_TMT"]), "vitesse": rng.randint(50, 57)},
        {"op": "stop", "minutes": rng.randint(18, 26), "moteur": "OFF"},
        {"op": "drive", "points": _inverser(ROUTES["TANA_MMG"]), "vitesse": rng.randint(50, 57)},
        {"op": "stop", "minutes": rng.randint(30, 40), "moteur": "OFF",
         "champs": {"situation": "En cours de déchargement"}},
        {"op": "set", "champs": {"situation": "En attente bon après déchargement ABI",
                                  "statut_camion": "LIBRE"}},
        {"op": "stop", "minutes": 420, "moteur": "OFF",
         "champs": {"situation": "Repos chauffeur"}},
    ]


def scenario_abe(rng, ot):
    """Chargement Tana → livraison DABE (Antsirabe) → retour base."""
    ch = _champs_produit(rng, "DABE", ot)
    return [
        {"op": "set", "champs": {**ch, "situation": "En cours de chargement",
                                  "statut_camion": "VIDE"}},
        {"op": "stop", "minutes": rng.randint(30, 42), "moteur": "OFF"},
        {"op": "set", "champs": {"situation": "En transit pour livraison TANA-ABE",
                                  "statut_camion": "CHARGÉ"}},
        {"op": "drive", "points": ROUTES["TANA_ABE"], "vitesse": rng.randint(54, 60)},
        {"op": "stop", "minutes": rng.randint(12, 18), "moteur": "ON",
         "champs": {"situation": "En attente de déchargement"}},
        {"op": "stop", "minutes": rng.randint(25, 35), "moteur": "OFF",
         "champs": {"situation": "En cours de déchargement"}},
        {"op": "set", "champs": {"situation": "Retour Tana après déchargement ABE",
                                  "statut_camion": "LIBRE"}},
        {"op": "drive", "points": _inverser(ROUTES["TANA_ABE"]), "vitesse": rng.randint(52, 58)},
        {"op": "stop", "minutes": 600, "moteur": "OFF",
         "champs": {"situation": "Repos chauffeur"}},
    ]


def scenario_fnr(rng, ot):
    """Chargement Tana → livraison DFIA (Fianarantsoa) avec pauses réglementaires."""
    ch = _champs_produit(rng, "DFIA", ot)
    abe_ambositra = ROUTES["ABE_FNR"][:2]      # Antsirabe → Ambositra
    ambositra_fnr = [ROUTES["ABE_FNR"][1], ROUTES["ABE_FNR"][2]]
    return [
        {"op": "set", "champs": {**ch, "situation": "En cours de chargement",
                                  "statut_camion": "VIDE"}},
        {"op": "stop", "minutes": rng.randint(30, 42), "moteur": "OFF"},
        {"op": "set", "champs": {"situation": "En transit pour livraison TANA-FNR",
                                  "statut_camion": "CHARGÉ"}},
        {"op": "drive", "points": ROUTES["TANA_ABE"], "vitesse": rng.randint(54, 60)},
        {"op": "stop", "minutes": rng.randint(20, 30), "moteur": "OFF"},
        {"op": "drive", "points": abe_ambositra, "vitesse": rng.randint(52, 58)},
        {"op": "stop", "minutes": rng.randint(18, 28), "moteur": "OFF"},
        {"op": "drive", "points": ambositra_fnr, "vitesse": rng.randint(50, 56)},
        {"op": "stop", "minutes": rng.randint(25, 35), "moteur": "OFF",
         "champs": {"situation": "En cours de déchargement"}},
        {"op": "set", "champs": {"situation": "En attente bon après déchargement FNR",
                                  "statut_camion": "LIBRE"}},
        {"op": "stop", "minutes": 420, "moteur": "OFF",
         "champs": {"situation": "Repos chauffeur"}},
    ]


# ------------------------------------------------------------------ orchestrateur
CAMIONS: dict[str, CamionSim] = {}
_ot = {"n": 0}


def _prochain_ot():
    _ot["n"] += 1
    return _ot["n"]


def initialiser(db) -> int:
    """Construit les scénarios du jour pour toute la flotte instrumentée."""
    global CAMIONS
    CAMIONS = {}
    ng = now_local()
    rng = random.Random(20260730 + int(ng.strftime("%j")))
    jour = ng.date()
    vehicules = db.scalars(select(Vehicule).where(
        Vehicule.statut == "ACTIF", Vehicule.conducteur_actuel_id.isnot(None))
        .order_by(Vehicule.plaque)).all()

    plans = ["tana", "tana", "tana_risque", "tana_legal", "mmg", "mmg", "abe",
             "fnr", "mmg_double", "tana", "mmg", "abe", "tana_risque",
             "tana_depart_tard", "mmg_double", "repos", "fnr", "tana", "mmg",
             "abe", "tana_legal", "gps_muet", "mmg", "tana", "abe", "repos",
             "mmg_double", "tana", "tana_attente", "mmg", "tana_risque", "abe",
             "fnr", "tana", "mmg", "repos", "tana_legal", "abe", "mmg_double",
             "tana", "mmg", "abe", "tana_depart_tard", "mmg", "tana", "repos",
             "mmg_double"]

    for i, v in enumerate(vehicules):
        plan = plans[i % len(plans)]
        debut = (datetime.combine(jour, datetime.min.time()).replace(hour=5)
                 + timedelta(minutes=rng.randint(0, 70)))
        coupure = None
        if plan.startswith("tana"):
            etapes = scenario_tana(rng, _prochain_ot(), agressif=(plan == "tana_risque"),
                                   pause_reglementaire=(plan != "tana_risque"))
            if plan == "tana_depart_tard":
                etapes.insert(0, {"op": "stop", "minutes": rng.randint(150, 210),
                                   "moteur": "OFF",
                                   "champs": {"situation": "Départ prévu pour livraison TMT-TANA ce jour"}})
            elif plan == "tana_attente":
                etapes.insert(0, {"op": "stop", "minutes": rng.randint(90, 150),
                                   "moteur": "ON",
                                   "champs": {"situation": "En attente code de validation LP"}})
        elif plan == "mmg":
            etapes = scenario_mmg(rng, _prochain_ot())
        elif plan == "mmg_double":
            etapes = scenario_mmg_double(rng, _prochain_ot())
        elif plan == "abe":
            etapes = scenario_abe(rng, _prochain_ot())
        elif plan == "fnr":
            etapes = scenario_fnr(rng, _prochain_ot())
        elif plan == "gps_muet":
            etapes = scenario_mmg(rng, _prochain_ot())
            coupure = debut + timedelta(hours=rng.randint(3, 5))
        else:  # repos
            etapes = [{"op": "set", "champs": {"situation": "Repos chauffeur",
                                                "statut_camion": "LIBRE"}},
                      {"op": "stop", "minutes": 800, "moteur": "OFF"}]
        CAMIONS[v.id] = CamionSim(v.id, random.Random(rng.random() + i), etapes, debut,
                                  agressif=(plan == "tana_risque"), coupure_gps=coupure)
    log.info("Simulateur initialisé : %d camions instrumentés", len(CAMIONS))
    return len(CAMIONS)


def avancer_tous(horizon: datetime, db, sec: bool = False):
    for vid, sim in CAMIONS.items():
        vehicule = db.get(Vehicule, vid)
        if vehicule is None:
            continue
        try:
            sim.avancer_jusqua(horizon, db, vehicule, sec=sec)
        except Exception:
            db.rollback()
            sim.etape = None  # redémarrage propre de l'étape au prochain passage
            log.exception("Erreur simulateur sur %s", vehicule.plaque)
    if not sec:
        # Addendum v1.4 — à chaque tick, les validations Niveau 2 échues sont
        # appliquées (PROVISOIRE → VALIDÉ), comme la sync périodique réelle.
        try:
            traiter_validations_echues(db, horizon)
        except Exception:
            db.rollback()
            log.exception("Erreur traitement validations Niveau 2")


def rejeu_journee() -> int:
    """Rejoue la journée depuis 05h00 jusqu'à maintenant (peuplement initial :
    trajets, TCC/TCJ/TTJ, missions, infractions, alertes)."""
    from . import engine
    from .engine import prefill_positions_gps
    db = SessionLocal()
    try:
        aujourd = now_local().date()
        minuit = datetime.combine(aujourd, datetime.min.time())
        deja = db.query(EvenementGPS).filter(EvenementGPS.horodatage >= minuit).first()
        if deja:
            log.info("Rejeu ignoré : événements du jour déjà présents")
            initialiser(db)
            # rattrapage « à sec » : les scénarios reprennent au bon point de la
            # journée sans réécrire les événements déjà ingérés
            avancer_tous(now_local(), db, sec=True)
            # validations Niveau 2 : rattraper uniquement ce qui manque
            planifier_validations_manquantes(db, now_local())
            traiter_validations_echues(db, now_local())
            log.info("Scénarios ré-armés (rattrapage sec jusqu'à maintenant)")
            return 0
        engine.PUBLISH_ENABLED["on"] = False
        initialiser(db)
        horizon = now_local()
        log.info("Rejeu de la journée jusqu'à %s…", horizon.strftime("%H:%M"))
        avancer_tous(horizon, db)
        engine.PUBLISH_ENABLED["on"] = True
        nb = prefill_positions_gps(db, aujourd, jusqu_a=horizon)
        # les trajets du rejeu planifient eux-mêmes leur validation via le hook ;
        # ceux déjà échus (clôturés depuis > 6–14 min) sont validés tout de suite
        planifier_validations_manquantes(db, horizon)
        traiter_validations_echues(db, horizon)
        log.info("Rejeu terminé (%d positions Partie C pré-remplies)", nb)
        return 1
    finally:
        db.close()


# =============================================================================
# ADDENDUM v1.4 (démo) — « Onglet Trajets » simulé : validation Niveau 2
# retardée des trajets MZoneX. En production réelle, ce rôle est tenu par le
# connecteur scrapers.py ciblant l'onglet Trajets (FREQUENCE_SYNC_TRAJETS_VALIDES).
# =============================================================================
VALIDATIONS_EN_ATTENTE: list[dict] = []

DELAI_VALIDATION_MIN_S = 6 * 60     # un trajet n'apparaît dans l'onglet Trajets
DELAI_VALIDATION_MAX_S = 14 * 60    # qu'une fois terminé (disponibilité différée)


def planifier_validation_simulee(db, vehicule, suivi, trajet, ts):
    """Hook branché sur engine.HOOKS_TRAJET_CLOTURE (uniquement si SIM_ENABLE).
    À la clôture d'un trajet MZoneX : validation officielle programmée dans
    6 à 14 minutes — comme sur le portail réel où le trajet n'apparaît dans
    l'onglet Trajets qu'après sa clôture effective."""
    from .models import StatutSourceTrajet
    plateforme = vehicule.plateforme_gps or "MZONEX"
    if plateforme == "CAMTRACKPRO":
        return  # CamtrackPro : trajets déjà VALIDÉS dès la création (§5)
    if trajet.statut_source != StatutSourceTrajet.PROVISOIRE:
        return
    if any(v["trajet_id"] == trajet.id for v in VALIDATIONS_EN_ATTENTE):
        return
    rng = random.Random(hash(trajet.id) & 0xFFFF)
    VALIDATIONS_EN_ATTENTE.append({
        "trajet_id": trajet.id, "vehicule_id": vehicule.id,
        "due": ts + timedelta(seconds=rng.randint(DELAI_VALIDATION_MIN_S,
                                                  DELAI_VALIDATION_MAX_S)),
        "source": plateforme,
    })


def planifier_validations_manquantes(db, horizon: datetime):
    """Après un redémarrage, la file mémoire est vide : re-planifier la
    validation de tout trajet MZoneX PROVISOIRE déjà clôturé (la file du
    portail réel, elle, survit à nos redémarrages)."""
    from .models import StatutSourceTrajet, SuiviJournalier, Trajet
    jour = horizon.date()
    lignes = db.scalars(select(SuiviJournalier).where(
        SuiviJournalier.date_jour == jour)).all()
    planifies = {v["trajet_id"] for v in VALIDATIONS_EN_ATTENTE}
    ajouts = 0
    for s in lignes:
        v = db.get(Vehicule, s.vehicule_id)
        if v is None or (v.plateforme_gps or "MZONEX") == "CAMTRACKPRO":
            continue
        for t in s.trajets or []:
            if t.id in planifies or t.heure_fin is None:
                continue
            if t.statut_source != StatutSourceTrajet.PROVISOIRE:
                continue
            rng = random.Random(hash(t.id) & 0xFFFF)
            due = t.heure_fin + timedelta(seconds=rng.randint(
                DELAI_VALIDATION_MIN_S, DELAI_VALIDATION_MAX_S))
            VALIDATIONS_EN_ATTENTE.append({
                "trajet_id": t.id, "vehicule_id": v.id,
                "due": min(due, horizon),   # déjà échu → validation immédiate
                "source": "MZONEX"})
            ajouts += 1
    if ajouts:
        log.info("Validations Niveau 2 re-planifiées : %d", ajouts)


def _distance_parcourue(db, vehicule_id: str, debut, fin) -> float | None:
    """Distance « native MZoneX » simulée : longueur de la trace GPS réelle du
    trajet, avec une légère dérive (±3 %) comme entre deux méthodes de calcul."""
    from .engine import haversine_km
    evs = db.scalars(select(EvenementGPS).where(
        EvenementGPS.vehicule_id == vehicule_id,
        EvenementGPS.horodatage >= debut, EvenementGPS.horodatage <= fin)
        .order_by(EvenementGPS.horodatage)).all()
    if len(evs) < 2:
        return None
    km = sum(haversine_km(evs[i].latitude, evs[i].longitude,
                          evs[i + 1].latitude, evs[i + 1].longitude)
             for i in range(len(evs) - 1))
    return round(km, 2)


def traiter_validations_echues(db, horizon: datetime) -> int:
    """Simule l'interrogation périodique de l'onglet Trajets : chaque trajet
    clôturé depuis 6–14 min y « apparaît » → réconciliation (PROVISOIRE→VALIDÉ).

    Les valeurs officielles diffèrent légèrement de la reconstruction maison
    (±60 s, dans la tolérance de rapprochement) ; 4 % des cas simulent une
    divergence > 10 min pour exercer le garde-fou qualité §4 (audit)."""
    from .models import StatutSourceTrajet, SuiviJournalier, Trajet
    from .reconciliation import reconcilier_trajets_valides
    echanges = [v for v in VALIDATIONS_EN_ATTENTE if v["due"] <= horizon]
    if not echanges:
        return 0
    items = []

    def _retirer(v):
        """Retrait tolérant : la liste est partagée avec la re-planification
        (validation déjà dépilée par l'autre passe → ValueError, journal du
        13/08) ; on ne plante jamais pour ça."""
        try:
            VALIDATIONS_EN_ATTENTE.remove(v)
        except ValueError:
            pass

    for v in echanges:
        t = db.get(Trajet, v["trajet_id"])
        if t is None or t.heure_fin is None \
                or t.statut_source != StatutSourceTrajet.PROVISOIRE:
            _retirer(v)
            continue
        rng = random.Random((hash(f"{t.id}-val") & 0xFFFF) ^ 0x5A5A)
        debut_v = t.heure_debut + timedelta(seconds=rng.randint(-60, 60))
        fin_v = t.heure_fin + timedelta(seconds=rng.randint(-60, 60))
        if rng.random() < 0.04:   # divergence anormale simulée (garde-fou §4)
            fin_v = t.heure_fin + timedelta(seconds=rng.randint(620, 900))
        fin_v = max(fin_v, debut_v + timedelta(seconds=30))
        items.append({"vehicule_id": v["vehicule_id"], "debut": debut_v,
                      "fin": min(fin_v, horizon),
                      "distance_km": _distance_parcourue(db, v["vehicule_id"],
                                                         t.heure_debut, t.heure_fin),
                      "source": v.get("source", "MZONEX")})
        _retirer(v)
    if not items:
        return 0
    # v1.11 — le jugement se fait À L'HORIZON de la passe ( pas au mur de
    # l'horloge ) : un trajet clôturé depuis ≥ 20 min à l'horizon est jugé,
    # même si la passe réelle arrive plus tôt (tests reproductibles à toute
    # heure ; en production horizon = maintenant, comportement identique)
    stats = reconcilier_trajets_valides(db, items, username="simulateur-niveau2",
                                        maintenant=horizon)
    log.info("Onglet Trajets (simulé) : %d validation(s) — %s", len(items), stats)
    return stats["remplaces"] + stats["crees"]
