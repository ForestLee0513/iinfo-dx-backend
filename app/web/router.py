"""web 모듈 라우터 (/web/*) — 사용자 client(별도 프론트 레포)가 붙는 API.

- GET /web/tables, /web/tables/{slug} : 난이도표 조회 (공개)
- GET /web/me                          : 현재 로그인 사용자 (인증 필수)
"""

from fastapi import APIRouter

from app.web.endpoints import me, tables

router = APIRouter()

router.include_router(tables.router, prefix="/tables")
router.include_router(me.router)
