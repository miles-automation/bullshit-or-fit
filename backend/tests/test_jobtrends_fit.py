from datetime import UTC, datetime

import pytest

from app.jobtrends.fit import (
    FitRole,
    clearance_status,
    comp_fits,
    format_fit_list,
    location_fits,
    rank_key,
    title_fits,
)


@pytest.mark.parametrize(
    ("title", "ok"),
    [
        ("Senior Software Engineer", True),
        ("Founding Engineer", True),
        ("Staff Backend Developer (Python)", True),
        ("Senior Go Engineer", False),
        ("Rust Developer", False),
        ("Senior C++ Engineer", False),
        ("Engineering Manager", False),
        ("Software Engineering Intern", False),
        ("Senior iOS Engineer", False),
        ("Account Executive", False),
        ("Product Designer", False),
        ("Staff Security Engineer", False),
        ("Software Engineer, Haskell", False),
        ("Engineering Leader, Platform Engineering", False),
        ("Senior Customer Architect", False),
        ("Senior Backend Engineer", True),
    ],
)
def test_title_fits(title: str, ok: bool) -> None:
    assert title_fits(title) is ok


@pytest.mark.parametrize(
    ("location", "ok"),
    [
        ("Remote - US", True),
        ("Remote U.S.", True),
        ("Remote (Worldwide)", True),
        ("Remote", True),
        ("Fort Collins, CO", True),
        ("Cheyenne, WY", True),
        ("Remote - Canada", False),
        ("Remote - South Africa", False),
        ("Remote - California", True),
        ("Remote - Anywhere", True),
        ("US-TX-REMOTE", True),
        ("Remote (EMEA)", False),
        ("San Francisco, CA", False),
        (None, False),
    ],
)
def test_location_fits(location: str | None, ok: bool) -> None:
    assert location_fits(location) is ok


def test_comp_fits() -> None:
    assert comp_fits(None, None)
    assert comp_fits(150_000, "USD")
    assert not comp_fits(149_999, "USD")
    assert not comp_fits(300_000, "EUR")


def _role(**kw) -> FitRole:  # noqa: ANN003
    base = dict(
        source="ats",
        company_name="Acme",
        title="Senior Software Engineer",
        location="Remote - US",
        url="https://x",
        comp_min=None,
        comp_max=None,
        team_size=None,
        first_seen=datetime(2026, 11, 15, tzinfo=UTC),
    )
    base.update(kw)
    return FitRole(**base)


def test_rank_puts_small_companies_first_then_pay() -> None:
    big_rich = _role(company_name="Big", comp_max=300_000)
    small = _role(company_name="Small", team_size=12)
    mid = _role(company_name="Mid", comp_max=200_000)
    assert sorted([mid, big_rich, small], key=rank_key) == [small, big_rich, mid]


def test_format_fit_list() -> None:
    text = format_fit_list([_role(team_size=12, comp_min=150_000, comp_max=200_000)], 1)
    assert "1 new fitting roles" in text
    assert (
        "Acme, 12 people: Senior Software Engineer (Remote - US; $150k-$200k)" in text
    )
    assert format_fit_list([], 3) == "no new fitting roles in the last 3 day(s)"


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("Must hold an active TS/SCI clearance.", "required"),
        ("Active Secret clearance required.", "required"),
        ("This position requires a current Top Secret clearance", "required"),
        ("Nice to Have: Active TS/SCI clearance", "sponsored"),
        ("Current TS/SCI clearance, or willingness to acquire one", "sponsored"),
        ("Active Secret clearance preferred.", "sponsored"),
        ("Must be able to obtain a Secret clearance.", "sponsored"),
        ("We sponsor security clearances for this role.", "sponsored"),
        ("Ability to obtain and maintain a TS/SCI clearance.", "sponsored"),
        ("Active U.S. Government TS/SCI clearance.", "required"),
        ("Clearance Level Must Currently Possess: Secret", "required"),
        ("Minimum Clearance Required to Start: Secret", "required"),
        ("Active Top Secret Clearance (Interim TS will be considered)", "required"),
        (
            "This position requires the ability to obtain a US Security Clearance",
            "sponsored",
        ),
        ("Clearance Required: Ability to Obtain Secret", "sponsored"),
        (
            "Travel Required: Up to 10% Clearance Required: Ability to Obtain Secret",
            "sponsored",
        ),
        ("Eligibility to obtain and maintain a U.S. Security Clearance.", "sponsored"),
        ("Ability to obtain an active U.S. Security Clearance", "sponsored"),
        (
            "Must have the ability to obtain / maintain a Public Trust clearance",
            "sponsored",
        ),
        (
            "Able to obtain any customer- or contract-required security clearance",
            "sponsored",
        ),
        (
            "Obtaining and maintaining all required security clearances is a condition of employment.",
            "sponsored",
        ),
        ("TS/SCI with Poly required", "required"),
        ("Clearance: Secret (active)", "required"),
        ("Candidates must currently hold a Secret clearance", "required"),
        ("Active Secret clearance required, Python preferred.", "required"),
        (
            "Ability to work independently, active Secret clearance required.",
            "required",
        ),
        (
            "No active security clearance is required. We sponsor your clearance.",
            "sponsored",
        ),
        ("An active security clearance is not required.", None),
        ("Security Clearance Requirement: None", None),
        ("An active Secret clearance is desired.", "sponsored"),
        ("Active Secret clearance is a bonus.", "sponsored"),
        ("An active Secret clearance is nice-to-have.", "sponsored"),
        (
            "Must have an active Secret clearance or be eligible to obtain one.",
            "sponsored",
        ),
        ("Office of Clearance and Settlement oversees markets.", None),
        (
            "An interim and/or final U.S. Secret Clearance Post-Start is required",
            "sponsored",
        ),
        ("Active Federal Public Trust", "required"),
        ("Build agents in Python with Postgres.", None),
        ("Requires 5 years of Python. Security is important.", None),
    ],
)
def test_clearance_status(text: str, status: str | None) -> None:
    assert clearance_status(text) == status


def test_format_tags_sponsored_clearance() -> None:
    text = format_fit_list([_role(clearance="sponsored")], 1)
    assert "[ats] [clearance: sponsored]" in text


def test_clearance_status_is_fast_on_pathological_input() -> None:
    import time

    start = time.perf_counter()
    for text in ("sponsor" * 3000, "obtain " * 3000, "clearance " * 3000, "a" * 60000):
        clearance_status(text)
    assert time.perf_counter() - start < 1.0
