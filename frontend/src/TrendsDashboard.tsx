import { useEffect, useMemo, useState } from "react";
import {
  type ChurnResponse,
  type CompMonth,
  type JobtrendsSummary,
  type KeywordOption,
  type MarketMonth,
  type Mover,
  type TrendResponse,
  fetchChurn,
  fetchComp,
  fetchKeywords,
  fetchMarket,
  fetchSummary,
  fetchTrend,
} from "./api";
import { BandChart, ChurnChart, LineChart, SERIES_COLORS, type Line } from "./charts";

const DEFAULT_KEYWORDS = ["agents", "claude", "llm", "rust", "remote"];
const usd = (n: number) => `$${Math.round(n / 1000)}k`;

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat-card">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

function MoverRow({ m, up }: { m: Mover; up: boolean }) {
  return (
    <li className="mover-row">
      <span className="mover-kw">{m.keyword}</span>
      <span className="mover-cat">{m.category}</span>
      <span className="mover-share">{m.latest_share}%</span>
      <span className={up ? "mover-delta up" : "mover-delta down"}>
        {m.mom_delta_pts > 0 ? "+" : ""}
        {m.mom_delta_pts}pt
      </span>
    </li>
  );
}

export function TrendsDashboard() {
  const [summary, setSummary] = useState<JobtrendsSummary | null>(null);
  const [keywords, setKeywords] = useState<KeywordOption[]>([]);
  const [selected, setSelected] = useState<string[]>(DEFAULT_KEYWORDS);
  const [trend, setTrend] = useState<TrendResponse | null>(null);
  const [comp, setComp] = useState<CompMonth[]>([]);
  const [churn, setChurn] = useState<ChurnResponse | null>(null);
  const [market, setMarket] = useState<MarketMonth[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchSummary(), fetchKeywords(), fetchComp(), fetchChurn(), fetchMarket()])
      .then(([s, k, c, ch, mk]) => {
        setSummary(s);
        setKeywords(k);
        setComp(c.months);
        setChurn(ch);
        setMarket(mk.months);
      })
      .catch((e) => setError(String(e?.detail ?? e)));
  }, []);

  useEffect(() => {
    if (!selected.length) {
      setTrend({ months: [], series: [] });
      return;
    }
    fetchTrend(selected)
      .then(setTrend)
      .catch((e) => setError(String(e?.detail ?? e)));
  }, [selected]);

  const byCategory = useMemo(() => {
    const groups: Record<string, string[]> = {};
    for (const k of keywords) (groups[k.category] ??= []).push(k.keyword);
    return groups;
  }, [keywords]);

  const trendLines: Line[] = useMemo(
    () =>
      (trend?.series ?? []).map((s, i) => ({
        label: s.keyword,
        color: SERIES_COLORS[i % SERIES_COLORS.length],
        values: s.shares,
      })),
    [trend],
  );

  const toggle = (kw: string) =>
    setSelected((prev) =>
      prev.includes(kw) ? prev.filter((k) => k !== kw) : [...prev, kw].slice(0, 8),
    );

  if (error) {
    return (
      <div className="page">
        <div className="section-shell">
          <h1>Hiring trends</h1>
          <p className="muted">Couldn’t load trends right now: {error}</p>
          <a className="back-link" href="/">
            ← Back to Bullshit or Fit
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="page dashboard">
      <header className="dash-head">
        <div>
          <span className="badge">Hiring-market data</span>
          <h1>What tech is actually hiring for</h1>
          <p className="muted">
            {summary
              ? `${summary.total_posts.toLocaleString()} “Who is hiring?” posts across ${summary.months} months (${summary.first_month} → ${summary.latest_month}), parsed from Hacker News.`
              : "Loading the latest hiring signal…"}
          </p>
        </div>
        <a className="back-link" href="/">
          ← Bullshit or Fit
        </a>
      </header>

      {summary && (
        <section className="stat-grid">
          <StatCard label="job posts analyzed" value={summary.total_posts.toLocaleString()} />
          <StatCard
            label="median comp (latest)"
            value={usd(summary.comp_median_usd)}
            sub={`${summary.comp_coverage_pct}% of posts list pay`}
          />
          <StatCard label="companies tracked" value={summary.distinct_authors.toLocaleString()} />
          <StatCard
            label="keep reposting"
            value={`${summary.recurring_pct}%`}
            sub="hire across 2+ months"
          />
        </section>
      )}

      {summary && (
        <section className="section-shell movers-shell">
          <div className="movers-col">
            <h2>Heating up</h2>
            <ul className="mover-list">
              {summary.risers.map((m) => (
                <MoverRow key={m.keyword} m={m} up />
              ))}
            </ul>
          </div>
          <div className="movers-col">
            <h2>Cooling off</h2>
            <ul className="mover-list">
              {summary.fallers.map((m) => (
                <MoverRow key={m.keyword} m={m} up={false} />
              ))}
            </ul>
          </div>
          <p className="movers-note muted">
            Month-over-month change in share of postings mentioning each term.
          </p>
        </section>
      )}

      <section className="section-shell">
        <div className="market-head">
          <div>
            <h2>Jobs vs. job-seekers</h2>
            <p className="muted">
              Monthly post volume in HN’s “Who is hiring?” (openings) vs. “Who wants to be
              hired?” (candidates) threads — a rough read on which way the market is leaning.
            </p>
          </div>
          {summary && summary.seekers_per_100_jobs > 0 && (
            <div className="market-ratio">
              <span className="market-ratio-num">{Math.round(summary.seekers_per_100_jobs)}</span>
              <span className="market-ratio-cap">
                job-seekers per 100 openings <em>(latest)</em>
              </span>
            </div>
          )}
        </div>
        <div className="chart-wrap">
          <LineChart
            months={market.map((m) => m.month)}
            lines={[
              {
                label: "openings",
                color: "#245cb8",
                values: market.map((m) => m.hiring_posts),
              },
              {
                label: "job-seekers",
                color: "#f05a2a",
                values: market.map((m) => m.wants_hired_posts),
              },
            ]}
            fmtY={(v) => `${Math.round(v)}`}
          />
        </div>
        <div className="legend">
          <span className="legend-item">
            <span className="legend-dot" style={{ background: "#245cb8" }} />
            openings (jobs)
          </span>
          <span className="legend-item">
            <span className="legend-dot" style={{ background: "#f05a2a" }} />
            job-seekers
          </span>
        </div>
      </section>

      <section className="section-shell">
        <h2>Keyword share of postings</h2>
        <p className="muted">Percent of monthly job posts that mention each term. Pick up to 8.</p>
        <div className="kw-picker">
          {Object.entries(byCategory).map(([cat, kws]) => (
            <div key={cat} className="kw-group">
              <span className="kw-cat">{cat}</span>
              {kws.map((kw) => (
                <button
                  key={kw}
                  className={selected.includes(kw) ? "kw-chip on" : "kw-chip"}
                  onClick={() => toggle(kw)}
                  type="button"
                >
                  {kw}
                </button>
              ))}
            </div>
          ))}
        </div>
        <div className="chart-wrap">
          <LineChart months={trend?.months ?? []} lines={trendLines} />
        </div>
        <div className="legend">
          {trendLines.map((l) => (
            <span key={l.label} className="legend-item">
              <span className="legend-dot" style={{ background: l.color }} />
              {l.label}
            </span>
          ))}
        </div>
      </section>

      <section className="section-shell">
        <h2>Advertised compensation</h2>
        <p className="muted">
          Midpoint of parsed USD salary ranges — median line, shaded p25–p75 band.
        </p>
        <div className="chart-wrap">
          <BandChart
            months={comp.map((m) => m.month)}
            lo={comp.map((m) => m.p25_usd)}
            mid={comp.map((m) => m.median_usd)}
            hi={comp.map((m) => m.p75_usd)}
            fmtY={usd}
          />
        </div>
      </section>

      <section className="section-shell">
        <h2>Who keeps hiring vs. drops off</h2>
        <p className="muted">
          Companies posting each month — <span className="k-ret">returning</span> +{" "}
          <span className="k-new">new</span> stacked; the{" "}
          <span className="k-churn">dashed line</span> is how many went quiet since the prior month.
        </p>
        <div className="chart-wrap">
          <ChurnChart
            months={(churn?.months ?? []).map((m) => m.month)}
            returning={(churn?.months ?? []).map((m) => m.returning)}
            fresh={(churn?.months ?? []).map((m) => m.new)}
            churned={(churn?.months ?? []).map((m) => m.churned)}
          />
        </div>
      </section>

      <footer className="dash-foot muted">
        Data: Hacker News “Who is hiring?” via the HN Algolia API. Analysis by jobtrends, the data
        engine behind Bullshit or Fit.
      </footer>
    </div>
  );
}
