"""Supabase 클라이언트 (service role) — 서비스 공통.

service role 키를 사용하므로 RLS를 우회한다. 이 키는 절대 클라이언트에
노출하면 안 되며 백엔드 환경변수로만 관리한다.
web(읽기) · crawl(쓰기) 서비스가 공통으로 사용한다.
"""

from functools import lru_cache

from supabase import Client, create_client

from app.core.config import settings


@lru_cache
def get_supabase() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 환경변수가 필요합니다"
        )
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
