from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("")
def health_check():
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "version": settings.APP_VERSION,
    }
