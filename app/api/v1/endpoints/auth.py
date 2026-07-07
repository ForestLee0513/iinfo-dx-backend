"""인증 엔드포인트 — supabase-js 없이 백엔드가 Supabase Auth를 대행한다.

- OAuth (구글 등): GET /login/{provider} → Supabase authorize → GET /callback.
  PKCE code_verifier를 state 키로 Redis(TTL 5분)에 보관하고 state는 httpOnly
  쿠키로 왕복하므로 동시 로그인·다중 인스턴스에 안전하다.
  프로바이더 추가(애플 등)는 .env OAUTH_PROVIDERS에 이름만 추가
  (+ Supabase 대시보드에서 프로바이더 활성화·Redirect URL 등록).
  콜백 완료 시 FE가 /login에 넘긴 ?redirect=(OAUTH_ALLOWED_REDIRECT_URLS의 오리진만,
  아니면 홈)로 303 리다이렉트한다 — refresh 쿠키가 심어진 상태이므로 FE는
  랜딩 후 POST /refresh로 access token을 받는다.
- 이메일: POST /signup(가입), POST /login(비밀번호 로그인)
- 세션: refresh token은 응답 본문에 넣지 않고 httpOnly 쿠키로만 발급한다 —
  FE(JS)는 access token만 메모리에 들고, 갱신은 POST /refresh(본문 없음,
  쿠키 자동 전송)로 처리한다. 별도 오리진 FE는 fetch에 credentials:"include"
  필요(CORS allow_credentials는 main.py에서 이미 켜져 있다).

쿠키/세션/PKCE/리다이렉트 공통 로직은 auth_common에 있다. 이 라우터는 사용자
클라이언트(web)용이며 어드민 전용 로그인은 endpoints/admin_auth.py에 있다.
"""

import asyncio
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from redis.exceptions import RedisError

from app.api.deps import CurrentUser
from app.api.v1.endpoints import auth_common as ac
from app.core.security import bearer_scheme
from app.core.openapi import PUBLIC
from app.crud import crud_bans
from app.db.redis import get_redis
from app.schemas.user import AuthUser, UserRole
from app.core.config import settings
from app.services import auth_service

router = APIRouter()

# 사용자 클라이언트(web) 로그인 컨텍스트 — 최소 역할 USER, web 리다이렉트 정책
_CTX = ac.AuthContext(
    required_role=UserRole.USER,
    redirect_urls=settings.OAUTH_ALLOWED_REDIRECT_URLS,
    refresh_route_name="refresh_session",
    pkce_key="auth:pkce:{state}",
)


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
    redirect_url: str | None = Query(
        None,
        alias="redirect",
        description="로그인 완료 후 돌려보낼 FE URL (허용 오리진만, 미지정·불일치 시 홈으로 폴백)",
    ),
    prompt: str = "",
) -> RedirectResponse:
    """provider(google 등)의 OAuth 인증 페이지로 리다이렉트한다.

    PKCE verifier(+ redirect URL)를 Redis에 저장하고 조회 키(state)를
    httpOnly 쿠키로 내려준다.
    """
    redirect_url = ac.sanitize_redirect_url(redirect_url, _CTX)
    if provider not in settings.OAUTH_PROVIDERS:
        return ac.login_start_failure(
            f"지원하지 않는 프로바이더: {provider}", redirect_url, _CTX
        )

    verifier, challenge = auth_service.generate_pkce()
    state = secrets.token_urlsafe(32)
    try:
        await get_redis().set(
            _CTX.pkce_key.format(state=state),
            json.dumps({"verifier": verifier, "redirect": redirect_url}),
            ex=ac.PKCE_TTL_SECONDS,
        )
    except RedisError:
        return ac.login_start_failure(
            "Redis를 사용할 수 없어 OAuth 로그인을 시작할 수 없습니다", redirect_url, _CTX
        )

    authorize_url = auth_service.build_authorize_url(
        provider, str(request.url_for("oauth_callback")), challenge, prompt
    )
    response = RedirectResponse(authorize_url)
    ac.set_state_cookie(response, state)
    return response


@router.get(
    "/callback",
    summary="OAuth 콜백 — 인가 코드를 세션으로 교환",
    response_class=RedirectResponse,
    openapi_extra=PUBLIC,
)
async def oauth_callback(
    request: Request,
    code: str | None = Query(None, description="Supabase가 전달한 인가 코드"),
    error_description: str | None = Query(None),
):
    """OAuth 콜백. Redis의 PKCE verifier(1회용)로 인가 코드를 세션으로 교환한다.

    refresh 쿠키를 심은 뒤 /login의 ?redirect=(허용 오리진만, 아니면 홈)로
    303 리다이렉트한다. 실패 시에도 홈으로 ?error=를 붙여 돌려보낸다.
    """
    if code is None:
        return ac.callback_failure(error_description or "인가 코드가 없습니다", _CTX)

    state = request.cookies.get(ac.STATE_COOKIE)
    if state is None:
        return ac.callback_failure(
            "로그인 상태 쿠키가 없습니다 — /login/{provider}부터 다시 시작하세요", _CTX
        )
    try:
        payload = await get_redis().getdel(_CTX.pkce_key.format(state=state))
    except RedisError:
        return ac.callback_failure(
            "Redis를 사용할 수 없어 로그인을 완료할 수 없습니다", _CTX
        )
    if payload is None:
        return ac.callback_failure("로그인 시도가 만료됐습니다 — 다시 시작하세요", _CTX)
    stored = json.loads(payload)

    try:
        data = await auth_service.exchange_pkce(code, stored["verifier"])
    except auth_service.AuthServiceError as e:
        error = ac.auth_error(e)
        return ac.callback_failure(f"OAuth 코드 교환 실패: {error.detail}", _CTX)
    if not data.get("refresh_token"):
        return ac.callback_failure("Supabase 세션 응답이 올바르지 않습니다", _CTX)

    user = ac.to_auth_user(data.get("user"))
    if user:
        active_ban = await asyncio.to_thread(crud_bans.get_active_ban, user.id)
        if active_ban:
            if data.get("access_token"):
                try:
                    await auth_service.sign_out(data["access_token"])
                except auth_service.AuthServiceError:
                    pass
            return ac.callback_failure("접근이 제한된 계정입니다", _CTX)

    target = stored.get("redirect") or ac.redirect_home(_CTX)
    redirect = RedirectResponse(target, status_code=303)
    redirect.delete_cookie(ac.STATE_COOKIE)
    ac.set_refresh_cookie(redirect, request, data["refresh_token"], _CTX)
    return redirect


# ---------- 이메일 로그인/가입 ----------


@router.post(
    "/signup",
    summary="이메일 회원가입",
    response_model=ac.SignupResponse,
    openapi_extra=PUBLIC,
)
async def email_signup(
    body: ac.SignupBody, request: Request, response: Response
):
    """이메일/비밀번호 가입. 이메일 확인이 켜져 있으면 세션 없이 확인 메일만 발송된다."""
    try:
        data = await auth_service.sign_up(body.email, body.password)
    except auth_service.AuthServiceError as e:
        raise ac.auth_error(e)

    # is_public=False일 때만 Admin API로 override — 트리거 기본값(true)과 다를 경우에만
    if not body.is_public:
        raw_user = data if data.get("id") else data.get("user") or {}
        user_id = raw_user.get("id")
        if user_id:
            try:
                await auth_service.update_user_metadata(user_id, {"public": False})
            except auth_service.AuthServiceError:
                pass  # 메타데이터 설정 실패는 가입 자체를 막지 않는다

    if data.get("access_token"):  # 자동 확인(autoconfirm) — 바로 로그인 상태
        session = await ac.issue_session(data, request, response, _CTX)
        return ac.SignupResponse(
            email_confirmation_required=False,
            session=session.session,
            user=session.user,
        )
    # 이메일 확인 대기 — user 객체가 최상위 또는 user 키로 온다
    raw_user = data if data.get("id") else data.get("user")
    auth_user = ac.to_auth_user(raw_user)
    if auth_user and not body.is_public:
        auth_user = auth_user.model_copy(update={"is_public": False})
    return ac.SignupResponse(
        email_confirmation_required=True,
        user=auth_user,
    )


@router.post(
    "/login",
    summary="이메일 로그인",
    response_model=ac.AuthSessionResponse,
    openapi_extra=PUBLIC,
)
async def email_login(body: ac.EmailCredentials, request: Request, response: Response):
    """이메일/비밀번호 로그인 — access token은 본문, refresh token은 쿠키로."""
    try:
        data = await auth_service.sign_in_with_password(body.email, body.password)
    except auth_service.AuthServiceError as e:
        raise ac.auth_error(e, unauthorized=True)
    return await ac.issue_session(data, request, response, _CTX)


# ---------- 세션 관리 ----------


@router.post(
    "/refresh",
    summary="세션 갱신 (쿠키 기반, 본문 불필요)",
    response_model=ac.AuthSessionResponse,
    openapi_extra=PUBLIC,
)
async def refresh_session(request: Request, response: Response):
    """httpOnly 쿠키의 refresh token으로 새 세션을 발급한다.

    rotation — 새 refresh token이 쿠키로 재발급되고 이전 것은 폐기된다.
    FE는 본문 없이 credentials 포함 POST만 하면 된다.
    """
    token = request.cookies.get(ac.REFRESH_COOKIE)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh 쿠키가 없습니다 — 다시 로그인하세요",
        )
    try:
        data = await auth_service.refresh_session(token)
    except auth_service.AuthServiceError as e:
        error = ac.auth_error(e, unauthorized=True)
        # 무효한 refresh token은 쿠키를 지워 재로그인을 유도한다
        # (HTTPException을 raise하면 주입된 response의 쿠키 헤더가 반영되지
        #  않으므로 실패 응답을 직접 구성한다)
        failure = JSONResponse(
            status_code=error.status_code, content={"detail": error.detail}
        )
        ac.delete_refresh_cookie(failure, request, _CTX)
        return failure
    return await ac.issue_session(data, request, response, _CTX)


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
    ac.delete_refresh_cookie(response, request, _CTX)

    access_token = credentials.credentials if credentials else None
    cookie_token = request.cookies.get(ac.REFRESH_COOKIE)
    try:
        if access_token is None and cookie_token:
            data = await auth_service.refresh_session(cookie_token)
            access_token = data.get("access_token")
        if access_token:
            await auth_service.sign_out(access_token)
    except auth_service.AuthServiceError as e:
        if e.status_code >= 500:
            raise ac.auth_error(e)
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
