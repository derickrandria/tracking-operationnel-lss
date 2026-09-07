/**
 * Module 3 — Missions : Reconstitution & Suivi Automatique des Cycles Logistiques Pétroliers (§6.3 / v2026.2).
 * Gestion des statuts LIBRE ➔ VIDE ➔ CHARGÉ ➔ LIBRE, Détection GRT & Déchargements,
 * Alertes opérationnelles (OT manquant, validation chargement/déchargement),
 * Déviations d'itinéraires et Invalidation avec motifs explicatifs.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import Icon from "../components/icons";
import { Badge, Btn, Card, Champ, inputCls, Modal, PageHeader, Spinner, Vide } from "../components/ui";
import { Alerte, Conducteur, Mission, Vehicule } from "../types";
import {
  cls,
  COULEURS_STATUT_CAMION,
  COULEURS_STATUT_MISSION,
  daysAgoISO,
  firstDayOfMonthISO,
  fmtDateFr,
  fmtDateHeure,
  fmtDuree,
  fmtHeure,
  todayISO
} from "../utils";
import { on } from "../ws";

export interface CibleActionMission {
  id?: string;
  mission_id?: string;
  alerte_id?: string;
  plaque: string;
  numero_ot?: string | null;
  distributeur?: string | null;
  produit?: string | null;
  depot_prevu?: string | null;
  depot_effectif?: string | null;
  depot?: string | null;
}

const ETAPE_LABELS: Record<string, { label: string; couleur: string }> = {
  INITIALISATION_OT: { label: "Attribution OT (VIDE)", couleur: "bg-sky-500" },
  PRESENCE_BASE: { label: "Présence Base Tana (BASETNR)", couleur: "bg-slate-500" },
  DEPART_BASE: { label: "Départ physique Base Tana (Début réel)", couleur: "bg-blue-600" },
  TRANSIT_CHARGEMENT: { label: "Transit vers dépôt chargeur (VIDE)", couleur: "bg-sky-500" },
  ENTREE_GRT: { label: "Arrivée Zone GRT Toamasina", couleur: "bg-indigo-500" },
  CHARGEMENT_EFFECTUE: { label: "Chargement effectué — Sortie GRT (CHARGÉ)", couleur: "bg-amber-500" },
  CHARGEMENT_TERMINE: { label: "Chargement validé (CHARGÉ)", couleur: "bg-amber-500" },
  TRANSIT_LIVRAISON: { label: "Transit vers dépôt récepteur (CHARGÉ)", couleur: "bg-amber-500" },
  ARRIVEE_DEPOT_RECEPTEUR: { label: "Arrivée au dépôt récepteur", couleur: "bg-blue-600" },
  DEVIATION_DETECTEE: { label: "Déviation d'itinéraire constatée", couleur: "bg-purple-600" },
  DECHARGEMENT_EFFECTUE: { label: "Déchargement confirmé — Fin mission (LIBRE)", couleur: "bg-emerald-500" },
  DECHARGEMENT_TERMINE: { label: "Déchargement terminé (LIBRE)", couleur: "bg-emerald-500" },
  REPOSITIONNEMENT_RETOUR: { label: "Trajet retour vers Base Tana (LIBRE)", couleur: "bg-slate-500" },
};

const DEPOTS_LISTE = [
  { code: "DSNR", label: "Depot Soanierana (DSNR)" },
  { code: "DABI", label: "Depot Alarobia (DABI)" },
  { code: "DMMG", label: "Depot Moramanga (DMMG)" },
  { code: "DFIA", label: "Depot Fianarantsoa (DFIA)" },
  { code: "DMDV", label: "Depot Morondava (DMDV)" },
  { code: "DMKR", label: "Depot Manakara (DMKR)" },
  { code: "DABE", label: "Depot Antsirabe (DABE)" },
];

const MOTIFS_INVALIDATION = [
  "Échantillonnage de produit (simple passage)",
  "Repos chauffeur sur parking du dépôt",
  "Attente ouverture du dépôt / Congé",
  "Autre motif opérationnel",
];

const DISTRIBUTEURS_LISTE = ["TOTAL", "GALANA", "VIVO", "JOVENA"];
const PRODUITS_LISTE = ["Gasoil (GO)", "Super (SP95)", "Pétrole Lampant (PL)", "Fuel Oil (FO)"];

interface StatsMissions {
  total: number;
  en_cours: number;
  nb_en_cours_vide: number;
  nb_en_cours_charge: number;
  terminees: number;
  deviees: number;
  retardees: number;
  km_vide: number;
  km_charge: number;
  km_total: number;
  nb_infractions: number;
}

export default function Missions() {
  const [dateDebut, setDateDebut] = useState(daysAgoISO(30)); // 31 jours glissants par défaut
  const [dateFin, setDateFin] = useState(todayISO());
  const [statutFiltre, setStatutFiltre] = useState("TOUTES");
  const [depotFiltre, setDepotFiltre] = useState("");
  const [distributeurFiltre, setDistributeurFiltre] = useState("");
  const [recherche, setRecherche] = useState("");

  const [missions, setMissions] = useState<Mission[]>([]);
  const [alertes, setAlertes] = useState<Alerte[]>([]);
  const [stats, setStats] = useState<StatsMissions | null>(null);
  const [chargement, setChargement] = useState(true);
  const [rattrapageEnCours, setRattrapageEnCours] = useState(false);

  // Modals et actions
  const [missionSelectionnee, setMissionSelectionnee] = useState<Mission | null>(null);
  const [modalNouvelleMissionOuverte, setModalNouvelleMissionOuverte] = useState(false);
  const [cibleInvalidation, setCibleInvalidation] = useState<CibleActionMission | null>(null);
  const [cibleDeviation, setCibleDeviation] = useState<CibleActionMission | null>(null);
  const [cibleSaisieOt, setCibleSaisieOt] = useState<CibleActionMission | null>(null);

  const [vehicules, setVehicules] = useState<Vehicule[]>([]);
  const [conducteurs, setConducteurs] = useState<Conducteur[]>([]);

  const timer = useRef<number>();

  async function lancerRattrapage7j(silencieux = false) {
    if (rattrapageEnCours) return;
    setRattrapageEnCours(true);
    try {
      await api("/api/missions/rattrapage", { method: "POST" });
      await chargerDonnees();
      await chargerAlertes();
    } catch (e) {
      if (!silencieux) {
        console.error("Erreur lors du rattrapage des missions sur 7 jours:", e);
      }
    } finally {
      setRattrapageEnCours(false);
    }
  }

  async function chargerAlertes() {
    try {
      const res = await api("/api/missions/alertes");
      setAlertes(res.items || []);
    } catch (e) {
      console.error("Erreur chargement alertes missions:", e);
    }
  }

  async function chargerDonnees() {
    try {
      const params = new URLSearchParams({
        date_debut: dateDebut,
        date_fin: dateFin,
      });
      if (statutFiltre !== "TOUTES") params.append("statut", statutFiltre);
      if (depotFiltre) params.append("depot", depotFiltre);
      if (distributeurFiltre) params.append("distributeur", distributeurFiltre);
      if (recherche.trim()) params.append("q", recherche.trim());

      const resMissions = await api(`/api/missions?${params.toString()}`);
      setMissions(resMissions.missions || []);
      setStats(resMissions.stats || null);
    } catch (e) {
      console.error("Erreur chargement missions:", e);
    } finally {
      setChargement(false);
    }
  }

  async function chargerReferentiels() {
    try {
      const [v, c] = await Promise.all([
        api("/api/vehicules"),
        api("/api/conducteurs"),
      ]);
      setVehicules(v || []);
      setConducteurs(c || []);
    } catch (e) {
      console.error("Erreur chargement référentiels:", e);
    }
  }

  // Chargement initial immédiat + Rattrapage automatique 7 jours en tâche de fond
  useEffect(() => {
    chargerReferentiels();
    chargerDonnees();
    chargerAlertes();
    lancerRattrapage7j(true);
  }, []);

  useEffect(() => {
    chargerDonnees();
  }, [dateDebut, dateFin, statutFiltre, depotFiltre, distributeurFiltre, recherche]);

  useEffect(() => {
    const rafraichir = () => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => {
        chargerDonnees();
        chargerAlertes();
      }, 1500);
    };
    const offs = [
      "mission.new",
      "mission.update",
      "alerte.new",
      "alerte.update",
      "suivi.update",
      "referentiels.changed",
      "data.refresh"
    ].map((t) => on(t, rafraichir));
    return () => offs.forEach((f) => f());
  }, [dateDebut, dateFin, statutFiltre, depotFiltre, distributeurFiltre, recherche]);

  const exporterExcel = () => {
    const params = new URLSearchParams({
      date_debut: dateDebut,
      date_fin: dateFin,
    });
    if (statutFiltre !== "TOUTES") params.append("statut", statutFiltre);
    if (depotFiltre) params.append("depot", depotFiltre);
    if (distributeurFiltre) params.append("distributeur", distributeurFiltre);
    if (recherche.trim()) params.append("q", recherche.trim());
    window.open(`/api/missions/export.xlsx?${params.toString()}`, "_blank");
  };

  const exporterPdf = () => {
    const params = new URLSearchParams({
      date_debut: dateDebut,
      date_fin: dateFin,
    });
    if (statutFiltre !== "TOUTES") params.append("statut", statutFiltre);
    if (depotFiltre) params.append("depot", depotFiltre);
    if (distributeurFiltre) params.append("distributeur", distributeurFiltre);
    if (recherche.trim()) params.append("q", recherche.trim());
    window.open(`/api/missions/export.pdf?${params.toString()}`, "_blank");
  };

  // Actions rapides résilientes (fonctionnent avec mission existante ou via plaque / alerte_id)
  async function actionValiderChargementRapide(cible: CibleActionMission) {
    if (!confirm(`Confirmez-vous la validation du chargement à GRT pour le véhicule ${cible.plaque} ?`)) {
      return;
    }
    try {
      await api("/api/missions/action-rapide", {
        method: "POST",
        body: JSON.stringify({
          action: "VALIDER_CHARGEMENT",
          mission_id: cible.mission_id || cible.id,
          alerte_id: cible.alerte_id,
          plaque: cible.plaque,
        }),
      });
      await chargerDonnees();
      await chargerAlertes();
    } catch (e: any) {
      alert("Erreur lors de la validation du chargement : " + (e?.message || e));
    }
  }

  async function actionValiderDechargementRapide(cible: CibleActionMission) {
    if (!confirm(`Confirmez-vous la validation du déchargement pour le véhicule ${cible.plaque} ? La mission passera à TERMINÉE et le statut du camion deviendra LIBRE.`)) {
      return;
    }
    try {
      await api("/api/missions/action-rapide", {
        method: "POST",
        body: JSON.stringify({
          action: "VALIDER_DECHARGEMENT",
          mission_id: cible.mission_id || cible.id,
          alerte_id: cible.alerte_id,
          plaque: cible.plaque,
        }),
      });
      await chargerDonnees();
      await chargerAlertes();
    } catch (e: any) {
      alert("Erreur lors de la validation du déchargement : " + (e?.message || e));
    }
  }

  async function actionTraiterAlerteRapide(alerteId: string) {
    try {
      await api("/api/missions/action-rapide", {
        method: "POST",
        body: JSON.stringify({
          action: "TRAITER_ALERTE",
          alerte_id: alerteId,
        }),
      });
      await chargerAlertes();
      await chargerDonnees();
    } catch (e: any) {
      alert("Erreur lors du traitement de l'alerte : " + (e?.message || e));
    }
  }

  const setPeriode = (mode: "31j" | "7j" | "aujourdhui" | "mois" | "mois_prec") => {
    const now = new Date();
    if (mode === "31j") {
      setDateDebut(daysAgoISO(30));
      setDateFin(todayISO());
    } else if (mode === "7j") {
      const j7 = new Date(now);
      j7.setDate(now.getDate() - 6);
      setDateDebut(j7.toISOString().slice(0, 10));
      setDateFin(todayISO());
      lancerRattrapage7j(false);
    } else if (mode === "aujourdhui") {
      setDateDebut(todayISO());
      setDateFin(todayISO());
    } else if (mode === "mois") {
      setDateDebut(firstDayOfMonthISO());
      setDateFin(todayISO());
    } else if (mode === "mois_prec") {
      const debutMoisPrec = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      const finMoisPrec = new Date(now.getFullYear(), now.getMonth(), 0);
      setDateDebut(debutMoisPrec.toISOString().slice(0, 10));
      setDateFin(finMoisPrec.toISOString().slice(0, 10));
    }
  };

  return (
    <div className="space-y-4">
      {/* En-tête principal */}
      <PageHeader
        titre="Missions & Cycles Logistiques"
        sousTitre={
          <>
            Période du <b>{fmtDateFr(dateDebut)}</b> au <b>{fmtDateFr(dateFin)}</b> ·{" "}
            {stats ? (
              <>
                <b>{stats.total}</b> missions enregistrées (<b>{stats.en_cours}</b> en cours,{" "}
                <b>{stats.terminees}</b> terminées, <b>{stats.deviees}</b> déviées)
              </>
            ) : (
              "Chargement des métriques…"
            )}
          </>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Btn
              variante="primaire"
              onClick={() => setModalNouvelleMissionOuverte(true)}
              className="bg-blue-600 hover:bg-blue-700 text-white font-medium"
            >
              <Icon nom="plus" className="w-4 h-4" />
              <span>+ Assigner OT / Nouvelle Mission</span>
            </Btn>
            <Btn
              variante="secondaire"
              onClick={() => lancerRattrapage7j(false)}
              disabled={rattrapageEnCours}
              title="Lancer la collecte rétrospective et le rattrapage automatique sur les 7 derniers jours"
            >
              {rattrapageEnCours ? <Spinner /> : <Icon nom="rafraichir" className="w-4 h-4 text-blue-500" />}
              <span>{rattrapageEnCours ? "Rattrapage en cours…" : "Rattrapage 7 jours"}</span>
            </Btn>
            <Btn variante="secondaire" onClick={exporterExcel} title="Exporter les missions sous Excel">
              <Icon nom="telecharger" className="w-4 h-4" />
              <span>Excel</span>
            </Btn>
            <Btn variante="secondaire" onClick={exporterPdf} title="Exporter les missions sous PDF">
              <Icon nom="telecharger" className="w-4 h-4" />
              <span>PDF</span>
            </Btn>
          </div>
        }
      />

      {/* Bandeau d'Alertes Logistiques Spécifiques (Règle 7 & 9) */}
      {alertes && alertes.length > 0 && (
        <Card className="!p-3 border border-amber-300 dark:border-amber-700 bg-amber-50/70 dark:bg-amber-950/40">
          <div className="flex items-center justify-between pb-2 border-b border-amber-200 dark:border-amber-800">
            <div className="flex items-center gap-2 text-[13px] font-bold text-amber-900 dark:text-amber-200 uppercase tracking-wide">
              <Icon nom="alerte" className="w-4 h-4 text-amber-600 dark:text-amber-400" />
              <span>Alertes & Actions Opérationnelles Requises ({alertes.length})</span>
            </div>
            <span className="text-[11px] text-amber-700 dark:text-amber-300 italic">
              Persistance active (conservées jusqu'à validation)
            </span>
          </div>

          <div className="mt-2 space-y-2 max-h-56 overflow-y-auto">
            {alertes.map((a) => {
              const missionAssociee = missions.find(
                (m) => m.plaque === a.plaque && (m.statut === "EN_COURS" || m.statut === "DÉVIÉE")
              );
              const cibleAlerte: CibleActionMission = {
                id: missionAssociee?.id,
                mission_id: missionAssociee?.id,
                alerte_id: a.id,
                plaque: a.plaque || "",
                numero_ot: missionAssociee?.numero_ot,
                distributeur: missionAssociee?.distributeur,
                produit: missionAssociee?.produit,
                depot_prevu: missionAssociee?.depot_prevu,
                depot_effectif: missionAssociee?.depot_effectif,
              };

              const badgeLabel =
                a.type === "MISSION_SANS_OT"
                  ? "OT Manquant (≥ 15 min)"
                  : a.type === "VALIDATION_CHARGEMENT"
                  ? "Validation Chargement (≥ 30 min)"
                  : a.type === "VALIDATION_DECHARGEMENT"
                  ? "Validation Déchargement (≥ 3h)"
                  : a.type === "DEVIATION_DETECTEE"
                  ? "Déviation Constatée"
                  : a.type;

              const badgeColor =
                a.type === "MISSION_SANS_OT"
                  ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300 border-red-300 dark:border-red-800"
                  : a.type === "VALIDATION_CHARGEMENT"
                  ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300 border-amber-300 dark:border-amber-800"
                  : a.type === "VALIDATION_DECHARGEMENT"
                  ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300 border-blue-300 dark:border-blue-800"
                  : "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300 border-purple-300 dark:border-purple-800";

              return (
                <div
                  key={a.id}
                  className="flex flex-wrap items-center justify-between gap-2 p-2 rounded-lg bg-white/95 dark:bg-slate-800/95 border border-amber-200/80 dark:border-amber-900/60 text-[12.5px] shadow-xs hover:border-amber-400 transition-colors"
                >
                  <div
                    className="flex items-center gap-2.5 flex-1 min-w-[280px] cursor-pointer"
                    onClick={() => {
                      if (missionAssociee) {
                        setMissionSelectionnee(missionAssociee);
                      } else if (a.plaque) {
                        setRecherche(a.plaque);
                      }
                    }}
                    title="Cliquer pour afficher la mission correspondante"
                  >
                    <span className={cls("px-2 py-0.5 rounded text-[11px] font-bold uppercase border", badgeColor)}>
                      {badgeLabel}
                    </span>
                    <span className="font-bold text-slate-800 dark:text-slate-100 font-mono underline decoration-dotted">
                      {a.plaque || "Véhicule inconnu"}
                    </span>
                    <span className="text-slate-600 dark:text-slate-300">{a.message}</span>
                    <span className="text-[11px] font-mono text-slate-400">({fmtDateHeure(a.date_heure)})</span>
                  </div>

                  {/* Actions contextuelles résilientes sur l'alerte */}
                  <div className="flex items-center gap-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                    {a.type === "MISSION_SANS_OT" && (
                      <button
                        type="button"
                        onClick={() => setCibleSaisieOt(cibleAlerte)}
                        className="px-2.5 py-1 text-[11px] font-semibold rounded bg-sky-600 hover:bg-sky-700 text-white shadow-xs"
                      >
                        Saisir OT
                      </button>
                    )}

                    {a.type === "VALIDATION_CHARGEMENT" && (
                      <button
                        type="button"
                        onClick={() => actionValiderChargementRapide(cibleAlerte)}
                        className="px-2.5 py-1 text-[11px] font-semibold rounded bg-amber-600 hover:bg-amber-700 text-white shadow-xs"
                      >
                        ✓ Valider Chargement
                      </button>
                    )}

                    {a.type === "VALIDATION_DECHARGEMENT" && (
                      <>
                        <button
                          type="button"
                          onClick={() => actionValiderDechargementRapide(cibleAlerte)}
                          className="px-2.5 py-1 text-[11px] font-semibold rounded bg-emerald-600 hover:bg-emerald-700 text-white shadow-xs"
                        >
                          ✓ Valider Déchargement
                        </button>
                        <button
                          type="button"
                          onClick={() => setCibleInvalidation(cibleAlerte)}
                          className="px-2 py-1 text-[11px] font-semibold rounded bg-slate-200 hover:bg-slate-300 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200"
                        >
                          ✗ Invalider
                        </button>
                      </>
                    )}

                    {a.type === "DEVIATION_DETECTEE" && (
                      <button
                        type="button"
                        onClick={() => setCibleDeviation(cibleAlerte)}
                        className="px-2.5 py-1 text-[11px] font-semibold rounded bg-purple-600 hover:bg-purple-700 text-white shadow-xs"
                      >
                        ⇄ Changer Dépôt
                      </button>
                    )}

                    <button
                      type="button"
                      onClick={() => actionTraiterAlerteRapide(a.id)}
                      title="Marquer cette alerte comme traitée et la masquer"
                      className="px-2 py-1 text-[11px] font-medium rounded text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700"
                    >
                      ✓ Traiter
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* Bandeau KPIs de synthèse */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Card className="!p-3 border-l-4 border-l-blue-500">
            <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Total Missions</div>
            <div className="mt-1 text-2xl font-bold text-slate-800 dark:text-slate-100 tabular-nums">{stats.total}</div>
            <div className="mt-0.5 text-[11px] text-slate-500">Sur la période</div>
          </Card>

          <Card className="!p-3 border-l-4 border-l-sky-500">
            <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Missions En Cours</div>
            <div className="mt-1 text-2xl font-bold text-sky-600 dark:text-sky-400 tabular-nums">{stats.en_cours}</div>
            <div className="mt-0.5 text-[11px] text-slate-500 flex items-center gap-2">
              <span className="text-sky-500 font-semibold">{stats.nb_en_cours_vide} VIDE</span> ·{" "}
              <span className="text-amber-500 font-semibold">{stats.nb_en_cours_charge} CHARGÉ</span>
            </div>
          </Card>

          <Card className="!p-3 border-l-4 border-l-emerald-500">
            <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Missions Terminées</div>
            <div className="mt-1 text-2xl font-bold text-emerald-600 dark:text-emerald-400 tabular-nums">{stats.terminees}</div>
            <div className="mt-0.5 text-[11px] text-emerald-600/80">Déchargées & Clôturées</div>
          </Card>

          <Card className="!p-3 border-l-4 border-l-purple-500">
            <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Missions Déviées</div>
            <div className="mt-1 text-2xl font-bold text-purple-600 dark:text-purple-400 tabular-nums">{stats.deviees}</div>
            <div className="mt-0.5 text-[11px] text-purple-600/80">Réorientées en route</div>
          </Card>

          <Card className="!p-3 border-l-4 border-l-amber-500">
            <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Km Total Parcouru</div>
            <div className="mt-1 text-2xl font-bold text-amber-600 dark:text-amber-400 tabular-nums">
              {stats.km_total.toLocaleString("fr-FR")} <span className="text-xs font-normal">km</span>
            </div>
            <div className="mt-0.5 text-[11px] text-slate-500 truncate" title={`${stats.km_vide} km à vide · ${stats.km_charge} km chargé`}>
              {stats.km_vide.toFixed(0)} km vide · {stats.km_charge.toFixed(0)} km ch.
            </div>
          </Card>

          <Card className="!p-3 border-l-4 border-l-red-500">
            <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Infractions Validées</div>
            <div className="mt-1 text-2xl font-bold text-red-600 dark:text-red-400 tabular-nums">{stats.nb_infractions}</div>
            <div className="mt-0.5 text-[11px] text-slate-500">Sur l'intervalle mission</div>
          </Card>
        </div>
      )}

      {/* Barre de Filtres Complète */}
      <Card className="!p-3 bg-slate-50/50 dark:bg-slate-900/40">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* Période & Boutons Rapides */}
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="flex items-center gap-2 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-2.5 py-1 shadow-sm">
              <Icon nom="calendrier" className="h-4 w-4 text-blue-500" />
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">Date début :</span>
                <input
                  type="date"
                  value={dateDebut}
                  max={dateFin || todayISO()}
                  onChange={(e) => setDateDebut(e.target.value)}
                  className="bg-transparent text-[13px] font-medium text-slate-800 dark:text-slate-100 outline-none cursor-pointer"
                />
              </div>
              <span className="text-slate-300 dark:text-slate-600 font-bold px-0.5">➔</span>
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">Date fin :</span>
                <input
                  type="date"
                  value={dateFin}
                  min={dateDebut}
                  max={todayISO()}
                  onChange={(e) => setDateFin(e.target.value)}
                  className="bg-transparent text-[13px] font-medium text-slate-800 dark:text-slate-100 outline-none cursor-pointer"
                />
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-1">
              <button
                type="button"
                onClick={() => setPeriode("31j")}
                className={cls(
                  "px-2.5 py-1 text-[12px] font-medium rounded-md border transition-colors",
                  dateDebut === daysAgoISO(30) && dateFin === todayISO()
                    ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-semibold"
                    : "border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:border-slate-400"
                )}
              >
                31 derniers jours
              </button>
              <button
                type="button"
                onClick={() => setPeriode("7j")}
                className="px-2.5 py-1 text-[12px] font-medium rounded-md bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:border-slate-400"
              >
                7 derniers jours
              </button>
              <button
                type="button"
                onClick={() => setPeriode("aujourdhui")}
                className="px-2.5 py-1 text-[12px] font-medium rounded-md bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:border-slate-400"
              >
                Aujourd'hui
              </button>
              <button
                type="button"
                onClick={() => setPeriode("mois")}
                className="px-2.5 py-1 text-[12px] font-medium rounded-md bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:border-slate-400"
              >
                Ce mois-ci
              </button>
              <button
                type="button"
                onClick={() => setPeriode("mois_prec")}
                className="px-2.5 py-1 text-[12px] font-medium rounded-md bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:border-slate-400"
              >
                Mois dernier
              </button>
            </div>
          </div>

          {/* Filtres contextuels */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Filtre Statut */}
            <select
              value={statutFiltre}
              onChange={(e) => setStatutFiltre(e.target.value)}
              className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-[13px] font-medium text-slate-700 dark:text-slate-200 outline-none"
            >
              <option value="TOUTES">Tous statuts</option>
              <option value="EN_COURS">En cours</option>
              <option value="TERMINÉE">Terminée</option>
              <option value="DÉVIÉE">Déviée</option>
            </select>

            {/* Filtre Dépôt */}
            <select
              value={depotFiltre}
              onChange={(e) => setDepotFiltre(e.target.value)}
              className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-[13px] font-medium text-slate-700 dark:text-slate-200 outline-none"
            >
              <option value="">Tous dépôts</option>
              {DEPOTS_LISTE.map((d) => (
                <option key={d.code} value={d.code}>
                  {d.label}
                </option>
              ))}
            </select>

            {/* Filtre Distributeur */}
            <select
              value={distributeurFiltre}
              onChange={(e) => setDistributeurFiltre(e.target.value)}
              className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-[13px] font-medium text-slate-700 dark:text-slate-200 outline-none"
            >
              <option value="">Tous distributeurs</option>
              {DISTRIBUTEURS_LISTE.map((dist) => (
                <option key={dist} value={dist}>
                  {dist}
                </option>
              ))}
            </select>

            {/* Recherche textuelle */}
            <div className="relative">
              <input
                type="text"
                value={recherche}
                onChange={(e) => setRecherche(e.target.value)}
                placeholder="Chauffeur, plaque, N° OT…"
                className="w-56 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 pl-8 pr-3 py-1.5 text-[13px] outline-none focus:border-blue-500"
              />
              <span className="absolute left-2.5 top-2 text-slate-400">
                <Icon nom="recherche" className="w-3.5 h-3.5" />
              </span>
            </div>
          </div>
        </div>
      </Card>

      {/* Grille Principale des Missions */}
      <Card className="!p-0 overflow-hidden shadow-sm">
        {chargement ? (
          <div className="flex h-64 items-center justify-center gap-2 text-slate-400">
            <Spinner /> Chargement des missions…
          </div>
        ) : missions.length === 0 ? (
          <Vide texte="Aucune mission trouvée pour cette période et ces critères de filtrage." />
        ) : (
          <div className="relative max-h-[calc(100vh-320px)] overflow-x-auto overflow-y-auto">
            <table className="w-full border-collapse text-left text-[12.5px]">
              {/* En-tête de Table Fixe */}
              <thead className="sticky top-0 z-10 border-b border-slate-200 dark:border-slate-700 bg-slate-100/90 dark:bg-slate-800/90 backdrop-blur font-semibold text-slate-600 dark:text-slate-300">
                <tr>
                  <th className="px-3.5 py-2.5 whitespace-nowrap">N° Mission</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">Date</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">Chauffeur</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">Immatriculation</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">N° OT</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">Distributeur</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">Produit</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">Dépôt Prévu</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">Destination Réelle</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Statut Camion</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Statut Mission</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Début Mission</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Date Chargement</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Date Déchargement</th>
                  <th className="px-3 py-2.5 text-right whitespace-nowrap">Km Parcouru</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Infractions</th>
                  <th className="px-3.5 py-2.5 text-center whitespace-nowrap">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {missions.map((m) => {
                  const estDeviee = m.statut === "DÉVIÉE" || m.est_deviee;
                  const statutCamion = m.statut_camion_actuel || (m.statut === "TERMINÉE" ? "LIBRE" : (m.numero_ot ? "VIDE" : "LIBRE"));
                  const couleurCamion = COULEURS_STATUT_CAMION[statutCamion] || "bg-slate-500/10 text-slate-600";
                  const couleurMission = COULEURS_STATUT_MISSION[m.statut] || "bg-blue-500/10 text-blue-600";

                  const aBesoinOt = statutCamion === "LIBRE" && !m.numero_ot && m.statut === "EN_COURS";
                  const aChargementEnAttente = m.validation_chargement === "EN_ATTENTE" && m.statut === "EN_COURS";
                  const aDechargementEnAttente = m.validation_dechargement === "EN_ATTENTE" && m.statut === "EN_COURS";

                  return (
                    <tr
                      key={m.id}
                      className="hover:bg-slate-50/70 dark:hover:bg-slate-800/40 transition-colors group cursor-pointer"
                      onClick={() => setMissionSelectionnee(m)}
                    >
                      {/* 1. N° Mission */}
                      <td className="px-3.5 py-2.5 font-semibold text-blue-600 dark:text-blue-400 whitespace-nowrap">
                        <span className="font-mono bg-blue-50 dark:bg-blue-950/40 px-2 py-0.5 rounded border border-blue-200 dark:border-blue-900">
                          {m.code_mission || `MIS-OT-${m.numero_ot || m.id.slice(0, 6)}`}
                        </span>
                      </td>

                      {/* Date */}
                      <td className="px-3 py-2.5 whitespace-nowrap text-slate-500 font-mono text-[12px]">
                        {m.date_jour}
                      </td>

                      {/* Chauffeur */}
                      <td className="px-3 py-2.5 font-medium whitespace-nowrap text-slate-800 dark:text-slate-100">
                        {m.conducteur?.prenom_usuel ? (
                          <span>
                            <b>{m.conducteur.prenom_usuel}</b>
                            {m.conducteur.nom_prenom && m.conducteur.nom_prenom !== m.conducteur.prenom_usuel && (
                              <span className="text-[11px] text-slate-400 ml-1.5 block font-normal">
                                {m.conducteur.nom_prenom}
                              </span>
                            )}
                          </span>
                        ) : (
                          <span className="text-slate-400 italic">Non assigné</span>
                        )}
                      </td>

                      {/* Immatriculation */}
                      <td className="px-3 py-2.5 font-mono font-bold whitespace-nowrap text-slate-700 dark:text-slate-200">
                        {m.plaque || "—"}
                      </td>

                      {/* N° OT */}
                      <td className="px-3 py-2.5 font-mono text-slate-600 dark:text-slate-300 whitespace-nowrap">
                        {m.numero_ot ? (
                          <span className="font-semibold text-slate-800 dark:text-slate-100">{m.numero_ot}</span>
                        ) : (
                          <span className="text-amber-500 text-[11px] italic">Sans OT</span>
                        )}
                      </td>

                      {/* Distributeur */}
                      <td className="px-3 py-2.5 whitespace-nowrap font-medium text-slate-700 dark:text-slate-300">
                        {m.distributeur || "—"}
                      </td>

                      {/* Produit */}
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        {m.produit ? (
                          <span className="inline-block px-2 py-0.5 rounded text-[11px] font-semibold bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700">
                            {m.produit}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>

                      {/* Dépôt Prévu */}
                      <td className="px-3 py-2.5 whitespace-nowrap text-slate-700 dark:text-slate-300 font-medium">
                        {m.depot_prevu || m.depot || "—"}
                      </td>

                      {/* Destination Réelle (si déviée) */}
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        {estDeviee ? (
                          <span className="inline-flex items-center gap-1 font-bold text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-950/40 px-2 py-0.5 rounded border border-purple-200 dark:border-purple-800">
                            <span>➔ {m.depot_effectif || "Réorienté"}</span>
                            <span className="text-[10px] uppercase">(DÉVIÉE)</span>
                          </span>
                        ) : (
                          <span className="text-slate-500 font-normal">{m.depot_effectif || m.depot_prevu || "—"}</span>
                        )}
                      </td>

                      {/* Statut Camion */}
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <Badge couleur={couleurCamion}>{statutCamion}</Badge>
                      </td>

                      {/* Statut Mission */}
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <Badge couleur={couleurMission}>{m.statut}</Badge>
                      </td>

                      {/* 1. Début Mission (date_debut) */}
                      <td className="px-3 py-2.5 text-center font-mono text-[12px] whitespace-nowrap">
                        {m.heure_debut || m.date_debut ? (
                          <span className="text-slate-700 dark:text-slate-200">
                            {fmtDateHeure(m.heure_debut || m.date_debut)}
                          </span>
                        ) : (
                          <span className="text-amber-600 dark:text-amber-400 font-medium italic text-[11.5px] bg-amber-50 dark:bg-amber-950/40 px-2 py-0.5 rounded border border-amber-200 dark:border-amber-900">
                            En attente
                          </span>
                        )}
                      </td>

                      {/* 2. Date Chargement (date_chargement) */}
                      <td className="px-3 py-2.5 text-center font-mono text-[12px] whitespace-nowrap">
                        {m.heure_chargement || m.date_chargement ? (
                          <span className="text-slate-700 dark:text-slate-200">
                            {fmtDateHeure(m.heure_chargement || m.date_chargement)}
                          </span>
                        ) : aChargementEnAttente ? (
                          <span className="text-amber-600 dark:text-amber-400 font-medium italic text-[11px] bg-amber-50 dark:bg-amber-950/40 px-1.5 py-0.5 rounded border border-amber-300">
                            À valider (≥ 30 min)
                          </span>
                        ) : (
                          <span className="text-slate-400 font-mono">—</span>
                        )}
                      </td>

                      {/* 3. Date Déchargement (date_fin) */}
                      <td className="px-3 py-2.5 text-center font-mono text-[12px] whitespace-nowrap">
                        {m.statut === "TERMINÉE" && (m.heure_fin || m.date_fin) ? (
                          <span className="text-slate-700 dark:text-slate-200">
                            {fmtDateHeure(m.heure_fin || m.date_fin)}
                          </span>
                        ) : aDechargementEnAttente ? (
                          <span className="text-blue-600 dark:text-blue-400 font-medium italic text-[11px] bg-blue-50 dark:bg-blue-950/40 px-1.5 py-0.5 rounded border border-blue-300">
                            À valider (≥ 3h)
                          </span>
                        ) : m.motif_invalidation ? (
                          <span className="text-slate-500 font-medium text-[11px] italic" title={m.motif_invalidation}>
                            Invalidé ({m.motif_invalidation.slice(0, 15)}…)
                          </span>
                        ) : (
                          <span className="text-blue-600 dark:text-blue-400 font-medium italic text-[11.5px] bg-blue-50 dark:bg-blue-950/40 px-2 py-0.5 rounded border border-blue-200 dark:border-blue-900">
                            En cours
                          </span>
                        )}
                      </td>

                      {/* Km Parcourus */}
                      <td className="px-3 py-2.5 text-right font-mono tabular-nums whitespace-nowrap">
                        <div className="font-bold text-slate-800 dark:text-slate-100">
                          {Math.round(m.kilometrage_total || m.kilometrage || 0)} km
                        </div>
                        {(m.km_vide !== undefined || m.km_charge !== undefined) && (
                          <div className="text-[10.5px] text-slate-400">
                            {Math.round(m.km_vide || 0)}v · {Math.round(m.km_charge || 0)}ch
                          </div>
                        )}
                      </td>

                      {/* Infractions */}
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        {(m.nb_infractions || 0) > 0 ? (
                          <span className="inline-flex items-center justify-center px-2 py-0.5 rounded-full text-[11px] font-bold bg-red-100 dark:bg-red-950/60 text-red-600 dark:text-red-400 border border-red-300 dark:border-red-800">
                            {m.nb_infractions}
                          </span>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </td>

                      {/* Actions rapides contextuelles */}
                      <td className="px-3.5 py-2.5 text-center whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-center gap-1.5">
                          {aBesoinOt && (
                            <button
                              type="button"
                              onClick={() => setCibleSaisieOt(m)}
                              title="Saisir les informations d'OT reçues"
                              className="px-2 py-1 text-[11px] font-bold rounded bg-sky-600 hover:bg-sky-700 text-white shadow-xs"
                            >
                              + OT
                            </button>
                          )}

                          {m.statut === "EN_COURS" && (statutCamion === "VIDE" || aChargementEnAttente) && (
                            <button
                              type="button"
                              onClick={() => actionValiderChargementRapide(m)}
                              title="Valider le chargement GRT"
                              className="px-2 py-1 text-[11px] font-bold rounded bg-amber-600 hover:bg-amber-700 text-white shadow-xs"
                            >
                              ✓ Chargé
                            </button>
                          )}

                          {m.statut === "EN_COURS" && statutCamion === "CHARGE" && (
                            <>
                              <button
                                type="button"
                                onClick={() => actionValiderDechargementRapide(m)}
                                title="Valider le déchargement au dépôt récepteur"
                                className="px-2 py-1 text-[11px] font-bold rounded bg-emerald-600 hover:bg-emerald-700 text-white shadow-xs"
                              >
                                ✓ Livré
                              </button>
                              <button
                                type="button"
                                onClick={() => setCibleInvalidation(m)}
                                title="Invalider le déchargement (échantillonnage, repos parking…)"
                                className="px-1.5 py-1 text-[11px] font-bold rounded bg-slate-200 hover:bg-slate-300 dark:bg-slate-700 text-slate-700 dark:text-slate-200"
                              >
                                ✗
                              </button>
                            </>
                          )}

                          {m.statut === "EN_COURS" && (
                            <button
                              type="button"
                              onClick={() => setCibleDeviation(m)}
                              title="Déclarer une déviation d'itinéraire vers un autre dépôt"
                              className="px-2 py-1 text-[11px] font-medium rounded bg-purple-100 hover:bg-purple-200 dark:bg-purple-950 dark:hover:bg-purple-900 text-purple-700 dark:text-purple-300 border border-purple-300 dark:border-purple-800"
                            >
                              ⇄ Déviation
                            </button>
                          )}

                          <button
                            type="button"
                            onClick={() => setMissionSelectionnee(m)}
                            className="px-2 py-1 text-[11px] font-medium rounded bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300"
                          >
                            Détails
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Modal / Volet Coulissant de Détails d'une Mission */}
      {missionSelectionnee && (
        <ModalDetailMission
          mission={missionSelectionnee}
          onFermer={() => setMissionSelectionnee(null)}
          onMissionModifiee={() => {
            chargerDonnees();
            chargerAlertes();
            setMissionSelectionnee(null);
          }}
          onOuvrirDeviation={() => {
            const m = missionSelectionnee;
            setMissionSelectionnee(null);
            setCibleDeviation(m);
          }}
          onOuvrirInvalidation={() => {
            const m = missionSelectionnee;
            setMissionSelectionnee(null);
            setCibleInvalidation(m);
          }}
        />
      )}

      {/* Modal Création de Mission / Attribution d'OT (Option A) */}
      {modalNouvelleMissionOuverte && (
        <ModalNouvelleMission
          vehicules={vehicules}
          conducteurs={conducteurs}
          onFermer={() => setModalNouvelleMissionOuverte(false)}
          onSucces={() => {
            chargerDonnees();
            chargerAlertes();
            setModalNouvelleMissionOuverte(false);
          }}
        />
      )}

      {/* Modal Saisie Rapide OT pour Mission ou Alerte */}
      {cibleSaisieOt && (
        <ModalSaisieOT
          cible={cibleSaisieOt}
          onFermer={() => setCibleSaisieOt(null)}
          onSucces={() => {
            chargerDonnees();
            chargerAlertes();
            setCibleSaisieOt(null);
          }}
        />
      )}

      {/* Modal Invalidation Déchargement avec Motifs Prédéfinis (Règle 5) */}
      {cibleInvalidation && (
        <ModalInvalidation
          cible={cibleInvalidation}
          onFermer={() => setCibleInvalidation(null)}
          onSucces={() => {
            chargerDonnees();
            chargerAlertes();
            setCibleInvalidation(null);
          }}
        />
      )}

      {/* Modal Déclaration de Déviation (Règle 6) */}
      {cibleDeviation && (
        <ModalDeviation
          cible={cibleDeviation}
          onFermer={() => setCibleDeviation(null)}
          onSucces={() => {
            chargerDonnees();
            chargerAlertes();
            setCibleDeviation(null);
          }}
        />
      )}
    </div>
  );
}

// =============================================================================
// MODAL DÉTAILS D'UNE MISSION AVEC FRISE CHRONOLOGIQUE ET ACTION DE CLÔTURE
// =============================================================================
function ModalDetailMission({
  mission,
  onFermer,
  onMissionModifiee,
  onOuvrirDeviation,
  onOuvrirInvalidation,
}: {
  mission: Mission;
  onFermer: () => void;
  onMissionModifiee: () => void;
  onOuvrirDeviation: () => void;
  onOuvrirInvalidation: () => void;
}) {
  const [enAction, setEnAction] = useState(false);

  async function cloreMissionManuellement() {
    if (!confirm(`Confirmez-vous la validation du déchargement et la clôture de la mission ${mission.code_mission || mission.numero_ot} ? Le camion repassera au statut LIBRE.`)) {
      return;
    }
    setEnAction(true);
    try {
      await api("/api/missions/action-rapide", {
        method: "POST",
        body: JSON.stringify({
          action: "VALIDER_DECHARGEMENT",
          mission_id: mission.id,
          plaque: mission.plaque,
        }),
      });
      onMissionModifiee();
    } catch (e: any) {
      alert("Erreur lors de la clôture : " + (e?.message || e));
    } finally {
      setEnAction(false);
    }
  }

  async function validerChargementDirect() {
    setEnAction(true);
    try {
      await api("/api/missions/action-rapide", {
        method: "POST",
        body: JSON.stringify({
          action: "VALIDER_CHARGEMENT",
          mission_id: mission.id,
          plaque: mission.plaque,
        }),
      });
      onMissionModifiee();
    } catch (e: any) {
      alert("Erreur validation chargement : " + (e?.message || e));
    } finally {
      setEnAction(false);
    }
  }

  return (
    <Modal
      ouvert={true}
      onFermer={onFermer}
      large={true}
      titre={`Détail Mission ${mission.code_mission || `MIS-OT-${mission.numero_ot || ''}`}`}
    >
      <div className="space-y-5">
        {/* En-tête de synthèse */}
        <div className="flex flex-wrap items-center justify-between gap-3 p-3.5 rounded-xl bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-base font-bold text-slate-800 dark:text-slate-100">{mission.plaque}</span>
              <span className="text-slate-400">·</span>
              <span className="font-semibold text-slate-700 dark:text-slate-200">
                {mission.conducteur?.prenom_usuel || mission.conducteur?.nom_prenom || "Chauffeur non renseigné"}
              </span>
            </div>
            <div className="text-[12px] text-slate-400 mt-0.5">
              N° OT : <b className="text-slate-600 dark:text-slate-300">{mission.numero_ot || "Sans OT (LIBRE)"}</b> · Produit :{" "}
              <b>{mission.produit || "—"}</b> · Distributeur : <b>{mission.distributeur || "—"}</b>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge couleur={COULEURS_STATUT_CAMION[mission.statut_camion_actuel || "LIBRE"]}>
              Camion {mission.statut_camion_actuel || "LIBRE"}
            </Badge>
            <Badge couleur={COULEURS_STATUT_MISSION[mission.statut]}>{mission.statut}</Badge>
          </div>
        </div>

        {/* Métriques Clés */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[13px]">
          <div className="p-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
            <div className="text-[11px] text-slate-400 uppercase">Dépôt Prévu</div>
            <div className="font-bold mt-1 text-slate-700 dark:text-slate-200">{mission.depot_prevu || mission.depot || "—"}</div>
          </div>
          <div className="p-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
            <div className="text-[11px] text-slate-400 uppercase">Destination Réelle</div>
            <div className={cls("font-bold mt-1", mission.est_deviee ? "text-purple-600 dark:text-purple-400" : "text-slate-700 dark:text-slate-200")}>
              {mission.depot_effectif || mission.depot_prevu || "—"}
              {mission.est_deviee && " (DÉVIÉE)"}
            </div>
          </div>
          <div className="p-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
            <div className="text-[11px] text-slate-400 uppercase">Km Parcourus</div>
            <div className="font-bold mt-1 text-slate-700 dark:text-slate-200 tabular-nums">
              {Math.round(mission.kilometrage_total || mission.kilometrage || 0)} km
              <span className="text-[11px] text-slate-400 font-normal block">
                ({Math.round(mission.km_vide || 0)} km vide · {Math.round(mission.km_charge || 0)} km chargé)
              </span>
            </div>
          </div>
          <div className="p-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
            <div className="text-[11px] text-slate-400 uppercase">Durée de Mission</div>
            <div className="font-bold mt-1 text-slate-700 dark:text-slate-200 tabular-nums">
              {mission.statut === "EN_COURS" ? `${fmtDuree(mission.duree_s)} (en cours)` : fmtDuree(mission.duree_s)}
            </div>
          </div>
        </div>

        {/* 3 Horodatages Clés de la Mission */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[13px]">
          <div className="p-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-900/60">
            <div className="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-blue-500" />
              1. Début Mission (Sortie Base)
            </div>
            <div className="font-mono font-bold mt-1.5 text-slate-800 dark:text-slate-100">
              {mission.heure_debut || mission.date_debut ? (
                fmtDateHeure(mission.heure_debut || mission.date_debut)
              ) : (
                <span className="text-amber-500 font-medium italic text-xs">En attente sortie base</span>
              )}
            </div>
          </div>

          <div className="p-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-900/60">
            <div className="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-amber-500" />
              2. Date Chargement (Sortie GRT)
            </div>
            <div className="font-mono font-bold mt-1.5 text-slate-800 dark:text-slate-100">
              {mission.heure_chargement || mission.date_chargement ? (
                fmtDateHeure(mission.heure_chargement || mission.date_chargement)
              ) : (
                <span className="text-slate-400 font-mono">—</span>
              )}
            </div>
          </div>

          <div className="p-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-900/60">
            <div className="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              3. Date Déchargement (Dépôt)
            </div>
            <div className="font-mono font-bold mt-1.5 text-slate-800 dark:text-slate-100">
              {mission.statut === "TERMINÉE" && (mission.heure_fin || mission.date_fin) ? (
                fmtDateHeure(mission.heure_fin || mission.date_fin)
              ) : (
                <span className="text-blue-500 font-medium italic text-xs">En cours de mission</span>
              )}
            </div>
          </div>
        </div>

        {/* Motif d'invalidation affiché si présent */}
        {mission.motif_invalidation && (
          <div className="p-3 rounded-lg border border-slate-300 dark:border-slate-700 bg-slate-100/80 dark:bg-slate-800/80 text-[12.5px]">
            <span className="font-bold text-slate-700 dark:text-slate-200">Motif d'invalidation précédent : </span>
            <span className="text-slate-600 dark:text-slate-300 italic">{mission.motif_invalidation}</span>
          </div>
        )}

        {/* Frise Chronologique des Jalons Détectés */}
        <div>
          <h4 className="text-[12px] font-bold uppercase tracking-wider text-slate-400 mb-3">
            Chronologie Logistique & Jalons Géographiques
          </h4>
          <div className="relative pl-6 space-y-4 before:absolute before:left-2 before:top-2 before:bottom-2 before:w-0.5 before:bg-slate-200 dark:before:bg-slate-700">
            {(mission.etapes || []).map((e, idx) => {
              const info = ETAPE_LABELS[e.etat] || { label: e.etat, couleur: "bg-blue-500" };
              return (
                <div key={idx} className="relative text-[13px]">
                  <span className={cls("absolute -left-[21px] top-1 h-3 w-3 rounded-full ring-4 ring-white dark:ring-slate-900", info.couleur)} />
                  <div className="font-semibold text-slate-800 dark:text-slate-100 flex items-center gap-2">
                    <span className="font-mono text-slate-500 text-[12px]">{fmtHeure(e.ts)}</span>
                    <span>{info.label}</span>
                  </div>
                  {e.lieu && <div className="text-[12px] text-slate-400 mt-0.5">{e.lieu}</div>}
                </div>
              );
            })}

            {mission.statut === "EN_COURS" && (
              <div className="relative text-[13px] text-blue-500 font-medium">
                <span className="absolute -left-[21px] top-1 h-3 w-3 rounded-full bg-blue-500 animate-ping ring-4 ring-white dark:ring-slate-900" />
                <span>Mission active en cours de réalisation…</span>
              </div>
            )}
          </div>
        </div>

        {/* Actions Opérateur */}
        {mission.statut !== "TERMINÉE" && (
          <div className="pt-4 border-t border-slate-200 dark:border-slate-800 flex flex-wrap items-center justify-between gap-3">
            <div className="text-[12px] text-slate-400">
              Arrêt prolongé (&ge; 3h) au dépôt récepteur clôture automatiquement la mission.
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Btn variante="secondaire" onClick={onOuvrirDeviation} disabled={enAction}>
                <Icon nom="recherche" className="w-3.5 h-3.5 text-purple-500" />
                <span>Déclarer Déviation</span>
              </Btn>
              {mission.statut_camion_actuel !== "CHARGE" && (
                <Btn variante="secondaire" onClick={validerChargementDirect} disabled={enAction} className="border-amber-400 text-amber-700 dark:text-amber-300">
                  <span>Valider Chargement GRT</span>
                </Btn>
              )}
              {mission.statut_camion_actuel === "CHARGE" && (
                <Btn variante="secondaire" onClick={onOuvrirInvalidation} disabled={enAction}>
                  <span>Invalider Déchargement</span>
                </Btn>
              )}
              <Btn variante="primaire" onClick={cloreMissionManuellement} disabled={enAction} className="bg-emerald-600 hover:bg-emerald-700 text-white font-medium">
                <Icon nom="verifier" className="w-4 h-4" />
                <span>Valider Déchargement & Clôturer (LIBRE)</span>
              </Btn>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

// =============================================================================
// MODAL ATTRIBUTION OT & CRÉATION DE MISSION (OPTION A)
// =============================================================================
function ModalNouvelleMission({
  vehicules,
  conducteurs,
  onFermer,
  onSucces,
}: {
  vehicules: Vehicule[];
  conducteurs: Conducteur[];
  onFermer: () => void;
  onSucces: () => void;
}) {
  const [vehiculeId, setVehiculeId] = useState(vehicules[0]?.id || "");
  const [conducteurId, setConducteurId] = useState(conducteurs[0]?.id || "");
  const [numeroOt, setNumeroOt] = useState("");
  const [distributeur, setDistributeur] = useState(DISTRIBUTEURS_LISTE[0]);
  const [produit, setProduit] = useState(PRODUITS_LISTE[0]);
  const [depotPrevu, setDepotPrevu] = useState(DEPOTS_LISTE[0].code);
  const [envoi, setEnvoi] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!vehiculeId) return alert("Veuillez sélectionner un véhicule.");
    if (!numeroOt.trim()) return alert("Veuillez saisir le numéro de l'OT.");

    setEnvoi(true);
    try {
      await api("/api/missions", {
        method: "POST",
        body: JSON.stringify({
          vehicule_id: vehiculeId,
          conducteur_id: conducteurId || undefined,
          numero_ot: numeroOt.trim(),
          distributeur,
          produit,
          depot: depotPrevu,
          depot_prevu: depotPrevu,
        }),
      });
      onSucces();
    } catch (err: any) {
      alert("Erreur lors de la création de la mission : " + (err?.message || err));
    } finally {
      setEnvoi(false);
    }
  }

  return (
    <Modal ouvert={true} onFermer={onFermer} titre="Attribuer un OT / Initialiser Mission">
      <form onSubmit={handleSubmit} className="space-y-4 text-[13px]">
        <div className="p-3 bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 rounded-lg text-[12px] text-blue-800 dark:text-blue-300">
          ⓘ L'attribution préalable de l'OT bascule immédiatement le camion au statut <b>VIDE</b> et initialise le corridor Toamasina / GRT.
        </div>

        <Champ label="Véhicule (Citerne)">
          <select
            value={vehiculeId}
            onChange={(e) => setVehiculeId(e.target.value)}
            className={inputCls}
            required
          >
            {vehicules.map((v) => (
              <option key={v.id} value={v.id}>
                {v.plaque} {v.modele ? `(${v.modele})` : ""}
              </option>
            ))}
          </select>
        </Champ>

        <Champ label="Conducteur Affecté (facultatif)">
          <select
            value={conducteurId}
            onChange={(e) => setConducteurId(e.target.value)}
            className={inputCls}
          >
            <option value="">-- Aucun / Chauffeur habituel --</option>
            {conducteurs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.prenom_usuel || c.nom_prenom} {c.matricule ? `[${c.matricule}]` : ""}
              </option>
            ))}
          </select>
        </Champ>

        <Champ label="Numéro Ordre de Transport (OT)">
          <input
            type="text"
            value={numeroOt}
            onChange={(e) => setNumeroOt(e.target.value)}
            placeholder="Ex: OT-98745"
            className={inputCls}
            required
          />
        </Champ>

        <div className="grid grid-cols-2 gap-3">
          <Champ label="Distributeur">
            <select
              value={distributeur}
              onChange={(e) => setDistributeur(e.target.value)}
              className={inputCls}
            >
              {DISTRIBUTEURS_LISTE.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </Champ>

          <Champ label="Nature du Produit">
            <select
              value={produit}
              onChange={(e) => setProduit(e.target.value)}
              className={inputCls}
            >
              {PRODUITS_LISTE.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Champ>
        </div>

        <Champ label="Dépôt Récepteur Prévu">
          <select
            value={depotPrevu}
            onChange={(e) => setDepotPrevu(e.target.value)}
            className={inputCls}
          >
            {DEPOTS_LISTE.map((d) => (
              <option key={d.code} value={d.code}>
                {d.label}
              </option>
            ))}
          </select>
        </Champ>

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
          <Btn variante="fantome" onClick={onFermer}>
            Annuler
          </Btn>
          <Btn type="submit" variante="primaire" disabled={envoi}>
            {envoi ? <Spinner /> : <span>Valider et Démarrer Mission</span>}
          </Btn>
        </div>
      </form>
    </Modal>
  );
}

// =============================================================================
// MODAL SAISIE OT RAPIDE SUR MISSION EN COURS OU ALERTE
// =============================================================================
function ModalSaisieOT({
  cible,
  onFermer,
  onSucces,
}: {
  cible: CibleActionMission;
  onFermer: () => void;
  onSucces: () => void;
}) {
  const [numeroOt, setNumeroOt] = useState(cible.numero_ot || "");
  const [distributeur, setDistributeur] = useState(cible.distributeur || DISTRIBUTEURS_LISTE[0]);
  const [produit, setProduit] = useState(cible.produit || PRODUITS_LISTE[0]);
  const [depotPrevu, setDepotPrevu] = useState(cible.depot_prevu || DEPOTS_LISTE[0].code);
  const [envoi, setEnvoi] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!numeroOt.trim()) return alert("Veuillez saisir le numéro de l'OT.");

    setEnvoi(true);
    try {
      await api("/api/missions/action-rapide", {
        method: "POST",
        body: JSON.stringify({
          action: "SAISIR_OT",
          plaque: cible.plaque,
          mission_id: cible.mission_id || cible.id,
          alerte_id: cible.alerte_id,
          numero_ot: numeroOt.trim(),
          distributeur,
          produit,
          nouveau_depot: depotPrevu,
        }),
      });
      onSucces();
    } catch (err: any) {
      alert("Erreur lors de la mise à jour de l'OT : " + (err?.message || err));
    } finally {
      setEnvoi(false);
    }
  }

  return (
    <Modal ouvert={true} onFermer={onFermer} titre={`Saisir OT — ${cible.plaque}`}>
      <form onSubmit={handleSubmit} className="space-y-4 text-[13px]">
        <div className="p-3 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded-lg text-[12px] text-amber-800 dark:text-amber-300">
          ⚠️ Le camion est actuellement en statut <b>LIBRE</b>. La saisie des informations de l'OT basculera automatiquement son statut en <b>VIDE</b> et résoudra l'alerte.
        </div>

        <Champ label="Numéro Ordre de Transport (OT)">
          <input
            type="text"
            value={numeroOt}
            onChange={(e) => setNumeroOt(e.target.value)}
            placeholder="Ex: OT-99410"
            className={inputCls}
            required
            autoFocus
          />
        </Champ>

        <div className="grid grid-cols-2 gap-3">
          <Champ label="Distributeur">
            <select
              value={distributeur}
              onChange={(e) => setDistributeur(e.target.value)}
              className={inputCls}
            >
              {DISTRIBUTEURS_LISTE.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </Champ>

          <Champ label="Nature du Produit">
            <select
              value={produit}
              onChange={(e) => setProduit(e.target.value)}
              className={inputCls}
            >
              {PRODUITS_LISTE.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Champ>
        </div>

        <Champ label="Dépôt Récepteur Prévu">
          <select
            value={depotPrevu}
            onChange={(e) => setDepotPrevu(e.target.value)}
            className={inputCls}
          >
            {DEPOTS_LISTE.map((d) => (
              <option key={d.code} value={d.code}>
                {d.label}
              </option>
            ))}
          </select>
        </Champ>

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
          <Btn variante="fantome" onClick={onFermer}>
            Annuler
          </Btn>
          <Btn type="submit" variante="primaire" disabled={envoi}>
            {envoi ? <Spinner /> : <span>Enregistrer OT (Statut ➔ VIDE)</span>}
          </Btn>
        </div>
      </form>
    </Modal>
  );
}

// =============================================================================
// MODAL INVALIDATION DE DÉCHARGEMENT AVEC MOTIFS PRÉDÉFINIS (RÈGLE 5)
// =============================================================================
function ModalInvalidation({
  cible,
  onFermer,
  onSucces,
}: {
  cible: CibleActionMission;
  onFermer: () => void;
  onSucces: () => void;
}) {
  const [motif, setMotif] = useState(MOTIFS_INVALIDATION[0]);
  const [commentaire, setCommentaire] = useState("");
  const [envoi, setEnvoi] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setEnvoi(true);
    try {
      await api("/api/missions/action-rapide", {
        method: "POST",
        body: JSON.stringify({
          action: "INVALIDER_DECHARGEMENT",
          mission_id: cible.mission_id || cible.id,
          alerte_id: cible.alerte_id,
          plaque: cible.plaque,
          motif,
          commentaire: commentaire.trim() || undefined,
        }),
      });
      onSucces();
    } catch (err: any) {
      alert("Erreur lors de l'invalidation : " + (err?.message || err));
    } finally {
      setEnvoi(false);
    }
  }

  return (
    <Modal ouvert={true} onFermer={onFermer} titre={`Invalider Déchargement — ${cible.plaque}`}>
      <form onSubmit={handleSubmit} className="space-y-4 text-[13px]">
        <div className="p-3 bg-slate-100 dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 rounded-lg text-[12px] text-slate-700 dark:text-slate-300">
          ⓘ L'invalidation du déchargement maintient la mission <b>EN COURS</b> et conserve le statut du camion à <b>CHARGÉ</b> (ex: simple passage pour échantillon, repos de nuit).
        </div>

        <Champ label="Motif d'invalidation">
          <select
            value={motif}
            onChange={(e) => setMotif(e.target.value)}
            className={inputCls}
            required
          >
            {MOTIFS_INVALIDATION.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </Champ>

        <Champ label="Remarque / Commentaire complémentaire (facultatif)">
          <textarea
            value={commentaire}
            onChange={(e) => setCommentaire(e.target.value)}
            placeholder="Détails complémentaires sur la situation..."
            rows={3}
            className={inputCls}
          />
        </Champ>

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
          <Btn variante="fantome" onClick={onFermer}>
            Annuler
          </Btn>
          <Btn type="submit" variante="primaire" disabled={envoi} className="bg-slate-700 hover:bg-slate-800 text-white">
            {envoi ? <Spinner /> : <span>Confirmer Invalidation (Reste CHARGÉ)</span>}
          </Btn>
        </div>
      </form>
    </Modal>
  );
}

// =============================================================================
// MODAL DÉCLARATION DE DÉVIATION DE DÉPÔT (RÈGLE 6)
// =============================================================================
function ModalDeviation({
  cible,
  onFermer,
  onSucces,
}: {
  cible: CibleActionMission;
  onFermer: () => void;
  onSucces: () => void;
}) {
  const [nouveauDepot, setNouveauDepot] = useState(cible.depot_effectif || DEPOTS_LISTE[0].label);
  const [motif, setMotif] = useState("");
  const [envoi, setEnvoi] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setEnvoi(true);
    try {
      await api("/api/missions/action-rapide", {
        method: "POST",
        body: JSON.stringify({
          action: "DECLARER_DEVIATION",
          mission_id: cible.mission_id || cible.id,
          alerte_id: cible.alerte_id,
          plaque: cible.plaque,
          nouveau_depot: nouveauDepot,
          motif: motif.trim() || undefined,
        }),
      });
      onSucces();
    } catch (err: any) {
      alert("Erreur lors de la déclaration de déviation : " + (err?.message || err));
    } finally {
      setEnvoi(false);
    }
  }

  return (
    <Modal ouvert={true} onFermer={onFermer} titre={`Déclarer Déviation — ${cible.plaque}`}>
      <form onSubmit={handleSubmit} className="space-y-4 text-[13px]">
        <div className="p-3 bg-purple-50 dark:bg-purple-950/40 border border-purple-200 dark:border-purple-800 rounded-lg text-[12px] text-purple-800 dark:text-purple-300">
          ⓘ Dépôt initialement prévu : <b>{cible.depot_prevu || cible.depot || "Non renseigné"}</b>. La déclaration d'une déviation basculera le statut de la mission en <b>DÉVIÉE</b>.
        </div>

        <Champ label="Nouveau Dépôt Récepteur">
          <select
            value={nouveauDepot}
            onChange={(e) => setNouveauDepot(e.target.value)}
            className={inputCls}
            required
          >
            {DEPOTS_LISTE.map((d) => (
              <option key={d.code} value={d.label}>
                {d.label}
              </option>
            ))}
          </select>
        </Champ>

        <Champ label="Motif ou instruction du distributeur">
          <input
            type="text"
            value={motif}
            onChange={(e) => setMotif(e.target.value)}
            placeholder="Ex: Réorientation demandée par Total suite à rupture de stock..."
            className={inputCls}
          />
        </Champ>

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-800">
          <Btn variante="fantome" onClick={onFermer}>
            Annuler
          </Btn>
          <Btn type="submit" variante="primaire" disabled={envoi} className="bg-purple-600 hover:bg-purple-700 text-white">
            {envoi ? <Spinner /> : <span>Confirmer Déviation</span>}
          </Btn>
        </div>
      </form>
    </Modal>
  );
}
