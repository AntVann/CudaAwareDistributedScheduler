import { BrowserRouter, Routes, Route } from "react-router-dom";
import { OperatorAuthProvider } from "./auth";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Jobs from "./pages/Jobs";
import Nodes from "./pages/Nodes";

export default function App() {
  return (
    <OperatorAuthProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="jobs" element={<Jobs />} />
            <Route path="nodes" element={<Nodes />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </OperatorAuthProvider>
  );
}
