import os

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str


@router.get("", response_model=HealthResponse)
def health_check(response: Response):
    response.headers["X-Deploy-Commit"] = os.environ.get("DEPLOY_SHA", "")
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "version": settings.APP_VERSION,
    }
