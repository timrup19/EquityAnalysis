import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import cytoscape from "cytoscape";

const TIER_COLORS = {
  0: "#a78bfa", // purple — end market
  1: "#60a5fa", // blue — infra
  2: "#fbbf24", // amber — equipment
  3: "#34d399", // green — materials
};

export default function GraphExplorer() {
  const { id } = useParams();
  const navigate = useNavigate();
  const containerRef = useRef(null);
  const cyRef = useRef(null);

  const [hops, setHops] = useState(2);
  const [warning, setWarning] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);

  // Fetch graph data and render
  useEffect(() => {
    setLoading(true);
    setWarning(null);

    fetch(`/api/graph/${id}?hops=${hops}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.warning) {
          setWarning(data.warning);
          if (cyRef.current) cyRef.current.destroy();
          cyRef.current = null;
          return;
        }
        renderGraph(data);
      })
      .finally(() => setLoading(false));

    return () => {
      if (cyRef.current) cyRef.current.destroy();
    };
  }, [id, hops]);

  function renderGraph(data) {
    if (!containerRef.current) return;
    if (cyRef.current) cyRef.current.destroy();

    const elements = [
      ...data.nodes.map((n) => ({
        data: {
          id: String(n.id),
          label: n.label,
          tier: n.tier,
          score: n.score,
          color: TIER_COLORS[n.tier] || "#9ca3af",
          isRoot: String(n.id) === String(id),
        },
      })),
      ...data.edges.map((e, i) => ({
        data: {
          id: `e${i}`,
          source: String(e.source),
          target: String(e.target),
          type: e.type,
          sub_ease: e.sub_ease,
        },
      })),
    ];

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "background-color": "data(color)",
            color: "#e5e7eb",
            "text-valign": "bottom",
            "text-margin-y": 6,
            "font-size": 11,
            width: 30,
            height: 30,
            "border-width": 0,
          },
        },
        {
          selector: "node[?isRoot]",
          style: {
            "border-width": 3,
            "border-color": "#fff",
            width: 40,
            height: 40,
            "font-weight": "bold",
            "font-size": 13,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#4b5563",
            "target-arrow-color": "#4b5563",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "arrow-scale": 0.8,
          },
        },
        {
          selector: "edge[sub_ease <= 2]",
          style: {
            "line-color": "#ef4444",
            "target-arrow-color": "#ef4444",
            width: 2.5,
          },
        },
      ],
      layout: {
        name: "breadthfirst",
        directed: true,
        spacingFactor: 1.4,
        roots: `[id = "${id}"]`,
      },
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
    });

    cy.on("tap", "node", (evt) => {
      const d = evt.target.data();
      setDetail(d);
    });

    cy.on("tap", (evt) => {
      if (evt.target === cy) setDetail(null);
    });

    cyRef.current = cy;
  }

  return (
    <div className="space-y-4">
      {/* ── Controls ──────────────────────────────────────────── */}
      <div className="flex items-center gap-4">
        <h2 className="text-xl font-semibold">Supply Chain Graph</h2>
        <label className="text-sm text-gray-400">
          Hops:
          <select
            value={hops}
            onChange={(e) => setHops(Number(e.target.value))}
            className="ml-2 bg-gray-800 text-gray-200 border border-gray-700 rounded px-2 py-1 text-sm"
          >
            <option value={1}>1</option>
            <option value={2}>2</option>
            <option value={3}>3</option>
          </select>
        </label>
        <label className="text-sm text-gray-400">
          Jump to company ID:
          <input
            type="number"
            min={1}
            className="ml-2 w-20 bg-gray-800 text-gray-200 border border-gray-700 rounded px-2 py-1 text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter" && e.target.value) {
                navigate(`/graph/${e.target.value}`);
              }
            }}
          />
        </label>
      </div>

      {/* ── Warning ───────────────────────────────────────────── */}
      {warning && (
        <div className="bg-amber-400/10 text-amber-400 border border-amber-400/30 rounded px-4 py-2 text-sm">
          {warning}
        </div>
      )}

      {/* ── Graph container ───────────────────────────────────── */}
      {loading ? (
        <p className="text-gray-500">Loading graph...</p>
      ) : (
        <div className="flex gap-4">
          <div
            ref={containerRef}
            className="flex-1 bg-gray-900 border border-gray-800 rounded-lg"
            style={{ height: 520 }}
          />

          {/* ── Side panel ──────────────────────────────────── */}
          <div className="w-64 shrink-0 space-y-3">
            <Legend />
            {detail && <NodeDetail data={detail} navigate={navigate} />}
          </div>
        </div>
      )}
    </div>
  );
}

function Legend() {
  const items = [
    { tier: 3, label: "T3 Materials", color: TIER_COLORS[3] },
    { tier: 2, label: "T2 Equipment", color: TIER_COLORS[2] },
    { tier: 1, label: "T1 Infra", color: TIER_COLORS[1] },
    { tier: 0, label: "T0 End Market", color: TIER_COLORS[0] },
  ];
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-xs space-y-1.5">
      <p className="text-gray-400 font-semibold mb-1">Legend</p>
      {items.map((it) => (
        <div key={it.tier} className="flex items-center gap-2">
          <span
            className="w-3 h-3 rounded-full inline-block"
            style={{ backgroundColor: it.color }}
          />
          <span className="text-gray-300">{it.label}</span>
        </div>
      ))}
      <div className="flex items-center gap-2 mt-2 pt-2 border-t border-gray-800">
        <span className="w-6 h-0.5 bg-red-500 inline-block" />
        <span className="text-gray-300">Hard to substitute (1–2)</span>
      </div>
    </div>
  );
}

function NodeDetail({ data, navigate }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-sm space-y-2">
      <p className="font-semibold text-white">{data.label}</p>
      <p className="text-gray-400 text-xs">
        Tier {data.tier} · ID {data.id}
      </p>
      {data.score != null && (
        <p className="text-gray-300">
          SC Score: <span className="font-mono">{data.score.toFixed(1)}</span>
        </p>
      )}
      <button
        onClick={() => navigate(`/graph/${data.id}`)}
        className="text-xs text-blue-400 hover:underline"
      >
        Explore this node →
      </button>
    </div>
  );
}
