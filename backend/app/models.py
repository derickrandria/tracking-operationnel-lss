"""Modèle de données — Plateforme de Tracking Opérationnel LSS (§5).

Une table PostgreSQL/SQLite par entité ; les clés étrangères assurent la
synchronisation native entre modules. Les durées réglementaires (TCC/TCJ/TTJ,
pauses) sont stockées en secondes (équivalent `interval`, portable).
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Enum as SAEnum, Float,
                        ForeignKey, Index, Integer, String, Text, Time,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import now_local
from .database import Base


def uid() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------- énumérations
class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    TRACKING = "TRACKING"
    CONSULTATION = "CONSULTATION"


class StatutVehicule(str, enum.Enum):
    ACTIF = "ACTIF"
    INACTIF = "INACTIF"
    MAINTENANCE = "MAINTENANCE"


class StatutConducteur(str, enum.Enum):
    ACTIF = "ACTIF"
    SUSPENDU = "SUSPENDU"
    CONGE = "CONGÉ"
    INACTIF = "INACTIF"


class StatutCamion(str, enum.Enum):
    LIBRE = "LIBRE"
    VIDE = "VIDE"
    CHARGE = "CHARGÉ"


class StatutMission(str, enum.Enum):
    EN_COURS = "EN_COURS"
    TERMINEE = "TERMINÉE"
    DEVIEE = "DÉVIÉE"
    RETARDEE = "RETARDÉE"


class GraviteInfraction(str, enum.Enum):
    CRITIQUE = "CRITIQUE"
    MOYENNE = "MOYENNE"
    FAIBLE = "FAIBLE"


class GraviteAlerte(str, enum.Enum):
    CRITIQUE = "CRITIQUE"
    MOYENNE = "MOYENNE"
    INFORMATION = "INFORMATION"


class StatutAlerte(str, enum.Enum):
    NOUVELLE = "NOUVELLE"
    VUE = "VUE"
    TRAITEE = "TRAITEE"


class TypeInfraction(str, enum.Enum):
    EXCES_VITESSE = "EXCES_VITESSE"
    ACCELERATION_BRUSQUE = "ACCELERATION_BRUSQUE"
    FREINAGE_BRUSQUE = "FREINAGE_BRUSQUE"
    DEPASSEMENT_TCC = "DEPASSEMENT_TCC"
    DEPASSEMENT_TCJ = "DEPASSEMENT_TCJ"
    DEPASSEMENT_TTJ = "DEPASSEMENT_TTJ"


class TypeAlerte(str, enum.Enum):
    TCC_DEPASSE = "TCC_DEPASSE"
    PAUSE_NON_PRISE = "PAUSE_NON_PRISE"
    CAMION_IMMOBILE = "CAMION_IMMOBILE"
    GPS_HORS_LIGNE = "GPS_HORS_LIGNE"
    MISSION_RETARDEE = "MISSION_RETARDEE"
    EXCES_VITESSE = "EXCES_VITESSE"
    HORS_ITINERAIRE = "HORS_ITINERAIRE"
    CARBURANT_SUSPECT = "CARBURANT_SUSPECT"
    NB_TRAJETS_EXCEPTIONNEL = "NB_TRAJETS_EXCEPTIONNEL"  # Addendum v1.1 §1.3 (> 10 trajets/jour)
    NOUVEAU_VEHICULE = "NOUVEAU_VEHICULE"      # §0quater D1 (14/08/2026 — découverte auto)
    NOUVEAU_CONDUCTEUR = "NOUVEAU_CONDUCTEUR"  # §0quater D2 (14/08/2026 — découverte auto)
    # §0septies B4/B5 (arbitrage LSS 20/08/2026 — conduite en direct)
    VITESSE_LIVE = "VITESSE_LIVE"              # > 45 km/h confirmé hors géozone
    SANS_BADGE = "SANS_BADGE"                  # camion qui roule sans clé chauffeur
    # §0decies D3 (arbitrage LSS 24/08/2026 — réparation v1.30 par relecture
    # portails des journées abîmées par l'ancienne bascule 01h00)
    REPARATION_DONNEES = "REPARATION_DONNEES"  # journée contrôlée/réparée (récap)
    # §0septies decies K1 (arbitrage LSS 27/08/2026 — observabilité Ym@ne)
    COLLECTE_YMANE = "COLLECTE_YMANE"        # collecte infractions en échec — auto-refermée à la guérison
    # Module Temps de conduite (TCH) — seuil 46h (avertissement) et 56h (limite)
    TCH_PROCHE_LIMITE = "TCH_PROCHE_LIMITE"      # TCH cumulé ≥ 46h00 (avertissement)
    TCH_LIMITE_ATTEINTE = "TCH_LIMITE_ATTEINTE"  # TCH cumulé ≥ 56h00 (limite réglementaire)
    # Détection de conflits et doublons d'affectation chauffeur
    CONFLIT_AFFECTATION = "CONFLIT_AFFECTATION"  # Chauffeur attribué manuellement vs détecté sur un autre camion
    DOUBLON_CONDUCTEUR = "DOUBLON_CONDUCTEUR"    # Doublon chauffeur sur la même journée
    # Correctif v1.46 (constat du 04/09/2026) — la collecte portails peut traîner
    # ou se bloquer SILENCIEUSEMENT (base arrêtée à 12h19, portails sains) :
    # alerte dès que le dernier événement ingéré dépasse le seuil de retard.
    COLLECTE_RETARD = "COLLECTE_RETARD"


class TypeEvenement(str, enum.Enum):
    DEBUT_MOUVEMENT = "DEBUT_MOUVEMENT"
    ARRET = "ARRET"
    PAUSE = "PAUSE"
    REPRISE = "REPRISE"
    EXCES_VITESSE = "EXCES_VITESSE"
    FREINAGE_BRUSQUE = "FREINAGE_BRUSQUE"
    ACCELERATION_BRUSQUE = "ACCELERATION_BRUSQUE"
    POSITION = "POSITION"


class SourceEvenement(str, enum.Enum):
    CAMTRACKPRO = "CAMTRACKPRO"
    MZONEX = "MZONEX"
    CALCUL_INTERNE = "CALCUL_INTERNE"   # infractions issues du moteur de calcul (§6.4)
    SIMULATEUR = "SIMULATEUR"


class StatutSourceTrajet(str, enum.Enum):
    """Addendum v1.4 §3.1 — fiabilité d'un trajet (stratégie hybride MZoneX) :
    PROVISOIRE : reconstruit en temps réel depuis l'onglet Événements ;
    VALIDÉ     : confirmé par l'onglet Trajets (calcul natif MZoneX, fiable)."""
    PROVISOIRE = "PROVISOIRE"
    VALIDE = "VALIDÉ"


class StatutValidationTrajet(str, enum.Enum):
    """Addendum v1.5 §7.1 — validité MÉTIER d'un trajet (règle absolue §2.1 :
    un trajet REJETÉ n'entre JAMAIS dans TCC/TCJ/TTJ — distance < 0,3 km =
    manœuvre locale, horodatages inutilisés) :
    EN_ATTENTE : en cours de construction ou non encore tranché ;
    VALIDE     : distance ≥ SEUIL_DISTANCE_MIN_TRAJET_KM (0,3 km) ;
    REJETE     : distance < seuil (ou durée en mouvement insuffisante
                 CamtrackPro) — conservé en base UNIQUEMENT pour l'audit,
                 exclu de tout calcul et de tout affichage de la grille."""
    EN_ATTENTE = "EN_ATTENTE"
    VALIDE = "VALIDE"
    REJETE = "REJETE"


SA_ENUM_KW = dict(native_enum=False, validate_strings=True, length=40)


# ------------------------------------------------------------- utilisateurs
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    nom_complet: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(SAEnum(Role, **SA_ENUM_KW))
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    date_creation: Mapped[datetime] = mapped_column(DateTime, default=now_local)


# ------------------------------------------------------------- référentiels
class Conducteur(Base):
    __tablename__ = "conducteurs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    nom_prenom: Mapped[str] = mapped_column(String(160), index=True)
    # §0sexies decies J1 (27/08/2026) — forme canonique de déduplication
    # (NFKD, accents écartés, minuscules : « MICHAËL Justin » = « MICHAEL
    # JUSTIN »). L'anti-doublon se fait ICI — JAMAIS plus via lower() SQL,
    # qui sous SQLite ne plie que A-Z (bug v1.37 et avant : fiches créées en
    # rafale pour tout nom accentué). L'index UNIQUE est posé par la
    # réparation v1.38 une fois les doublons historiques résorbés.
    nom_normalise: Mapped[str | None] = mapped_column(String(170), index=True,
                                                      nullable=True)
    tokens_set: Mapped[str | None] = mapped_column(String(170), index=True,
                                                   nullable=True)
    # Code badge MZoneX (driverKeyCode) — renseigné pour les camions MZoneX, vide pour CamtrackPro
    code_badge_mzonex: Mapped[int | None] = mapped_column(Integer, index=True,
                                                          nullable=True)
    prenom_usuel: Mapped[str] = mapped_column(String(60), index=True)
    # Matricule : renseigné ou driverKeyCode pour MZoneX, vide/null pour CamtrackPro
    matricule: Mapped[str | None] = mapped_column(String(40), index=True,
                                                  nullable=True)
    telephone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    statut: Mapped[StatutConducteur] = mapped_column(
        SAEnum(StatutConducteur, **SA_ENUM_KW), default=StatutConducteur.ACTIF)
    date_creation: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    aliases = relationship("ConducteurAlias", back_populates="conducteur",
                           cascade="all, delete-orphan", lazy="selectin")


class ConducteurAlias(Base):
    """Alias textuels et variantes orthographiques pour le rapprochement des chauffeurs."""
    __tablename__ = "conducteur_aliases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conducteur_id: Mapped[str] = mapped_column(
        ForeignKey("conducteurs.id", ondelete="CASCADE"), index=True)
    alias_brut: Mapped[str] = mapped_column(String(160))
    alias_normalise: Mapped[str] = mapped_column(String(170), unique=True, index=True)
    source: Mapped[str | None] = mapped_column(String(30), default="MANUEL")
    date_creation: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    conducteur = relationship("Conducteur", back_populates="aliases")


class Vehicule(Base):
    __tablename__ = "vehicules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    plaque: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(60), nullable=True)
    marque: Mapped[str | None] = mapped_column(String(40), nullable=True)
    capacite: Mapped[float | None] = mapped_column(Float, nullable=True)  # litres
    statut: Mapped[StatutVehicule] = mapped_column(
        SAEnum(StatutVehicule, **SA_ENUM_KW), default=StatutVehicule.ACTIF)
    gps_associe: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Addendum v1.2 §7.1 — portail GPS d'appartenance du véhicule (flotte mixte
    # MZoneX / CamtrackPro, confirmé par les captures réelles des deux portails)
    plateforme_gps: Mapped[str] = mapped_column(String(20), default="MZONEX")
    conducteur_actuel_id: Mapped[str | None] = mapped_column(
        ForeignKey("conducteurs.id"), nullable=True)
    date_creation: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    # dernière position connue (alimentée par le collecteur / §10)
    last_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_vitesse: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_adresse: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    moteur_on: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    conducteur_actuel = relationship("Conducteur", lazy="joined")


class SituationCamion(Base):
    """Référentiel éditable (Annexe C) — aucune valeur figée dans le code."""
    __tablename__ = "situations_camion"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    libelle: Mapped[str] = mapped_column(String(200), unique=True)
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    ordre: Mapped[int] = mapped_column(Integer, default=0)


# ------------------------------------------------------------- suivi (§5.3)
class SuiviJournalier(Base):
    """Une ligne = un camion pour un jour donné.

    Partie A : véhicule/conducteur (persistant, synchronisé référentiels)
    Partie B : situation/statut/dépôt/... (persistant, reporté chaque jour)
    Partie C : emplacements J-1 et positions horaires (reset quotidien)
    Partie D : calculs automatiques (trajets, TCC/TCJ/TTJ — reset quotidien)
    """
    __tablename__ = "suivi_journalier"
    __table_args__ = (UniqueConstraint("date_jour", "vehicule_id", name="uq_suivi_jour_camion"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    date_jour: Mapped[date] = mapped_column(Date, index=True)

    # Partie A
    vehicule_id: Mapped[str] = mapped_column(ForeignKey("vehicules.id"), index=True)
    conducteur_id: Mapped[str | None] = mapped_column(ForeignKey("conducteurs.id"), index=True, nullable=True)
    # §0septies B2 (20/08/2026) — origine de l'attribution chauffeur du jour :
    # None / « BADGE » (portail, fait foi) / « MANUEL » (saisie — jamais
    # écrasée par un badge, même valide)
    conducteur_origine: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Partie B
    situation: Mapped[str | None] = mapped_column(String(200), nullable=True)
    statut_camion: Mapped[StatutCamion | None] = mapped_column(
        SAEnum(StatutCamion, **SA_ENUM_KW), nullable=True)
    depot_recepteur: Mapped[str | None] = mapped_column(String(10), nullable=True)
    distributeur: Mapped[str | None] = mapped_column(String(20), nullable=True)
    produit: Mapped[str | None] = mapped_column(String(10), nullable=True)
    numero_ot: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Partie C
    emplacement_j_moins_1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    position_08h: Mapped[str | None] = mapped_column(String(200), nullable=True)
    position_10h: Mapped[str | None] = mapped_column(String(200), nullable=True)
    position_12h: Mapped[str | None] = mapped_column(String(200), nullable=True)
    position_14h: Mapped[str | None] = mapped_column(String(200), nullable=True)
    position_16h: Mapped[str | None] = mapped_column(String(200), nullable=True)
    position_18h: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # §0vicies decies N2 (31/08/2026) — relevés automatiques du soir
    position_20h: Mapped[str | None] = mapped_column(String(200), nullable=True)
    position_22h: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Partie D
    heure_depart: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    arret_final: Mapped[str | None] = mapped_column(String(220), nullable=True)
    tcc_s: Mapped[int] = mapped_column(Integer, default=0)
    tcj_s: Mapped[int] = mapped_column(Integer, default=0)
    ttj_s: Mapped[int] = mapped_column(Integer, default=0)
    total_pause_s: Mapped[int] = mapped_column(Integer, default=0)
    km_parcourus: Mapped[float] = mapped_column(Float, default=0)

    mission_id: Mapped[str | None] = mapped_column(ForeignKey("missions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local)

    vehicule = relationship("Vehicule", lazy="joined")
    conducteur = relationship("Conducteur", lazy="joined")
    trajets: Mapped[list["Trajet"]] = relationship(
        "Trajet", back_populates="suivi", cascade="all, delete-orphan",
        order_by="Trajet.numero", lazy="selectin")


class Trajet(Base):
    """Table enfant normalisée (1-N) — Trajet 1…10 affichés (plafond §5.3.1 porté
    à 10 par l'Addendum v1.1 ; les trajets 11+ restent stockés, sans hard-limit).

    Addendum v1.4 §3.1 : chaque trajet porte un statut de fiabilité
    (PROVISOIRE → VALIDÉ, stratégie hybride MZoneX Événements + Trajets),
    la plateforme d'origine et, quand l'onglet Trajets la fournit, la distance
    officielle calculée nativement par MZoneX."""
    __tablename__ = "trajets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    suivi_id: Mapped[str] = mapped_column(ForeignKey("suivi_journalier.id"), index=True)
    numero: Mapped[int] = mapped_column(Integer)
    heure_debut: Mapped[datetime] = mapped_column(DateTime)
    heure_fin: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pause_apres_s: Mapped[int] = mapped_column(Integer, default=0)

    # --- Addendum v1.4 (stratégie hybride temps réel + validation a posteriori)
    statut_source: Mapped[StatutSourceTrajet] = mapped_column(
        SAEnum(StatutSourceTrajet, **SA_ENUM_KW), default=StatutSourceTrajet.VALIDE)
    source_plateforme: Mapped[str | None] = mapped_column(String(20), nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)  # officielle MZoneX

    # --- Addendum v1.5 §7.1 — validité métier (règle absolue §2 : un trajet
    # REJETÉ — distance < 0,3 km — n'entre jamais dans TCC/TCJ/TTJ)
    statut_validation: Mapped[StatutValidationTrajet] = mapped_column(
        SAEnum(StatutValidationTrajet, **SA_ENUM_KW),
        default=StatutValidationTrajet.EN_ATTENTE)

    # --- §0septies (arbitrages LSS 20/08/2026) — badge chauffeur + carnet de
    # conduite, publiés par les API des portails (Trips MZoneX / rapport id 9
    # CamtrackPro). None = non fourni (≠ 0 infraction : on n'invente pas §10).
    conducteur_badge: Mapped[str | None] = mapped_column(String(160), nullable=True)
    conducteur_badge_id: Mapped[str | None] = mapped_column(
        ForeignKey("conducteurs.id"), index=True, nullable=True)
    badge_ecarte: Mapped[str | None] = mapped_column(String(160), nullable=True)
    v_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    ralenti_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exc_vitesse: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exc_freinage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exc_accel: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exc_ralenti: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exc_surregime: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exc_autres: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # v3 AM-3 (22/08/2026) + §0decies (24/08/2026) : segment B [00:00 → fin]
    # d'un trajet qui franchit minuit — marqué pour ne JAMAIS être confondu
    # avec une ligne sans source lors d'une revérification portail (le portail
    # ne republie un trajet qu'au jour de son DÉBUT ; le segment B naît du
    # découpage d'écriture, pas d'une nouvelle publication).
    suite_minuit: Mapped[bool] = mapped_column(Boolean, default=False)

    conducteur_badge_ref = relationship("Conducteur", lazy="joined")

    suivi = relationship("SuiviJournalier", back_populates="trajets")


# ------------------------------------------------------------- missions (§5.4)
class Mission(Base):
    __tablename__ = "missions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    code_mission: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    date_jour: Mapped[date] = mapped_column(Date, index=True)
    conducteur_id: Mapped[str | None] = mapped_column(ForeignKey("conducteurs.id"), index=True, nullable=True)
    vehicule_id: Mapped[str] = mapped_column(ForeignKey("vehicules.id"), index=True)
    numero_mission_du_jour: Mapped[int] = mapped_column(Integer, default=1)
    statut: Mapped[StatutMission] = mapped_column(
        SAEnum(StatutMission, **SA_ENUM_KW), default=StatutMission.EN_COURS)
    statut_camion_actuel: Mapped[str] = mapped_column(String(20), default="VIDE")
    heure_debut: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heure_chargement: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heure_fin: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duree_s: Mapped[int] = mapped_column(Integer, default=0)
    numero_ot: Mapped[str | None] = mapped_column(String(40), nullable=True)
    produit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    depot: Mapped[str | None] = mapped_column(String(30), nullable=True)
    depot_prevu: Mapped[str | None] = mapped_column(String(30), nullable=True)
    depot_effectif: Mapped[str | None] = mapped_column(String(30), nullable=True)
    est_deviee: Mapped[bool] = mapped_column(Boolean, default=False)
    motif_deviation: Mapped[str | None] = mapped_column(String(200), nullable=True)
    distributeur: Mapped[str | None] = mapped_column(String(30), nullable=True)
    km_vide: Mapped[float] = mapped_column(Float, default=0.0)
    km_charge: Mapped[float] = mapped_column(Float, default=0.0)
    kilometrage: Mapped[float] = mapped_column(Float, default=0.0)
    kilometrage_total: Mapped[float] = mapped_column(Float, default=0.0)
    origine: Mapped[str | None] = mapped_column(String(200), nullable=True)
    etapes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local)

    vehicule = relationship("Vehicule", lazy="joined")
    conducteur = relationship("Conducteur", lazy="joined")
    infractions = relationship("Infraction", back_populates="mission", lazy="selectin")


# ------------------------------------------------------------- infractions/alertes
class Infraction(Base):
    __tablename__ = "infractions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    date_jour: Mapped[date] = mapped_column(Date, index=True)
    heure: Mapped[datetime] = mapped_column(Time)
    conducteur_id: Mapped[str | None] = mapped_column(ForeignKey("conducteurs.id"), nullable=True)
    vehicule_id: Mapped[str] = mapped_column(ForeignKey("vehicules.id"), index=True)
    type: Mapped[TypeInfraction] = mapped_column(SAEnum(TypeInfraction, **SA_ENUM_KW), index=True)
    gravite: Mapped[GraviteInfraction] = mapped_column(SAEnum(GraviteInfraction, **SA_ENUM_KW))
    duree_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valeur_mesuree: Mapped[float | None] = mapped_column(Float, nullable=True)
    seuil_reference: Mapped[float | None] = mapped_column(Float, nullable=True)
    mission_id: Mapped[str | None] = mapped_column(ForeignKey("missions.id"), nullable=True)
    source: Mapped[SourceEvenement] = mapped_column(
        SAEnum(SourceEvenement, **SA_ENUM_KW), default=SourceEvenement.SIMULATEUR)
    # v3 AM-5 / C3 (arbitrage LSS 22/08/2026) : l'onglet Infractions n'affiche
    # QUE les infractions pré-filtrées par une AUTRE plateforme (import
    # externe) ; les lignes historiques créées localement (exterieure=False)
    # restent en base pour l'audit mais ne s'affichent plus dans l'onglet.
    exterieure: Mapped[bool] = mapped_column(Boolean, default=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    adresse: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # --- §0quinquies decies I1→I4 (arbitrages LSS 25/08/2026) — source Ym@ne
    # + workflow de validation. Jamais d'écriture locale : ces lignes naissent
    # uniquement du collecteur Ym@ne (exterieure=True).
    niveau: Mapped[str | None] = mapped_column(String(20), nullable=True)   # ALERTE | ALARME
    nom_ymane: Mapped[str | None] = mapped_column(String(160), nullable=True)  # nom publié
    chauffeur_brut: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ymane_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    seuil_unite: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "kmh" | "s" | "brut"
    # --- §0octies decies L2 (arbitrage LSS 27/08/2026) — période de
    # l'infraction : enddatetime verbatim Ym@ne (rattrapage automatique des
    # lignes déjà en base par l'upsert I5, fenêtre J-8→J).
    date_fin: Mapped[date | None] = mapped_column(Date, nullable=True)
    heure_fin: Mapped[datetime | None] = mapped_column(Time, nullable=True)
    # Exécution I5 (26/08/2026) : seuil VERBATIM de Ym@ne quand il n'est pas
    # un nombre convertible — plage horaire (« 18:00:00 to 05:30:00 » pour la
    # conduite de nuit) ou indice brut (accélération/freinage). Jamais d'unité
    # inventée : on affiche ce que le portail dit.
    seuil_texte: Mapped[str | None] = mapped_column(String(80), nullable=True)
    validation: Mapped[str] = mapped_column(String(12), default="NON_TRAITEE")
    observation: Mapped[str | None] = mapped_column(String(500), nullable=True)
    validee_par: Mapped[str | None] = mapped_column(String(80), nullable=True)
    validee_le: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    vehicule = relationship("Vehicule", lazy="joined")
    conducteur = relationship("Conducteur", lazy="joined")
    mission = relationship("Mission", back_populates="infractions", foreign_keys=[mission_id], lazy="joined")


class Alerte(Base):
    __tablename__ = "alertes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    date_heure: Mapped[datetime] = mapped_column(DateTime, index=True, default=now_local)
    type: Mapped[TypeAlerte] = mapped_column(SAEnum(TypeAlerte, **SA_ENUM_KW), index=True)
    gravite: Mapped[GraviteAlerte] = mapped_column(SAEnum(GraviteAlerte, **SA_ENUM_KW))
    vehicule_id: Mapped[str | None] = mapped_column(ForeignKey("vehicules.id"), index=True, nullable=True)
    conducteur_id: Mapped[str | None] = mapped_column(ForeignKey("conducteurs.id"), index=True, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    statut: Mapped[StatutAlerte] = mapped_column(
        SAEnum(StatutAlerte, **SA_ENUM_KW), default=StatutAlerte.NOUVELLE)
    lien_module: Mapped[str | None] = mapped_column(String(200), nullable=True)
    infraction_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    vehicule = relationship("Vehicule", lazy="joined")
    conducteur = relationship("Conducteur", lazy="joined")


# ------------------------------------------------------------- historique (§5.7)
class HistoriqueJournalier(Base):
    """Archive en lecture seule d'un SuiviJournalier clôturé (job de minuit §8)."""
    __tablename__ = "historique_journalier"
    __table_args__ = (UniqueConstraint("date_jour", "vehicule_id", name="uq_hist_jour_camion"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    date_jour: Mapped[date] = mapped_column(Date, index=True)
    annee: Mapped[int] = mapped_column(Integer, index=True)
    mois: Mapped[int] = mapped_column(Integer, index=True)
    vehicule_id: Mapped[str] = mapped_column(ForeignKey("vehicules.id"), index=True)
    conducteur_id: Mapped[str | None] = mapped_column(ForeignKey("conducteurs.id"), index=True, nullable=True)
    donnees: Mapped[dict] = mapped_column(JSON)  # snapshot complet (suivi + trajets)
    nb_infractions: Mapped[int] = mapped_column(Integer, default=0)
    nb_alertes: Mapped[int] = mapped_column(Integer, default=0)
    archive_le: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    vehicule = relationship("Vehicule", lazy="joined")
    conducteur = relationship("Conducteur", lazy="joined")


# ------------------------------------------------------------- paramétrage (§5.8)
class ParametrageSeuil(Base):
    """Seuils éditables par l'Administrateur sans toucher au code (§7.4)."""
    __tablename__ = "parametrage_seuils"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    cle: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    valeur: Mapped[float] = mapped_column(Float)  # secondes pour les durées
    type_valeur: Mapped[str] = mapped_column(String(20), default="DUREE_S")
    description: Mapped[str | None] = mapped_column(String(240), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local)


# ------------------------------------------------------------- événements GPS (§5.9)
class EvenementGPS(Base):
    """Données brutes issues du scraping/simulateur — table technique (§10)."""
    __tablename__ = "evenements_gps"
    __table_args__ = (Index("ix_evenement_vehicule_ts", "vehicule_id", "horodatage"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    vehicule_id: Mapped[str] = mapped_column(ForeignKey("vehicules.id"))
    horodatage: Mapped[datetime] = mapped_column(DateTime, index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    adresse: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vitesse: Mapped[float] = mapped_column(Float, default=0)
    etat_moteur: Mapped[str] = mapped_column(String(5), default="ON")  # ON / OFF
    type_evenement: Mapped[TypeEvenement] = mapped_column(
        SAEnum(TypeEvenement, **SA_ENUM_KW), default=TypeEvenement.POSITION)
    source: Mapped[SourceEvenement] = mapped_column(
        SAEnum(SourceEvenement, **SA_ENUM_KW), default=SourceEvenement.SIMULATEUR)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


# ------------------------------------------------------------- audit (§11)
class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    date_heure: Mapped[datetime] = mapped_column(DateTime, index=True, default=now_local)
    username: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(80))
    entite: Mapped[str] = mapped_column(String(40))
    entite_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
