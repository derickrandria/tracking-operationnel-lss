import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../api";
import Icon from "../components/icons";
import { Btn, Spinner } from "../components/ui";

export default function Login() {
  const nav = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [erreur, setErreur] = useState("");
  const [chargement, setChargement] = useState(false);

  async function soumettre(e: FormEvent) {
    e.preventDefault();
    setErreur("");
    setChargement(true);
    try {
      await login(username.trim(), password);
      nav("/dashboard");
    } catch (err: any) {
      setErreur(err.message || "Échec de connexion");
    } finally {
      setChargement(false);
    }
  }

  return (
    <div className="flex min-h-screen bg-nuit-950 text-slate-100">
      {/* Panneau gauche */}
      <div className="hidden lg:flex w-[46%] flex-col justify-between bg-gradient-to-br from-blue-900 via-nuit-900 to-nuit-950 p-10">
        <div className="flex items-center gap-4">
          <div className="rounded-xl bg-white px-3.5 py-2.5 shadow-xl shadow-black/30">
            <img src="/logo-lss.png" alt="LSS" className="h-10 w-auto" />
          </div>
          <div>
            <div className="text-lg font-extrabold tracking-tight">Tracking Opérationnel</div>
            <div className="text-[13px] text-blue-200/70">Plateforme de suivi de flotte pétrolière</div>
          </div>
        </div>
        <div className="space-y-6">
          <h1 className="text-3xl font-bold leading-tight">
            Suivi temps réel des camions-citernes,<br />
            <span className="text-blue-400">calculs réglementaires automatisés.</span>
          </h1>
          <ul className="space-y-3 text-[14px] text-slate-300">
            {[
              "Positions GPS live des 51 citernes (MZoneX / CamtrackPro)",
              "TCC / TCJ / TTJ calculés automatiquement — 4h30 · 10h · 12h",
              "Infractions et alertes détectées sans saisie manuelle",
              "Missions reconstituées — cycle BASE → TMT → dépôt → retour",
            ].map((t) => (
              <li key={t} className="flex items-start gap-2.5">
                <Icon nom="verifier" className="mt-0.5 w-4 h-4 text-emerald-400 shrink-0" />
                {t}
              </li>
            ))}
          </ul>
        </div>
        <div className="text-[12px] text-slate-500">Antananarivo, Madagascar — v1.45</div>
      </div>

      {/* Formulaire */}
      <div className="flex flex-1 items-center justify-center p-6">
        <form onSubmit={soumettre} className="w-full max-w-sm space-y-5 rounded-2xl border border-slate-800 bg-nuit-900 p-8 shadow-2xl">
          <div className="flex flex-col items-center gap-3">
            <div className="rounded-xl bg-white px-4 py-2.5 shadow-md lg:hidden">
              <img src="/logo-lss.png" alt="LSS" className="h-9 w-auto" />
            </div>
            <div className="text-center lg:text-left lg:self-start">
              <h2 className="text-xl font-bold">Connexion</h2>
              <p className="mt-1 text-[13px] text-slate-400">Accès sécurisé par rôle (admin / tracking / consultation)</p>
            </div>
          </div>
          {erreur && (
            <div className="rounded-lg border border-red-500/50 bg-red-500/10 px-3 py-2 text-[13px] text-red-300">{erreur}</div>
          )}
          <label className="block">
            <span className="mb-1 block text-[12px] font-medium text-slate-400">Nom d'utilisateur</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username"
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-[14px] focus:border-blue-500 focus:outline-none" />
          </label>
          <label className="block">
            <span className="mb-1 block text-[12px] font-medium text-slate-400">Mot de passe</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password"
              className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-[14px] focus:border-blue-500 focus:outline-none" />
          </label>
          <Btn type="submit" className="w-full justify-center py-2" disabled={chargement}>
            {chargement ? <Spinner /> : <Icon nom="deconnexion" className="w-4 h-4" />}
            Se connecter
          </Btn>
          <div className="rounded-lg bg-slate-800/60 p-3 text-[11.5px] leading-relaxed text-slate-400">
            <b className="text-slate-300">Comptes de démonstration :</b><br />
            admin / Admin@2026 (Administrateur)<br />
            tracking / Tracking@2026 (Responsable Tracking)<br />
            consultation / Consult@2026 (lecture seule)
          </div>
        </form>
      </div>
    </div>
  );
}
