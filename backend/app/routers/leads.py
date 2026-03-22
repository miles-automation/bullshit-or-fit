import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from app.config import settings

router = APIRouter(prefix="/leads", tags=["leads"])

SPARK_SWARM_API_URL = settings.spark_swarm_api_url.rstrip("/")
SPARK_SLUG = settings.spark_slug


class LeadSubmitIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    company: str | None = None
    message: str | None = None
    source_url: str | None = None
    website: str | None = None  # honeypot


class LeadResendIn(BaseModel):
    email: EmailStr


@router.post("/submit")
def submit_lead(payload: LeadSubmitIn, request: Request) -> JSONResponse:
    url = f"{SPARK_SWARM_API_URL}/public/sparks/{SPARK_SLUG}/leads"
    body = payload.model_dump()
    body["source_url"] = body.get("source_url") or str(request.url)
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=body)
    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return JSONResponse(content=resp.json())


@router.post("/resend")
def resend_confirmation(payload: LeadResendIn) -> JSONResponse:
    url = f"{SPARK_SWARM_API_URL}/public/sparks/{SPARK_SLUG}/leads/resend-confirmation"
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=payload.model_dump())
    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return JSONResponse(content=resp.json())


@router.get("/confirm")
def confirm_lead(token: str = Query(..., min_length=10)) -> JSONResponse:
    url = f"{SPARK_SWARM_API_URL}/public/leads/confirm"
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params={"token": token})
    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    return JSONResponse(content=resp.json())
