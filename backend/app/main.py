from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from app.config import settings
from app.logging_config import configure_logging

SPARK_SWARM_API_URL = settings.spark_swarm_api_url.rstrip("/")
SPARK_SLUG = settings.spark_slug


class LeadSubmitPayload(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    company: str | None = None
    message: str | None = None
    source_url: str | None = None
    website: str | None = None  # honeypot


class LeadResendPayload(BaseModel):
    email: EmailStr


app = FastAPI(title="Bullshit or Fit", version="0.1.0")
configure_logging(settings.environment)

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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/healthz")
def api_healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/landing-config")
def landing_config() -> JSONResponse:
    url = f"{SPARK_SWARM_API_URL}/public/sparks/{SPARK_SLUG}/landing-config"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
    if not resp.is_success:
        raise HTTPException(
            status_code=resp.status_code, detail="Failed to fetch landing config"
        )
    return JSONResponse(content=resp.json())


@app.post("/api/leads/submit")
def submit_lead(payload: LeadSubmitPayload, request: Request) -> JSONResponse:
    url = f"{SPARK_SWARM_API_URL}/public/sparks/{SPARK_SLUG}/leads"
    body = payload.model_dump()
    body["source_url"] = body.get("source_url") or str(request.url)
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=body)
    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return JSONResponse(content=resp.json())


@app.post("/api/leads/resend")
def resend_confirmation(payload: LeadResendPayload) -> JSONResponse:
    url = f"{SPARK_SWARM_API_URL}/public/sparks/{SPARK_SLUG}/leads/resend-confirmation"
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=payload.model_dump())
    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return JSONResponse(content=resp.json())


@app.get("/api/leads/confirm")
def confirm_lead(token: str = Query(..., min_length=10)) -> JSONResponse:
    url = f"{SPARK_SWARM_API_URL}/public/leads/confirm"
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params={"token": token})
    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return JSONResponse(content=resp.json())


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
