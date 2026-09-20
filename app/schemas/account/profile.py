"""사용자 프로필 조회/수정(웹 클라이언트) API 모델.

ProfileResponse는 플랫폼 수준 공통 프로필(public.profiles)만 담는다.
서비스 전용 필드(dj_name/dj_id 등)는 IidxProfileResponse처럼 서비스별 응답에만 포함된다.
요청 모델에 dj_name/dj_id/profile_image_url은 없다 — API로 직접 수정하지 않는 필드다.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.account.user import UserRole

# DB의 profiles_handle_format_chk 제약과 동일한 규칙.
# 소문자 영문/숫자/밑줄/마침표만 허용하며, 마침표는 연속으로 쓸 수 없다.
HANDLE_PATTERN = re.compile(r"\A(?!.*\.\.)[a-z0-9_.]{2,20}\Z")
# 조회는 기존 handle 및 사용자 입력을 위해 대소문자를 구분하지 않는다. 저장 시에는
# HANDLE_PATTERN만 사용해 소문자 규칙을 계속 강제한다.
HANDLE_LOOKUP_PATTERN = re.compile(r"\A(?!.*\.\.)[A-Za-z0-9_.]{2,20}\Z")


class SocialLink(BaseModel):
    """소셜 링크 1건 — {platform, url}."""

    platform: str = Field(..., min_length=1, max_length=30)
    # PATCH에서 platform만 전송하면 해당 플랫폼의 기존 URL을 유지한다.
    url: str = Field("", max_length=500)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return ""
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url은 http(s):// 로 시작해야 합니다.")
        return v


class ProfileResponse(BaseModel):
    """GET /profile/{identifier} 응답 — 플랫폼 수준 공통 프로필.

    서비스 전용 필드(dj_name/dj_id 등)는 포함하지 않는다. 서비스 프로필이
    필요하면 /profile/{service}/{identifier} 엔드포인트를 사용할 것.
    email/provider는 본인 조회(is_mine=True)일 때만 채워진다.
    """

    id: str
    handle: str | None = None
    nickname: str | None = None
    role: UserRole = UserRole.USER
    is_public: bool = True
    social_links: list[SocialLink] = Field(default_factory=list)
    profile_image_url: str | None = None
    updated_at: datetime | None = None
    is_mine: bool = False
    email: str | None = None
    provider: str | None = None
    followers_count: int = 0
    following_count: int = 0
    # 익명 요청이거나 본인 프로필을 볼 때는 의미가 없으므로 None
    is_following: bool | None = None
    joined_services: list[str] = Field(default_factory=list)
    # 가입한 서비스의 개별 프로필 공개 여부. 키는 서비스명(예: iidx)이다.
    service_visibility: dict[str, bool] = Field(default_factory=dict)


class IidxProfileResponse(ProfileResponse):
    """GET /profile/iidx/{identifier} 응답 — 플랫폼 프로필 + IIDX 서비스 전용 필드.

    is_public은 플랫폼 수준, iidx_is_public은 IIDX 서비스 수준 공개 여부다.
    가시성 게이트는 iidx_is_public 기준.
    """

    iidx_is_public: bool = True
    dj_name: str | None = None
    dj_id: str | None = None
    # 북마크릿 프로필 크롤 필드 (미크롤 시 null). 상세 구조는 크롤러 Profile 타입 참고.
    community_nickname: str | None = None
    play_count: int | None = None
    notes_radar: dict | None = None   # {SP:{...}, DP:{...}}
    dan: dict | None = None           # {SP:"10TH_DAN"|null, DP:...}
    arena_class: dict | None = None   # {SP:"B4", DP:"---"}


class IidxProfileUpdateRequest(BaseModel):
    """PATCH /profile/iidx/me 요청 — 명시적으로 보낸 필드만 갱신한다(부분 업데이트)."""

    is_public: bool | None = None


class FollowUserSummary(BaseModel):
    """팔로워/팔로잉 목록의 사용자 1명 요약."""

    id: str
    handle: str | None = None
    nickname: str | None = None
    profile_image_url: str | None = None


class ProfileSearchSuggestion(BaseModel):
    """프로필 검색 자동완성 항목 — profile_path로 해당 서비스 화면으로 이동한다."""

    id: str
    handle: str | None = None
    nickname: str | None = None
    dj_name: str | None = None
    dj_id: str | None = None
    profile_image_url: str | None = None
    profile_path: str


class ProfileSearchResponse(BaseModel):
    """GET /profile/search 응답 — 검색어와 자동완성 후보 목록."""

    query: str
    service: Literal["iidx", "iinfo_dx"]
    results: list[ProfileSearchSuggestion] = Field(default_factory=list)


class FollowListResponse(BaseModel):
    """GET /profile/{identifier}/followers, /following 공통 응답."""

    users: list[FollowUserSummary]
    page: int
    per_page: int
    total: int


class ProfileUpdateRequest(BaseModel):
    """PATCH /profile/me 요청 — 명시적으로 보낸 필드만 갱신한다(부분 업데이트).

    handle은 아직 없는 경우에만 최초 설정할 수 있고, 한 번 지정하면 변경하거나
    해제할 수 없다. nickname은 handle과 달리 유일하지 않아도 되는 일반 표시용
    닉네임이며, null이거나 공백으로만 채워 보내면(비움) handle로 대체되어 저장된다
    (handle이 아직 없으면 null로 저장된다).
    social_links는 보낸 목록 전체를 기준으로 갱신한다. 단, URL을 생략하거나 빈
    문자열로 보낸 플랫폼은 기존 URL을 유지한다.
    is_public은 플랫폼 프로필 공개 여부를 전환한다. service_visibility는
    서비스명별 공개 여부를 한 번에 전환한다. 가입하지 않은 서비스 키는 무시한다.
    """

    handle: str | None = None
    nickname: str | None = None
    social_links: list[SocialLink] | None = None
    is_public: bool | None = None
    service_visibility: dict[str, bool] | None = None

    @field_validator("handle")
    @classmethod
    def _validate_handle(cls, v: str | None) -> str | None:
        if v is not None and not HANDLE_PATTERN.match(v):
            raise ValueError(
                "handle은 소문자 영문/숫자/밑줄/마침표만 사용한 2~20자여야 하며, "
                "연속 마침표는 사용할 수 없습니다."
            )
        return v

    @field_validator("nickname")
    @classmethod
    def _validate_nickname(cls, v: str | None) -> str | None:
        # 공백만 있는 값은 "닉네임 비움"으로 취급한다 — 라우터에서 handle로 대체된다.
        if v is None:
            return v
        v = v.strip()
        if v and len(v) > 20:
            raise ValueError("nickname은 최대 20자여야 합니다.")
        return v or None
