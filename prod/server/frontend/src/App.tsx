import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import AppShell from "./components/AppShell";
import LoginPage from "./pages/Login";
import { homePath } from "./roles";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginGate />} />
        <Route path="/*" element={<AppShell />} />
      </Routes>
    </BrowserRouter>
  );
}

function LoginGate() {
  const { token, roles } = useAuth();
  if (token) return <Navigate to={homePath(roles)} replace />;
  return <LoginPage />;
}
