/** Carnet de conduite — §0septies B3 (arbitrages LSS du 20/08/2026).
 *  Deux vues : score par chauffeur + détail des « trajets noirs ».
 *  Données 100 % officielles des portails (compteurs MZoneX, rapport
 *  CamtrackPro) — aucune saisie, aucune colonne ajoutée à la grille. */
import { useEffect, useState } from "react";
import { api } from "../api";
import Icon from "../components/icons";
import { Badge, Card, PageHeader, Spinner, Vide } from "../components/ui";
import { cls, fmtDuree } from "../utils";

type CarnetChauffeur = {
  conducteur_id: string | null; chauffeur: string; sans_badge: boolean;
  trajets: number; km: number; v_max: number | null; ralenti_s: number;
  exc_vitesse: number; exc_freinage: number; exc_accel: number;
  exc_ralenti: number; exc_surregime: number; exc_autres: number;
  total: number; pour_100km: number | null; vehicules: string[];
};

type TrajetNoir = {
  date: string; plaque: string; debut: string; fin: string | null;
  distance_km: number | null; chauffeur: string; source: string | null;
  v_max: number | null; ralenti_s: number | null;
  exc_vitesse: number; exc_freinage: number; exc_accel: number;
  exc_ralenti: number; exc_surregime: number; exc_autres: number;
  total: number;
};

const COLS = [
  ["exc_vitesse", "Vitesse"], ["exc_freinage", "Frein."],
  ["exc_accel", "Accél."], ["exc_ralenti", "Ralenti"],
  ["exc_surregime", "Régime"], ["exc_autres", "Autres"],
] as const;

function CellExc({ n }: { n: number }) {
  return <td className={cls("tabular-nums text-center", n > 0 ? "text-red-500 font-semibold" : "text-slate-300 dark:text-slate-600")}>{n || "·"}</td>;
}

export default function Conduite() {
  const [du, setDu] = useState("");
  const [au, setAu] = useState("");
  const [chauffeurs, setChauffeurs] = useState<CarnetChauffeur[] | null>(null);
  const [trajets, setTrajets] = useState<TrajetNoir[] | null>(null);

  async function charger() {
    const p = new URLSearchParams();
    if (du) p.set("du", du);
    if (au) p.set("au", au);
    const q = p.toString() ? `?${p}` : "";
    try {
      const [c, t] = await Promise.all([
        api(`/api/conduite/chauffeurs${q}`),
        api(`/api/conduite/trajets${q}`),
      ]);
      setChauffeurs(c.chauffeurs || []);
      setTrajets(t.trajets || []);
    } catch (e) {
      console.error("Erreur chargement conduite:", e);
      setChauffeurs([]);
      setTrajets([]);
    }
  }

  useEffect(() => { charger(); }, [du, au]);

  return (
    <div className="flex h-full flex-col gap-4">{/* §0octies decies L1 — barre horizontale toujours visible */}
      <PageHeader titre="Conduite"
        sousTitre={<>Carnet de conduite officiel des portails — badge chauffeur + infractions d'écoconduite · <b>semaine en cours</b> par défaut</>}
        actions={<>
          <input type="date" value={du} onChange={(e) => setDu(e.target.value)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]" />
          <span className="text-slate-400 text-[12px]">au</span>
          <input type="date" value={au} onChange={(e) => setAu(e.target.value)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]" />
        </>} />

      {/* Vue 1 — score par chauffeur */}
      <Card className="!p-0 overflow-hidden flex min-h-0 flex-1 flex-col" contenuClasse="overflow-auto !p-0" titre={<span>Score par chauffeur ({chauffeurs?.length ?? "…"})<span className="ml-2 text-[12px] font-normal text-slate-400">tri par infractions · « /100 km » = infractions ramenées à 100 km</span></span>}>
        {!chauffeurs ? (
          <div className="flex h-32 items-center justify-center text-slate-400"><Spinner /></div>
        ) : chauffeurs.length === 0 ? (
          <Vide texte="Aucun trajet officiel sur cette période." />
        ) : (
          <div>
            <table className="table-pro">
              <thead><tr>
                <th>Chauffeur</th><th className="text-center">Trajets</th><th className="text-right">km</th>
                {COLS.map(([k, l]) => <th key={k} className="text-center">{l}</th>)}
                <th className="text-center">Total</th><th className="text-right">/100 km</th>
                <th className="text-right">V max</th><th className="text-right">Ralenti</th><th>Camions</th>
              </tr></thead>
              <tbody>
                {chauffeurs.map((c, i) => (
                  <tr key={c.conducteur_id || i} className={cls(c.sans_badge && "bg-amber-50/60 dark:bg-amber-900/10")}>
                    <td className="whitespace-nowrap font-medium">
                      {c.chauffeur}{" "}
                      {c.sans_badge && <Badge couleur="bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-400/40 ml-1">à vérifier</Badge>}
                    </td>
                    <td className="tabular-nums text-center">{c.trajets}</td>
                    <td className="tabular-nums text-right">{c.km.toLocaleString("fr-FR")}</td>
                    {COLS.map(([k]) => <CellExc key={k} n={c[k]} />)}
                    <td className={cls("tabular-nums text-center font-bold", c.total > 0 ? "text-red-600" : "text-emerald-600")}>{c.total}</td>
                    <td className="tabular-nums text-right">{c.pour_100km ?? "—"}</td>
                    <td className="tabular-nums text-right">{c.v_max != null ? `${Math.round(c.v_max)} km/h` : "—"}</td>
                    <td className="tabular-nums text-right">{c.ralenti_s ? fmtDuree(c.ralenti_s) : "—"}</td>
                    <td className="max-w-[180px] truncate text-[12px] text-slate-400" title={c.vehicules.join(", ")}>{c.vehicules.slice(0, 3).join(", ")}{c.vehicules.length > 3 ? ` +${c.vehicules.length - 3}` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Vue 2 — trajets noirs */}
      <Card className="!p-0 overflow-hidden flex min-h-0 flex-1 flex-col" contenuClasse="overflow-auto !p-0" titre={<span>Trajets noirs ({trajets?.length ?? "…"})<span className="ml-2 text-[12px] font-normal text-slate-400">qui, quand, vitesse max, nature des infractions — les plus chargés d'abord</span></span>}>
        {!trajets ? (
          <div className="flex h-32 items-center justify-center text-slate-400"><Spinner /></div>
        ) : trajets.length === 0 ? (
          <Vide texte="Aucun trajet avec infraction sur cette période. 🎉" />
        ) : (
          <div>
            <table className="table-pro">
              <thead><tr>
                <th>Date</th><th>Début</th><th>Fin</th><th>Plaque</th><th>Chauffeur</th>
                <th className="text-right">km</th><th className="text-right">V max</th>
                {COLS.map(([k, l]) => <th key={k} className="text-center">{l}</th>)}
                <th className="text-center">Total</th><th className="text-right">Ralenti</th><th>Source</th>
              </tr></thead>
              <tbody>
                {trajets.map((t, i) => (
                  <tr key={i}>
                    <td className="whitespace-nowrap">{t.date.split("-").reverse().join("/")}</td>
                    <td className="whitespace-nowrap tabular-nums">{t.debut?.slice(11, 16)}</td>
                    <td className="whitespace-nowrap tabular-nums">{t.fin ? t.fin.slice(11, 16) : "en cours"}</td>
                    <td className="whitespace-nowrap font-bold">{t.plaque}</td>
                    <td className="whitespace-nowrap">{t.chauffeur}</td>
                    <td className="tabular-nums text-right">{t.distance_km != null ? t.distance_km.toLocaleString("fr-FR") : "—"}</td>
                    <td className={cls("tabular-nums text-right", (t.v_max ?? 0) > 90 && "text-red-500 font-semibold")}>
                      {t.v_max != null ? Math.round(t.v_max) : "—"}
                    </td>
                    {COLS.map(([k]) => <CellExc key={k} n={t[k]} />)}
                    <td className="tabular-nums text-center font-bold text-red-600">{t.total}</td>
                    <td className="tabular-nums text-right">{t.ralenti_s ? fmtDuree(t.ralenti_s) : "—"}</td>
                    <td className="text-[12px] text-slate-400">{t.source === "CAMTRACKPRO" ? "CamtrackPro" : "MZoneX"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
