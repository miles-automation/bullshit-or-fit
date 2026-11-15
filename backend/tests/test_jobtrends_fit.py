from datetime import UTC, datetime, timedelta

import pytest

from app.jobtrends.fit import (
    FitRole,
    language_blocks,
    posting_fresh,
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


def test_rank_puts_small_companies_first_then_newest_then_pay() -> None:
    day = datetime(2026, 11, 15, tzinfo=UTC)
    small = _role(
        company_name="Small", team_size=12, posted_at=day - timedelta(days=20)
    )
    fresh = _role(company_name="Fresh", posted_at=day, comp_max=160_000)
    fresh_rich = _role(company_name="FreshRich", posted_at=day, comp_max=300_000)
    old = _role(company_name="Old", posted_at=day - timedelta(days=9), comp_max=400_000)
    assert sorted([old, fresh, small, fresh_rich], key=rank_key) == [
        small,
        fresh_rich,
        fresh,
        old,
    ]


def test_format_fit_list() -> None:
    text = format_fit_list([_role(team_size=12, comp_min=150_000, comp_max=200_000)], 1)
    assert "1 new fitting roles" in text
    assert (
        "Acme, 12 people: Senior Software Engineer (Remote - US; $150k-$200k; first seen"
        in text
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
            "Must currently hold an active Secret clearance and be able to obtain TS/SCI",
            "required",
        ),
        ("Active Secret clearance required No visa sponsorship available", "required"),
        (
            "U.S. citizenship required and an active Secret clearance is preferred",
            "sponsored",
        ),
        ("Active Secret clearance Python preferred", "required"),
        ("Active Secret clearance required (no exceptions)", "required"),
        ("Candidates do not need an active Secret clearance", None),
        ("An active security clearance is not a prerequisite", None),
        (
            "Must possess and maintain a valid U.S. government-issued security clearance",
            "required",
        ),
        ("Must possess an active Secret clearance upon hire", "required"),
        ("Active Secret required", "required"),
        ("U.S. citizenship required; no clearance needed", None),
        (
            "Clearance Level Must Currently Possess: None Clearance Level Must Be Able to Obtain: Secret",
            "sponsored",
        ),
        ("Must be willing and able to obtain a Secret clearance", "sponsored"),
        ("Our secret sauce is great people", None),
        ("Applicants are not required to have an active Secret clearance.", None),
        ("An active Secret clearance is highly preferred.", "sponsored"),
        ("Active Secret clearance is highly desired", "sponsored"),
        ("Ideally candidates hold an active Secret clearance", "sponsored"),
        (
            "Preferred Qualifications: Five years of Python development experience Active Secret clearance",
            "sponsored",
        ),
        (
            "Preferred Qualifications:\nFive years of Python\nActive Secret clearance",
            "sponsored",
        ),
        (
            "Preferred Qualifications: Python\nRequirements: Active Secret clearance",
            "required",
        ),
        ("Nice to Have: Kubernetes\nActive Secret clearance required", "required"),
        ("Security Clearance Required: No", None),
        (
            "US Citizenship required (future security clearance may be required)",
            "sponsored",
        ),
        (
            "Active or prior US security clearance, or exposure to digital forensics",
            "sponsored",
        ),
        ("Active Secret or TS/SCI clearance required", "required"),
        ("Active Secret clearance or higher required", "required"),
        ("Must have an active Secret clearance or equivalent.", "required"),
        (
            "Must have an active Secret clearance or pass a background investigation.",
            "sponsored",
        ),
        (
            "Must hold an active Secret clearance or be willing to undergo an investigation.",
            "sponsored",
        ),
        ("Must possess an active Secret clearance or TS.", "required"),
        ("Nice to Have\nActive Secret clearance", "sponsored"),
        ("Nice-to-Haves\nActive TS/SCI clearance", "sponsored"),
        ("Nice to Have\nPython\nRequired:\nActive Secret clearance", "required"),
        ("Active TS/SCI or Top Secret clearance", "required"),
        ("Active US Security Clearance (TS/SCI) required", "required"),
        (
            "Preferred Qualifications Experience with Python and distributed systems Active Secret clearance Required Qualifications Five years of software engineering experience",
            "sponsored",
        ),
        (
            "Preferred Qualifications:\nPython\nRequired:\nActive Secret clearance",
            "required",
        ),
        (
            "Required Qualifications Active Secret clearance Preferred Qualifications Kubernetes",
            "required",
        ),
        (
            "Security Clearance: This position requires the ability to obtain a US Security Clearance for which the US Government requires US Citizenship",
            "sponsored",
        ),
        (
            "CLEARANCE: Must be able to obtain and maintain a US government security clearance",
            "sponsored",
        ),
        (
            "Candidates with an ACTIVE PUBLIC TRUST or SUITABILITY are preferred",
            "sponsored",
        ),
        ("Clearance Required: Active Secret Clearance", "required"),
        ("Security Clearance: TS/SCI", None),
        ("Active security clearance Salary Range: $169,350 - $190,000", "required"),
        ("Active Secret Clearance Pay Information GeoZone: D", "required"),
        ("Security Clearance Requirement: NoneThis position is remote", None),
        (
            "Preferred Qualifications: a Secret Clearance (or higher level) is preferred",
            "sponsored",
        ),
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


def test_format_shows_posting_age() -> None:
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)
    text = format_fit_list([_role(posted_at=now - timedelta(days=3))], 1, now=now)
    assert "posted 3d ago" in text
    assert "posted today" in format_fit_list([_role(posted_at=now)], 1, now=now)


DITTO = (
    "Build reliable Rust systems \u2014 Design and deliver production-grade libraries and services. "
    "Relevant technical toolkit \u2014 You're comfortable building in Rust, writing asynchronous code."
)


@pytest.mark.parametrize(
    ("text", "blocked"),
    [
        (DITTO, True),
        (
            "Strong experience with Go in production. You will own our Go services.",
            True,
        ),
        ("5+ years of professional Java experience required.", True),
        (
            "We use Kotlin and Scala across the backend; experience with Kotlin required.",
            True,
        ),
        ("Our stack is Python, TypeScript and some Go services.", False),
        ("Experience with Python, Go, or Java in production.", False),
        ("Python and Postgres. Rust experience is a plus.", False),
        ("Build agents in Python. Familiarity with Rust is nice to have.", False),
        ("Let's go build the future together. Go above and beyond.", False),
        ("We write JavaScript and TypeScript everywhere.", False),
        ("Strong C++ skills required for the trading engine.", True),
        ("Nice to have: Rust. Bonus: Java.", False),
        (
            "Strong backend skills in at least one production language, such as Go, Rust, Node.js or Python.",
            False,
        ),
        ("Working proficiency in Java or Node.js.", False),
        (
            "We build .NET services. We integrate with Java services and Java clients.",
            False,
        ),
        ("Go and build the future with us.", False),
        ("Go experience is required.", True),
        ("3+ years of Go experience required.", True),
        (
            "Our frontend is TypeScript. You must have 5 years of production Rust experience.",
            True,
        ),
        ("We use Rust and Kotlin internally.", True),
        ("C# and .NET backend. Some Java tooling around the edges.", False),
        ("", False),
    ],
)
def test_language_blocks(text: str, blocked: bool) -> None:
    assert language_blocks(text) is blocked


def test_posting_fresh_boundaries() -> None:
    oldest = datetime(2026, 10, 16, tzinfo=UTC)
    assert posting_fresh(None, oldest)
    assert posting_fresh(oldest, oldest)
    assert posting_fresh(oldest + timedelta(seconds=1), oldest)
    assert not posting_fresh(oldest - timedelta(seconds=1), oldest)
