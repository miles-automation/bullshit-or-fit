"""Wiring tests for the jobtrends dashboard API.

The response-shaping math lives in the service functions (unit-tested elsewhere);
here we just confirm the routes are mounted, return the documented shape, and stay
DB-free by overriding get_db with a fake empty session. The `/keywords` route
needs no DB and returns real taxonomy data.
"""

from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


class _EmptyResult:
    def all(self) -> list:
        return []

    def scalars(self) -> "_EmptyResult":
        return self

    def __iter__(self):
        return iter([])


class _EmptySession:
    def execute(self, *_a, **_k) -> _EmptyResult:  # noqa: ANN002, ANN003
        return _EmptyResult()

    def scalar(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN201
        return None


def _override_db():
    yield _EmptySession()


app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def test_keywords_route_returns_taxonomy() -> None:
    resp = client.get("/api/v1/jobtrends/keywords")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) > 10
    kws = {d["keyword"] for d in data}
    assert {"python", "agents", "remote"} <= kws
    assert all("category" in d for d in data)


def test_trend_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/trend?keywords=python,rust")
    assert resp.status_code == 200
    body = resp.json()
    assert body["months"] == []
    assert {s["keyword"] for s in body["series"]} == {"python", "rust"}


def test_comp_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/comp")
    assert resp.status_code == 200
    assert resp.json() == {"months": []}


def test_churn_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/churn")
    assert resp.status_code == 200
    body = resp.json()
    assert body["months"] == []
    assert body["distinct_authors"] == 0


def test_comp_sources_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/comp/sources")
    assert resp.status_code == 200
    assert resp.json() == {"sources": []}


def test_market_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/market")
    assert resp.status_code == 200
    assert resp.json() == {"months": []}


def test_companies_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/companies")
    assert resp.status_code == 200
    assert resp.json() == {"total_open": 0, "companies": 0, "top": []}


def test_remote_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/remote")
    assert resp.status_code == 200
    assert resp.json() == {
        "total_open": 0,
        "companies": 0,
        "top_companies": [],
        "top_categories": [],
    }


def test_usajobs_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/usajobs")
    assert resp.status_code == 200
    assert resp.json() == {
        "total_open": 0,
        "agencies": 0,
        "top_agencies": [],
        "top_categories": [],
    }


def test_skills_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/skills")
    assert resp.status_code == 200
    assert resp.json() == {"total_roles": 0, "sources": [], "skills": []}


def test_locations_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/locations")
    assert resp.status_code == 200
    assert resp.json() == {"sources": []}


def test_skill_comp_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/skills/comp")
    assert resp.status_code == 200
    assert resp.json() == {"sources": [], "skills": []}


def test_summary_route_empty_ok() -> None:
    resp = client.get("/api/v1/jobtrends/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_posts"] == 0
    assert body["risers"] == []
    assert body["latest_month"] is None
    assert body["seekers_per_100_jobs"] == 0.0
