import { useEffect, useMemo, useState } from 'react'

interface LandingConfig {
  enabled: boolean
  cta: string
  headline: string
  subheadline: string
}

interface LeadForm {
  name: string
  email: string
  company: string
  message: string
  website: string
}

interface SignalCard {
  title: string
  description: string
}

interface WorkflowStep {
  title: string
  detail: string
}

type FormStatus = 'idle' | 'submitting' | 'success' | 'error'
type ConfirmState = 'idle' | 'loading' | 'confirmed' | 'error'

const DEFAULT_CONFIG: LandingConfig = {
  enabled: true,
  cta: 'Request Early Access',
  headline: 'Verify resume claims before you waste interview cycles',
  subheadline:
    'Bullshit or Fit cross-checks credentials, employment claims, and public footprint evidence so you can screen with confidence.',
}

const CONFIRM_STATES: Record<ConfirmState, string | null> = {
  idle: null,
  loading: 'Confirming your request...',
  confirmed: 'You are confirmed. We will follow up shortly.',
  error: 'Confirmation failed. Please request another confirmation email.',
}

const SIGNAL_CARDS: SignalCard[] = [
  {
    title: 'Employment continuity',
    description:
      'Cross-check employer claims against public web footprint, domain history, and timeline consistency.',
  },
  {
    title: 'Credential plausibility',
    description:
      'Flag mismatches between claimed education, certifications, and available third-party corroboration.',
  },
  {
    title: 'Identity and location clues',
    description:
      'Review behavioral and source signals that suggest synthetic profiles, spoofing, or credibility gaps.',
  },
]

const WORKFLOW_STEPS: WorkflowStep[] = [
  {
    title: 'Submit candidate details',
    detail: 'Share the claim set you want verified before scheduling interviews.',
  },
  {
    title: 'Review evidence trace',
    detail: 'Bullshit or Fit assembles source-backed findings and confidence indicators.',
  },
  {
    title: 'Decide with your team',
    detail: 'You get a pass, investigate, or decline recommendation with human review in control.',
  },
]

const DECISION_BRIEF_ITEMS: string[] = [
  'Claim-by-claim verdict with confidence notes',
  'Source trail for quick recruiter handoff',
  'Recommended next step: pass, investigate, or decline',
]

export function App() {
  const [config, setConfig] = useState<LandingConfig>(DEFAULT_CONFIG)
  const [form, setForm] = useState<LeadForm>({
    name: '',
    email: '',
    company: '',
    message: '',
    website: '',
  })
  const [status, setStatus] = useState<FormStatus>('idle')
  const [statusMessage, setStatusMessage] = useState('')
  const [resendEmail, setResendEmail] = useState('')
  const [resendMessage, setResendMessage] = useState('')
  const [confirmState, setConfirmState] = useState<ConfirmState>('idle')

  useEffect(() => {
    fetch('/api/landing-config')
      .then((res) => (res.ok ? res.json() : null))
      .then((json: Partial<LandingConfig> | null) => {
        if (!json) return
        setConfig((prev) => ({
          ...prev,
          ...json,
        }))
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const token = params.get('confirm')
    if (!token) return

    setConfirmState('loading')
    fetch(`/api/leads/confirm?token=${encodeURIComponent(token)}`)
      .then(async (res) => {
        if (!res.ok) throw new Error('confirm_failed')
        return res.json()
      })
      .then(() => setConfirmState('confirmed'))
      .catch(() => setConfirmState('error'))
  }, [])

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setStatus('submitting')
    setStatusMessage('')

    try {
      const response = await fetch('/api/leads/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          source_url: window.location.href,
        }),
      })

      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        setStatus('error')
        setStatusMessage(data?.detail || data?.message || 'Submission failed.')
        return
      }

      setStatus('success')
      setStatusMessage(data?.message || 'Submission accepted. Check your email to confirm.')
      setForm({ name: '', email: '', company: '', message: '', website: '' })
      setResendEmail((prev) => prev || form.email)
    } catch {
      setStatus('error')
      setStatusMessage('Submission failed due to a network error.')
    }
  }

  async function onResend(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setResendMessage('')
    if (!resendEmail) return

    const response = await fetch('/api/leads/resend', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: resendEmail }),
    })

    if (!response.ok) {
      setResendMessage('Could not resend confirmation. Try again in a minute.')
      return
    }

    const data = await response.json().catch(() => ({}))
    setResendMessage(data?.message || 'If found, a confirmation email was sent.')
  }

  const confirmMessage = useMemo(() => CONFIRM_STATES[confirmState], [confirmState])

  return (
    <div className="page">
      <header className="hero section-shell">
        <div className="hero-copy">
          <div className="badge">Candidate Risk Screening</div>
          <h1>{config.headline}</h1>
          <p className="hero-lead">{config.subheadline}</p>
          <div className="hero-actions">
            <a className="cta" href="#lead-form">{config.cta}</a>
            <a className="ghost-cta" href="#how-it-works">See process</a>
          </div>
          <ul className="hero-points">
            <li>Evidence-first screening before interview scheduling</li>
            <li>No black-box hiring decisions or opaque scoring</li>
            <li>Built for recruiter and hiring manager workflows</li>
          </ul>
        </div>
        <aside className="hero-panel" aria-label="Verification coverage">
          <p className="panel-kicker">Signal Matrix</p>
          <h2>What gets checked in every screening cycle</h2>
          <ul>
            <li>Resume claim consistency across public sources</li>
            <li>Employer and domain legitimacy indicators</li>
            <li>Credential plausibility and timeline anomalies</li>
            <li>Identity risk clues for synthetic or spoofed profiles</li>
          </ul>
          <p className="panel-note">
            Output is packaged as a decision brief your team can review in minutes.
          </p>
        </aside>
      </header>

      {confirmMessage && <section className={`notice ${confirmState}`}>{confirmMessage}</section>}

      <main>
        <section className="section-shell proof-strip">
          <p>Fast hiring needs fast validation, not guesswork.</p>
          <ul>
            <li>3-step review cycle</li>
            <li>Source-backed evidence trace</li>
            <li>Built-in confirmation workflow</li>
          </ul>
        </section>

        <section className="section-shell">
          <h2>Why good teams still miss fake profiles</h2>
          <p>
            Interviews are expensive, and recruiters are expected to move fast. Inflated titles, fake employers, and synthetic personas can
            slip through when screening depends on manual gut checks.
          </p>
        </section>

        <section className="section-shell">
          <h2>What Bullshit or Fit verifies</h2>
          <p>
            Bullshit or Fit cross-references candidate claims against verifiable web evidence so your team can triage risk before interviews.
          </p>
          <div className="signal-grid">
            {SIGNAL_CARDS.map((card) => (
              <article className="signal-card" key={card.title}>
                <h3>{card.title}</h3>
                <p>{card.description}</p>
              </article>
            ))}
          </div>
        </section>

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

        <section className="section-shell trust-panel">
          <h2>What you get before interview scheduling</h2>
          <ul className="brief-list">
            {DECISION_BRIEF_ITEMS.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p>
            Bullshit or Fit provides screening intelligence, not employment decisions. Human review stays in control.
          </p>
        </section>

        <section id="lead-form" className="section-shell form-section">
          <div className="form-intro">
            <h2>Get early access</h2>
            <p>
              Tell us what roles you are hiring for and we will walk you through the first verification workflow.
            </p>
          </div>
          <div className="form-wrap">
            <form onSubmit={onSubmit} className="lead-form">
              <label>
                Name
                <input
                  value={form.name}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, name: e.target.value }))}
                  required
                />
              </label>
              <label>
                Work Email
                <input
                  type="email"
                  value={form.email}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, email: e.target.value }))}
                  required
                />
              </label>
              <label>
                Company
                <input
                  value={form.company}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, company: e.target.value }))}
                />
              </label>
              <label>
                What role are you hiring for?
                <textarea
                  value={form.message}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setForm((f) => ({ ...f, message: e.target.value }))}
                  rows={4}
                />
              </label>
              <label className="honeypot" aria-hidden="true">
                Website
                <input
                  tabIndex={-1}
                  autoComplete="off"
                  value={form.website}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, website: e.target.value }))}
                />
              </label>
              <button type="submit" disabled={status === 'submitting'}>
                {status === 'submitting' ? 'Submitting...' : 'Request Access'}
              </button>
            </form>
            {statusMessage && <p className={`status ${status}`}>{statusMessage}</p>}

            <form onSubmit={onResend} className="resend-form">
              <h3>Need another confirmation email?</h3>
              <input
                type="email"
                placeholder="you@company.com"
                value={resendEmail}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setResendEmail(e.target.value)}
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
        <span>Verification support only. Final hiring decisions require human review.</span>
      </footer>
    </div>
  )
}
