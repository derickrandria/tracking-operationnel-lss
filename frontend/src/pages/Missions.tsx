/** Module 3 — Missions : reconstitution automatique des cycles logistiques (§6.3). */
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import Icon from "../components/icons";
import { Badge, Card, PageHeader, Spinner, Vide } from "../components/ui";
import { Mission } from "../types";
import { cls, COULEURS_STATUT_MISSION, fmtDateFr, fmtDuree, fmtHeure, todayISO } from "../utils";
import { on } from "../ws";

const ETAPE_LABELS: Record<string, string> = {
  TRANSIT_CHARGEMENT: "Départ vers chargement",
  CHARGEMENT_TERMINE: "Chargement terminé (CHARGÉ)",
  DECHARGEMENT_TERMINE: "Déchargement terminé (LIBRE)",
};

function CarteMission({ m, defautOuverte }: { m: Mission; defautOuverte?: boolean }) {
  const [ouverte, setOuverte] = useState(!!defautOuverte);
  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden">
      <button onClick={() => setOuverte((v) => !v)}
        className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left bg-slate-50 dark:bg-slate-800/40 hover:bg-slate-100 dark:hover:bg-slate-800">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-blue-600 text-[11px] font-bold text-white">
          {m.numero_mission_du_jour}
        </span>
        <span className="text-[13px] font-semibold">Mission n°{m.numero_mission_du_jour}</span>
        <Badge couleur={COULEURS_STATUT_MISSION[m.statut]}>{m.statut.replace("_", " ")}</Badge>
        <span className="ml-auto flex items-center gap-3 text-[12px] text-slate-400">
          <span className="tabular-nums">{fmtHeure(m.heure_debut)} → {m.heure_fin ? fmtHeure(m.heure_fin) : "…"}</span>
          <span className="tabular-nums">{m.statut === "EN_COURS" ? "durée en cours" : `durée ${fmtDuree(m.duree_s)}`}</span>
          <span className="tabular-nums">{Math.round(m.kilometrage)} km</span>
          <Icon nom="chevron" className={cls("w-3.5 h-3.5 transition-transform", ouverte && "rotate-180")} />
        </span>
      </button>
      {ouverte && (
        <div className="grid gap-4 p-4 sm:grid-cols-2">
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[13px]">
            <dt className="text-slate-400">N° OT</dt><dd className="font-medium">{m.numero_ot || "—"}</dd>
            <dt className="text-slate-400">Produit</dt><dd className="font-medium">{m.produit || "—"}</dd>
            <dt className="text-slate-400">Dépôt</dt><dd className="font-medium">{m.depot || "—"}</dd>
            <dt className="text-slate-400">Distributeur</dt><dd className="font-medium">{m.distributeur || "—"}</dd>
            <dt className="text-slate-400">Origine</dt><dd className="font-medium truncate" title={m.origine || ""}>{m.origine || "—"}</dd>
            <dt className="text-slate-400">Kilométrage</dt><dd className="font-medium tabular-nums">{m.kilometrage.toFixed(1)} km</dd>
          </dl>
          <div>
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Étapes détectées</div>
            <ol className="relative space-y-2.5 border-l border-slate-300 dark:border-slate-700 pl-4">
              {(m.etapes || []).map((e, i) => (
                <li key={i} className="relative text-[12.5px]">
                  <span className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full bg-blue-500" />
                  <b className="tabular-nums">{fmtHeure(e.ts)}</b> — {ETAPE_LABELS[e.etat] || e.etat}
                  {e.lieu && <span className="block text-slate-400 truncate">{e.lieu}</span>}
                </li>
              ))}
              {m.statut === "EN_COURS" && (
                <li className="relative text-[12.5px] text-blue-400">
                  <span className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full bg-blue-500 ws-on" />
                  Mission en cours…
                </li>
              )}
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Missions() {
  const [date, setDate] = useState(todayISO());
  const [data, setData] = useState<{ lignes: any[] } | null>(null);
  const [filtre, setFiltre] = useState("");
  const timer = useRef<number>();

  async function charger() {
    setData(await api(`/api/missions/jour/${date}`));
  }

  useEffect(() => { charger(); }, [date]);

  useEffect(() => {
    const rafraichir = () => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(charger, 2500);
    };
    const offs = ["mission.new", "mission.update", "data.refresh"].map((t) => on(t, rafraichir));
    return () => offs.forEach((f) => f());
  }, [date]);

  if (!data) return <div className="flex h-64 items-center justify-center gap-2 text-slate-400"><Spinner /> Chargement…</div>;

  const q = filtre.trim().toLowerCase();
  const lignes = data.lignes.filter((l) =>
    !q || l.conducteur?.prenom_usuel?.toLowerCase().includes(q) ||
    l.conducteur?.nom_prenom?.toLowerCase().includes(q) || l.plaque?.toLowerCase().includes(q));
  const nbMissions = lignes.reduce((n, l) => n + l.nb_missions, 0);
  const multi = lignes.filter((l) => l.nb_missions >= 2).length;

  return (
    <div className="space-y-4">
      <PageHeader titre="Missions"
        sousTitre={<><b className="capitalize">{fmtDateFr(date)}</b> · {nbMissions} missions détectées · <b>{multi}</b> chauffeurs en multi-missions (segmentation automatique §6.3)</>}
        actions={<>
          <input type="date" value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)}
            className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-[13px]" />
          <input value={filtre} onChange={(e) => setFiltre(e.target.value)} placeholder="Filtrer chauffeur / plaque…"
            className="w-56 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-[13px]" />
        </>} />

      {lignes.filter((l) => l.nb_missions > 0).length === 0 && (
        <Vide texte="Aucune mission détectée pour cette journée (une mission démarre à chaque transition LIBRE → VIDE)." />
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {lignes.filter((l) => l.nb_missions > 0).map((l) => (
          <Card key={l.vehicule_id} className="!p-0"
            titre={<span>{l.conducteur?.prenom_usuel} <span className="font-normal text-slate-400">({l.plaque})</span></span>}
            actions={<Badge couleur={l.nb_missions >= 2 ? "bg-violet-500/15 text-violet-500 border-violet-500/40" : undefined}>
              {l.nb_missions} mission{l.nb_missions > 1 ? "s" : ""}
            </Badge>}>
            <div className="space-y-2.5">
              <div className="text-[12px] text-slate-400">
                Situation actuelle : <b className="text-slate-500 dark:text-slate-300">{l.situation || "—"}</b> · Statut : <b>{l.statut_camion || "—"}</b>
              </div>
              {l.missions.map((m: Mission) => (
                <CarteMission key={m.id} m={m} defautOuverte={l.nb_missions >= 2 || m.statut !== "TERMINÉE"} />
              ))}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
