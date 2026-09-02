import { Navigate, Route, Routes } from "react-router-dom";
import { getToken, getUser } from "./api";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Suivi from "./pages/Suivi";
import Missions from "./pages/Missions";
import Infractions from "./pages/Infractions";
import Conduite from "./pages/Conduite";
import TempsConduite from "./pages/TempsConduite";
import Alertes from "./pages/Alertes";
import Historique from "./pages/Historique";
import Conducteurs from "./pages/Conducteurs";
import Vehicules from "./pages/Vehicules";
import Parametres from "./pages/Parametres";

function Protege({ children, admin = false }: { children: JSX.Element; admin?: boolean }) {
  const user = getUser();
  if (!getToken() || !user) return <Navigate to="/login" replace />;
  if (admin && user.role !== "ADMIN") return <Navigate to="/dashboard" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Protege><Layout /></Protege>}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/suivi" element={<Suivi />} />
        <Route path="/temps-conduite" element={<TempsConduite />} />
        <Route path="/missions" element={<Missions />} />
        <Route path="/infractions" element={<Infractions />} />
        <Route path="/conduite" element={<Conduite />} />
        <Route path="/alertes" element={<Alertes />} />
        <Route path="/historique" element={<Historique />} />
        <Route path="/conducteurs" element={<Conducteurs />} />
        <Route path="/vehicules" element={<Vehicules />} />
        <Route path="/parametres" element={<Protege admin><Parametres /></Protege>} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  );
}
