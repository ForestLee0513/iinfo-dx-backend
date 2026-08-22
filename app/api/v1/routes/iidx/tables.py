"""난이도표 공개 조회 API (인증 불필요).

크롤링으로 Supabase에 적재된 난이도표를 프론트엔드에 그대로 제공한다.
쓰기/갱신 경로는 없다 — 데이터는 주간 크롤링 스케줄러로만 갱신된다.

엔드포인트:
  GET /iidx/tables                 — 난이도표 목록(드롭다운 소스)
  GET /iidx/tables/{slug}          — 표 1개 + 엔트리 원본
  GET /iidx/tables/{slug}/board    — 서열표(엔트리 + 클리어 램프, 사용자 비교)
"""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import OptionalIdentity
from app.core.openapi import PUBLIC
from app.crud.account import profiles as crud_profiles
from app.crud.iidx import scores as crud_scores
from app.crud.iidx.tables import (
    fetch_entries,
    fetch_table,
    fetch_table_meta,
    fetch_tables,
    sort_entries_by_rank,
)
from app.schemas.account.profile import HANDLE_PATTERN
from app.schemas.iidx.table import (
    BoardUser,
    TableBoardResponse,
    TableDetail,
    TableListResponse,
)
from app.services.iidx.tables.board import build_table_board

router = APIRouter()


@router.get("", summary="난이도표 목록 조회", response_model=TableListResponse, openapi_extra=PUBLIC)
def list_tables():
    return {"tables": fetch_tables()}


@router.get("/{slug}", summary="난이도표 1개 + 엔트리 조회", response_model=TableDetail, openapi_extra=PUBLIC)
def get_table(slug: str):
    table = fetch_table(slug)
    if table is None:
        raise HTTPException(status_code=404, detail=f"표를 찾을 수 없습니다: {slug}")
    # 등급형/숫자형 모두 높은 난이도부터 내려오도록 정렬해서 내려준다.
    sort_entries_by_rank(table)
    return table


def _resolve_identifier(identifier: str) -> dict | None:
    """identifier(UUID 또는 handle)로 account 프로필 행을 찾는다. 없으면 None."""
    try:
        uuid.UUID(identifier)
    except ValueError:
        if not HANDLE_PATTERN.match(identifier):
            return None
        return crud_profiles.get_profile_row_by_handle(identifier)
    return crud_profiles.get_profile_row(identifier)


async def _resolve_board_user(identifier: str, viewer_id: str | None) -> BoardUser:
    """서열표 대상 사용자를 해석하고 공개 여부를 검사한다.

    `/iidx/scores/summary`와 동일한 규칙 — IIDX 미가입이거나, 비공개 프로필을
    본인이 아닌 사람이 조회하면 404(존재 은닉).
    """
    row = await asyncio.to_thread(_resolve_identifier, identifier)
    if row is None:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    user_id = row["user_id"]
    if not await asyncio.to_thread(crud_profiles.is_iidx_member, user_id):
        raise HTTPException(status_code=404, detail="IIDX 서비스에 가입하지 않은 사용자입니다.")

    is_mine = viewer_id is not None and viewer_id == user_id
    if not row.get("iidx_is_public", True) and not is_mine:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    return BoardUser(
        user_id=user_id, handle=row.get("handle"), dj_name=row.get("dj_name")
    )


async def _score_rows(user: BoardUser | None, play_style: str) -> list[dict] | None:
    """대상이 있으면 현재 스냅샷의 성적 행을, 없으면 None(램프 미표시)을 반환한다."""
    if user is None:
        return None
    return await asyncio.to_thread(
        crud_scores.get_board_score_rows, user.user_id, play_style
    )


@router.get(
    "/{slug}/board",
    summary="난이도표 서열표 (클리어 램프 / 사용자 비교)",
    response_model=TableBoardResponse,
    openapi_extra=PUBLIC,
)
async def get_table_board(
    slug: str,
    identity: OptionalIdentity,
    identifier: str | None = Query(
        None,
        description="램프를 표시할 대상 유저의 UUID 또는 handle. "
        "생략하면 로그인 사용자 본인, 비로그인이면 곡 목록만(램프 없음) 반환한다.",
    ),
    opponent: str | None = Query(
        None, description="비교 대상 유저의 UUID 또는 handle. 지정하면 비교 모드."
    ),
):
    """난이도표를 등급/유형(지력·개인차) 섹션으로 묶어 클리어 램프와 함께 반환한다.

    - **비로그인**: `identifier` 없이 호출하면 `user=null`, 모든 엔트리 `score=null`로
      곡 목록만 내려온다 — 비로그인 화면이 그대로 같은 응답을 쓸 수 있다.
    - **로그인**: `identifier`를 생략하면 토큰의 사용자 본인 성적을 사용한다.
      다른 사용자를 보려면 `identifier`(UUID/handle)를 넘긴다 — 대상의 IIDX 공개
      여부를 따른다(비공개면 본인만, 미가입이면 404).
    - **비교 모드**: `opponent`를 함께 넘기면 각 엔트리에 `opponent_score`가 채워지고
      `comparison`에 승/패/무와 승률이 담긴다. 램프 서열은 NO PLAY < FAILED <
      ASSIST < EASY < NORMAL < HARD < EX-HARD < FULL COMBO. 양쪽 모두 성적이
      매칭되지 않은 엔트리는 집계에서 빠지고, 한쪽만 없으면 NO PLAY로 본다.
    - 램프 매칭은 표 엔트리의 (타이틀, 난이도)를 성적 스냅샷과 대조해 이뤄진다.
      표기 차이가 커서 매칭에 실패한 엔트리는 `score=null`로 남는다.
    """
    table = await asyncio.to_thread(fetch_table_meta, slug)
    if table is None:
        raise HTTPException(status_code=404, detail=f"표를 찾을 수 없습니다: {slug}")

    viewer_id = identity.id if identity is not None else None

    # 램프 주체: identifier > 로그인 사용자 본인 > 없음(비로그인 = 곡 목록만)
    if identifier is not None:
        user = await _resolve_board_user(identifier, viewer_id)
    elif viewer_id is not None:
        # 본인 조회는 공개 여부·IIDX 가입 여부를 따지지 않는다 — 미온보딩이면
        # 성적이 없어 램프가 전부 null일 뿐, 곡 목록은 그대로 보여준다.
        row = await asyncio.to_thread(crud_profiles.get_profile_row, viewer_id)
        user = BoardUser(
            user_id=viewer_id,
            handle=(row or {}).get("handle"),
            dj_name=(row or {}).get("dj_name"),
        )
    else:
        user = None

    if opponent is not None and user is None:
        raise HTTPException(
            status_code=401,
            detail="비교하려면 로그인하거나 identifier를 지정해야 합니다.",
        )
    opponent_user = (
        await _resolve_board_user(opponent, viewer_id) if opponent is not None else None
    )

    play_style = table["play_style"]
    entries, my_rows, opponent_rows = await asyncio.gather(
        asyncio.to_thread(fetch_entries, table["id"]),
        _score_rows(user, play_style),
        _score_rows(opponent_user, play_style),
    )

    return build_table_board(
        table,
        entries,
        user=user,
        opponent=opponent_user,
        my_rows=my_rows,
        opponent_rows=opponent_rows,
    )
