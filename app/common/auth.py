"""공통 의존성 — Supabase 액세스 토큰 검증.

프론트엔드가 Supabase로 로그인(구글 OAuth / 이메일)한 뒤 받은
access_token을 `Authorization: Bearer <token>` 헤더로 보내면,
백엔드는 토큰 서명·만료·발급자를 검증하고 사용자 정보를 추출한다.

get_current_user를 엔드포인트 파라미터 또는 라우터의 dependencies에
추가하면 해당 API는 인증 필수가 된다.
"""

from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.common.schemas import AuthUser

bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
    )


def _decode_token(token: str) -> dict:
    issuer = f"{settings.SUPABASE_URL}/auth/v1"
    if settings.SUPABASE_JWT_SECRET:
        # 레거시 프로젝트: 대칭 키(HS256) 검증
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

    return AuthUser(
        id=payload["sub"],
        email=payload.get("email"),
        provider=(payload.get("app_metadata") or {}).get("provider"),
        role=payload.get("role"),
    )


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
