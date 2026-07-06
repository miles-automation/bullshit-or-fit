"""Unit tests for the jobtrends extract + trend stages (DB-free).

Keyword matching is pure; the trend pivot is exercised with a fake session that
returns constructed rows, so the share/MoM math is verified without Postgres.
"""

from dataclasses import dataclass
from datetime import date

from app.jobtrends.extract import compile_taxonomy, match_keywords
from app.jobtrends.taxonomy import flat_keywords, keyword_category
from app.jobtrends.trend import (
    KeywordTrend,
    TrendReport,
    format_trend_table,
    keyword_trend,
)

# --- taxonomy / matching ---------------------------------------------------


def test_flat_keywords_and_categories_align() -> None:
    flat = flat_keywords()
    cats = keyword_category()
    assert set(flat) == set(cats)
    assert cats["python"] == "language"
    assert cats["mcp"] == "ai"
    assert cats["remote"] == "role"


def test_match_keywords_presence() -> None:
    patterns = compile_taxonomy()
    text = "Founding engineer: Python/FastAPI, Postgres, Claude + MCP. Remote."
    hits = match_keywords(text, patterns)
    assert {
        "python",
        "fastapi",
        "postgres",
        "claude",
        "mcp",
        "remote",
        "founding",
    } <= hits


def test_match_keywords_word_boundaries() -> None:
    patterns = compile_taxonomy()
    # "go" must not fire inside "categorize"; "ts" not inside "guts".
    assert "go" not in match_keywords("we categorize things", patterns)
    assert "go" in match_keywords("we use Go and Rust", patterns)
    assert "typescript" in match_keywords("strong TS skills", patterns)
    assert "typescript" not in match_keywords("trust your guts", patterns)


def test_match_keywords_case_insensitive() -> None:
    patterns = compile_taxonomy()
    assert "kubernetes" in match_keywords("KUBERNETES and k8s", patterns)
    assert "rust" in match_keywords("RUST systems role", patterns)


# --- trend pivot (fake session) --------------------------------------------


@dataclass(frozen=True)
class _Row:
    month: date
    keyword: str
    posts_matched: int
    posts_total: int


class _FakeResult:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def all(self) -> list[_Row]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def execute(self, *_a, **_k) -> _FakeResult:  # noqa: ANN002, ANN003
        return _FakeResult(self._rows)


def test_keyword_trend_shares_and_mom() -> None:
    rows = [
        _Row(date(2026, 6, 1), "python", 10, 100),
        _Row(date(2026, 7, 1), "python", 25, 100),
        _Row(date(2026, 6, 1), "rust", 5, 100),
        _Row(date(2026, 7, 1), "rust", 3, 100),
    ]
    report = keyword_trend(_FakeSession(rows), ["python", "rust"])
    assert report.months == ["2026-06", "2026-07"]
    py = next(k for k in report.keywords if k.keyword == "python")
    assert py.shares == [10.0, 25.0]
    assert py.mom_delta_pts == 15.0
    rust = next(k for k in report.keywords if k.keyword == "rust")
    assert rust.shares == [5.0, 3.0]
    assert rust.mom_delta_pts == -2.0


def test_keyword_trend_missing_month_is_none() -> None:
    rows = [
        _Row(date(2026, 6, 1), "python", 10, 100),
        _Row(date(2026, 7, 1), "rust", 5, 100),  # python absent in July
    ]
    report = keyword_trend(_FakeSession(rows), ["python"])
    py = report.keywords[0]
    assert py.shares == [10.0, None]  # July has data (rust) but no python row
    assert py.mom_delta_pts is None  # can't delta against a missing month


def test_keyword_trend_zero_total_is_safe() -> None:
    rows = [_Row(date(2026, 6, 1), "python", 0, 0)]
    report = keyword_trend(_FakeSession(rows), ["python"])
    assert report.keywords[0].shares == [0.0]  # no divide-by-zero


def test_format_trend_table_renders() -> None:
    report = TrendReport(
        months=["2026-06", "2026-07"],
        keywords=[KeywordTrend("python", [10.0, 25.0], 15.0)],
    )
    out = format_trend_table(report)
    assert "python" in out
    assert "+15pt" in out
    assert "06" in out and "07" in out


def test_format_trend_table_empty() -> None:
    out = format_trend_table(TrendReport(months=[], keywords=[]))
    assert "no data" in out
