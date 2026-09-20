"""Supabase Auth(GoTrue) REST 게이트웨이 — supabase-js 없이 백엔드가 직접 처리.

로그인/가입/토큰 갱신/로그아웃을 GoTrue HTTP API 호출로 수행한다.
supabase-py 클라이언트는 세션·PKCE verifier를 프로세스 내부 저장소에 보관해
다중 사용자 서버에 부적합하므로, 이 모듈은 상태 없는 REST 호출만 사용한다.

OAuth는 PKCE code flow다: authorize URL 생성(code_challenge 포함) →
콜백에서 auth_code + code_verifier를 세션으로 교환. code_verifier의 보관은
호출자(엔드포인트)가 Redis로 처리한다 — 이 모듈은 상태를 갖지 않는다.
"""

import base64
import hashlib
import inspect
import logging
import secrets
from typing import Awaitable, Callable
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class AuthServiceError(Exception):
    """GoTrue 오류 응답 (HTTP status + 메시지)."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


def generate_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) 쌍 생성 — S256 방식."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorize_url(provider: str, redirect_to: str, code_challenge: str, prompt: str = "") -> str:
    """GoTrue authorize URL — 브라우저를 여기로 보내면 provider 로그인이 시작된다.

    provider는 파라미터일 뿐이라 애플 등 추가 시 이 코드는 그대로다
    (허용 목록은 settings.OAUTH_PROVIDERS, 실제 활성화는 Supabase 대시보드).
    """
    query = urlencode(
        {
            "provider": provider,
            "redirect_to": redirect_to,
            "code_challenge": code_challenge,
            "code_challenge_method": "s256",
            "prompt": prompt
        }
    )
    return f"{settings.SUPABASE_URL}/auth/v1/authorize?{query}"


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"GoTrue 오류 (HTTP {response.status_code})"
    # GoTrue 오류 포맷은 버전에 따라 msg / error_description / error 중 하나
    return (
        body.get("msg")
        or body.get("error_description")
        or body.get("error")
        or str(body)
    )


async def _post(path: str, *, json: dict, headers: dict | None = None) -> dict:
    request_headers = {"apikey": settings.SUPABASE_SERVICE_ROLE_KEY}
    if headers:
        request_headers.update(headers)
    async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT) as client:
        response = await client.post(
            f"{settings.SUPABASE_URL}/auth/v1{path}",
            json=json,
            headers=request_headers,
        )
    if not response.is_success:
        raise AuthServiceError(response.status_code, _error_message(response))
    return response.json() if response.content else {}


async def exchange_pkce(auth_code: str, code_verifier: str) -> dict:
    """OAuth 인가 코드 → 세션 교환 (access/refresh token + user)."""
    return await _post(
        "/token?grant_type=pkce",
        json={"auth_code": auth_code, "code_verifier": code_verifier},
    )


async def sign_in_with_password(email: str, password: str) -> dict:
    """이메일/비밀번호 로그인."""
    return await _post(
        "/token?grant_type=password",
        json={"email": email, "password": password},
    )


async def sign_up(email: str, password: str) -> dict:
    """이메일 회원가입 — 이메일 확인이 켜져 있으면 세션 없이 user만 반환된다."""
    return await _post("/signup", json={"email": email, "password": password})


async def refresh_session(refresh_token: str) -> dict:
    """refresh token으로 새 세션 발급 (rotation — 이전 refresh token은 폐기)."""
    return await _post(
        "/token?grant_type=refresh_token",
        json={"refresh_token": refresh_token},
    )


async def sign_out(access_token: str) -> None:
    """로그아웃 — 해당 사용자의 refresh token을 서버에서 폐기한다."""
    await _post(
        "/logout",
        json={},
        headers={"Authorization": f"Bearer {access_token}"},
    )


def _admin_headers() -> dict:
    """Supabase Admin API 호출용 service role 헤더."""
    return {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
    }


_pre_delete_hooks: list[Callable[[str], Awaitable[None] | None]] = []


def register_pre_delete_hook(hook: Callable[[str], Awaitable[None] | None]) -> None:
    """회원 탈퇴 시 각 서비스가 자기 소유 파일(Storage 등)을 정리할 수 있도록 훅을 등록한다.

    delete_user()가 auth.users를 지우면 DB 행은 on delete cascade로 정리되지만
    Supabase Storage의 실제 파일은 cascade 대상이 아니라 그대로 남는다 — 그래서
    cascade가 storage_path를 담은 행(예: iidx.score_uploads)을 지우기 전에,
    등록된 훅으로 각 서비스가 자신의 파일을 먼저 지울 기회를 준다.
    account 계층은 iidx를 import하지 않는다는 의존 방향 규칙(CLAUDE.md)이 있어
    이 모듈이 iidx의 정리 로직을 직접 호출할 수 없다 — 대신 iidx 쪽이 기동 시
    (app/main.py) 자신의 정리 함수를 여기에 등록해 역방향 의존 없이 연결한다.
    훅 하나가 실패해도 로그만 남기고 나머지 훅 + 실제 계정 삭제는 계속 진행한다
    (정리 실패로 탈퇴 자체가 막히면 안 되므로) — 실패한 파일은 고아로 남을 수
    있으니 알림/모니터링은 훅 구현체 쪽 책임이다.
    """
    _pre_delete_hooks.append(hook)


async def delete_user(user_id: str) -> None:
    """Supabase Admin API로 사용자 계정을 영구 삭제한다.

    DB가 cascade로 삭제되어 storage_path 등의 참조를 잃기 전에, 등록된 사전
    정리 훅(register_pre_delete_hook)을 먼저 실행한다.
    """
    for hook in _pre_delete_hooks:
        try:
            result = hook(user_id)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("회원 탈퇴 사전 정리 훅 실패 (user_id=%s)", user_id)

    async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT) as client:
        response = await client.delete(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            headers=_admin_headers(),
        )
    if not response.is_success:
        raise AuthServiceError(response.status_code, _error_message(response))


async def get_user_by_id(user_id: str) -> dict:
    """Supabase Admin API로 단일 사용자 정보를 조회한다."""
    async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT) as client:
        response = await client.get(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            headers=_admin_headers(),
        )
    if not response.is_success:
        raise AuthServiceError(response.status_code, _error_message(response))
    return response.json()


async def update_user_metadata(user_id: str, app_metadata: dict) -> dict:
    """Supabase Admin API로 사용자의 app_metadata를 업데이트한다 (부분 병합)."""
    async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT) as client:
        response = await client.put(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            json={"app_metadata": app_metadata},
            headers=_admin_headers(),
        )
    if not response.is_success:
        raise AuthServiceError(response.status_code, _error_message(response))
    return response.json()
