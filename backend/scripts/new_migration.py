"""Create an Alembic migration with a sequential 4-digit rev-id.

    uv run python scripts/new_migration.py "describe the change"

Files sort as 0001_*, 0002_* (not random hashes).
"""

import re
import subprocess
import sys
from pathlib import Path

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def next_rev_id() -> str:
    nums = [int(m.group(1)) for f in VERSIONS.glob("*.py") if (m := re.match(r"(\d+)_", f.name))]
    return f"{(max(nums) + 1) if nums else 1:04d}"


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print('usage: new_migration.py "describe the change"', file=sys.stderr)  # noqa: T201
        return 2
    message = sys.argv[1]
    return subprocess.run(
        ["alembic", "revision", "--autogenerate", "-m", message, "--rev-id", next_rev_id()],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
