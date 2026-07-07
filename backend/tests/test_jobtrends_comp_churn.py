"""Unit tests for the comp parser and cohort/churn math (both DB-free)."""

from datetime import date

from app.jobtrends.comp import parse_comp
from app.jobtrends.recurrence import compute_cohorts

# --- comp parsing: accepts real salaries ----------------------------------


def test_comp_k_range() -> None:
    c = parse_comp("Full-time | $180-200K + equity | Remote")
    assert c is not None
    assert (c.min_amount, c.max_amount, c.currency, c.period) == (
        180000,
        200000,
        "USD",
        "year",
    )
    assert c.midpoint == 190000


def test_comp_k_range_with_usd_and_endash() -> None:
    c = parse_comp("OTE (Americas) | $150k–195k USD + equity | Go, Zig")
    assert c is not None
    assert (c.min_amount, c.max_amount) == (150000, 195000)


def test_comp_comma_range() -> None:
    c = parse_comp("Sacramento, CA | Full Time | $145,000-$170,000")
    assert c is not None
    assert (c.min_amount, c.max_amount) == (145000, 170000)


def test_comp_single_k() -> None:
    c = parse_comp("Senior Engineer | Remote | 160k base | Python")
    assert c is not None
    assert c.min_amount == c.max_amount == 160000


def test_comp_euro_currency() -> None:
    c = parse_comp("Berlin | €70k-90k | hybrid")
    assert c is not None
    assert c.currency == "EUR"


def test_comp_hourly_annualized() -> None:
    c = parse_comp("Contract | $75/hr | remote")
    assert c is not None
    assert c.period == "hour"
    assert c.min_amount == 75 * 2080


# --- comp parsing: rejects the noise Phase-1's naive regex swept in --------


def test_comp_rejects_funding() -> None:
    assert parse_comp("We just announced our $42M Series B! Join us.") is None


def test_comp_rejects_github_stars() -> None:
    assert parse_comp("Open source, 25k+ stars, Series A. Backend role.") is None


def test_comp_rejects_users_and_arr() -> None:
    assert parse_comp("We serve 500k users and $30k MRR. Hiring engineers.") is None


def test_comp_rejects_no_number() -> None:
    assert parse_comp("Competitive salary + equity. Remote. Senior SWE.") is None


def test_comp_rejects_implausible_band() -> None:
    # "5k" is below the floor (30k); a stray small number shouldn't parse as salary.
    assert parse_comp("Sign-on bonus 5k. Great team.") is None


def test_comp_picks_salary_over_nearby_funding() -> None:
    c = parse_comp("Series A startup, $12M raised. Comp: $150k-180k. Remote.")
    assert c is not None
    assert (c.min_amount, c.max_amount) == (150000, 180000)


def test_comp_rejects_401k_benefits() -> None:
    # Over full job descriptions the single-k regex reads "401k" as a $401k salary.
    assert parse_comp("Backend Engineer. Benefits: 401k matching, health, PTO.") is None
    assert parse_comp("Generous 401k, unlimited PTO, remote-first.") is None


def test_comp_keeps_401k_comma_salary() -> None:
    # Comma-form $401,000 is a genuine (if lofty) salary — must NOT be dropped.
    c = parse_comp("Principal Engineer | $401,000 base | Remote")
    assert c is not None
    assert c.min_amount == 401000


def test_comp_real_salary_survives_alongside_401k() -> None:
    # A real range wins even when 401k benefits are mentioned in the same post.
    c = parse_comp("Senior Eng | $180k-220k + 401k matching | Remote")
    assert c is not None
    assert (c.min_amount, c.max_amount) == (180000, 220000)


# --- cohort / churn math ---------------------------------------------------


def test_compute_cohorts_new_returning_churn() -> None:
    j, f, m = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
    pairs = [
        ("acme", j),
        ("globex", j),  # Jan: 2 new
        ("acme", f),
        ("initech", f),  # Feb: acme returns, globex churns, initech new
        ("initech", m),
        ("umbrella", m),  # Mar: initech returns, acme churns, umbrella new
    ]
    rows = compute_cohorts(pairs)
    assert [r.month for r in rows] == [j, f, m]

    jan, feb, mar = rows
    assert (
        jan.active_authors,
        jan.new_authors,
        jan.returning_authors,
        jan.churned_prev,
    ) == (2, 2, 0, 0)
    assert (
        feb.active_authors,
        feb.new_authors,
        feb.returning_authors,
        feb.churned_prev,
    ) == (2, 1, 1, 1)
    assert (
        mar.active_authors,
        mar.new_authors,
        mar.returning_authors,
        mar.churned_prev,
    ) == (2, 1, 1, 1)


def test_compute_cohorts_skips_null_author() -> None:
    d = date(2026, 1, 1)
    rows = compute_cohorts([("acme", d), ("", d), (None, d)])  # type: ignore[list-item]
    assert rows[0].active_authors == 1


def test_compute_cohorts_empty() -> None:
    assert compute_cohorts([]) == []
