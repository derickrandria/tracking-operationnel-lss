/** Module 1 — Dashboard : KPI, carte temps réel, graphiques (§6.1). */
import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { api } from "../api";
import EChart from "../components/EChart";
import { Badge, Card, PageHeader, Spinner } from "../components/ui";
import { useTheme } from "../theme";
import { cls, fmtDuree } from "../utils";
import { on } from "../ws";

const COULEURS_STATUT: Record<string, string> = {
  "CHARGÉ": "#f59e0b", "VIDE": "#3b82f6", "LIBRE": "#22c55e",
};

function iconeCamion(statut: string | null, moteur?: boolean, vitesse?: number) {
  const c = COULEURS_STATUT[statut || ""] || "#94a3b8";
  const estEnMouvement = (vitesse || 0) > 3;
  const estMoteurOn = Boolean(moteur);

  const bordure = estEnMouvement
    ? "border-emerald-500 shadow-emerald-500/50"
    : estMoteurOn
    ? "border-amber-500 shadow-amber-500/40"
    : "border-slate-400 opacity-90";

  return L.divIcon({
    className: "",
    html: `<div class="marqueur-camion ${bordure}" style="width:16px;height:16px;border-radius:50%;background:${c};border:2.5px solid white;box-shadow:0 2px 5px rgba(0,0,0,0.4);"></div>`,
    iconSize: [16, 16], iconAnchor: [8, 8], popupAnchor: [0, -10],
  });
}

function CarteFlotte({ positions, suivre }: { positions: any[]; suivre: Map<string, any> }) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const marqueurs = useRef<Map<string, L.Marker>>(new Map());
  const cadre = useRef(false);
  const theme = useTheme();

  useEffect(() => {
    if (!el.current) return;
    if (mapRef.current) {
      try { mapRef.current.remove(); } catch {}
      mapRef.current = null;
    }
    if ((el.current as any)._leaflet_id) {
      delete (el.current as any)._leaflet_id;
    }
    try {
      const map = L.map(el.current, { zoomControl: true, attributionControl: true })
        .setView([-18.9, 47.9], 8);
      mapRef.current = map;
    } catch (err) {
      console.warn("Erreur init Leaflet:", err);
    }
    return () => {
      if (mapRef.current) {
        try { mapRef.current.remove(); } catch {}
        mapRef.current = null;
      }
      marqueurs.current.clear();
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    try {
      map.eachLayer((l) => { if (l instanceof L.TileLayer) map.removeLayer(l); });
      const sombre = theme === "dark";
      L.tileLayer(
        sombre
          ? "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          : "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        { attribution: "© OpenStreetMap · © CARTO", maxZoom: 18 },
      ).addTo(map);
      setTimeout(() => {
        try { map.invalidateSize(); } catch {}
      }, 100);
    } catch (err) {
      console.warn("Erreur mise à jour tuiles Leaflet:", err);
    }
  }, [theme]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    try {
      const presents = new Set<string>();
      (positions || []).forEach((p) => {
        if (p.lat == null || p.lng == null) return;
        presents.add(p.vehicule_id);
        const contactTxt = p.vitesse > 3
          ? `<span style="color:#22c55e;font-weight:bold;">🟢 En mouvement (${Math.round(p.vitesse)} km/h)</span>`
          : p.moteur
          ? `<span style="color:#f59e0b;font-weight:bold;">🟠 Moteur ON (À l'arrêt)</span>`
          : `<span style="color:#64748b;">⚪ Stationné / Moteur OFF</span>`;

        const html = `<div style="font-size:12px;min-width:180px;line-height:1.4;">` +
          `<div style="font-weight:bold;font-size:13px;border-bottom:1px solid #e2e8f0;padding-bottom:3px;margin-bottom:4px;">` +
          `${p.plaque} <span style="font-weight:normal;color:#64748b;">(${p.conducteur || "Sans chauffeur"})</span>` +
          `</div>` +
          `<div style="color:#334155;margin-bottom:3px;">📍 ${p.adresse || "Position enregistrée"}</div>` +
          `<div style="margin-bottom:2px;"><b>Statut :</b> <span style="color:${COULEURS_STATUT[p.statut_camion] || '#64748b'};font-weight:bold;">${p.statut_camion || '—'}</span></div>` +
          `<div style="margin-bottom:3px;">${contactTxt}</div>` +
          `<div style="color:#94a3b8;font-size:11px;">🕒 ${p.maj ? p.maj.slice(0, 16).replace('T', ' ') : 'Dernière position connue'}</div>` +
          `</div>`;

        let m = marqueurs.current.get(p.vehicule_id);
        if (!m) {
          m = L.marker([p.lat, p.lng], { icon: iconeCamion(p.statut_camion, p.moteur, p.vitesse) }).addTo(map);
          marqueurs.current.set(p.vehicule_id, m);
        } else {
          m.setLatLng([p.lat, p.lng]);
          m.setIcon(iconeCamion(p.statut_camion, p.moteur, p.vitesse));
        }
        m.bindPopup(html);
      });
      marqueurs.current.forEach((m, id) => {
        if (!presents.has(id)) { m.remove(); marqueurs.current.delete(id); }
      });
      if (!cadre.current && presents.size > 2) {
        const groupe = L.featureGroup([...marqueurs.current.values()]);
        map.fitBounds(groupe.getBounds().pad(0.25));
        cadre.current = true;
      }
    } catch (err) {
      console.warn("Erreur mise à jour marqueurs Leaflet:", err);
    }
  }, [positions]);

  return <div ref={el} className="h-[430px] w-full rounded-lg overflow-hidden" />;
}

function Kpi({ label, valeur, sous, accent }: { label: string; valeur: any; sous?: string; accent?: string }) {
  return (
    <div className={cls("rounded-xl border p-3 bg-white dark:bg-nuit-900",
      accent || "border-slate-200 dark:border-slate-800")}>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-extrabold tabular-nums">{valeur}</div>
      {sous && <div className="mt-0.5 text-[12px] text-slate-400">{sous}</div>}
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<any | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [maj, setMaj] = useState("");
  const theme = useTheme();
  const timer = useRef<number>();

  async function charger() {
    try {
      const d = await api("/api/dashboard");
      setData(d);
      setErreur(null);
      setMaj(new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    } catch (e: any) {
      console.error("Erreur chargement dashboard:", e);
      setErreur(e?.message || "Erreur de chargement des données");
    }
  }

  useEffect(() => {
    charger();
    // rafraîchissement piloté par les événements temps réel (débattu)
    const rafraichir = () => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(charger, 1500);
    };
    const offs = ["suivi.update", "position", "mission.new", "mission.update",
      "infraction.new", "alerte.new", "data.refresh", "referentiels.changed"]
      .map((t) => on(t, rafraichir));
    return () => offs.forEach((f) => f());
  }, []);

  if (erreur && !data) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3 text-slate-500">
        <p className="text-red-500 font-medium">{erreur}</p>
        <button
          type="button"
          onClick={() => charger()}
          className="px-3 py-1.5 text-xs font-semibold rounded bg-blue-600 hover:bg-blue-700 text-white"
        >
          Réessayer
        </button>
      </div>
    );
  }

  if (!data) return <div className="flex h-64 items-center justify-center gap-2 text-slate-400"><Spinner /> Chargement du dashboard…</div>;

  const k = data.kpis;
  const txt = theme === "dark" ? "#cbd5e1" : "#475569";
  const grille = theme === "dark" ? "#1e293b" : "#e2e8f0";

  return (
    <div className="space-y-4">
      <PageHeader titre="Dashboard" sousTitre={<>Vue temps réel de l'exploitation · dernière MAJ <b>{maj}</b></>} />

      {/* KPI cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Camions actifs" valeur={k.camions_actifs} sous={`${k.camions_suivis} suivis aujourd'hui`} />
        <Kpi label="Libres / Vides / Chargés" valeur={`${k.libres} · ${k.vides} · ${k.charges}`} sous={`${k.non_renseignes} non renseignés`} />
        <Kpi label="Missions du jour"
          valeur={<span>{k.missions_en_cours}<span className="text-[13px] font-semibold text-slate-400"> / {k.missions_terminees}</span></span>}
          sous={`en cours / terminées${k.missions_deviees ? ` · ${k.missions_deviees} déviée${k.missions_deviees > 1 ? "s" : ""}` : ""}${k.missions_retardees ? ` · ${k.missions_retardees} retardée${k.missions_retardees > 1 ? "s" : ""}` : ""}`} />
        <Kpi label="Infractions" valeur={k.infractions_jour} sous={`${k.infractions_mois} ce mois`}
          accent={k.infractions_jour > 0 ? "border-red-500/50" : undefined} />
        <Kpi label="Alertes non vues" valeur={k.alertes_non_vues}
          accent={k.alertes_non_vues > 0 ? "border-amber-500/50" : undefined} />
        <Kpi label="Kilométrage du jour" valeur={`${Math.round(k.km_total_jour)} km`}
          sous={`${Math.round(k.km_vide_jour || 0)} km vide · ${Math.round(k.km_charge_jour || 0)} km chargé`} />
      </div>

      {/* Carte + colonnes */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" titre="Carte des positions en temps réel"
          actions={<div className="flex items-center gap-3 text-[11px]">
            {Object.entries(COULEURS_STATUT).map(([s, c]) => (
              <span key={s} className="flex items-center gap-1"><i className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: c }} />{s}</span>
            ))}
          </div>}>
          <CarteFlotte positions={data.positions} suivre={new Map()} />
        </Card>

        <div className="space-y-4">
          <Card titre="Top chauffeurs (régularité)">
            <ol className="space-y-2">
              {data.top_chauffeurs.map((t: any, i: number) => (
                <li key={t.id} className="flex items-center justify-between gap-2 text-[13px]">
                  <span className="flex items-center gap-2 min-w-0">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-slate-200 dark:bg-slate-700 text-[11px] font-bold">{i + 1}</span>
                    <span className="truncate font-medium">{t.prenom_usuel}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1.5">
                    <Badge>{t.km_jour} km</Badge>
                    <Badge couleur={t.infractions_mois === 0 ? "bg-emerald-500/15 text-emerald-500 border-emerald-500/40" : "bg-red-500/15 text-red-500 border-red-500/40"}>
                      {t.infractions_mois} infr.
                    </Badge>
                  </span>
                </li>
              ))}
              {data.top_chauffeurs.length === 0 && <li className="text-[13px] text-slate-400">En attente d'activité…</li>}
            </ol>
          </Card>
          <Card titre="Top véhicules (km du jour)">
            <ol className="space-y-2">
              {data.top_vehicules.map((t: any, i: number) => (
                <li key={t.plaque} className="flex items-center justify-between text-[13px]">
                  <span className="flex items-center gap-2">
                    <span className="flex h-5 w-5 items-center justify-center rounded-md bg-slate-200 dark:bg-slate-700 text-[11px] font-bold">{i + 1}</span>
                    <b>{t.plaque}</b>
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="text-slate-400">{t.statut_camion || "—"}</span>
                    <Badge>{t.km_jour} km</Badge>
                  </span>
                </li>
              ))}
              {data.top_vehicules.length === 0 && <li className="text-[13px] text-slate-400">En attente d'activité…</li>}
            </ol>
          </Card>
        </div>
      </div>

      {/* Graphiques */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card titre="Répartition des statuts (aujourd'hui)">
          <EChart option={{
            tooltip: {}, grid: { left: 40, right: 10, top: 20, bottom: 30 },
            xAxis: { type: "category", data: data.charts.statuts.map((s: any) => s.label), axisLabel: { color: txt, fontSize: 10 } },
            yAxis: { type: "value", axisLabel: { color: txt }, splitLine: { lineStyle: { color: grille } } },
            series: [{ type: "bar", barWidth: 26, data: data.charts.statuts.map((s: any) => s.value),
              itemStyle: { borderRadius: [5, 5, 0, 0], color: "#3b82f6" } }],
          }} height={210} />
        </Card>
        <Card titre="Infractions par type (mois)">
          <EChart option={{
            tooltip: {}, grid: { left: 110, right: 20, top: 10, bottom: 25 },
            xAxis: { type: "value", axisLabel: { color: txt }, splitLine: { lineStyle: { color: grille } } },
            yAxis: { type: "category", axisLabel: { color: txt, fontSize: 10 },
              data: data.charts.infractions_par_type.map((s: any) => s.label.replace("_", " ")) },
            series: [{ type: "bar", barWidth: 14, data: data.charts.infractions_par_type.map((s: any) => s.value),
              itemStyle: { borderRadius: [0, 5, 5, 0], color: "#ef4444" } }],
          }} height={210} />
        </Card>
        <Card titre="Produits transportés">
          <EChart option={{
            tooltip: {},
            series: [{ type: "pie", radius: ["45%", "70%"], label: { color: txt, fontSize: 11 },
              data: data.charts.produits.map((p: any) => ({ name: p.label, value: p.value })) }],
            color: ["#3b82f6", "#f59e0b", "#22c55e"],
          }} height={210} />
        </Card>
        <Card titre="Dépôts destinataires">
          <EChart option={{
            tooltip: {},
            series: [{ type: "pie", radius: ["45%", "70%"], label: { color: txt, fontSize: 11 },
              data: data.charts.depots.map((p: any) => ({ name: p.label, value: p.value })) }],
            color: ["#8b5cf6", "#06b6d4", "#f59e0b", "#ef4444", "#22c55e", "#eab308", "#94a3b8"],
          }} height={210} />
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card titre="Temps moyen de conduite journalier (30 jours)">
          <EChart option={{
            tooltip: { trigger: "axis", valueFormatter: (v: number) => fmtDuree(v) },
            legend: { textStyle: { color: txt } },
            grid: { left: 50, right: 20, top: 30, bottom: 30 },
            xAxis: { type: "category", data: data.charts.conduite_30j.map((c: any) => c.date.slice(5)), axisLabel: { color: txt, fontSize: 10 } },
            yAxis: { type: "value", axisLabel: { color: txt, formatter: (v: number) => `${Math.round(v / 3600)}h` }, splitLine: { lineStyle: { color: grille } } },
            series: [
              { name: "TCJ moyenne", type: "line", smooth: true, data: data.charts.conduite_30j.map((c: any) => c.tcj_moyen_s), lineStyle: { color: "#3b82f6" }, itemStyle: { color: "#3b82f6" }, areaStyle: { opacity: 0.12 } },
              { name: "TTJ moyen", type: "line", smooth: true, data: data.charts.conduite_30j.map((c: any) => c.ttj_moyen_s), lineStyle: { color: "#f59e0b" }, itemStyle: { color: "#f59e0b" } },
            ],
          }} height={250} />
        </Card>
        <Card titre="Heatmap — densité d'infractions (heure × jour, 30 jours)">
          <EChart option={{
            tooltip: { position: "top" },
            grid: { left: 60, right: 15, top: 10, bottom: 55 },
            xAxis: { type: "category", data: ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"], axisLabel: { color: txt }, splitArea: { show: true } },
            yAxis: { type: "category", data: Array.from({ length: 24 }, (_, h) => `${h}h`), axisLabel: { color: txt, fontSize: 9 }, splitArea: { show: true } },
            visualMap: { min: 0, max: Math.max(2, ...data.charts.heatmap.map((p: any) => p[2])), calculable: true,
              orient: "horizontal", left: "center", bottom: 0, textStyle: { color: txt },
              inRange: { color: ["#1e293b", "#7c2d12", "#ef4444"] } },
            series: [{ type: "heatmap", data: data.charts.heatmap.map((p: any) => [p[1], p[0], p[2]]), label: { show: false } }],
          }} height={250} />
        </Card>
      </div>
    </div>
  );
}
