"""Supabase 클라이언트 (service role) — 서비스 공통.

service role 키를 사용하므로 RLS를 우회한다. 이 키는 절대 클라이언트에
노출하면 안 되며 백엔드 환경변수로만 관리한다.
web(읽기) · crawl(쓰기) 서비스가 공통으로 사용한다.

스키마가 둘로 나뉘어 있어(공통 계정 = public, IIDX 서비스 = iidx) 스키마별 전용
클라이언트를 둔다:
  - get_supabase()      : public (auth 계정 계층 — profiles/user_bans/user_follows, RPC admin_list_users)
  - get_supabase_iidx() : iidx   (곡/채보/버전/난이도표 + 서비스 프로필 + 크롤 운영
                                   테이블(crawl_*) + 동기화 RPC)

각 클라이언트는 생성 시점에 기본 스키마를 고정한다(ClientOptions.schema). 이렇게
하면 스키마 헤더가 요청마다 바뀌지 않아, lru_cache로 공유되는 싱글턴을 FastAPI
스레드풀에서 동시에 써도 안전하다(전역 헤더를 뮤테이트하는 .schema() 방식과 대비).
"""

import logging
from functools import lru_cache

import httpx
from supabase import Client, ClientOptions, create_client

from app.core.config import settings

logger = logging.getLogger(__name__)

# 유휴 상태로 오래 재사용된 HTTP/2 keep-alive 연결을 Supabase 엣지가 끊으면,
# 죽은 연결로 나가는 첫 요청이 RemoteProtocolError("Server disconnected")로 실패한다.
# 이때 재시도하면 커넥션 풀이 죽은 연결을 버리고 새 연결을 열어 정상 처리된다.
_RETRYABLE = (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError)
_MAX_RETRIES = 1


def _install_disconnect_retry(session: httpx.Client) -> None:
    """postgrest가 쓰는 httpx 세션의 request를 감싸 연결 끊김/PGRST303 시 1회 재시도한다.

    호출부(crud/*)를 손대지 않고 모든 Supabase 데이터 접근을 한 곳에서 견고화한다.
    주의: 서버가 요청을 처리(commit)한 직후 응답 전에 끊긴 경우, insert류는
    재시도로 중복 적용될 수 있다. 다만 대부분의 끊김은 유휴 stale 커넥션에
    요청을 보내는 시점(서버 처리 전)에 발생하고, 크롤 RPC는 upsert/전량교체라
    멱등하므로 실무상 안전하다.

    PGRST303("JWT issued at future")은 우리 쪽 키가 잘못된 게 아니라 Supabase
    엣지의 PostgREST 인스턴스 간 시계 오차로 간헐적으로 발생하는 것으로 알려진
    현상이다(동일한 고정 service-role 키로 어떤 요청은 성공하고 어떤 요청은
    실패). 애플리케이션 재시도 외에 우리 쪽에서 고칠 수 있는 원인이 아니므로
    같은 방식으로 1회 재시도한다.
    """
    original_request = session.request

    def request_with_retry(*args, **kwargs):
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = original_request(*args, **kwargs)
            except _RETRYABLE as exc:
                if attempt == _MAX_RETRIES:
                    raise
                logger.warning(
                    "Supabase 연결 끊김, 재시도(%d/%d): %s",
                    attempt + 1, _MAX_RETRIES, exc,
                )
                continue

            if attempt < _MAX_RETRIES and _is_jwt_future_error(response):
                logger.warning(
                    "Supabase JWT 시계 오차(PGRST303), 재시도(%d/%d)",
                    attempt + 1, _MAX_RETRIES,
                )
                continue
            return response

    session.request = request_with_retry


def _is_jwt_future_error(response: httpx.Response) -> bool:
    """PostgREST가 'JWT issued at future'(PGRST303)로 응답했는지 확인한다."""
    if response.status_code != 400:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("code") == "PGRST303"


def _create_client(schema: str) -> Client:
    """지정 스키마를 기본값으로 고정한 service-role 클라이언트를 만든다."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 환경변수가 필요합니다"
        )
    client = create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY,
        options=ClientOptions(schema=schema),
    )
    _install_disconnect_retry(client.postgrest.session)
    return client


@lru_cache
def get_supabase() -> Client:
    """public 스키마 클라이언트 — 계정 계층(profiles/user_bans/user_follows)과
    public에 정의된 RPC(admin_list_users) 접근용."""
    return _create_client("public")


@lru_cache
def get_supabase_iidx() -> Client:
    """iidx 스키마 클라이언트 — 곡/채보/버전/난이도표/서비스 프로필, 크롤 운영
    테이블(crawl_targets/crawl_schedules/crawl_sync_logs), 동기화 RPC 접근용."""
    return _create_client("iidx")
