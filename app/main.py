import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.admin.router import router as admin_router
from app.common import health
from app.core.config import settings
from app.crawl import scheduler
from app.crawl.router import router as crawl_router
from app.web.router import router as web_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.CRAWL_SCHEDULE_ENABLED:
        scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── 모듈러 모놀리식: 단일 앱에 모듈별 라우터를 네임스페이스로 마운트 ──
API_PREFIX = "/api/v1"
app.include_router(health.router, prefix=f"{API_PREFIX}/health", tags=["Health"])
app.include_router(web_router, prefix=f"{API_PREFIX}/web", tags=["Web"])
app.include_router(crawl_router, prefix=f"{API_PREFIX}/crawl", tags=["Crawl"])
app.include_router(admin_router, prefix=f"{API_PREFIX}/admin", tags=["Admin"])


@app.get("/", tags=["Root"])
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
    }
