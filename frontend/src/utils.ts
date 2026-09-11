/** Formatters et libellés métier. */

export const cls = (...parts: (string | false | null | undefined)[]) =>
  parts.filter(Boolean).join(" ");

/** 16200 -> "4h30" ; 720 -> "12 min" */
export function fmtDuree(s: number | null | undefined): string {
  /** Durée au format H:MM (Addendum v1.8 §CA-6) : 1500 → « 0:25 »,
   *  16200 → « 4:30 » ; les HEURES restent en HH:MM (fmtHeure). */
  if (s === null || s === undefined) return "—";
  const neg = s < 0;
  const v = Math.abs(Math.round(s));
  if (v < 60) return `${neg ? "-" : ""}0:${String(v).padStart(2, "0")}`;
  const h = Math.floor(v / 3600);
  const m = Math.floor((v % 3600) / 60);
  return `${neg ? "-" : ""}${h}:${String(m).padStart(2, "0")}`;
}

export function fmtHeure(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.slice(11, 16);
}

export function fmtDateHeure(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) {
      // Fallback si chaîne non standard
      const clean = iso.replace("T", " ").slice(0, 16);
      return clean || "—";
    }
    const day = String(d.getDate()).padStart(2, "0");
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const year = d.getFullYear();
    const hours = String(d.getHours()).padStart(2, "0");
    const minutes = String(d.getMinutes()).padStart(2, "0");
    return `${day}/${month}/${year} ${hours}:${minutes}`;
  } catch {
    return String(iso).slice(0, 16).replace("T", " ");
  }
}

export function fmtDateFr(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso + (iso.length === 10 ? "T12:00:00" : "")).toLocaleDateString("fr-FR", {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });
}

export const LABELS_INFRACTION: Record<string, string> = {
  EXCES_VITESSE: "Excès de vitesse",
  ACCELERATION_BRUSQUE: "Accélération brusque",
  FREINAGE_BRUSQUE: "Freinage brusque",
  DEPASSEMENT_TCC: "Dépassement TCC",
  DEPASSEMENT_TCJ: "Dépassement TCJ",
  DEPASSEMENT_TTJ: "Dépassement TTJ",
};

export const LABELS_ALERTE: Record<string, string> = {
  TCC_DEPASSE: "Seuil réglementaire",
  PAUSE_NON_PRISE: "Pause non prise",
  CAMION_IMMOBILE: "Camion immobile",
  GPS_HORS_LIGNE: "GPS hors ligne",
  MISSION_RETARDEE: "Mission retardée",
  EXCES_VITESSE: "Excès de vitesse",
  HORS_ITINERAIRE: "Hors itinéraire",
  CARBURANT_SUSPECT: "Carburant suspect",
  NB_TRAJETS_EXCEPTIONNEL: "Nb trajets exceptionnel (> 10)",
  NOUVEAU_VEHICULE: "Nouveau véhicule détecté",
  NOUVEAU_CONDUCTEUR: "Nouveau chauffeur détecté",
  VITESSE_LIVE: "Excès de vitesse en direct",
  SANS_BADGE: "Roule sans badge",
  REPARATION_DONNEES: "Réparation de données",
  COLLECTE_YMANE: "Collecte Ym@ne en échec",
  COLLECTE_RETARD: "Collecte GPS en retard",
  TCH_PROCHE_LIMITE: "TCH proche de la limite",
  TCH_LIMITE_ATTEINTE: "TCH limite atteinte",
  CONFLIT_AFFECTATION: "Conflit d'affectation chauffeur",
  DOUBLON_CONDUCTEUR: "Doublon chauffeur",
  CHANGEMENT_CONDUCTEUR_DETECTE: "Changement de conducteur détecté (arbitrage)",
  MISSION_SANS_OT: "Mission sans OT",
  VALIDATION_CHARGEMENT: "Validation chargement",
  VALIDATION_DECHARGEMENT: "Validation déchargement",
  DEVIATION_DETECTEE: "Déviation détectée",
};

export const COULEURS_GRAVITE: Record<string, string> = {
  CRITIQUE: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/40",
  MOYENNE: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/40",
  FAIBLE: "bg-sky-500/15 text-sky-600 dark:text-sky-400 border-sky-500/40",
  INFORMATION: "bg-slate-500/15 text-slate-600 dark:text-slate-400 border-slate-500/40",
};

export const COULEURS_STATUT_CAMION: Record<string, string> = {
  "LIBRE": "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40",
  "VIDE": "bg-sky-500/15 text-sky-600 dark:text-sky-400 border-sky-500/40",
  "CHARGE": "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/40",
  "CHARGÉ": "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/40",
};

export const COULEURS_STATUT_MISSION: Record<string, string> = {
  EN_COURS: "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/40",
  "TERMINÉE": "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40",
  "DÉVIÉE": "bg-purple-500/15 text-purple-600 dark:text-purple-400 border-purple-500/40",
  "RETARDÉE": "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/40",
};

export const COULEURS_STATUT_ALERTE: Record<string, string> = {
  NOUVELLE: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/40",
  VUE: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/40",
  TRAITEE: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40",
};

export function firstDayOfMonthISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

export function daysAgoISO(n: number = 30): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function todayISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
