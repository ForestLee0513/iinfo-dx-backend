"""인증 엔드포인트 — supabase-js 없이 백엔드가 Supabase Auth를 대행한다.

- OAuth (구글 등): GET /login/{provider} → Supabase authorize → GET /callback.
  PKCE code_verifier를 state 키로 Redis(TTL 5분)에 보관하고 state는 httpOnly
  쿠키로 왕복하므로 동시 로그인·다중 인스턴스에 안전하다.
  프로바이더 추가(애플 등)는 .env OAUTH_PROVIDERS에 이름만 추가
  (+ Supabase 대시보드에서 프로바이더 활성화·Redirect URL 등록).
  콜백 완료 시 AUTH_SUCCESS_REDIRECT_URL(또는 /login의 ?next=, 허용 오리진만)
  로 303 리다이렉트한다 — refresh 쿠키가 심어진 상태이므로 FE는 랜딩 후
  POST /refresh로 access token을 받는다. 미설정이면 세션 JSON 반환(개발용).
- 이메일: POST /signup(가입), POST /login(비밀번호 로그인)
- 세션: refresh token은 응답 본문에 넣지 않고 httpOnly 쿠키로만 발급한다 —
  FE(JS)는 access token만 메모리에 들고, 갱신은 POST /refresh(본문 없음,
  쿠키 자동 전송)로 처리한다. 별도 오리진 FE는 fetch에 credentials:"include"
  필요(CORS allow_credentials는 main.py에서 이미 켜져 있다).
"""

import json
import secrets
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from redis.exceptions import RedisError

from app.common.auth import CurrentUser, _extract_app_role, bearer_scheme
from app.common.openapi import PUBLIC
from app.common.redis import get_redis
from app.common.schemas import AuthUser
from app.core.config import settings
from app.web import auth_service

router = APIRouter()

_STATE_COOKIE = "oauth_state"
_PKCE_KEY = "auth:pkce:{state}"
_PKCE_TTL_SECONDS = 300

_REFRESH_COOKIE = "refresh_token"
# Supabase refresh token은 시간 만료가 아니라 rotation 기반 — 쿠키 수명만 제한한다
_REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30일


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


# ---------- 내부 헬퍼 ----------


def _to_auth_user(user: dict | None) -> AuthUser | None:
    """GoTrue user 객체 → AuthUser (app_metadata.role 기반 권한 포함)."""
    if not user or not user.get("id"):
        return None
    app_metadata = user.get("app_metadata") or {}
    return AuthUser(
        id=str(user["id"]),
        email=user.get("email"),
        provider=app_metadata.get("provider"),
        app_role=_extract_app_role(app_metadata),
    )


def _cookie_secure() -> bool:
    # SameSite=None은 Secure가 필수. 그 외에는 로컬 개발(http)만 예외.
    return (
        settings.AUTH_COOKIE_SAMESITE.lower() == "none"
        or settings.ENVIRONMENT != "local"
    )


def _refresh_cookie_path(request: Request) -> str:
    """refresh 쿠키를 인증 라우터(/…/auth) 경로에만 전송되도록 제한한다."""
    return str(request.url_for("refresh_session").path).rsplit("/", 1)[0]


def _set_refresh_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        _REFRESH_COOKIE,
        token,
        max_age=_REFRESH_COOKIE_MAX_AGE,
        path=_refresh_cookie_path(request),
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        secure=_cookie_secure(),
    )


def _delete_refresh_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(_REFRESH_COOKIE, path=_refresh_cookie_path(request))


def _issue_session(
    data: dict, request: Request, response: Response
) -> AuthSessionResponse:
    """GoTrue 토큰 응답 → refresh 쿠키 발급 + 본문(access token만) 구성."""
    user = _to_auth_user(data.get("user"))
    if not data.get("access_token") or not data.get("refresh_token") or user is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Supabase 세션 응답이 올바르지 않습니다",
        )
    _set_refresh_cookie(response, request, data["refresh_token"])
    return AuthSessionResponse(
        session=SessionData(
            access_token=data["access_token"],
            token_type=data.get("token_type", "bearer"),
            expires_in=data.get("expires_in"),
        ),
        user=user,
    )


def _allowed_redirect_origins() -> set[str]:
    """콜백 후 리다이렉트를 허용할 오리진 — 기본 FE URL + CORS 허용 목록."""
    origins = set(settings.CORS_ORIGINS)
    if settings.AUTH_SUCCESS_REDIRECT_URL:
        parts = urlsplit(settings.AUTH_SUCCESS_REDIRECT_URL)
        origins.add(f"{parts.scheme}://{parts.netloc}")
    return origins


def _validate_next_url(next_url: str) -> str:
    """open redirect 방지 — 허용 오리진의 http(s) URL만 통과시킨다."""
    parts = urlsplit(next_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if parts.scheme not in ("http", "https") or origin not in _allowed_redirect_origins():
        raise HTTPException(
            status_code=400, detail=f"허용되지 않은 next URL: {next_url}"
        )
    return next_url


def _callback_failure(detail: str, status_code: int = 400) -> Response:
    """콜백 실패 처리 — FE URL이 설정돼 있으면 ?error=로 돌려보내고, 아니면 HTTP 오류."""
    target = settings.AUTH_SUCCESS_REDIRECT_URL
    if target:
        separator = "&" if urlsplit(target).query else "?"
        return RedirectResponse(
            f"{target}{separator}{urlencode({'error': detail})}", status_code=303
        )
    raise HTTPException(status_code=status_code, detail=detail)


def _auth_error(e: auth_service.AuthServiceError, unauthorized: bool = False) -> HTTPException:
    """GoTrue 오류 → HTTP 응답. 5xx는 502로, 인증 실패 계열은 401로 변환."""
    if e.status_code >= 500:
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=e.message)
    if unauthorized and e.status_code in (400, 401, 403):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.message)
    return HTTPException(status_code=e.status_code, detail=e.message)


# ---------- OAuth (구글 등 — OAUTH_PROVIDERS로 확장) ----------


@router.get(
    "/login/{provider}",
    summary="OAuth 로그인 시작",
    response_class=RedirectResponse,
    openapi_extra=PUBLIC,
)
async def oauth_login(
    provider: str,
    request: Request,
    next_url: str | None = Query(
        None,
        alias="next",
        description="로그인 완료 후 돌려보낼 FE URL (허용 오리진만, 미지정 시 AUTH_SUCCESS_REDIRECT_URL)",
    ),
) -> RedirectResponse:
    """provider(google 등)의 OAuth 인증 페이지로 리다이렉트한다.

    PKCE verifier(+ next URL)를 Redis에 저장하고 조회 키(state)를
    httpOnly 쿠키로 내려준다.
    """
    if provider not in settings.OAUTH_PROVIDERS:
        raise HTTPException(
            status_code=404, detail=f"지원하지 않는 프로바이더: {provider}"
        )
    if next_url is not None:
        next_url = _validate_next_url(next_url)

    verifier, challenge = auth_service.generate_pkce()
    state = secrets.token_urlsafe(32)
    try:
        await get_redis().set(
            _PKCE_KEY.format(state=state),
            json.dumps({"verifier": verifier, "next": next_url}),
            ex=_PKCE_TTL_SECONDS,
        )
    except RedisError:
        raise HTTPException(
            status_code=503, detail="Redis를 사용할 수 없어 OAuth 로그인을 시작할 수 없습니다"
        )

    authorize_url = auth_service.build_authorize_url(
        provider, str(request.url_for("oauth_callback")), challenge
    )
    response = RedirectResponse(authorize_url)
    response.set_cookie(
        _STATE_COOKIE,
        state,
        max_age=_PKCE_TTL_SECONDS,
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        secure=_cookie_secure(),
    )
    return response


@router.get(
    "/callback",
    summary="OAuth 콜백 — 인가 코드를 세션으로 교환",
    response_model=AuthSessionResponse,
    openapi_extra=PUBLIC,
)
async def oauth_callback(
    request: Request,
    response: Response,
    code: str | None = Query(None, description="Supabase가 전달한 인가 코드"),
    error_description: str | None = Query(None),
):
    """OAuth 콜백. Redis의 PKCE verifier(1회용)로 인가 코드를 세션으로 교환한다.

    AUTH_SUCCESS_REDIRECT_URL(또는 /login의 ?next=)이 있으면 refresh 쿠키를
    심은 뒤 FE로 303 리다이렉트하고, 없으면 세션 JSON을 반환한다(개발용).
    """
    if code is None:
        return _callback_failure(error_description or "인가 코드가 없습니다")

    state = request.cookies.get(_STATE_COOKIE)
    if state is None:
        return _callback_failure(
            "로그인 상태 쿠키가 없습니다 — /login/{provider}부터 다시 시작하세요"
        )
    try:
        payload = await get_redis().getdel(_PKCE_KEY.format(state=state))
    except RedisError:
        return _callback_failure(
            "Redis를 사용할 수 없어 로그인을 완료할 수 없습니다", status_code=503
        )
    if payload is None:
        return _callback_failure("로그인 시도가 만료됐습니다 — 다시 시작하세요")
    stored = json.loads(payload)

    try:
        data = await auth_service.exchange_pkce(code, stored["verifier"])
    except auth_service.AuthServiceError as e:
        error = _auth_error(e)
        return _callback_failure(
            f"OAuth 코드 교환 실패: {error.detail}", status_code=error.status_code
        )
    if not data.get("refresh_token"):
        return _callback_failure("Supabase 세션 응답이 올바르지 않습니다", status_code=502)

    target = stored.get("next") or settings.AUTH_SUCCESS_REDIRECT_URL
    if target:
        redirect = RedirectResponse(target, status_code=303)
        redirect.delete_cookie(_STATE_COOKIE)
        _set_refresh_cookie(redirect, request, data["refresh_token"])
        return redirect

    response.delete_cookie(_STATE_COOKIE)
    return _issue_session(data, request, response)


# ---------- 이메일 로그인/가입 ----------


@router.post(
    "/signup",
    summary="이메일 회원가입",
    response_model=SignupResponse,
    openapi_extra=PUBLIC,
)
async def email_signup(body: EmailCredentials, request: Request, response: Response):
    """이메일/비밀번호 가입. 이메일 확인이 켜져 있으면 세션 없이 확인 메일만 발송된다."""
    try:
        data = await auth_service.sign_up(body.email, body.password)
    except auth_service.AuthServiceError as e:
        raise _auth_error(e)

    if data.get("access_token"):  # 자동 확인(autoconfirm) — 바로 로그인 상태
        session = _issue_session(data, request, response)
        return SignupResponse(
            email_confirmation_required=False,
            session=session.session,
            user=session.user,
        )
    # 이메일 확인 대기 — user 객체가 최상위 또는 user 키로 온다
    return SignupResponse(
        email_confirmation_required=True,
        user=_to_auth_user(data if data.get("id") else data.get("user")),
    )


@router.post(
    "/login",
    summary="이메일 로그인",
    response_model=AuthSessionResponse,
    openapi_extra=PUBLIC,
)
async def email_login(body: EmailCredentials, request: Request, response: Response):
    """이메일/비밀번호 로그인 — access token은 본문, refresh token은 쿠키로."""
    try:
        data = await auth_service.sign_in_with_password(body.email, body.password)
    except auth_service.AuthServiceError as e:
        raise _auth_error(e, unauthorized=True)
    return _issue_session(data, request, response)


# ---------- 세션 관리 ----------


@router.post(
    "/refresh",
    summary="세션 갱신 (쿠키 기반, 본문 불필요)",
    response_model=AuthSessionResponse,
    openapi_extra=PUBLIC,
)
async def refresh_session(request: Request, response: Response):
    """httpOnly 쿠키의 refresh token으로 새 세션을 발급한다.

    rotation — 새 refresh token이 쿠키로 재발급되고 이전 것은 폐기된다.
    FE는 본문 없이 credentials 포함 POST만 하면 된다.
    """
    token = request.cookies.get(_REFRESH_COOKIE)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh 쿠키가 없습니다 — 다시 로그인하세요",
        )
    try:
        data = await auth_service.refresh_session(token)
    except auth_service.AuthServiceError as e:
        error = _auth_error(e, unauthorized=True)
        # 무효한 refresh token은 쿠키를 지워 재로그인을 유도한다
        # (HTTPException을 raise하면 주입된 response의 쿠키 헤더가 반영되지
        #  않으므로 실패 응답을 직접 구성한다)
        failure = JSONResponse(
            status_code=error.status_code, content={"detail": error.detail}
        )
        _delete_refresh_cookie(failure, request)
        return failure
    return _issue_session(data, request, response)


@router.post(
    "/logout",
    summary="로그아웃 — refresh token 폐기 + 쿠키 제거",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=PUBLIC,
)
async def logout(
    request: Request,
    response: Response,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
):
    """세션을 종료한다 — 서버에서 refresh token을 폐기하고 쿠키를 지운다.

    access token(Bearer)이 있으면 그것으로 폐기하고, 없으면 쿠키의
    refresh token으로 세션을 복원한 뒤 폐기한다. 폐기 후에도 access token은
    남은 만료 시간까지는 유효하다(서명 검증 방식의 한계).
    """
    _delete_refresh_cookie(response, request)

    access_token = credentials.credentials if credentials else None
    cookie_token = request.cookies.get(_REFRESH_COOKIE)
    try:
        if access_token is None and cookie_token:
            data = await auth_service.refresh_session(cookie_token)
            access_token = data.get("access_token")
        if access_token:
            await auth_service.sign_out(access_token)
    except auth_service.AuthServiceError as e:
        if e.status_code >= 500:
            raise _auth_error(e)
        # 이미 만료/폐기된 토큰 — 로그아웃 목적은 달성된 상태


@router.get(
    "/me",
    summary="현재 로그인 사용자 조회",
    response_model=AuthUser,
    openapi_extra=PUBLIC,
)
def read_current_user(current_user: CurrentUser):
    """현재 로그인한 사용자 정보 조회 (인증 필수)."""
    return current_user
