"""공통 의존성 — Supabase 액세스 토큰 검증.

프론트엔드가 Supabase로 로그인(구글 OAuth / 이메일)한 뒤 받은
access_token을 `Authorization: Bearer <token>` 헤더로 보내면,
백엔드는 토큰 서명·만료·발급자를 검증하고 사용자 정보를 추출한다.

get_current_user를 엔드포인트 파라미터 또는 라우터의 dependencies에
추가하면 해당 API는 인증 필수가 된다.
"""

from functools import lru_cache
from typing import Annotated, Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.common.schemas import AuthUser, UserRole

bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
    )


def _decode_token(token: str) -> dict:
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
        )
    # 권장 방식: JWKS 공개 키(RS256/ES256) 검증
    signing_key = _jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience="authenticated",
        issuer=issuer,
    )


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
) -> AuthUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = _decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    app_metadata = payload.get("app_metadata") or {}
    return AuthUser(
        id=payload["sub"],
        email=payload.get("email"),
        provider=app_metadata.get("provider"),
        role=payload.get("role"),
        app_role=_extract_app_role(app_metadata),
    )


def _extract_app_role(app_metadata: dict) -> UserRole:
    """app_metadata.role → UserRole. 미설정/알 수 없는 값은 USER로 강등(기본 최소 권한)."""
    raw = str(app_metadata.get("role") or "").strip().upper()
    try:
        return UserRole(raw)
    except ValueError:
        return UserRole.USER


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


def require_role(required: UserRole) -> Callable[..., AuthUser]:
    """역할 검사 의존성 팩토리 — 요구 역할 '이상'(ROLE_LEVELS 서열)만 통과시킨다.

    사용: user: Annotated[AuthUser, Depends(require_role(UserRole.ADMIN))]
    역할이 늘어나면 UserRole/ROLE_LEVELS에만 추가하면 기존 검사가 그대로 동작한다.
    """

    def checker(user: CurrentUser) -> AuthUser:
        if not user.has_role(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{required.value} 권한이 필요합니다.",
            )
        return user

    return checker
