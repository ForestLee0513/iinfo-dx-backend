"""인증 관련 엔드포인트.

로그인/회원가입은 프론트엔드가 Supabase와 직접 처리한다
(supabase-js의 signInWithOAuth("google"), signInWithPassword 등).
백엔드는 토큰 검증 결과를 확인하는 엔드포인트만 제공한다.
"""

from fastapi import APIRouter

from app.common.auth import CurrentUser
from app.common.openapi import PUBLIC
from app.common.schemas import AuthUser

router = APIRouter()


@router.get("/me", response_model=AuthUser, openapi_extra=PUBLIC)
def read_current_user(current_user: CurrentUser):
    """현재 로그인한 사용자 정보 조회 (인증 필수)."""
    return current_user
