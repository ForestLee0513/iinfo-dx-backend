"""web 모듈 라우터 (/web/*) — 사용자 client(별도 프론트 레포)가 붙는 API.

- GET  /web/tables, /web/tables/{slug}               : 난이도표 조회 (공개)
- GET  /web/auth/login/{provider}, /web/auth/callback : OAuth 로그인 (구글 등)
- POST /web/auth/signup, /web/auth/login              : 이메일 가입/로그인
- POST /web/auth/refresh, /web/auth/logout            : 세션 갱신/로그아웃 (httpOnly 쿠키 기반)
- GET  /web/auth/me                                   : 현재 로그인 사용자 (인증 필수)
"""

from fastapi import APIRouter

from app.web.endpoints import auth, tables

router = APIRouter()

router.include_router(tables.router, prefix="/tables")
router.include_router(auth.router, prefix="/auth")
