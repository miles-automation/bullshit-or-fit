"""Unit tests for cross-source comp unification (all DB-free)."""

from app.jobtrends.comp import (
    annualize_structured,
    comp_sources,
    format_comp_sources,
    parsed_comp_fields,
)


# ---- annualize_structured (USAJobs PositionRemuneration) ------------------


def test_annualize_per_year_passthrough() -> None:
    assert annualize_structured("99000", "153000", "Per Year") == (99000, 153000)


def test_annualize_two_letter_code() -> None:
    # The API sometimes sends the short code instead of the human string.
    assert annualize_structured("99000", "153000", "PA") == (99000, 153000)


def test_annualize_hourly_multiplies() -> None:
    # $30–$50/hr -> annualized ×2080.
    assert annualize_structured("30", "50", "Per Hour") == (62400, 104000)


def test_annualize_swaps_reversed_range() -> None:
    assert annualize_structured("153000", "99000", "Per Year") == (99000, 153000)


def test_annualize_rejects_variable_interval() -> None:
    # "Without Compensation" / fee-basis can't be annualized honestly.
    assert annualize_structured("0", "0", "Without Compensation") is None


def test_annualize_rejects_missing_and_implausible() -> None:
    assert annualize_structured(None, "100000", "Per Year") is None
    assert annualize_structured("abc", "100000", "Per Year") is None
    assert annualize_structured("500", "900", "Per Year") is None  # below floor
    assert annualize_structured("5000000", "9000000", "Per Year") is None  # above cap


def test_annualize_defaults_to_year_when_interval_absent() -> None:
    assert annualize_structured("120000", "160000", None) == (120000, 160000)


# ---- parsed_comp_fields (free-text fallback for boards) -------------------


def test_parsed_comp_fields_from_title_or_content() -> None:
    fields = parsed_comp_fields("Senior Engineer", "Comp: $150k–195k + equity")
    assert fields is not None
    assert fields["comp_kind"] == "parsed"
    assert fields["comp_currency"] == "USD"
    assert (fields["comp_min"], fields["comp_max"]) == (150000, 195000)


def test_parsed_comp_fields_none_when_no_salary() -> None:
    assert parsed_comp_fields("Backend Engineer", "Great team, competitive pay") is None


# ---- comp_sources / format (fake session over comp_source_stat) ----------


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


def test_comp_sources_sorts_data_first_then_median() -> None:
    # (source, n_roles, n_with_comp, coverage_pct, p25, median, p75)
    rows = [
        ("hn", 400, 120, 30.0, 150000, 180000, 210000),
        ("usajobs", 10000, 9800, 98.0, 70000, 110000, 150000),
        ("remote_board", 128, 0, 0.0, 0, 0, 0),  # no comp -> sinks to the bottom
    ]
    out = comp_sources(_FakeSession(rows))
    assert [r.source for r in out] == ["hn", "usajobs", "remote_board"]
    assert out[0].median_usd == 180000
    assert out[-1].n_with_comp == 0


def test_format_comp_sources_renders_and_empty() -> None:
    assert "no data" in format_comp_sources([])
    rows = [("usajobs", 10000, 9800, 98.0, 70000, 110000, 150000)]
    out = format_comp_sources(comp_sources(_FakeSession(rows)))
    assert "usajobs" in out
    assert "$110k" in out
