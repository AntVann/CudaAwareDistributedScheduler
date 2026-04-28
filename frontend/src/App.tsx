import { BrowserRouter, Routes, Route } from "react-router-dom";
import { OperatorAuthProvider } from "./auth";
import Layout from "./components/Layout";
import AdminTokenRequests from "./pages/AdminTokenRequests";
import Dashboard from "./pages/Dashboard";
import Jobs from "./pages/Jobs";
import Nodes from "./pages/Nodes";
import RequestToken from "./pages/RequestToken";

export default function App() {
  return (
    <OperatorAuthProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="jobs" element={<Jobs />} />
            <Route path="nodes" element={<Nodes />} />
            <Route path="request-token" element={<RequestToken />} />
            <Route path="admin/token-requests" element={<AdminTokenRequests />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </OperatorAuthProvider>
  );
}
