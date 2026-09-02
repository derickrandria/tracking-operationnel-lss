"""Configuration centrale — Plateforme de Tracking Opérationnel LSS.

Toutes les valeurs sont surchargeables par variables d'environnement afin de
permettre le passage en production (PostgreSQL/Redis/Celery) sans modification
du code (voir README.md).
"""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent            # backend/app
ROOT_DIR = BASE_DIR.parent                            # backend
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Fichier backend/.env optionnel (identifiants MZoneX/CamtrackPro, seuils, …) —
# pratique en production : les variables déjà posées dans l'environnement priment.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env", override=False)
except ImportError:
    pass

# Compatibilité v1.18.2 — le code attend CAMTRACKPRO_URL / CAMTRACKPRO_USER /
# CAMTRACKPRO_PASSWORD (schéma documenté dans backend/.env.exemple). Accepter
# aussi l'ancien nommage CAMTRACK_* : sinon KeyError « CAMTRACKPRO_URL » à la
# connexion et le rapport trajets ne ramène aucun véhicule CamtrackPro.
for _ancien, _nouveau in (("CAMTRACK_URL", "CAMTRACKPRO_URL"),
                          ("CAMTRACK_USER", "CAMTRACKPRO_USER"),
                          ("CAMTRACK_PASSWORD", "CAMTRACKPRO_PASSWORD")):
    if not os.getenv(_nouveau) and os.getenv(_ancien):
        os.environ[_nouveau] = os.environ[_ancien]
del _ancien, _nouveau

# ---------------------------------------------------------------- temporel
TZ_NAME = os.getenv("APP_TZ", "Indian/Antananarivo")
TZ = ZoneInfo(TZ_NAME)


def now_local() -> datetime:
    """Horodatage naïf en heure locale (Indian/Antananarivo)."""
    return datetime.now(TZ).replace(tzinfo=None)


# Référence IA v2 §8.2/§8.3/§9 — bascule de journée à 01h00 (arbitrage
# métier du 06/08/2026, supersède Addendum v1.8 qui disait 02h00) :
# un trajet appartient au jour de son HEURE DE DÉBUT, SAUF un début avant
# 01h00 qui COMPLETE la veille (le camion finit sa nuit ; la journée
# logistique court donc de 01h00 à 01h00). Surcharge : APP_BASCULE_JOUR_H.
from datetime import date as _date, timedelta as _timedelta
HEURE_BASCULE_JOUR = 0
"""v3 (AM-3, arbitrage LSS C1 du 22/08/2026) : la bascule de journée est
abrogée — l'ancienne constante (01h00, §0bis B 06/08) ne pilote plus rien ;
elle reste figée ici à minuit pour compatibilité des anciens exports."""


def jour_attribution(debut: datetime) -> "_date":
    """Jour d'attribution d'un trajet — v3 AM-3 (arbitrage LSS C1 du
    22/08/2026) : **DATE(heure_debut), point final.**

    Les règles « début < 01h00 → HIER » (§0bis B) / « < 02h00 » sont
    ABROGÉES. Un trajet encore en cours à 23:59:59 est découpé à minuit
    (segment A au jour J, segment B `[00:00 → fin]` au jour J+1) — voir
    `daily.consolider_jour` et la découpe d'écriture en réconciliation.

    23h40 → 00h30 : segment A au jour du 23h40, segment B au lendemain."""
    return debut.date()


def bascule_du(dt: datetime) -> datetime:
    """Dernier minuit (début de la journée civile couramment ouverte) —
    v3 AM-3 (C1) : la journée logistique = la journée civile."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------- identifiants véhicules
import re as _re

# Plaque malgache normalisée : « 8076 TCB » · « 7306 TCE-…(LSS) » → « 8076TCB »
IDENT_PLAQUE_RE = _re.compile(r"^\d{3,4}[A-Z]{2,3}$")


def normaliser_ident(texte: str | None) -> str:
    """Normalisation d'un identifiant VENU DU PORTAIL (§10) : préfixe seul
    (hors « (…) » et suffixe « -… »), espaces retirés, MAJUSCULES.
    « 8076 TCB (LSS) » → « 8076TCB ». Ne JAMAIS l'appliquer aux champs
    saisis/en base (voir normaliser_saisie — le « - » y est significatif,
    ex. boîtier « OBC-8076TCB »)."""
    brut = (texte or "").split("(")[0].split("-")[0]
    return "".join(brut.split()).upper()


def normaliser_saisie(texte: str | None) -> str:
    """§0quater D0 (14/08/2026) — normalisation des champs SAISIS À LA MAIN
    ou déjà EN BASE : espaces retirés + MAJUSCULES, POINT FINAL (les tirets
    et parenthèses y sont légitimes : « OBC-8076 TCB » → « OBC-8076TCB »).
    Sans cela, une plaque saisie avec espace ne correspond jamais aux
    relevés (données inexploitées, constat métier 14/08)."""
    return "".join((texte or "").split()).upper()


def plaque_depuis_libelle_portail(libelle: str | None) -> str:
    """§0quinquies D4 (14/08/2026) — extrait la PLAQUE normalisée d'un libellé
    publié par un portail GPS (listes déroulantes véhicules) :
      · MZoneX      « 8076 TCB (LSS) »                → « 8076TCB »
      · CamtrackPro « 0826 TBS-MERCEDES -LPSA(LSS) »  → « 0826TBS »
    Règle : tête du libellé jusqu'à la première parenthèse ou le premier
    tiret, espaces retirés, MAJUSCULES, puis garde IDENT_PLAQUE_RE (3-4
    chiffres + 2-3 lettres). Retourne "" si le libellé n'est pas une plaque
    (« TOTAUX », nom de groupe…) — à tracer côté appelant (D3)."""
    tete = _re.split(r"[(\-]", libelle or "", maxsplit=1)[0]
    candidat = "".join(tete.split()).upper()
    return candidat if IDENT_PLAQUE_RE.match(candidat) else ""


def mots_ignores_conducteur() -> frozenset:
    """§0quinquies D5 (14/08/2026, arbitrage métier) — le portail écrit
    parfois un LIEU dans la colonne « chauffeur » (« Garage LSS 2 »…) : ces
    mots ne créent JAMAIS de fiche chauffeur. Surcharge possible :
    CONDUCTEUR_MOTS_IGNORES="mot1,mot2" (dans backend/.env)."""
    brut = os.getenv("CONDUCTEUR_MOTS_IGNORES",
                     "garage,dépôt,depot,station,parking,atelier")
    return frozenset(m.strip().lower() for m in brut.split(",") if m.strip())


def normaliser_libelle(texte: str | None) -> str:
    """§0septies B2 — comparaison de libellés INSENSIBLE casse ET accents
    (« GARAGE LSS » = « garage lss » = « Garage LSS »). §0sexies decies J1
    (27/08/2026, extension déclarée) : les espaces INTERNES multiples sont
    aussi resserrés (« MICHAËL  JUSTIN » = « MICHAËL JUSTIN ») — dérive
    constatée sur données réelles v1.38 ; chaque `\u00a0` compte comme un
    espace."""
    import unicodedata
    if not texte:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(texte))
    brut = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(brut.lower().split())


def mots_ignores_badge() -> frozenset:
    """§0septies B2 (arbitrage LSS 20/08/2026) — EXCEPTION à « le badge fait
    foi » : les clés de SERVICE ne désignent pas un chauffeur (« Nouveau
    conducteur », « garage LSS » — chauffeurs sans badge / clé atelier). Ces
    badges sont ÉCARTÉS (tracés, journal §10) et la saisie manuelle fait le
    travail. Comparaison sur le libellé normalisé (casse/accents ignorés).
    Surcharge : CONDUCTEUR_BADGE_IGNORES="a,b" (dans backend/.env)."""
    brut = os.getenv("CONDUCTEUR_BADGE_IGNORES",
                     "nouveau conducteur,garage lss")
    return frozenset(normaliser_libelle(m) for m in brut.split(",") if m.strip())


# §0quinquies (14/08/2026) — un « Début du trajet » connu mais resté sans
# ligne n'est ré-ingéré (auto-réparation v1.17) que s'il est RÉCENT : au-delà
# de ce délai sans « Fin » ni mouvement, le boîtier est simplement muet
# (contact coupé, camion garé) et rouvrir une ligne fantôme contredit la
# fraîcheur de l'arbitrage R2 (15 min). R2 reprend la main dès le premier
# roulage réel. Surcharge : REPARATION_AGE_MAX_S.
AGE_MAX_REPARATION_S = int(os.getenv("REPARATION_AGE_MAX_S", "2700"))  # 45 min


# ---------------------------------------------------------------- données
# SQLite par défaut (démo autonome) ; passer DATABASE_URL=postgresql+psycopg2://...
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'lss.db'}")

# ---------------------------------------------------------------- sécurité
JWT_SECRET = os.getenv("JWT_SECRET", "lss-dev-secret-a-changer-en-prod")
JWT_ALG = "HS256"
TOKEN_TTL_MIN = int(os.getenv("TOKEN_TTL_MIN", "10080"))  # 7 jours (choix métier 05/08)

# ---------------------------------------------------------------- simulateur
# Le simulateur remplace le scraping MZoneX/CamtrackPro tant que les identifiants
# ne sont pas fournis. SIM_ENABLE=0 pour le désactiver (mode production réelle).
SIM_ENABLE = os.getenv("SIM_ENABLE", "1") == "1"
SIM_TICK_S = int(os.getenv("SIM_TICK_S", "20"))       # cadence de remontée live (s)

# ---------------------------------------------------------------- frontend
FRONTEND_DIST = os.getenv("FRONTEND_DIST", str(ROOT_DIR.parent / "frontend" / "dist"))

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]

# ---------------------------------------------------------------- géographie
# Points logistiques (coordonnées approximatives, Madagascar).
GEO = {
    "BASE_TANA": (-18.8792, 47.5079, "Base LSS — Antananarivo"),
    "DEPOT_DABI": (-18.8100, 47.4450, "Dépôt DABI — Ambohibao, Tana"),
    "RAFF_TMT": (-18.1492, 49.4023, "Raffinerie TMT — Toamasina"),
    "DMMG": (-18.9489, 48.2257, "Dépôt DMMG — Moramanga"),
    "DABE": (-19.8659, 47.0333, "Dépôt DABE — Antsirabe"),
    "DFIA": (-21.4536, 47.0857, "Dépôt DFIA — Fianarantsoa"),
    "DSNR": (-12.2787, 49.2917, "Dépôt DSNR — Antsiranana"),
    "DMKR": (-22.1486, 48.0106, "Dépôt DMKR — Manakara"),
    "DMDV": (-15.7167, 46.3167, "Dépôt DMDV — Mahajanga"),
}

# Corridors logistiques (polylignes simplifiées) utilisés pour l'alerte
# « camion hors itinéraire ».
CORRIDORS = {
    "RN2 (Tana–Moramanga–Toamasina)": [
        (-18.8792, 47.5079), (-18.8500, 47.6200), (-18.9489, 48.2257),
        (-18.8741, 48.4521), (-18.7270, 48.7240), (-18.4445, 49.0880),
        (-18.1820, 49.2680), (-18.1492, 49.4023),
    ],
    "RN7 (Tana–Antsirabe–Fianarantsoa)": [
        (-18.8792, 47.5079), (-19.3000, 47.3000), (-19.5260, 47.2430),
        (-19.8659, 47.0333), (-20.3000, 47.0500), (-20.7500, 47.1800),
        (-21.2000, 47.1500), (-21.4536, 47.0857),
    ],
    "Tana–Ambohibao": [(-18.8792, 47.5079), (-18.8100, 47.4450)],
}

# Routes simplifiées (avec libellés de localité) — utilisées par le simulateur.
ROUTES = {
    "TANA_MMG": [
        ("Base LSS — Antananarivo", -18.8792, 47.5079),
        ("Ambohimangakely", -18.8650, 47.5900),
        ("Manjakandriana", -18.9167, 47.8000),
        ("Ambanidia", -18.9350, 48.0100),
        ("Moramanga", -18.9489, 48.2257),
    ],
    "MMG_TMT": [
        ("Moramanga", -18.9489, 48.2257),
        ("Andasibe", -18.8741, 48.4521),
        ("Beforona", -18.7270, 48.7240),
        ("Brickaville", -18.4445, 49.0880),
        ("Ranomainty", -18.1820, 49.2680),
        ("Toamasina (TMT)", -18.1492, 49.4023),
    ],
    "TANA_ABE": [
        ("Antananarivo", -18.8792, 47.5079),
        ("Ambatolampy", -19.3833, 47.4333),
        ("Antsirabe", -19.8659, 47.0333),
    ],
    "ABE_FNR": [
        ("Antsirabe", -19.8659, 47.0333),
        ("Ambositra", -20.5303, 47.2434),
        ("Fianarantsoa", -21.4536, 47.0857),
    ],
    "TANA_DABI": [
        ("Base LSS — Antananarivo", -18.8792, 47.5079),
        ("Ambohibao (DABI)", -18.8100, 47.4450),
    ],
}

DEPOTS = ["DABI", "DMMG", "DSNR", "DFIA", "DABE", "DMKR", "DMDV"]
DISTRIBUTEURS = ["GALANA", "VIVO", "JOVENA", "TOTAL"]
PRODUITS = ["SP95", "GO", "PL"]
