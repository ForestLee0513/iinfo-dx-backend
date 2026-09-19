"""API 공통 의존성 — 인증/권한.

get_current_user를 엔드포인트 파라미터(또는 라우터의 dependencies)에 추가하면
해당 API는 인증 필수가 된다. 역할 게이트는 require_role(UserRole.X)로 건다.
어드민 전용 엔드포인트는 AdminUser를 파라미터로 받으면 된다.

역할 체계가 확장되면 UserRole/ROLE_LEVELS(app/schemas/user.py)에만 추가하면
기존 검사가 그대로 동작한다. 권한 부여 자체는 Supabase 계정의
app_metadata.role로 처리한다(README '어드민 권한 부여' 참고).
"""

import asyncio
from dataclasses import dataclass
from typing import Annotated, Callable

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.security import bearer_scheme, decode_token
from app.crud.account import bans as crud_bans, profiles as crud_profiles
from app.schemas.account.user import AuthUser, UserRole


async def get_current_user(
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
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    app_metadata = payload.get("app_metadata") or {}
    user_id = payload["sub"]

    active_ban, profile = await asyncio.gather(
        asyncio.to_thread(crud_bans.get_active_ban, user_id),
        asyncio.to_thread(crud_profiles.get_profile, user_id),
    )
    if active_ban:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="접근이 제한된 계정입니다.",
        )
    return AuthUser(
        id=user_id,
        email=payload.get("email"),
        provider=app_metadata.get("provider"),
        app_role=profile.role,
        is_public=profile.is_public,
    )


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


@dataclass
class TokenIdentity:
    """토큰 클레임만으로 구성한 최소 신원 정보 — DB 조회(프로필/밴)는 하지 않는다."""

    id: str
    email: str | None
    provider: str | None


async def get_optional_identity(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
) -> TokenIdentity | None:
    """공개 엔드포인트에서 '요청자 본인인지'만 곁들이고 싶을 때 쓰는 옵셔널 인증.

    Authorization 헤더가 없거나, 있어도 검증에 실패(만료·위조 등)하면 인증
    실패로 막지 않고 익명(None)으로 취급한다 — 이 의존성을 쓰는 엔드포인트는
    토큰 없이도 정상 동작해야 하는 공개 API이기 때문이다. get_current_user와
    달리 프로필/밴 조회를 하지 않아 익명 트래픽에 추가 DB 왕복이 없다.
    """
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        return None
    app_metadata = payload.get("app_metadata") or {}
    return TokenIdentity(
        id=payload["sub"],
        email=payload.get("email"),
        provider=app_metadata.get("provider"),
    )


OptionalIdentity = Annotated[TokenIdentity | None, Depends(get_optional_identity)]


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


# 어드민 전용 — Supabase 계정의 app_metadata.role == "ADMIN" 이상만 통과
get_admin_user = require_role(UserRole.ADMIN)
AdminUser = Annotated[AuthUser, Depends(get_admin_user)]

# 슈퍼어드민 전용 — 역할 부여 등 최상위 권한 작업 (개발자 본인 1명)
get_super_admin_user = require_role(UserRole.SUPER_ADMIN)
SuperAdminUser = Annotated[AuthUser, Depends(get_super_admin_user)]


async def get_upload_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    x_upload_token: Annotated[str | None, Header()] = None,
) -> AuthUser:
    """JWT(Authorization: Bearer) 또는 업로드 토큰(X-Upload-Token) 중 하나로 인증.

    북마크릿 업로드 엔드포인트 전용. 일반 Bearer JWT가 있으면 그걸 우선 사용하고,
    없으면 X-Upload-Token 헤더로 Redis에서 user_id를 조회한다.
    """
    if credentials is not None:
        return await get_current_user(credentials)

    if x_upload_token is not None:
        from app.services.iidx.scores.upload_token import get_user_id

        user_id = await get_user_id(x_upload_token)
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="만료되었거나 유효하지 않은 업로드 토큰입니다.",
            )
        active_ban, profile = await asyncio.gather(
            asyncio.to_thread(crud_bans.get_active_ban, user_id),
            asyncio.to_thread(crud_profiles.get_profile, user_id),
        )
        if active_ban:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="접근이 제한된 계정입니다.",
            )
        return AuthUser(
            id=user_id,
            email=None,
            provider=None,
            app_role=profile.role,
            is_public=profile.is_public,
            upload_token=x_upload_token,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


# 북마크릿 업로드 전용 — JWT 또는 X-Upload-Token 헤더 중 하나 필수
UploadUser = Annotated[AuthUser, Depends(get_upload_user)]
