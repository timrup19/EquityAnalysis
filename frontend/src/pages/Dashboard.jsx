import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

export default function Dashboard() {
  const [signals, setSignals] = useState([]);
  const [watchlist, setWatchlist] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch("/api/signals").then((r) => r.json()),
      fetch("/api/watchlist").then((r) => r.json()),
    ])
      .then(([sig, wl]) => {
        setSignals(sig);
        setWatchlist(wl);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <p className="text-gray-500">Loading...</p>;
  }

  return (
    <div className="space-y-8">
      {/* ── Portfolio summary placeholder ──────────────────────── */}
      <section>
        <h2 className="text-xl font-semibold mb-3">Portfolio</h2>
        <div className="grid grid-cols-3 gap-4">
          <Card label="Total Value" value="—" sub="No price data yet" />
          <Card label="Open Positions" value="—" sub="Log trades in Decision Log" />
          <Card label="Top Score" value={
            watchlist.length > 0
              ? watchlist[0].composite_score.toFixed(1)
              : "—"
          } sub={watchlist.length > 0 ? watchlist[0].ticker : ""} />
        </div>
      </section>

      {/* ── Signal feed ───────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xl font-semibold">Signal Feed</h2>
          <Link
            to="/watchlist"
            className="text-sm text-blue-400 hover:text-blue-300"
          >
            View Watchlist →
          </Link>
        </div>

        {signals.length === 0 ? (
          <p className="text-gray-500 text-sm">
            No signals yet. Run ingestion pipelines to generate signals.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-400 border-b border-gray-800">
                  <th className="py-2 pr-4">Ticker</th>
                  <th className="py-2 pr-4">Signal Type</th>
                  <th className="py-2 pr-4">Direction</th>
                  <th className="py-2 pr-4">Date</th>
                  <th className="py-2">Value</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr
                    key={s.id}
                    className="border-b border-gray-800/50 hover:bg-gray-900"
                  >
                    <td className="py-2 pr-4 font-mono">
                      <Link
                        to={`/graph/${s.company_id}`}
                        className="text-blue-400 hover:underline"
                      >
                        {s.ticker || "—"}
                      </Link>
                    </td>
                    <td className="py-2 pr-4">{s.signal_type}</td>
                    <td className="py-2 pr-4">
                      <DirectionBadge direction={s.direction} />
                    </td>
                    <td className="py-2 pr-4 text-gray-400">{s.signal_date}</td>
                    <td className="py-2 text-gray-300 truncate max-w-xs">
                      {s.signal_value || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Watchlist preview ─────────────────────────────────── */}
      <section>
        <h2 className="text-xl font-semibold mb-3">Top Watchlist</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-400 border-b border-gray-800">
                <th className="py-2 pr-4">Ticker</th>
                <th className="py-2 pr-4">Name</th>
                <th className="py-2 pr-4">Tier</th>
                <th className="py-2 pr-4 text-right">Composite</th>
                <th className="py-2 pr-4 text-right">Quality</th>
                <th className="py-2 text-right">SC</th>
              </tr>
            </thead>
            <tbody>
              {watchlist.map((w) => (
                <tr
                  key={w.id}
                  className="border-b border-gray-800/50 hover:bg-gray-900"
                >
                  <td className="py-2 pr-4 font-mono">
                    <Link
                      to={`/graph/${w.id}`}
                      className="text-blue-400 hover:underline"
                    >
                      {w.ticker}
                    </Link>
                  </td>
                  <td className="py-2 pr-4">{w.name}</td>
                  <td className="py-2 pr-4 text-gray-400">{w.tier}</td>
                  <td className="py-2 pr-4 text-right font-mono">
                    {w.composite_score.toFixed(1)}
                  </td>
                  <td className="py-2 pr-4 text-right font-mono text-gray-400">
                    {w.quality_score?.toFixed(1) ?? "—"}
                  </td>
                  <td className="py-2 text-right font-mono text-gray-400">
                    {w.supply_chain_score?.toFixed(1) ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Card({ label, value, sub }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-semibold mt-1">{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}

function DirectionBadge({ direction }) {
  const colors = {
    positive: "text-green-400 bg-green-400/10",
    negative: "text-red-400 bg-red-400/10",
    neutral: "text-gray-400 bg-gray-400/10",
  };
  const cls = colors[direction] || colors.neutral;
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${cls}`}>
      {direction || "—"}
    </span>
  );
}
