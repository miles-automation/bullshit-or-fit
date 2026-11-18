from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.jobtrends.decisions import TrackerRow
from app.jobtrends.fit import (
    ACTIVE_APPLICATION_STATUSES,
    build_decided_index,
    company_id,
    normalize_company,
    normalize_title,
    posting_keys,
    url_key,
)

FEEDBACK_SCHEMA = "bof.fit-feedback/v1"
MORNING_SCHEMA = "bof.morning/v1"
CLAIM_SCHEMA = "bof.claim/v1"
ANSWER_SCHEMA = "bof.answer/v1"
OWNER = "owner"
AGENT = "agent"
OWNER_FILE = "owner-feedback.jsonl"
AGENT_FILE = "agent-suggestions.jsonl"
DECISIONS = frozenset({"skip_role", "good_fit", "skip_company", "note"})
OWNER_AUTHOR = "rich"
SHORTLIST_SIZE = 3
MAX_ACTIVE_PER_COMPANY = 1
STRONG_FIT_RULE = (
    "official posting URL known, pay listed, listed on the company's own site"
)
ANSWER_KINDS = frozenset({"owner_writing", "approved_final", "sent"})
DRAFT_ANSWER_KINDS = frozenset({"owner_writing", "approved_final"})


class RecordError(ValueError):
    pass


@dataclass(frozen=True)
class Feedback:
    id: str
    role_id: str | None
    company_id: str
    source: str
    decision: str
    reason: str
    author: str
    author_kind: str
    created_at: str
    evidence: str | None = None


def feedback_id(
    role_id: str | None, company: str, decision: str, reason: str, author: str
) -> str:
    raw = json.dumps(
        [role_id, company, decision, reason.strip(), author], sort_keys=True
    )
    return "fb_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _read_jsonl(path: Path, schema: str) -> list[tuple[int, dict[str, Any]]]:
    if not path.exists():
        return []
    records: list[tuple[int, dict[str, Any]]] = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordError(f"{path.name}:{number}: not JSON ({exc.msg})") from exc
        if record.get("schema") != schema:
            raise RecordError(f"{path.name}:{number}: schema must be {schema}")
        records.append((number, record))
    return records


def _feedback(path: Path, kind: str) -> list[Feedback]:
    items: list[Feedback] = []
    for number, r in _read_jsonl(path, FEEDBACK_SCHEMA):
        where = f"{path.name}:{number}"
        decision = r.get("decision")
        if decision not in DECISIONS:
            raise RecordError(f"{where}: decision must be one of {sorted(DECISIONS)}")
        company = r.get("company_id") or ""
        if not company.startswith("co:"):
            raise RecordError(f"{where}: company_id must start with co:")
        role = r.get("role_id")
        if decision in {"skip_role", "good_fit"} and not role:
            raise RecordError(f"{where}: {decision} needs a role_id")
        author = (r.get("author") or "").strip()
        reason = (r.get("reason") or "").strip()
        if not author or not reason or not r.get("created_at"):
            raise RecordError(f"{where}: author, reason and created_at are required")
        evidence = r.get("evidence")
        if kind == OWNER and author != OWNER_AUTHOR and not evidence:
            raise RecordError(
                f"{where}: owner feedback written by {author} needs the owner's words in evidence"
            )
        items.append(
            Feedback(
                id=r.get("id") or feedback_id(role, company, decision, reason, author),
                role_id=role,
                company_id=company,
                source=r.get("source") or "unknown",
                decision=decision,
                reason=reason,
                author=author,
                author_kind=kind,
                created_at=r["created_at"],
                evidence=evidence,
            )
        )
    return items


def load_feedback(directory: Path) -> list[Feedback]:
    return _feedback(directory / OWNER_FILE, OWNER) + _feedback(
        directory / AGENT_FILE, AGENT
    )


def append_feedback(
    directory: Path,
    *,
    kind: str,
    company: str,
    decision: str,
    reason: str,
    author: str,
    source: str,
    role_id: str | None = None,
    evidence: str | None = None,
    now: datetime | None = None,
) -> tuple[str, bool]:
    if kind not in {OWNER, AGENT}:
        raise RecordError("kind must be owner or agent")
    path = directory / (OWNER_FILE if kind == OWNER else AGENT_FILE)
    record_id = feedback_id(role_id, company, decision, reason, author)
    record = {
        "schema": FEEDBACK_SCHEMA,
        "id": record_id,
        "role_id": role_id,
        "company_id": company,
        "source": source,
        "decision": decision,
        "reason": reason.strip(),
        "author": author,
        "created_at": (now or datetime.now(tz=UTC)).isoformat(),
        "evidence": evidence,
    }
    existing = {f.id for f in _feedback(path, kind)}
    if record_id in existing:
        return record_id, False
    directory.mkdir(parents=True, exist_ok=True)
    trial = directory / f".{path.name}.check"
    trial.write_text(json.dumps(record) + "\n")
    try:
        _feedback(trial, kind)
    finally:
        trial.unlink()
    with path.open("a") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record_id, True


@dataclass
class Morning:
    shortlist: list[dict[str, Any]] = field(default_factory=list)
    held: list[dict[str, Any]] = field(default_factory=list)
    suppressed: list[dict[str, Any]] = field(default_factory=list)
    not_strong: int = 0
    suggestions: list[dict[str, Any]] = field(default_factory=list)


def _latest_owner(feedback: Iterable[Feedback]) -> dict[str, Feedback]:
    latest: dict[str, Feedback] = {}
    for f in sorted(feedback, key=lambda f: f.created_at):
        if f.author_kind != OWNER or f.decision == "note":
            continue
        target = f.role_id if f.decision in {"skip_role", "good_fit"} else f.company_id
        if target:
            latest[target] = f
    return latest


def strong_fit(role: dict[str, Any]) -> bool:
    return bool(
        role.get("official_url")
        and (role.get("pay") or {}).get("max")
        and role.get("traceable")
    )


def _brief(role: dict[str, Any]) -> dict[str, Any]:
    return {
        "role_id": role["role_id"],
        "company_id": role["company_id"],
        "company_name": role["company_name"],
        "title": role["title"],
    }


def shortlist(
    export: dict[str, Any],
    tracker: list[TrackerRow],
    feedback: list[Feedback],
    *,
    size: int = SHORTLIST_SIZE,
    max_active: int = MAX_ACTIVE_PER_COMPANY,
) -> Morning:
    decided = build_decided_index(
        [
            (
                url_key(row.url),
                normalize_company(row.company),
                normalize_title(row.title),
            )
            for row in tracker
        ],
        [
            (
                r["company_name"],
                r["title"],
                posting_keys(r.get("official_url"))
                | posting_keys(r.get("listing_url")),
            )
            for r in export.get("roles", [])
        ],
    )
    active_count: dict[str, int] = {}
    for row in tracker:
        if row.status in ACTIVE_APPLICATION_STATUSES:
            cid = company_id(row.company)
            active_count[cid] = active_count.get(cid, 0) + 1
    for cid in export.get("active_application_companies", []):
        active_count[cid] = max(active_count.get(cid, 0), 1)
    owner = _latest_owner(feedback)
    by_role: dict[str | None, list[Feedback]] = {}
    for f in feedback:
        if f.author_kind == AGENT:
            by_role.setdefault(f.role_id or f.company_id, []).append(f)
    morning = Morning()
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for role in export.get("roles", []):
        cid = role["company_id"]
        keys = posting_keys(role.get("official_url")) | posting_keys(
            role.get("listing_url")
        )
        if decided.covers(role["company_name"], role["title"], keys):
            morning.suppressed.append(
                {**_brief(role), "reason": "already in the application tracker"}
            )
            continue
        role_fb = owner.get(role["role_id"])
        company_fb = owner.get(cid)
        if role_fb and role_fb.decision == "skip_role":
            morning.suppressed.append(
                {
                    **_brief(role),
                    "reason": f"owner skipped this role: {role_fb.reason}",
                    "feedback_id": role_fb.id,
                }
            )
            continue
        if company_fb and company_fb.decision == "skip_company":
            morning.suppressed.append(
                {
                    **_brief(role),
                    "reason": f"owner skipped this company: {company_fb.reason}",
                    "feedback_id": company_fb.id,
                }
            )
            continue
        if active_count.get(cid, 0) >= max_active:
            morning.held.append(
                {
                    **_brief(role),
                    "reason": f"active application at this company (limit {max_active})",
                }
            )
            continue
        suggestions = [
            {"id": f.id, "author": f.author, "decision": f.decision, "reason": f.reason}
            for f in by_role.get(role["role_id"], []) + by_role.get(cid, [])
        ]
        if suggestions:
            morning.suggestions.append({**_brief(role), "suggestions": suggestions})
        if not strong_fit(role):
            morning.not_strong += 1
            continue
        boost = 0 if role_fb and role_fb.decision == "good_fit" else 1
        candidates.append(
            (boost, role["rank"], {**role, "agent_suggestions": suggestions})
        )
    morning.shortlist = [
        r for _, _, r in sorted(candidates, key=lambda c: (c[0], c[1]))
    ][:size]
    return morning


def morning_document(
    morning: Morning, export: dict[str, Any], *, source: dict[str, Any], now: datetime
) -> dict[str, Any]:
    return {
        "schema": MORNING_SCHEMA,
        "generated_at": now.astimezone(UTC).isoformat(),
        "export": {"generated_at": export.get("generated_at"), **source},
        "rules": {
            "shortlist_size": SHORTLIST_SIZE,
            "max_active_per_company": MAX_ACTIVE_PER_COMPANY,
            "strong_fit": STRONG_FIT_RULE,
            "agent_suggestions": "shown, never applied",
        },
        "data_freshness": export.get("data_freshness", []),
        "shortlist": [
            {k: v for k, v in r.items() if k != "posting_text"}
            for r in morning.shortlist
        ],
        "best_match": morning.shortlist[0]["role_id"] if morning.shortlist else None,
        "held": morning.held,
        "suppressed": morning.suppressed,
        "not_strong": morning.not_strong,
        "agent_suggestions": morning.suggestions,
    }


def format_morning(document: dict[str, Any]) -> str:
    lines = [f"Morning job search, {document['generated_at']}"]
    stale = [
        f["source"]
        for f in document.get("data_freshness", [])
        if f.get("state") != "ok"
    ]
    if stale:
        lines.append(f"stale sources: {', '.join(stale)}")
    if not document["shortlist"]:
        lines.append("No strong fits today.")
    for i, r in enumerate(document["shortlist"], start=1):
        pay = r["pay"]
        lines.append(
            f"{i}. {r['company_name']}: {r['title']} (${(pay['min'] or pay['max']) // 1000}k-${pay['max'] // 1000}k)"
        )
        lines.append(f"   {r['official_url']}  role {r['role_id']}")
        if r["gaps"]:
            lines.append(f"   gaps: {'; '.join(r['gaps'])}")
        for s in r.get("agent_suggestions", []):
            lines.append(
                f"   agent suggestion (not applied): {s['author']} {s['decision']}: {s['reason']}"
            )
    lines.append(
        f"held for active applications: {len(document['held'])}; suppressed: "
        f"{len(document['suppressed'])}; not strong enough: {document['not_strong']}"
    )
    for h in document["held"]:
        lines.append(f"  held: {h['company_name']}: {h['title']} ({h['reason']})")
    for s in document["suppressed"]:
        lines.append(f"  suppressed: {s['company_name']}: {s['title']} ({s['reason']})")
    return "\n".join(lines)


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    evidence: str
    verification: str
    confidential: bool
    employer: str | None
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Answer:
    id: str
    kind: str
    text: str
    source: str
    question: str | None


def load_claims(path: Path) -> list[Claim]:
    claims: list[Claim] = []
    for number, r in _read_jsonl(path, CLAIM_SCHEMA):
        if r.get("verification") not in {"verified", "unverified"}:
            raise RecordError(
                f"{path.name}:{number}: verification must be verified or unverified"
            )
        if not r.get("id") or not r.get("text") or "evidence" not in r:
            raise RecordError(
                f"{path.name}:{number}: id, text and evidence are required"
            )
        claims.append(
            Claim(
                id=r["id"],
                text=r["text"],
                evidence=r["evidence"],
                verification=r["verification"],
                confidential=bool(r.get("confidential", False)),
                employer=r.get("employer"),
                tags=tuple(r.get("tags", [])),
            )
        )
    return claims


def load_answers(path: Path) -> list[Answer]:
    answers: list[Answer] = []
    for number, r in _read_jsonl(path, ANSWER_SCHEMA):
        if r.get("kind") not in ANSWER_KINDS:
            raise RecordError(
                f"{path.name}:{number}: kind must be one of {sorted(ANSWER_KINDS)}"
            )
        if not r.get("id") or not r.get("text") or not r.get("source"):
            raise RecordError(f"{path.name}:{number}: id, text and source are required")
        answers.append(
            Answer(r["id"], r["kind"], r["text"], r["source"], r.get("question"))
        )
    return answers


def draftable(claim: Claim) -> bool:
    return claim.verification == "verified" and not claim.confidential


_WORD = re.compile(r"[a-z][a-z0-9+#.]{2,}")
_COVER_REQUIRED = re.compile(
    r"cover letter[^.\n]{0,60}\b(required|must|mandatory)\b|\b(required|must include|please include|submit)\b[^.\n]{0,60}cover letter",
    re.I,
)
_COVER_MENTION = re.compile(r"cover letter", re.I)


def cover_letter_rule(text: str | None) -> str:
    if text and _COVER_REQUIRED.search(text):
        return "required by the posting: write one, specific to this role"
    if text and _COVER_MENTION.search(text):
        return "optional: write one only if it adds something specific"
    return "not requested: skip unless it adds something specific"


def rank_claims(claims: list[Claim], text: str | None) -> list[Claim]:
    words = set(_WORD.findall((text or "").lower()))

    def score(claim: Claim) -> int:
        own = set(_WORD.findall(claim.text.lower())) | {t.lower() for t in claim.tags}
        return len(own & words)

    return sorted(claims, key=lambda c: (-score(c), c.id))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def build_packet(
    role: dict[str, Any],
    posting_text: str | None,
    claims: list[Claim],
    answers: list[Answer],
    packets_dir: Path,
    *,
    now: datetime,
) -> Path:
    tag = hashlib.sha256(role["role_id"].encode()).hexdigest()[:8]
    folder = (
        packets_dir / f"{now.date().isoformat()}-{_slug(role['company_name'])}-{tag}"
    )
    packet = folder / "packet.md"
    if packet.exists():
        return packet
    usable = rank_claims([c for c in claims if draftable(c)], posting_text)
    held = [c for c in claims if not draftable(c)]
    lines = [
        f"# UNSENT application packet: {role['company_name']}, {role['title']}",
        "",
        "Status: unsent. Do not submit, email or contact anyone without Rich's explicit go-ahead.",
        "",
        f"- Role ID: {role['role_id']}",
        f"- Company ID: {role['company_id']}",
        f"- Official posting: {role['official_url']}",
        f"- Location: {role.get('location')}",
        f"- Pay: ${(role['pay']['min'] or role['pay']['max']) // 1000}k-${role['pay']['max'] // 1000}k",
        f"- Cover letter: {cover_letter_rule(posting_text)}",
        "",
        "## Why it fits",
        *[f"- {r}" for r in role["fit_reasons"]],
        "",
        "## Gaps to check",
        *([f"- {g}" for g in role["gaps"]] or ["- none found by the filters"]),
        "",
        "## Drafting rules",
        "- Lead with what Rich built and owned. Claude Code and Codex go in Skills only.",
        "- Use only the claims below. No unverified numbers, no confidential Sturdy details.",
        "- Voice: reuse owner writing and approved wording; sent answers are reference, not approved wording.",
        "- Start from resume-draft.md; change only what this role needs.",
        "",
        "## Claims usable in drafts (most relevant first)",
        *[f"- [{c.id}] {c.text}" for c in usable],
        "",
        "## Claims held back",
        *[
            f"- [{c.id}] {'unverified' if c.verification != 'verified' else 'confidential'}"
            for c in held
        ],
        "",
        "## Voice and approved wording",
        *[
            f"- [{a.id}] ({a.kind}) {a.text[:300]}"
            for a in answers
            if a.kind in DRAFT_ANSWER_KINDS
        ],
        "",
        "## Sent answers (reference only, not approved word for word)",
        *[f"- [{a.id}] {a.source}" for a in answers if a.kind == "sent"],
        "",
        "## Posting text",
        "",
        posting_text or "(not included in the export; open the official posting)",
        "",
    ]
    folder.mkdir(parents=True, exist_ok=True)
    packet.write_text("\n".join(lines))
    return packet


_RECEIPT_SENDER = re.compile(
    r"no-?reply|notifications?@|mailer-daemon|do-not-reply", re.I
)
_RECEIPT_SUBJECT = re.compile(
    r"thank(s| you) for (applying|your application)|we(’|')?ve received|received your application"
    r"|application (confirmation|received)|we got your application",
    re.I,
)


def mail_status(log_text: str, companies: list[str]) -> list[dict[str, Any]]:
    keys = [
        (name, normalize_company(name)) for name in companies if normalize_company(name)
    ]
    items: list[dict[str, Any]] = []
    for line in log_text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not cells[2].isdigit():
            continue
        found, folder, uid, _date, sender, subject = cells
        blob = normalize_company(f"{sender} {subject}")
        company = next((name for name, key in keys if key and key in blob), None)
        if _RECEIPT_SUBJECT.search(subject):
            kind, status = "automated_receipt", "application received"
        elif _RECEIPT_SENDER.search(sender):
            kind, status = "automated_notice", "notice"
        else:
            kind, status = "possible_human_reply", "reply: read it in the mailbox"
        items.append(
            {
                "date": found,
                "source_ref": f"{folder}:{uid}",
                "company": company,
                "status": status,
                "kind": kind,
            }
        )
    return items
