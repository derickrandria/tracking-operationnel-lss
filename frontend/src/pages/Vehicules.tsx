/** Module 8 — Véhicules : référentiel unique des camions (§6.8). */
import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, getUser } from "../api";
import Icon from "../components/icons";
import { Badge, Btn, Card, Champ, inputCls, Modal, PageHeader, Spinner, Vide } from "../components/ui";
import { addToast } from "../components/toast";
import { Conducteur, Vehicule } from "../types";
import { cls, fmtHeure } from "../utils";
import { on } from "../ws";

const COULEURS_STATUT: Record<string, string> = {
  ACTIF: "bg-emerald-500/15 text-emerald-500 border-emerald-500/40",
  INACTIF: "bg-slate-500/15 text-slate-500 border-slate-500/40",
  MAINTENANCE: "bg-amber-500/15 text-amber-500 border-amber-500/40",
};

export default function Vehicules() {
  const [params] = useSearchParams();
  const [items, setItems] = useState<Vehicule[] | null>(null);
  const [conducteurs, setConducteurs] = useState<Conducteur[]>([]);
  const [q, setQ] = useState(params.get("q") || "");
  const [statut, setStatut] = useState("");
  const [form, setForm] = useState<Partial<Vehicule> | null>(null);
  const ecriture = ["ADMIN", "TRACKING"].includes(getUser()?.role || "");
  const admin = getUser()?.role === "ADMIN";

  async function charger() {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (statut) p.set("statut", statut);
    setItems(await api(`/api/vehicules?${p}`));
  }
  useEffect(() => { const t = setTimeout(charger, 200); return () => clearTimeout(t); }, [q, statut]);
  useEffect(() => on("referentiels.changed", () => charger()), [q, statut]);
  useEffect(() => { api("/api/conducteurs?statut=ACTIF").then(setConducteurs).catch(() => {}); }, []);

  async function sauvegarder(e: FormEvent) {
    e.preventDefault();
    const corps = { ...form, capacite: form.capacite ? Number(form.capacite) : null,
      conducteur_actuel_id: form.conducteur_actuel_id || null };
    try {
      if (form!.id) {
        await api(`/api/vehicules/${form!.id}`, { method: "PATCH", body: JSON.stringify(corps) });
        addToast({ type: "succes", titre: "Véhicule modifié",
          message: "Réaffectation répercutée immédiatement sur le Suivi du jour." });
      } else {
        await api("/api/vehicules", { method: "POST", body: JSON.stringify(corps) });
        addToast({ type: "succes", titre: "Véhicule ajouté" });
      }
      setForm(null);
      charger();
    } catch (err: any) {
      addToast({ type: "erreur", titre: "Enregistrement impossible", message: err.message });
    }
  }

  async function supprimer(v: Vehicule) {
    if (!confirm(`Supprimer définitivement le véhicule ${v.plaque} ?`)) return;
    try {
      await api(`/api/vehicules/${v.id}`, { method: "DELETE" });
      addToast({ type: "succes", titre: "Véhicule supprimé" });
      charger();
    } catch (err: any) {
      addToast({ type: "erreur", titre: "Suppression refusée", message: err.message });
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">{/* §0octies decies L1 — barre horizontale toujours visible */}
      <PageHeader titre="Véhicules"
        sousTitre={`Flotte de camions-citernes — boîtiers OBC (MZoneX / CamtrackPro) · ${items?.length ?? "…"} véhicules`}
        actions={<>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Rechercher (plaque, description)…"
            className="w-56 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-[13px]" />
          <select value={statut} onChange={(e) => setStatut(e.target.value)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="">Tous statuts</option>
            {["ACTIF", "INACTIF", "MAINTENANCE"].map((s) => <option key={s}>{s}</option>)}
          </select>
          {ecriture && (
            <Btn onClick={() => setForm({ statut: "ACTIF", capacite: 36000 })}><Icon nom="plus" /> Ajouter</Btn>
          )}
        </>} />

      <Card className="!p-0 overflow-hidden flex min-h-0 flex-1 flex-col" contenuClasse="overflow-auto !p-0" titre={`${items?.length ?? 0} véhicules`}>
        {!items ? (
          <div className="flex h-40 items-center justify-center gap-2 text-slate-400"><Spinner /></div>
        ) : items.length === 0 ? (
          <Vide texte="Aucun véhicule trouvé." />
        ) : (
          <div>
            <table className="table-pro">
              <thead><tr>
                <th>Plaque</th><th>Description</th><th>Marque</th><th>Capacité (L)</th>
                <th>GPS associé (OBC)</th><th>Conducteur actuel</th><th>Dernier point GPS</th>
                <th>Statut</th>{ecriture && <th className="w-28">Actions</th>}
              </tr></thead>
              <tbody>
                {items.map((v) => (
                  <tr key={v.id}>
                    <td className="font-bold whitespace-nowrap">{v.plaque}</td>
                    <td className="text-[12px] text-slate-400 whitespace-nowrap">{v.description}</td>
                    <td className="whitespace-nowrap">{v.marque || "—"}</td>
                    <td className="tabular-nums">{v.capacite?.toLocaleString("fr-FR") || "—"}</td>
                    <td className="font-mono text-[12px] text-slate-400">
                      <span className={cls("mr-1.5 rounded px-1 py-0.5 text-[9.5px] font-bold font-sans",
                        (v.plateforme_gps || "MZONEX") === "CAMTRACKPRO"
                          ? "bg-violet-500/15 text-violet-600 dark:text-violet-400"
                          : "bg-sky-500/15 text-sky-600 dark:text-sky-400")}
                        title="Portail GPS de remontée des données (flotte mixte)">
                        {(v.plateforme_gps || "MZONEX") === "CAMTRACKPRO" ? "CTPRO" : "MZX"}
                      </span>
                      {v.gps_associe || "—"}
                    </td>
                    <td className="font-medium whitespace-nowrap">{v.conducteur?.prenom_usuel || <span className="text-slate-400">—</span>}</td>
                    <td className="max-w-[220px] text-[11.5px] text-slate-400">
                      {v.position?.adresse ? (
                        <span title={v.position.adresse} className="block truncate">
                          {v.position.adresse}
                          <span className="text-slate-500"> · {fmtHeure(v.position.maj)}</span>
                        </span>
                      ) : "—"}
                    </td>
                    <td><Badge couleur={COULEURS_STATUT[v.statut]}>{v.statut}</Badge></td>
                    {ecriture && (
                      <td className="whitespace-nowrap">
                        <Btn variante="fantome" className="!px-2 !py-0.5 text-[11.5px]" onClick={() => setForm(v)}>Modifier</Btn>
                        {admin && (
                          <Btn variante="fantome" className="!px-2 !py-0.5 text-[11.5px] text-red-500" onClick={() => supprimer(v)}>Suppr.</Btn>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal ouvert={!!form} onFermer={() => setForm(null)}
        titre={form?.id ? `Modifier ${form.plaque}` : "Nouveau véhicule"}>
        {form && (
          <form onSubmit={sauvegarder} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Champ label="Plaque d'immatriculation">
                <input required className={inputCls} value={form.plaque || ""} disabled={!!form.id}
                  onChange={(e) => setForm({ ...form, plaque: e.target.value.toUpperCase() })} />
              </Champ>
              <Champ label="Description (tracteur/citerne)">
                <input className={inputCls} value={form.description || ""} placeholder="ex : 0576TCD/0797TBP"
                  onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </Champ>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Champ label="Marque">
                <input className={inputCls} value={form.marque || ""}
                  onChange={(e) => setForm({ ...form, marque: e.target.value })} />
              </Champ>
              <Champ label="Capacité (litres)">
                <input type="number" className={inputCls} value={form.capacite ?? ""}
                  onChange={(e) => setForm({ ...form, capacite: e.target.valueAsNumber })} />
              </Champ>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Champ label="GPS associé (identifiant OBC)">
                <input className={inputCls} value={form.gps_associe || ""} placeholder="auto : OBC-<plaque>"
                  onChange={(e) => setForm({ ...form, gps_associe: e.target.value })} />
              </Champ>
              <Champ label="Statut">
                <select className={inputCls} value={form.statut || "ACTIF"}
                  onChange={(e) => setForm({ ...form, statut: e.target.value })}>
                  {["ACTIF", "INACTIF", "MAINTENANCE"].map((s) => <option key={s}>{s}</option>)}
                </select>
              </Champ>
            </div>
            <Champ label="Conducteur actuel (répercuté sur le Suivi du jour)">
              <select className={inputCls} value={form.conducteur_actuel_id || ""}
                onChange={(e) => setForm({ ...form, conducteur_actuel_id: e.target.value || null })}>
                <option value="">— Aucun —</option>
                {conducteurs.map((c) => (
                  <option key={c.id} value={c.id}>{c.prenom_usuel} — {c.nom_prenom}</option>
                ))}
              </select>
            </Champ>
            <div className="flex justify-end gap-2 pt-2">
              <Btn variante="secondaire" onClick={() => setForm(null)}>Annuler</Btn>
              <Btn type="submit"><Icon nom="verifier" /> Enregistrer</Btn>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}
