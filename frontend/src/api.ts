// ---------------------------------------------------------------------------
// api.ts – typed fetch wrapper and endpoint functions
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      (body as Record<string, unknown>)?.detail ??
      (body as Record<string, unknown>)?.message ??
      "Request failed";
    throw new ApiError(res.status, String(detail));
  }
  return body as T;
}

// ---- Response types -------------------------------------------------------

export interface LandingConfig {
  enabled: boolean;
  cta: string;
  headline: string;
  subheadline: string;
}

export interface LeadSubmitResult {
  message: string;
}

export interface LeadResendResult {
  message: string;
}

export interface LeadConfirmResult {
  status: string;
}

// ---- Endpoint functions ---------------------------------------------------

export function fetchLandingConfig(): Promise<Partial<LandingConfig>> {
  return apiFetch<Partial<LandingConfig>>("/api/landing-config");
}

export interface LeadSubmitIn {
  name: string;
  email: string;
  company: string;
  message: string;
  website: string;
  source_url: string;
}

export function submitLead(payload: LeadSubmitIn): Promise<LeadSubmitResult> {
  return apiFetch<LeadSubmitResult>("/api/leads/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export interface LeadResendIn {
  email: string;
}

export function resendConfirmation(
  payload: LeadResendIn,
): Promise<LeadResendResult> {
  return apiFetch<LeadResendResult>("/api/leads/resend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function confirmLead(token: string): Promise<LeadConfirmResult> {
  return apiFetch<LeadConfirmResult>(
    `/api/leads/confirm?token=${encodeURIComponent(token)}`,
  );
}
