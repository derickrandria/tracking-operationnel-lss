/** Grille Suivi Journalier — composant PARTAGÉ (Addendum v1.1 §4).
 *
 * Utilisée à l'identique par :
 *   · l'onglet Suivi Journalier (édition + autosave, jour courant) ;
 *   · l'Historique Suivi Journalier (lecture seule, journée archivée) :
 *     sélectionner une journée affiche STRICTEMENT la même structure —
 *     parties A/B/C/D, en-têtes groupés, 27 colonnes trajets (Heure de départ,
 *     Fin T1, Pause 1, Début/Fin/Pause T2→T9), colonnes Partie A figées.
 *
 * `onEdit` absent / `lectureSeule` → cellules désactivées, structure inchangée.
 */
import { Fragment, useMemo, useState } from "react";
import Icon from "./icons";
import { Modal } from "./ui";
import { Referentiels, SuiviLigne } from "../types";
import { cls, fmtDuree, fmtHeure } from "../utils";

// §0vicies decies N2 (31/08/2026) : + 20h/22h (relevés automatiques du soir)
const HEURES = ["08h", "10h", "12h", "14h", "16h", "18h", "20h", "22h"] as const;
export const TRAJETS_AFFICHES = 9;  // 27 colonnes : exigence audit (voir Suivi.tsx)

/** Select inline d'une cellule (désactivé = même rendu structurel, lecture seule). */
function CellSelect({ valeur, options, onChange, className, vide = "—", disabled }: {
  valeur: string | null; options: string[]; onChange: (v: string | null) => void;
  className?: string; vide?: string; disabled?: boolean;
}) {
  return (
    <select className={cls("cell-select", className)} value={valeur ?? ""} disabled={disabled}
      onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{vide}</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

/** Champ texte inline. */
function CellText({ valeur, onChange, className, placeholder, disabled }: {
  valeur: string | null; onChange: (v: string | null) => void;
  className?: string; placeholder?: string; disabled?: boolean;
}) {
  return (
    <input className={cls("cell-input", className)} defaultValue={valeur ?? ""}
      placeholder={placeholder} disabled={disabled}
      onBlur={(e) => { const v = e.target.value.trim() || null; if (v !== (valeur ?? null)) onChange(v); }}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
  );
}

function Temps({ secondes, depasse }: { secondes: number; depasse?: boolean }) {
  if (!secondes) return <span className="text-slate-400">—</span>;
  return (
    <span className={cls("font-bold tabular-nums", depasse ? "text-red-500" : "text-slate-700 dark:text-slate-200")}>
      {fmtDuree(secondes)}
    </span>
  );
}

/** Addendum v1.9 §4.3 — cellule TCC avec alerte visuelle en priorité :
 *  · ROUGE 🚨 si le TCC dépasse 4h30 (infraction) ;
 *  · ORANGE ⚠ pulsant s'il reste ≤ 30 min de conduite continue possible ;
 *  · VERT sinon. Règle métier : le TCC coupe uniquement après une pause ≥ 30 min. */
function CelluleTCC({ secondes, seuilMax, masque }: { secondes: number; seuilMax: number; masque?: boolean }) {
  // §0vicies decies N1 (31/08/2026) : journée terminée → cellule TCC « 0:00 »
  // (le chrono temps réel n'a pas de sens sur des trajets déjà terminés).
  if (masque) return <span className="font-bold tabular-nums text-slate-500 dark:text-slate-400" title="Le TCC est un chrono temps réel : il n'est affiché que sur la journée en cours">0:00</span>;
  if (!secondes) return <span className="text-slate-400">—</span>;
  const restant = seuilMax - secondes;
  const depasse = restant < 0;
  const alerte = !depasse && restant <= 1800;   // ≤ 30 min restantes (§4.3)
  return (
    <span title={depasse ? "TCC DÉPASSÉ (> 4h30) — pause ≥ 30 min OBLIGATOIRE"
      : alerte ? `Plus que ${fmtDuree(restant)} de conduite continue — prévoyez une pause ≥ 30 min`
      : "Temps de conduite continue depuis la dernière pause ≥ 30 min"}
      className={cls("font-bold tabular-nums rounded px-1 py-0.5",
        depasse ? "bg-red-600/15 text-red-600"
        : alerte ? "animate-pulse bg-amber-500/15 text-amber-600"
        : "text-emerald-600 dark:text-emerald-500")}>
      {fmtDuree(secondes)}{depasse ? " 🚨" : alerte ? " ⚠" : ""}
    </span>
  );
}

/** Marquage PROVISOIRE / VALIDÉ (Addendum v1.4 §3.2) : orange italique pour
 * un trajet reconstruit en temps réel, affichage neutre une fois validé par
 * l'onglet Trajets MZoneX / le rapport CamtrackPro. */
const TITRE_PROVISOIRE = "Trajet PROVISOIRE — reconstruction temps réel (données en cours de confirmation MZoneX/CamtrackPro)";

function CelluleHeure({ iso, enCours, provisoire }: { iso?: string | null; enCours?: boolean; provisoire?: boolean }) {
  if (enCours) return <span title={TITRE_PROVISOIRE} className="text-[10px] font-semibold italic text-amber-600">en cours</span>;
  if (!iso) return <span className="text-slate-300 dark:text-slate-600">—</span>;
  return (
    <span title={provisoire ? TITRE_PROVISOIRE : undefined}
      className={cls("tabular-nums", provisoire && "italic text-amber-600 dark:text-amber-500")}>
      {fmtHeure(iso)}
    </span>
  );
}

function CellulePause({ secondes, provisoire }: { secondes?: number; provisoire?: boolean }) {
  if (!secondes) return <span className="text-slate-300 dark:text-slate-600">—</span>;
  return (
    <span title={provisoire ? TITRE_PROVISOIRE : undefined}
      className={cls("tabular-nums", provisoire && "italic text-amber-600 dark:text-amber-500")}>
      {fmtDuree(secondes)}
    </span>
  );
}

const TH = ({ children, classe }: any) => (
  <th className={cls("whitespace-nowrap", classe)}>{children}</th>
);
const TD_D = "bg-emerald-500/[0.04] whitespace-nowrap text-[11.5px] text-center";
const PAS_DE_MODIF = () => {};

export interface GrilleSuiviProps {
  lignes: SuiviLigne[];
  seuils?: Record<string, number>;
  modeDetail: boolean;
  refs?: Referentiels | null;
  lectureSeule: boolean;
  onEdit?: (ligne: SuiviLigne, champ: string, valeur: string | null) => void;
  pendingUI?: Record<string, boolean>;
  /** §0vicies decies N1 : journée terminée → colonne TCC affichée « 0:00 ». */
  masquerTCC?: boolean;
}

export default function GrilleSuivi({ lignes, seuils, modeDetail, refs,
                                      lectureSeule, onEdit, pendingUI,
                                      masquerTCC }: GrilleSuiviProps) {
  const [extra, setExtra] = useState<SuiviLigne | null>(null);
  const fige = lectureSeule || !onEdit;
  const edit = onEdit || PAS_DE_MODIF;
  const pauseMin = seuils?.DUREE_MIN_PAUSE_VALIDE || 900;
  const tccMax = seuils?.SEUIL_TCC_MAX ?? 16200;   // 4h30 (Addendum v1.9 §1.1)

  const conducteursComptes = useMemo(() => {
    const map = new Map<string, number>();
    for (const l of lignes) {
      if (l.conducteur?.id) {
        map.set(l.conducteur.id, (map.get(l.conducteur.id) || 0) + 1);
      }
    }
    return map;
  }, [lignes]);

  return (
    <>
      <table className="table-pro">
        <thead>
          {modeDetail && (
            <tr>
              <th colSpan={4} className="!border-b-0 bg-slate-500/90 py-1 text-center text-[10px] font-bold uppercase tracking-wider text-white">A — Véhicule &amp; Chauffeur</th>
              <th colSpan={6} className="!border-b-0 bg-blue-600/90 py-1 text-center text-[10px] font-bold uppercase tracking-wider text-white">B — Données métier</th>
              <th colSpan={7} className="!border-b-0 bg-amber-600/90 py-1 text-center text-[10px] font-bold uppercase tracking-wider text-white">C — Relevés de la journée</th>
              <th colSpan={3 * TRAJETS_AFFICHES + 7}
                className="!border-b-0 bg-emerald-700/90 py-1 text-center text-[10px] font-bold uppercase tracking-wider text-white">
                D — Calculs automatiques · TCC/TCJ/TTJ · détail des trajets (Temps réel)
              </th>
            </tr>
          )}
          <tr>
            <TH classe="sticky left-0 z-20 min-w-[104px] bg-slate-200 dark:bg-nuit-800 text-slate-500">{modeDetail ? "Plaque" : "A · CC"}</TH>
            <TH classe={modeDetail ? "sticky left-[104px] z-20 min-w-[168px] bg-slate-200 dark:bg-nuit-800" : undefined}>Description</TH>
            <TH classe={modeDetail ? "sticky left-[272px] z-20 min-w-[170px] bg-slate-200 dark:bg-nuit-800 shadow-[6px_0_10px_-6px_rgba(15,23,42,0.25)]" : undefined}>Chauffeur</TH>
            <TH>Téléphone</TH>
            <TH classe="text-blue-500">B · Situation</TH><TH classe="text-blue-500">Statut</TH>
            <TH classe="text-blue-500">Dépôt</TH><TH classe="text-blue-500">Distrib.</TH>
            <TH classe="text-blue-500">Produit</TH><TH classe="text-blue-500">N° OT</TH>
            <TH classe="text-amber-500">C · J-1</TH>
            {HEURES.map((h) => <TH key={h} classe="text-amber-500">{h}</TH>)}
            {/* Addendum v1.9 §4 — TCC/TCJ/TTJ AVANT les trajets (alerte priorité) */}
            <TH classe="text-emerald-500">TCC</TH><TH classe="text-emerald-500">TCJ</TH><TH classe="text-emerald-500">TTJ</TH>
            {!modeDetail && <TH classe="text-emerald-500">D · Départ</TH>}
            {modeDetail ? (
              Array.from({ length: TRAJETS_AFFICHES }, (_, i) => (
                <Fragment key={i}>
                  <TH classe="text-emerald-500 !px-1.5 text-[10px]">{i === 0 ? "Heure de départ" : `Début T${i + 1}`}</TH>
                  <TH classe="text-emerald-500 !px-1.5 text-[10px]">Fin T{i + 1}</TH>
                  <TH classe="text-emerald-500 !px-1.5 text-[10px]">Pause {i + 1}</TH>
                </Fragment>
              ))
            ) : (
              <TH classe="text-emerald-500">Trajets (fin ⏸ pause)</TH>
            )}
            <TH classe="text-emerald-500">Arrêt final</TH>
            <TH classe="text-emerald-500">Lieu Arrêt</TH>
            <TH>Km</TH><TH></TH>
          </tr>
        </thead>
        <tbody>
          {lignes.map((l) => {
            const estDoublonChauffeur = l.conducteur?.id ? (conducteursComptes.get(l.conducteur.id) || 0) > 1 : false;
            return (
            <tr key={l.id} className={cls(l.flag_tcc || l.flag_tcj || l.flag_ttj ? "bg-red-500/[0.04]" : "")}>
              <td className={cls("sticky left-0 z-10 font-bold whitespace-nowrap bg-white dark:bg-nuit-900")}>{l.plaque}</td>
              <td className={cls("whitespace-nowrap text-slate-400 text-[12px]",
                modeDetail && "sticky left-[104px] z-10 bg-white dark:bg-nuit-900")}>
                {l.description}
                {/* §0nonies decies M4 (29/08/2026) — boîtier muet : zone sans
                    réseau probable ; la relecture officielle complètera seule */}
                {!fige && l.gps_age_s != null && l.gps_age_s > 1800 && (
                  <div className="mt-0.5 text-[10px] font-semibold text-amber-500"
                    title="Aucun signal du boîtier depuis plus de 30 min — zone sans réseau probable : les trajets seront complétés automatiquement à la remontée des données (relecture officielle, §0nonies decies M1).">
                    boîtier muet — données en transit
                  </div>
                )}
              </td>
              <td className={cls("whitespace-nowrap",
                modeDetail && "sticky left-[272px] z-10 bg-white dark:bg-nuit-900 shadow-[6px_0_10px_-6px_rgba(15,23,42,0.25)]")}>
                {l.conducteur ? (
                  <div className="flex flex-col py-0.5">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span title={l.conducteur.nom_prenom} className="font-semibold text-slate-800 dark:text-slate-100">
                        {l.conducteur.prenom_usuel || l.conducteur.nom_prenom}
                      </span>
                      {l.conducteur.code_badge_mzonex ? (
                        <span className="px-1 py-0.2 rounded text-[10px] font-mono font-medium bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20" title="driverKeyCode MZoneX">
                          {l.conducteur.code_badge_mzonex}
                        </span>
                      ) : null}
                      {l.conducteur_origine === "MANUEL" && (
                        <span className="px-1 py-0.2 rounded text-[9.5px] font-medium bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20" title="Attribution manuelle (prioritaire)">
                          Manuel
                        </span>
                      )}
                      {estDoublonChauffeur && (
                        <span className="px-1 py-0.2 rounded text-[9.5px] font-bold bg-red-500/15 text-red-600 border border-red-500/30 animate-pulse" title="Doublon : ce chauffeur est affecté à plus d'un camion sur cette journée !">
                          ⚠️ Doublon
                        </span>
                      )}
                    </div>
                    <span className="text-[10.5px] text-slate-400 truncate max-w-[170px]" title={l.conducteur.nom_prenom}>
                      {l.conducteur.nom_prenom}
                    </span>
                  </div>
                ) : <span className="text-slate-400">—</span>}
              </td>
              <td className="whitespace-nowrap text-slate-400 text-[12px]">{l.conducteur?.telephone || "—"}</td>

              {/* ---------------- Partie B ---------------- */}
              <td className="min-w-[230px]">
                <CellSelect disabled={fige} valeur={l.situation} options={refs?.situations || []}
                  onChange={(v) => edit(l, "situation", v)} className="bg-blue-500/[0.04]" />
              </td>
              <td>
                <CellSelect disabled={fige} valeur={l.statut_camion} options={refs?.statuts_camion || []}
                  onChange={(v) => edit(l, "statut_camion", v)} className={cls("font-semibold bg-blue-500/[0.04]",
                    l.statut_camion === "CHARGÉ" && "text-amber-500",
                    l.statut_camion === "VIDE" && "text-sky-500",
                    l.statut_camion === "LIBRE" && "text-emerald-500")} />
              </td>
              <td><CellSelect disabled={fige} valeur={l.depot_recepteur} options={refs?.depots || []} onChange={(v) => edit(l, "depot_recepteur", v)} className="bg-blue-500/[0.04]" /></td>
              <td><CellSelect disabled={fige} valeur={l.distributeur} options={refs?.distributeurs || []} onChange={(v) => edit(l, "distributeur", v)} className="bg-blue-500/[0.04]" /></td>
              <td><CellSelect disabled={fige} valeur={l.produit} options={refs?.produits || []} onChange={(v) => edit(l, "produit", v)} className="bg-blue-500/[0.04]" /></td>
              <td className="min-w-[120px]"><CellText disabled={fige} valeur={l.numero_ot} onChange={(v) => edit(l, "numero_ot", v)} className="bg-blue-500/[0.04]" /></td>

              {/* ---------------- Partie C ---------------- */}
              <td className="min-w-[130px]"><CellText disabled={fige} valeur={l.emplacement_j_moins_1} onChange={(v) => edit(l, "emplacement_j_moins_1", v)} className="bg-amber-500/[0.05]" /></td>
              {HEURES.map((h) => {
                const champ = `position_${h}` as keyof SuiviLigne;
                return (
                  <td key={h} className="min-w-[120px]">
                    <CellText disabled={fige} valeur={l[champ] as string | null}
                      onChange={(v) => edit(l, champ as string, v)} className="bg-amber-500/[0.05]" />
                  </td>
                );
              })}

              {/* ---------------- Partie D (automatique) ----------------
                 Addendum v1.9 §4 : TCC/TCJ/TTJ AVANT les trajets */}
              <td className="bg-emerald-500/[0.04] text-center">
                <CelluleTCC secondes={l.tcc_s} seuilMax={tccMax} masque={masquerTCC} />
              </td>
              <td className="bg-emerald-500/[0.04] text-center"><Temps secondes={l.tcj_s} depasse={l.flag_tcj} /></td>
              <td className="bg-emerald-500/[0.04] text-center"><Temps secondes={l.ttj_s} depasse={l.flag_ttj} /></td>
              {!modeDetail && (
                <td className="whitespace-nowrap font-medium tabular-nums bg-emerald-500/[0.04] text-center">{fmtHeure(l.heure_depart)}</td>
              )}
              {modeDetail ? (
                Array.from({ length: TRAJETS_AFFICHES }, (_, i) => {
                  const t = l.trajets[i];
                  const prov = t?.statut_source === "PROVISOIRE";
                  const extraIci = i === TRAJETS_AFFICHES - 1 && l.trajets.length > TRAJETS_AFFICHES
                    ? l.trajets.slice(TRAJETS_AFFICHES) : null;
                  return (
                    <Fragment key={i}>
                      <td className={cls(TD_D, i === 0 && "font-semibold")}>
                        <CelluleHeure iso={t?.heure_debut ?? (i === 0 ? l.heure_depart : null)} provisoire={prov} />
                      </td>
                      {/* Addendum v1.9 §3.3 (décision métier 05/08) — colonne
                          Fin VIDE pour le trajet en cours : sa durée de
                          conduite vit déjà dans la colonne TCC ; le début
                          orange signale le provisoire */}
                      <td className={TD_D}>{t && t.heure_fin
                        ? <CelluleHeure iso={t.heure_fin} provisoire={prov} />
                        : (!t ? <CelluleHeure /> : null)}</td>
                      <td className={TD_D}>
                        <CellulePause secondes={t?.pause_apres_s} provisoire={prov} />
                        {extraIci && (
                          <button onClick={() => setExtra(l)}
                            title={`${extraIci.length} trajet(s) supplémentaire(s) — cliquer pour consulter`}
                            className="ml-1 rounded bg-sky-500/15 px-1 text-[10px] font-bold text-sky-500 hover:bg-sky-500/30">
                            +{extraIci.length}
                          </button>
                        )}
                      </td>
                    </Fragment>
                  );
                })
              ) : (
                <td className="min-w-[190px] bg-emerald-500/[0.04]">
                  <div className="flex max-w-[260px] flex-wrap gap-1">
                    {l.trajets.length === 0 && <span className="text-slate-400">—</span>}
                    {l.trajets.map((t) => {
                      const prov = t.statut_source === "PROVISOIRE";
                      return (
                      <span key={t.id} title={`Début ${fmtHeure(t.heure_debut)} → ${t.heure_fin ? fmtHeure(t.heure_fin) : "en cours"}` + (t.pause_apres_s ? ` · pause ${fmtDuree(t.pause_apres_s)}` : "") + (prov ? " · PROVISOIRE" : "")}
                        className={cls("rounded px-1 py-0.5 text-[10.5px] tabular-nums whitespace-nowrap",
                          prov ? "bg-amber-500/15 italic text-amber-700 dark:text-amber-400 border border-amber-500/40"
                               : "bg-slate-200/80 dark:bg-slate-700/70")}>
                        T{t.numero} {t.heure_fin ? fmtHeure(t.heure_fin) : <>{fmtHeure(t.heure_debut)}→</>}
                        {t.pause_apres_s > 0 && <span className={cls("ml-0.5", t.pause_apres_s >= pauseMin ? "text-emerald-500" : "text-slate-400")}>⏸{fmtDuree(t.pause_apres_s)}</span>}
                      </span>
                      );
                    })}
                  </div>
                </td>
              )}
              <td className="whitespace-nowrap text-[12px] bg-emerald-500/[0.04]">{l.arret_final || "—"}</td>
              {/* Addendum v1.9 §4.2 — colonne « Lieu Arrêt » */}
              <td className="max-w-[170px] truncate bg-emerald-500/[0.04] text-[12px] text-slate-500 dark:text-slate-300"
                title={l.lieu_arret || undefined}>{l.lieu_arret || "—"}</td>
              <td className="tabular-nums text-slate-400">{l.km_parcourus || "—"}</td>
              <td className="w-6 text-center">
                {pendingUI?.[l.id] && <span title="Modification en attente d'enregistrement" className="inline-block h-2 w-2 rounded-full bg-amber-500" />}
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>

      {/* Légende PROVISOIRE / VALIDÉ (Addendum v1.4 §3.2) — reprise à
         l'identique dans les exports Excel/PDF (cohérence écran/export §3.3) */}
      {modeDetail && (
        <p className="px-2 pt-1.5 text-[10.5px] italic text-amber-600 dark:text-amber-500">
          ⓘ Trajet en cours : début <b>orange</b> affiché immédiatement, colonne <b>Fin vide</b> —
          la durée de conduite courante est dans la colonne <b>TCC</b> (Addendum v1.9).
          Orange italique = données <b>en cours de validation</b> :
          une heure ne passe en noir que lorsque le trajet est terminé ET suivi d'une pause ≥ 20 min.
        </p>
      )}

      {/* Trajets supplémentaires (> 9) : consultables, jamais perdus */}
      <Modal ouvert={!!extra} onFermer={() => setExtra(null)}
        titre={extra ? `Tous les trajets — ${extra.plaque} (${extra.trajets.length})` : ""}>
        {extra && (
          <table className="table-pro">
            <thead><tr><th>#</th><th>Début</th><th>Fin</th><th>Pause après</th><th>Fiabilité</th></tr></thead>
            <tbody>
              {extra.trajets.map((t) => {
                const prov = t.statut_source === "PROVISOIRE";
                return (
                <tr key={t.id} className={cls(prov && "italic text-amber-600 dark:text-amber-500")}>
                  <td className="font-bold">T{t.numero}</td>
                  <td className="tabular-nums">{fmtHeure(t.heure_debut)}</td>
                  <td className="tabular-nums">{t.heure_fin ? fmtHeure(t.heure_fin) : ""}</td>
                  <td className="tabular-nums">{t.pause_apres_s ? fmtDuree(t.pause_apres_s) : "—"}</td>
                  <td className="text-[10.5px]">{prov ? "PROVISOIRE" : "VALIDÉ"}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Modal>
    </>
  );
}
