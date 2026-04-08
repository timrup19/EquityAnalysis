import { Routes, Route, NavLink } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Watchlist from "./pages/Watchlist";
import GraphExplorer from "./pages/GraphExplorer";
import DecisionLog from "./pages/DecisionLog";

const NAV = [
  { to: "/", label: "Dashboard" },
  { to: "/watchlist", label: "Watchlist" },
  { to: "/graph/6", label: "Graph" },
  { to: "/log", label: "Decision Log" },
];

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      {/* ── Navbar ─────────────────────────────────────────────── */}
      <nav className="border-b border-gray-800 bg-gray-900 px-6 py-3 flex items-center gap-8">
        <span className="text-lg font-semibold tracking-tight text-white">
          EquityPro
        </span>
        <div className="flex gap-4">
          {NAV.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `text-sm px-2 py-1 rounded transition-colors ${
                  isActive
                    ? "bg-gray-700 text-white"
                    : "text-gray-400 hover:text-white"
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </div>
      </nav>

      {/* ── Routes ─────────────────────────────────────────────── */}
      <main className="flex-1 p-6 max-w-7xl mx-auto w-full">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/graph/:id" element={<GraphExplorer />} />
          <Route path="/log" element={<DecisionLog />} />
        </Routes>
      </main>
    </div>
  );
}
