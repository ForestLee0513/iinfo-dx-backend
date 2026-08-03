"""보안 프리미티브 — Supabase 액세스 토큰 검증.

프론트엔드가 Supabase로 로그인(구글 OAuth / 이메일)한 뒤 받은
access_token을 `Authorization: Bearer <token>` 헤더로 보내면,
백엔드는 토큰 서명·만료·발급자를 검증하고 사용자 정보를 추출한다.

여기에는 토큰 디코딩/역할 추출 같은 저수준 유틸만 둔다.
FastAPI 의존성(get_current_user, require_role 등)은 app/api/deps.py에 있다.
"""

from functools import lru_cache

import jwt
from fastapi.security import HTTPBearer

from app.core.config import settings
from app.schemas.account.user import UserRole

bearer_scheme = HTTPBearer(auto_error=False)

# 클럭 스큐(서버·컨테이너 시계 오차) 허용치(초). Docker Desktop 등에서 슬립/재개
# 후 컨테이너 시계가 살짝 어긋나면 방금 발급된 토큰의 iat/nbf가 "미래"로 보여
# 검증이 간헐적으로 실패한다("The token is not yet valid"). 몇 초 여유를 둬
# 유효한 토큰이 시계 오차로 거절되지 않게 한다(만료 판정에도 동일 여유 적용).
_CLOCK_SKEW_LEEWAY = 30


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
    )


def decode_token(token: str) -> dict:
    """토큰 헤더의 alg에 따라 검증 경로를 선택한다.

    SUPABASE_JWT_SECRET이 설정돼 있어도 비대칭(ES256/RS256) 토큰은 JWKS로
    검증한다 — 새 JWT Signing Keys로 마이그레이션한 프로젝트에서 secret이
    남아 있으면 모든 검증이 실패하던 문제 방지 (마이그레이션 과도기에
    HS256/ES256 토큰이 섞여 들어와도 각자 맞는 경로로 검증된다).
    """
    issuer = f"{settings.SUPABASE_URL}/auth/v1"
    alg = jwt.get_unverified_header(token).get("alg")
    if alg == "HS256":
        # 레거시 프로젝트: 대칭 키(HS256) 검증
        if not settings.SUPABASE_JWT_SECRET:
            raise jwt.InvalidTokenError(
                "HS256 토큰이지만 SUPABASE_JWT_SECRET이 설정되지 않았습니다"
            )
        return jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            issuer=issuer,
            leeway=_CLOCK_SKEW_LEEWAY,
        )
    # 권장 방식: JWKS 공개 키(RS256/ES256) 검증
    signing_key = _jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience="authenticated",
        issuer=issuer,
        leeway=_CLOCK_SKEW_LEEWAY,
    )


def extract_app_role(app_metadata: dict) -> UserRole:
    """app_metadata.role → UserRole. 미설정/알 수 없는 값은 USER로 강등(기본 최소 권한)."""
    raw = str(app_metadata.get("role") or "").strip().upper()
    try:
        return UserRole(raw)
    except ValueError:
        return UserRole.USER
