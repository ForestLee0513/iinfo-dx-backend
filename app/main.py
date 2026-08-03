import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.openapi import setup_docs
from app.db.redis import close_redis
from app.services.iidx.admin import jobs as admin_jobs
from app.services.iidx.difficulty_crawl import scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 대상별 스케줄은 Redis에 저장된 설정만 적용 (없으면 자동 실행 없음)
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

# 모든 v1 라우터를 /api/v1 아래로 마운트 (app/api/v1/api.py에서 취합)
app.include_router(api_router, prefix="/api/v1")

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
