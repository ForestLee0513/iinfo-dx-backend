"""난이도표 공개 조회 API (인증 불필요).

크롤링으로 Supabase에 적재된 난이도표를 프론트엔드에 그대로 제공한다.
쓰기/갱신 경로는 없다 — 데이터는 주간 크롤링 스케줄러로만 갱신된다.
"""

from fastapi import APIRouter, HTTPException

from app.web.queries import fetch_table, fetch_tables
from app.web.schemas import TableDetail, TableListResponse

router = APIRouter()


@router.get("", summary="난이도표 목록 조회", response_model=TableListResponse)
def list_tables():
    return {"tables": fetch_tables()}


@router.get("/{slug}", summary="난이도표 1개 + 엔트리 조회", response_model=TableDetail)
def get_table(slug: str):
    table = fetch_table(slug)
    if table is None:
        raise HTTPException(status_code=404, detail=f"표를 찾을 수 없습니다: {slug}")
    return table
