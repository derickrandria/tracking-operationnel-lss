/** Module 5 — Alertes : flux temps réel classé par gravité (§6.5). */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, download, getUser } from "../api";
import Icon from "../components/icons";
import { Badge, Btn, Card, PageHeader, Spinner, Vide } from "../components/ui";
import { addToast } from "../components/toast";
import { Alerte } from "../types";
import { cls, COULEURS_GRAVITE, COULEURS_STATUT_ALERTE, fmtDateHeure, LABELS_ALERTE } from "../utils";
import { on } from "../ws";

const ICONES: Record<string, string> = {
  TCC_DEPASSE: "horloge", PAUSE_NON_PRISE: "horloge", CAMION_IMMOBILE: "vehicules",
  GPS_HORS_LIGNE: "localisation", MISSION_RETARDEE: "missions", EXCES_VITESSE: "vitesse",
  NOUVEAU_VEHICULE: "vehicules", NOUVEAU_CONDUCTEUR: "conducteurs",
  HORS_ITINERAIRE: "missions", CARBURANT_SUSPECT: "carburant",
  REPARATION_DONNEES: "historique",
  TCH_PROCHE_LIMITE: "horloge", TCH_LIMITE_ATTEINTE: "horloge",
};

export default function Alertes() {
  const [data, setData] = useState<{ items: Alerte[] } | null>(null);
  const [gravite, setGravite] = useState("");
  const [statut, setStatut] = useState("");
  const [type, setType] = useState("");
  const [types, setTypes] = useState<Record<string, string>>({});
  const user = getUser();
  const nav = useNavigate();
  const ecriture = user?.role !== "CONSULTATION";

  function query() {
    const p = new URLSearchParams();
    if (gravite) p.set("gravite", gravite);
    if (statut) p.set("statut", statut);
    if (type) p.set("type", type);
    return p.toString();
  }

  async function charger() {
    const d = await api(`/api/alertes?${query()}`);
    setData(d);
    setTypes(d.types || {});
  }

  useEffect(() => { charger(); }, [gravite, statut, type]);
  useEffect(() => on("alerte.new", () => charger()), [gravite, statut, type]);

  async function marquer(a: Alerte, s: "VUE" | "TRAITEE") {
    try {
      await api(`/api/alertes/${a.id}/statut`, { method: "POST", body: JSON.stringify({ statut: s }) });
      charger();
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Action impossible", message: e.message });
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader titre="Alertes"
        sousTitre="Flux temps réel des anomalies opérationnelles — classification Critique / Moyenne / Information"
        actions={<>
          <select value={gravite} onChange={(e) => setGravite(e.target.value)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="">Toutes gravités</option>
            {["CRITIQUE", "MOYENNE", "INFORMATION"].map((g) => <option key={g}>{g}</option>)}
          </select>
          <select value={statut} onChange={(e) => setStatut(e.target.value)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="">Tous statuts</option>
            {[["NOUVELLE", "Nouvelle"], ["VUE", "Vue"], ["TRAITEE", "Traitée"]].map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select value={type} onChange={(e) => setType(e.target.value)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="">Tous types</option>
            {Object.entries(types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          {ecriture && (
            <Btn variante="secondaire" onClick={async () => { await api("/api/alertes/tout-marquer-vues", { method: "POST" }); charger(); }}>
              <Icon nom="verifier" /> Tout marquer vues
            </Btn>
          )}
          <Btn variante="secondaire" onClick={() => download("/api/alertes/export.xlsx", "alertes.xlsx").then(() => addToast({ type: "succes", titre: "Export Excel téléchargé" }))}>
            <Icon nom="telecharger" /> Excel
          </Btn>
        </>} />

      <Card className="!p-2" titre={`Flux d'alertes ${data ? `· ${data.items.length}` : ""}`}>
        {!data ? (
          <div className="flex h-40 items-center justify-center gap-2 text-slate-400"><Spinner /></div>
        ) : data.items.length === 0 ? (
          <Vide texte="Aucune alerte ne correspond aux filtres." />
        ) : (
          <ul className="space-y-1.5">
            {data.items.map((a) => (
              <li key={a.id}
                className={cls("flex items-start gap-3 rounded-lg border p-2.5 transition",
                  a.statut === "NOUVELLE"
                    ? "border-red-500/30 bg-red-500/[0.05]"
                    : "border-slate-200 dark:border-slate-800 opacity-90")}>
                <span className={cls("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border", COULEURS_GRAVITE[a.gravite])}>
                  <Icon nom={ICONES[a.type] || "alertes"} className="w-4 h-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge couleur={COULEURS_GRAVITE[a.gravite]}>{a.gravite}</Badge>
                    <span className="text-[12px] font-semibold">{LABELS_ALERTE[a.type] || a.type}</span>
                    <span className="text-[11px] tabular-nums text-slate-400">{fmtDateHeure(a.date_heure)}</span>
                    <Badge couleur={COULEURS_STATUT_ALERTE[a.statut]}>{a.statut === "TRAITEE" ? "Traitée" : a.statut === "VUE" ? "Vue" : "Nouvelle"}</Badge>
                  </div>
                  <p className="mt-0.5 text-[13px] leading-snug">{a.message}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-[11.5px] text-slate-400">
                    {a.plaque && <span className="font-bold text-slate-500 dark:text-slate-300">{a.plaque}</span>}
                    {a.conducteur && <span>{a.conducteur.prenom_usuel}</span>}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col gap-1">
                  {a.lien_module && (
                    <Btn variante="fantome" className="!px-2 !py-1 text-[11.5px]" onClick={() => nav(a.lien_module!)}>
                      Voir le module
                    </Btn>
                  )}
                  {ecriture && a.statut === "NOUVELLE" && (
                    <Btn variante="fantome" className="!px-2 !py-1 text-[11.5px]" onClick={() => marquer(a, "VUE")}>Marquer vue</Btn>
                  )}
                  {ecriture && a.statut !== "TRAITEE" && (
                    <Btn variante="fantome" className="!px-2 !py-1 text-[11.5px] text-emerald-500" onClick={() => marquer(a, "TRAITEE")}>Traiter</Btn>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
