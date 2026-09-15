"""인증 공통 로직 — 사용자(web)/어드민(admin) 로그인 라우터가 공유한다.

쿠키·세션 발급·PKCE·리다이렉트 검증처럼 플로우와 무관한 헬퍼를 여기 모으고,
플로우별로 다른 부분(요구 역할, 허용 리다이렉트 목록, refresh 쿠키 경로 스코프,
PKCE Redis 키 네임스페이스)은 AuthContext로 주입한다.

이렇게 하면 web(사용자)과 admin(어드민)이 같은 세션/쿠키 규약을 공유하면서도
각자의 리다이렉트 정책과 역할 게이트를 갖는다.
"""

import asyncio
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.core.config import settings
from app.crud.account import bans as crud_bans, profiles as crud_profiles
from app.schemas.account.user import AuthUser, UserRole
from app.services.account import auth_service

# ---------- 쿠키/PKCE 공통 상수 ----------

STATE_COOKIE = "oauth_state"
PKCE_TTL_SECONDS = 300

REFRESH_COOKIE = "refresh_token"
# Supabase refresh token은 시간 만료가 아니라 rotation 기반 — 쿠키 수명만 제한한다
REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30일


# ---------- 플로우 컨텍스트 ----------


@dataclass(frozen=True)
class AuthContext:
    """로그인 플로우(web/admin)마다 다른 정책을 담는다.

    required_role     : 로그인 성공에 필요한 최소 역할 (web=USER, admin=ADMIN)
    redirect_urls     : OAuth 완료 후 허용할 FE URL 목록 (오리진 화이트리스트)
    refresh_route_name: refresh 엔드포인트 이름 — refresh 쿠키 경로 스코프 계산에 사용
    pkce_key          : PKCE verifier용 Redis 키 템플릿 ({state} 포함, 플로우별 분리)
    """

    required_role: UserRole
    redirect_urls: list[str]
    refresh_route_name: str
    pkce_key: str


# ---------- 요청/응답 스키마 ----------


class SessionData(BaseModel):
    """Supabase 세션 — refresh token은 본문이 아니라 httpOnly 쿠키로 전달된다."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int | None = None


class AuthSessionResponse(BaseModel):
    """로그인/토큰 갱신 성공 응답."""

    session: SessionData
    user: AuthUser


class SignupResponse(BaseModel):
    """회원가입 응답 — Supabase 이메일 확인이 켜져 있으면 session이 없다."""

    email_confirmation_required: bool
    session: SessionData | None = None
    user: AuthUser | None = None


class EmailCredentials(BaseModel):
    email: str
    password: str


class SignupBody(BaseModel):
    email: str
    password: str
    is_public: bool = True


# ---------- 사용자 변환/권한 ----------


def to_auth_user(user: dict | None) -> AuthUser | None:
    """GoTrue user 객체 → AuthUser (app_metadata.role 기반 권한 포함)."""
    if not user or not user.get("id"):
        return None
    app_metadata = user.get("app_metadata") or {}
    return AuthUser(
        id=str(user["id"]),
        email=user.get("email"),
        provider=app_metadata.get("provider"),
    )


# ---------- 쿠키 ----------


def _cookie_secure() -> bool:
    # SameSite=None은 Secure가 필수. 그 외에는 로컬 개발(http)만 예외.
    return (
        settings.AUTH_COOKIE_SAMESITE.lower() == "none"
        or settings.ENVIRONMENT != "local"
    )


def _refresh_cookie_path(request: Request, ctx: AuthContext) -> str:
    """refresh 쿠키를 해당 인증 라우터(/…/auth) 경로에만 전송되도록 제한한다."""
    return str(request.url_for(ctx.refresh_route_name).path).rsplit("/", 1)[0]


def set_refresh_cookie(
    response: Response, request: Request, token: str, ctx: AuthContext
) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=REFRESH_COOKIE_MAX_AGE,
        path=_refresh_cookie_path(request, ctx),
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        secure=_cookie_secure(),
    )


def delete_refresh_cookie(
    response: Response, request: Request, ctx: AuthContext
) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=_refresh_cookie_path(request, ctx))


def require_cookie_request_origin(request: Request) -> None:
    """쿠키 인증으로 상태를 바꾸는 요청의 CSRF를 차단한다.

    허용된 FE 오리진(CORS·OAuth 리다이렉트 목록)에서 온 브라우저 POST만 허용한다.
    Bearer 인증 경로는 호출자가 토큰을 명시적으로 보내므로 이 검사가 필요 없다.
    """
    origin = request.headers.get("origin")
    allowed: set[str] = set()
    for configured_url in (
        *settings.CORS_ORIGINS,
        *settings.OAUTH_ALLOWED_REDIRECT_URLS,
        *settings.ADMIN_ALLOWED_REDIRECT_URLS,
    ):
        try:
            parts = urlsplit(configured_url)
        except ValueError:
            continue
        if parts.scheme in ("http", "https") and parts.netloc:
            allowed.add(f"{parts.scheme}://{parts.netloc}")
    if origin is None or origin not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="허용되지 않은 요청 출처입니다.")


def set_state_cookie(response: Response, state: str) -> None:
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=PKCE_TTL_SECONDS,
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        secure=_cookie_secure(),
    )


# ---------- 세션 발급 ----------


async def issue_session(
    data: dict, request: Request, response: Response, ctx: AuthContext
) -> AuthSessionResponse:
    """GoTrue 토큰 응답 → refresh 쿠키 발급 + 본문(access token만) 구성.

    접근 제한 계정이거나 ctx.required_role 미달 사용자는 403으로 막는다.
    """
    user = to_auth_user(data.get("user"))
    if not data.get("access_token") or not data.get("refresh_token") or user is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Supabase 세션 응답이 올바르지 않습니다",
        )
    active_ban, profile = await asyncio.gather(
        asyncio.to_thread(crud_bans.get_active_ban, user.id),
        asyncio.to_thread(crud_profiles.get_profile, user.id),
    )
    user = user.model_copy(update={"app_role": profile.role, "is_public": profile.is_public})
    if active_ban:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="접근이 제한된 계정입니다.",
        )
    if not user.has_role(ctx.required_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{ctx.required_role.value} 권한이 필요합니다.",
        )
    set_refresh_cookie(response, request, data["refresh_token"], ctx)
    return AuthSessionResponse(
        session=SessionData(
            access_token=data["access_token"],
            token_type=data.get("token_type", "bearer"),
            expires_in=data.get("expires_in"),
        ),
        user=user,
    )


# ---------- 리다이렉트 검증 (OAuth) ----------


def redirect_home(ctx: AuthContext) -> str:
    """로그인 후 돌아갈 기본 FE 홈 — 허용 목록의 첫 URL."""
    urls = ctx.redirect_urls
    return urls[0] if urls else "http://localhost:3000"


def _allowed_redirect_origins(ctx: AuthContext) -> set[str]:
    """콜백 후 리다이렉트를 허용할 오리진 — ctx.redirect_urls 기준."""
    origins = set()
    for url in ctx.redirect_urls:
        parts = urlsplit(url)
        origins.add(f"{parts.scheme}://{parts.netloc}")
    return origins


def sanitize_redirect_url(redirect_url: str | None, ctx: AuthContext) -> str:
    """FE가 넘긴 redirect URL을 검증한다 — 허용 오리진의 http(s) URL만 통과시키고,
    미지정·불일치·비표준 스킴 URL은 에러 대신 홈으로 폴백한다 (open redirect 방지).

    브라우저(WHATWG URL 파서)는 http/https 같은 "special scheme"에서 백슬래시(\)를
    슬래시(/)와 동일하게 취급해 "https:\evil.com", "https:/\evil.com" 같은 입력도
    "https://evil.com"으로 정규화해 이동한다. 반면 RFC 3986 기반인 urlsplit은
    백슬래시를 일반 문자로 남겨두므로, 서버와 브라우저가 같은 문자열을 서로 다른
    호스트로 해석할 여지(parser differential)가 생긴다. 지금은 우연히 이런 값이
    urlsplit에서도 netloc이 비어 화이트리스트에 걸리지 않지만, 파서 구현에 기대지
    않고 백슬래시가 섞인 값은 애초에 통째로 거부한다.

    urlsplit은 NFKC 정규화 시 '/', '@' 등으로 풀리는 유니코드 동형이의 문자
    (예: U+2100 "℀" → "a/c")가 netloc에 섞이면 ValueError를 던진다 — 검증 실패가
    아니라 예외이므로 그대로 두면 이 함수가 500을 내며 죽는다. 그런 입력도 결국
    허용 목록에 없는 값일 뿐이므로 홈 폴백으로 흡수한다.
    """
    if redirect_url and "\\" not in redirect_url:
        try:
            parts = urlsplit(redirect_url)
        except ValueError:
            return redirect_home(ctx)
        origin = f"{parts.scheme}://{parts.netloc}"
        if parts.scheme in ("http", "https") and origin in _allowed_redirect_origins(ctx):
            return redirect_url
    return redirect_home(ctx)


def _redirect_with_error(target: str, detail: str) -> RedirectResponse:
    """target에 ?error=를 붙여 303 리다이렉트한다 (기존 쿼리스트링 보존)."""
    separator = "&" if urlsplit(target).query else "?"
    return RedirectResponse(
        f"{target}{separator}{urlencode({'error': detail})}", status_code=303
    )


def callback_failure(detail: str, ctx: AuthContext) -> RedirectResponse:
    """콜백 실패 처리 — 홈으로 ?error=를 붙여 돌려보낸다."""
    return _redirect_with_error(redirect_home(ctx), detail)


def login_start_failure(
    detail: str, redirect_url: str, ctx: AuthContext
) -> RedirectResponse:
    """로그인 시작 실패 처리 — 검증된 redirect(없으면 홈)로 ?error=를 붙여 돌려보낸다.

    브라우저가 /login/{provider}로 전체 페이지 이동하므로, 시작 단계에서
    실패해도 JSON을 노출하지 않고 FE로 되돌려 ?error=를 전달한다.
    """
    return _redirect_with_error(redirect_url or redirect_home(ctx), detail)


# ---------- GoTrue 오류 변환 ----------


def auth_error(
    e: auth_service.AuthServiceError, unauthorized: bool = False
) -> HTTPException:
    """GoTrue 오류 → HTTP 응답. 5xx는 502로, 인증 실패 계열은 401로 변환."""
    if e.status_code >= 500:
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=e.message)
    if unauthorized and e.status_code in (400, 401, 403):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.message)
    return HTTPException(status_code=e.status_code, detail=e.message)
