/** Module 4 — Infractions (§0quinquies decies I1→I4, arbitrages LSS 25/08/2026).
 *  Source unique : Ym@ne (MZoneX) — niveaux ALERTE/ALARME pré-filtrés (I1/I2).
 *  Les 10 colonnes dictées à l'identique (I3) ; workflow de validation
 *  Valide/Invalide avec observation obligatoire (I4) — jamais de suppression,
 *  invalidées exclues des totaux et des exports, visibles à l'écran. */
import { useEffect, useRef, useState } from "react";
import { api, download, getUser } from "../api";
import Icon from "../components/icons";
import { Badge, Btn, Card, Champ, inputCls, Modal, PageHeader, Spinner, Vide } from "../components/ui";
import { addToast } from "../components/toast";
import { CompteursInfractions, Conducteur, Infraction, Vehicule } from "../types";
import { cls } from "../utils";
import { on } from "../ws";

const COULEURS_NIVEAU: Record<string, string> = {
  ALARME: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30",
  ALERTE: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
};

const COULEURS_VALIDATION: Record<string, string> = {
  NON_TRAITEE: "bg-slate-500/10 text-slate-600 dark:text-slate-300 border-slate-400/30",
  VALIDE: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
  INVALIDE: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30",
};

const LABELS_VALIDATION: Record<string, string> = {
  NON_TRAITEE: "Non traitée",
  VALIDE: "Valide",
  INVALIDE: "Invalide",
};

export default function Infractions() {
  const [du, setDu] = useState("");
  const [au, setAu] = useState("");
  const [vehiculeId, setVehiculeId] = useState("");
  const [conducteurId, setConducteurId] = useState("");
  const [niveau, setNiveau] = useState("");
  const [validation, setValidation] = useState("");
  const [famille, setFamille] = useState("");              // §0octies decies L3 (27/08) — filtre type d'infraction
  const [familles, setFamilles] = useState<string[]>([]);
  const [data, setData] = useState<{ items: Infraction[]; total: number; compteurs: CompteursInfractions } | null>(null);
  const [vehicules, setVehicules] = useState<Vehicule[]>([]);
  const [conducteurs, setConducteurs] = useState<Conducteur[]>([]);
  const [cible, setCible] = useState<Infraction | null>(null);   // invalidation en cours
  const [observation, setObservation] = useState("");
  const [enCours, setEnCours] = useState(false);
  const [enRelance, setEnRelance] = useState(false);          // §0septies decies K2
  const timer = useRef<number>();
  const peutValider = ["ADMIN", "TRACKING"].includes(getUser()?.role || "");

  function query() {
    const p = new URLSearchParams();
    if (du) p.set("du", du);
    if (au) p.set("au", au);
    if (vehiculeId) p.set("vehicule_id", vehiculeId);
    if (conducteurId) p.set("conducteur_id", conducteurId);
    if (niveau) p.set("niveau", niveau);
    if (validation) p.set("validation", validation);
    if (famille) p.set("famille", famille);
    return p.toString();
  }

  async function charger() {
    try {
      const res = await api(`/api/infractions?${query()}`);
      setData(res || { items: [], total: 0, compteurs: { non_traitees: 0, validees: 0, invalidees: 0, comptabilisees: 0 } as any });
    } catch (e) {
      console.error("Erreur chargement infractions:", e);
      setData({ items: [], total: 0, compteurs: { non_traitees: 0, validees: 0, invalidees: 0, comptabilisees: 0 } as any });
    }
  }

  useEffect(() => { charger(); }, [du, au, vehiculeId, conducteurId, niveau, validation, famille]);
  useEffect(() => {
    api("/api/vehicules").then(setVehicules).catch(() => {});
    api("/api/conducteurs").then(setConducteurs).catch(() => {});
    api("/api/infractions/familles").then(setFamilles).catch(() => {});
  }, []);

  useEffect(() => {
    const off = on("infraction.new", () => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(charger, 1200);
    });
    return off;
  }, []);

  async function decider(i: Infraction, decision: "VALIDE" | "INVALIDE", obs?: string) {
    setEnCours(true);
    try {
      await api(`/api/infractions/${i.id}/validation`, {
        method: "POST",
        body: JSON.stringify({ decision, observation: obs ?? null }),
      });
      addToast({
        type: "succes",
        titre: decision === "VALIDE" ? "Infraction validée" : "Infraction invalidée",
        message: `${i.plaque ?? ""} — ${i.nom ?? ""}`.trim() || undefined,
      });
      setCible(null);
      setObservation("");
      await charger();
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Décision non enregistrée", message: e.message });
    } finally {
      setEnCours(false);
    }
  }

  function choisirDecision(i: Infraction, valeur: string) {
    if (valeur === i.validation) return;
    if (valeur === "INVALIDE") {           // I4 : observation OBLIGATOIRE
      setCible(i);
      setObservation(i.observation || "");
    } else if (valeur === "VALIDE") {
      decider(i, "VALIDE");                // Valide directement (re-décision possible)
    }
    // « Non traitée » = état initial affiché, pas un choix (I4 : Valide/Invalide).
  }

  // §0septies decies K2 (27/08/2026) — « Relancer la collecte maintenant » :
  // relance synchrone et idempotente, message serveur affiché tel quel.
  async function relancerCollecte() {
    setEnRelance(true);
    try {
      const r = await api("/api/infractions/ymane/rejouer", { method: "POST" });
      addToast({ type: r.ok ? "succes" : "erreur",
                 titre: r.ok ? "Collecte Ym@ne relancée" : "Collecte Ym@ne impossible",
                 message: r.message });
      await charger();
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Collecte Ym@ne impossible", message: e.message });
    } finally {
      setEnRelance(false);
    }
  }

  async function exporter(format: "xlsx" | "pdf") {
    try {
      const p = new URLSearchParams();
      if (du) p.set("du", du);
      if (au) p.set("au", au);
      await download(`/api/infractions/export.${format}?${p}`, `infractions.${format}`);
      addToast({ type: "succes", titre: `Export ${format.toUpperCase()} téléchargé` });
    } catch (e: any) {
      addToast({ type: "erreur", titre: "Export impossible", message: e.message });
    }
  }

  const c = data?.compteurs;

  return (
    <div className="flex h-full flex-col gap-4">{/* §0octies decies L1 — grille pleine hauteur (défilement interne, barre horizontale toujours visible) */}
      <PageHeader titre="Infractions"
        sousTitre={<>Source : <b>Ym@ne</b> (MZoneX) — infractions pré-filtrées <b>ALERTE/ALARME</b> (I1/I2 ; le niveau « enregistrement » ne compte jamais)
          {c && <> · <b>{c.non_traitees}</b> non traitée{c.non_traitees > 1 ? "s" : ""} · <b className="text-emerald-500">{c.validees}</b> validée{c.validees > 1 ? "s" : ""}
            {" "}· <b className="text-red-500">{c.invalidees}</b> invalidée{c.invalidees > 1 ? "s" : ""} (exclues des totaux/exports)
            {" "}· <b>{c.comptabilisees}</b> comptabilisée{c.comptabilisees > 1 ? "s" : ""}</>}</>}
        actions={<>
          <input type="date" value={du} onChange={(e) => setDu(e.target.value)} className={cls(inputCls, "!w-auto")} />
          <span className="text-slate-400 text-[12px]">au</span>
          <input type="date" value={au} onChange={(e) => setAu(e.target.value)} className={cls(inputCls, "!w-auto")} />
          <select value={vehiculeId} onChange={(e) => setVehiculeId(e.target.value)} className={cls(inputCls, "!w-auto")}>
            <option value="">Tous véhicules</option>
            {vehicules.map((v) => <option key={v.id} value={v.id}>{v.plaque}</option>)}
          </select>
          <select value={conducteurId} onChange={(e) => setConducteurId(e.target.value)} className={cls(inputCls, "!w-auto")}>
            <option value="">Tous chauffeurs</option>
            {conducteurs.map((x) => <option key={x.id} value={x.id}>{x.nom_prenom}</option>)}
          </select>
          <select value={niveau} onChange={(e) => setNiveau(e.target.value)} className={cls(inputCls, "!w-auto")}>
            <option value="">Tous niveaux</option>
            <option value="ALERTE">Alerte</option>
            <option value="ALARME">Alarme</option>
          </select>
          <select value={famille} onChange={(e) => setFamille(e.target.value)} className={cls(inputCls, "!w-auto")}>
            <option value="">Tous types d'infraction</option>
            {familles.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <select value={validation} onChange={(e) => setValidation(e.target.value)} className={cls(inputCls, "!w-auto")}>
            <option value="">Toutes validations</option>
            <option value="NON_TRAITEE">Non traitées</option>
            <option value="VALIDE">Valides</option>
            <option value="INVALIDE">Invalides</option>
          </select>
          {peutValider && (
            <Btn variante="secondaire" disabled={enRelance} onClick={relancerCollecte}
                 title="Relance immédiate de la collecte Ym@ne (sans attendre le cycle automatique de 15 minutes)">
              <Icon nom="alertes" /> {enRelance ? "Collecte en cours…" : "Relancer la collecte maintenant"}
            </Btn>
          )}
          <Btn variante="secondaire" onClick={() => exporter("xlsx")}><Icon nom="telecharger" /> Excel</Btn>
          <Btn variante="secondaire" onClick={() => exporter("pdf")}><Icon nom="telecharger" /> PDF</Btn>
        </>} />

      <Card className="!p-0 overflow-hidden flex min-h-0 flex-1 flex-col"
        contenuClasse="overflow-auto !p-0"
        titre={`${data?.total ?? 0} infractions affichées (période : 30 derniers jours par défaut)`}>
        {!data ? (
          <div className="flex h-40 items-center justify-center gap-2 text-slate-400"><Spinner /></div>
        ) : data.items.length === 0 ? (
          <Vide texte="Aucune infraction sur cette période / ces filtres." />
        ) : (
          <div>
            <table className="table-pro">
              <thead><tr>
                {/* I3 amendé §0octies decies L2 (27/08) — 12 colonnes */}
                <th>Date</th><th>Heure</th><th>Immatriculation</th><th>Chauffeur</th>
                <th>Infraction</th><th>Niveau</th><th>Seuil</th><th>Coordonnées GPS</th>
                <th>Début de l'infraction</th><th>Fin de l'infraction</th>
                <th>Validation</th><th>Observation</th>
              </tr></thead>
              <tbody>
                {data.items.map((i) => (
                  <tr key={i.id} className={cls(i.validation === "INVALIDE" && "opacity-70")}>
                    <td className="whitespace-nowrap">{i.date_jour.split("-").reverse().join("/")}</td>
                    <td className="whitespace-nowrap tabular-nums">{i.heure?.slice(0, 8)}</td>
                    <td className="whitespace-nowrap font-bold">{i.plaque || "—"}</td>
                    <td className="whitespace-nowrap font-medium">{i.chauffeur_affiche || "—"}</td>
                    <td className="max-w-[260px]">
                      <span className="flex items-start gap-1.5">
                        <Icon nom={i.type === "EXCES_VITESSE" ? "vitesse" : "infractions"} className="w-3.5 h-3.5 mt-0.5 text-slate-400 shrink-0" />
                        <span>{i.nom || "—"}</span>
                      </span>
                    </td>
                    <td>{i.niveau ? <Badge couleur={COULEURS_NIVEAU[i.niveau]}>{i.niveau}</Badge> : "—"}</td>
                    <td className="whitespace-nowrap tabular-nums">{i.seuil_libelle || "—"}</td>
                    <td className="whitespace-nowrap text-[12px] tabular-nums text-slate-500 dark:text-slate-400">
                      {i.coordonnees ? (
                        <a className="underline decoration-dotted hover:text-blue-500"
                          href={`https://www.openstreetmap.org/?mlat=${i.coordonnees.split(",")[0]}&mlon=${i.coordonnees.split(",")[1]}#map=16/${i.coordonnees.split(",")[0]}/${i.coordonnees.split(",")[1]}`}
                          target="_blank" rel="noreferrer" title="Voir sur la carte">
                          {i.coordonnees}
                        </a>
                      ) : "—"}
                    </td>
                    <td className="whitespace-nowrap tabular-nums">{/* L2 — verbatim Ym@ne, jamais d'invention */}
                      {i.date_jour ? `${i.date_jour.split("-").reverse().join("/")} ${i.heure?.slice(0, 8) || ""}` : "—"}
                    </td>
                    <td className="whitespace-nowrap tabular-nums">
                      {i.date_fin ? `${i.date_fin.split("-").reverse().join("/")} ${i.heure_fin?.slice(0, 8) || ""}` : "—"}
                    </td>
                    <td className="whitespace-nowrap">
                      {peutValider ? (
                        <select value={i.validation} disabled={enCours}
                          onChange={(e) => choisirDecision(i, e.target.value)}
                          className={cls("rounded-md border px-1.5 py-1 text-[12px] font-semibold",
                            COULEURS_VALIDATION[i.validation])}>
                          <option value="NON_TRAITEE" disabled>Non traitée</option>
                          <option value="VALIDE">Valide</option>
                          <option value="INVALIDE">Invalide</option>
                        </select>
                      ) : (
                        <Badge couleur={COULEURS_VALIDATION[i.validation]}>
                          {LABELS_VALIDATION[i.validation]}
                        </Badge>
                      )}
                      {i.validee_par && (
                        <div className="mt-0.5 text-[10px] text-slate-400" title={`${i.validee_par} — ${i.validee_le ?? ""}`}>
                          par {i.validee_par}
                        </div>
                      )}
                    </td>
                    <td className="max-w-[220px] text-[12px] text-slate-500 dark:text-slate-400">
                      <span title={i.observation || ""}>{i.observation || "—"}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* I4 — l'invalidation exige une observation (cause) */}
      <Modal ouvert={!!cible} onFermer={() => setCible(null)}
        titre={`Invalider l'infraction — ${cible?.plaque ?? ""}`}>
        <div className="space-y-3">
          <p className="text-[13px] text-slate-500 dark:text-slate-400">
            <b>{cible?.nom}</b> — {cible?.date_jour?.split("-").reverse().join("/")} {cible?.heure?.slice(0, 8)}.
            L'observation est <b>obligatoire</b> : elle explique la cause de l'invalidation
            (la ligne reste visible à l'écran et tracée, mais sort des totaux et des exports).
          </p>
          <Champ label="Observation (obligatoire)">
            <textarea value={observation} onChange={(e) => setObservation(e.target.value)}
              rows={3} maxLength={500} autoFocus
              placeholder="Ex. : camion en atelier à cette heure (boîtier déclenché au démarrage)…"
              className={inputCls} />
          </Champ>
          <div className="flex justify-end gap-2">
            <Btn variante="secondaire" onClick={() => setCible(null)}>Annuler</Btn>
            <Btn disabled={enCours || !observation.trim()}
              onClick={() => cible && decider(cible, "INVALIDE", observation.trim())}>
              <Icon nom="verifier" /> Confirmer l'invalidation
            </Btn>
          </div>
        </div>
      </Modal>
    </div>
  );
}
