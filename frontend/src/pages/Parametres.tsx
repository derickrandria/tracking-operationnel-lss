/** Paramètres (Administrateur) : seuils réglementaires sans code (§5.8/§7.4),
 * référentiel Situations (Annexe C), utilisateurs, journal d'audit (§11). */
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import Icon from "../components/icons";
import { Badge, Btn, Card, Champ, inputCls, Modal, PageHeader, Spinner } from "../components/ui";
import { addToast } from "../components/toast";
import { cls, fmtDateHeure, fmtDuree } from "../utils";

/** Convertit "4:30" / "90" → secondes, et réciproquement. */
function versSaisie(p: any): string {
  if (p.type_valeur === "DUREE_S") {
    const v = Math.round(p.valeur);
    return `${Math.floor(v / 3600)}:${String(Math.floor((v % 3600) / 60)).padStart(2, "0")}`;
  }
  return String(p.valeur);
}
function depuisSaisie(p: any, s: string): number | null {
  if (p.type_valeur === "DUREE_S") {
    const m = s.trim().match(/^(\d{1,3})\s*[:h]\s*(\d{1,2})?$/);
    if (!m) return null;
    return parseInt(m[1]) * 3600 + (parseInt(m[2] || "0") * 60);
  }
  const n = parseFloat(s);
  return isNaN(n) ? null : n;
}

function CarteSeuil({ p, onMaj }: { p: any; onMaj: () => void }) {
  const [val, setVal] = useState(versSaisie(p));
  const [dirty, setDirty] = useState(false);
  async function enregistrer() {
    const v = depuisSaisie(p, val);
    if (v === null || v <= 0) {
      addToast({ type: "erreur", titre: "Valeur invalide", message: p.type_valeur === "DUREE_S" ? "Format attendu : 4:30" : "Nombre attendu" });
      return;
    }
    await api(`/api/parametres/${p.cle}`, { method: "PATCH", body: JSON.stringify({ valeur: v }) });
    addToast({ type: "succes", titre: "Seuil mis à jour", message: `${p.cle} pris en compte immédiatement par le moteur de calcul.` });
    setDirty(false);
    onMaj();
  }
  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-800 p-3.5 bg-white dark:bg-nuit-900">
      <div className="flex items-center justify-between gap-2">
        <div className="font-mono text-[12px] font-bold text-blue-500">{p.cle}</div>
        {p.type_valeur === "DUREE_S" && <Badge>{fmtDuree(p.valeur)}</Badge>}
      </div>
      <p className="mt-1 min-h-[30px] text-[11.5px] leading-snug text-slate-400">{p.description}</p>
      <div className="mt-2 flex items-center gap-2">
        <input className={cls(inputCls, "w-24 text-center tabular-nums")} value={val}
          onChange={(e) => { setVal(e.target.value); setDirty(true); }} />
        {p.type_valeur === "DUREE_S" && <span className="text-[11px] text-slate-400">h:min</span>}
        {dirty && <Btn className="!px-2.5 !py-1 text-[11.5px]" onClick={enregistrer}>Enregistrer</Btn>}
      </div>
    </div>
  );
}

export default function Parametres() {
  const [params, setParams] = useState<any[] | null>(null);
  const [situations, setSituations] = useState<any[] | null>(null);
  const [users, setUsers] = useState<any[] | null>(null);
  const [audit, setAudit] = useState<any[] | null>(null);
  const [onglet, setOnglet] = useState<"seuils" | "situations" | "utilisateurs" | "audit">("seuils");
  const [nouvelleSituation, setNouvelleSituation] = useState("");
  const [formUser, setFormUser] = useState<any | null>(null);

  async function charger() {
    api("/api/parametres").then(setParams).catch(() => setParams([]));
    api("/api/situations").then(setSituations).catch(() => setSituations([]));
    api("/api/utilisateurs").then(setUsers).catch(() => setUsers([]));
    api("/api/audit?limite=60").then(setAudit).catch(() => setAudit([]));
  }
  useEffect(() => { charger(); }, []);

  async function ajouterSituation(e: FormEvent) {
    e.preventDefault();
    if (!nouvelleSituation.trim()) return;
    try {
      await api("/api/situations", { method: "POST", body: JSON.stringify({ libelle: nouvelleSituation.trim() }) });
      setNouvelleSituation("");
      charger();
      addToast({ type: "succes", titre: "Situation ajoutée" });
    } catch (err: any) {
      addToast({ type: "erreur", titre: "Ajout impossible", message: err.message });
    }
  }

  async function basculerSituation(s: any) {
    await api(`/api/situations/${s.id}`, { method: "PATCH", body: JSON.stringify({ actif: !s.actif }) });
    charger();
  }

  async function creerUser(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/api/utilisateurs", { method: "POST", body: JSON.stringify(formUser) });
      setFormUser(null);
      charger();
      addToast({ type: "succes", titre: "Utilisateur créé" });
    } catch (err: any) {
      addToast({ type: "erreur", titre: "Création impossible", message: err.message });
    }
  }

  const ONGLETS = [
    ["seuils", "Seuils réglementaires"], ["situations", "Situations camion"],
    ["utilisateurs", "Utilisateurs"], ["audit", "Journal d'audit"],
  ] as const;

  return (
    <div className="flex flex-col gap-4">{/* §0octies decies L1 — mise au point v1.42 : page multi-sections → défilement vertical DE PAGE ; listes bornées à 60 % d'écran avec barres internes */}
      <PageHeader titre="Paramètres"
        sousTitre="Configuration de la plateforme — modifications appliquées sans redémarrage ni modification du code" />

      <div className="flex gap-1 rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-nuit-900 p-1 w-fit">
        {ONGLETS.map(([k, v]) => (
          <button key={k} onClick={() => setOnglet(k)}
            className={cls("rounded-lg px-3.5 py-1.5 text-[13px] font-medium transition",
              onglet === k ? "bg-blue-600 text-white shadow" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800")}>
            {v}
          </button>
        ))}
      </div>

      {onglet === "seuils" && (
        !params ? <Spinner /> : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {params.map((p) => <CarteSeuil key={p.cle} p={p} onMaj={charger} />)}
          </div>
        )
      )}

      {onglet === "situations" && (
        <Card titre={`Référentiel Situations (Annexe C) — ${situations?.length ?? 0} valeurs`}>
          <form onSubmit={ajouterSituation} className="mb-3 flex gap-2">
            <input className={cls(inputCls, "max-w-md")} value={nouvelleSituation}
              onChange={(e) => setNouvelleSituation(e.target.value)}
              placeholder="Nouvelle situation (ex : En transit pour livraison TANA-MGR)" />
            <Btn type="submit"><Icon nom="plus" /> Ajouter</Btn>
          </form>
          {!situations ? <Spinner /> : (
            <div className="grid grid-cols-1 gap-1 sm:grid-cols-2 xl:grid-cols-3">
              {situations.map((s) => (
                <div key={s.id} className={cls("flex items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-[12.5px]",
                  s.actif ? "border-slate-200 dark:border-slate-800" : "border-dashed border-slate-300 dark:border-slate-700 opacity-50")}>
                  <span className="truncate" title={s.libelle}>{s.libelle}</span>
                  <button onClick={() => basculerSituation(s)}
                    className={cls("shrink-0 rounded-md px-2 py-0.5 text-[11px] font-semibold",
                      s.actif ? "bg-emerald-500/15 text-emerald-500" : "bg-slate-500/15 text-slate-400")}>
                    {s.actif ? "Actif" : "Inactif"}
                  </button>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {onglet === "utilisateurs" && (
        <Card contenuClasse="overflow-auto max-h-[60vh]" titre="Comptes utilisateurs (RBAC §2)"
          actions={<Btn onClick={() => setFormUser({ role: "CONSULTATION" })}><Icon nom="plus" /> Créer un compte</Btn>}>
          {!users ? <Spinner /> : (
            <table className="table-pro">
              <thead><tr><th>Utilisateur</th><th>Nom complet</th><th>Rôle</th><th>Actif</th><th>Créé le</th><th></th></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td className="font-bold">{u.username}</td>
                    <td>{u.nom_complet}</td>
                    <td><Badge couleur={u.role === "ADMIN" ? "bg-violet-500/15 text-violet-500 border-violet-500/40" : u.role === "TRACKING" ? "bg-blue-500/15 text-blue-500 border-blue-500/40" : "bg-slate-500/15 text-slate-400 border-slate-500/40"}>{u.role}</Badge></td>
                    <td>{u.actif ? "✔" : "—"}</td>
                    <td className="text-[12px] text-slate-400">{u.date_creation?.slice(0, 10)}</td>
                    <td>
                      <Btn variante="fantome" className="!px-2 !py-0.5 text-[11.5px]"
                        onClick={async () => {
                          await api(`/api/utilisateurs/${u.id}`, { method: "PATCH", body: JSON.stringify({ actif: !u.actif }) });
                          charger();
                        }}>
                        {u.actif ? "Désactiver" : "Réactiver"}
                      </Btn>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {onglet === "audit" && (
        <Card contenuClasse="!p-0" titre="Journal d'audit — actions sensibles (§11)">
          {!audit ? <Spinner /> : (
            <div className="overflow-x-auto max-h-[60vh] overflow-y-auto">
              <table className="table-pro">
                <thead><tr><th>Date/heure</th><th>Utilisateur</th><th>Action</th><th>Entité</th><th>Détails (avant → après)</th></tr></thead>
                <tbody>
                  {audit.map((a) => (
                    <tr key={a.id}>
                      <td className="whitespace-nowrap tabular-nums text-[12px]">{fmtDateHeure(a.date_heure)}</td>
                      <td className="font-medium">{a.username}</td>
                      <td className="whitespace-nowrap"><Badge>{a.action}</Badge></td>
                      <td className="text-[12px]">{a.entite}</td>
                      <td className="max-w-[480px] text-[11.5px] text-slate-400">
                        <code className="break-all">{JSON.stringify(a.details)}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Création d'utilisateur */}
      <Modal ouvert={!!formUser} onFermer={() => setFormUser(null)} titre="Nouveau compte utilisateur">
        {formUser && (
          <form onSubmit={creerUser} className="space-y-3">
            <Champ label="Nom d'utilisateur">
              <input required className={inputCls} value={formUser.username || ""}
                onChange={(e) => setFormUser({ ...formUser, username: e.target.value })} />
            </Champ>
            <Champ label="Nom complet">
              <input required className={inputCls} value={formUser.nom_complet || ""}
                onChange={(e) => setFormUser({ ...formUser, nom_complet: e.target.value })} />
            </Champ>
            <div className="grid grid-cols-2 gap-3">
              <Champ label="Mot de passe">
                <input required type="password" className={inputCls} value={formUser.password || ""}
                  onChange={(e) => setFormUser({ ...formUser, password: e.target.value })} />
              </Champ>
              <Champ label="Rôle">
                <select className={inputCls} value={formUser.role}
                  onChange={(e) => setFormUser({ ...formUser, role: e.target.value })}>
                  <option value="CONSULTATION">Consultation (lecture seule)</option>
                  <option value="TRACKING">Responsable Tracking</option>
                  <option value="ADMIN">Administrateur</option>
                </select>
              </Champ>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Btn variante="secondaire" onClick={() => setFormUser(null)}>Annuler</Btn>
              <Btn type="submit"><Icon nom="verifier" /> Créer</Btn>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}
