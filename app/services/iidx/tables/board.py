"""서열표 집계 — 난이도표 엔트리에 사용자 클리어 램프를 얹는다.

난이도표 엔트리(iidx.difficulty_entries)와 사용자 성적(iidx.user_chart_scores)은
서로 다른 출처(구글 시트 크롤 / eagate CSV)에서 오고, difficulty_entries.chart_id는
아직 채워지지 않으므로 **타이틀 + 난이도**로 매칭한다. 타이틀 정규화는 성적 매처와
같은 규칙(app/services/iidx/scores/matcher.py)을 재사용해 표기 차이를 흡수한다:
완전일치 → NFKC 정규화 → loose(구두점/공백/대소문자 무시) 순 폴백.

램프 정규화(eagate 원문 → 8종 표준 램프)도 클리어 현황 요약과 같은 규칙을 쓴다.
"""

from app.schemas.iidx.table import (
    BoardComparison,
    BoardEntry,
    BoardScore,
    BoardSection,
    BoardUser,
    TableBoardResponse,
)
from app.services.iidx.scores.matcher import loose_title, norm_title
from app.services.iidx.scores.summary import LAMP_KEYS, normalize_lamp

# 램프 서열(약한 순) — 비교 모드에서 승패를 가릴 때 쓴다. FE의 CLEAR_LAMP_ORDER와 동일.
_LAMP_RANK = {key: i for i, key in enumerate(LAMP_KEYS)}

# 섹션 제목에 쓰는 표 유형 라벨 (5ch 표의 지력/개인차 구분).
_TABLE_TYPE_LABEL = {"STRENGTH": "지력", "PERSONAL": "개인차"}

# 섹션 정렬 시 표 유형 순서 — 지력 → 개인차 → 미분류.
_TABLE_TYPE_ORDER = {"STRENGTH": 0, "PERSONAL": 1}


class ScoreIndex:
    """성적 행을 (타이틀, 난이도) 키로 조회할 수 있게 만든 인덱스."""

    def __init__(self, rows: list[dict]):
        self._exact: dict[tuple[str, str], dict] = {}
        self._norm: dict[tuple[str, str], dict] = {}
        self._loose: dict[tuple[str, str], dict] = {}
        loose_conflicts: set[tuple[str, str]] = set()

        for row in rows:
            title = row.get("title") or ""
            difficulty = row.get("difficulty") or ""
            self._exact.setdefault((title, difficulty), row)
            self._norm.setdefault((norm_title(title), difficulty), row)

            lk = loose_title(title)
            if not lk:
                continue
            key = (lk, difficulty)
            existing = self._loose.get(key)
            if existing is None:
                self._loose[key] = row
            elif existing is not row:
                # 서로 다른 곡이 같은 loose 키로 뭉개진 경우 — 오매칭을 막으려 제외한다.
                loose_conflicts.add(key)
        for key in loose_conflicts:
            self._loose.pop(key, None)

    def find(self, title: str, difficulty: str) -> dict | None:
        return (
            self._exact.get((title, difficulty))
            or self._norm.get((norm_title(title), difficulty))
            or self._loose.get((loose_title(title), difficulty))
        )


def _to_score(row: dict | None) -> BoardScore | None:
    """성적 행을 응답용 BoardScore로 변환. 매칭 실패(None)면 None."""
    if row is None:
        return None
    return BoardScore(
        clear_lamp=normalize_lamp(row.get("clear_type")),
        dj_level=row.get("dj_level"),
        ex_score=row.get("ex_score"),
        level=row.get("level"),
        last_played_at=row.get("last_played_at"),
    )


def _section_key(entry: dict) -> tuple[str | None, float | None, str | None]:
    rating = entry.get("rating")
    return (
        entry.get("grade"),
        float(rating) if rating is not None else None,
        entry.get("table_type"),
    )


def _section_title(grade: str | None, rating: float | None, table_type: str | None) -> str:
    base = grade if grade is not None else (f"{rating:g}" if rating is not None else "미분류")
    label = _TABLE_TYPE_LABEL.get(table_type or "")
    return f"{base} {label}" if label else base


def _section_id(grade: str | None, rating: float | None, table_type: str | None) -> str:
    base = grade if grade is not None else (f"{rating:g}" if rating is not None else "none")
    return f"{base}:{table_type or 'ALL'}"


def _section_sort_key(
    key: tuple[str | None, float | None, str | None], grade_rank: dict[str, int]
) -> tuple:
    """섹션 정렬 기준 — 등급/레이팅 모두 **높은 쪽부터** 내려온다.

    - GRADE 표: 표가 선언한 grades[]는 약한 순(예: F..S+)이므로 그 서열의 역순
      (알파벳순 아님). 목록에 없는 등급은 뒤로.
    - NUMERIC 표: rating 내림차순(높은 난이도부터).
    같은 등급/레이팅 안에서는 지력 → 개인차 → 미분류 순.
    """
    grade, rating, table_type = key
    # 미상 등급은 항상 뒤로 보내기 위해 (분류됨=0, 미분류=1) 플래그를 앞에 둔다.
    known = grade in grade_rank if grade is not None else False
    grade_order = (0, -grade_rank[grade]) if known else (1, 0)
    rating_order = -rating if rating is not None else float("inf")
    return (grade_order, rating_order, _TABLE_TYPE_ORDER.get(table_type or "", 2))


def build_table_board(
    table: dict,
    entries: list[dict],
    *,
    user: BoardUser | None,
    opponent: BoardUser | None,
    my_rows: list[dict] | None,
    opponent_rows: list[dict] | None,
) -> TableBoardResponse:
    """표 메타 + 엔트리 + (선택) 성적으로 서열표 응답을 만든다.

    my_rows / opponent_rows가 None이면 그 쪽 램프는 전부 null이 된다
    (비로그인 조회 = 곡 목록만 내려주는 경우).
    """
    my_index = ScoreIndex(my_rows) if my_rows is not None else None
    opp_index = ScoreIndex(opponent_rows) if opponent_rows is not None else None

    grade_rank = {g: i for i, g in enumerate(table.get("grades") or [])}
    grouped: dict[tuple[str | None, float | None, str | None], list[BoardEntry]] = {}

    win = lose = draw = 0
    compared = 0

    for e in entries:
        title = e["title"]
        difficulty = e["difficulty"]
        my_row = my_index.find(title, difficulty) if my_index is not None else None
        opp_row = opp_index.find(title, difficulty) if opp_index is not None else None

        my_score = _to_score(my_row)
        opp_score = _to_score(opp_row) if opp_index is not None else None

        # 비교 집계 — 양쪽 모두 성적이 없는(=둘 다 곡 매칭 실패) 엔트리는 제외하고,
        # 한쪽만 없는 경우는 NO PLAY로 간주해 승패를 가린다.
        if opp_index is not None and my_index is not None and (my_row or opp_row):
            compared += 1
            mine = _LAMP_RANK[my_score.clear_lamp if my_score else "no_play"]
            theirs = _LAMP_RANK[opp_score.clear_lamp if opp_score else "no_play"]
            if mine > theirs:
                win += 1
            elif mine < theirs:
                lose += 1
            else:
                draw += 1

        rating = e.get("rating")
        grouped.setdefault(_section_key(e), []).append(
            BoardEntry(
                id=e["id"],
                title=title,
                series=e.get("series"),
                play_style=e["play_style"],
                difficulty=difficulty,
                level=e.get("level"),
                grade=e.get("grade"),
                rating=float(rating) if rating is not None else None,
                table_type=e.get("table_type"),
                score=my_score,
                opponent_score=opp_score,
            )
        )

    sections = [
        BoardSection(
            id=_section_id(*key),
            title=_section_title(*key),
            grade=key[0],
            rating=key[1],
            table_type=key[2],
            entries=section_entries,
        )
        for key, section_entries in sorted(
            grouped.items(), key=lambda kv: _section_sort_key(kv[0], grade_rank)
        )
    ]

    comparison = None
    if opp_index is not None and my_index is not None:
        decided = win + lose
        comparison = BoardComparison(
            total=compared,
            win=win,
            lose=lose,
            draw=draw,
            win_rate=round(win / decided * 100, 1) if decided else None,
        )

    return TableBoardResponse(
        slug=table["slug"],
        name=table["name"],
        source=table["source"],
        play_style=table["play_style"],
        rating_type=table["rating_type"],
        level=table.get("level"),
        grades=table.get("grades"),
        updated_at=table["updated_at"],
        user=user,
        opponent=opponent,
        total_entries=len(entries),
        sections=sections,
        comparison=comparison,
    )
