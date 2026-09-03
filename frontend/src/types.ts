/** Types partagés avec l'API. */
export interface ConducteurAlias {
  id: string;
  alias_brut: string;
}

export interface Conducteur {
  id: string;
  nom_prenom: string;
  prenom_usuel: string;
  matricule?: string | null;
  code_badge_mzonex?: number | null;
  nom_normalise?: string | null;
  tokens_set?: string | null;
  telephone: string | null;
  statut: string;
  statut_operationnel?: "En mission (Vide)" | "En mission (Chargé)" | "Disponible" | "En repos" | string;
  vehicule_plaque?: string | null;
  date_creation?: string;
  aliases?: ConducteurAlias[];
  mission_active?: {
    id: string;
    code_mission: string;
    numero_ot: string | null;
    produit: string | null;
    depot_prevu: string | null;
    statut_camion: string;
  } | null;
}

export interface Vehicule {
  id: string;
  plaque: string;
  description: string | null;
  marque: string | null;
  capacite: number | null;
  statut: string;
  statut_operationnel?: "LIBRE" | "VIDE" | "CHARGÉ" | "En maintenance" | string;
  situation?: string | null;
  statut_camion?: string | null;
  gps_associe: string | null;
  plateforme_gps?: string | null;
  conducteur_actuel_id: string | null;
  conducteur: Conducteur | null;
  date_creation?: string;
  mission_active?: {
    id: string;
    code_mission: string;
    numero_ot: string | null;
    produit: string | null;
    depot_prevu: string | null;
    statut_camion: string;
  } | null;
  position?: {
    lat: number | null;
    lng: number | null;
    vitesse: number | null;
    adresse: string | null;
    maj: string | null;
    moteur: boolean | null;
  };
}

export interface Trajet {
  id: string;
  numero: number;
  heure_debut: string;
  heure_fin: string | null;
  pause_apres_s: number;
  /** Addendum v1.4 §3.1 — PROVISOIRE (temps réel, onglet Événements) ou
   *  VALIDÉ (confirmé par l'onglet Trajets MZoneX / rapport CamtrackPro). */
  statut_source?: "PROVISOIRE" | "VALIDÉ" | null;
  source_plateforme?: string | null;
  distance_km?: number | null;
  /** Addendum v1.5 §7.1 — validité métier (REJETE un trajet < 0,3 km ;
   * les REJETÉS sont filtrés côté serveur et n'arrivent pas jusqu'ici). */
  statut_validation?: "EN_ATTENTE" | "VALIDE" | "REJETE" | null;
  conducteur_badge?: string | null;
  conducteur_badge_id?: string | null;
}

export interface SuiviLigne {
  id: string;
  date_jour: string;
  vehicule_id: string;
  plaque: string;
  description: string | null;
  conducteur_id: string | null;
  conducteur: Conducteur | null;
  conducteur_origine?: "MANUEL" | "BADGE" | null;
  situation: string | null;
  statut_camion: "LIBRE" | "VIDE" | "CHARGÉ" | null;
  depot_recepteur: string | null;
  distributeur: string | null;
  produit: string | null;
  numero_ot: string | null;
  emplacement_j_moins_1: string | null;
  position_08h: string | null;
  position_10h: string | null;
  position_12h: string | null;
  position_14h: string | null;
  position_16h: string | null;
  position_18h: string | null;
  // §0vicies decies N2 (31/08/2026) — relevés automatiques du soir
  position_20h: string | null;
  position_22h: string | null;
  heure_depart: string | null;
  arret_final: string | null;
  /** §0nonies decies M4 (29/08/2026) — âge du dernier signal GPS (secondes) ;
      badge « boîtier muet — données en transit » dès 30 min de silence. */
  gps_age_s?: number | null;
  lieu_arret?: string | null;   // Addendum v1.9 §4.2 — colonne « Lieu Arrêt »
  tcc_s: number;
  tcj_s: number;
  ttj_s: number;
  total_pause_s: number;
  km_parcourus: number;
  trajets: Trajet[];
  nb_trajets: number;
  mission_id: string | null;
  flag_tcc: boolean;
  flag_tcj: boolean;
  flag_ttj: boolean;
}

export interface Mission {
  id: string;
  code_mission?: string;
  date_jour: string;
  conducteur_id: string | null;
  conducteur: Conducteur | null;
  vehicule_id: string;
  plaque: string;
  numero_mission_du_jour: number;
  statut: "EN_COURS" | "TERMINÉE" | "DÉVIÉE" | "RETARDÉE";
  statut_camion_actuel?: "VIDE" | "CHARGE" | "CHARGÉ" | "LIBRE" | string;
  heure_debut: string | null;
  heure_chargement?: string | null;
  heure_fin: string | null;
  duree_s: number;
  numero_ot: string | null;
  produit: string | null;
  depot: string | null;
  depot_prevu?: string | null;
  depot_effectif?: string | null;
  est_deviee?: boolean;
  motif_deviation?: string | null;
  distributeur: string | null;
  km_vide?: number;
  km_charge?: number;
  kilometrage: number;
  kilometrage_total?: number;
  nb_infractions?: number;
  origine: string | null;
  etapes: { etat: string; ts: string; lieu: string | null; zone?: string | null }[];
  created_at?: string;
  updated_at?: string;
}

export interface Infraction {
  id: string;
  date_jour: string;
  heure: string;
  /** §0octies decies L2 (27/08/2026) — fin verbatim Ym@ne (peut être au J+1) ;
   *  null → « — » à l'écran, jamais d'invention. */
  date_fin?: string | null;
  heure_fin?: string | null;
  conducteur: Conducteur | null;
  chauffeur_affiche: string | null;
  plaque: string;
  type: string;
  gravite: "CRITIQUE" | "MOYENNE" | "FAIBLE";
  duree_s: number | null;
  valeur_mesuree: number | null;
  seuil_reference: number | null;
  mission_id: string | null;
  source: string;
  adresse: string | null;
  // §0quinquies decies I3/I4 (v1.35) — onglet alimenté par Ym@ne
  nom: string | null;
  niveau: "ALERTE" | "ALARME" | null;
  seuil_unite: "kmh" | "s" | "brut" | null;
  seuil_texte: string | null;
  seuil_libelle: string | null;
  coordonnees: string | null;
  validation: "NON_TRAITEE" | "VALIDE" | "INVALIDE";
  observation: string | null;
  validee_par: string | null;
  validee_le: string | null;
  ymane_id: string | null;
}

export interface CompteursInfractions {
  non_traitees: number;
  validees: number;
  invalidees: number;
  comptabilisees: number;
}

export interface Alerte {
  id: string;
  date_heure: string;
  type: string;
  gravite: "CRITIQUE" | "MOYENNE" | "INFORMATION";
  plaque: string | null;
  conducteur: Conducteur | null;
  message: string;
  statut: "NOUVELLE" | "VUE" | "TRAITEE";
  lien_module: string | null;
}

export interface Referentiels {
  situations: string[];
  statuts_camion: string[];
  depots: string[];
  distributeurs: string[];
  produits: string[];
  statuts_vehicule: string[];
  statuts_conducteur: string[];
}

export interface JourneeTCH {
  tcj_s: number;
  ttj_s: number;
  vehicules: string[];
  inclus_dans_tch: boolean;
  en_cours: boolean;
}

export interface ConducteurTCH {
  conducteur_id: string;
  nom_prenom: string;
  prenom_usuel: string;
  matricule: string;
  telephone: string | null;
  statut: string;
  vehicules_actifs: string[];
  tch_cumul_s: number;
  tch_restant_s: number;
  date_dernier_reset: string | null;
  alerte_statut: "NORMAL" | "PROCHE_LIMITE" | "LIMITE_ATTEINTE";
  historique: Record<string, JourneeTCH>;
}

export interface SyntheseTCH {
  du: string;
  au: string;
  dates: string[];
  seuils: {
    seuil_alerte_s: number;
    seuil_max_s: number;
    seuil_reset_repos_s: number;
  };
  stats: {
    total_chauffeurs: number;
    en_conduite_aujourdhui: number;
    proche_limite: number;
    limite_atteinte: number;
    tch_moyen_s: number;
  };
  lignes: ConducteurTCH[];
}
