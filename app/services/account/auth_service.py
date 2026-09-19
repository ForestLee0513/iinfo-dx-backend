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
import secrets
from urllib.parse import urlencode

import httpx

from app.core.config import settings


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


async def delete_user(user_id: str) -> None:
    """Supabase Admin API로 사용자 계정을 영구 삭제한다."""
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
