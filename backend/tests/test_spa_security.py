from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from app.main import _safe_spa_file


def _make_static(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html>INDEX")
    (static / "assets" / "app.js").write_text("APPJS")

    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    link = static / "link.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pass
    return static


def test_legit_nested_file_is_served(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    assert (
        _safe_spa_file(static, "assets/app.js")
        == (static / "assets" / "app.js").resolve()
    )


def test_index_is_served(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    assert _safe_spa_file(static, "index.html") == (static / "index.html").resolve()


def test_missing_path_falls_back_to_index(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    assert (
        _safe_spa_file(static, "some/client/route") == static.resolve() / "index.html"
    )


def test_absolute_system_path_is_rejected(tmp_path: Path) -> None:
    static = _make_static(tmp_path)

    assert _safe_spa_file(static, "/etc/passwd") == static.resolve() / "index.html"


def test_absolute_path_to_out_of_root_secret_is_rejected(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    secret = tmp_path / "secret.txt"
    assert _safe_spa_file(static, str(secret)) == static.resolve() / "index.html"


def test_dotdot_traversal_is_rejected(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    assert _safe_spa_file(static, "../secret.txt") == static.resolve() / "index.html"
    assert _safe_spa_file(static, "../../etc/passwd") == static.resolve() / "index.html"


def test_symlink_escaping_root_is_rejected(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    if not (static / "link.txt").is_symlink():
        import pytest

        pytest.skip("symlinks unsupported on this platform")
    assert _safe_spa_file(static, "link.txt") == static.resolve() / "index.html"


def _client(static: Path) -> TestClient:
    app = FastAPI()

    @app.get("/{path:path}")
    async def spa_fallback(path: str) -> FileResponse:
        return FileResponse(_safe_spa_file(static, path))

    return TestClient(app)


def test_http_legit_asset(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    resp = _client(static).get("/assets/app.js")
    assert resp.status_code == 200
    assert resp.text == "APPJS"


def test_http_spa_deeplink_returns_index(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    resp = _client(static).get("/some/client/route")
    assert resp.status_code == 200
    assert "INDEX" in resp.text


def test_http_root_returns_index(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    resp = _client(static).get("/")
    assert resp.status_code == 200
    assert "INDEX" in resp.text


def test_http_encoded_traversal_returns_index_not_secret(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    client = _client(static)

    for target in ("/%2e%2e/secret.txt", "/%2e%2e%2f%2e%2e%2fetc%2fpasswd"):
        resp = client.get(target)
        assert resp.status_code == 200
        assert "TOP-SECRET" not in resp.text
        assert "INDEX" in resp.text


def test_http_symlink_returns_index_not_secret(tmp_path: Path) -> None:
    static = _make_static(tmp_path)
    if not (static / "link.txt").is_symlink():
        import pytest

        pytest.skip("symlinks unsupported on this platform")
    resp = _client(static).get("/link.txt")
    assert resp.status_code == 200
    assert "TOP-SECRET" not in resp.text
