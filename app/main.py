import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin import jobs as admin_jobs
from app.admin.router import router as admin_router
from app.common import health
from app.common.openapi import setup_docs
from app.common.redis import close_redis
from app.core.config import settings
from app.difficulty_crawl import scheduler
from app.difficulty_crawl.router import router as difficulty_crawl_router
from app.songs_crawl.router import router as songs_crawl_router
from app.web.router import router as web_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 스케줄 활성 여부는 Redis 오버라이드/.env 설정으로 apply_schedule에서 결정
    await scheduler.start()
    # 서버 중단으로 끊긴 크롤 작업이 있으면 남은 스텝부터 이어서 실행
    await admin_jobs.resume_interrupted_job()
    yield
    scheduler.shutdown()
    await close_redis()


# 기본 문서 비활성화 — 공개/비공개 스웨거는 setup_docs가 직접 마운트한다
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# 별도 FE(클라이언트/어드민)에서의 브라우저 호출 허용
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ── 모듈러 모놀리식: 단일 앱에 모듈별 라우터를 네임스페이스로 마운트 ──
API_PREFIX = "/api/v1"
app.include_router(health.router, prefix=f"{API_PREFIX}/health", tags=["Health"])
app.include_router(web_router, prefix=f"{API_PREFIX}/web", tags=["Web"])
app.include_router(songs_crawl_router, prefix=f"{API_PREFIX}/songs-crawl", tags=["SongsCrawl"])
app.include_router(difficulty_crawl_router, prefix=f"{API_PREFIX}/difficulty-crawl", tags=["DifficultyCrawl"])
app.include_router(admin_router, prefix=f"{API_PREFIX}/admin", tags=["Admin"])

# 공개(/docs — PUBLIC 마커 엔드포인트만) / 비공개(/internal/docs — 전체) 문서.
# 모든 include_router 뒤에 호출해야 스펙에 전 라우트가 잡힌다.
setup_docs(app)


@app.get("/", include_in_schema=False)
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "internal_docs": "/internal/docs",
    }
