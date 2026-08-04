"""성적 CSV 업로드 서비스.

흐름:
1. SHA-256 해시 계산 → 동일 내용(같은 user·style·hash)이면 기존 ID 반환, 스냅샷 미생성.
2. Supabase Storage에 원본 CSV 저장.
3. score_uploads 레코드 생성.
4. CSV 파싱 → user_chart_scores bulk insert.
5. score_current 갱신 (이 시점부터 새 스냅샷이 활성화됨).
"""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass

from app.crud.iidx import scores as crud_scores
from app.services.iidx.scores import storage as score_storage
from app.services.iidx.scores.matcher import enrich_async
from app.services.iidx.scores.parser import ParseResult, parse_csv


@dataclass
class UploadResult:
    upload_id: str
    source: str       # "official" | "crawled"
    song_count: int
    uploaded_at: str | None
    changed: bool     # False = 동일 내용, 스냅샷 미생성


async def upload_score_csv(
    user_id: str,
    play_style: str,
    csv_bytes: bytes,
) -> UploadResult:
    """성적 CSV 바이트를 처리하고 UploadResult를 반환한다.

    내용이 이전 업로드와 완전히 동일하면 스냅샷을 생성하지 않고 changed=False 반환.
    """
    # UTF-8 BOM을 포함한 채로 해시하므로 바이트 원본 그대로 사용
    content_hash = hashlib.sha256(csv_bytes).hexdigest()

    # 중복 감지
    existing = await asyncio.to_thread(
        crud_scores.get_upload_by_hash, user_id, play_style, content_hash
    )
    if existing:
        return UploadResult(
            upload_id=existing["id"],
            source=existing["source"],
            song_count=existing["song_count"],
            uploaded_at=existing.get("uploaded_at"),
            changed=False,
        )

    # CSV 파싱 (BOM 자동 제거)
    raw = csv_bytes.decode("utf-8-sig")
    parsed: ParseResult = parse_csv(raw)

    # Storage 업로드
    upload_id = str(uuid.uuid4())
    storage_path = f"{user_id}/{play_style}/{upload_id}.csv"
    await score_storage.upload_csv_async(storage_path, csv_bytes)

    # 곡 수 = 중복 없는 타이틀 수
    song_count = len({s.title for s in parsed.scores})

    # DB 레코드 생성
    upload_row = await asyncio.to_thread(
        crud_scores.insert_upload,
        upload_id=upload_id,
        user_id=user_id,
        play_style=play_style,
        source=parsed.source,
        content_hash=content_hash,
        storage_path=storage_path,
        song_count=song_count,
    )

    # songs/charts 마스터 매칭 — song_id 획득 + 크롤 CSV 누락 필드 보완
    is_crawled = parsed.source == "crawled"
    enriched = await enrich_async(parsed.scores, play_style, is_crawled)

    # 성적 행 bulk insert
    score_rows = [
        {
            "title": e.score.title,
            "difficulty": e.score.difficulty,
            # 크롤 CSV: songs 테이블에서 보완된 값 우선, 없으면 CSV 원본(None)
            "version": e.filled_version if is_crawled else e.score.version,
            "genre": e.filled_genre if is_crawled else e.score.genre,
            "artist": e.filled_artist if is_crawled else e.score.artist,
            "play_count": e.score.play_count,
            "last_played_at": (
                e.score.last_played_at.isoformat() if e.score.last_played_at else None
            ),
            "miss_count": e.score.miss_count,
            "level": e.score.level,
            "ex_score": e.score.ex_score,
            "pgreat": e.score.pgreat,
            "great": e.score.great,
            "clear_type": e.score.clear_type,
            "dj_level": e.score.dj_level,
            "song_id": e.song_id,
        }
        for e in enriched
    ]
    await asyncio.to_thread(
        crud_scores.insert_chart_scores, upload_id, user_id, play_style, score_rows
    )

    # 현재 스냅샷 포인터 갱신
    await asyncio.to_thread(crud_scores.upsert_current, user_id, play_style, upload_id)

    return UploadResult(
        upload_id=upload_id,
        source=parsed.source,
        song_count=song_count,
        uploaded_at=upload_row.get("uploaded_at"),
        changed=True,
    )
