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
  return apiFetch<Partial<LandingConfig>>("/api/v1/landing-config");
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
  return apiFetch<LeadSubmitResult>("/api/v1/leads/submit", {
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
  return apiFetch<LeadResendResult>("/api/v1/leads/resend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function confirmLead(token: string): Promise<LeadConfirmResult> {
  return apiFetch<LeadConfirmResult>(
    `/api/v1/leads/confirm?token=${encodeURIComponent(token)}`,
  );
}

// ---- jobtrends (hiring-market dashboard) ----------------------------------

export interface Mover {
  keyword: string;
  category: string;
  latest_share: number;
  mom_delta_pts: number;
}

export interface JobtrendsSummary {
  first_month: string | null;
  latest_month: string | null;
  months: number;
  total_posts: number;
  distinct_authors: number;
  recurring_pct: number;
  comp_coverage_pct: number;
  comp_median_usd: number;
  risers: Mover[];
  fallers: Mover[];
}

export interface TrendSeries {
  keyword: string;
  category: string;
  shares: (number | null)[];
  mom_delta_pts: number | null;
}

export interface TrendResponse {
  months: string[];
  series: TrendSeries[];
}

export interface CompMonth {
  month: string;
  posts_with_comp: number;
  posts_total: number;
  coverage_pct: number;
  p25_usd: number;
  median_usd: number;
  p75_usd: number;
}

export interface CohortMonth {
  month: string;
  active: number;
  new: number;
  returning: number;
  churned: number;
}

export interface ChurnResponse {
  distinct_authors: number;
  recurring_authors: number;
  recurring_pct: number;
  months: CohortMonth[];
}

export interface KeywordOption {
  keyword: string;
  category: string;
}

export const fetchSummary = () =>
  apiFetch<JobtrendsSummary>("/api/v1/jobtrends/summary");

export const fetchKeywords = () =>
  apiFetch<KeywordOption[]>("/api/v1/jobtrends/keywords");

export const fetchTrend = (keywords: string[]) =>
  apiFetch<TrendResponse>(
    `/api/v1/jobtrends/trend?keywords=${encodeURIComponent(keywords.join(","))}`,
  );

export const fetchComp = () =>
  apiFetch<{ months: CompMonth[] }>("/api/v1/jobtrends/comp");

export const fetchChurn = () =>
  apiFetch<ChurnResponse>("/api/v1/jobtrends/churn");
