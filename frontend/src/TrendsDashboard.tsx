import { useEffect, useMemo, useState } from "react";
import {
  type ChurnResponse,
  type CompMonth,
  type CompSource,
  type CompaniesResponse,
  type CompanyPay,
  type GeoSource,
  type LayoffsResponse,
  type JobtrendsSummary,
  type KeywordOption,
  type MarketMonth,
  type Mover,
  type RemoteResponse,
  type SkillCompRow,
  type SkillsResponse,
  type TrendResponse,
  type UsaJobsResponse,
  fetchChurn,
  fetchCompSources,
  fetchCompanies,
  fetchComp,
  fetchKeywords,
  fetchMarket,
  fetchCompanyPay,
  fetchLayoffs,
  fetchLocations,
  fetchRemote,
  fetchSkillComp,
  fetchSkills,
  fetchSummary,
  fetchTrend,
  fetchUsaJobs,
} from "./api";

const SOURCE_LABEL: Record<string, string> = {
  hn: "HN posts",
  ats: "companies",
  remote_board: "remote",
  usajobs: "federal",
};
import { BandChart, ChurnChart, LineChart, SERIES_COLORS, type Line } from "./charts";

const DEFAULT_KEYWORDS = ["agents", "claude", "llm", "rust", "remote"];
const usd = (n: number) => `$${Math.round(n / 1000)}k`;
const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

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
  const [compSources, setCompSources] = useState<CompSource[]>([]);
  const [skillComp, setSkillComp] = useState<SkillCompRow[]>([]);
  const [geo, setGeo] = useState<GeoSource[]>([]);
  const [companyPay, setCompanyPay] = useState<CompanyPay[]>([]);
  const [layoffs, setLayoffs] = useState<LayoffsResponse | null>(null);
  const [churn, setChurn] = useState<ChurnResponse | null>(null);
  const [market, setMarket] = useState<MarketMonth[]>([]);
  const [companies, setCompanies] = useState<CompaniesResponse | null>(null);
  const [remote, setRemote] = useState<RemoteResponse | null>(null);
  const [usajobs, setUsajobs] = useState<UsaJobsResponse | null>(null);
  const [skills, setSkills] = useState<SkillsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetchSummary(),
      fetchKeywords(),
      fetchComp(),
      fetchChurn(),
      fetchMarket(),
      fetchCompanies(),
      fetchRemote(),
      fetchUsaJobs(),
      fetchSkills(),
      fetchCompSources(),
      fetchSkillComp(),
      fetchLocations(),
      fetchCompanyPay(),
      fetchLayoffs(),
    ])
      .then(([s, k, c, ch, mk, co, rm, uj, sk, cs, scp, lc, cp, lo]) => {
        setSummary(s);
        setKeywords(k);
        setComp(c.months);
        setChurn(ch);
        setMarket(mk.months);
        setCompanies(co);
        setRemote(rm);
        setUsajobs(uj);
        setSkills(sk);
        setCompSources(cs.sources);
        setSkillComp(scp.skills);
        setGeo(lc.sources);
        setCompanyPay(cp.companies);
        setLayoffs(lo);
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

  // Shared USD axis for the per-source comp comparison: span every source that
  // actually has pay data, so their p25–p75 bands are directly comparable.
  const compScale = useMemo(() => {
    const withComp = compSources.filter((s) => s.n_with_comp > 0);
    const lo = Math.min(...withComp.map((s) => s.p25_usd));
    const hi = Math.max(...withComp.map((s) => s.p75_usd));
    return { lo, hi, span: Math.max(1, hi - lo), rows: withComp };
  }, [compSources]);

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
              ? `${summary.total_posts.toLocaleString()} Hacker News “Who is hiring?” posts across ${summary.months} months (${summary.first_month} → ${summary.latest_month}), plus live openings from company, remote, and federal job boards.`
              : "Loading the latest hiring signal…"}
          </p>
          {summary?.data_updated && (
            <p className="updated-line">Data updated {fmtDate(summary.data_updated)}</p>
          )}
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

      {layoffs && layoffs.total_notices > 0 && (
        <section className="section-shell">
          <div className="market-head">
            <div>
              <h2>Layoffs (WARN filings)</h2>
              <p className="muted">
                The other side of supply: employees affected by mass layoffs, from official
                state WARN Act filings ({layoffs.states.join(", ")}). Bucketed by the month the
                notice was filed.
              </p>
            </div>
            <div className="market-ratio">
              <span className="market-ratio-num">
                {(layoffs.total_employees / 1000).toFixed(0)}k
              </span>
              <span className="market-ratio-cap">
                employees affected · {layoffs.total_notices.toLocaleString()} filings
              </span>
            </div>
          </div>
          <div className="chart-wrap">
            <LineChart
              months={layoffs.months.slice(-24).map((m) => m.month)}
              lines={[
                {
                  label: "employees affected",
                  color: "#c03e18",
                  values: layoffs.months.slice(-24).map((m) => m.employees_affected),
                },
              ]}
              fmtY={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : `${Math.round(v)}`)}
            />
          </div>
          <p className="co-caption">Most recent filings</p>
          <ul className="warn-list">
            {layoffs.recent.slice(0, 8).map((n, i) => (
              <li key={`${n.company}-${i}`} className="warn-row">
                <span className="warn-co">{n.company}</span>
                <span className="warn-loc muted">
                  {n.city ? `${n.city}, ` : ""}
                  {n.state}
                </span>
                <span className="warn-emp">
                  {n.employees_affected != null ? n.employees_affected.toLocaleString() : "—"}
                </span>
                <span className="warn-date muted">{n.notice_date}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {companies && companies.total_open > 0 && (
        <section className="section-shell">
          <div className="market-head">
            <div>
              <h2>Who’s hiring right now</h2>
              <p className="muted">
                Live open roles pulled straight from {companies.companies} companies’ job
                boards (Greenhouse). Refreshed daily.
              </p>
            </div>
            <div className="market-ratio">
              <span className="market-ratio-num">
                {companies.total_open.toLocaleString()}
              </span>
              <span className="market-ratio-cap">open roles right now</span>
            </div>
          </div>
          <ul className="co-list">
            {companies.top.map((c) => {
              const max = companies.top[0]?.open_roles || 1;
              return (
                <li key={c.company_token} className="co-row">
                  <span className="co-name">{c.company_name}</span>
                  <span className="co-bar-track">
                    <span
                      className="co-bar"
                      style={{ width: `${Math.max(3, (100 * c.open_roles) / max)}%` }}
                    />
                  </span>
                  <span className="co-count">{c.open_roles}</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {remote && remote.total_open > 0 && (
        <section className="section-shell">
          <div className="market-head">
            <div>
              <h2>Remote job market</h2>
              <p className="muted">
                A live sample of open remote roles from Remotive + RemoteOK ({remote.companies}{" "}
                companies). Coverage grows as the daily snapshot history builds.
              </p>
            </div>
            <div className="market-ratio">
              <span className="market-ratio-num">{remote.total_open.toLocaleString()}</span>
              <span className="market-ratio-cap">remote roles tracked</span>
            </div>
          </div>
          {remote.top_categories.length > 0 && (
            <>
              <p className="muted co-caption">Openings by function</p>
              <ul className="co-list co-list--labels">
                {remote.top_categories.map((c) => {
                  const max = remote.top_categories[0]?.open_roles || 1;
                  return (
                    <li key={c.name} className="co-row">
                      <span className="co-name">{c.name}</span>
                      <span className="co-bar-track">
                        <span
                          className="co-bar"
                          style={{ width: `${Math.max(4, (100 * c.open_roles) / max)}%` }}
                        />
                      </span>
                      <span className="co-count">{c.open_roles}</span>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </section>
      )}

      {usajobs && usajobs.total_open > 0 && (
        <section className="section-shell">
          <div className="market-head">
            <div>
              <h2>Federal hiring</h2>
              <p className="muted">
                Open roles across {usajobs.agencies} US federal agencies, from the official
                USAJobs API — the public-sector side of the market.
              </p>
            </div>
            <div className="market-ratio">
              <span className="market-ratio-num">{usajobs.total_open.toLocaleString()}</span>
              <span className="market-ratio-cap">federal roles tracked</span>
            </div>
          </div>
          {usajobs.top_agencies.length > 0 && (
            <>
              <p className="muted co-caption">Top agencies</p>
              <ul className="co-list co-list--labels">
                {usajobs.top_agencies.map((a) => {
                  const max = usajobs.top_agencies[0]?.open_roles || 1;
                  return (
                    <li key={a.name} className="co-row">
                      <span className="co-name">{a.name}</span>
                      <span className="co-bar-track">
                        <span
                          className="co-bar"
                          style={{ width: `${Math.max(4, (100 * a.open_roles) / max)}%` }}
                        />
                      </span>
                      <span className="co-count">{a.open_roles}</span>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </section>
      )}

      {(() => {
        const companies = geo.find((g) => g.source === "ats");
        if (!companies || companies.top_metros.length === 0) return null;
        const max = companies.top_metros[0]?.n_roles || 1;
        // remote_board is definitionally 100% remote — a tautology — so the
        // informative contrast is companies vs federal.
        const order = ["ats", "usajobs"];
        const remoteChips = order
          .map((src) => geo.find((g) => g.source === src))
          .filter((g): g is GeoSource => Boolean(g));
        return (
          <section className="section-shell">
            <div className="market-head">
              <div>
                <h2>Where the jobs are</h2>
                <p className="muted">
                  Top metros among {companies.total.toLocaleString()} open company roles
                  (from the location on each posting), plus how much of each channel is
                  remote.
                </p>
              </div>
            </div>
            <div className="remote-share-row">
              {remoteChips.map((g) => (
                <span key={g.source} className="remote-share-chip">
                  <span className="remote-share-pct">{g.remote_pct}%</span>
                  <span className="remote-share-src">remote · {SOURCE_LABEL[g.source] ?? g.source}</span>
                </span>
              ))}
            </div>
            <ul className="co-list metro-list">
              {companies.top_metros.map((m) => (
                <li key={m.metro} className="co-row metro-row">
                  <span className="co-name">{m.metro}</span>
                  <span className="co-bar-track">
                    <span
                      className="co-bar"
                      style={{ width: `${Math.max(3, (100 * m.n_roles) / max)}%` }}
                    />
                  </span>
                  <span className="co-count">{m.n_roles.toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </section>
        );
      })()}

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

      {skills && skills.total_roles > 0 && (
        <section className="section-shell">
          <div className="market-head">
            <div>
              <h2>In-demand skills right now</h2>
              <p className="muted">
                Share of {skills.total_roles.toLocaleString()} live open roles (companies +
                remote + federal) mentioning each skill — the same taxonomy as above, applied
                across every source. The per-source split shows where each skill is concentrated.
              </p>
            </div>
          </div>
          <ul className="co-list skill-list">
            {skills.skills.map((s) => {
              const max = skills.skills[0]?.share || 1;
              return (
                <li key={s.keyword} className="skill-row">
                  <span className="co-name">{s.keyword}</span>
                  <div className="skill-main">
                    <span className="co-bar-track">
                      <span
                        className="co-bar"
                        style={{ width: `${Math.max(3, (100 * s.share) / max)}%` }}
                      />
                    </span>
                    <span className="skill-sources">
                      {Object.entries(s.by_source)
                        .filter(([, pct]) => pct > 0)
                        .map(([src, pct]) => `${SOURCE_LABEL[src] ?? src} ${pct}%`)
                        .join(" · ")}
                    </span>
                  </div>
                  <span className="co-count">{s.share}%</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {compScale.rows.length > 0 && (
        <section className="section-shell">
          <div className="market-head">
            <div>
              <h2>What each channel pays</h2>
              <p className="muted">
                Median advertised pay per source on one axis (annualized USD). Federal
                comes from structured USAJobs pay data; companies, remote and HN posts
                from disclosed ranges. Bars span p25–p75; the tick is the median.
              </p>
            </div>
          </div>
          <ul className="comp-src-list">
            {compScale.rows.map((s) => {
              const left = (100 * (s.p25_usd - compScale.lo)) / compScale.span;
              const width = (100 * (s.p75_usd - s.p25_usd)) / compScale.span;
              const mid = (100 * (s.median_usd - compScale.lo)) / compScale.span;
              return (
                <li key={s.source} className="comp-src-row">
                  <span className="comp-src-label">{SOURCE_LABEL[s.source] ?? s.source}</span>
                  <span className="comp-src-track">
                    <span
                      className="comp-src-band"
                      style={{ left: `${left}%`, width: `${Math.max(1.5, width)}%` }}
                    />
                    <span className="comp-src-tick" style={{ left: `${mid}%` }} />
                  </span>
                  <span className="comp-src-median">{usd(s.median_usd)}</span>
                  <span className="comp-src-cov muted">
                    {s.coverage_pct}% of {s.n_roles.toLocaleString()}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {companyPay.length > 0 && (
        <section className="section-shell">
          <div className="market-head">
            <div>
              <h2>Who pays the most</h2>
              <p className="muted">
                Companies ranked by median advertised pay across their currently-open
                roles (annualized USD; only companies with enough disclosed pay). n =
                roles with pay.
              </p>
            </div>
          </div>
          <ul className="co-list company-pay-list">
            {companyPay.map((c) => {
              const max = companyPay[0]?.median_usd || 1;
              return (
                <li key={c.company_token} className="co-row company-pay-row">
                  <span className="co-name">{c.company_name}</span>
                  <span className="co-bar-track">
                    <span
                      className="co-bar"
                      style={{ width: `${Math.max(4, (100 * c.median_usd) / max)}%` }}
                    />
                  </span>
                  <span className="co-count">
                    {usd(c.median_usd)}
                    <span className="pay-n"> · {c.n_with_comp}</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {skillComp.length > 0 && (
        <section className="section-shell">
          <div className="market-head">
            <div>
              <h2>What each skill pays, by channel</h2>
              <p className="muted">
                Median advertised pay for roles mentioning each skill, split by source —
                so you can see the same skill priced across companies, remote, federal and
                HN. Only skills with enough comped roles in 2+ sources are shown; n = roles.
              </p>
            </div>
          </div>
          <ul className="co-list skill-comp-list">
            {skillComp.map((s) => {
              const cells = ["ats", "remote_board", "usajobs", "hn"]
                .filter((src) => s.by_source[src])
                .map((src) => ({ src, ...s.by_source[src] }));
              const top = Math.max(...cells.map((c) => c.median_usd));
              return (
                <li key={s.keyword} className="skill-comp-row">
                  <span className="co-name">{s.keyword}</span>
                  <div className="skill-comp-cells">
                    {cells.map((c) => (
                      <span
                        key={c.src}
                        className={
                          c.median_usd === top ? "comp-chip comp-chip--top" : "comp-chip"
                        }
                      >
                        <span className="comp-chip-src">{SOURCE_LABEL[c.src] ?? c.src}</span>
                        <span className="comp-chip-val">{usd(c.median_usd)}</span>
                        <span className="comp-chip-n">n={c.n_with_comp.toLocaleString()}</span>
                      </span>
                    ))}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

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
        <p>
          <strong>How this works.</strong> Historical trends come from Hacker News “Who is
          hiring?” threads (via the HN Algolia API). Live openings, comp, and locations come
          from public company boards (Greenhouse, Ashby, Lever), remote aggregators (Remotive,
          RemoteOK), and the federal USAJobs API. Compensation is read from structured pay
          fields where a source provides them, otherwise parsed from the posting text;
          everything is annualized to USD before it’s compared.
        </p>
        <p>
          Continuous-board figures reflect roles open right now and refresh daily. Analysis by
          jobtrends, the data engine behind Bullshit or Fit.
        </p>
      </footer>
    </div>
  );
}
