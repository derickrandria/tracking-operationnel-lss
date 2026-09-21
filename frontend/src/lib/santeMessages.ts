/**
 * R7/v1.54 (21/09/2026) — MESSAGES DE COLLECTE : l'écran dit la CAUSE RÉELLE.
 *
 * Avant : dès qu'une source figurait dans `sources_bloquees_localement`
 * (c'est-à-dire « attente SQLite »), l'interface affichait
 * « collecte MZONEX bloquée localement — base verrouillée ». Or un dépassement
 * de budget passé à attendre le PORTAIL, une pagination lente ou une écriture
 * interrompue par l'échéance produisaient le même bandeau : l'exploitant
 * cherchait un verrou SQLite inexistant.
 *
 * Règle : le message se compose avec TROIS informations distinctes fournies par
 * le backend — le STATUT global, la CLASSE d'erreur et la PHASE réelle — et
 * jamais avec une supposition. Un DÉPASSEMENT DE BUDGET se dit comme tel, quelle
 * que soit la phase ; l'écriture n'implique « base verrouillée » QUE si la
 * classe est réellement `attente_sqlite` et qu'aucun dépassement n'est en cause.
 */

export type EtatCollecteSource = {
  statut?: string | null;
  classe?: string | null;
  /** Cause DÉRIVÉE par le backend : `portail_lent` quand le budget a été
      consommé par le portail — l'issue (classe) reste `budget_depasse`. */
  cause?: string | null;
  phase?: string | null;
  /** Ancien nom de la phase dans /api/sante (compatibilité). */
  etape?: string | null;
  issue?: string | null;
  raison_annulation?: string | null;
  /** Action attendue de l'exploitant (fournie par le backend). */
  action?: string | null;
  /** Message déjà composé par le backend : prioritaire s'il est fourni. */
  message?: string | null;
};

/** Phases telles qu'elles sont publiées par `/api/sante` (§ « phase »). */
export const PHASES_LISIBLES: Record<string, string> = {
  attente_http: "attente du portail",
  authentification: "authentification",
  pagination: "lecture paginée",
  vehicule: "traitement par véhicule",
  parsing: "lecture des données",
  ecriture: "écriture en base",
  verrou: "attente du verrou",
  inconnue: "non déterminée",
};

export function libellePhase(phase?: string | null): string {
  const cle = String(phase || "").toLowerCase();
  return PHASES_LISIBLES[cle] || "non déterminée";
}

/**
 * Message affiché pour une source. `null` = aucun bandeau à montrer.
 */
export function messageCollecte(
  source: string,
  etat?: EtatCollecteSource | null,
): string | null {
  if (!etat) return null;
  const statut = etat.statut || null;
  // La CAUSE (quand le backend la fournit) précise la classe : un budget
  // consommé par le portail reste un dépassement de budget, mais l'écran dit
  // aussi que la lenteur vient du portail.
  const cause = etat.cause || etat.classe || null;
  const classe = etat.classe || null;
  const phase = libellePhase(etat.phase || etat.etape);
  const motif = etat.raison_annulation ? ` (motif : ${etat.raison_annulation})` : "";

  // 1) Un DÉPASSEMENT DE BUDGET se dit comme tel — même si la phase est
  //    l'écriture : jamais « base verrouillée ».
  if (etat.issue === "BUDGET_DEPASSE" || classe === "budget_depasse"
      || statut === "COLLECTE_BUDGET_DEPASSE") {
    return `Collecte ${source} interrompue : budget dépassé pendant la phase ${phase}${motif}. Nouvelle tentative prévue.`;
  }
  // 2) Le portail répond, mais lentement (budget consommé CÔTÉ PORTAIL).
  if (cause === "portail_lent") {
    return `Collecte ${source} ralentie : le portail répond lentement (phase ${phase}). La collecte reprendra au prochain cycle.`;
  }
  // 3) Notre base a fait attendre l'écriture — c'est le SEUL cas « base
  //    verrouillée », et il est nommé précisément.
  if (cause === "attente_sqlite") {
    return `Collecte ${source} interrompue : la base de données a fait attendre l'écriture (phase ${phase}). Nouvelle tentative prévue.`;
  }
  // 4) Verrou applicatif détenu par une autre tâche.
  if (cause === "verrou_occupe") {
    return `Collecte ${source} en attente : une autre tâche détient le verrou de cette source.`;
  }
  // 5) Configuration manquante.
  if (cause === "configuration_absente") {
    return `Collecte ${source} impossible : la configuration de la source est absente (jeton ou identifiants).`;
  }
  // 6) Portail injoignable.
  if (cause === "portail_indisponible") {
    return `Source ${source} en panne — collecte interrompue (le portail ne répond pas).`;
  }
  // 7) Passe en cours : information, pas alerte.
  if (statut === "COLLECTE_EN_COURS") {
    return `Collecte ${source} en cours (phase ${phase}).`;
  }
  // 8) Échec sans classe reconnue.
  if (cause === "collecte_echouee" || statut === "COLLECTE_ECHOUEE"
      || statut === "COLLECTE_DEGRADEE") {
    return `Collecte ${source} en échec (phase ${phase}). Diagnostic dans /api/sante.`;
  }
  return null;
}

/** Le bandeau historique « base verrouillée » ne doit PLUS apparaître pour un
 * dépassement de budget, un portail lent ou une attente HTTP (R7). */
export function estBaseVerrouillee(etat?: EtatCollecteSource | null): boolean {
  if (!etat) return false;
  if (etat.issue === "BUDGET_DEPASSE") return false;
  return etat.classe === "attente_sqlite";
}
