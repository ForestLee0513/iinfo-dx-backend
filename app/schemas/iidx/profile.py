"""북마크릿 프로필 업로드 스키마 — 크롤러 Profile 타입(iinfo-dx-crawler src/types.ts)과 1:1.

키는 크롤러가 보내는 그대로 camelCase를 유지한다. 수치 필드는 number로 파싱되고,
dan은 크롤러가 이미 DAN enum 문자열("10TH_DAN" 등)로 매핑하며 미취득은 null,
arenaClass는 원본 문자열 그대로(미취득 "---"). 크롤 실패 시 프로필 자체가 생략된다.
"""

from pydantic import BaseModel

# 段位認定 등급 (crawler constants.ts DAN 테이블). 미취득은 null.
# 검증은 크롤러 책임 — 예기치 못한 값으로 업로드 전체가 실패하지 않도록 str로 받는다.
DAN_VALUES = (
    "7TH_KYU", "6TH_KYU", "5TH_KYU", "4TH_KYU", "3RD_KYU", "2ND_KYU", "1ST_KYU",
    "1ST_DAN", "2ND_DAN", "3RD_DAN", "4TH_DAN", "5TH_DAN", "6TH_DAN", "7TH_DAN",
    "8TH_DAN", "9TH_DAN", "10TH_DAN", "CHUUDEN", "KAIDEN",
)


class RadarValues(BaseModel):
    """노츠레이더 6지표 + 합계. 스타일별로 일부 지표가 없을 수 있어 모두 옵셔널."""

    notes: float | None = None
    chord: float | None = None
    peak: float | None = None
    charge: float | None = None
    scratch: float | None = None
    softLan: float | None = None
    total: float | None = None


class NotesRadar(BaseModel):
    SP: RadarValues | None = None
    DP: RadarValues | None = None


class DanByStyle(BaseModel):
    SP: str | None = None  # DAN enum 문자열 또는 미취득 null
    DP: str | None = None


class ArenaClassByStyle(BaseModel):
    SP: str | None = None  # 원본 문자열 (미취득 "---")
    DP: str | None = None


class IidxProfileUpload(BaseModel):
    """POST /iidx/scores/upload 의 profile 폼 필드(JSON) — 크롤러 Profile."""

    communityNickname: str | None = None
    djName: str | None = None
    iidxId: str | None = None
    playCount: int | None = None
    notesRadar: NotesRadar | None = None
    dan: DanByStyle | None = None
    arenaClass: ArenaClassByStyle | None = None
