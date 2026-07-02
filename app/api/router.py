from fastapi import APIRouter

from app.api.endpoints import auth, health

api_router = APIRouter()

# ── 공개 API (인증 불필요) ──────────────────────────────
api_router.include_router(health.router, prefix="/health", tags=["Health"])

# ── 인증 필수 API ──────────────────────────────────────
# /auth/me는 엔드포인트 파라미터(CurrentUser)로 인증을 요구한다.
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])

# 라우터 전체를 인증 필수로 등록하려면 dependencies를 추가한다:
#
# from fastapi import Depends
# from app.api.deps import get_current_user
#
# api_router.include_router(
#     example.router,
#     prefix="/example",
#     tags=["Example"],
#     dependencies=[Depends(get_current_user)],
# )
