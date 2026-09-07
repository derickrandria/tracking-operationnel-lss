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

const COULEURS_STATUT_OP: Record<string, string> = {
  "En mission (Vide)": "bg-sky-500/15 text-sky-600 dark:text-sky-400 border-sky-500/40",
  "En mission (Chargé)": "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/40",
  "Disponible": "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40",
  "En repos": "bg-slate-500/15 text-slate-600 dark:text-slate-400 border-slate-500/40",
};

export default function Conducteurs() {
  const [params] = useSearchParams();
  const [items, setItems] = useState<Conducteur[] | null>(null);
  const [q, setQ] = useState(params.get("q") || "");
  const [statut, setStatut] = useState("");
  const [filtreType, setFiltreType] = useState<"TOUS" | "MZONEX" | "CAMTRACK">("TOUS");
  const [tri, setTri] = useState<"prenom_usuel" | "nom_prenom" | "matricule">("prenom_usuel");
  const [form, setForm] = useState<Partial<Conducteur> | null>(null);
  const [fusionSource, setFusionSource] = useState<Conducteur | null>(null);
  const [fusionCibleId, setFusionCibleId] = useState<string>("");
  const [creerAliasFusion, setCreerAliasFusion] = useState<boolean>(true);
  const [enFusion, setEnFusion] = useState<boolean>(false);
  const [nouvelAlias, setNouvelAlias] = useState<string>("");

  const ecriture = ["ADMIN", "TRACKING"].includes(getUser()?.role || "");
  const admin = getUser()?.role === "ADMIN";

  async function charger() {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (statut) p.set("statut", statut);
    try {
      const res = await api(`/api/conducteurs?${p}`);
      setItems(res || []);
    } catch (err) {
      console.error("Erreur chargement conducteurs:", err);
      setItems([]);
    }
  }
  useEffect(() => {
    charger();
  }, []);
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

  async function executerFusion(e: FormEvent) {
    e.preventDefault();
    if (!fusionSource || !fusionCibleId) return;
    setEnFusion(true);
    try {
      await api("/api/conducteurs/fusionner", {
        method: "POST",
        body: JSON.stringify({
          source_id: fusionSource.id,
          cible_id: fusionCibleId,
          creer_alias: creerAliasFusion,
        }),
      });
      addToast({
        type: "succes",
        titre: "Fusion réussie",
        message: `Les trajets et suivis de ${fusionSource.nom_prenom} ont été réassignés.`,
      });
      setFusionSource(null);
      setFusionCibleId("");
      charger();
    } catch (err: any) {
      addToast({ type: "erreur", titre: "Échec de la fusion", message: err.message });
    } finally {
      setEnFusion(false);
    }
  }

  async function ajouterAliasHandler(cid: string) {
    if (!nouvelAlias.trim()) return;
    try {
      await api(`/api/conducteurs/${cid}/aliases`, {
        method: "POST",
        body: JSON.stringify({ alias: nouvelAlias.trim() }),
      });
      addToast({ type: "succes", titre: "Alias ajouté" });
      setNouvelAlias("");
      charger();
      if (form && form.id === cid) {
        const frais = (items || []).find((x) => x.id === cid);
        if (frais) setForm(frais);
      }
    } catch (err: any) {
      addToast({ type: "erreur", titre: "Impossible d'ajouter l'alias", message: err.message });
    }
  }

  async function supprimerAliasHandler(cid: string, aid: string) {
    try {
      await api(`/api/conducteurs/${cid}/aliases/${aid}`, { method: "DELETE" });
      addToast({ type: "succes", titre: "Alias supprimé" });
      charger();
      if (form && form.id === cid) {
        setForm({
          ...form,
          aliases: (form.aliases || []).filter((a) => a.id !== aid),
        });
      }
    } catch (err: any) {
      addToast({ type: "erreur", titre: "Suppression refusée", message: err.message });
    }
  }

  const listeFiltree = (items || []).filter((c) => {
    const code = c.code_badge_mzonex || c.matricule;
    if (filtreType === "MZONEX") return !!code;
    if (filtreType === "CAMTRACK") return !code;
    return true;
  });

  const liste = listeFiltree.slice().sort((a, b) => {
    if (tri === "matricule") {
      const codeA = a.code_badge_mzonex?.toString() || a.matricule || "";
      const codeB = b.code_badge_mzonex?.toString() || b.matricule || "";
      return codeA.localeCompare(codeB);
    }
    return (a[tri] || "").localeCompare(b[tri] || "");
  });

  return (
    <div className="flex h-full flex-col gap-4">{/* §0octies decies L1 — barre horizontale toujours visible */}
      <PageHeader titre="Conducteurs"
        sousTitre={`Référentiel unique — gestion anti-doublons et identification multi-portails (driverKeyCode MZoneX & CamtrackPro) · ${items?.length ?? "…"} chauffeurs`}
        actions={<>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Rechercher (nom, code, alias)…"
            className="w-56 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-[13px]" />
          <select value={statut} onChange={(e) => setStatut(e.target.value)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="">Tous statuts</option>
            {["ACTIF", "SUSPENDU", "CONGÉ", "INACTIF"].map((s) => <option key={s}>{s}</option>)}
          </select>
          <select value={filtreType} onChange={(e) => setFiltreType(e.target.value as any)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="TOUS">Tous les chauffeurs</option>
            <option value="MZONEX">MZoneX (avec driverKeyCode)</option>
            <option value="CAMTRACK">CamtrackPro (sans code)</option>
          </select>
          <select value={tri} onChange={(e) => setTri(e.target.value as any)} className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-[13px]">
            <option value="prenom_usuel">Trier : prénom usuel</option>
            <option value="nom_prenom">Trier : nom complet</option>
            <option value="matricule">Trier : driverKeyCode</option>
          </select>
          {ecriture && (
            <Btn onClick={() => setForm({ statut: "ACTIF" })}><Icon nom="plus" /> Ajouter</Btn>
          )}
        </>} />

      <Card className="!p-0 overflow-hidden flex min-h-0 flex-1 flex-col" contenuClasse="overflow-auto !p-0" titre={`${liste.length} chauffeurs (${items?.length ?? 0} au total)`}>
        {!items ? (
          <div className="flex h-40 items-center justify-center gap-2 text-slate-400"><Spinner /></div>
        ) : liste.length === 0 ? (
          <Vide texte="Aucun chauffeur trouvé." />
        ) : (
          <div>
            <table className="table-pro">
              <thead><tr>
                <th>driverKeyCode</th>
                <th>Nom et Prénom (complet)</th>
                <th>Prénom usuel</th>
                <th>Alias connus</th>
                <th>Téléphone</th>
                <th>Véhicule affecté</th>
                <th>Activité Opérationnelle</th>
                <th>Statut Fiche</th>
                <th>Créé le</th>
                {ecriture && <th className="w-36 text-center">Actions</th>}
              </tr></thead>
              <tbody>
                {liste.map((c) => {
                  const codeAffiche = c.code_badge_mzonex || (c.matricule && !c.matricule.startsWith("CH") && !c.matricule.startsWith("AUTO-") ? c.matricule : null);
                  return (
                    <tr key={c.id}>
                      <td className="font-mono text-[12px]">
                        {codeAffiche ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11.5px] font-semibold bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20">
                            {codeAffiche}
                          </span>
                        ) : (
                          <span className="text-slate-400 italic text-[11.5px]">—</span>
                        )}
                      </td>
                      <td>
                        <div className="font-medium text-slate-900 dark:text-slate-100">{c.nom_prenom}</div>
                      </td>
                      <td className="font-bold text-slate-700 dark:text-slate-300">{c.prenom_usuel}</td>
                      <td>
                        {c.aliases && c.aliases.length > 0 ? (
                          <div className="flex flex-wrap gap-1 max-w-xs">
                            {c.aliases.map((a) => (
                              <span key={a.id} className="inline-block px-1.5 py-0.2 rounded text-[10.5px] bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border border-slate-200 dark:border-slate-700">
                                {a.alias_brut}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-slate-400 text-[11px]">—</span>
                        )}
                      </td>
                      <td className="tabular-nums text-[12.5px]">{c.telephone || <span className="text-slate-400">non renseigné</span>}</td>
                      <td>{c.vehicule_plaque ? <Badge>{c.vehicule_plaque}</Badge> : <span className="text-slate-400">—</span>}</td>
                      <td>
                        {c.statut_operationnel ? (
                          <Badge couleur={COULEURS_STATUT_OP[c.statut_operationnel] || "bg-slate-500/15 text-slate-500"}>
                            {c.statut_operationnel}
                          </Badge>
                        ) : (
                          <span className="text-slate-400 text-[11px]">—</span>
                        )}
                      </td>
                      <td><Badge couleur={COULEURS_STATUT[c.statut]}>{c.statut}</Badge></td>
                      <td className="text-[12px] text-slate-400">{c.date_creation?.slice(0, 10).split("-").reverse().join("/")}</td>
                      {ecriture && (
                        <td className="whitespace-nowrap text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Btn variante="fantome" className="!px-2 !py-0.5 text-[11.5px]" onClick={() => setForm(c)}>
                              Modifier
                            </Btn>
                            <Btn variante="secondaire" className="!px-2 !py-0.5 text-[11.5px] text-indigo-600 dark:text-indigo-400" onClick={() => { setFusionSource(c); setFusionCibleId(""); }}>
                              Fusionner
                            </Btn>
                            <Btn variante="fantome" className="!px-1.5 !py-0.5 text-[11.5px] text-red-500 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950/30" onClick={() => supprimer(c)}>
                              Supprimer
                            </Btn>
                          </div>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* MODAL CRÉATION / ÉDITION */}
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
              <Champ label="driverKeyCode MZoneX (laisser vide si CamtrackPro)">
                <input type="number" className={inputCls} value={form.code_badge_mzonex ?? (form.matricule && !isNaN(Number(form.matricule)) ? Number(form.matricule) : "")}
                  placeholder="Ex. 10583 (laisser vide pour CamtrackPro)"
                  onChange={(e) => {
                    const val = e.target.value.trim();
                    const num = val ? parseInt(val, 10) : null;
                    setForm({ ...form, code_badge_mzonex: num, matricule: val || null });
                  }} />
              </Champ>
              <Champ label="Statut">
                <select className={inputCls} value={form.statut || "ACTIF"}
                  onChange={(e) => setForm({ ...form, statut: e.target.value })}>
                  {["ACTIF", "SUSPENDU", "CONGÉ", "INACTIF"].map((s) => <option key={s}>{s}</option>)}
                </select>
              </Champ>
            </div>

            {/* Gestion des Alias si le conducteur existe déjà */}
            {form.id && (
              <div className="pt-2 border-t border-slate-200 dark:border-slate-800">
                <div className="text-[12.5px] font-semibold text-slate-700 dark:text-slate-300 mb-2">
                  Alias et variantes d'écriture reconnues
                </div>
                <div className="space-y-1.5 max-h-32 overflow-y-auto mb-2">
                  {(form.aliases || []).length === 0 ? (
                    <div className="text-[11.5px] text-slate-400 italic">Aucun alias associé.</div>
                  ) : (
                    (form.aliases || []).map((a) => (
                      <div key={a.id} className="flex items-center justify-between px-2 py-1 bg-slate-50 dark:bg-slate-800/60 rounded border border-slate-200 dark:border-slate-700 text-[12px]">
                        <span className="font-medium text-slate-700 dark:text-slate-300">{a.alias_brut}</span>
                        <button type="button" onClick={() => supprimerAliasHandler(form.id!, a.id)} className="text-red-500 hover:text-red-700 text-[11px] font-bold">
                          ✕
                        </button>
                      </div>
                    ))
                  )}
                </div>
                <div className="flex gap-2">
                  <input value={nouvelAlias} onChange={(e) => setNouvelAlias(e.target.value)} placeholder="Ajouter une variante (ex. nom portail)…"
                    className="flex-1 rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1 text-[12px]" />
                  <Btn type="button" variante="secondaire" className="!px-2.5 !py-1 text-[11.5px]" onClick={() => ajouterAliasHandler(form.id!)}>
                    Ajouter
                  </Btn>
                </div>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-3">
              <Btn variante="secondaire" onClick={() => setForm(null)}>Annuler</Btn>
              <Btn type="submit"><Icon nom="verifier" /> Enregistrer</Btn>
            </div>
          </form>
        )}
      </Modal>

      {/* MODAL FUSION DE CHAUFFEURS */}
      <Modal ouvert={!!fusionSource} onFermer={() => setFusionSource(null)}
        titre={`Fusionner ${fusionSource?.nom_prenom || "le chauffeur"}`}>
        {fusionSource && (
          <form onSubmit={executerFusion} className="space-y-4">
            <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg text-[12.5px] text-amber-800 dark:text-amber-300">
              <div className="font-bold mb-1">Chauffeur source à fusionner :</div>
              <div>• Nom : <strong>{fusionSource.nom_prenom}</strong></div>
              <div>• driverKeyCode : <strong>{fusionSource.code_badge_mzonex || fusionSource.matricule || "—"}</strong></div>
              <div>• Véhicule affecté : <strong>{fusionSource.vehicule_plaque || "Aucun"}</strong></div>
            </div>

            <Champ label="Chauffeur cible (destinataire de tous les trajets/suivis)">
              <select required className={inputCls} value={fusionCibleId} onChange={(e) => setFusionCibleId(e.target.value)}>
                <option value="">Sélectionner le chauffeur cible…</option>
                {(items || [])
                  .filter((c) => c.id !== fusionSource.id)
                  .sort((a, b) => a.nom_prenom.localeCompare(b.nom_prenom))
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.nom_prenom} {c.code_badge_mzonex || c.matricule ? `[Code: ${c.code_badge_mzonex || c.matricule}]` : ""}
                    </option>
                  ))}
              </select>
            </Champ>

            <div className="flex items-center gap-2 pt-1">
              <input type="checkbox" id="creerAliasChk" checked={creerAliasFusion}
                onChange={(e) => setCreerAliasFusion(e.target.checked)} className="rounded border-slate-300" />
              <label htmlFor="creerAliasChk" className="text-[12.5px] text-slate-700 dark:text-slate-300 cursor-pointer">
                Conserver « <strong>{fusionSource.nom_prenom}</strong> » comme alias permanent du chauffeur cible
              </label>
            </div>

            <div className="text-[11.5px] text-slate-500 dark:text-slate-400">
              ℹ️ Après la fusion, la fiche source sera supprimée. Tous les trajets, temps de conduite, infractions et suivis seront immédiatement réassignés au chauffeur cible.
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Btn type="button" variante="secondaire" onClick={() => setFusionSource(null)}>Annuler</Btn>
              <Btn type="submit" disabled={!fusionCibleId || enFusion} className="!bg-indigo-600 hover:!bg-indigo-700 text-white">
                {enFusion ? <Spinner /> : <Icon nom="verifier" />} Confirmer la fusion
              </Btn>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}
