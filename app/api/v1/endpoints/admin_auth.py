"""어드민 전용 인증 엔드포인트 (/admin/auth/*) — 별도 어드민 FE 로그인.

사용자 클라이언트(web/auth)와 동일한 세션/쿠키/PKCE 규약을 쓰되,
**로그인 성공에 ADMIN 역할을 요구**한다(Supabase app_metadata.role == "ADMIN").
ADMIN 미달 계정은 자격 증명이 맞아도 세션을 발급하지 않는다.

- OAuth: GET /login/{provider} → Supabase authorize → GET /callback.
  콜백에서 세션 교환 후 ADMIN 역할을 확인하고, 미달이면 refresh 쿠키를
  심지 않고 어드민 홈으로 ?error=를 붙여 돌려보낸다. 허용 리다이렉트는
  ADMIN_ALLOWED_REDIRECT_URLS(사용자 클라이언트와 분리).
- 이메일: POST /login (비밀번호 로그인). 어드민은 셀프 가입이 없으므로
  /signup은 두지 않는다(계정 생성/권한 부여는 Supabase 콘솔에서).
- 세션: refresh token은 httpOnly 쿠키(경로 /admin/auth 스코프)로만 발급.
  갱신은 POST /refresh(본문 없음, credentials 포함).

PKCE verifier는 web과 별도 Redis 네임스페이스(auth:pkce:admin:*)에 저장해
web 플로우의 state로 어드민 콜백을 통과할 수 없도록 격리한다.
어드민 API는 문서상 internal 전용이므로 PUBLIC 데코레이터를 붙이지 않는다.
"""

import asyncio
import json
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from redis.exceptions import RedisError

from app.api.deps import AdminUser
from app.api.v1.endpoints import auth_common as ac
from app.core.security import bearer_scheme
from app.crud import crud_bans, crud_profiles
from app.db.redis import get_redis
from app.schemas.user import AuthUser, UserRole
from app.core.config import settings
from app.services import auth_service

router = APIRouter()

# 어드민 로그인 컨텍스트 — 최소 역할 ADMIN, 어드민 리다이렉트 정책, 별도 PKCE 네임스페이스
_CTX = ac.AuthContext(
    required_role=UserRole.ADMIN,
    redirect_urls=settings.ADMIN_ALLOWED_REDIRECT_URLS,
    refresh_route_name="admin_refresh_session",
    pkce_key="auth:pkce:admin:{state}",
)


# ---------- OAuth (구글 등 — OAUTH_PROVIDERS로 확장) ----------


@router.get(
    "/login/{provider}",
    summary="[어드민] OAuth 로그인 시작",
    response_class=RedirectResponse,
)
async def admin_oauth_login(
    provider: str,
    request: Request,
    redirect_url: str | None = Query(
        None,
        alias="redirect",
        description="로그인 완료 후 돌려보낼 어드민 FE URL (허용 오리진만, 미지정·불일치 시 홈으로 폴백)",
    ),
    prompt: str = "",
) -> RedirectResponse:
    """provider(google 등)의 OAuth 인증 페이지로 리다이렉트한다.

    web과 동일한 PKCE 흐름이나, 콜백에서 ADMIN 역할을 확인한다.
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
        provider, str(request.url_for("admin_oauth_callback")), challenge, prompt
    )
    response = RedirectResponse(authorize_url)
    ac.set_state_cookie(response, state)
    return response


@router.get(
    "/callback",
    summary="[어드민] OAuth 콜백 — 인가 코드를 세션으로 교환",
    response_class=RedirectResponse,
)
async def admin_oauth_callback(
    request: Request,
    code: str | None = Query(None, description="Supabase가 전달한 인가 코드"),
    error_description: str | None = Query(None),
):
    """OAuth 콜백. PKCE verifier(1회용)로 세션 교환 후 ADMIN 역할을 확인한다.

    ADMIN이면 refresh 쿠키를 심고 어드민 FE로 303 리다이렉트하며,
    미달·실패 시 refresh 쿠키 없이 홈으로 ?error=를 붙여 돌려보낸다.
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

    # 세션 응답 검증 + ADMIN 역할 게이트 — 미달이면 쿠키를 심지 않는다
    user = ac.to_auth_user(data.get("user"))
    if not data.get("refresh_token") or user is None:
        return ac.callback_failure("Supabase 세션 응답이 올바르지 않습니다", _CTX)

    async def _revoke():
        if data.get("access_token"):
            try:
                await auth_service.sign_out(data["access_token"])
            except auth_service.AuthServiceError:
                pass

    # user_profiles에서 role·ban을 병렬 조회
    profile, active_ban = await asyncio.gather(
        asyncio.to_thread(crud_profiles.get_profile, user.id),
        asyncio.to_thread(crud_bans.get_active_ban, user.id),
    )
    user = user.model_copy(update={"app_role": profile.role})

    if not user.has_role(UserRole.ADMIN):
        # role이 없는 경우: 신규 가입 감지 — created_at ≈ last_sign_in_at이면
        # 이번 OAuth로 처음 생성된 계정이므로 계정까지 삭제해 흔적을 남기지 않는다
        user_data = data.get("user") or {}
        try:
            ca = datetime.fromisoformat(user_data["created_at"].replace("Z", "+00:00"))
            lsa = datetime.fromisoformat(user_data["last_sign_in_at"].replace("Z", "+00:00"))
            if abs((lsa - ca).total_seconds()) < 5:
                await _revoke()
                try:
                    await auth_service.delete_user(user.id)
                except auth_service.AuthServiceError:
                    pass
                return ac.callback_failure("등록된 계정이 아닙니다. 어드민 계정으로 로그인하세요", _CTX)
        except (KeyError, ValueError, TypeError):
            pass
        await _revoke()
        return ac.callback_failure("관리자 권한이 필요합니다", _CTX)

    if active_ban:
        await _revoke()
        return ac.callback_failure("접근이 제한된 계정입니다", _CTX)

    target = stored.get("redirect") or ac.redirect_home(_CTX)
    redirect = RedirectResponse(target, status_code=303)
    redirect.delete_cookie(ac.STATE_COOKIE)
    ac.set_refresh_cookie(redirect, request, data["refresh_token"], _CTX)
    return redirect


# ---------- 이메일 로그인 ----------


@router.post(
    "/login",
    summary="[어드민] 이메일 로그인",
    response_model=ac.AuthSessionResponse,
)
async def admin_email_login(
    body: ac.EmailCredentials, request: Request, response: Response
):
    """이메일/비밀번호 로그인 — ADMIN 역할 미달이면 403.

    access token은 본문, refresh token은 쿠키(/admin/auth 스코프)로.
    """
    try:
        data = await auth_service.sign_in_with_password(body.email, body.password)
    except auth_service.AuthServiceError as e:
        raise ac.auth_error(e, unauthorized=True)
    # issue_session이 ctx.required_role(ADMIN) 미달을 403으로 막는다
    return await ac.issue_session(data, request, response, _CTX)


# ---------- 세션 관리 ----------


@router.post(
    "/refresh",
    summary="[어드민] 세션 갱신 (쿠키 기반, 본문 불필요)",
    response_model=ac.AuthSessionResponse,
)
async def admin_refresh_session(request: Request, response: Response):
    """httpOnly 쿠키의 refresh token으로 새 세션을 발급한다(rotation).

    갱신 시에도 ADMIN 역할을 재확인해, 권한이 회수된 계정은 여기서 막힌다.
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
        failure = JSONResponse(
            status_code=error.status_code, content={"detail": error.detail}
        )
        ac.delete_refresh_cookie(failure, request, _CTX)
        return failure
    return await ac.issue_session(data, request, response, _CTX)


@router.post(
    "/logout",
    summary="[어드민] 로그아웃 — refresh token 폐기 + 쿠키 제거",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def admin_logout(
    request: Request,
    response: Response,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
):
    """세션을 종료한다 — 서버에서 refresh token을 폐기하고 쿠키를 지운다."""
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
    summary="[어드민] 현재 로그인 어드민 조회",
    response_model=AuthUser,
)
def admin_read_current_user(current_user: AdminUser):
    """현재 로그인한 어드민 정보 조회 (ADMIN 인증 필수)."""
    return current_user
