"""admin 모듈 라우터 (/admin/*) — 자리표시자.

실제 어드민 API는 별도 어드민 레포 작업 시 이 모듈에 구현한다.
지금은 네임스페이스만 예약해 두고 엔드포인트는 만들지 않는다.
"""

from fastapi import APIRouter

router = APIRouter()

# TODO: 어드민 엔드포인트 미구현 (별도 레포 예정)
