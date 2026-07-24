"""Supabase 클라이언트 (service role) — 서비스 공통.

service role 키를 사용하므로 RLS를 우회한다. 이 키는 절대 클라이언트에
노출하면 안 되며 백엔드 환경변수로만 관리한다.
web(읽기) · crawl(쓰기) 서비스가 공통으로 사용한다.
"""

import logging
from functools import lru_cache

import httpx
from supabase import Client, create_client

from app.core.config import settings

logger = logging.getLogger(__name__)

# 유휴 상태로 오래 재사용된 HTTP/2 keep-alive 연결을 Supabase 엣지가 끊으면,
# 죽은 연결로 나가는 첫 요청이 RemoteProtocolError("Server disconnected")로 실패한다.
# 이때 재시도하면 커넥션 풀이 죽은 연결을 버리고 새 연결을 열어 정상 처리된다.
_RETRYABLE = (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError)
_MAX_RETRIES = 1


def _install_disconnect_retry(session: httpx.Client) -> None:
    """postgrest가 쓰는 httpx 세션의 request를 감싸 연결 끊김 시 1회 재시도한다.

    호출부(crud/*)를 손대지 않고 모든 Supabase 데이터 접근을 한 곳에서 견고화한다.
    주의: 서버가 요청을 처리(commit)한 직후 응답 전에 끊긴 경우, insert류는
    재시도로 중복 적용될 수 있다. 다만 대부분의 끊김은 유휴 stale 커넥션에
    요청을 보내는 시점(서버 처리 전)에 발생하고, 크롤 RPC는 upsert/전량교체라
    멱등하므로 실무상 안전하다.
    """
    original_request = session.request

    def request_with_retry(*args, **kwargs):
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return original_request(*args, **kwargs)
            except _RETRYABLE as exc:
                if attempt == _MAX_RETRIES:
                    raise
                logger.warning(
                    "Supabase 연결 끊김, 재시도(%d/%d): %s",
                    attempt + 1, _MAX_RETRIES, exc,
                )

    session.request = request_with_retry


@lru_cache
def get_supabase() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 환경변수가 필요합니다"
        )
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    _install_disconnect_retry(client.postgrest.session)
    return client
