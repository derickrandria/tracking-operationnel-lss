import { Navigate, Route, Routes } from "react-router-dom";
import { getToken, getUser } from "./api";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./components/Layout";
import Alertes from "./pages/Alertes";
import Conducteurs from "./pages/Conducteurs";
import Conduite from "./pages/Conduite";
import Dashboard from "./pages/Dashboard";
import Historique from "./pages/Historique";
import Infractions from "./pages/Infractions";
import Login from "./pages/Login";
import Missions from "./pages/Missions";
import Parametres from "./pages/Parametres";
import Suivi from "./pages/Suivi";
import TempsConduite from "./pages/TempsConduite";
import Vehicules from "./pages/Vehicules";

function Protege({ children, admin = false }: { children: JSX.Element; admin?: boolean }) {
  const user = getUser();
  if (!getToken() || !user) return <Navigate to="/login" replace />;
  if (admin && user.role !== "ADMIN") return <Navigate to="/dashboard" replace />;
  return children;
}

export default function App() {
  return (
    <ErrorBoundary moduleNom="Application Principale">
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Protege><Layout /></Protege>}>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<ErrorBoundary moduleNom="Tableau de bord"><Dashboard /></ErrorBoundary>} />
          <Route path="/suivi" element={<ErrorBoundary moduleNom="Suivi Journalier"><Suivi /></ErrorBoundary>} />
          <Route path="/temps-conduite" element={<ErrorBoundary moduleNom="Temps de Conduite (TCH)"><TempsConduite /></ErrorBoundary>} />
          <Route path="/missions" element={<ErrorBoundary moduleNom="Missions & Cycles Logistiques"><Missions /></ErrorBoundary>} />
          <Route path="/infractions" element={<ErrorBoundary moduleNom="Infractions & Vitesse"><Infractions /></ErrorBoundary>} />
          <Route path="/conduite" element={<ErrorBoundary moduleNom="Conduite"><Conduite /></ErrorBoundary>} />
          <Route path="/alertes" element={<ErrorBoundary moduleNom="Alertes & Surveillance"><Alertes /></ErrorBoundary>} />
          <Route path="/historique" element={<ErrorBoundary moduleNom="Historique"><Historique /></ErrorBoundary>} />
          <Route path="/conducteurs" element={<ErrorBoundary moduleNom="Conducteurs"><Conducteurs /></ErrorBoundary>} />
          <Route path="/vehicules" element={<ErrorBoundary moduleNom="Véhicules"><Vehicules /></ErrorBoundary>} />
          <Route path="/parametres" element={<Protege admin><ErrorBoundary moduleNom="Paramètres"><Parametres /></ErrorBoundary></Protege>} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  );
}
