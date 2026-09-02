/** Module 7 — Conducteurs : référentiel unique des chauffeurs (§6.7). */
import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, getUser } from "../api";
import Icon from "../components/icons";
import { Badge, Btn, Card, Champ, inputCls, Modal, PageHeader, Spinner, Vide } from "../components/ui";
import { addToast } from "../components/toast";
import { Conducteur } from "../types";
import { on } from "../ws";

const COULEURS_STATUT: Record<string, string> = {
  ACTIF: "bg-emerald-500/15 text-emerald-500 border-emerald-500/40",
  SUSPENDU: "bg-red-500/15 text-red-500 border-red-500/40",
  "CONGÉ": "bg-amber-500/15 text-amber-500 border-amber-500/40",
  INACTIF: "bg-slate-500/15 text-slate-500 border-slate-500/40",
};

export default function Conducteurs() {
  const [params] = useSearchParams();
  const [items, setItems] = useState<Conducteur[] | null>(null);
  const [q, setQ] = useState(params.get("q") || "");
  const [statut, setStatut] = useState("");
  const [tri, setTri] = useState<"prenom_usuel" | "nom_prenom" | "matricule">("prenom_usuel");
  const [form, setForm] = useState<Partial<Conducteur> | null>(null);
  const ecriture = ["ADMIN", "TRACKING"].includes(getUser()?.role || "");
  const admin = getUser()?.role === "ADMIN";

  async function charger() {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (statut) p.set("statut", statut);
    setItems(await api(`/api/conducteurs?${p}`));
  }
  useEffect(() => { const t = setTimeout(charger, 200); return () => clearTimeout(t); }, [q, statut]);
  useEffect(() => on("referentiels.changed", () => charger()), [q, statut]);

  async function sauvegarder(e: FormEvent) {
    e.preventDefault();
    try {
      if (form!.id) {
        await api(`/api/conducteurs/${form!.id}`, { method: "PATCH", body: JSON.stringify(form) });
        addToast({ type: "succes", titre: "Conducteur modifié", message: "Synchronisé vers Suivi, Missions, Dashboard…" });
      } else {
        await api("/api/conducteurs", { method: "POST", body: JSON.stringify(form) });
        addToast({ type: "succes", titre: "Conducteur ajouté" });
      }
      setForm(null);
      charger();
    } catch (err: any) {
      addToast({ type: "erreur", titre: "Enregistrement impossible", message: err.message });
    }
  }

  async function supprimer(c: Conducteur) {
    if (!confirm(`Supprimer définitivement ${c.nom_prenom} ?`)) return;
    try {
      await api(`/api/conducteurs/${c.id}`, { method: "DELETE" });
      addToast({ type: "succes", titre: "Conducteur supprimé" });
      charger();
    } catch (err: any) {
      addToast({ type: "erreur", titre: "Suppression refusée", message: err.message });
    }
  }

  const liste = (items || []).slice().sort((a, b) => (a[tri] || "").localeCompare(b[tri] || ""));

  return (
    <div className="flex h-full flex-col gap-4">{/* §0octies decies L1 — barre horizontale toujours visible */}
      <PageHeader titre="Conducteurs"
        sousTitre={`Référentiel unique — toute modification est diffusée automatiquement vers Suivi, Missions, Dashboard, Alertes, Historique · ${items?.length ?? "…"} chauffeurs`}
        actions={<>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Rechercher (nom, matricule)…"
            className="w-56 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-[13px]" />
          <select value={statut} onChange={(e) => setStatut(e.target.value)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="">Tous statuts</option>
            {["ACTIF", "SUSPENDU", "CONGÉ", "INACTIF"].map((s) => <option key={s}>{s}</option>)}
          </select>
          <select value={tri} onChange={(e) => setTri(e.target.value as any)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="prenom_usuel">Trier : prénom usuel</option>
            <option value="nom_prenom">Trier : nom complet</option>
            <option value="matricule">Trier : matricule</option>
          </select>
          {ecriture && (
            <Btn onClick={() => setForm({ statut: "ACTIF" })}><Icon nom="plus" /> Ajouter</Btn>
          )}
        </>} />

      <Card className="!p-0 overflow-hidden flex min-h-0 flex-1 flex-col" contenuClasse="overflow-auto !p-0" titre={`${liste.length} chauffeurs`}>
        {!items ? (
          <div className="flex h-40 items-center justify-center gap-2 text-slate-400"><Spinner /></div>
        ) : liste.length === 0 ? (
          <Vide texte="Aucun chauffeur trouvé." />
        ) : (
          <div>
            <table className="table-pro">
              <thead><tr>
                <th>Matricule</th><th>Nom et Prénom</th><th>Prénom usuel</th><th>Téléphone</th>
                <th>Véhicule affecté</th><th>Statut</th><th>Créé le</th>{ecriture && <th className="w-28">Actions</th>}
              </tr></thead>
              <tbody>
                {liste.map((c) => (
                  <tr key={c.id}>
                    <td className="font-mono text-[12px] text-slate-400">{c.matricule}</td>
                    <td className="font-medium">{c.nom_prenom}</td>
                    <td className="font-bold">{c.prenom_usuel}</td>
                    <td className="tabular-nums text-[12.5px]">{c.telephone || <span className="text-slate-400">non renseigné</span>}</td>
                    <td>{c.vehicule_plaque ? <Badge>{c.vehicule_plaque}</Badge> : <span className="text-slate-400">—</span>}</td>
                    <td><Badge couleur={COULEURS_STATUT[c.statut]}>{c.statut}</Badge></td>
                    <td className="text-[12px] text-slate-400">{c.date_creation?.slice(0, 10).split("-").reverse().join("/")}</td>
                    {ecriture && (
                      <td className="whitespace-nowrap">
                        <Btn variante="fantome" className="!px-2 !py-0.5 text-[11.5px]" onClick={() => setForm(c)}>Modifier</Btn>
                        {admin && (
                          <Btn variante="fantome" className="!px-2 !py-0.5 text-[11.5px] text-red-500" onClick={() => supprimer(c)}>Suppr.</Btn>
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
        titre={form?.id ? `Modifier ${form.prenom_usuel}` : "Nouveau conducteur"}>
        {form && (
          <form onSubmit={sauvegarder} className="space-y-3">
            <Champ label="Nom et Prénom (complet)">
              <input required className={inputCls} value={form.nom_prenom || ""}
                onChange={(e) => setForm({ ...form, nom_prenom: e.target.value })} />
            </Champ>
            <div className="grid grid-cols-2 gap-3">
              <Champ label="Prénom usuel">
                <input required className={inputCls} value={form.prenom_usuel || ""}
                  onChange={(e) => setForm({ ...form, prenom_usuel: e.target.value })} />
              </Champ>
              <Champ label="Téléphone">
                <input className={inputCls} value={form.telephone || ""}
                  onChange={(e) => setForm({ ...form, telephone: e.target.value })} />
              </Champ>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Champ label="Matricule (auto si vide)">
                <input className={inputCls} value={form.matricule || ""} disabled={!!form.id}
                  onChange={(e) => setForm({ ...form, matricule: e.target.value })} />
              </Champ>
              <Champ label="Statut">
                <select className={inputCls} value={form.statut || "ACTIF"}
                  onChange={(e) => setForm({ ...form, statut: e.target.value })}>
                  {["ACTIF", "SUSPENDU", "CONGÉ", "INACTIF"].map((s) => <option key={s}>{s}</option>)}
                </select>
              </Champ>
            </div>
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
