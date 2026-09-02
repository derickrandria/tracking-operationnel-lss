/** Coquille applicative : sidebar des 8 modules + topbar (recherche globale,
 * cloche d'alertes temps réel, thème sombre/clair, utilisateur, état WS). */
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, clearAuth, getUser } from "../api";
import { toggleTheme, useTheme } from "../theme";
import { cls, fmtDateHeure, LABELS_ALERTE } from "../utils";
import { connectWS, disconnectWS, on, onStatus } from "../ws";
import Icon from "./icons";
import { addToast, Toasts } from "./toast";

const MODULES = [
  { path: "/dashboard", label: "Dashboard", icone: "dashboard" },
  { path: "/suivi", label: "Suivi Journalier", icone: "suivi" },
  { path: "/missions", label: "Missions", icone: "missions" },
  { path: "/infractions", label: "Infractions", icone: "infractions" },
  { path: "/conduite", label: "Conduite", icone: "conducteurs" },
  { path: "/alertes", label: "Alertes", icone: "alertes" },
  { path: "/historique", label: "Historique", icone: "historique" },
  { path: "/conducteurs", label: "Conducteurs", icone: "conducteurs" },
  { path: "/vehicules", label: "Véhicules", icone: "vehicules" },
];

function RechercheGlobale() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any | null>(null);
  const [ouvert, setOuvert] = useState(false);
  const nav = useNavigate();
  const timer = useRef<number>();

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (q.trim().length < 2) { setRes(null); return; }
    timer.current = window.setTimeout(async () => {
      try {
        setRes(await api(`/api/recherche?q=${encodeURIComponent(q.trim())}`));
        setOuvert(true);
      } catch { /* silencieux */ }
    }, 250);
  }, [q]);

  const total = res
    ? res.conducteurs.length + res.vehicules.length + res.alertes.length + res.infractions.length
    : 0;

  return (
    <div className="relative hidden sm:block">
      <div className="flex items-center gap-2 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800/70 px-2.5 py-1.5 w-64">
        <Icon nom="recherche" className="w-4 h-4 text-slate-400" />
        <input value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => q.trim().length >= 2 && setOuvert(true)}
          onBlur={() => setTimeout(() => setOuvert(false), 150)}
          placeholder="Recherche globale…"
          className="w-full bg-transparent text-[13px] focus:outline-none" />
      </div>
      {ouvert && res && (
        <div className="absolute right-0 z-40 mt-1 w-80 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-nuit-900 shadow-2xl p-2 text-[13px]">
          {total === 0 && <div className="p-2 text-slate-400">Aucun résultat</div>}
          {res.vehicules.map((v: any) => (
            <button key={v.id} onMouseDown={() => { setOuvert(false); nav(`/vehicules?q=${encodeURIComponent(v.plaque)}`); }}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800">
              <Icon nom="vehicules" className="w-3.5 h-3.5 text-slate-400" />
              <b>{v.plaque}</b><span className="truncate text-slate-400">{v.description}</span>
            </button>
          ))}
          {res.conducteurs.map((c: any) => (
            <button key={c.id} onMouseDown={() => { setOuvert(false); nav(`/conducteurs?q=${encodeURIComponent(c.prenom_usuel)}`); }}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800">
              <Icon nom="conducteurs" className="w-3.5 h-3.5 text-slate-400" />
              <b>{c.prenom_usuel}</b><span className="truncate text-slate-400">{c.nom_prenom}</span>
            </button>
          ))}
          {res.infractions.map((i: any) => (
            <button key={i.id} onMouseDown={() => { setOuvert(false); nav("/infractions"); }}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800">
              <Icon nom="infractions" className="w-3.5 h-3.5 text-red-400" />
              <span className="truncate">Infraction {i.plaque} — {i.date_jour}</span>
            </button>
          ))}
          {res.alertes.map((a: any) => (
            <button key={a.id} onMouseDown={() => { setOuvert(false); nav("/alertes"); }}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800">
              <Icon nom="alertes" className="w-3.5 h-3.5 text-amber-400" />
              <span className="truncate">{a.message}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Layout() {
  const theme = useTheme();
  const user = getUser();
  const nav = useNavigate();
  const loc = useLocation();
  const [wsEtat, setWsEtat] = useState<"connecté" | "déconnecté">("déconnecté");
  const [nonVues, setNonVues] = useState(0);
  const [menuUser, setMenuUser] = useState(false);
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    connectWS();
    const off1 = onStatus(setWsEtat);
    // v1.10.1 — ne JAMAIS deviner la version : si le serveur ne la renvoie
    // pas, c'est un ANCIEN serveur (ex. v1.9 encore en route) → l'afficher.
    api("/api/sante").then((d) => setVersion(d?.version ?? "ancien")).catch(() => {});
    api("/api/alertes/compteurs").then((d) => setNonVues(d.non_vues)).catch(() => {});
    const off2 = on("alerte.new", (a) => {
      setNonVues((n) => n + 1);
      addToast({
        type: a.gravite === "CRITIQUE" ? "critique" : a.gravite === "MOYENNE" ? "alerte" : "info",
        titre: `Alerte ${LABELS_ALERTE[a.type] || a.type} — ${a.plaque || ""}`,
        message: a.message,
      }, a.gravite === "CRITIQUE" ? 12000 : 6000);
    });
    const off3 = on("jour.change", () => {
      addToast({ type: "info", titre: "Changement de journée", message: "Cycle de minuit exécuté : archivage + réinitialisation." });
      setTimeout(() => location.reload(), 1500);
    });
    return () => { off1(); off2(); off3(); };
  }, []);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ============================== Sidebar ============================== */}
      <aside className="hidden md:flex w-60 shrink-0 flex-col border-r border-slate-200 dark:border-slate-800 bg-white dark:bg-nuit-900">
        <div className="flex items-center gap-2.5 px-4 py-4 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center rounded-lg bg-white px-2 py-1.5 shadow-sm ring-1 ring-slate-200 dark:ring-slate-700">
            <img src="/logo-lss.png" alt="LSS" className="h-7 w-auto" />
          </div>
          <div>
            <div className="text-[15px] font-extrabold tracking-tight">Tracking</div>
            <div className="text-[11px] text-slate-400">Flotte pétrolière · Tana</div>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {MODULES.map((m) => (
            <NavLink key={m.path} to={m.path}
              className={({ isActive }) => cls(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium transition",
                isActive
                  ? "bg-blue-600/10 text-blue-600 dark:text-blue-400"
                  : "text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800")}>
              <Icon nom={m.icone} className="w-[18px] h-[18px]" />
              {m.label}
            </NavLink>
          ))}
          {user?.role === "ADMIN" && (
            <NavLink to="/parametres"
              className={({ isActive }) => cls(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium transition",
                isActive ? "bg-blue-600/10 text-blue-600 dark:text-blue-400"
                  : "text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800")}>
              <Icon nom="parametres" className="w-[18px] h-[18px]" />
              Paramètres
            </NavLink>
          )}
        </nav>
        <div className="border-t border-slate-100 dark:border-slate-800 p-3 text-[11px] text-slate-400">
          <div className="flex items-center gap-1.5">
            <span className={cls("h-2 w-2 rounded-full", wsEtat === "connecté" ? "bg-emerald-500 ws-on" : "bg-red-500")} />
            Temps réel : {wsEtat}
          </div>
          <div className="mt-1">
            {version === null && "…"}
            {version === "ancien" && "⚠ ANCIEN SERVEUR — relancez demarrer.bat"}
            {version && version !== "ancien" && `v${version} — Tracking Opérationnel LSS`}
          </div>
        </div>
      </aside>

      {/* ============================== Contenu ============================== */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-nuit-900/80 backdrop-blur px-4 py-2.5">
          <div className="md:hidden flex items-center">
            <span className="rounded-md bg-white px-1.5 py-1 ring-1 ring-slate-200 dark:ring-slate-700">
              <img src="/logo-lss.png" alt="LSS" className="h-5 w-auto" />
            </span>
          </div>
          <div className="flex-1 flex justify-center md:justify-start">
            <RechercheGlobale />
          </div>
          <div className="flex items-center gap-1.5">
            <button onClick={() => nav("/alertes")} title="Alertes"
              className="relative rounded-lg p-2 hover:bg-slate-200/70 dark:hover:bg-slate-700/60">
              <Icon nom="alertes" className="w-[18px] h-[18px]" />
              {nonVues > 0 && (
                <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-bold text-white">
                  {nonVues > 99 ? "99+" : nonVues}
                </span>
              )}
            </button>
            <button onClick={toggleTheme} title={theme === "dark" ? "Mode clair" : "Mode sombre"}
              className="rounded-lg p-2 hover:bg-slate-200/70 dark:hover:bg-slate-700/60">
              <Icon nom={theme === "dark" ? "soleil" : "lune"} className="w-[18px] h-[18px]" />
            </button>
            <div className="relative">
              <button onClick={() => setMenuUser((v) => !v)}
                className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-200/70 dark:hover:bg-slate-700/60">
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-blue-600/90 text-[12px] font-bold text-white">
                  {(user?.nom_complet || "?").slice(0, 2).toUpperCase()}
                </span>
                <span className="hidden sm:block text-left leading-tight">
                  <span className="block text-[12.5px] font-semibold">{user?.nom_complet}</span>
                  <span className="block text-[11px] text-slate-400">
                    {user?.role === "ADMIN" ? "Administrateur" : user?.role === "TRACKING" ? "Responsable Tracking" : "Consultation"}
                  </span>
                </span>
              </button>
              {menuUser && (
                <div className="absolute right-0 z-40 mt-1 w-48 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-nuit-900 shadow-2xl p-1.5"
                  onMouseLeave={() => setMenuUser(false)}>
                  <div className="px-3 py-2 text-[12px] text-slate-400 border-b border-slate-100 dark:border-slate-800">
                    Connecté : {user?.username}
                  </div>
                  <button
                    onClick={() => { clearAuth(); disconnectWS(); nav("/login"); }}
                    className="mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-[13px] text-red-500 hover:bg-red-500/10">
                    <Icon nom="deconnexion" className="w-4 h-4" /> Déconnexion
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        {/* nav mobile */}
        <nav className="flex md:hidden overflow-x-auto border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-nuit-900 px-2">
          {MODULES.map((m) => (
            <NavLink key={m.path} to={m.path}
              className={({ isActive }) => cls("whitespace-nowrap px-3 py-2 text-[12.5px] border-b-2 -mb-px",
                isActive ? "border-blue-500 text-blue-500 font-semibold" : "border-transparent text-slate-500")}>
              {m.label}
            </NavLink>
          ))}
        </nav>

        <main className="min-h-0 flex-1 overflow-y-auto p-4" key={loc.pathname}>
          <Outlet />
        </main>
      </div>
      <Toasts />
    </div>
  );
}
