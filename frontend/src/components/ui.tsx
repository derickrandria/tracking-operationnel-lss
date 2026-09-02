/** Petits composants UI partagés. */
import { ReactNode, useEffect } from "react";
import { cls } from "../utils";
import Icon from "./icons";

export function Card({ children, className, titre, actions, contenuClasse }: {
  children: ReactNode; className?: string; titre?: ReactNode; actions?: ReactNode;
  /** §0octies decies L1 (27/08/2026) — zone de contenu (grille pleine hauteur,
   *  défilement interne → barre horizontale toujours visible). */
  contenuClasse?: string;
}) {
  return (
    <section className={cls(
      "rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-nuit-900 shadow-sm",
      className)}>
      {(titre || actions) && (
        <header className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-slate-100 dark:border-slate-800">
          <h3 className="font-semibold text-sm text-slate-700 dark:text-slate-200">{titre}</h3>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={cls("p-4 min-h-0 flex-1", contenuClasse)}>{children}</div>
    </section>
  );
}

export function Badge({ children, couleur }: { children: ReactNode; couleur?: string }) {
  return (
    <span className={cls(
      "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-semibold whitespace-nowrap",
      couleur || "bg-slate-500/10 text-slate-600 dark:text-slate-300 border-slate-400/30")}>
      {children}
    </span>
  );
}

export function Btn({ children, onClick, variante = "primaire", className, disabled, type = "button", title }: {
  children: ReactNode; onClick?: () => void;
  variante?: "primaire" | "secondaire" | "danger" | "fantome";
  className?: string; disabled?: boolean; type?: "button" | "submit"; title?: string;
}) {
  const styles = {
    primaire: "bg-blue-600 hover:bg-blue-500 text-white border-blue-600",
    secondaire: "bg-slate-200 hover:bg-slate-300 text-slate-700 dark:bg-slate-700 dark:hover:bg-slate-600 dark:text-slate-100 border-transparent",
    danger: "bg-red-600/90 hover:bg-red-500 text-white border-red-600",
    fantome: "bg-transparent hover:bg-slate-200/70 dark:hover:bg-slate-700/60 text-slate-600 dark:text-slate-300 border-slate-300 dark:border-slate-700",
  }[variante];
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick}
      className={cls("inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[13px] font-medium transition disabled:opacity-50 disabled:cursor-not-allowed", styles, className)}>
      {children}
    </button>
  );
}

export function Modal({ ouvert, onFermer, titre, children, large }: {
  ouvert: boolean; onFermer: () => void; titre: string; children: ReactNode; large?: boolean;
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onFermer();
    if (ouvert) window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [ouvert, onFermer]);
  if (!ouvert) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 backdrop-blur-sm p-4 fade-in"
      onMouseDown={(e) => e.target === e.currentTarget && onFermer()}>
      <div className={cls("mt-10 w-full rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-nuit-900 shadow-2xl",
        large ? "max-w-3xl" : "max-w-lg")}>
        <header className="flex items-center justify-between px-5 py-3 border-b border-slate-100 dark:border-slate-800">
          <h3 className="font-semibold">{titre}</h3>
          <button onClick={onFermer} className="p-1 rounded-md hover:bg-slate-200/70 dark:hover:bg-slate-700/60">
            <Icon nom="fermer" />
          </button>
        </header>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Champ({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">{label}</span>
      {children}
    </label>
  );
}

export const inputCls =
  "w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-[13px] focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500/40";

export function Spinner({ className }: { className?: string }) {
  return (
    <span className={cls("inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent opacity-70", className)} />
  );
}

export function PageHeader({ titre, sousTitre, actions }: {
  titre: string; sousTitre?: ReactNode; actions?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-xl font-bold tracking-tight">{titre}</h2>
        {sousTitre && <p className="mt-0.5 text-[13px] text-slate-500 dark:text-slate-400">{sousTitre}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  );
}

export function Vide({ texte }: { texte: string }) {
  return (
    <div className="py-10 text-center text-sm text-slate-400 dark:text-slate-500">{texte}</div>
  );
}
