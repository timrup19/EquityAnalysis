import { useEffect, useState } from "react";

const ACTIONS = ["BUY", "SELL", "ADD", "TRIM"];

const EMPTY_FORM = {
  company_id: "",
  action: "BUY",
  shares: "",
  cost_basis: "",
  thesis: "",
  key_risks: "",
  exit_conditions: "",
};

export default function DecisionLog() {
  const [form, setForm] = useState(EMPTY_FORM);
  const [companies, setCompanies] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);

  useEffect(() => {
    fetch("/api/companies")
      .then((r) => r.json())
      .then((data) => {
        const pub = data.filter((c) => c.is_public && c.ticker);
        setCompanies(pub);
      });
  }, []);

  function set(field) {
    return (e) => setForm((f) => ({ ...f, [field]: e.target.value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setResult(null);

    const body = {
      ...form,
      company_id: Number(form.company_id),
      shares: Number(form.shares),
      cost_basis: Number(form.cost_basis),
    };

    try {
      const res = await fetch("/api/portfolio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        setResult({ ok: false, msg: data.detail || "Error" });
      } else {
        setResult({ ok: true, msg: `Trade #${data.id} created.` });
        setForm(EMPTY_FORM);
      }
    } catch (err) {
      setResult({ ok: false, msg: err.message });
    } finally {
      setSubmitting(false);
    }
  }

  const selectedCompany = companies.find(
    (c) => String(c.id) === String(form.company_id)
  );

  return (
    <div className="max-w-2xl space-y-6">
      <h2 className="text-xl font-semibold">Decision Log</h2>
      <p className="text-sm text-gray-400">
        Record a trade decision with your thesis, risks, and exit conditions.
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Company + Action row */}
        <div className="grid grid-cols-2 gap-4">
          <Field label="Company">
            <select
              required
              value={form.company_id}
              onChange={set("company_id")}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
            >
              <option value="">Select company...</option>
              {companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.ticker} — {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Action">
            <select
              value={form.action}
              onChange={set("action")}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
            >
              {ACTIONS.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </Field>
        </div>

        {/* Shares + Cost basis */}
        <div className="grid grid-cols-2 gap-4">
          <Field label="Shares">
            <input
              type="number"
              required
              min="0"
              step="any"
              value={form.shares}
              onChange={set("shares")}
              placeholder="100"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
            />
          </Field>
          <Field label="Cost Basis ($)">
            <input
              type="number"
              required
              min="0"
              step="any"
              value={form.cost_basis}
              onChange={set("cost_basis")}
              placeholder="245.50"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
            />
          </Field>
        </div>

        {/* Score context */}
        {selectedCompany && (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-sm text-gray-400 flex gap-6">
            <span>
              Composite:{" "}
              <span className="text-white font-mono">
                {selectedCompany.composite_score?.toFixed(1) ?? "—"}
              </span>
            </span>
            <span>
              SC Score:{" "}
              <span className="text-white font-mono">
                {selectedCompany.sc_score?.toFixed(1) ?? "—"}
              </span>
            </span>
            <span>Tier {selectedCompany.tier}</span>
          </div>
        )}

        {/* Thesis */}
        <Field label="Thesis">
          <textarea
            required
            rows={3}
            value={form.thesis}
            onChange={set("thesis")}
            placeholder="Why are you entering this position? What is the supply chain edge?"
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
          />
        </Field>

        {/* Key risks */}
        <Field label="Key Risks">
          <textarea
            required
            rows={2}
            value={form.key_risks}
            onChange={set("key_risks")}
            placeholder="What could go wrong? Supply chain normalization, margin compression, etc."
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
          />
        </Field>

        {/* Exit conditions */}
        <Field label="Exit Conditions">
          <textarea
            required
            rows={2}
            value={form.exit_conditions}
            onChange={set("exit_conditions")}
            placeholder="When will you exit? SC score below 5, thesis invalidated, etc."
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
          />
        </Field>

        {/* Submit */}
        <div className="flex items-center gap-4">
          <button
            type="submit"
            disabled={submitting}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium px-5 py-2 rounded transition-colors"
          >
            {submitting ? "Saving..." : "Log Trade"}
          </button>
          {result && (
            <span
              className={`text-sm ${
                result.ok ? "text-green-400" : "text-red-400"
              }`}
            >
              {result.msg}
            </span>
          )}
        </div>
      </form>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="text-sm text-gray-400 mb-1 block">{label}</span>
      {children}
    </label>
  );
}
