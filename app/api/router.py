from fastapi import APIRouter

from app.api.endpoints import auth, crawl, health, tables

api_router = APIRouter()

# ── 공개 API (인증 불필요) ──────────────────────────────
api_router.include_router(health.router, prefix="/health", tags=["Health"])

# 난이도표 조회는 공개. 데이터는 주간 크롤링 스케줄러로만 갱신된다.
api_router.include_router(tables.router, prefix="/tables", tags=["Tables"])

# 크롤링 진단(targets/preview)도 공개. 쓰기 경로가 없어 조회/미리보기 전용이다.
api_router.include_router(crawl.router, prefix="/crawl", tags=["Crawl"])

# ── 인증 필수 API ──────────────────────────────────────
# /auth/me는 엔드포인트 파라미터(CurrentUser)로 인증을 요구한다.
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
