"""API v1 라우터 취합 — 모든 엔드포인트 라우터를 하나로 묶는다.

main.py는 이 api_router를 /api/v1 프리픽스로 마운트한다. URL 경로는
모듈러 구조였을 때와 동일하게 유지된다(web/tables, web/auth, songs-crawl 등).
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    admin_auth,
    auth,
    difficulty_crawl,
    health,
    songs_crawl,
    tables,
)

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["Health"])
# 사용자 client API — 기존 /web 네임스페이스 유지
api_router.include_router(tables.router, prefix="/web/tables", tags=["Web"])
api_router.include_router(auth.router, prefix="/web/auth", tags=["Web"])
# 크롤 진단/미리보기 (공개, 읽기 전용)
api_router.include_router(songs_crawl.router, prefix="/songs-crawl", tags=["SongsCrawl"])
api_router.include_router(
    difficulty_crawl.router, prefix="/difficulty-crawl", tags=["DifficultyCrawl"]
)
# 어드민 API (인증 + ADMIN 역할)
api_router.include_router(admin_auth.router, prefix="/admin/auth", tags=["AdminAuth"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
