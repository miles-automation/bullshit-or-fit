# Task 600 acceptance evidence

Implementation branch: `feature/600`. Target: reviewed PR only; production activation is not authorized by this task. Weaver was unavailable for this desktop session; coordination used a native Codex subagent and Spark Swarm task 600 comments. No Weaver delivery is claimed.

## Real corpus and reproducibility

On September 7, 2026, the new collector retrieved seven primary SEC documents for Airbnb, Cloudflare and Twilio: two original annual periods for each issuer, plus Cloudflare's 2026-04-29 10-K/A. The [first live ingest](first-ingest.json) records official source URLs, forms, dates, byte counts and successful parse results. Documents total 15,386,440 bytes. All original HTML bytes, retrieval metadata and hashes were stored separately from derived text.

The [initial replay](replay-ingest.json) made zero HTTP requests and stored no duplicate filings. Early development rules emitted 322 candidates; an intermediate [rebuild](rebuild.json) emitted 288 after the first precision fixes. These files retain their actual historical counts rather than rewriting earlier execution evidence.

The authoritative final [PostgreSQL validation](postgres-validation.json) used those same stored public documents and retrieval metadata, copied from the initial SQLite sample into an isolated PostgreSQL 16 database without downloading documents again. The complete Alembic migration chain through 0016 succeeded. Final rule configuration emitted **215 candidates**, and two consecutive offline rebuilds each returned 7 filings / 215 candidates / 0 failures. Final replay retained **7 raw filings, 7 normalized documents, 215 candidates**, made **0 HTTP requests**, and used three cached submissions responses. The Cloudflare amendment remains a separate accession linked to the unique original for its report period.

The [sample JSON export](sample-signals.json) includes exact excerpts, stable offsets, filing identity/dates, source URLs, parser and rule versions, hashes and assertion labels. It selects one example of each category/assertion pair to aid inspection, not to estimate accuracy. Every one of the 215 final real-sample excerpts was checked against its normalized-document offsets, and its stored raw hash and extractor version were verified. Behavioral tests separately exercise the same provenance contract. No confidence percentages, company scores or event-date guesses are produced.

## Independent example inspection

A separate parent agent inspected 14 actual excerpts across the five categories, including reported events, generic boilerplate, ambiguous mixtures and false positives. That inspection found meaningful defects in the first rules and drove context filters, category-specific intention patterns and conservative mixed-assertion handling. The following cases record the inspection and resulting behavior; paragraph indices address the normalized documents, not raw HTML.

| Filing / paragraph | Independent observation | Result after fixes |
|---|---|---|
| Cloudflare 0001477333-26-000026 / 119 | Generic ability-to-retain-personnel list | Unknown, no hiring event asserted |
| Cloudflare amendment / 174 | Director's AI expertise is not issuer AI investment | Excluded |
| Cloudflare amendment / 185 | Another director biography mentions product development | Excluded |
| Cloudflare 0001477333-26-000016 / 334 | Product/R&D expansion discussion lacks a clear dated event | Unknown |
| Cloudflare / 410 | Demand paragraph mixes historical adverse conditions and future risk | Unknown |
| Cloudflare / 439 | Brand promotion, new markets and mixed spending expectations | Unknown |
| Cloudflare / 511 | Actual headcount growth, planned expansion and hypothetical constraints coexist | Unknown |
| Cloudflare / 545 | Historical hiring difficulty and future retention risk coexist | Unknown |
| Cloudflare / 618 | AI competition/obsolescence risk | Hypothetical risk |
| Cloudflare / 957 | Debt restructuring is not workforce restructuring | Workforce candidate excluded |
| Twilio 0001447669-26-000021 / 477 | AI plans must not label a nearby hiring dependency as a hiring intention | Hiring candidate unknown |
| Twilio / 480 | Product/regulatory restructuring is not workforce restructuring | Workforce candidate excluded |
| Twilio / 726 | Explicit intention to use AI and automate processes | Forward-looking intention |
| Twilio / 1678 | Explicit February 2023 workforce-reduction announcement in a 2026 filing | Reported event; event date remains null, 2023 preserved in excerpt |
| Airbnb accounting exclusion | Excluding restructuring costs from a metric does not negate restructuring | No negated workforce claim |

The counts are not precision/recall measurements. Candidate false positives and missed events remain possible. Unknown is intentionally common. A product discussing AI is not necessarily investing in internal automation; a workforce plan may be historical; table lines and page breaks may remove useful context. This first slice provides inspectable evidence rather than semantic certainty.

## Checks and self-review

- `make check`: passed; **227 backend tests**, including **32 SEC tests**; **21 frontend tests**; Ruff lint/format; mypy across 46 application files; frontend TypeScript.
- `npm run build`: passed (the repository CI's additional frontend build).
- Real PostgreSQL migration chain: passed through 0016. [SEC schema/model comparison](schema-validation.json): no differences.
- Optional global `alembic check` detects pre-existing non-SEC index-name drift in experiment, ATS, HN and commute-shed tables. No SEC differences. Those existing models/migrations were not modified by this task.
- Behavioral coverage: replay/deduplication, download-budget resumption, separate amendments, malformed arrays/documents, failed-download retry, bounded response/retry behavior, shared request locking/cache, parser table/paragraph boundaries, exact excerpt provenance, risk/negation/mixed context, custom-rule fingerprints, unmatched mappings, and disabled/unavailable refresh isolation.
- Self-review: inspected migrations/models, raw-versus-derived storage, official URL restrictions and response limits, processing bounds, byte/hash/offset provenance, rule-version updates, CLI filters/truncation/error exits, bounded-memory exports/rebuilds, worker isolation, absence of new explanatory internal comments/docstrings, and the final diff. No runtime model calls, production configuration changes or job-row coupling.

Independent code review is a separate remaining gate after the PR is published. Passing tests and sample inspection alone are not final acceptance.

## Cleanup state at review handoff

The worktree and branch remain active for independent review and any requested changes; owner is the task 600 implementer. Public-sample scratch databases/cache and the task-only PostgreSQL container will be removed after reviewer access is no longer needed. Required evidence is versioned here. No production process, unrelated worktree, or shared dependency cache has been changed or removed. Task 600 remains open until its agreed delivery and cleanup are verified.
