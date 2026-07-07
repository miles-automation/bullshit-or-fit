import { useEffect, useState } from "react";
import { type JobtrendsSummary, fetchSummary } from "./api";

const usd = (n: number) => `$${Math.round(n / 1000)}k`;

/** Landing-page teaser for the /trends dashboard. Live stats when the API is
 *  reachable, graceful copy-only fallback when it isn't — the CTA always works. */
export function TrendsTeaser() {
  const [summary, setSummary] = useState<JobtrendsSummary | null>(null);

  useEffect(() => {
    let alive = true;
    fetchSummary()
      .then((s) => {
        // Only surface stats when the response really is a summary — keeps the
        // teaser from rendering garbage if the endpoint returns something odd.
        if (alive && s && typeof s.total_posts === "number") setSummary(s);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section className="section-shell teaser">
      <div className="teaser-copy">
        <span className="badge teaser-badge">Free hiring-market data</span>
        <h2>See what tech is actually hiring for</h2>
        <p>
          The data engine behind Bullshit or Fit parses every Hacker News “Who is hiring?”
          thread — in-demand skills, real advertised pay, and which companies keep posting
          month after month.
        </p>
        <a className="teaser-cta" href="/trends">
          Explore the trends →
        </a>
      </div>
      {summary && (
        <div className="teaser-stats" aria-hidden="true">
          <div>
            <span className="teaser-num">{summary.total_posts.toLocaleString()}</span>
            <span className="teaser-cap">posts analyzed</span>
          </div>
          <div>
            <span className="teaser-num">{usd(summary.comp_median_usd)}</span>
            <span className="teaser-cap">median comp</span>
          </div>
          <div>
            <span className="teaser-num">{summary.recurring_pct}%</span>
            <span className="teaser-cap">keep reposting</span>
          </div>
        </div>
      )}
    </section>
  );
}
