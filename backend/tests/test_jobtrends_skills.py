"""Unit tests for the cross-source skill-demand pivot (DB-free via a fake session)."""

from app.jobtrends.skill_demand import format_skill_table, skill_demand


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


def test_skill_demand_overall_and_per_source() -> None:
    # (source, keyword, category, roles_matched, roles_total)
    rows = [
        ("ats", "python", "language", 60, 100),
        ("usajobs", "python", "language", 10, 100),
        ("ats", "rust", "language", 20, 100),
        ("usajobs", "rust", "language", 0, 100),
    ]
    report = skill_demand(_FakeSession(rows))
    assert report.total_roles == 200  # 100 ats + 100 usajobs
    assert report.sources == ["ats", "usajobs"]

    py = report.skills[0]  # sorted by roles_matched desc -> python first (70)
    assert py.keyword == "python"
    assert py.roles_matched == 70
    assert py.share == 35.0  # 70 / 200
    assert py.by_source == {"ats": 60.0, "usajobs": 10.0}

    rust = report.skills[1]
    assert rust.roles_matched == 20
    assert rust.share == 10.0
    # rust never appears in usajobs, so only ats has a (non-zero-total) share entry
    assert rust.by_source == {"ats": 20.0, "usajobs": 0.0}


def test_skill_demand_empty() -> None:
    report = skill_demand(_FakeSession([]))
    assert report.total_roles == 0
    assert report.skills == []
    assert "no data" in format_skill_table(report)


def test_format_skill_table() -> None:
    rows = [("ats", "python", "language", 60, 100)]
    out = format_skill_table(skill_demand(_FakeSession(rows)))
    assert "python" in out
    assert "ats:60%" in out
