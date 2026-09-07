# SEC annual filings and text evidence

BullshitOrFit can collect 10-K filings separately from job postings and identify inspectable text evidence candidates. This is a deterministic paragraph/rule extractor, not a company-health score, prediction, or recommendation. It makes no paid data or model API calls.

## Run locally

Use the existing PostgreSQL database and migration workflow. SEC ingestion is disabled in the worker until `SEC_ENABLED=true`. CLI ingestion is an explicit one-shot operation and does not require that switch.

```sh
make migrate
cd backend
export SEC_CONTACT_EMAIL=hello@sparkswarm.com
export SEC_CACHE_DIR=/absolute/shared/path/sec-cache
uv run python -m app.jobtrends.sec.cli registry
uv run python -m app.jobtrends.sec.cli ingest --cik 1559720 --periods 2 --max-filings 3
uv run python -m app.jobtrends.sec.cli signals --cik 1559720 --since 2025-01-01 --limit 20
uv run python -m app.jobtrends.sec.cli signals --category workforce_restructuring --form 10-K --limit 20
uv run python -m app.jobtrends.sec.cli rebuild --cik 1559720 --limit 10
```

Rich authorized `hello@sparkswarm.com`, an existing public Spark Swarm contact, for the development sample. It is not a built-in default. Deployments must explicitly configure an authorized contact. Never use a fabricated address. This assignment does not enable or deploy the production source.

`SEC_CACHE_DIR` defaults to `.cache/sec` relative to the process working directory. All local SEC clients must share an absolute directory to coordinate throttling; container replicas must share its volume or run only one SEC collector. The default is two requests per second, shared through a file lock. The client retries transport failures, 429, and selected 5xx responses up to four attempts with bounded backoff. Other HTTP errors are reported immediately. Responses are bounded to 30 MB, redirects are rejected, and only official SEC HTTPS hosts are accepted. Submissions JSON is cached for 24 hours; successfully stored filing documents are not downloaded again.

`--max-filings` bounds document download attempts, not already-cached rows. Rerunning resumes past cached filings. Discovery also searches up to 20 historical submissions files when needed and reports incomplete annual-period coverage. The worker refreshes on its existing daily interval. Failed issuer discovery or filing retrieval is visible in summaries/logs; per-filing download failures are persisted and retried next run. The CLI returns nonzero when failures occur. An SEC outage cannot stop the other sources.

## Identity and provenance

The JSON registry contains ten explicitly mapped Greenhouse employers: Airbnb, Cloudflare, Affirm, Pinterest, Reddit, GitLab, Twilio, Lyft, Asana, and Coinbase. CIK identity was checked against SEC records on September 7, 2026; each row retains its official submissions URL. Provider/token pairs match the existing curated employer registry. These are exact maintained mappings, not inferred subsidiary relationships. `registry` lists every unmatched seed employer without asserting why it is unmatched. Customize with `SEC_REGISTRY_PATH` or the CLI's global `--registry` argument.

Discovery chooses two recent **reporting periods with original 10-Ks**, not calendar filing years. 10-K/A amendments remain separate records and link to an original only when a unique original for that reporting period is known. An amendment may cover only Part III; it never substitutes for a complete report. Other annual forms, financial fact normalization, and issuer-parent inference are outside this version.

Three tables in the existing `jobtrends` schema keep SEC records distinct from jobs:

- `sec_filings`: accession, CIK, employer mapping, form, filing/report dates, official URL, original document bytes, SHA-256, retrieval/attempt timestamps and download error. Accession is the deduplication key.
- `sec_documents`: rebuildable normalized text, parser/extractor versions, raw hash, parse status/error and best-effort section status.
- `sec_signals`: rebuildable evidence candidates with category, rule/version, assertion, exact excerpt, paragraph index, character offsets and optional section.

Offsets address Python Unicode characters in the normalized document, not raw HTML bytes. Every exported excerpt must equal `normalized_text[start_offset:end_offset]`. HTML normalization keeps block/paragraph boundaries and readable table cells/rows, removes scripts and hidden inline-XBRL material, and preserves the original bytes independently. Unsupported/malformed documents remain raw with an explicit parse failure. Section identification is best effort: unknown is a legitimate result. Layout can split a paragraph across page boundaries, and tables lose visual alignment.

## Signal semantics and customization

The five categories cover workforce restructuring, hiring/retention constraints, automation/AI, demand/customer concentration, and expansion/capital investment. Category membership means **inspect this evidence**, not that an event occurred. Workforce rules require employee/workforce context; automation/AI rules require operational, product, implementation, or investment context and exclude common director-selection biographies.

Assertions are conservative labels: `reported_event`, `forward_looking_intention`, `hypothetical_risk`, `negated`, or `unknown`. Conflicting past/future/risk cues produce `unknown`. Category-specific patterns prevent a statement about AI plans from labeling a nearby hiring risk as a hiring intention. Ordinary risk text cannot become a reported layoff merely because it mentions restructuring. Accounting exclusions do not negate an event. Unknown text and candidate false positives remain visible for inspection.

Event dates are **not inferred**: exports set `event_date` to null and retain the full excerpt, filing date and report date. For example, Twilio's 2026 filing describes a February 2023 workforce reduction. Filing date filters must not be interpreted as event date filters. AI product strategy is not necessarily internal automation investment. Historical numeric facts embedded in otherwise hypothetical paragraphs still require reading the evidence.

Rules live in `app/jobtrends/sec/rules.json`; override with `SEC_RULES_PATH` or global `--rules`. Each category has candidate/context/exclusion patterns and reported/intention patterns. The extractor version includes the declared version and a hash of the entire rule configuration. Bump the declared version for classification algorithm changes and `PARSER_VERSION` for normalization changes. Ingest refreshes outdated derived versions without refetching raw documents; `rebuild` explicitly recomputes them offline. A future semantic implementation can replace the pure `extract(text, rules)` step while retaining the evidence/provenance storage contract.

`signals` always queries source `sec`; filters include CIK, category, filing-date bounds and form. Results are bounded and explicitly report truncation. JSON exports are intended for research and future read-only tooling; no new HTTP/MCP endpoint or dashboard is added here.

## Validation

See [task 600 evidence](evidence/sec-600/README.md) for real filing counts, replay/rebuild results, independently inspected examples, required checks and remaining limitations.

Primary references: [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [SEC developer access policy](https://www.sec.gov/about/developer-resources).
