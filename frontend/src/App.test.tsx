import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { App } from './App'

interface MockFetchOverrides {
  landingConfig?: Record<string, unknown>
  confirmOk?: boolean
  submitOk?: boolean
  submitMessage?: string
  submitError?: string
  resendOk?: boolean
}

function mockFetch(overrides: MockFetchOverrides = {}) {
  return vi.fn((url: string) => {
    if (url === '/api/v1/landing-config') {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(overrides.landingConfig ?? {}),
      })
    }
    if (url.startsWith('/api/v1/leads/confirm')) {
      const ok = overrides.confirmOk ?? true
      return Promise.resolve({
        ok,
        json: () => (ok ? Promise.resolve({ status: 'confirmed' }) : Promise.reject()),
      })
    }
    if (url === '/api/v1/leads/submit') {
      const ok = overrides.submitOk ?? true
      const status = ok ? 200 : 422
      return Promise.resolve({
        ok,
        status,
        json: () =>
          Promise.resolve(
            ok
              ? { message: overrides.submitMessage ?? 'Submission accepted. Check your email to confirm.' }
              : { detail: overrides.submitError ?? 'Submission failed.' },
          ),
      })
    }
    if (url === '/api/v1/leads/resend') {
      const ok = overrides.resendOk ?? true
      return Promise.resolve({
        ok,
        json: () => Promise.resolve({ message: 'If found, a confirmation email was sent.' }),
      })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

describe('App', () => {
  let originalFetch: typeof globalThis.fetch

  beforeEach(() => {
    originalFetch = globalThis.fetch
    globalThis.fetch = mockFetch() as unknown as typeof globalThis.fetch
    // Reset URL to avoid confirm token side effects
    Object.defineProperty(window, 'location', {
      value: { href: 'http://localhost/', search: '' },
      writable: true,
    })
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  describe('rendering', () => {
    it('renders the default headline and CTA', async () => {
      render(<App />)
      expect(screen.getByText('Know the market. Make your move.')).toBeInTheDocument()
      expect(screen.getByText('Get early access')).toBeInTheDocument()
    })

    it('renders all tool cards', () => {
      render(<App />)
      expect(screen.getByRole('heading', { name: /Local radar/ })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: /Your Market/ })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: /Market trends/ })).toBeInTheDocument()
    })

    it('renders workflow steps', () => {
      render(<App />)
      expect(screen.getByText('See your market')).toBeInTheDocument()
      expect(screen.getByText('Find your fit')).toBeInTheDocument()
      expect(screen.getByText('Move with signal')).toBeInTheDocument()
    })

    it('renders the disclaimer in the footer', () => {
      render(<App />)
      expect(
        screen.getByText('Market intelligence, not career or financial advice — just what the postings say.'),
      ).toBeInTheDocument()
    })

    it('renders the lead form with required fields', () => {
      render(<App />)
      expect(screen.getByText('Name')).toBeInTheDocument()
      expect(screen.getByText('Email')).toBeInTheDocument()
      expect(screen.getByText('Where are you based? (optional)')).toBeInTheDocument()
      expect(screen.getByText('What are you working toward? (optional)')).toBeInTheDocument()
    })

    it('renders privacy and terms links in footer', () => {
      render(<App />)
      expect(screen.getByText('Privacy')).toHaveAttribute('href', '/privacy.html')
      expect(screen.getByText('Terms')).toHaveAttribute('href', '/terms.html')
    })
  })

  describe('lead form submission', () => {
    it('submits form and shows success message', async () => {
      const user = userEvent.setup()
      globalThis.fetch = mockFetch() as unknown as typeof globalThis.fetch
      render(<App />)

      const nameInput = screen.getByRole('textbox', { name: /name/i })
      const emailInput = screen.getByRole('textbox', { name: 'Email' })

      await user.type(nameInput, 'Jane Recruiter')
      await user.type(emailInput, 'jane@example.com')

      const submitButton = screen.getByRole('button', { name: /join the waitlist/i })
      await user.click(submitButton)

      await waitFor(() => {
        expect(screen.getByText('Submission accepted. Check your email to confirm.')).toBeInTheDocument()
      })
    })

    it('shows submitting state while request is in flight', async () => {
      const user = userEvent.setup()
      let resolveSubmit: (value: unknown) => void
      globalThis.fetch = vi.fn((url: string) => {
        if (url === '/api/v1/landing-config') {
          return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
        }
        if (url === '/api/v1/leads/submit') {
          return new Promise((resolve) => {
            resolveSubmit = resolve
          })
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }) as unknown as typeof globalThis.fetch

      render(<App />)

      const nameInput = screen.getByRole('textbox', { name: /name/i })
      const emailInput = screen.getByRole('textbox', { name: 'Email' })
      await user.type(nameInput, 'Jane')
      await user.type(emailInput, 'jane@co.com')

      const submitButton = screen.getByRole('button', { name: /join the waitlist/i })
      await user.click(submitButton)

      expect(screen.getByRole('button', { name: /submitting/i })).toBeDisabled()

      // Resolve to clean up
      resolveSubmit!({ ok: true, json: () => Promise.resolve({ message: 'OK' }) })
    })

    it('shows error message on submission failure', async () => {
      const user = userEvent.setup()
      globalThis.fetch = mockFetch({ submitOk: false, submitError: 'Rate limit exceeded.' }) as unknown as typeof globalThis.fetch
      render(<App />)

      const nameInput = screen.getByRole('textbox', { name: /name/i })
      const emailInput = screen.getByRole('textbox', { name: 'Email' })
      await user.type(nameInput, 'Jane')
      await user.type(emailInput, 'jane@co.com')

      await user.click(screen.getByRole('button', { name: /join the waitlist/i }))

      await waitFor(() => {
        expect(screen.getByText('Rate limit exceeded.')).toBeInTheDocument()
      })
    })

    it('shows network error message when fetch throws', async () => {
      const user = userEvent.setup()
      globalThis.fetch = vi.fn((url: string) => {
        if (url === '/api/v1/landing-config') return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
        if (url === '/api/v1/leads/submit') return Promise.reject(new Error('Network failure'))
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }) as unknown as typeof globalThis.fetch

      render(<App />)

      const nameInput = screen.getByRole('textbox', { name: /name/i })
      const emailInput = screen.getByRole('textbox', { name: 'Email' })
      await user.type(nameInput, 'Jane')
      await user.type(emailInput, 'jane@co.com')

      await user.click(screen.getByRole('button', { name: /join the waitlist/i }))

      await waitFor(() => {
        expect(screen.getByText('Submission failed due to a network error.')).toBeInTheDocument()
      })
    })

    it('clears form fields after successful submission', async () => {
      const user = userEvent.setup()
      globalThis.fetch = mockFetch() as unknown as typeof globalThis.fetch
      render(<App />)

      const nameInput = screen.getByRole('textbox', { name: /name/i })
      const emailInput = screen.getByRole('textbox', { name: 'Email' })
      await user.type(nameInput, 'Jane')
      await user.type(emailInput, 'jane@co.com')

      await user.click(screen.getByRole('button', { name: /join the waitlist/i }))

      await waitFor(() => {
        expect(nameInput).toHaveValue('')
        expect(emailInput).toHaveValue('')
      })
    })
  })

  describe('resend confirmation', () => {
    it('sends resend request and shows feedback', async () => {
      const user = userEvent.setup()
      globalThis.fetch = mockFetch() as unknown as typeof globalThis.fetch
      render(<App />)

      const resendInput = screen.getByPlaceholderText('you@company.com')
      await user.type(resendInput, 'test@example.com')

      const resendButton = screen.getByRole('button', { name: /resend confirmation/i })
      await user.click(resendButton)

      await waitFor(() => {
        expect(screen.getByText('If found, a confirmation email was sent.')).toBeInTheDocument()
      })
    })

    it('shows error when resend fails', async () => {
      const user = userEvent.setup()
      globalThis.fetch = mockFetch({ resendOk: false }) as unknown as typeof globalThis.fetch
      render(<App />)

      const resendInput = screen.getByPlaceholderText('you@company.com')
      await user.type(resendInput, 'test@example.com')

      await user.click(screen.getByRole('button', { name: /resend confirmation/i }))

      await waitFor(() => {
        expect(screen.getByText('Could not resend confirmation. Try again in a minute.')).toBeInTheDocument()
      })
    })
  })

  describe('email confirmation flow', () => {
    it('shows confirmed state when token is valid', async () => {
      Object.defineProperty(window, 'location', {
        value: { href: 'http://localhost/?confirm=abc123', search: '?confirm=abc123' },
        writable: true,
      })
      globalThis.fetch = mockFetch({ confirmOk: true }) as unknown as typeof globalThis.fetch

      render(<App />)

      await waitFor(() => {
        expect(screen.getByText('You are confirmed. We will follow up shortly.')).toBeInTheDocument()
      })
    })

    it('shows error state when confirmation fails', async () => {
      Object.defineProperty(window, 'location', {
        value: { href: 'http://localhost/?confirm=bad', search: '?confirm=bad' },
        writable: true,
      })
      globalThis.fetch = mockFetch({ confirmOk: false }) as unknown as typeof globalThis.fetch

      render(<App />)

      await waitFor(() => {
        expect(
          screen.getByText('Confirmation failed. Please request another confirmation email.'),
        ).toBeInTheDocument()
      })
    })

    it('shows loading state during confirmation', async () => {
      Object.defineProperty(window, 'location', {
        value: { href: 'http://localhost/?confirm=abc', search: '?confirm=abc' },
        writable: true,
      })
      let resolveConfirm: (value: unknown) => void
      globalThis.fetch = vi.fn((url: string) => {
        if (url === '/api/v1/landing-config') {
          return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
        }
        if (url.startsWith('/api/v1/leads/confirm')) {
          return new Promise((resolve) => {
            resolveConfirm = resolve
          })
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }) as unknown as typeof globalThis.fetch

      render(<App />)

      expect(screen.getByText('Confirming your request...')).toBeInTheDocument()

      // Clean up
      resolveConfirm!({ ok: true, json: () => Promise.resolve({}) })
    })
  })
})
