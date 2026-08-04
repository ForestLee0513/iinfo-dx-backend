"""eagate 성적 CSV 파서.

두 가지 소스를 자동 판별한다:
  official : eagate score_download.html 에서 직접 받아온 전체 CSV.
             バージョン/ジャンル/アーティスト/プレー回数/ミスカウント/最終プレー日시 모두 채워짐.
  crawled  : 북마크릿의 buildStyleCsv 로 난이도표 페이지를 재조합한 CSV.
             위 필드들은 모두 "-" (단일 대시)로 채워짐 → null 처리.

두 형식 모두 41컬럼 헤더 구조는 동일하다.
소스 판별 기준: 첫 번째 데이터 행의 バージョン 컬럼(index 0) == "-" → crawled.

레벨 0 인 난이도 슬롯은 "해당 곡에 이 난이도 없음"을 의미하므로 건너뛴다.
"""

import csv
import io
from dataclasses import dataclass
from datetime import datetime

# 41컬럼 구성: 5개 곡 단위 컬럼 + 난이도×7컬럼×5 + 최종플레이일시
DIFFICULTIES = ["BEGINNER", "NORMAL", "HYPER", "ANOTHER", "LEGGENDARIA"]
_DIFF_OFFSETS = {d: 5 + i * 7 for i, d in enumerate(DIFFICULTIES)}
_CRAWLED_MARKER = "-"  # csv.ts의 const DASH = "-"


@dataclass
class ChartScore:
    title: str
    difficulty: str
    # 공식 CSV 전용 — 크롤이면 None
    version: str | None
    genre: str | None
    artist: str | None
    play_count: int | None
    last_played_at: datetime | None
    miss_count: int | None
    # 공통
    level: int
    ex_score: int
    pgreat: int
    great: int
    clear_type: str | None   # "NO PLAY" → None
    dj_level: str | None     # "---" → None


@dataclass
class ParseResult:
    source: str              # "official" | "crawled"
    scores: list[ChartScore]


# ── 내부 파싱 헬퍼 ──────────────────────────────────────────────────────────

def _opt_str(v: str) -> str | None:
    """빈 문자열·대시류는 None으로."""
    v = v.strip()
    return None if v in ("", "-", "---") else v


def _opt_int(v: str) -> int | None:
    """정수 파싱, 대시·빈 값은 None."""
    v = v.strip()
    if not v or v in ("-", "---"):
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _opt_dt(v: str) -> datetime | None:
    """ISO 날짜 파싱, 대시·빈 값은 None."""
    v = v.strip()
    if not v or v in ("-", "---"):
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _parse_clear(v: str) -> str | None:
    """クリアタイプ: "NO PLAY" → None."""
    v = v.strip()
    return None if v == "NO PLAY" else (_opt_str(v))


def _parse_dj_level(v: str) -> str | None:
    """DJ LEVEL: "---" → None."""
    v = v.strip()
    return None if v == "---" else (_opt_str(v))


def _safe_int(v: str, default: int = 0) -> int:
    try:
        return int(v.strip())
    except (ValueError, AttributeError):
        return default


# ── 공개 API ────────────────────────────────────────────────────────────────

def parse_csv(raw: str) -> ParseResult:
    """CSV 문자열을 파싱해 ParseResult를 반환한다.

    UTF-8 BOM(\\uFEFF)을 자동 제거한다. 헤더 행은 건너뛰고,
    레벨 0 인 난이도 슬롯은 무시한다.
    """
    if raw.startswith("﻿"):
        raw = raw[1:]

    reader = csv.reader(io.StringIO(raw.replace("\r\n", "\n")))
    rows = list(reader)
    if not rows:
        return ParseResult(source="official", scores=[])

    if len(rows[0]) < 41:
        raise ValueError(f"CSV 헤더 컬럼 수 불일치: {len(rows[0])} (41 필요)")

    data_rows = [r for r in rows[1:] if any(c.strip() for c in r)]
    if not data_rows:
        return ParseResult(source="official", scores=[])

    # 소스 판별: バージョン 컬럼(index 0) == "-" → 크롤 방식
    source = "crawled" if data_rows[0][0].strip() == _CRAWLED_MARKER else "official"
    is_crawled = source == "crawled"

    scores: list[ChartScore] = []
    for row in data_rows:
        if len(row) < 41:
            continue
        title = row[1].strip()
        if not title:
            continue

        # 곡 단위 필드 (크롤이면 전부 None)
        version = None if is_crawled else _opt_str(row[0])
        genre = None if is_crawled else _opt_str(row[2])
        artist = None if is_crawled else _opt_str(row[3])
        play_count = None if is_crawled else _opt_int(row[4])
        last_played_at = None if is_crawled else _opt_dt(row[40])

        for diff, offset in _DIFF_OFFSETS.items():
            level = _safe_int(row[offset])
            if level == 0:
                # 해당 난이도 차트가 없는 슬롯
                continue

            ex_score = _safe_int(row[offset + 1])
            pgreat = _safe_int(row[offset + 2])
            great = _safe_int(row[offset + 3])

            # ミスカウント: 크롤은 항상 None / 공식은 "---"(미기록)이거나 정수
            miss_count: int | None = None if is_crawled else _opt_int(row[offset + 4])
            clear_type = _parse_clear(row[offset + 5])
            dj_level = _parse_dj_level(row[offset + 6])

            scores.append(
                ChartScore(
                    title=title,
                    difficulty=diff,
                    version=version,
                    genre=genre,
                    artist=artist,
                    play_count=play_count,
                    last_played_at=last_played_at,
                    miss_count=miss_count,
                    level=level,
                    ex_score=ex_score,
                    pgreat=pgreat,
                    great=great,
                    clear_type=clear_type,
                    dj_level=dj_level,
                )
            )

    return ParseResult(source=source, scores=scores)
