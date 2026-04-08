import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

export default function Watchlist() {
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/companies")
      .then((r) => r.json())
      .then(setCompanies)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="text-gray-500">Loading...</p>;

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">All Companies</h2>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-400 border-b border-gray-800">
              <th className="py-2 pr-4">Ticker</th>
              <th className="py-2 pr-4">Name</th>
              <th className="py-2 pr-4">Tier</th>
              <th className="py-2 pr-4">Country</th>
              <th className="py-2 pr-4 text-right">Composite</th>
              <th className="py-2 pr-4 text-right">Quality</th>
              <th className="py-2 pr-4 text-right">Momentum</th>
              <th className="py-2 pr-4 text-right">Valuation</th>
              <th className="py-2 pr-4 text-right">SC Score</th>
              <th className="py-2 pr-4 text-right">Bottleneck</th>
              <th className="py-2 text-right">Conc. Risk</th>
            </tr>
          </thead>
          <tbody>
            {companies.map((c) => (
              <tr
                key={c.id}
                className="border-b border-gray-800/50 hover:bg-gray-900"
              >
                <td className="py-2 pr-4 font-mono">
                  {c.ticker ? (
                    <Link
                      to={`/graph/${c.id}`}
                      className="text-blue-400 hover:underline"
                    >
                      {c.ticker}
                    </Link>
                  ) : (
                    <span className="text-gray-500">—</span>
                  )}
                </td>
                <td className="py-2 pr-4">{c.name}</td>
                <td className="py-2 pr-4">
                  <TierBadge tier={c.tier} />
                </td>
                <td className="py-2 pr-4 text-gray-400">{c.country}</td>
                <td className="py-2 pr-4 text-right font-mono">
                  <ScoreCell value={c.composite_score} max={100} />
                </td>
                <td className="py-2 pr-4 text-right font-mono text-gray-400">
                  {fmt(c.quality_score)}
                </td>
                <td className="py-2 pr-4 text-right font-mono text-gray-400">
                  {fmt(c.momentum_score)}
                </td>
                <td className="py-2 pr-4 text-right font-mono text-gray-400">
                  {fmt(c.valuation_score)}
                </td>
                <td className="py-2 pr-4 text-right font-mono text-gray-400">
                  {fmt(c.sc_score)}
                </td>
                <td className="py-2 pr-4 text-right font-mono text-gray-400">
                  {fmt(c.bottleneck_score)}
                </td>
                <td className="py-2 text-right font-mono text-gray-400">
                  {fmt(c.concentration_risk)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function fmt(val) {
  return val != null ? val.toFixed(1) : "—";
}

function TierBadge({ tier }) {
  const labels = { 0: "End Mkt", 1: "Infra", 2: "Equip", 3: "Materials" };
  const colors = {
    0: "text-purple-400 bg-purple-400/10",
    1: "text-blue-400 bg-blue-400/10",
    2: "text-amber-400 bg-amber-400/10",
    3: "text-green-400 bg-green-400/10",
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${colors[tier] || ""}`}>
      T{tier} {labels[tier] || ""}
    </span>
  );
}

function ScoreCell({ value, max }) {
  if (value == null) return <span className="text-gray-500">—</span>;
  const pct = value / max;
  const color =
    pct >= 0.6 ? "text-green-400" : pct >= 0.4 ? "text-yellow-400" : "text-red-400";
  return <span className={color}>{value.toFixed(1)}</span>;
}
