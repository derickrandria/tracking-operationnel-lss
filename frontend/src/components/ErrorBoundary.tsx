/**
 * Composant ErrorBoundary global et modulaire (§12 / Résilience UI).
 * Intercepte les erreurs de rendu React, prévient l'écran blanc et offre une interface de secours conviviale
 * avec options de récupération sans perte de session.
 */
import React, { Component, ErrorInfo, ReactNode } from "react";
import Icon from "./icons";
import { Btn } from "./ui";

interface Props {
  children: ReactNode;
  moduleNom?: string;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  copieSucces: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
    errorInfo: null,
    copieSucces: false,
  };

  public static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary a intercepté une erreur :", error, errorInfo);
    this.setState({ errorInfo });
  }

  private reinitialiser = () => {
    this.setState({ hasError: false, error: null, errorInfo: null, copieSucces: false });
  };

  private copierRapport = () => {
    const { error, errorInfo } = this.state;
    const rapport = [
      `=== RAPPORT D'INCIDENT UI LSS TRACKING ===`,
      `Date : ${new Date().toISOString()}`,
      `Module : ${this.props.moduleNom || "Non spécifié"}`,
      `URL : ${window.location.href}`,
      `Navigateur : ${navigator.userAgent}`,
      `Erreur : ${error?.name || "Inconnue"} - ${error?.message || ""}`,
      `Stack :`,
      error?.stack || "Pas de trace disponible",
      `Composants :`,
      errorInfo?.componentStack || "Pas de pile composants",
    ].join("\n");

    navigator.clipboard.writeText(rapport).then(() => {
      this.setState({ copieSucces: true });
      setTimeout(() => this.setState({ copieSucces: false }), 3000);
    }).catch(() => {
      alert("Impossible de copier automatiquement. Veuillez sélectionner le texte manuellement.");
    });
  };

  public render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      const { moduleNom } = this.props;
      const { error, copieSucces } = this.state;

      return (
        <div className="min-h-[420px] flex items-center justify-center p-6">
          <div className="w-full max-w-2xl rounded-2xl border border-red-200 dark:border-red-900/60 bg-white dark:bg-nuit-900 shadow-xl p-6 sm:p-8">
            <div className="flex items-start gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-red-100 dark:bg-red-950/80 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800">
                <Icon nom="alerte" className="h-6 w-6" />
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-bold text-slate-800 dark:text-slate-100">
                  Incident d'affichage {moduleNom ? `sur le module ${moduleNom}` : "détecté"}
                </h3>
                <p className="mt-1 text-[13px] text-slate-600 dark:text-slate-400 leading-relaxed">
                  Une anomalie d'affichage JavaScript a été interceptée par le système de sécurité. Vos données en base de données sont <b>parfaitement intactes et sécurisées</b>.
                </p>

                {error && (
                  <div className="mt-4 p-3 rounded-lg bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 text-[12px] font-mono text-red-800 dark:text-red-300 overflow-x-auto">
                    <b>{error.name}:</b> {error.message}
                  </div>
                )}

                <div className="mt-6 flex flex-wrap items-center gap-3">
                  <Btn
                    variante="primaire"
                    onClick={this.reinitialiser}
                    className="bg-blue-600 hover:bg-blue-700 text-white"
                  >
                    <Icon nom="rafraichir" className="w-4 h-4" />
                    <span>Réessayer le chargement</span>
                  </Btn>

                  <Btn
                    variante="secondaire"
                    onClick={() => (window.location.href = "/dashboard")}
                  >
                    <span>Tableau de bord</span>
                  </Btn>

                  <Btn
                    variante="fantome"
                    onClick={() => window.location.reload()}
                    title="Recharge complètement l'application"
                  >
                    <span>Actualiser la page</span>
                  </Btn>

                  <Btn
                    variante="fantome"
                    onClick={this.copierRapport}
                    className={copieSucces ? "text-emerald-600 dark:text-emerald-400 border-emerald-300 dark:border-emerald-800" : ""}
                  >
                    <Icon nom={copieSucces ? "verifier" : "copier"} className="w-4 h-4" />
                    <span>{copieSucces ? "Rapport copié !" : "Copier le diagnostic"}</span>
                  </Btn>
                </div>

                <details className="mt-6 border-t border-slate-100 dark:border-slate-800 pt-4">
                  <summary className="text-[12px] font-medium text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 cursor-pointer">
                    Détails techniques pour l'assistance
                  </summary>
                  <pre className="mt-2 p-3 text-[11px] font-mono rounded bg-slate-900 text-slate-300 overflow-x-auto max-h-48 leading-tight whitespace-pre-wrap">
                    {error?.stack || "Pas de pile disponible"}
                  </pre>
                </details>
              </div>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
