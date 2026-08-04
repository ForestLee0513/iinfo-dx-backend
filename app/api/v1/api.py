"""API v1 라우터 취합 — 스키마 2계층(public/iidx)에 맞춰 도메인별로 묶는다.

main.py가 이 api_router를 /api/v1 프리픽스로 마운트한다. URL 구조:
  - 공용 계정 계층(public 스키마): /api/v1/auth, /api/v1/profile — 최종 사용자용, 모든 서비스 공유.
  - 플랫폼 어드민 콘솔(전체 서비스 관할): /api/v1/admin/* — 어드민 로그인/세션 + 회원 관리.
    특정 서비스에 종속되지 않는 운영자 계정 계층이므로 /iidx 밖에 둔다.
  - IIDX 서비스(iidx 스키마): /api/v1/iidx/* — 서비스 데이터(난이도표/크롤/카탈로그).
    다른 서비스가 붙으면 /api/v1/<svc>/* 로 같은 패턴 추가.
  - 헬스체크: /api/v1/health (서비스 무관).
"""

from fastapi import APIRouter

from app.api.v1.routes import health
from app.api.v1.routes.account import auth, profile
from app.api.v1.routes.admin import auth as admin_auth, users as admin_users
from app.api.v1.routes.iidx import admin_catalog, crawl, profile as iidx_profile, scores as iidx_scores, tables

api_router = APIRouter()

# 헬스체크 (서비스 무관)
api_router.include_router(health.router, prefix="/health", tags=["Health"])

# ── 공용 계정 계층 (public 스키마) — 최종 사용자, 모든 서비스 공유 ──
api_router.include_router(auth.router, prefix="/auth", tags=["Account Auth"])
# 서비스별 프로필 라우터를 /profile/{identifier} 앞에 등록해야 경로 충돌을 피한다.
api_router.include_router(iidx_profile.router, prefix="/profile/iidx", tags=["IIDX Profile"])
api_router.include_router(profile.router, prefix="/profile", tags=["Account Profile"])

# ── 플랫폼 어드민 콘솔 (전체 서비스 관할) — /admin ──
# 어드민 로그인/세션과 회원 관리는 특정 서비스가 아니라 플랫폼 전체를 관할하므로
# /iidx 밖 최상위에 둔다. (회원/제재/역할은 public 스키마 = 계정 계층 데이터)
admin_router = APIRouter(prefix="/admin")
admin_router.include_router(admin_auth.router, prefix="/auth", tags=["Admin Auth"])
admin_router.include_router(admin_users.router, tags=["Admin Users"])
api_router.include_router(admin_router)

# ── IIDX 서비스 (iidx 스키마) — /iidx 아래로 묶는다 ──
# 새 서비스가 붙으면 동일하게 APIRouter(prefix="/<svc>")를 만들어 취합하면 된다.
iidx_router = APIRouter(prefix="/iidx")
iidx_router.include_router(tables.router, prefix="/tables", tags=["IIDX"])
iidx_router.include_router(crawl.router, prefix="/crawl", tags=["IIDX Crawl"])
iidx_router.include_router(iidx_scores.router, prefix="/scores", tags=["IIDX Scores"])
# 등록된 곡/난이도표 조회(ADMIN) — IIDX 서비스 데이터라 /iidx 아래에 둔다
iidx_router.include_router(admin_catalog.router, prefix="/admin", tags=["IIDX Admin Catalog"])
api_router.include_router(iidx_router)
