import { useEffect, useState } from "react";
import { type ExpFunnel, fetchExpSummary } from "./api";

// Cost-per-intent = your ad spend on that concept ÷ its intent clicks. Enter the
// spend from the ad platform; the harness supplies the intents.
export function ExpDashboard() {
  const [rows, setRows] = useState<ExpFunnel[] | null>(null);
  const [spend, setSpend] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchExpSummary()
      .then((r) => setRows(r.concepts))
      .catch((e) => setError(String(e?.detail ?? e)));
  }, []);

  const cpi = (f: ExpFunnel): string => {
    const s = Number(spend[f.slug]);
    if (!s || !f.intents) return "—";
    return `$${(s / f.intents).toFixed(2)}`;
  };

  return (
    <div className="page dashboard">
      <header className="dash-head">
        <div>
          <span className="badge">Concept test — payment intent</span>
          <h1>Which concept do strangers reach for a card on?</h1>
          <p className="muted">
            Ranked by <strong>intent rate</strong> — the share of visitors who click
            through a real price. Curiosity (views) is cheap; intent is the signal.
            Enter each concept's ad spend to see cost-per-intent.
          </p>
        </div>
      </header>

      {error && (
        <section className="section-shell">
          <p className="muted">Couldn't load: {error}</p>
        </section>
      )}

      {rows && (
        <section className="section-shell">
          <div className="exp-table" role="table">
            <div className="exp-row exp-head" role="row">
              <span>Concept</span>
              <span>Views</span>
              <span>Intents</span>
              <span>Intent %</span>
              <span>Reserves</span>
              <span>Ad spend</span>
              <span>Cost / intent</span>
            </div>
            {rows.map((f, i) => (
              <div
                className={`exp-row${i === 0 && f.intents > 0 ? " exp-leader" : ""}`}
                role="row"
                key={f.slug}
              >
                <span className="exp-name">
                  {i === 0 && f.intents > 0 ? "🏆 " : ""}
                  {f.name}
                </span>
                <span className="exp-num">{f.views.toLocaleString()}</span>
                <span className="exp-num exp-intent">{f.intents.toLocaleString()}</span>
                <span className="exp-num">{f.intent_rate}%</span>
                <span className="exp-num muted">{f.reserves.toLocaleString()}</span>
                <span>
                  <span className="exp-spend">
                    $
                    <input
                      type="number"
                      inputMode="decimal"
                      value={spend[f.slug] ?? ""}
                      onChange={(e) =>
                        setSpend((p) => ({ ...p, [f.slug]: e.target.value }))
                      }
                      placeholder="0"
                    />
                  </span>
                </span>
                <span className="exp-num exp-cpi">{cpi(f)}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <footer className="dash-foot muted">
        <p>
          Signal &gt; vanity: build the concept with the lowest cost-per-intent that
          clears your kill threshold — not the one with the most views. Then talk to
          the people who clicked buy before you build.
        </p>
      </footer>
    </div>
  );
}
