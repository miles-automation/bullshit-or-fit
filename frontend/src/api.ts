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
  seekers_per_100_jobs: number;
  data_updated: string | null;
  risers: Mover[];
  fallers: Mover[];
}

export interface MarketMonth {
  month: string;
  hiring_posts: number;
  wants_hired_posts: number;
  seekers_per_100_jobs: number;
}

export interface CompanyOpenings {
  company_name: string;
  company_token: string;
  open_roles: number;
}

export interface CompaniesResponse {
  total_open: number;
  companies: number;
  top: CompanyOpenings[];
}

export interface NameCount {
  name: string;
  open_roles: number;
}

export interface RemoteResponse {
  total_open: number;
  companies: number;
  top_companies: NameCount[];
  top_categories: NameCount[];
}

export interface UsaJobsResponse {
  total_open: number;
  agencies: number;
  top_agencies: NameCount[];
  top_categories: NameCount[];
}

export interface Skill {
  keyword: string;
  category: string;
  roles_matched: number;
  share: number;
  by_source: Record<string, number>;
}

export interface SkillsResponse {
  total_roles: number;
  sources: string[];
  skills: Skill[];
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

export interface CompSource {
  source: string;
  n_roles: number;
  n_with_comp: number;
  coverage_pct: number;
  p25_usd: number;
  median_usd: number;
  p75_usd: number;
}

export interface CompSourcesResponse {
  sources: CompSource[];
}

export interface WageBand {
  area_code: string;
  area_name: string;
  p10_usd: number;
  p25_usd: number;
  median_usd: number;
  p75_usd: number;
  p90_usd: number;
}

export interface WagesResponse {
  occupation: string;
  area: WageBand | null;
  national: WageBand | null;
  areas: { code: string; name: string }[];
}

export interface SkillSignal {
  skill: string;
  category: string;
  demand_share: number;
  mom_delta_pts: number | null;
  trajectory: string; // 'rising' | 'flat' | 'falling'
}

export interface RoleMatch {
  company: string;
  title: string;
  source: string;
  skills_matched: number;
  comp_median_usd: number | null;
}

export interface MarketFitResponse {
  skills: string[];
  seniority: string | null;
  comp_usd: number | null;
  comp_n: number;
  comp_p25_usd: number;
  comp_median_usd: number;
  comp_p75_usd: number;
  comp_verdict: string; // 'under' | 'fit' | 'over' | 'unknown'
  comp_delta_pct: number | null;
  skill_signals: SkillSignal[];
  gaps: SkillSignal[];
  matching_roles: number;
  top_roles: RoleMatch[];
}

export interface WarnMonth {
  month: string;
  notices: number;
  employees_affected: number;
}

export interface WarnNotice {
  company: string;
  state: string;
  city: string | null;
  employees_affected: number | null;
  notice_date: string | null;
}

export interface WarnState {
  state: string;
  notices: number;
  employees_affected: number;
}

export interface LayoffsResponse {
  total_notices: number;
  total_employees: number;
  states: string[];
  months: WarnMonth[];
  recent: WarnNotice[];
  by_state: WarnState[];
}

export interface CompanyPay {
  company_name: string;
  company_token: string;
  n_with_comp: number;
  p25_usd: number;
  median_usd: number;
  p75_usd: number;
}

export interface CompanyPayResponse {
  companies: CompanyPay[];
}

export interface MetroCount {
  metro: string;
  n_roles: number;
}

export interface GeoSource {
  source: string;
  total: number;
  remote_pct: number;
  top_metros: MetroCount[];
}

export interface GeoResponse {
  sources: GeoSource[];
}

export interface SkillCompCell {
  n_with_comp: number;
  p25_usd: number;
  median_usd: number;
  p75_usd: number;
}

export interface SkillCompRow {
  keyword: string;
  category: string;
  total_n: number;
  by_source: Record<string, SkillCompCell>;
}

export interface SkillCompResponse {
  sources: string[];
  skills: SkillCompRow[];
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

export interface ShedRole {
  company: string;
  title: string;
  location: string | null;
  url: string | null;
  comp_min: number | null;
  comp_max: number | null;
  is_new: boolean;
}

export interface ShedEmployer {
  token: string;
  name: string;
  category: string;
  hq_city: string | null;
  hq_state: string | null;
  distance_mi: number | null;
  careers_url: string;
  notes: string | null;
  has_feed: boolean;
  open_roles: number | null;
  new_roles: number;
}

export interface ShedTier {
  tier: string;
  label: string;
  open_roles: number;
  employers: ShedEmployer[];
}

export interface CommuteShedResponse {
  home: string;
  total_employers: number;
  total_open_roles: number;
  new_roles: number;
  trajectory_days: number;
  tiers: ShedTier[];
  roles: ShedRole[];
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

export const fetchCompSources = () =>
  apiFetch<CompSourcesResponse>("/api/v1/jobtrends/comp/sources");

export const fetchChurn = () =>
  apiFetch<ChurnResponse>("/api/v1/jobtrends/churn");

export const fetchMarket = () =>
  apiFetch<{ months: MarketMonth[] }>("/api/v1/jobtrends/market");

export const fetchCompanies = () =>
  apiFetch<CompaniesResponse>("/api/v1/jobtrends/companies");

export const fetchRemote = () =>
  apiFetch<RemoteResponse>("/api/v1/jobtrends/remote");

export const fetchUsaJobs = () =>
  apiFetch<UsaJobsResponse>("/api/v1/jobtrends/usajobs");

export const fetchSkills = () =>
  apiFetch<SkillsResponse>("/api/v1/jobtrends/skills");

export const fetchSkillComp = () =>
  apiFetch<SkillCompResponse>("/api/v1/jobtrends/skills/comp");

export const fetchLocations = () =>
  apiFetch<GeoResponse>("/api/v1/jobtrends/locations");

export const fetchCompanyPay = () =>
  apiFetch<CompanyPayResponse>("/api/v1/jobtrends/companies/pay");

export const fetchLayoffs = () =>
  apiFetch<LayoffsResponse>("/api/v1/jobtrends/layoffs");

export const fetchWages = (area: string) =>
  apiFetch<WagesResponse>(`/api/v1/jobtrends/wages?area=${encodeURIComponent(area)}`);

export const fetchLocal = () =>
  apiFetch<CommuteShedResponse>("/api/v1/jobtrends/local");

export const fetchMarketFit = (
  skills: string[],
  seniority: string | null,
  comp: number | null,
) => {
  const p = new URLSearchParams({ skills: skills.join(",") });
  if (seniority) p.set("seniority", seniority);
  if (comp) p.set("comp", String(comp));
  return apiFetch<MarketFitResponse>(`/api/v1/jobtrends/market-fit?${p.toString()}`);
};
