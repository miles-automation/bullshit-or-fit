"""Unit tests for the comp × skill pivot (DB-free via a fake session)."""

from app.jobtrends.skill_comp import format_skill_comp, skill_comp


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


# (source, keyword, category, n_with_comp, p25, median, p75)
ROWS = [
    ("ats", "go", "language", 300, 180000, 210000, 250000),
    ("usajobs", "go", "language", 40, 85000, 110000, 140000),
    ("hn", "go", "language", 60, 160000, 185000, 210000),
    ("ats", "rust", "language", 120, 190000, 220000, 260000),
    # rust only in one source -> excluded by min_sources=2
]


def test_skill_comp_pivots_and_ranks() -> None:
    report = skill_comp(_FakeSession(ROWS))
    assert report.sources == ["ats", "hn", "usajobs"]
    # only 'go' has >=2 sources
    assert [s.keyword for s in report.skills] == ["go"]
    go = report.skills[0]
    assert go.total_n == 400  # 300 + 40 + 60
    assert set(go.by_source) == {"ats", "hn", "usajobs"}
    assert go.by_source["ats"].median_usd == 210000
    assert go.by_source["usajobs"].median_usd == 110000  # federal much lower


def test_skill_comp_excludes_single_source_skills() -> None:
    report = skill_comp(_FakeSession(ROWS), min_sources=2)
    assert "rust" not in {s.keyword for s in report.skills}
    # allowing single-source surfaces it
    report_all = skill_comp(_FakeSession(ROWS), min_sources=1)
    assert "rust" in {s.keyword for s in report_all.skills}


def test_skill_comp_empty_and_format() -> None:
    assert skill_comp(_FakeSession([])).skills == []
    assert "no data" in format_skill_comp(skill_comp(_FakeSession([])))
    out = format_skill_comp(skill_comp(_FakeSession(ROWS)))
    assert "go" in out
    assert "usajobs:$110k" in out
