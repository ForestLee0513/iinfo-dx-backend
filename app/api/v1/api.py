"""API v1 라우터 취합 — 모든 엔드포인트 라우터를 하나로 묶는다.

main.py는 이 api_router를 /api/v1 프리픽스로 마운트한다. URL 경로는
모듈러 구조였을 때와 동일하게 유지된다(web/tables, web/auth, crawl 등).
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    admin_auth,
    admin_catalog,
    auth,
    crawl,
    health,
    tables,
)

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["Health"])
# 사용자 client API — 기존 /web 네임스페이스 유지
api_router.include_router(tables.router, prefix="/web/tables", tags=["Web"])
api_router.include_router(auth.router, prefix="/web/auth", tags=["Web"])
# 크롤 통합 API — 진단(targets/preview, 공개) + 수동 실행/스케줄(jobs/schedules, ADMIN)
api_router.include_router(crawl.router, prefix="/crawl", tags=["Crawl"])
# 어드민 API (인증 + ADMIN 역할, 회원 관리 전용)
api_router.include_router(admin_auth.router, prefix="/admin/auth", tags=["AdminAuth"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
# 어드민 카탈로그 조회 (등록된 서열표·곡 조회, 읽기 전용)
api_router.include_router(admin_catalog.router, prefix="/admin", tags=["Admin Catalog"])
