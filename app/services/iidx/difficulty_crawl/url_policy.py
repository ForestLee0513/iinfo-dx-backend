"""크롤러의 외부 요청 대상 검증.

크롤 미리보기는 공개 API이므로, 호출자가 URL을 지정해 백엔드를 내부망 프록시로
사용하지 못하게 각 크롤러가 허용한 HTTPS 호스트만 요청할 수 있다.
"""

from urllib.parse import urlsplit


_ALLOWED_HOSTS: dict[str, frozenset[str]] = {
    "5ch_sheet": frozenset({"docs.google.com"}),
}


def validate_target_url(crawler: str, url: object) -> str:
    """크롤러가 지원하는 공개 HTTPS URL인지 검증해 그대로 반환한다."""
    if not isinstance(url, str) or not url:
        raise ValueError("크롤링 URL이 필요합니다.")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("크롤링 URL 형식이 올바르지 않습니다.") from exc

    allowed_hosts = _ALLOWED_HOSTS.get(crawler)
    if (
        allowed_hosts is None
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or host not in allowed_hosts
        or port not in (None, 443)
    ):
        raise ValueError("허용되지 않은 크롤링 URL입니다.")
    return url
