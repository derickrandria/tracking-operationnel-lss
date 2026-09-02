/** Notifications toast (alertes temps réel — §6.5/§12). */
import { useSyncExternalStore } from "react";
import { cls } from "../utils";
import Icon from "./icons";

export interface Toast {
  id: number;
  type: "critique" | "alerte" | "info" | "succes" | "erreur";
  titre: string;
  message?: string;
}

let toasts: Toast[] = [];
let seq = 1;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((l) => l());
}

export function addToast(t: Omit<Toast, "id">, dureeMs = 7000) {
  const id = seq++;
  toasts = [...toasts, { ...t, id }];
  notify();
  if (dureeMs > 0) setTimeout(() => retirerToast(id), dureeMs);
}

export function retirerToast(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  notify();
}

const STYLES: Record<Toast["type"], string> = {
  critique: "border-red-500/60 bg-red-600 text-white",
  alerte: "border-amber-500/60 bg-amber-500 text-slate-900",
  info: "border-slate-400/40 bg-white dark:bg-nuit-800 text-slate-800 dark:text-slate-100",
  succes: "border-emerald-500/60 bg-emerald-600 text-white",
  erreur: "border-red-500/60 bg-red-700 text-white",
};

export function Toasts() {
  const liste = useSyncExternalStore((cb) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  }, () => toasts);

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex w-[min(92vw,380px)] flex-col gap-2">
      {liste.map((t) => (
        <div key={t.id} className={cls("toast-in rounded-xl border p-3 shadow-2xl", STYLES[t.type])}>
          <div className="flex items-start justify-between gap-2">
            <div className="text-[13px] font-bold">{t.titre}</div>
            <button onClick={() => retirerToast(t.id)} className="opacity-70 hover:opacity-100">
              <Icon nom="fermer" className="w-3.5 h-3.5" />
            </button>
          </div>
          {t.message && <div className="mt-0.5 text-[12px] leading-snug opacity-95">{t.message}</div>}
        </div>
      ))}
    </div>
  );
}
