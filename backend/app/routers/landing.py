import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings

router = APIRouter(prefix="/landing-config", tags=["landing"])

SPARK_SWARM_API_URL = settings.spark_swarm_api_url.rstrip("/")
SPARK_SLUG = settings.spark_slug


@router.get("")
def landing_config() -> JSONResponse:
    url = f"{SPARK_SWARM_API_URL}/public/sparks/{SPARK_SLUG}/landing-config"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
    if not resp.is_success:
        raise HTTPException(
            status_code=resp.status_code, detail="Failed to fetch landing config"
        )
    return JSONResponse(content=resp.json())
