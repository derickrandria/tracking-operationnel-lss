/** Module « Temps de conduite » — Compteur TCH (Temps de Conduite Hebdomadaire)
 * et matrice journalière par chauffeur.
 *
 * Structure de la grille :
 *   - Entêtes figées (2 lignes) : Ligne 1 (Dates) + Ligne 2 (Sous-colonnes TCJ / TTJ)
 *   - Colonnes fixes à gauche : Chauffeur (Nom et prénom complet) | Cumul TCH | TCH restant
 *   - Colonnes défilantes à droite : paires TCJ / TTJ par date.
 *   - Barre de défilement horizontale toujours visible à l'écran.
 *
 * Règles réglementaires :
 *   - TCH = somme des TCJ du chauffeur depuis le dernier reset (repos continu ≥ 24h)
 *     dans la limite du cycle hebdomadaire (max 56h00).
 *   - TCH restant = 56h00 - TCH cumulé.
 *   - Alerte avertissement ≥ 46h00 / Alerte limite ≥ 56h00.
 *   - Réconciliation canonique des noms (MZoneX / CamtrackPro / Fiches locales).
 *   - Mise à jour temps réel WebSocket sans rechargement.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, download } from "../api";
import Icon from "../components/icons";
import { addToast } from "../components/toast";
import { Badge, Btn, PageHeader, Spinner, Vide } from "../components/ui";
import { ConducteurTCH, SyntheseTCH } from "../types";
import { cls, fmtDuree, todayISO } from "../utils";
import { on } from "../ws";

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function presets() {
  const maintenant = new Date();
  const auj = iso(maintenant);

  // Début de semaine (Lundi)
  const dSem = new Date(maintenant);
  const jourSemaine = dSem.getDay(); // 0 = Dimanche, 1 = Lundi, ...
  const decSem = jourSemaine === 0 ? 6 : jourSemaine - 1;
  dSem.setDate(dSem.getDate() - decSem);

  const j7 = new Date(maintenant);
  j7.setDate(j7.getDate() - 6);

  const j14 = new Date(maintenant);
  j14.setDate(j14.getDate() - 13);

  const debutMois = new Date(maintenant.getFullYear(), maintenant.getMonth(), 1);

  return [
    { id: "semaine", label: "Cette semaine", du: iso(dSem), au: auj },
    { id: "7j", label: "7 derniers jours", du: iso(j7), au: auj },
    { id: "14j", label: "14 derniers jours", du: iso(j14), au: auj },
    { id: "mois", label: "Ce mois-ci", du: iso(debutMois), au: auj },
  ];
}

const COULEURS_STATUT: Record<string, string> = {
  ACTIF: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40",
  SUSPENDU: "bg-red-500/15 text-red-500 border-red-500/40",
  "CONGÉ": "bg-amber-500/15 text-amber-500 border-amber-500/40",
  INACTIF: "bg-slate-500/15 text-slate-500 border-slate-500/40",
};

export default function TempsConduite() {
  const [params, setParams] = useSearchParams();
  const P = useMemo(presets, []);
  const [du, setDu] = useState(params.get("du") || P[1].du); // Défaut : 7 derniers jours
  const [au, setAu] = useState(params.get("au") || P[1].au);
  const [q, setQ] = useState(params.get("q") || "");
  const [filtreStatut, setFiltreStatut] = useState(params.get("statut") || "");
  const [filtreAlerte, setFiltreAlerte] = useState(params.get("alerte") || "");
  const [data, setData] = useState<SyntheseTCH | null>(null);
  const [chargement, setChargement] = useState(true);

  const raccourciActif = P.find((p) => p.du === du && p.au === au)?.id || "custom";

  function changerPlage(nDu: string, nAu: string) {
    if (nDu && nAu && nAu < nDu) {
      addToast({
        type: "erreur",
        titre: "Plage invalide",
        message: "La date « Au » ne peut pas être antérieure à « Du ».",
      });
      return;
    }
    setDu(nDu);
    setAu(nAu);
    const p = new URLSearchParams(params);
    p.set("du", nDu);
    p.set("au", nAu);
    setParams(p, { replace: true });
  }

  const charger = useCallback(async (silencieux = false) => {
    if (!silencieux) setChargement(true);
    try {
      const p = new URLSearchParams();
      if (du) p.set("du", du);
      if (au) p.set("au", au);
      if (q.trim()) p.set("q", q.trim());
      if (filtreStatut) p.set("statut", filtreStatut);
      if (filtreAlerte) p.set("alerte", filtreAlerte);

      const d = await api(`/api/temps-conduite?${p}`);
      setData(d);
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Erreur de chargement", message: e.message });
    } finally {
      setChargement(false);
    }
  }, [du, au, q, filtreStatut, filtreAlerte]);

  useEffect(() => {
    charger();
  }, [charger]);

  // Écoute temps réel WebSocket
  useEffect(() => {
    // 1. Mise à jour en direct lors d'un trajet en cours
    const off1 = on("suivi.update", (payload) => {
      if (!payload?.suivi) return;
      const suivi = payload.suivi;
      const cId = suivi.conducteur_id;
      if (!cId) return;

      const auj = todayISO();
      setData((prev) => {
        if (!prev) return prev;
        const index = prev.lignes.findIndex((l) => l.conducteur_id === cId);
        if (index === -1) return prev;

        const lignes = [...prev.lignes];
        const ligne = { ...lignes[index] };
        const hist = { ...ligne.historique };

        // Ancien TCJ d'aujourd'hui pour ce chauffeur
        const ancTcj = hist[auj]?.tcj_s || 0;
        const nouvTcj = suivi.tcj_s || 0;
        const nouvTtj = suivi.ttj_s || 0;
        const diffTcj = nouvTcj - ancTcj;

        // Mise à jour de la journée d'aujourd'hui
        hist[auj] = {
          tcj_s: nouvTcj,
          ttj_s: nouvTtj,
          vehicules: Array.from(new Set([...(hist[auj]?.vehicules || []), suivi.plaque].filter(Boolean))),
          inclus_dans_tch: true,
          en_cours: true,
        };

        // Recalcul du TCH cumulé et restant
        const nouvCumul = Math.max(0, ligne.tch_cumul_s + diffTcj);
        const nouvRestant = 201600 - nouvCumul;

        let alerteStatut: "NORMAL" | "PROCHE_LIMITE" | "LIMITE_ATTEINTE" = "NORMAL";
        if (nouvCumul >= 201600) alerteStatut = "LIMITE_ATTEINTE";
        else if (nouvCumul >= 165600) alerteStatut = "PROCHE_LIMITE";

        ligne.tch_cumul_s = nouvCumul;
        ligne.tch_restant_s = nouvRestant;
        ligne.alerte_statut = alerteStatut;
        ligne.historique = hist;

        lignes[index] = ligne;
        return { ...prev, lignes };
      });
    });

    // 2. Alertes nouvelles
    const off2 = on("alerte.new", () => {
      charger(true);
    });

    // 3. Changement référentiels / bascule minuit
    const off3 = on("referentiels.changed", () => charger(true));
    const off4 = on("jour.change", () => charger(true));

    return () => {
      off1();
      off2();
      off3();
      off4();
    };
  }, [charger]);

  // Exports
  async function exporter(fmt: "xlsx" | "pdf") {
    const p = new URLSearchParams();
    if (du) p.set("du", du);
    if (au) p.set("au", au);
    if (q.trim()) p.set("q", q.trim());
    if (filtreStatut) p.set("statut", filtreStatut);
    if (filtreAlerte) p.set("alerte", filtreAlerte);

    try {
      await download(
        `/api/temps-conduite/export.${fmt}?${p}`,
        `Temps_Conduite_${du}_au_${au}.${fmt}`
      );
      addToast({
        type: "succes",
        titre: `Export ${fmt.toUpperCase()} téléchargé`,
        message: `Synthèse des temps de conduite du ${du.split("-").reverse().join("/")} au ${au.split("-").reverse().join("/")}.`,
      });
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Export impossible", message: e.message });
    }
  }

  const lignes = data?.lignes || [];
  const dates = data?.dates || [];
  const stats = data?.stats;

  return (
    <div className="flex h-full flex-col min-h-0 gap-2.5">
      <PageHeader
        titre="Temps de conduite (TCH)"
        sousTitre={
          <>
            Compteur hebdomadaire réglementaire (max <b>56h00</b> · avertissement à <b>46h00</b>) · Reset après <b>24h</b> de repos continu
          </>
        }
        actions={
          <>
            <input
              type="date"
              value={du}
              max={todayISO()}
              className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-[13px]"
              onChange={(e) => changerPlage(e.target.value, au)}
            />
            <span className="text-slate-400 text-[12px]">au</span>
            <input
              type="date"
              value={au}
              max={todayISO()}
              className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-[13px]"
              onChange={(e) => changerPlage(du, e.target.value)}
            />

            <Btn variante="secondaire" onClick={() => exporter("xlsx")} title="Export Excel de la matrice affichée">
              <Icon nom="telecharger" /> Excel
            </Btn>
            <Btn variante="secondaire" onClick={() => exporter("pdf")} title="Export PDF paysage">
              <Icon nom="telecharger" /> PDF
            </Btn>

            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Rechercher chauffeur, matricule…"
              className="w-52 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-[13px]"
            />
            <select
              value={filtreAlerte}
              onChange={(e) => setFiltreAlerte(e.target.value)}
              className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]"
            >
              <option value="">Toutes alertes TCH</option>
              <option value="PROCHE_LIMITE">Proche limite (≥ 46h)</option>
              <option value="LIMITE_ATTEINTE">Limite atteinte (≥ 56h)</option>
              <option value="NORMAL">Normal (&lt; 46h)</option>
            </select>
            <select
              value={filtreStatut}
              onChange={(e) => setFiltreStatut(e.target.value)}
              className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]"
            >
              <option value="">Tous statuts</option>
              {["ACTIF", "SUSPENDU", "CONGÉ", "INACTIF"].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </>
        }
      />

      {/* Raccourcis de dates & Légende */}
      <div className="flex flex-wrap items-center justify-between gap-2 shrink-0">
        <div className="flex flex-wrap items-center gap-2">
          <Icon nom="calendrier" className="h-4 w-4 text-slate-400" />
          <span className="text-[12px] font-semibold text-slate-400">Période :</span>
          {P.map((p) => (
            <button
              key={p.id}
              onClick={() => changerPlage(p.du, p.au)}
              className={cls(
                "rounded-full border px-3 py-0.5 text-[12px] font-medium transition-colors",
                raccourciActif === p.id
                  ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400"
                  : "border-slate-200 dark:border-slate-700 text-slate-400 hover:border-slate-400"
              )}
            >
              {p.label}
            </button>
          ))}
          {raccourciActif === "custom" && (
            <span className="rounded-full border border-violet-500 bg-violet-500/10 px-3 py-0.5 text-[12px] font-medium text-violet-500">
              Personnalisé
            </span>
          )}
        </div>

        <div className="flex items-center gap-3 text-[11.5px] text-slate-500 dark:text-slate-400">
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" /> &lt; 46h00 (Normal)
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-amber-500" /> ≥ 46h00 (Proche limite)
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-red-500" /> ≥ 56h00 (Limite atteinte)
          </span>
        </div>
      </div>

      {/* Cartes KPI */}
      {stats && (
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-5 shrink-0">
          <div className="rounded-xl border border-slate-200 dark:border-slate-800 p-2.5 bg-white dark:bg-nuit-900">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Total Chauffeurs</div>
            <div className="mt-0.5 text-2xl font-extrabold tabular-nums">{stats.total_chauffeurs}</div>
          </div>
          <div className="rounded-xl border border-slate-200 dark:border-slate-800 p-2.5 bg-white dark:bg-nuit-900">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">En conduite aujourd'hui</div>
            <div className="mt-0.5 text-2xl font-extrabold tabular-nums text-blue-600 dark:text-blue-400">
              {stats.en_conduite_aujourdhui}
            </div>
          </div>
          <div
            className={cls(
              "rounded-xl border p-2.5 bg-white dark:bg-nuit-900",
              stats.proche_limite > 0 ? "border-amber-500/50 bg-amber-500/[0.03]" : "border-slate-200 dark:border-slate-800"
            )}
          >
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Proches limite (≥ 46h)</div>
            <div className={cls("mt-0.5 text-2xl font-extrabold tabular-nums", stats.proche_limite > 0 ? "text-amber-500" : "")}>
              {stats.proche_limite}
            </div>
          </div>
          <div
            className={cls(
              "rounded-xl border p-2.5 bg-white dark:bg-nuit-900",
              stats.limite_atteinte > 0 ? "border-red-500/50 bg-red-500/[0.03]" : "border-slate-200 dark:border-slate-800"
            )}
          >
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Limite atteinte (≥ 56h)</div>
            <div className={cls("mt-0.5 text-2xl font-extrabold tabular-nums", stats.limite_atteinte > 0 ? "text-red-500" : "")}>
              {stats.limite_atteinte}
            </div>
          </div>
          <div className="rounded-xl border border-slate-200 dark:border-slate-800 p-2.5 bg-white dark:bg-nuit-900">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">TCH moyen actif</div>
            <div className="mt-0.5 text-2xl font-extrabold tabular-nums">{fmtDuree(stats.tch_moyen_s)}</div>
          </div>
        </div>
      )}

      {/* Grille principale :
          - En-têtes figées sur 2 lignes (top-0 et top-[30px])
          - Colonnes fixes à gauche (Chauffeur, Cumul TCH, TCH restant)
          - Barre de défilement horizontale toujours visible dans la vue
      */}
      <div className="flex-1 min-h-0 flex flex-col rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-nuit-900 shadow-sm overflow-hidden">
        <header className="flex items-center justify-between px-4 py-2 border-b border-slate-100 dark:border-slate-800 shrink-0 bg-slate-50/50 dark:bg-nuit-800/50">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm text-slate-700 dark:text-slate-200">
              Temps de conduite par chauffeur ({lignes.length})
            </span>
            <span className="text-[12px] text-slate-400">
              · Colonnes fixes à gauche · Défilement horizontal des journées TCJ / TTJ
            </span>
          </div>
        </header>

        {chargement && !data ? (
          <div className="flex flex-1 items-center justify-center gap-2 text-slate-400">
            <Spinner /> Chargement des temps de conduite…
          </div>
        ) : lignes.length === 0 ? (
          <div className="flex-1 flex items-center justify-center">
            <Vide texte="Aucun chauffeur ne correspond aux critères." />
          </div>
        ) : (
          <div className="flex-1 min-h-0 overflow-auto">
            <table className="table-pro w-full border-separate border-spacing-0">
              <thead>
                {/* Ligne 1 des entêtes : Colonnes fixes (rowSpan=2) + Blocs de date (colSpan=2) */}
                <tr>
                  <th
                    rowSpan={2}
                    className="sticky left-0 top-0 z-40 min-w-[260px] max-w-[260px] w-[260px] bg-slate-100 dark:bg-nuit-800 border-r border-b border-slate-200 dark:border-slate-700 text-left px-3 py-1.5"
                  >
                    Chauffeur (Nom et prénom)
                  </th>
                  <th
                    rowSpan={2}
                    className="sticky left-[260px] top-0 z-40 min-w-[105px] max-w-[105px] w-[105px] bg-slate-100 dark:bg-nuit-800 border-r border-b border-slate-200 dark:border-slate-700 text-center px-2 py-1.5"
                  >
                    Cumul TCH
                  </th>
                  <th
                    rowSpan={2}
                    className="sticky left-[365px] top-0 z-40 min-w-[105px] max-w-[105px] w-[105px] bg-slate-100 dark:bg-nuit-800 border-r-2 border-b border-slate-300 dark:border-slate-600 shadow-[2px_0_5px_-2px_rgba(0,0,0,0.15)] text-center px-2 py-1.5"
                  >
                    TCH restant
                  </th>

                  {dates.map((d_str) => {
                    const isToday = d_str === todayISO();
                    return (
                      <th
                        key={d_str}
                        colSpan={2}
                        className={cls(
                          "sticky top-0 z-30 text-center border-r border-b border-slate-200 dark:border-slate-700 px-2 py-1 text-[11.5px] h-[30px]",
                          isToday
                            ? "bg-blue-600/15 text-blue-600 dark:text-blue-400 font-extrabold"
                            : "bg-slate-100 dark:bg-nuit-800 font-bold text-slate-700 dark:text-slate-200"
                        )}
                      >
                        {d_str.split("-").reverse().slice(0, 2).join("/")}
                        {isToday && <span className="ml-1 text-[10px] uppercase font-normal">(Aujourd'hui)</span>}
                      </th>
                    );
                  })}
                </tr>

                {/* Ligne 2 des entêtes (Sous-colonnes TCJ / TTJ fixées sous la ligne 1) */}
                <tr>
                  {dates.map((d_str) => (
                    <React.Fragment key={d_str}>
                      <th
                        className="sticky top-[30px] z-30 min-w-[70px] w-[70px] h-[24px] bg-slate-50 dark:bg-nuit-800 border-r border-b border-slate-200 dark:border-slate-700 text-center py-0.5 text-[10.5px] font-bold text-slate-600 dark:text-slate-300"
                        title={`Temps de conduite journalière du ${d_str}`}
                      >
                        TCJ
                      </th>
                      <th
                        className="sticky top-[30px] z-30 min-w-[70px] w-[70px] h-[24px] bg-slate-50 dark:bg-nuit-800 border-r border-b border-slate-200 dark:border-slate-700 text-center py-0.5 text-[10.5px] font-bold text-slate-400 dark:text-slate-500"
                        title={`Temps de travail journalier du ${d_str}`}
                      >
                        TTJ
                      </th>
                    </React.Fragment>
                  ))}
                </tr>
              </thead>

              <tbody>
                {lignes.map((l) => {
                  const estCritique = l.alerte_statut === "LIMITE_ATTEINTE";
                  const estAvertissement = l.alerte_statut === "PROCHE_LIMITE";

                  return (
                    <tr
                      key={l.conducteur_id}
                      className={cls(
                        "hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors",
                        estCritique
                          ? "bg-red-500/[0.04] dark:bg-red-950/10"
                          : estAvertissement
                          ? "bg-amber-500/[0.04] dark:bg-amber-950/10"
                          : ""
                      )}
                    >
                      {/* Colonne 1 : Chauffeur (FIXE) — Nom et Prénom complet */}
                      <td className="sticky left-0 z-10 min-w-[260px] max-w-[260px] w-[260px] bg-white dark:bg-nuit-900 border-r border-b border-slate-100 dark:border-slate-800 px-3 py-2">
                        <div className="flex flex-col">
                          {/* Ligne 1 : Nom et Prénom complet */}
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-[13px] text-slate-900 dark:text-slate-100 truncate" title={l.nom_prenom}>
                              {l.nom_prenom}
                            </span>
                            {l.statut !== "ACTIF" && (
                              <Badge couleur={COULEURS_STATUT[l.statut]} className="!text-[9.5px] !px-1 shrink-0">
                                {l.statut}
                              </Badge>
                            )}
                          </div>
                          {/* Ligne 2 : Matricule + Prénom usuel + Camion(s) */}
                          <div className="flex items-center gap-1.5 text-[11px] text-slate-400 mt-0.5">
                            <span className="font-mono text-slate-500 font-semibold">{l.matricule}</span>
                            {l.prenom_usuel && l.prenom_usuel !== l.nom_prenom && (
                              <span className="text-slate-400 font-medium truncate">
                                · {l.prenom_usuel}
                              </span>
                            )}
                            {l.vehicules_actifs.length > 0 && (
                              <span className="truncate text-blue-600 dark:text-blue-400 font-medium" title={`Camion(s) assigné(s) : ${l.vehicules_actifs.join(", ")}`}>
                                · {l.vehicules_actifs.join(", ")}
                              </span>
                            )}
                          </div>
                        </div>
                      </td>

                      {/* Colonne 2 : Cumul TCH (FIXE) */}
                      <td className="sticky left-[260px] z-10 min-w-[105px] max-w-[105px] w-[105px] bg-white dark:bg-nuit-900 border-r border-b border-slate-100 dark:border-slate-800 text-center px-2 py-2">
                        <span
                          className={cls(
                            "inline-flex items-center justify-center rounded-lg px-2 py-0.5 font-mono text-[12.5px] font-extrabold tabular-nums",
                            estCritique
                              ? "bg-red-500/15 text-red-600 dark:text-red-400 border border-red-500/40"
                              : estAvertissement
                              ? "bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/40"
                              : l.tch_cumul_s > 0
                              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30"
                              : "text-slate-400"
                          )}
                        >
                          {fmtDuree(l.tch_cumul_s)}
                        </span>
                      </td>

                      {/* Colonne 3 : TCH restant (FIXE avec ombre séparatrice) */}
                      <td className="sticky left-[365px] z-10 min-w-[105px] max-w-[105px] w-[105px] bg-white dark:bg-nuit-900 border-r-2 border-b border-slate-300 dark:border-slate-600 shadow-[2px_0_5px_-2px_rgba(0,0,0,0.15)] text-center px-2 py-2">
                        <span
                          className={cls(
                            "font-mono text-[12.5px] font-bold tabular-nums",
                            l.tch_restant_s <= 0
                              ? "text-red-600 dark:text-red-400 font-extrabold"
                              : l.tch_restant_s <= 36000 // <= 10h
                              ? "text-amber-600 dark:text-amber-400"
                              : "text-slate-600 dark:text-slate-300"
                          )}
                        >
                          {fmtDuree(l.tch_restant_s)}
                        </span>
                      </td>

                      {/* Colonnes dynamiques : TCJ / TTJ par date */}
                      {dates.map((d_str) => {
                        const h = l.historique[d_str];
                        const tcj = h?.tcj_s || 0;
                        const ttj = h?.ttj_s || 0;
                        const isToday = d_str === todayISO();
                        const isEnCours = h?.en_cours;

                        return (
                          <React.Fragment key={d_str}>
                            {/* Cellule TCJ */}
                            <td
                              className={cls(
                                "border-r border-b border-slate-100 dark:border-slate-800 text-center px-1.5 py-2 font-mono text-[12px] tabular-nums",
                                tcj > 0
                                  ? h?.inclus_dans_tch
                                    ? isEnCours
                                      ? "text-blue-600 dark:text-blue-400 font-bold bg-blue-50/50 dark:bg-blue-950/20"
                                      : "text-slate-800 dark:text-slate-100 font-bold"
                                    : "text-slate-400 dark:text-slate-500" // Conduite avant le dernier reset
                                  : "text-slate-300 dark:text-slate-700"
                              )}
                              title={
                                tcj > 0
                                  ? `TCJ : ${fmtDuree(tcj)}${h?.vehicules?.length ? ` (${h.vehicules.join(", ")})` : ""}${
                                      !h?.inclus_dans_tch ? " — avant le dernier reset 24h" : ""
                                    }`
                                  : "Aucune conduite"
                              }
                            >
                              {tcj > 0 ? (
                                <span className="flex items-center justify-center gap-1">
                                  {isEnCours && <span className="h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" />}
                                  {fmtDuree(tcj)}
                                </span>
                              ) : (
                                "—"
                              )}
                            </td>

                            {/* Cellule TTJ */}
                            <td
                              className={cls(
                                "border-r border-b border-slate-100 dark:border-slate-800 text-center px-1.5 py-2 font-mono text-[11.5px] tabular-nums text-slate-400 dark:text-slate-500",
                                isEnCours && "bg-blue-50/30 dark:bg-blue-950/10"
                              )}
                              title={ttj > 0 ? `TTJ : ${fmtDuree(ttj)}` : "—"}
                            >
                              {ttj > 0 ? fmtDuree(ttj) : "—"}
                            </td>
                          </React.Fragment>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
