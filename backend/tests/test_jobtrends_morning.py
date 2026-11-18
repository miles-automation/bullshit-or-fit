import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.jobtrends.decisions import parse_tracker
from app.jobtrends.fit import FitList, FitRole
from app.jobtrends.fit_export import EXPORT_SCHEMA, build_export
from app.jobtrends.fit_feed import FeedError, latest, publish
from app.jobtrends.morning import (
    AGENT,
    OWNER,
    RecordError,
    append_feedback,
    build_packet,
    cover_letter_rule,
    load_answers,
    load_claims,
    load_feedback,
    mail_status,
    morning_document,
    shortlist,
)

NOW = datetime(2026, 11, 18, 14, tzinfo=UTC)


def _role(**kw: Any) -> FitRole:  # noqa: ANN401
    base: dict[str, Any] = dict(
        source="ats",
        company_name="Acme",
        title="Senior Software Engineer",
        location="Remote - US",
        url="https://job-boards.greenhouse.io/acme/jobs/1",
        comp_min=180_000,
        comp_max=220_000,
        team_size=40,
        first_seen=NOW,
        posted_at=NOW,
        role_id="ats:greenhouse:acme:1",
        official_url="https://job-boards.greenhouse.io/acme/jobs/1",
        posting_text="We use Python and Postgres.",
    )
    base.update(kw)
    return FitRole(**base)


def _export(*roles: FitRole, active: frozenset[str] = frozenset()) -> dict[str, Any]:
    return build_export(
        FitList(roles=list(roles), hidden=1, active_companies=active),
        days=1,
        max_age_days=30,
        freshness=[
            {"source": "ats", "state": "ok", "last_seen_at": None, "checked_at": None}
        ],
        now=NOW,
    )


def test_export_is_versioned_and_keeps_rank_order() -> None:
    doc = _export(
        _role(),
        _role(
            source="himalayas",
            company_name="Beta",
            role_id="himalayas:beta:x",
            url="https://himalayas.app/companies/beta/jobs/x",
            official_url=None,
            traceable=False,
            comp_max=None,
            comp_min=None,
            team_size=None,
            posted_at=None,
        ),
        active=frozenset({"co:beta"}),
    )
    assert doc["schema"] == EXPORT_SCHEMA
    assert doc["generated_at"] == NOW.isoformat()
    assert doc["hidden_tracker_roles"] == 1
    first, second = doc["roles"]
    assert (first["rank"], first["role_id"], first["company_id"]) == (
        1,
        "ats:greenhouse:acme:1",
        "co:acme",
    )
    assert "small company (40 people)" in first["fit_reasons"]
    assert first["gaps"] == []
    assert second["official_url"] is None
    assert second["active_application_at_company"] is True
    assert set(second["gaps"]) >= {
        "pay not listed",
        "not found on the company's own site",
        "official posting URL unknown",
        "active application at this company",
    }
    json.dumps(doc)


def test_export_only_carries_posting_text_for_the_top_roles() -> None:
    roles = [_role(role_id=f"r{i}", company_name=f"C{i}") for i in range(12)]
    doc = _export(*roles)
    assert doc["roles"][9]["posting_text"]
    assert doc["roles"][10]["posting_text"] is None


def _transport(store: list[dict[str, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "k"
        if request.method == "POST":
            body = json.loads(request.content)
            store.append(
                {
                    **body,
                    "id": len(store) + 1,
                    "created_at": f"2026-11-18T0{len(store)}:00:00Z",
                }
            )
            return httpx.Response(201, json=store[-1])
        return httpx.Response(200, json={"comments": store, "total": len(store)})

    return httpx.MockTransport(handler)


def test_feed_round_trip_returns_the_newest_matching_document() -> None:
    store: list[dict[str, Any]] = []
    client = httpx.Client(
        base_url="https://ss.test",
        headers={"X-API-Key": "k"},
        transport=_transport(store),
    )
    store.append(
        {
            "id": 99,
            "body": "probe",
            "comment_metadata": {"schema": "probe"},
            "created_at": "2026-11-19T00:00:00Z",
        }
    )
    publish(
        {**_export(_role()), "generated_at": "old"},
        5,
        author="claude-code",
        client=client,
    )
    publish(_export(_role()), 5, author="codex-default", client=client)
    found = latest(5, EXPORT_SCHEMA, client=client)
    assert found["posted_by"] == "codex-default"
    assert found["document"]["generated_at"] == NOW.isoformat()
    assert store[1]["author_type"] == "agent"
    with pytest.raises(FeedError, match="no bof.morning/v1"):
        latest(5, "bof.morning/v1", client=client)


def test_feed_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_SWARM_API_KEY", raising=False)
    with pytest.raises(FeedError, match="not set"):
        latest(5, EXPORT_SCHEMA)


def _fb(home: Path, **kw: Any) -> tuple[str, bool]:  # noqa: ANN401
    base: dict[str, Any] = dict(
        kind=OWNER,
        company="co:acme",
        decision="skip_role",
        reason="not engineering",
        author="claude-code",
        source="ats",
        role_id="ats:greenhouse:acme:1",
        evidence='Rich, 2026-11-18 chat: "skip that one"',
        now=NOW,
    )
    base.update(kw)
    return append_feedback(home, **base)


def test_feedback_append_is_idempotent_and_never_rewrites(tmp_path: Path) -> None:
    first = _fb(tmp_path)
    again = _fb(tmp_path)
    assert first[1] is True and again == (first[0], False)
    owner_file = tmp_path / "owner-feedback.jsonl"
    edited = owner_file.read_text().replace(
        "not engineering", "not engineering, hand edit"
    )
    owner_file.write_text(edited)
    _fb(tmp_path, reason="second reason")
    assert "hand edit" in owner_file.read_text()
    assert len(owner_file.read_text().splitlines()) == 2


def test_agent_cannot_write_owner_feedback_without_the_owners_words(
    tmp_path: Path,
) -> None:
    for evidence in (None, "   ", "Rich said so", '"ok"'):
        with pytest.raises(RecordError, match="owner's quoted words"):
            _fb(tmp_path, evidence=evidence)
    with pytest.raises(RecordError, match="reserved"):
        _fb(tmp_path, author=" Rich ")
    assert not (tmp_path / "owner-feedback.jsonl").exists()
    _fb(tmp_path)


def test_owner_kind_claimed_in_the_agent_file_stays_a_suggestion(
    tmp_path: Path,
) -> None:
    (tmp_path / "agent-suggestions.jsonl").write_text(
        json.dumps(
            {
                "schema": "bof.fit-feedback/v1",
                "author_kind": "owner",
                "company_id": "co:acme",
                "role_id": "ats:greenhouse:acme:1",
                "decision": "skip_role",
                "reason": "looks weak",
                "author": "rich",
                "source": "ats",
                "created_at": NOW.isoformat(),
            }
        )
        + "\n"
    )
    [record] = load_feedback(tmp_path)
    assert record.author_kind == AGENT


def test_bad_feedback_lines_fail_loudly(tmp_path: Path) -> None:
    (tmp_path / "owner-feedback.jsonl").write_text(
        '{"schema": "bof.fit-feedback/v1"}\n'
    )
    with pytest.raises(RecordError, match="owner-feedback.jsonl:1"):
        load_feedback(tmp_path)


TRACKER = """| Company | Role | Link | Status |
|---|---|---|---|
| Applied Co | Senior SWE | [x](https://job-boards.greenhouse.io/applied/jobs/7) | submitted |
| Skipped Co | Engineer | none | skipped |
| Old Co | Engineer | none | rejected |
"""


def _roles() -> list[FitRole]:
    return [
        _role(),
        _role(
            company_name="Applied Co",
            role_id="ats:applied:7",
            url="https://job-boards.greenhouse.io/applied/jobs/7",
            official_url="https://job-boards.greenhouse.io/applied/jobs/7",
        ),
        _role(
            company_name="Applied Co", title="Staff Engineer", role_id="ats:applied:8"
        ),
        _role(
            company_name="Skipped Co", title="Staff Engineer", role_id="ats:skipped:2"
        ),
        _role(company_name="Old Co", title="Staff Engineer", role_id="ats:old:2"),
        _role(company_name="Weak", role_id="w", comp_max=None, comp_min=None),
    ]


def test_shortlist_suppresses_applied_and_holds_active_companies() -> None:
    morning = shortlist(_export(*_roles()), parse_tracker(TRACKER), [])
    assert [r["company_name"] for r in morning.shortlist] == [
        "Acme",
        "Skipped Co",
        "Old Co",
    ]
    assert [(s["role_id"], s["reason"]) for s in morning.suppressed] == [
        ("ats:applied:7", "already in the application tracker")
    ]
    assert [h["role_id"] for h in morning.held] == ["ats:applied:8"]
    assert morning.not_strong == 1


def test_owner_role_skip_is_not_a_company_ban_and_agent_guesses_are_not_applied(
    tmp_path: Path,
) -> None:
    _fb(tmp_path)
    _fb(
        tmp_path,
        kind=AGENT,
        author="codex-default",
        company="co:skipped",
        role_id="ats:skipped:2",
        reason="probably too senior",
    )
    other_acme = _role(title="Staff Software Engineer", role_id="ats:greenhouse:acme:2")
    roles = [*_roles(), other_acme]
    morning = shortlist(
        _export(*roles), parse_tracker(TRACKER), load_feedback(tmp_path)
    )
    ids = [r["role_id"] for r in morning.shortlist]
    assert "ats:greenhouse:acme:1" not in ids
    assert "ats:greenhouse:acme:2" in ids
    assert "ats:skipped:2" in ids
    skipped = next(r for r in morning.shortlist if r["role_id"] == "ats:skipped:2")
    assert skipped["agent_suggestions"][0]["author"] == "codex-default"
    assert any("owner skipped this role" in s["reason"] for s in morning.suppressed)


def test_owner_company_skip_and_good_fit(tmp_path: Path) -> None:
    _fb(tmp_path, decision="skip_company", role_id=None, company="co:old", reason="no")
    _fb(
        tmp_path,
        decision="good_fit",
        company="co:skipped",
        role_id="ats:skipped:2",
        reason="yes",
    )
    morning = shortlist(
        _export(*_roles()), parse_tracker(TRACKER), load_feedback(tmp_path)
    )
    assert [r["role_id"] for r in morning.shortlist] == [
        "ats:skipped:2",
        "ats:greenhouse:acme:1",
    ]


def test_morning_document_can_be_empty() -> None:
    morning = shortlist(_export(_role(comp_max=None, comp_min=None)), [], [])
    doc = morning_document(morning, _export(), source={"file": "x"}, now=NOW)
    assert doc["shortlist"] == [] and doc["best_match"] is None


CLAIMS = [
    {
        "schema": "bof.claim/v1",
        "id": "c-py",
        "text": "Built Python and Postgres services.",
        "evidence": "baseline",
        "verification": "verified",
    },
    {
        "schema": "bof.claim/v1",
        "id": "c-num",
        "text": "Cut cost 50%.",
        "evidence": "none",
        "verification": "unverified",
    },
    {
        "schema": "bof.claim/v1",
        "id": "c-secret",
        "text": "Customer spend by account.",
        "evidence": "x",
        "verification": "verified",
        "confidential": True,
    },
]
ANSWERS = [
    {
        "schema": "bof.answer/v1",
        "id": "a-own",
        "kind": "owner_writing",
        "text": "In my words.",
        "source": "chat",
    },
    {
        "schema": "bof.answer/v1",
        "id": "a-sent",
        "kind": "sent",
        "text": "Sent text.",
        "source": "sent/Close-answers.txt",
    },
]


def _write(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_packet_uses_only_draftable_claims_and_never_overwrites(tmp_path: Path) -> None:
    claims = load_claims(_write(tmp_path / "claims.jsonl", CLAIMS))
    answers = load_answers(_write(tmp_path / "answers.jsonl", ANSWERS))
    role = _export(_role())["roles"][0]
    packet = build_packet(
        role, role["posting_text"], claims, answers, tmp_path / "packets", now=NOW
    )
    text = packet.read_text()
    assert text.startswith("# UNSENT application packet: Acme")
    usable = text.split("## Claims usable in drafts")[1].split("## Claims held back")[0]
    assert "[c-py]" in usable and "c-num" not in usable and "c-secret" not in usable
    assert "[c-num] unverified" in text and "[c-secret] confidential" in text
    assert "(owner_writing) In my words." in text
    assert "[a-sent] sent/Close-answers.txt" in text
    assert "Cover letter: not requested" in text
    packet.write_text("my edits")
    again = build_packet(
        role, role["posting_text"], claims, answers, tmp_path / "packets", now=NOW
    )
    assert again == packet and packet.read_text() == "my edits"


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("A cover letter is required.", "required"),
        ("Please include a cover letter with your resume", "required"),
        ("Cover letter optional.", "optional"),
        ("Send your resume.", "not requested"),
        (None, "not requested"),
    ],
)
def test_cover_letter_rule(text: str | None, rule: str) -> None:
    assert cover_letter_rule(text).startswith(rule)


def test_claim_and_answer_validation(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match="verification"):
        load_claims(
            _write(tmp_path / "c.jsonl", [{**CLAIMS[0], "verification": "maybe"}])
        )
    with pytest.raises(RecordError, match="kind"):
        load_answers(_write(tmp_path / "a.jsonl", [{**ANSWERS[0], "kind": "draft"}]))


LOG = """| Found | Folder | UID | Date | From | Subject |
|---|---|---|---|---|---|
| 2026-11-06 21:26Z | INBOX | 2193 | Fri | no-reply@us.greenhouse-mail.io | We've received your application for Senior Engineer at Affirm |
| 2026-11-06 23:21Z | INBOX | 2194 | Fri | Paramark Team <recruiting@paramark.com> | we got your application, Rich |
| 2026-11-07 10:00Z | INBOX | 2200 | Sat | Jane Doe <jane@close.com> | Next steps with Close |
| 2026-11-07 11:00Z | INBOX | 2201 | Sat | Google Voice <voice-noreply@google.com> | New voicemail |
"""


def test_mail_status_keeps_only_status_fields() -> None:
    items = mail_status(LOG, ["Affirm", "Paramark", "Close"])
    assert [(i["source_ref"], i["company"], i["kind"]) for i in items] == [
        ("INBOX:2193", "Affirm", "automated_receipt"),
        ("INBOX:2194", "Paramark", "automated_receipt"),
        ("INBOX:2200", "Close", "possible_human_reply"),
        ("INBOX:2201", None, "automated_notice"),
    ]
    assert all(
        set(i) == {"date", "source_ref", "company", "status", "kind"} for i in items
    )


def test_shortlist_takes_one_role_per_company() -> None:
    roles = [
        _role(),
        _role(title="Staff Software Engineer", role_id="ats:greenhouse:acme:2"),
        _role(company_name="Beta", role_id="b1"),
    ]
    morning = shortlist(_export(*roles), [], [])
    assert [r["role_id"] for r in morning.shortlist] == ["ats:greenhouse:acme:1", "b1"]
    assert (
        morning.held[0]["reason"] == "another role at this company is on the shortlist"
    )


def test_newer_owner_feedback_supersedes_an_agent_suggestion(tmp_path: Path) -> None:
    _fb(
        tmp_path,
        kind=AGENT,
        author="claude-code",
        decision="note",
        role_id=None,
        reason="crypto company, probably skip",
    )
    morning = shortlist(_export(_role()), [], load_feedback(tmp_path))
    [role] = morning.shortlist
    assert role["agent_suggestions"][0]["reason"] == "crypto company, probably skip"
    later = datetime(2026, 11, 18, 15, tzinfo=UTC)
    _fb(
        tmp_path,
        author="claude-code",
        decision="note",
        role_id=None,
        reason="fine if it's a good fit",
        evidence='Rich: "Coinbase is fine if it\'s a good fit."',
        now=later,
    )
    morning = shortlist(_export(_role()), [], load_feedback(tmp_path))
    [role] = morning.shortlist
    assert role["agent_suggestions"] == []
    assert (
        role["superseded_suggestions"][0]["reason"] == "crypto company, probably skip"
    )
    assert role["owner_notes"][0]["reason"] == "fine if it's a good fit"


def test_rich_can_hand_write_owner_records_without_evidence(tmp_path: Path) -> None:
    (tmp_path / "owner-feedback.jsonl").write_text(
        json.dumps(
            {
                "schema": "bof.fit-feedback/v1",
                "company_id": "co:acme",
                "role_id": "r1",
                "decision": "skip_role",
                "reason": "no",
                "author": "rich",
                "source": "ats",
                "created_at": NOW.isoformat(),
            }
        )
    )
    [record] = load_feedback(tmp_path)
    assert record.author_kind == OWNER
    _fb(tmp_path, reason="another")
    assert len(load_feedback(tmp_path)) == 2


def test_owner_role_skip_follows_the_posting_across_sources(tmp_path: Path) -> None:
    url = "https://job-boards.greenhouse.io/acme/jobs/1"
    _fb(tmp_path, role_id="himalayas:acme:x", url=url)
    morning = shortlist(_export(_role()), [], load_feedback(tmp_path))
    assert morning.shortlist == []
    assert "owner skipped this role" in morning.suppressed[0]["reason"]


def test_tracker_not_a_stale_export_decides_active_companies() -> None:
    tracker = parse_tracker(
        "| Company | Role | Link | Status |\n|---|---|---|---|\n"
        "| Acme | Old role | none | rejected |\n"
    )
    morning = shortlist(_export(_role(), active=frozenset({"co:acme"})), tracker, [])
    assert [r["role_id"] for r in morning.shortlist] == ["ats:greenhouse:acme:1"]
    assert morning.held == []


def test_suggestions_on_suppressed_roles_are_still_reported(tmp_path: Path) -> None:
    _fb(
        tmp_path,
        kind=AGENT,
        decision="note",
        reason="weak",
        evidence=None,
    )
    _fb(tmp_path, now=datetime(2026, 11, 18, 15, tzinfo=UTC))
    morning = shortlist(_export(_role()), [], load_feedback(tmp_path))
    [entry] = morning.suppressed
    assert entry["superseded_suggestions"][0]["reason"] == "weak"


def test_published_morning_document_carries_no_owner_evidence(tmp_path: Path) -> None:
    _fb(tmp_path, decision="note", role_id=None, reason="fine if good")
    morning = shortlist(_export(_role()), [], load_feedback(tmp_path))
    doc = morning_document(morning, _export(_role()), source={}, now=NOW)
    text = json.dumps(doc)
    assert "fine if good" in text
    assert "skip that one" not in text


def test_append_repairs_a_missing_final_newline(tmp_path: Path) -> None:
    _fb(tmp_path)
    path = tmp_path / "owner-feedback.jsonl"
    path.write_text(path.read_text().rstrip("\n"))
    _fb(tmp_path, reason="second")
    assert len(load_feedback(tmp_path)) == 2


def test_answers_with_held_figures_or_confidential_flags_are_not_offered(
    tmp_path: Path,
) -> None:
    claims = load_claims(_write(tmp_path / "claims.jsonl", CLAIMS))
    answers = load_answers(
        _write(
            tmp_path / "answers.jsonl",
            [
                {**ANSWERS[0], "id": "a-leak", "text": "We cut cost 50% last year."},
                {**ANSWERS[0], "id": "a-conf", "confidential": True},
                ANSWERS[0],
            ],
        )
    )
    role = _export(_role())["roles"][0]
    packet = build_packet(role, None, claims, answers, tmp_path / "p", now=NOW)
    voice = packet.read_text().split("## Voice and approved wording")[1]
    voice = voice.split("## Sent")[0]
    assert "[a-own]" in voice
    assert "a-leak" not in voice and "a-conf" not in voice


def test_latest_pages_through_every_comment() -> None:
    comments = [
        {
            "id": i,
            "body": "x",
            "created_at": "2026-11-19T00:00:00Z"
            if i == 600
            else "2026-11-18T00:00:00Z",
            "comment_metadata": {"schema": EXPORT_SCHEMA, "n": i},
        }
        for i in range(1, 601)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        after = int(request.url.params.get("after_comment_id", 0))
        limit = int(request.url.params["limit"])
        assert request.url.params["kinds"] == EXPORT_SCHEMA
        page = [c for c in comments if c["id"] > after][:limit]
        return httpx.Response(200, json={"comments": page, "total": len(page)})

    client = httpx.Client(
        base_url="https://ss.test", transport=httpx.MockTransport(handler)
    )
    assert latest(5, EXPORT_SCHEMA, client=client)["document"]["n"] == 600


def test_latest_out_refuses_to_replace_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.jobtrends import morning_cli

    def fake_latest(*_args: Any, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"comment_id": 1, "posted_at": "t", "posted_by": "x", "document": {}}

    monkeypatch.setattr(morning_cli, "latest", fake_latest)
    target = tmp_path / "tracker.md"
    target.write_text("keep me")
    assert morning_cli.main(["latest", "--task", "1", "--out", str(target)]) == 1
    assert target.read_text() == "keep me"


def test_mail_status_reads_escaped_pipes() -> None:
    row = "| 2026-11-08 09:00Z | INBOX | 2300 | Sun | Sam <sam@acme.com> | Acme \\| Next steps |\n"
    items = mail_status(LOG + row, ["Acme"])
    assert items[-1]["source_ref"] == "INBOX:2300"
    assert items[-1]["kind"] == "possible_human_reply"


def test_unmatched_quote_is_not_owner_evidence(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match="quoted words"):
        _fb(tmp_path, evidence='I think Rich would say " skip this company')
    _fb(tmp_path, evidence="Rich, chat: “skip it”")


def test_publish_scrubs_evidence_from_any_document() -> None:
    store: list[dict[str, Any]] = []
    client = httpx.Client(
        base_url="https://ss.test",
        headers={"X-API-Key": "k"},
        transport=_transport(store),
    )
    old = {
        "schema": "bof.morning/v1",
        "shortlist": [
            {"owner_notes": [{"id": "x", "reason": "r", "evidence": "secret"}]}
        ],
    }
    publish(old, 5, author="claude-code", client=client)
    assert "secret" not in json.dumps(store)
    assert store[0]["comment_metadata"]["kind"] == "bof.morning/v1"


def test_feedback_dedupe_only_against_the_newest_record_for_the_target(
    tmp_path: Path,
) -> None:
    first, added = _fb(tmp_path)
    assert added
    assert _fb(tmp_path)[1] is False
    with_url = _fb(tmp_path, url="https://job-boards.greenhouse.io/acme/jobs/1")
    assert with_url[1] is True and with_url[0] != first
    _fb(
        tmp_path,
        decision="good_fit",
        reason="changed my mind",
        now=datetime(2026, 11, 18, 15, tzinfo=UTC),
    )
    again = _fb(tmp_path, now=datetime(2026, 11, 18, 16, tzinfo=UTC))
    assert again[1] is True
    morning = shortlist(_export(_role()), [], load_feedback(tmp_path))
    assert morning.shortlist == []


def test_generic_careers_url_skip_does_not_hide_other_roles(tmp_path: Path) -> None:
    careers = "https://acme.example/careers"
    _fb(tmp_path, role_id="r-a", url=careers)
    roles = [_role(role_id="r-b", official_url=careers, url=careers)]
    morning = shortlist(_export(*roles), [], load_feedback(tmp_path))
    assert [r["role_id"] for r in morning.shortlist] == ["r-b"]


def test_reformatted_held_figures_still_block_answers(tmp_path: Path) -> None:
    claims = load_claims(
        _write(
            tmp_path / "claims.jsonl",
            [{**CLAIMS[1], "text": "Saved $1.2M a year."}],
        )
    )
    answers = load_answers(
        _write(
            tmp_path / "answers.jsonl",
            [
                {**ANSWERS[0], "id": "a-big", "text": "That saved $1,200,000."},
                ANSWERS[0],
            ],
        )
    )
    role = _export(_role())["roles"][0]
    text = build_packet(
        role, None, claims, answers, tmp_path / "p", now=NOW
    ).read_text()
    voice = text.split("## Voice and approved wording")[1].split("## Sent")[0]
    assert "a-big" not in voice and "[a-own]" in voice


def test_latest_falls_back_to_documents_without_a_kind() -> None:
    comments = [
        {
            "id": 7,
            "body": "old",
            "created_at": "2026-11-18T00:00:00Z",
            "comment_metadata": {"schema": EXPORT_SCHEMA, "n": 7},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        wanted = request.url.params.get("kinds")
        page = [
            c
            for c in comments
            if not wanted or c["comment_metadata"].get("kind") == wanted
        ]
        return httpx.Response(200, json={"comments": page, "total": len(page)})

    client = httpx.Client(
        base_url="https://ss.test", transport=httpx.MockTransport(handler)
    )
    assert latest(5, EXPORT_SCHEMA, client=client)["document"]["n"] == 7


def test_text_output_shows_suggestions_on_held_roles(tmp_path: Path) -> None:
    from app.jobtrends.morning import format_morning

    _fb(tmp_path, kind=AGENT, decision="note", reason="maybe later", evidence=None)
    tracker = parse_tracker(
        "| Company | Role | Link | Status |\n|---|---|---|---|\n"
        "| Acme | Other | none | submitted |\n"
    )
    morning = shortlist(_export(_role()), tracker, load_feedback(tmp_path))
    text = format_morning(
        morning_document(morning, _export(_role()), source={}, now=NOW)
    )
    assert "    agent suggestion (not applied): claude-code note: maybe later" in text
