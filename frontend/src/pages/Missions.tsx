/**
 * Module 3 — Missions : Reconstitution & Suivi Automatique des Cycles Logistiques Pétroliers (§6.3 / v2026.1).
 * Gestion des statuts LIBRE ➔ VIDE ➔ CHARGÉ ➔ LIBRE, Détection GRT & Déchargements,
 * Déviations d'itinéraires et Synchronisation Inter-onglets.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import Icon from "../components/icons";
import { Badge, Btn, Card, Champ, inputCls, Modal, PageHeader, Spinner, Vide } from "../components/ui";
import { Conducteur, Mission, Vehicule } from "../types";
import {
  cls,
  COULEURS_STATUT_CAMION,
  COULEURS_STATUT_MISSION,
  daysAgoISO,
  firstDayOfMonthISO,
  fmtDateFr,
  fmtDuree,
  fmtHeure,
  todayISO
} from "../utils";
import { on } from "../ws";

const ETAPE_LABELS: Record<string, { label: string; couleur: string }> = {
  INITIALISATION_OT: { label: "Attribution OT & Départ à vide (VIDE)", couleur: "bg-sky-500" },
  TRANSIT_CHARGEMENT: { label: "Transit vers dépôt chargeur (VIDE)", couleur: "bg-sky-500" },
  ENTREE_GRT: { label: "Arrivée Zone GRT Tamatave", couleur: "bg-indigo-500" },
  CHARGEMENT_EFFECTUE: { label: "Chargement effectué (CHARGÉ)", couleur: "bg-amber-500" },
  CHARGEMENT_TERMINE: { label: "Chargement validé (CHARGÉ)", couleur: "bg-amber-500" },
  TRANSIT_LIVRAISON: { label: "Transit vers dépôt récepteur (CHARGÉ)", couleur: "bg-amber-500" },
  ARRIVEE_DEPOT_RECEPTEUR: { label: "Arrivée au dépôt récepteur", couleur: "bg-blue-600" },
  DEVIATION_DETECTEE: { label: "Déviation d'itinéraire détectée", couleur: "bg-purple-600" },
  DECHARGEMENT_EFFECTUE: { label: "Déchargement confirmé (LIBRE)", couleur: "bg-emerald-500" },
  DECHARGEMENT_TERMINE: { label: "Déchargement terminé (LIBRE)", couleur: "bg-emerald-500" },
  RETOUR_BASE: { label: "Retour Base Tana (LIBRE)", couleur: "bg-slate-500" },
};

const DEPOTS_LISTE = [
  { code: "DMMG", label: "Moramanga (DMMG)" },
  { code: "DABI", label: "Alarobia / Ambohibao (DABI)" },
  { code: "DSNR", label: "Soanierana (DSNR)" },
  { code: "DABE", label: "Antsirabe (DABE)" },
  { code: "DFIA", label: "Fianarantsoa (DFIA)" },
  { code: "DMDV", label: "Morondava (DMDV)" },
  { code: "DMKR", label: "Manakara (DMKR)" },
];

const DISTRIBUTEURS_LISTE = ["TOTAL", "GALANA", "VIVO", "JOVENA"];
const PRODUITS_LISTE = ["Gasoil (GO)", "Super (SP95)", "Pétrole Lampant (PL)"];

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
  const [stats, setStats] = useState<StatsMissions | null>(null);
  const [chargement, setChargement] = useState(true);

  const [missionSelectionnee, setMissionSelectionnee] = useState<Mission | null>(null);
  const [modalNouvelleMissionOuverte, setModalNouvelleMissionOuverte] = useState(false);
  const [vehicules, setVehicules] = useState<Vehicule[]>([]);
  const [conducteurs, setConducteurs] = useState<Conducteur[]>([]);

  const timer = useRef<number>();

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

      const res = await api(`/api/missions?${params.toString()}`);
      setMissions(res.missions || []);
      setStats(res.stats || null);
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

  useEffect(() => {
    chargerDonnees();
  }, [dateDebut, dateFin, statutFiltre, depotFiltre, distributeurFiltre, recherche]);

  useEffect(() => {
    chargerReferentiels();
  }, []);

  useEffect(() => {
    const rafraichir = () => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(chargerDonnees, 2000);
    };
    const offs = ["mission.new", "mission.update", "suivi.update", "referentiels.changed", "data.refresh"].map((t) => on(t, rafraichir));
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
              <option value="RETARDÉE">Retardée</option>
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
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Horodatages (Départ ➔ Fin)</th>
                  <th className="px-3 py-2.5 text-right whitespace-nowrap">Km Parcouru</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Infractions</th>
                  <th className="px-3.5 py-2.5 text-center whitespace-nowrap">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {missions.map((m) => {
                  const estDeviee = m.statut === "DÉVIÉE" || m.est_deviee;
                  const statutCamion = m.statut_camion_actuel || (m.statut === "TERMINÉE" ? "LIBRE" : "VIDE");
                  const couleurCamion = COULEURS_STATUT_CAMION[statutCamion] || "bg-slate-500/10 text-slate-600";
                  const couleurMission = COULEURS_STATUT_MISSION[m.statut] || "bg-blue-500/10 text-blue-600";

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
                        {m.numero_ot || "—"}
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

                      {/* Horodatages */}
                      <td className="px-3 py-2.5 text-center font-mono text-[11.5px] whitespace-nowrap text-slate-600 dark:text-slate-400">
                        {fmtHeure(m.heure_debut)} ➔ {m.heure_chargement ? fmtHeure(m.heure_chargement) : "…"} ➔{" "}
                        {m.heure_fin ? fmtHeure(m.heure_fin) : "…"}
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

                      {/* Action */}
                      <td className="px-3.5 py-2.5 text-center whitespace-nowrap">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setMissionSelectionnee(m);
                          }}
                          className="px-2 py-1 text-[11px] font-medium rounded bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300"
                        >
                          Détails
                        </button>
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
            setMissionSelectionnee(null);
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
            setModalNouvelleMissionOuverte(false);
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
}: {
  mission: Mission;
  onFermer: () => void;
  onMissionModifiee: () => void;
}) {
  const [enAction, setEnAction] = useState(false);
  const [nouvelleDestination, setNouvelleDestination] = useState(mission.depot_effectif || mission.depot_prevu || "");

  async function cloreMissionManuellement() {
    if (!confirm(`Confirmez-vous la validation du déchargement et la clôture de la mission ${mission.code_mission || mission.numero_ot} ? Le camion repassera au statut LIBRE.`)) {
      return;
    }
    setEnAction(true);
    try {
      await api(`/api/missions/${mission.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          statut: "TERMINÉE",
          statut_camion_actuel: "LIBRE",
          depot_effectif: nouvelleDestination || mission.depot_prevu,
        }),
      });
      onMissionModifiee();
    } catch (e: any) {
      alert("Erreur lors de la clôture : " + (e?.message || e));
    } finally {
      setEnAction(false);
    }
  }

  async function declarerDeviation() {
    const dest = prompt("Veuillez saisir le nouveau dépôt / nouvelle destination :", nouvelleDestination);
    if (!dest || dest.trim() === "") return;
    setEnAction(true);
    try {
      await api(`/api/missions/${mission.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          statut: "DÉVIÉE",
          est_deviee: true,
          depot_effectif: dest.trim(),
          motif_deviation: `Déviation manuelle vers ${dest.trim()}`,
        }),
      });
      onMissionModifiee();
    } catch (e: any) {
      alert("Erreur lors de la mise à jour : " + (e?.message || e));
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
              N° OT : <b className="text-slate-600 dark:text-slate-300">{mission.numero_ot || "—"}</b> · Produit :{" "}
              <b>{mission.produit || "—"}</b> · Distributeur : <b>{mission.distributeur || "—"}</b>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge couleur={COULEURS_STATUT_CAMION[mission.statut_camion_actuel || "VIDE"]}>
              Camion {mission.statut_camion_actuel || "VIDE"}
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
              {mission.statut === "EN_COURS" ? "En cours…" : fmtDuree(mission.duree_s)}
            </div>
          </div>
        </div>

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
            <div className="flex items-center gap-2">
              <Btn variante="fantome" onClick={declarerDeviation} disabled={enAction}>
                <span>Signaler une déviation</span>
              </Btn>
              <Btn variante="primaire" onClick={cloreMissionManuellement} disabled={enAction} className="bg-emerald-600 hover:bg-emerald-700 text-white">
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
  const [conducteurId, setConducteurId] = useState("");
  const [numeroOt, setNumeroOt] = useState("");
  const [distributeur, setDistributeur] = useState(DISTRIBUTEURS_LISTE[0]);
  const [produit, setProduit] = useState(PRODUITS_LISTE[0]);
  const [depotPrevu, setDepotPrevu] = useState(DEPOTS_LISTE[0].code);
  const [envoi, setEnvoi] = useState(false);

  // Mise à jour automatique du chauffeur titulaire du véhicule
  useEffect(() => {
    const v = vehicules.find((x) => x.id === vehiculeId);
    if (v?.conducteur_actuel_id) {
      setConducteurId(v.conducteur_actuel_id);
    }
  }, [vehiculeId, vehicules]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!vehiculeId) return alert("Veuillez sélectionner un véhicule.");

    setEnvoi(true);
    try {
      await api("/api/missions", {
        method: "POST",
        body: JSON.stringify({
          vehicule_id: vehiculeId,
          conducteur_id: conducteurId || undefined,
          numero_ot: numeroOt.trim() || undefined,
          distributeur,
          produit,
          depot_prevu: depotPrevu,
          date_jour: todayISO(),
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
    <Modal ouvert={true} onFermer={onFermer} titre="Assigner un Ordre de Transport (OT) / Nouvelle Mission">
      <form onSubmit={handleSubmit} className="space-y-4 text-[13px]">
        <Champ label="Véhicule (Camion)">
          <select
            value={vehiculeId}
            onChange={(e) => setVehiculeId(e.target.value)}
            className={inputCls}
            required
          >
            {vehicules.map((v) => (
              <option key={v.id} value={v.id}>
                {v.plaque} {v.description ? `— ${v.description}` : ""}
              </option>
            ))}
          </select>
        </Champ>

        <Champ label="Chauffeur affecté">
          <select
            value={conducteurId}
            onChange={(e) => setConducteurId(e.target.value)}
            className={inputCls}
          >
            <option value="">-- Chauffeur titulaire ou détection auto --</option>
            {conducteurs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.nom_prenom} ({c.prenom_usuel}) {c.matricule ? `[${c.matricule}]` : ""}
              </option>
            ))}
          </select>
        </Champ>

        <div className="grid grid-cols-2 gap-3">
          <Champ label="Numéro Ordre de Transport (OT)">
            <input
              type="text"
              value={numeroOt}
              onChange={(e) => setNumeroOt(e.target.value)}
              placeholder="Ex: OT-44821"
              className={inputCls}
              required
            />
          </Champ>

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
        </div>

        <div className="grid grid-cols-2 gap-3">
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
        </div>

        <div className="p-3 bg-sky-50 dark:bg-sky-950/40 border border-sky-200 dark:border-sky-800 rounded-lg text-[12px] text-sky-800 dark:text-sky-300">
          ⓘ L'attribution de l'OT génère un <b>Mission_ID</b> unique et bascule automatiquement le camion au statut <b>VIDE</b> (transit vers chargement à Tamatave).
        </div>

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
