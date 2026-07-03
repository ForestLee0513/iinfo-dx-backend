"""공개(클라이언트) / 비공개(내부) OpenAPI 문서 분리.

- 공개: /docs, /redoc, /openapi.json — PUBLIC 마커를 붙인 엔드포인트만 노출
- 비공개: /internal/docs, /internal/openapi.json — 어드민/크롤 포함 전체 엔드포인트

공개 여부는 opt-in이다: 엔드포인트 데코레이터에 openapi_extra=PUBLIC을 붙인
것만 공개 스펙에 실리므로, 클라이언트에 일부만 노출하는 제어가 기본값이 된다.

이 분리는 '문서 노출' 제어일 뿐 '접근' 제어가 아니다 — 실제 차단은
인증(require_role)과 리버스 프록시에서 처리한다. /internal/* 경로는
프록시(NPM)에서 외부(공개 도메인) 접근을 차단할 것.
"""

from fastapi import FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

_VISIBILITY_KEY = "x-visibility"

# 사용: @router.get(..., openapi_extra=PUBLIC)
PUBLIC = {_VISIBILITY_KEY: "public"}


def _is_public(route) -> bool:
    return (
        isinstance(route, APIRoute)
        and (route.openapi_extra or {}).get(_VISIBILITY_KEY) == "public"
    )


def setup_docs(app: FastAPI) -> None:
    """공개/비공개 스웨거 라우트를 마운트한다 (모든 include_router 뒤에 호출).

    스펙은 첫 요청 시 생성 후 캐시한다 — 라우트는 기동 이후 불변이므로 안전.
    """
    specs: dict[str, dict] = {}

    def _public_spec() -> dict:
        if "public" not in specs:
            specs["public"] = get_openapi(
                title=f"{app.title} — Client API",
                version=app.version,
                description="사용자 클라이언트 공개 API. 노출 지정(PUBLIC)된 엔드포인트만 포함한다.",
                routes=[route for route in app.routes if _is_public(route)],
            )
        return specs["public"]

    def _internal_spec() -> dict:
        if "internal" not in specs:
            specs["internal"] = get_openapi(
                title=f"{app.title} — Internal API",
                version=app.version,
                description="내부 전용 문서 — 어드민/크롤 진단을 포함한 전체 엔드포인트.",
                routes=app.routes,
            )
        return specs["internal"]

    @app.get("/openapi.json", include_in_schema=False)
    def public_openapi():
        return _public_spec()

    @app.get("/docs", include_in_schema=False)
    def public_docs():
        return get_swagger_ui_html(
            openapi_url="/openapi.json", title=f"{app.title} — Client API"
        )

    @app.get("/redoc", include_in_schema=False)
    def public_redoc():
        return get_redoc_html(
            openapi_url="/openapi.json", title=f"{app.title} — Client API"
        )

    @app.get("/internal/openapi.json", include_in_schema=False)
    def internal_openapi():
        return _internal_spec()

    @app.get("/internal/docs", include_in_schema=False)
    def internal_docs():
        return get_swagger_ui_html(
            openapi_url="/internal/openapi.json", title=f"{app.title} — Internal API"
        )
