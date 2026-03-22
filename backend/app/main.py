import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.logging_config import configure_logging
from app.routers import landing, leads

logger = logging.getLogger(__name__)

configure_logging(settings.environment)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    logger.info("App starting", extra={"environment": settings.environment})
    yield
    # shutdown
    logger.info("App shutting down")


app = FastAPI(title="Bullshit or Fit", version="0.1.0", lifespan=lifespan)

allow_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# --- Health checks (kept in main.py per convention) ---


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/healthz")
def api_healthz() -> dict[str, str]:
    return {"status": "ok"}


# --- Routers ---

app.include_router(leads.router, prefix="/api")
app.include_router(landing.router, prefix="/api")


# --- Static / SPA fallback ---

static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/{path:path}")
    def serve_spa(path: str):
        if path.startswith("api/") or path in {"healthz", "api/v1/healthz"}:
            raise HTTPException(status_code=404, detail="Not found")
        candidate = static_dir / path
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")
