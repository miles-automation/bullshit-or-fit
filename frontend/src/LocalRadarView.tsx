import { useEffect, useState } from "react";
import {
  type CommuteShedResponse,
  type ShedEmployer,
  type ShedMover,
  type ShedRole,
  fetchLocal,
} from "./api";

const usd = (n: number) => `$${Math.round(n / 1000)}k`;
const comp = (r: ShedRole) =>
  r.comp_min != null && r.comp_max != null
    ? `${usd(r.comp_min)}–${usd(r.comp_max)}`
    : "—";
const dist = (mi: number | null) => (mi == null ? "remote" : `${mi} mi`);

// Net-change chip from recorded history (↑/↓/flat); null until history accrues.
function NetChip({ net }: { net: number | null }) {
  if (net == null) return null;
  if (net > 0) return <span className="shed-net up">↑{net}/30d</span>;
  if (net < 0) return <span className="shed-net down">↓{Math.abs(net)}/30d</span>;
  return <span className="shed-net flat">flat</span>;
}

function EmployerRow({ e }: { e: ShedEmployer }) {
  const meta = [e.category, e.hq_city && `${e.hq_city}${e.hq_state ? `, ${e.hq_state}` : ""}`]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className="shed-emp">
      <div className="shed-emp-head">
        <a className="shed-emp-name" href={e.careers_url} target="_blank" rel="noreferrer">
          {e.name} <span className="shed-ext">↗</span>
        </a>
        <span className="shed-emp-status">
          {e.has_feed ? (
            <>
              <span className="shed-count">{e.open_roles} open</span>
              {e.opened_30d > 0 && (
                <span className="shed-hot" title="opened in the last 30 days">
                  🔥 +{e.opened_30d}/30d
                </span>
              )}
              <NetChip net={e.net_30d} />
            </>
          ) : (
            <span className="shed-maponly">careers ↗</span>
          )}
        </span>
      </div>
      <div className="shed-emp-meta muted">
        {meta} · {dist(e.distance_mi)}
      </div>
      {e.notes && <div className="shed-emp-notes">{e.notes}</div>}
    </li>
  );
}

function MoversStrip({ movers }: { movers: ShedMover[] }) {
  if (!movers.length) return null;
  return (
    <section className="section-shell shed-movers">
      <h2>🔥 Heating up</h2>
      <p className="muted">
        Employers adding roles fastest (openings in the last 30 days). This is the
        signal — who's building — not the raw count.
      </p>
      <ul className="shed-mover-list">
        {movers.map((m) => (
          <li key={m.token} className="shed-mover">
            <span className="shed-mover-name">{m.name}</span>
            <span className="shed-mover-n">+{m.opened_30d}</span>
            <span className="shed-mover-of muted">of {m.open_roles} open</span>
            <NetChip net={m.net_30d} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function LiveOpenings({ roles }: { roles: ShedRole[] }) {
  if (!roles.length) return null;
  return (
    <section className="section-shell">
      <h2>Open right now — in reach</h2>
      <p className="muted">
        Concrete openings from the employers we can read live, filtered to their local
        office or remote. Everything else on the map is a warm link above.
      </p>
      <ul className="warn-list">
        {roles.map((r, i) => (
          <li key={`${r.company}-${i}`} className="shed-role">
            {r.url ? (
              <a className="shed-role-title" href={r.url} target="_blank" rel="noreferrer">
                {r.title}
              </a>
            ) : (
              <span className="shed-role-title">{r.title}</span>
            )}
            <span className="shed-role-co muted">
              {r.company} · {r.location ?? "—"}
            </span>
            <span className="shed-role-comp">{comp(r)}</span>
            {r.is_new && <span className="shed-new">new</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function LocalRadarView() {
  const [data, setData] = useState<CommuteShedResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchLocal()
      .then(setData)
      .catch((e) => setError(String(e?.detail ?? e)));
  }, []);

  return (
    <div className="page dashboard">
      <header className="dash-head">
        <div>
          <span className="badge">Bullshit or fit — your backyard</span>
          <h1>Who's around {data ? data.home : "Laramie"}</h1>
          <p className="muted">
            A standing map of every employer reachable from here — tiered by how far,
            with live openings where a company publishes a machine-readable board and a
            warm link everywhere else. Built to consult, not chase: know the terrain
            before you need it.
          </p>
        </div>
        <a className="back-link" href="/you">
          ← Your market
        </a>
      </header>

      {error && (
        <section className="section-shell">
          <p className="muted">Couldn't load the radar right now: {error}</p>
        </section>
      )}

      {data && (
        <>
          <section className="section-shell shed-stats">
            <div className="shed-stat">
              <span className="shed-stat-n">{data.total_employers}</span>
              <span className="shed-stat-l muted">employers mapped</span>
            </div>
            <div className="shed-stat">
              <span className="shed-stat-n">{data.total_open_roles}</span>
              <span className="shed-stat-l muted">live openings in reach</span>
            </div>
            <div className="shed-stat">
              <span className="shed-stat-n">{data.new_roles}</span>
              <span className="shed-stat-l muted">
                new in {data.trajectory_days} days
              </span>
            </div>
          </section>

          <MoversStrip movers={data.movers} />

          {data.tiers.map((t) => (
            <section key={t.tier} className="section-shell">
              <div className="market-head">
                <div>
                  <h2>{t.label}</h2>
                  <p className="muted">
                    {t.employers.length} employer{t.employers.length === 1 ? "" : "s"}
                    {t.open_roles > 0 ? ` · ${t.open_roles} live openings` : ""}
                  </p>
                </div>
              </div>
              <ul className="shed-emp-list">
                {t.employers.map((e) => (
                  <EmployerRow key={e.token} e={e} />
                ))}
              </ul>
            </section>
          ))}

          <LiveOpenings roles={data.roles} />
        </>
      )}

      <footer className="dash-foot muted">
        <p>
          Map is hand-curated; live counts come from public company boards
          (Greenhouse/Lever/Ashby), filtered to roles in commuting reach or remote.
          "New" = first seen in the last {data?.trajectory_days ?? 7} days. Map-only
          employers are on Workday/Taleo/private boards we don't yet read — the link
          is the move. Not career advice — just who's around.
        </p>
      </footer>
    </div>
  );
}
