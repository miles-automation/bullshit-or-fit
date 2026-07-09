import { useEffect, useMemo, useRef, useState } from "react";
import {
  type KeywordOption,
  type MarketFitResponse,
  type WagesResponse,
  fetchKeywords,
  fetchMarketFit,
  fetchWages,
} from "./api";

const SENIORITIES = ["junior", "mid", "senior", "staff", "principal"];
// Only genuine skills belong in the picker (role/seniority modifiers are separate).
const SKILL_CATEGORIES = ["language", "framework", "data", "infra", "ai"];
const usd = (n: number) => `$${Math.round(n / 1000)}k`;
const SOURCE_LABEL: Record<string, string> = {
  ats: "company",
  remote_board: "remote",
};

const traj = (t: string) => (t === "rising" ? "↑" : t === "falling" ? "↓" : "");

function VerdictCard({ r }: { r: MarketFitResponse }) {
  const lo = r.comp_p25_usd;
  const hi = r.comp_p75_usd;
  const span = Math.max(1, hi - lo);
  const medPos = (100 * (r.comp_median_usd - lo)) / span;
  const youPos =
    r.comp_usd != null
      ? Math.min(100, Math.max(0, (100 * (r.comp_usd - lo)) / span))
      : null;

  const headline: Record<string, string> = {
    under: "You're under market",
    fit: "You're at market",
    over: "You're above market",
  };
  const blurb: Record<string, string> = {
    under: `Market median for your profile is ${usd(r.comp_median_usd)} — you're ${Math.abs(r.comp_delta_pct ?? 0)}% below it. There's room to ask for more.`,
    fit: `You're right in the market band (${usd(lo)}–${usd(hi)}). Median is ${usd(r.comp_median_usd)}.`,
    over: `You're above the p75 (${usd(hi)}). Median is ${usd(r.comp_median_usd)}, so you're ${r.comp_delta_pct}% over — strong, or a stretch depending on the role.`,
  };

  return (
    <section className="section-shell">
      <div className="market-head">
        <div>
          <h2>What you're worth</h2>
          {r.comp_n > 0 ? (
            <p className="muted">
              Based on {r.comp_n.toLocaleString()} live roles matching your skills
              {r.seniority ? ` at ${r.seniority} level` : ""} that disclose pay.
            </p>
          ) : (
            <p className="muted">
              Not enough roles matching your exact profile disclose pay yet — try
              fewer or more common skills.
            </p>
          )}
        </div>
        {r.comp_verdict !== "unknown" && (
          <div className={`mf-verdict mf-verdict--${r.comp_verdict}`}>
            {headline[r.comp_verdict]}
          </div>
        )}
      </div>

      {r.comp_n > 0 && (
        <>
          <div className="mf-band-wrap">
            <span className="mf-band-end">{usd(lo)}</span>
            <span className="mf-band-track">
              <span className="mf-band-fill" />
              <span className="mf-band-tick" style={{ left: `${medPos}%` }}>
                <span className="mf-band-lbl">median {usd(r.comp_median_usd)}</span>
              </span>
              {youPos != null && (
                <span className="mf-band-you" style={{ left: `${youPos}%` }}>
                  <span className="mf-band-youlbl">you {usd(r.comp_usd ?? 0)}</span>
                </span>
              )}
            </span>
            <span className="mf-band-end">{usd(hi)}</span>
          </div>
          {r.comp_verdict !== "unknown" && (
            <p className="mf-blurb">{blurb[r.comp_verdict]}</p>
          )}
          {r.comp_verdict === "unknown" && r.comp_usd == null && (
            <p className="mf-blurb muted">
              Add your current comp above to see exactly where you land.
            </p>
          )}
        </>
      )}
    </section>
  );
}

function MarketFitResult({ r }: { r: MarketFitResponse }) {
  return (
    <>
      <VerdictCard r={r} />

      <section className="section-shell">
        <h2>Who's hiring you right now</h2>
        <p className="muted">
          {r.matching_roles.toLocaleString()} live openings match most of your skills.
          {r.top_roles.length > 0 ? " Top matches:" : ""}
        </p>
        <ul className="warn-list">
          {r.top_roles.map((t, i) => (
            <li key={`${t.company}-${i}`} className="warn-row">
              <span className="warn-co">{t.title}</span>
              <span className="warn-loc muted">
                {t.company} · {SOURCE_LABEL[t.source] ?? t.source}
              </span>
              <span className="warn-emp">
                {t.comp_median_usd != null ? usd(t.comp_median_usd) : "—"}
              </span>
              <span className="warn-date muted">{t.skills_matched} skills</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="section-shell">
        <h2>Your skills in the market</h2>
        <ul className="co-list skill-comp-list">
          {r.skill_signals.map((s) => (
            <li key={s.skill} className="mf-skill-row">
              <span className="co-name">{s.skill}</span>
              <span className="mf-skill-share">{s.demand_share}% of posts</span>
              <span
                className={
                  s.trajectory === "rising"
                    ? "mf-traj up"
                    : s.trajectory === "falling"
                      ? "mf-traj down"
                      : "mf-traj"
                }
              >
                {traj(s.trajectory)}{" "}
                {s.mom_delta_pts != null
                  ? `${s.mom_delta_pts > 0 ? "+" : ""}${s.mom_delta_pts}pt`
                  : "steady"}
              </span>
            </li>
          ))}
        </ul>
        {r.gaps.length > 0 && (
          <p className="mf-gaps">
            <strong>Rising skills you don't have yet:</strong>{" "}
            {r.gaps.map((g) => `${g.skill} (+${g.mom_delta_pts}pt)`).join(" · ")}
          </p>
        )}
      </section>
    </>
  );
}

function LocationWages({ w }: { w: WagesResponse }) {
  const a = w.area;
  if (!a) return null;
  const lo = a.p10_usd;
  const hi = a.p90_usd;
  const span = Math.max(1, hi - lo);
  const pos = (v: number) =>
    `${Math.min(100, Math.max(0, (100 * (v - lo)) / span))}%`;
  return (
    <section className="section-shell">
      <div className="market-head">
        <div>
          <h2>What it actually pays in {a.area_name}</h2>
          <p className="muted">
            BLS OEWS — software developers, <strong>all employers</strong> in your
            state (not just the well-known/remote roles the match above skews toward).
            This is the honest local floor-to-ceiling.
          </p>
        </div>
      </div>
      <div className="mf-band-wrap">
        <span className="mf-band-end">{usd(lo)}</span>
        <span className="mf-band-track">
          <span className="mf-band-fill" />
          <span className="mf-band-tick" style={{ left: pos(a.p25_usd) }} />
          <span className="mf-band-tick" style={{ left: pos(a.median_usd) }}>
            <span className="mf-band-lbl">median {usd(a.median_usd)}</span>
          </span>
          <span className="mf-band-tick" style={{ left: pos(a.p75_usd) }} />
        </span>
        <span className="mf-band-end">{usd(hi)}</span>
      </div>
      <p className="mf-blurb muted">
        Band = 10th–90th percentile ({usd(lo)}–{usd(hi)}); ticks are the 25th, median,
        and 75th.
        {w.national ? ` The US median is ${usd(w.national.median_usd)}.` : ""} The
        big-name remote roles in the match above often sit near — or above — your
        state's 90th percentile, so treat those as the stretch, not the baseline.
      </p>
    </section>
  );
}

export function MarketFitView() {
  const [keywords, setKeywords] = useState<KeywordOption[]>([]);
  const [skills, setSkills] = useState<string[]>([]);
  const [seniority, setSeniority] = useState("");
  const [comp, setComp] = useState("");
  const [result, setResult] = useState<MarketFitResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [areas, setAreas] = useState<{ code: string; name: string }[]>([]);
  const [location, setLocation] = useState("");
  const [wages, setWages] = useState<WagesResponse | null>(null);

  useEffect(() => {
    fetchKeywords()
      .then(setKeywords)
      .catch(() => {});
    fetchWages("")
      .then((w) => setAreas(w.areas))
      .catch(() => {});
  }, []);

  const latestWageReq = useRef("");
  const onLocation = (code: string) => {
    setLocation(code);
    setWages(null); // clear immediately so the card never shows a stale state
    latestWageReq.current = code;
    if (code)
      fetchWages(code)
        .then((w) => {
          // ignore out-of-order responses: only apply if this is still the pick
          if (latestWageReq.current === code) setWages(w);
        })
        .catch(() => {});
  };

  const byCategory = useMemo(() => {
    const g: Record<string, string[]> = {};
    for (const k of keywords)
      if (SKILL_CATEGORIES.includes(k.category)) (g[k.category] ??= []).push(k.keyword);
    return g;
  }, [keywords]);

  const toggle = (kw: string) =>
    setSkills((p) => (p.includes(kw) ? p.filter((k) => k !== kw) : [...p, kw]));

  const submit = () => {
    if (!skills.length) return;
    setLoading(true);
    setError(null);
    fetchMarketFit(skills, seniority || null, comp ? Number(comp) : null)
      .then(setResult)
      .catch((e) => setError(String(e?.detail ?? e)))
      .finally(() => setLoading(false));
  };

  return (
    <div className="page dashboard">
      <header className="dash-head">
        <div>
          <span className="badge">Bullshit or fit — for you</span>
          <h1>Are your expectations a fit for the market?</h1>
          <p className="muted">
            Pick your skills, seniority, and current comp. We'll benchmark you against
            the live market — what you're worth, who's hiring you, and what's worth
            learning next.
          </p>
          <p className="muted">
            Tied to one place?{" "}
            <a className="mf-inline-link" href="/local">
              See who's actually around you →
            </a>
          </p>
        </div>
        <a className="back-link" href="/trends">
          ← Market overview
        </a>
      </header>

      <section className="section-shell">
        <p className="co-caption">Your skills</p>
        <div className="kw-picker">
          {Object.entries(byCategory).map(([cat, kws]) => (
            <div key={cat} className="kw-group">
              <span className="kw-cat">{cat}</span>
              {kws.map((kw) => (
                <button
                  key={kw}
                  type="button"
                  className={skills.includes(kw) ? "kw-chip on" : "kw-chip"}
                  onClick={() => toggle(kw)}
                >
                  {kw}
                </button>
              ))}
            </div>
          ))}
        </div>
        <div className="mf-controls">
          <label className="mf-field">
            <span>Your state</span>
            <select value={location} onChange={(e) => onLocation(e.target.value)}>
              <option value="">Select…</option>
              {areas.map((a) => (
                <option key={a.code} value={a.code}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
          <label className="mf-field">
            <span>Seniority</span>
            <select value={seniority} onChange={(e) => setSeniority(e.target.value)}>
              <option value="">Any</option>
              {SENIORITIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="mf-field">
            <span>Your comp (USD/yr)</span>
            <input
              type="number"
              inputMode="numeric"
              placeholder="e.g. 180000"
              value={comp}
              onChange={(e) => setComp(e.target.value)}
            />
          </label>
          <button
            className="mf-submit"
            type="button"
            onClick={submit}
            disabled={!skills.length || loading}
          >
            {loading ? "Checking…" : "Check my fit"}
          </button>
        </div>
        {error && <p className="muted">Couldn't check right now: {error}</p>}
        {!result && !error && (
          <p className="muted mf-hint">
            Pick at least one skill, then check your fit.
          </p>
        )}
      </section>

      {wages && <LocationWages w={wages} />}

      {result && <MarketFitResult r={result} />}

      <footer className="dash-foot muted">
        <p>
          Benchmarked against live private-sector openings (company boards + remote
          aggregators) and Hacker News hiring trends. Comp is annualized USD; “market”
          means roles matching your skills. Not financial or career advice — just what
          the postings say.
        </p>
      </footer>
    </div>
  );
}
