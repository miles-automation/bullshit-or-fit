"""Unit tests for the company pay leaderboard (DB-free via a fake session)."""

from app.jobtrends.company_pay import company_pay, format_company_pay


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def yield_per(self, _n: int) -> list:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def execute(self, *_a, **_k) -> _Result:  # noqa: ANN002, ANN003
        return _Result(self._rows)


def _rows() -> list:
    # (company_name, company_token, comp_min, comp_max)
    rows = []
    # OpenAI: 6 roles, midpoints centered ~300k
    for _ in range(6):
        rows.append(("OpenAI", "openai", 280000, 320000))
    # Acme: 6 roles ~160k
    for _ in range(6):
        rows.append(("Acme", "acme", 150000, 170000))
    # Tiny: only 2 roles -> below MIN_SAMPLE, excluded
    rows += [("Tiny", "tiny", 500000, 600000), ("Tiny", "tiny", 500000, 600000)]
    return rows


def test_company_pay_ranks_by_median_and_applies_floor() -> None:
    out = company_pay(_FakeSession(_rows()))
    names = [c.company_name for c in out]
    assert names == ["OpenAI", "Acme"]  # Tiny excluded (n<6), OpenAI ranks first
    assert out[0].median_usd == 300000
    assert out[0].n_with_comp == 6
    assert out[1].median_usd == 160000


def test_company_pay_min_sample_override() -> None:
    out = company_pay(_FakeSession(_rows()), min_sample=2)
    # Tiny now qualifies and, at ~550k median, tops the board.
    assert out[0].company_name == "Tiny"
    assert out[0].median_usd == 550000


def test_company_pay_empty_and_format() -> None:
    assert company_pay(_FakeSession([])) == []
    assert "no data" in format_company_pay([])
    out = format_company_pay(company_pay(_FakeSession(_rows())))
    assert "OpenAI" in out and "$300k" in out
