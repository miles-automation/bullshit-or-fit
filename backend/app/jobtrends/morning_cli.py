from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.jobtrends.decisions import TrackerError, parse_tracker
from app.jobtrends.fit_export import EXPORT_SCHEMA
from app.jobtrends.fit_feed import FeedError, latest, publish
from app.jobtrends.morning import (
    DECISIONS,
    MORNING_SCHEMA,
    RecordError,
    append_feedback,
    build_packet,
    format_morning,
    load_answers,
    load_claims,
    load_feedback,
    mail_status,
    morning_document,
    shortlist,
)


def _read_json(path: str) -> dict[str, Any]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    document = json.loads(text)
    if not isinstance(document, dict):
        raise RecordError("expected a JSON object")
    return document


def _cmd_publish(args: argparse.Namespace) -> int:
    document = _read_json(args.path)
    if document.get("schema") not in {EXPORT_SCHEMA, MORNING_SCHEMA}:
        raise RecordError(f"refusing to publish schema {document.get('schema')!r}")
    comment = publish(document, args.task, author=args.author)
    print(f"published {document['schema']} as comment {comment} on task {args.task}")  # noqa: T201
    return 0


def _cmd_latest(args: argparse.Namespace) -> int:
    found = latest(args.task, args.schema)
    payload = json.dumps(found, indent=2)
    if args.out:
        target = Path(args.out)
        try:
            with target.open("w" if args.force else "x") as handle:
                handle.write(payload)
        except FileExistsError as exc:
            raise RecordError(f"{target} exists; pass --force to replace it") from exc
        print(  # noqa: T201
            f"wrote comment {found['comment_id']} ({found['posted_at']}, by {found['posted_by']}) to {args.out}"
        )
    else:
        print(payload)  # noqa: T201
    return 0


def _export(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.export:
        document = _read_json(args.export)
        if "document" in document and "comment_id" in document:
            return document["document"], {"comment_id": document["comment_id"]}
        return document, {"file": args.export}
    found = latest(args.feed_task, EXPORT_SCHEMA)
    return found["document"], {
        "task": args.feed_task,
        "comment_id": found["comment_id"],
    }


def _write_new(folder: Path, stem: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(1000):
        path = folder / (
            f"{stem}-morning.json" if n == 0 else f"{stem}-{n}-morning.json"
        )
        try:
            with path.open("x") as handle:
                handle.write(text)
            return path
        except FileExistsError:
            continue
    raise RecordError(f"could not create a run record in {folder}")


def _cmd_morning(args: argparse.Namespace) -> int:
    home = Path(args.home)
    export, source = _export(args)
    if export.get("schema") != EXPORT_SCHEMA:
        raise RecordError(f"expected {EXPORT_SCHEMA}")
    tracker = parse_tracker((home / "tracker.md").read_text())
    feedback = load_feedback(home / "feedback")
    now = datetime.now(tz=UTC)
    result = shortlist(export, tracker, feedback)
    document = morning_document(result, export, source=source, now=now)
    if result.shortlist and not args.no_packet:
        best = result.shortlist[0]
        packet = build_packet(
            best,
            best.get("posting_text"),
            load_claims(home / "corpus" / "claims.jsonl"),
            load_answers(home / "corpus" / "answers.jsonl"),
            home / "packets",
            now=now,
        )
        document["packet"] = str(packet.relative_to(home))
    out = _write_new(
        home / "runs",
        now.strftime("%Y-%m-%dT%H%M%SZ"),
        json.dumps(document, indent=2),
    )
    print(format_morning(document))  # noqa: T201
    if document.get("packet"):
        print(f"packet for the best match (unsent): {document['packet']}")  # noqa: T201
    print(f"run record: {out.relative_to(home)}")  # noqa: T201
    if args.publish_task:
        comment = publish(document, args.publish_task, author=args.author)
        print(
            f"published morning result as comment {comment} on task {args.publish_task}"
        )  # noqa: T201
    return 0


def _cmd_feedback(args: argparse.Namespace) -> int:
    record_id, added = append_feedback(
        Path(args.home) / "feedback",
        kind=args.kind,
        company=args.company_id,
        role_id=args.role_id,
        url=args.url,
        decision=args.decision,
        reason=args.reason,
        author=args.author,
        source=args.source,
        evidence=args.evidence,
    )
    print(
        f"{'recorded' if added else 'already recorded'} {args.kind} feedback {record_id}"
    )  # noqa: T201
    return 0


def _cmd_mail_status(args: argparse.Namespace) -> int:
    home = Path(args.home)
    companies = [
        row.company for row in parse_tracker((home / "tracker.md").read_text())
    ]
    log = home / "mailwatch" / "job-mail.md"
    print(json.dumps(mail_status(log.read_text(), companies), indent=2))  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobtrends-morning")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pub = sub.add_parser(
        "publish", help="post an export or morning result to the feed task"
    )
    p_pub.add_argument("path", help="JSON file, or - for stdin")
    p_pub.add_argument("--task", type=int, required=True)
    p_pub.add_argument("--author", required=True)

    p_latest = sub.add_parser(
        "latest", help="read the newest document from the feed task"
    )
    p_latest.add_argument("--task", type=int, required=True)
    p_latest.add_argument("--schema", default=EXPORT_SCHEMA)
    p_latest.add_argument("--out")
    p_latest.add_argument("--force", action="store_true")

    p_run = sub.add_parser(
        "run", help="shortlist, hold and suppress, build one unsent packet"
    )
    p_run.add_argument(
        "--home", required=True, help="job-search folder with tracker.md"
    )
    src = p_run.add_mutually_exclusive_group(required=True)
    src.add_argument("--export", help="export JSON file (or output of latest)")
    src.add_argument("--feed-task", type=int)
    p_run.add_argument("--no-packet", action="store_true")
    p_run.add_argument("--publish-task", type=int)
    p_run.add_argument("--author", default="unknown-agent")

    p_fb = sub.add_parser("feedback", help="append one fit decision or suggestion")
    p_fb.add_argument("--home", required=True)
    p_fb.add_argument("--kind", choices=["owner", "agent"], required=True)
    p_fb.add_argument("--company-id", required=True)
    p_fb.add_argument("--role-id")
    p_fb.add_argument("--url", help="official or listing URL of the role")
    p_fb.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    p_fb.add_argument("--reason", required=True)
    p_fb.add_argument("--author", required=True)
    p_fb.add_argument("--source", required=True)
    p_fb.add_argument("--evidence")

    p_mail = sub.add_parser(
        "mail-status", help="status-only view of the mail watcher log"
    )
    p_mail.add_argument("--home", required=True)

    args = parser.parse_args(argv)
    commands = {
        "publish": _cmd_publish,
        "latest": _cmd_latest,
        "run": _cmd_morning,
        "feedback": _cmd_feedback,
        "mail-status": _cmd_mail_status,
    }
    try:
        return commands[args.cmd](args)
    except (RecordError, TrackerError, FeedError, json.JSONDecodeError) as exc:
        print(f"jobtrends-morning {args.cmd}: {exc}", file=sys.stderr)  # noqa: T201
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
