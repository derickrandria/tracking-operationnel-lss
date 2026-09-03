/** Module 6 — Historique.
 *
 * ADDENDUM v1.1 §4 : sous-onglets internes
 *   · Historique Suivi Journalier — plage de dates Du/Au + raccourcis rapides,
 *     KPI recalculés sur la période, URL partageable (?du=&au=), vue de synthèse
 *     + GRILLE IDENTIQUE à l'onglet Suivi Journalier dès qu'une journée est
 *     sélectionnée (parties A/B/C/D, 27 colonnes trajets — exigence audit),
 *     exports multi-jours au format strict du Suivi Journalier.
 *   · Historique Infractions / Alertes — pages existantes, inchangées (§4.2).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, download } from "../api";
import Icon from "../components/icons";
import GrilleSuivi from "../components/GrilleSuivi";
import { Badge, Btn, Card, Spinner, Vide } from "../components/ui";
import { addToast } from "../components/toast";
import Infractions from "./Infractions";
import Alertes from "./Alertes";
import { cls, fmtDateFr, fmtDuree, fmtHeure, todayISO } from "../utils";

const CLE_MODE = "lss_suivi_mode_detail";

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** Raccourcis rapides du sélecteur de plage (§4.3). */
function presets() {
  const maintenant = new Date();
  const auj = iso(maintenant);
  const j31 = new Date(maintenant); j31.setDate(j31.getDate() - 30);
  const j7 = new Date(maintenant); j7.setDate(j7.getDate() - 6);
  const debutMois = new Date(maintenant.getFullYear(), maintenant.getMonth(), 1);
  const debutMoisPrec = new Date(maintenant.getFullYear(), maintenant.getMonth() - 1, 1);
  const finMoisPrec = new Date(maintenant.getFullYear(), maintenant.getMonth(), 0);
  return [
    { id: "31j", label: "31 derniers jours (Défaut)", du: iso(j31), au: auj },
    { id: "7j", label: "7 derniers jours", du: iso(j7), au: auj },
    { id: "jour", label: "Aujourd'hui", du: auj, au: auj },
    { id: "mois", label: "Ce mois-ci", du: iso(debutMois), au: auj },
    { id: "mois_prec", label: "Mois dernier", du: iso(debutMoisPrec), au: iso(finMoisPrec) },
  ];
}

type Onglet = "suivi" | "infractions" | "alertes";

function extraireConducteursRelaisHist(h: any): Array<{ nom: string; duree_s: number }> {
  const trajets = h.trajets || h.donnees?.trajets || [];
  if (!trajets || trajets.length === 0) return [];
  const mapRelais = new Map<string, number>();
  const nomPrincipal = (h.conducteur?.nom_prenom || "").toLowerCase().trim();
  const prenomPrincipal = (h.conducteur?.prenom_usuel || "").toLowerCase().trim();

  for (const t of trajets) {
    const badge = (t.conducteur_badge || "").trim();
    if (!badge) continue;
    const badgeNorm = badge.toLowerCase();
    if (nomPrincipal && (badgeNorm === nomPrincipal || nomPrincipal.includes(badgeNorm))) {
      continue;
    }
    if (prenomPrincipal && (badgeNorm === prenomPrincipal || prenomPrincipal.includes(badgeNorm))) {
      continue;
    }
    let sec = 0;
    if (t.heure_debut && t.heure_fin) {
      const d1 = new Date(t.heure_debut).getTime();
      const d2 = new Date(t.heure_fin).getTime();
      if (d2 > d1) sec = Math.round((d2 - d1) / 1000);
    }
    mapRelais.set(badge, (mapRelais.get(badge) || 0) + sec);
  }
  return Array.from(mapRelais.entries()).map(([nom, duree_s]) => ({ nom, duree_s }));
}

export default function Historique() {
  const [onglet, setOnglet] = useState<Onglet>("suivi");
  const [params, setParams] = useSearchParams();
  const P = useMemo(presets, []);
  const [du, setDu] = useState(params.get("du") || P[0].du);   // défaut : 31 jours glissants (P[0])
  const [au, setAu] = useState(params.get("au") || P[0].au);
  const [q, setQ] = useState("");
  const [data, setData] = useState<any | null>(null);
  const [stats, setStats] = useState<any | null>(null);

  // ------- vue « grille identique au Suivi Journalier » pour une journée -------
  const [jourGrille, setJourGrille] = useState("");
  const [nonce, setNonce] = useState(0);   // ré-affichage forcé de la même journée
  const [grille, setGrille] = useState<any[] | null>(null);
  const [modeDetail, setModeDetail] = useState(localStorage.getItem(CLE_MODE) !== "0");
  const refGrille = useRef<HTMLDivElement>(null);

  const raccourciActif = P.find((p) => p.du === du && p.au === au)?.id || "custom";

  // §4.3 — validation : « Au » jamais antérieur à « Du » + URL partageable
  function changerPlage(nDu: string, nAu: string) {
    if (nDu && nAu && nAu < nDu) {
      addToast({ type: "erreur", titre: "Plage invalide",
        message: "La date « Au » ne peut pas être antérieure à « Du »." });
      return;
    }
    setDu(nDu); setAu(nAu);
    setParams({ du: nDu, au: nAu }, { replace: true });
  }

  useEffect(() => {
    if (onglet !== "suivi" || !du || !au || au < du) return;
    setData(null);
    api(`/api/historique/suivi?du=${du}&au=${au}&q=${encodeURIComponent(q)}`).then(setData);
    api(`/api/historique/stats?du=${du}&au=${au}`).then(setStats);
  }, [onglet, du, au, q]);

  /** Charge la grille complète (snapshot archivé, trajets compris) de la journée. */
  useEffect(() => {
    if (!jourGrille) { setGrille(null); return; }
    setGrille(null);
    api(`/api/historique/suivi?du=${jourGrille}&au=${jourGrille}&detail=1`)
      .then((d) => {
        setGrille(d.items.map((h: any) => ({ ...(h.donnees || {}), id: h.id })));
        window.setTimeout(() => refGrille.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
      })
      .catch(() => setGrille([]));
  }, [jourGrille, nonce]);

  /** §3.3 — export de LA journée affichée : fichier strictement identique à
   * l'export de l'onglet Suivi Journalier (produit depuis l'archive). */
  async function exporterJour(fmt: "xlsx" | "pdf") {
    if (!jourGrille) return;
    const p = new URLSearchParams({ date: jourGrille, mode: modeDetail ? "detail" : "compact" });
    try {
      await download(`/api/historique/suivi/export-jour.${fmt}?${p}`,
        `Suivi_Journalier_${jourGrille}.${fmt}`);
      addToast({ type: "succes", titre: `Export ${fmt.toUpperCase()} téléchargé`,
        message: `Journée du ${jourGrille.split("-").reverse().join("/")} — ${grille?.length ?? 0} camions (${modeDetail ? "détaillé" : "compact"}).` });
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Export impossible", message: e.message });
    }
  }

  /** §4.4 — export multi-jours au format strict du Suivi Journalier. */
  async function exporter(fmt: "xlsx" | "pdf") {
    const p = new URLSearchParams({ du, au, mode: "detail" });
    if (q.trim()) p.set("q", q.trim());
    try {
      await download(`/api/historique/suivi/export.${fmt}?${p}`,
        `Historique_Suivi_Journalier_${du}_au_${au}.${fmt}`);
      addToast({ type: "succes", titre: `Export ${fmt.toUpperCase()} téléchargé`,
        message: fmt === "xlsx"
          ? "Une feuille par jour (JJ-MM-AAAA) + feuille « Synthèse »."
          : "Page de garde récapitulative + une section par jour." });
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Export impossible", message: e.message });
    }
  }

  // TTJ moyenne calculée sur la période (complément des stats serveur)
  const ttjMoyen = useMemo(() => {
    const vals = (data?.items || []).map((h: any) => h.ttj_s).filter(Boolean);
    return vals.length ? Math.round(vals.reduce((a: number, b: number) => a + b, 0) / vals.length) : 0;
  }, [data]);

  const inputDate = "rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]";

  return (
    <div className="flex flex-col gap-4">{/* §0octies decies L1 — mise au point v1.42 : page MULTI-SECTIONS → défilement vertical DE PAGE ; chaque tableau garde sa zone de défilement interne bornée (barre horizontale toujours visible) */}
      {/* ------- sous-onglets internes (§4.2) ------- */}
      <div className="flex items-center gap-1.5 border-b border-slate-200 dark:border-slate-800">
        {([
          { id: "suivi", label: "Historique Suivi Journalier", icone: "suivi" },
          { id: "infractions", label: "Historique Infractions", icone: "infractions" },
          { id: "alertes", label: "Historique Alertes", icone: "alertes" },
        ] as const).map((o) => (
          <button key={o.id} onClick={() => setOnglet(o.id)}
            className={cls("flex items-center gap-2 -mb-px border-b-2 px-4 py-2.5 text-[13px] font-semibold transition-colors",
              onglet === o.id
                ? "border-blue-500 text-blue-600 dark:text-blue-400"
                : "border-transparent text-slate-400 hover:text-slate-600 dark:hover:text-slate-300")}>
            <Icon nom={o.icone} className="h-4 w-4" /> {o.label}
          </button>
        ))}
      </div>

      {onglet === "infractions" && <Infractions />}
      {onglet === "alertes" && <Alertes />}

      {onglet === "suivi" && (
        <>
          {/* ------- sélecteur de plage de dates (§4.3) ------- */}
          <Card className="!p-3 bg-slate-50/50 dark:bg-slate-900/40" titre="">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2.5">
                {/* Sélecteurs Début & Fin distincts et labellisés */}
                <div className="flex items-center gap-2 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-2.5 py-1 shadow-sm">
                  <Icon nom="calendrier" className="h-4 w-4 text-blue-500" />
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">Date début :</span>
                    <input
                      type="date"
                      value={du}
                      max={au || todayISO()}
                      className="bg-transparent text-[13px] font-medium text-slate-800 dark:text-slate-100 outline-none cursor-pointer"
                      onChange={(e) => changerPlage(e.target.value, au)}
                    />
                  </div>
                  <span className="text-slate-300 dark:text-slate-600 font-bold px-0.5">➔</span>
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">Date fin :</span>
                    <input
                      type="date"
                      value={au}
                      min={du}
                      max={todayISO()}
                      className="bg-transparent text-[13px] font-medium text-slate-800 dark:text-slate-100 outline-none cursor-pointer"
                      onChange={(e) => changerPlage(du, e.target.value)}
                    />
                  </div>
                </div>

                <div className="mx-0.5 h-6 w-px bg-slate-200 dark:bg-slate-700 hidden sm:block" />

                {/* Raccourcis rapides */}
                <div className="flex flex-wrap items-center gap-1">
                  {P.map((p) => (
                    <button
                      key={p.id}
                      onClick={() => changerPlage(p.du, p.au)}
                      className={cls(
                        "rounded-md border px-2.5 py-1 text-[12px] font-medium transition-colors",
                        raccourciActif === p.id
                          ? "border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400 font-semibold"
                          : "border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:border-slate-400"
                      )}
                    >
                      {p.label}
                    </button>
                  ))}
                  {raccourciActif === "custom" && (
                    <span className="rounded-md border border-violet-500 bg-violet-500/10 px-2.5 py-1 text-[12px] font-semibold text-violet-500">
                      Plage personnalisée
                    </span>
                  )}
                </div>
              </div>

              {/* Recherche et exports */}
              <div className="flex items-center gap-2">
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Rechercher (chauffeur, plaque)…"
                  className="w-52 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-[13px] shadow-sm"
                />
                <Btn
                  variante="secondaire"
                  onClick={() => exporter("xlsx")}
                  title="§4.4 — une feuille par jour (JJ-MM-AAAA) + feuille Synthèse, format identique au Suivi Journalier"
                >
                  <Icon nom="telecharger" /> Excel
                </Btn>
                <Btn
                  variante="secondaire"
                  onClick={() => exporter("pdf")}
                  title="§4.4 — page de garde + une section par jour, format identique au Suivi Journalier"
                >
                  <Icon nom="telecharger" /> PDF
                </Btn>
              </div>
            </div>
          </Card>

          {/* ------- sélection directe d'une journée → grille identique ------- */}
          <Card className="!p-3"
            titre={<span className="flex items-center gap-2 text-[13px] font-semibold">
              <Icon nom="tableau" className="h-4 w-4 text-emerald-500" />
              Voir une journée — grille identique à l'onglet Suivi Journalier
            </span>}>
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="text-slate-400">Journée du</span>
              <input type="date" value={jourGrille} max={todayISO()} className={inputDate}
                onChange={(e) => setJourGrille(e.target.value)} />
              <Btn variante="primaire" disabled={!jourGrille} onClick={() => setNonce((n) => n + 1)}>
                Afficher la grille
              </Btn>
              <span className="text-[12px] text-slate-400">
                …ou cliquez sur une ligne du tableau de synthèse ci-dessous. Lecture seule : la journée est archivée.
              </span>
            </div>
          </Card>

          {/* ------- GRILLE IDENTIQUE à l'onglet Suivi Journalier (lecture seule) ------- */}
          {jourGrille && (
            <Card className="!p-0 overflow-hidden"
              titre={<div ref={refGrille} className="flex items-center gap-2">
                <span>Grille du <b className="capitalize">{fmtDateFr(jourGrille)}</b></span>
                <Badge couleur="bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40">
                  structure identique · lecture seule
                </Badge>
              </div>}
              actions={<div className="flex items-center gap-2">
                <Btn variante={modeDetail ? "primaire" : "secondaire"}
                  title="27 colonnes trajets (exigence audit) : Heure de départ, Fin T1, Pause 1, Début/Fin/Pause T2→T9"
                  onClick={() => { const v = !modeDetail; setModeDetail(v); localStorage.setItem(CLE_MODE, v ? "1" : "0"); }}>
                  <Icon nom="tableau" /> {modeDetail ? "Détaillé" : "Compact"}
                </Btn>
                <Btn variante="secondaire" onClick={() => exporterJour("xlsx")} disabled={!grille}
                  title="Export Excel de la journée affichée — fichier identique à l'export de l'onglet Suivi Journalier">
                  <Icon nom="telecharger" /> Excel
                </Btn>
                <Btn variante="secondaire" onClick={() => exporterJour("pdf")} disabled={!grille}
                  title="Export PDF de la journée affichée — paysage A3 en mode détaillé">
                  <Icon nom="telecharger" /> PDF
                </Btn>
                <Btn variante="fantome" onClick={() => setJourGrille("")}><Icon nom="fermer" /> Fermer</Btn>
              </div>}>
              {grille === null ? (
                <div className="flex h-32 items-center justify-center gap-2 text-slate-400"><Spinner /> Chargement de la journée…</div>
              ) : grille.length === 0 ? (
                <Vide texte="Cette journée n'est pas encore archivée (l'archivage a lieu chaque nuit à minuit) ou ne contient aucun véhicule." />
              ) : (
                <div className="max-h-[72vh] overflow-auto">{/* v1.42 — barres internes verticale + horizontale */}
                  <GrilleSuivi lignes={grille} modeDetail={modeDetail}
                    refs={null} lectureSeule masquerTCC />{/* §0vicies decies N1 — Historique : TCC toujours « 0:00 » */}
                </div>
              )}
            </Card>
          )}

          {/* ------- KPI recalculés sur la période (§4.3) ------- */}
          {stats && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-7">
              {[
                { l: "Jours archivés", v: stats.nb_jours_archives },
                { l: "Camions suivis", v: stats.nb_lignes },
                { l: "Km parcourus", v: `${Math.round(stats.km_total)} km` },
                { l: "TCJ moyenne", v: fmtDuree(stats.tcj_moyen_s) },
                { l: "TTJ moyenne", v: fmtDuree(ttjMoyen) },
                { l: "Pause moyenne", v: fmtDuree(stats.pause_moyenne_s) },
                { l: "Infractions", v: stats.infractions_total, rouge: stats.infractions_total > 0 },
              ].map((k) => (
                <div key={k.l} className={cls("rounded-xl border p-3 bg-white dark:bg-nuit-900",
                  k.rouge ? "border-red-500/50" : "border-slate-200 dark:border-slate-800")}>
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{k.l}</div>
                  <div className="mt-1 text-xl font-extrabold tabular-nums">{k.v}</div>
                </div>
              ))}
            </div>
          )}

          {/* ------- vue de synthèse (§4.2) : cliquer une journée ouvre la grille ------- */}
          <Card className="!p-0 overflow-hidden" contenuClasse="!p-0"
            titre={data ? `${data.total} journées camion — du ${du.split("-").reverse().join("/")} au ${au.split("-").reverse().join("/")}` : "Chargement…"}>
            {!data ? (
              <div className="flex h-40 items-center justify-center gap-2 text-slate-400"><Spinner /></div>
            ) : data.items.length === 0 ? (
              <Vide texte="Aucune archive sur cette période (les journées sont archivées automatiquement à minuit)." />
            ) : (
              <div className="max-h-[60vh] overflow-auto">{/* v1.42 — défilement borné des deux sens, barres toujours visibles */}
                <table className="table-pro">
                  <thead><tr>
                    <th>Date</th><th>Plaque</th><th>Conducteur</th><th>Situation de clôture</th>
                    <th>Statut</th><th>Dépôt</th><th>Produit</th><th>Départ</th><th>TCJ</th><th>TTJ</th>
                    <th>Trajets</th><th>Km</th><th>Infractions</th><th>Alertes</th>
                  </tr></thead>
                  <tbody>
                    {data.items.map((h: any) => {
                      const relais = extraireConducteursRelaisHist(h);
                      const nomPrincipal = h.conducteur?.prenom_usuel || h.conducteur?.nom_prenom;
                      return (
                      <tr key={h.id} className="cursor-pointer"
                        title="Voir la grille complète de cette journée (identique au Suivi Journalier)"
                        onClick={() => setJourGrille(h.date_jour)}>
                        <td className="whitespace-nowrap font-medium">{h.date_jour.split("-").reverse().join("/")}</td>
                        <td className="font-bold whitespace-nowrap">{h.plaque}</td>
                        <td className="whitespace-nowrap">
                          <div className="flex flex-col py-0.5">
                            {nomPrincipal ? (
                              <span className="font-bold text-slate-900 dark:text-slate-100">
                                {nomPrincipal}
                              </span>
                            ) : (
                              <span className="text-slate-400">—</span>
                            )}
                            {relais.map((r) => (
                              <span key={r.nom} className="text-[11px] font-normal text-slate-600 dark:text-slate-400">
                                {r.nom}{r.duree_s > 0 ? ` (${fmtDuree(r.duree_s)})` : ""}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="max-w-[220px] truncate text-[12px]">{h.situation || "—"}</td>
                        <td><Badge>{h.statut_camion || "—"}</Badge></td>
                        <td>{h.depot_recepteur || "—"}</td>
                        <td>{h.produit || "—"}</td>
                        <td className="tabular-nums">{fmtHeure(h.heure_depart)}</td>
                        <td className="tabular-nums">{fmtDuree(h.tcj_s)}</td>
                        <td className="tabular-nums">{fmtDuree(h.ttj_s)}</td>
                        <td className="tabular-nums">
                          {h.nb_trajets || "—"}
                          {h.nb_trajets > 9 && (
                            <span title="Trajets 10+ stockés (alerte « nombre exceptionnel »)"
                              className="ml-1 rounded bg-sky-500/15 px-1 text-[10px] font-bold text-sky-500">+{h.nb_trajets - 9}</span>
                          )}
                        </td>
                        <td className="tabular-nums">{h.km_parcourus || "—"}</td>
                        <td>
                          <Badge couleur={h.nb_infractions > 0 ? "bg-red-500/15 text-red-500 border-red-500/40" : undefined}>
                            {h.nb_infractions}
                          </Badge>
                        </td>
                        <td><Badge>{h.nb_alertes}</Badge></td>
                      </tr>
                    );})}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
