"""Operator CLI for bounded fulfillment automations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.automations.pdf_batch import AutomationError, run_pdf_batch
from app.automations.spec import load_pdf_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gig-automation")
    sub = parser.add_subparsers(dest="command", required=True)
    pdf = sub.add_parser("pdf-run", help="run a PDF-to-table batch")
    pdf.add_argument("--spec", type=Path, required=True)
    pdf.add_argument("--input", type=Path, required=True)
    pdf.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "pdf-run":
            result = run_pdf_batch(load_pdf_spec(args.spec), args.input, args.output)
            print(  # noqa: T201
                json.dumps(
                    {
                        "run_id": result.run_id,
                        "documents": result.documents,
                        "pages": result.pages,
                        "required_exception_rate": result.required_exception_rate,
                        "acceptance_passed": result.acceptance_passed,
                        "elapsed_ms": result.elapsed_ms,
                        "workbook": str(result.workbook_path),
                        "audit_manifest": str(result.manifest_path),
                    },
                    indent=2,
                )
            )
            return 0 if result.acceptance_passed else 3
    except (AutomationError, OSError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
