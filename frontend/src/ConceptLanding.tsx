import { useEffect, useMemo, useState } from "react";
import {
  type ExpConcept,
  type ExpTier,
  fetchConcept,
  logExpEvent,
  submitLead,
} from "./api";

// Stable per-visitor id so a `view` and a later `intent` join into one funnel.
function sessionId(): string {
  try {
    const k = "bof_exp_sid";
    let v = localStorage.getItem(k);
    if (!v) {
      v = crypto.randomUUID();
      localStorage.setItem(k, v);
    }
    return v;
  } catch {
    return "anon";
  }
}

function utm() {
  const p = new URLSearchParams(window.location.search);
  return {
    utm_source: p.get("utm_source"),
    utm_campaign: p.get("utm_campaign"),
    referrer: document.referrer || null,
  };
}

export function ConceptLanding({ slug }: { slug: string }) {
  const [concept, setConcept] = useState<ExpConcept | null>(null);
  const [error, setError] = useState(false);
  const [reserveTier, setReserveTier] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [reserved, setReserved] = useState(false);
  const sid = useMemo(sessionId, []);
  const ctx = useMemo(utm, []);

  useEffect(() => {
    fetchConcept(slug)
      .then((c) => {
        setConcept(c);
        // The visit itself — one per page load.
        logExpEvent(slug, { event_type: "view", session_id: sid, ...ctx });
      })
      .catch(() => setError(true));
  }, [slug, sid, ctx]);

  // The money metric: a click THROUGH a real price.
  function onPickTier(t: ExpTier) {
    logExpEvent(slug, { event_type: "intent", tier: t.name, session_id: sid, ...ctx });
    if (t.checkout_url) {
      window.location.href = t.checkout_url; // real Stripe checkout
    } else {
      setReserveTier(t.name);
      document
        .getElementById("reserve")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  async function onReserve(e: React.FormEvent) {
    e.preventDefault();
    if (!email) return;
    logExpEvent(slug, { event_type: "reserve", tier: reserveTier, session_id: sid, ...ctx });
    try {
      await submitLead({
        name: "waitlist",
        email,
        company: slug,
        message: `reserve:${reserveTier ?? ""}`,
        website: "",
        source_url: window.location.href,
      });
    } catch {
      /* the reserve event is already logged — email delivery is best-effort */
    }
    setReserved(true);
  }

  if (error) {
    return (
      <div className="page">
        <section className="section-shell">
          <p className="muted">This page isn't available.</p>
        </section>
      </div>
    );
  }
  if (!concept) return <div className="page" />;

  return (
    <div className="page">
      <header className="hero section-shell">
        <div className="hero-copy">
          <div className="badge">{concept.badge}</div>
          <h1>{concept.headline}</h1>
          <p className="hero-lead">{concept.subhead}</p>
          <div className="hero-actions">
            <a className="cta" href="#pricing">
              See pricing
            </a>
          </div>
          <ul className="hero-points">
            {concept.bullets.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </div>
        <aside className="hero-panel" aria-label="How it works">
          <p className="panel-kicker">How it works</p>
          <ol className="concept-steps">
            {concept.how_it_works.map((s, i) => (
              <li key={s}>
                <span className="concept-step-n">{i + 1}</span>
                {s}
              </li>
            ))}
          </ol>
        </aside>
      </header>

      <main>
        <section id="pricing" className="section-shell">
          <h2>Simple pricing</h2>
          <p className="muted">Start today — cancel anytime.</p>
          <div className="concept-tiers">
            {concept.tiers.map((t) => (
              <article className="concept-tier" key={t.name}>
                <h3>{t.name}</h3>
                <p className="concept-price">{t.price}</p>
                <p className="muted">{t.blurb}</p>
                <button className="cta" type="button" onClick={() => onPickTier(t)}>
                  {t.cta_label}
                </button>
              </article>
            ))}
          </div>
        </section>

        <section id="reserve" className="section-shell form-section">
          <div className="form-intro">
            <h2>{reserved ? "You're on the list" : "Get early access"}</h2>
            <p>
              {reserved
                ? "Thanks — we'll email you the moment it's ready."
                : "We're onboarding the first users now. Leave your email to get in."}
            </p>
          </div>
          {!reserved && (
            <form onSubmit={onReserve} className="lead-form">
              <label>
                Email
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </label>
              <button type="submit">Reserve my spot</button>
            </form>
          )}
        </section>
      </main>

      <footer className="site-footer">
        <a href="/privacy.html">Privacy</a>
        <a href="/terms.html">Terms</a>
        <span>Early access — building in progress.</span>
      </footer>
    </div>
  );
}
