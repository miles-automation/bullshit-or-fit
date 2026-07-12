import { useEffect, useMemo, useState } from "react";
import {
  type LandingConfig,
  ApiError,
  submitLead,
  resendConfirmation,
  confirmLead,
} from "./api";
import { TrendsTeaser } from "./TrendsTeaser";

interface LeadForm {
  name: string;
  email: string;
  company: string;
  message: string;
  website: string;
}

interface ToolCard {
  title: string;
  href: string;
  description: string;
}

interface WorkflowStep {
  title: string;
  detail: string;
}

type FormStatus = "idle" | "submitting" | "success" | "error";
type ConfirmState = "idle" | "loading" | "confirmed" | "error";

const DEFAULT_CONFIG: LandingConfig = {
  enabled: true,
  cta: "Get early access",
  headline: "Know the market. Make your move.",
  subheadline:
    "Bullshit or Fit maps the tech hiring market — who's actually hiring near you, what your skills are worth, and where demand is heading — so your next move is informed, not a guess.",
};

const CONFIRM_STATES: Record<ConfirmState, string | null> = {
  idle: null,
  loading: "Confirming your request...",
  confirmed: "You are confirmed. We will follow up shortly.",
  error: "Confirmation failed. Please request another confirmation email.",
};

// The three surfaces that are LIVE today — the landing points straight at them.
const TOOL_CARDS: ToolCard[] = [
  {
    title: "Local radar",
    href: "/local",
    description:
      "Every employer reachable from your city, tiered by distance — live openings where we can read the board, plus who's heating up.",
  },
  {
    title: "Your Market",
    href: "/you",
    description:
      "Benchmark your skills, seniority, and comp against live openings. What you're worth, who's hiring you, and what's worth learning next.",
  },
  {
    title: "Market trends",
    href: "/trends",
    description:
      "What tech is actually hiring for — skills on the rise, real comp bands, and the demand-vs-supply balance over time.",
  },
];

const WORKFLOW_STEPS: WorkflowStep[] = [
  {
    title: "See your market",
    detail:
      "Pull the live picture — who's hiring within reach of you and across the remote market, and what they pay.",
  },
  {
    title: "Find your fit",
    detail:
      "Benchmark your skills and comp, spot the gaps worth closing, and surface the roles you actually match.",
  },
  {
    title: "Move with signal",
    detail:
      "Track where demand is heading, so you prepare for where the market's going — not where it's been.",
  },
];

export function App() {
  // Hero copy lives in code now — the product owns it. (The old lead-gen landing
  // pulled headline/CTA from a Spark Swarm config; that funnel is retired.)
  const config: LandingConfig = DEFAULT_CONFIG;
  const [form, setForm] = useState<LeadForm>({
    name: "",
    email: "",
    company: "",
    message: "",
    website: "",
  });
  const [status, setStatus] = useState<FormStatus>("idle");
  const [statusMessage, setStatusMessage] = useState("");
  const [resendEmail, setResendEmail] = useState("");
  const [resendMessage, setResendMessage] = useState("");
  const [confirmState, setConfirmState] = useState<ConfirmState>("idle");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("confirm");
    if (!token) return;

    setConfirmState("loading");
    confirmLead(token)
      .then(() => setConfirmState("confirmed"))
      .catch(() => setConfirmState("error"));
  }, []);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("submitting");
    setStatusMessage("");

    try {
      const data = await submitLead({
        ...form,
        source_url: window.location.href,
      });
      setStatus("success");
      setStatusMessage(
        data?.message || "Submission accepted. Check your email to confirm.",
      );
      setForm({ name: "", email: "", company: "", message: "", website: "" });
      setResendEmail((prev) => prev || form.email);
    } catch (err) {
      setStatus("error");
      if (err instanceof ApiError) {
        setStatusMessage(err.detail || "Submission failed.");
      } else {
        setStatusMessage("Submission failed due to a network error.");
      }
    }
  }

  async function onResend(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setResendMessage("");
    if (!resendEmail) return;

    try {
      const data = await resendConfirmation({ email: resendEmail });
      setResendMessage(
        data?.message || "If found, a confirmation email was sent.",
      );
    } catch {
      setResendMessage("Could not resend confirmation. Try again in a minute.");
    }
  }

  const confirmMessage = useMemo(
    () => CONFIRM_STATES[confirmState],
    [confirmState],
  );

  return (
    <div className="page">
      <header className="hero section-shell">
        <div className="hero-copy">
          <div className="badge">For job seekers</div>
          <h1>{config.headline}</h1>
          <p className="hero-lead">{config.subheadline}</p>
          <div className="hero-actions">
            <a className="cta" href="/local">
              Open the radar
            </a>
            <a className="ghost-cta" href="#lead-form">
              {config.cta}
            </a>
          </div>
          <ul className="hero-points">
            <li>See who's actually hiring within reach of you</li>
            <li>Benchmark your skills and comp against the live market</li>
            <li>Track where demand is heading — skate to the puck</li>
          </ul>
        </div>
        <aside className="hero-panel" aria-label="What you can do today">
          <p className="panel-kicker">Live today</p>
          <h2>Three ways to read the market</h2>
          <ul>
            <li>Local radar — who's around you, and who's hiring</li>
            <li>Your Market — what your skills and comp are worth</li>
            <li>Trends — what the market is actually hiring for</li>
          </ul>
          <p className="panel-note">
            Real openings and real wages — signal, not job-board noise.
          </p>
        </aside>
      </header>

      {confirmMessage && (
        <section className={`notice ${confirmState}`}>{confirmMessage}</section>
      )}

      <main>
        <section className="section-shell proof-strip">
          <p>Real openings. Real wages. Real signal — not job-board noise.</p>
          <ul>
            <li>Live employer + role data</li>
            <li>Location-real comp bands</li>
            <li>Demand trends over time</li>
          </ul>
        </section>

        <section className="section-shell">
          <h2>Read the market three ways</h2>
          <p>
            Most job hunts run on guesswork and job-board noise. Bullshit or Fit
            gives you the live picture — where the work is, what it pays, and
            where it's heading — so you spend your effort where it counts.
          </p>
          <div className="signal-grid">
            {TOOL_CARDS.map((card) => (
              <a className="signal-card" key={card.title} href={card.href}>
                <h3>{card.title} →</h3>
                <p>{card.description}</p>
              </a>
            ))}
          </div>
        </section>

        <TrendsTeaser />

        <section id="how-it-works" className="section-shell">
          <h2>How it works</h2>
          <ol className="workflow">
            {WORKFLOW_STEPS.map((step) => (
              <li key={step.title}>
                <h3>{step.title}</h3>
                <p>{step.detail}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="lead-form" className="section-shell form-section">
          <div className="form-intro">
            <h2>Join the waitlist</h2>
            <p>
              We're building the fuller job-seeker toolkit — a resume
              substance-check, per-posting fit scoring, and more. Leave your
              email to get in early.
            </p>
          </div>
          <div className="form-wrap">
            <form onSubmit={onSubmit} className="lead-form">
              <label>
                Name
                <input
                  value={form.name}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                    setForm((f) => ({ ...f, name: e.target.value }))
                  }
                  required
                />
              </label>
              <label>
                Email
                <input
                  type="email"
                  value={form.email}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                    setForm((f) => ({ ...f, email: e.target.value }))
                  }
                  required
                />
              </label>
              <label>
                Where are you based? (optional)
                <input
                  value={form.company}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                    setForm((f) => ({ ...f, company: e.target.value }))
                  }
                />
              </label>
              <label>
                What are you working toward? (optional)
                <textarea
                  value={form.message}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                    setForm((f) => ({ ...f, message: e.target.value }))
                  }
                  rows={4}
                />
              </label>
              <label className="honeypot" aria-hidden="true">
                Website
                <input
                  tabIndex={-1}
                  autoComplete="off"
                  value={form.website}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                    setForm((f) => ({ ...f, website: e.target.value }))
                  }
                />
              </label>
              <button type="submit" disabled={status === "submitting"}>
                {status === "submitting" ? "Submitting..." : "Join the waitlist"}
              </button>
            </form>
            {statusMessage && (
              <p className={`status ${status}`}>{statusMessage}</p>
            )}

            <form onSubmit={onResend} className="resend-form">
              <h3>Need another confirmation email?</h3>
              <input
                type="email"
                placeholder="you@company.com"
                value={resendEmail}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setResendEmail(e.target.value)
                }
                required
              />
              <button type="submit">Resend confirmation</button>
            </form>
            {resendMessage && <p className="status info">{resendMessage}</p>}
          </div>
        </section>
      </main>

      <footer className="site-footer">
        <a href="/privacy.html">Privacy</a>
        <a href="/terms.html">Terms</a>
        <span>
          Market intelligence, not career or financial advice — just what the
          postings say.
        </span>
      </footer>
    </div>
  );
}
