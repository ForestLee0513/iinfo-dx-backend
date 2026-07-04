"""API 공통 의존성 — 인증/권한.

get_current_user를 엔드포인트 파라미터(또는 라우터의 dependencies)에 추가하면
해당 API는 인증 필수가 된다. 역할 게이트는 require_role(UserRole.X)로 건다.
어드민 전용 엔드포인트는 AdminUser를 파라미터로 받으면 된다.

역할 체계가 확장되면 UserRole/ROLE_LEVELS(app/schemas/user.py)에만 추가하면
기존 검사가 그대로 동작한다. 권한 부여 자체는 Supabase 계정의
app_metadata.role로 처리한다(README '어드민 권한 부여' 참고).
"""

from typing import Annotated, Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.security import bearer_scheme, decode_token, extract_app_role
from app.schemas.user import AuthUser, UserRole


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
        payload = decode_token(credentials.credentials)
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
        app_role=extract_app_role(app_metadata),
    )


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


# 어드민 전용 — Supabase 계정의 app_metadata.role == "ADMIN" 이상만 통과
get_admin_user = require_role(UserRole.ADMIN)
AdminUser = Annotated[AuthUser, Depends(get_admin_user)]
