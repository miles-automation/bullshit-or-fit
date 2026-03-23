"""Tests for proxy routes that forward requests to Spark Swarm."""

from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _mock_httpx_response(
    status_code: int = 200,
    json_data: dict | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# GET /api/v1/landing-config
# ---------------------------------------------------------------------------


class TestLandingConfig:
    @patch("app.routers.landing.httpx.Client")
    def test_returns_upstream_config(self, mock_client_cls: MagicMock) -> None:
        upstream_body = {
            "enabled": True,
            "cta": "Join",
            "headline": "H",
            "subheadline": "S",
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_httpx_response(200, upstream_body)
        mock_client_cls.return_value = mock_client

        response = client.get("/api/v1/landing-config")

        assert response.status_code == 200
        assert response.json() == upstream_body

    @patch("app.routers.landing.httpx.Client")
    def test_upstream_error_returns_http_error(
        self, mock_client_cls: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_httpx_response(
            502, {"detail": "bad gateway"}
        )
        mock_client_cls.return_value = mock_client

        response = client.get("/api/v1/landing-config")

        assert response.status_code == 502


# ---------------------------------------------------------------------------
# POST /api/v1/leads/submit
# ---------------------------------------------------------------------------


class TestLeadSubmit:
    VALID_PAYLOAD = {
        "email": "jane@example.com",
        "name": "Jane",
        "company": "Acme",
        "message": "Need help",
    }

    @patch("app.routers.leads.httpx.Client")
    def test_submit_success(self, mock_client_cls: MagicMock) -> None:
        upstream_body = {"message": "Lead created"}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(200, upstream_body)
        mock_client_cls.return_value = mock_client

        response = client.post("/api/v1/leads/submit", json=self.VALID_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == upstream_body

    @patch("app.routers.leads.httpx.Client")
    def test_submit_upstream_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(
            422, {"detail": "Rate limited"}
        )
        mock_client_cls.return_value = mock_client

        response = client.post("/api/v1/leads/submit", json=self.VALID_PAYLOAD)

        assert response.status_code == 422
        assert response.json()["detail"] == "Rate limited"

    def test_submit_invalid_email(self) -> None:
        payload = {**self.VALID_PAYLOAD, "email": "not-an-email"}
        response = client.post("/api/v1/leads/submit", json=payload)
        assert response.status_code == 422

    def test_submit_missing_name(self) -> None:
        payload = {"email": "jane@example.com"}
        response = client.post("/api/v1/leads/submit", json=payload)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/leads/resend
# ---------------------------------------------------------------------------


class TestLeadResend:
    @patch("app.routers.leads.httpx.Client")
    def test_resend_success(self, mock_client_cls: MagicMock) -> None:
        upstream_body = {"message": "If found, confirmation email was sent."}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(200, upstream_body)
        mock_client_cls.return_value = mock_client

        response = client.post(
            "/api/v1/leads/resend", json={"email": "jane@example.com"}
        )

        assert response.status_code == 200
        assert response.json() == upstream_body

    @patch("app.routers.leads.httpx.Client")
    def test_resend_upstream_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(
            429, {"detail": "Too many"}
        )
        mock_client_cls.return_value = mock_client

        response = client.post(
            "/api/v1/leads/resend", json={"email": "jane@example.com"}
        )

        assert response.status_code == 429

    def test_resend_invalid_email(self) -> None:
        response = client.post("/api/v1/leads/resend", json={"email": "bad"})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/leads/confirm
# ---------------------------------------------------------------------------


class TestLeadConfirm:
    @patch("app.routers.leads.httpx.Client")
    def test_confirm_success(self, mock_client_cls: MagicMock) -> None:
        upstream_body = {"status": "confirmed"}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_httpx_response(200, upstream_body)
        mock_client_cls.return_value = mock_client

        response = client.get(
            "/api/v1/leads/confirm", params={"token": "abc1234567890"}
        )

        assert response.status_code == 200
        assert response.json() == upstream_body

    @patch("app.routers.leads.httpx.Client")
    def test_confirm_upstream_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = _mock_httpx_response(
            404, {"detail": "Not found"}
        )
        mock_client_cls.return_value = mock_client

        response = client.get(
            "/api/v1/leads/confirm", params={"token": "abc1234567890"}
        )

        assert response.status_code == 404

    def test_confirm_missing_token(self) -> None:
        response = client.get("/api/v1/leads/confirm")
        assert response.status_code == 422

    def test_confirm_token_too_short(self) -> None:
        response = client.get("/api/v1/leads/confirm", params={"token": "short"})
        assert response.status_code == 422
