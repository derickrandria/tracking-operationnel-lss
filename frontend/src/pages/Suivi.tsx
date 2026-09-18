/** Module 2 — Suivi Journalier (cœur du système §6.2).
 *
 * Grille à 4 parties (composant partagé `GrilleSuivi`) :
 *   A — synchronisée (référentiels, jamais effacée)         : lecture seule
 *   B — métier (persistante, reportée chaque jour)          : saisie manuelle
 *   C — quotidienne (réinitialisée à minuit)                : saisie + pré-remplissage GPS
 *   D — calculée automatiquement (moteur §7)                : lecture seule, temps réel
 *
 * ADDENDUM v1.1 + exigence audit « 27 colonnes » (voir GrilleSuivi.tsx),
 * §2 autosave (debounce 1,5 s + synchro 60 s), §3 exports Excel/PDF.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, download, getUser } from "../api";
import Icon from "../components/icons";
import GrilleSuivi from "../components/GrilleSuivi";
import { Btn, PageHeader, Spinner } from "../components/ui";
import { addToast } from "../components/toast";
import { Referentiels, SuiviLigne } from "../types";
import { cls, fmtDateFr, todayISO } from "../utils";
import { on } from "../ws";

const CLE_BROUILLON = "lss_suivi_brouillon";  // §2.5 — résilience localStorage
const CLE_MODE = "lss_suivi_mode_detail";
const RETRY_DELAIS = [3000, 10000, 30000];    // §2.5 — backoff progressif

type PendingMap = Record<string, Record<string, string | null>>;
type EtatSave = { etat: "neutre" | "attente" | "en_cours" | "ok" | "erreur"; heure?: string; nb?: number };

export default function Suivi() {
  const [params, setParams] = useSearchParams();
  const [date, setDate] = useState(params.get("date") || todayISO());
  const [data, setData] = useState<{
    lignes: SuiviLigne[]; seuils: Record<string, number>;
    /** v1.48 — sources portails dont la dernière collecte a échoué. */
    sources_en_echec?: string[];
    /** v1.50 — parmi elles, celles dont la cause est LOCALE (base verrouillée). */
    sources_bloquees_localement?: string[];
  } | null>(null);
  const [refs, setRefs] = useState<Referentiels | null>(null);
  const [recherche, setRecherche] = useState(params.get("q") || "");
  const [filtreStatut, setFiltreStatut] = useState("");
  const [modeDetail, setModeDetail] = useState(localStorage.getItem(CLE_MODE) !== "0"); // détaillé par défaut (audit)
  const [chargement, setChargement] = useState(true);
  const user = getUser();
  const lectureSeule = user?.role === "CONSULTATION" || date !== todayISO();

  // ---------------- autosave (Addendum v1.1 §2) ----------------
  const [save, setSave] = useState<EtatSave>({ etat: "neutre" });
  const [pendingUI, setPendingUI] = useState<Record<string, boolean>>({});
  const pendingRef = useRef<PendingMap>({});
  const volRef = useRef(false);
  const essaisRef = useRef(0);
  const debounceRef = useRef<Record<string, number>>({});
  const timersRef = useRef<number[]>([]);
  const restaureRef = useRef(false);

  const nbEnAttente = () =>
    Object.values(pendingRef.current).reduce((n, c) => n + Object.keys(c).length, 0);

  const persisterBrouillon = useCallback(() => {
    localStorage.setItem(CLE_BROUILLON, JSON.stringify(pendingRef.current));
    setPendingUI(Object.fromEntries(Object.keys(pendingRef.current).map((k) => [k, true])));
  }, []);

  /** Envoie au backend toutes les modifications en attente (delta uniquement §2.2.2). */
  const vider = useCallback(async () => {
    if (volRef.current || lectureSeule) return;
    const mods = Object.entries(pendingRef.current)
      .map(([id, champs]) => ({ id, champs }))
      .filter((m) => Object.keys(m.champs).length > 0);
    if (!mods.length) return;
    volRef.current = true;
    setSave({ etat: "en_cours", nb: mods.length });
    try {
      const r = await api("/api/suivi", {
        method: "PATCH", body: JSON.stringify({ modifications: mods }),
      });
      // ne purge que les champs effectivement traités (pas les frappes arrivées en vol)
      mods.forEach((m) => {
        const courant = pendingRef.current[m.id];
        if (!courant) return;
        Object.entries(m.champs).forEach(([k, v]) => { if (courant[k] === v) delete courant[k]; });
        if (!Object.keys(courant).length) delete pendingRef.current[m.id];
      });
      persisterBrouillon();
      essaisRef.current = 0;
      setData((d) => d && ({
        ...d,
        lignes: d.lignes.map((l) => {
          const maj = (r.lignes || []).find((x: SuiviLigne) => x.id === l.id);
          return maj || l;
        }),
      }));
      const reste = nbEnAttente();
      setSave(reste
        ? { etat: "attente", nb: reste }
        : { etat: "ok", heure: r.serveur_heure || new Date().toLocaleTimeString("fr-FR") });
      if (reste) timersRef.current.push(window.setTimeout(() => vider(), 400));
    } catch {
      // §2.5 : retry automatique avec backoff progressif, brouillon conservé
      const delai = RETRY_DELAIS[Math.min(essaisRef.current, RETRY_DELAIS.length - 1)];
      essaisRef.current += 1;
      setSave({ etat: "erreur" });
      timersRef.current.push(window.setTimeout(() => vider(), delai));
    } finally {
      volRef.current = false;
    }
  }, [lectureSeule, persisterBrouillon]);

  /** Déclaré à chaque modification de cellule : optimiste + debounce 1,5 s (§2.2.1). */
  const programmer = useCallback((ligne: SuiviLigne, champ: string, valeur: string | null) => {
    if (lectureSeule) return;
    const isPassageLibre = champ === "statut_camion" && valeur === "LIBRE";
    setData((d) => d && ({
      ...d,
      lignes: d.lignes.map((l) => (l.id === ligne.id ? {
        ...l,
        [champ]: valeur,
        ...(isPassageLibre ? { numero_ot: null, distributeur: null, produit: null, depot_recepteur: null } : {})
      } : l)),
    }));
    pendingRef.current[ligne.id] = {
      ...pendingRef.current[ligne.id],
      [champ]: valeur,
      ...(isPassageLibre ? { numero_ot: null, distributeur: null, produit: null, depot_recepteur: null } : {})
    };
    persisterBrouillon();
    setSave({ etat: "attente", nb: nbEnAttente() });
    window.clearTimeout(debounceRef.current[ligne.id]);
    debounceRef.current[ligne.id] = window.setTimeout(() => vider(), 1500);
  }, [lectureSeule, persisterBrouillon, vider]);

  // sauvegarde périodique de sécurité : toutes les 60 secondes (§2.2.2)
  useEffect(() => {
    const minute = window.setInterval(() => vider(), 60000);
    return () => {
      window.clearInterval(minute);
      timersRef.current.forEach(window.clearTimeout);
      Object.values(debounceRef.current).forEach(window.clearTimeout);
    };
  }, [vider]);

  // -------------------------------------------------- chargement
  const charger = useCallback(async (silencieux = false) => {
    if (!silencieux) setChargement(true);
    try {
      const d = await api(`/api/suivi?date=${date}`);
      // §2.5 : ré-application du brouillon localStorage après un rafraîchissement
      if (!restaureRef.current && !lectureSeule) {
        restaureRef.current = true;
        try {
          const brouillon: PendingMap = JSON.parse(localStorage.getItem(CLE_BROUILLON) || "{}");
          const ids = new Set((d.lignes || []).map((l: SuiviLigne) => l.id));
          const recuperes = Object.entries(brouillon).filter(([id, c]) => ids.has(id) && Object.keys(c).length);
          if (recuperes.length) {
            pendingRef.current = Object.fromEntries(recuperes);
            persisterBrouillon();
            d.lignes = (d.lignes || []).map((l: SuiviLigne) =>
              pendingRef.current[l.id] ? { ...l, ...pendingRef.current[l.id] } : l);
            setSave({ etat: "attente", nb: nbEnAttente() });
            timersRef.current.push(window.setTimeout(() => vider(), 800));
            addToast({ type: "info", titre: "Brouillon récupéré",
              message: "Des modifications non enregistrées ont été restaurées et renvoyées au serveur." });
          } else {
            localStorage.removeItem(CLE_BROUILLON);
          }
        } catch { /* brouillon illisible : on l'ignore */ }
      }
      setData(d);
    } catch (e) {
      console.error("Erreur chargement suivi:", e);
      setData({ lignes: [], seuils: {} });
    } finally {
      setChargement(false);
    }
  }, [date, lectureSeule, persisterBrouillon, vider]);

  useEffect(() => {
    charger();
    api("/api/referentiels").then(setRefs).catch(() => {});
  }, [charger]);

  // Mises à jour temps réel de la Partie D (moteur de calcul)
  useEffect(() => {
    const off = on("suivi.update", (p) => {
      if (!p?.suivi) return;
      setData((d) => {
        if (!d) return d;
        const idx = d.lignes.findIndex((l) => l.id === p.suivi.id);
        if (idx === -1) return d;
        const lignes = d.lignes.slice();
        lignes[idx] = { ...lignes[idx], ...p.suivi };
        return { ...d, lignes };
      });
    });
    const off2 = on("data.refresh", () => charger(true));
    return () => { off(); off2(); };
  }, [charger]);

  const [syncing, setSyncing] = useState(false);

  async function syncGPS() {
    setSyncing(true);
    try {
      const r = await api("/api/suivi/sync-gps", { method: "POST" });
      const totalPoints = (r.mzonex_n1_points || 0) + (r.camtrackpro_n1_points || 0);
      const recusN2 = r.n2_trajets?.recus || 0;
      addToast({
        type: "succes",
        titre: "Synchronisation GPS effectuée",
        message: `${totalPoints} point(s) N1 collecté(s), ${recusN2} trajet(s) N2 actualisé(s).`
      });
      charger(true);
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Synchronisation impossible", message: e.message });
    } finally {
      setSyncing(false);
    }
  }

  async function prefillGPS() {
    try {
      const r = await api(`/api/suivi/prefill?date=${date}`, { method: "POST" });
      addToast({ type: "succes", titre: "Pré-remplissage GPS", message: `${r.positions_remplies} positions renseignées.` });
      charger(true);
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Pré-remplissage impossible", message: e.message });
    }
  }

  /** §3 — export Excel/PDF : respecte filtres actifs + mode compact/détaillé. */
  // §0duodecies F4 (25/08/2026) — Rapport de diagnostic exportable : fichier
  // texte (lignes du jour toutes statuts, compteurs, positions GPS, actions
  // automatiques) à joindre à toute question sur une ligne de la grille.
  async function diagnostic() {
    const cible = recherche.trim();
    if (!cible) {
      addToast({ type: "info", titre: "Rapport de diagnostic",
                 message: "Tapez d'abord la plaque dans la recherche (ex. 0926TBV), puis cliquez « Diagnostic »." });
      return;
    }
    try {
      await download(`/api/diagnostic/journee?plaque=${encodeURIComponent(cible)}&jour=${date}`,
        `diagnostic_${cible.replace(/\s/g, "")}_${date}.txt`);
      addToast({ type: "succes", titre: "Rapport de diagnostic",
                 message: "Fichier téléchargé — joignez-le tel quel à votre question." });
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Diagnostic impossible", message: e.message });
    }
  }

  async function exporter(fmt: "xlsx" | "pdf") {
    const p = new URLSearchParams({ date, mode: modeDetail ? "detail" : "compact" });
    if (filtreStatut) p.set("statut", filtreStatut);
    if (recherche.trim()) p.set("q", recherche.trim());
    try {
      await download(`/api/suivi/export.${fmt}?${p}`, `Suivi_Journalier_${date}.${fmt}`);
      addToast({ type: "succes", titre: `Export ${fmt.toUpperCase()} téléchargé`,
        message: `Suivi du ${date.split("-").reverse().join("/")} — ${lignes.length} camions (${modeDetail ? "détaillé" : "compact"}).` });
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Export impossible", message: e.message });
    }
  }

  const lignes = useMemo(() => {
    let ls = data?.lignes || [];
    const q = recherche.trim().toLowerCase();
    if (q) {
      ls = ls.filter((l) =>
        l.plaque?.toLowerCase().includes(q) ||
        l.description?.toLowerCase().includes(q) ||
        l.conducteur?.prenom_usuel?.toLowerCase().includes(q) ||
        l.conducteur?.nom_prenom?.toLowerCase().includes(q));
    }
    if (filtreStatut) ls = ls.filter((l) => (l.statut_camion || "—") === filtreStatut);
    return ls;
  }, [data, recherche, filtreStatut]);

  const indicateurs = {
    neutre: { icone: "verifier", txt: "À jour", style: "text-slate-400 border-slate-300 dark:border-slate-700" },
    attente: { icone: "horloge", txt: "Modification en cours…", style: "text-amber-500 border-amber-500/40 bg-amber-500/10" },
    en_cours: { icone: "spinner", txt: "Enregistrement…", style: "text-sky-500 border-sky-500/40 bg-sky-500/10" },
    ok: { icone: "verifier", txt: `Enregistré à ${save.heure || ""}`, style: "text-emerald-500 border-emerald-500/40 bg-emerald-500/10" },
    erreur: { icone: "alerte", txt: "Erreur de sauvegarde — nouvelle tentative…", style: "text-red-500 border-red-500/50 bg-red-500/10 animate-pulse" },
  }[save.etat];

  return (
    <div className="flex h-full flex-col gap-3">
      <PageHeader
        titre="Suivi Journalier"
        sousTitre={<>
          <b className="capitalize">{fmtDateFr(date)}</b> · {lignes.length} camions ·{" "}
          {lectureSeule ? (user?.role === "CONSULTATION" ? "lecture seule (Consultation)" : "lecture seule (jour archivé/actif différent)") : "sauvegarde automatique (§2 Addendum v1.1)"}
        </>}
        actions={<>
          {!lectureSeule && (
            <span title="Sauvegarde automatique : après chaque modification (1,5 s) + synchronisation de sécurité (60 s)"
              className={cls("flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] font-semibold", indicateurs.style)}>
              {save.etat === "en_cours" ? <Spinner className="h-3.5 w-3.5" /> : <Icon nom={indicateurs.icone as any} className="h-3.5 w-3.5" />}
              {indicateurs.txt}
            </span>
          )}
          <input type="date" value={date} max={todayISO()}
            onChange={(e) => { setDate(e.target.value); setParams({ date: e.target.value }); }}
            className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-[13px]" />
          <Btn variante="secondaire" onClick={() => { setDate(todayISO()); setParams({}); }}>Aujourd'hui</Btn>
          {!lectureSeule && (
            <>
              <Btn variante="primaire" onClick={syncGPS} disabled={syncing} title="Force la synchronisation immédiate avec les serveurs GPS MZoneX et CamtrackPro">
                {syncing ? <Spinner className="h-3.5 w-3.5" /> : <Icon nom="rafraichir" />} Sync GPS
              </Btn>
              <Btn variante="secondaire" onClick={prefillGPS} title="Remplit la Partie C depuis les positions GPS">
                <Icon nom="localisation" /> Pré-remplir (GPS)
              </Btn>
            </>
          )}
          <Btn variante={modeDetail ? "primaire" : "secondaire"} title="27 colonnes trajets (exigence audit) : Heure de départ, Fin T1, Pause 1, Début/Fin/Pause T2→T9"
            onClick={() => { const v = !modeDetail; setModeDetail(v); localStorage.setItem(CLE_MODE, v ? "1" : "0"); }}>
            <Icon nom="tableau" /> {modeDetail ? "Détaillé" : "Compact"}
          </Btn>
          <Btn variante="secondaire" onClick={() => exporter("xlsx")} title="Export Excel de la vue affichée (filtres + mode respectés)">
            <Icon nom="telecharger" /> Excel
          </Btn>
          <Btn variante="secondaire" onClick={() => exporter("pdf")} title="Export PDF paysage (A3 en mode détaillé)">
            <Icon nom="telecharger" /> PDF
          </Btn>
          <Btn variante="secondaire" onClick={diagnostic}
            title="Rapport de diagnostic (règle F4 du 25/08/2026) : fichier texte de la journée pour une plaque — lignes, compteurs, positions GPS et actions automatiques. Tapez la plaque dans la recherche, puis cliquez ici.">
            <Icon nom="recherche" /> Diagnostic
          </Btn>
          <input value={recherche} onChange={(e) => setRecherche(e.target.value)}
            placeholder="Rechercher plaque / chauffeur…"
            className="w-44 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-[13px]" />
          <select value={filtreStatut} onChange={(e) => setFiltreStatut(e.target.value)}
            className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="">Tous statuts</option>
            {["LIBRE", "VIDE", "CHARGÉ", "—"].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </>} />

      {/* Légende des parties */}
      <div className="flex flex-wrap gap-3 text-[11px] font-medium">
        <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-sm bg-slate-400" /> A — Données synchronisées (persistantes)</span>
        <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-sm bg-blue-500" /> B — Données métier (reportées chaque jour)</span>
        <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-sm bg-amber-500" /> C — Saisie quotidienne (reset à minuit)</span>
        <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-sm bg-emerald-500" /> D — Calculs automatiques temps réel</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-nuit-900">
        {chargement && !data ? (
          <div className="flex h-40 items-center justify-center gap-2 text-slate-400"><Spinner /> Chargement…</div>
        ) : (
          <GrilleSuivi lignes={lignes} seuils={data?.seuils} modeDetail={modeDetail}
            sourcesEnPanne={data?.sources_en_echec ?? []}
            sourcesBloqueesLocalement={data?.sources_bloquees_localement ?? []}
            refs={refs} lectureSeule={lectureSeule}
            onEdit={lectureSeule ? undefined : programmer} pendingUI={pendingUI}
            /* §0vicies decies N1 — TCC « 0:00 » dès que le jour n'est plus le jour en cours */
            masquerTCC={date !== todayISO()} onRefresh={charger} />
        )}
      </div>
    </div>
  );
}
