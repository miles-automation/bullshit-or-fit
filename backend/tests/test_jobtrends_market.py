"""Unit tests for the demand/supply market report (DB-free via a fake session)."""

from datetime import date

from app.jobtrends.market import format_market_table, market_report


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def execute(self, *_a, **_k) -> _Result:  # noqa: ANN002, ANN003
        return _Result(self._rows)


def test_market_report_ratio() -> None:
    # (stream, month, post_count) rows out of stream_month.
    rows = [
        ("hiring", date(2026, 6, 1), 300),
        ("wants_hired", date(2026, 6, 1), 450),
        ("hiring", date(2026, 7, 1), 200),
        ("wants_hired", date(2026, 7, 1), 100),
    ]
    report = market_report(_FakeSession(rows))
    assert [m.month for m in report] == ["2026-06", "2026-07"]
    assert report[0].hiring_posts == 300
    assert report[0].wants_hired_posts == 450
    assert report[0].seekers_per_100_jobs == 150.0  # 450/300 * 100
    assert report[1].seekers_per_100_jobs == 50.0  # 100/200 * 100


def test_market_report_zero_hiring_is_safe() -> None:
    rows = [("wants_hired", date(2026, 6, 1), 50)]
    report = market_report(_FakeSession(rows))
    assert report[0].hiring_posts == 0
    assert report[0].seekers_per_100_jobs == 0.0  # no divide-by-zero


def test_format_market_table() -> None:
    rows = [("hiring", date(2026, 7, 1), 200), ("wants_hired", date(2026, 7, 1), 100)]
    out = format_market_table(market_report(_FakeSession(rows)))
    assert "2026-07" in out
    assert "seekers/100 jobs" in out


def test_format_market_table_empty() -> None:
    assert "no data" in format_market_table([])
