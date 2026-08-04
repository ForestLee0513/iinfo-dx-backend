"""사용자 프로필 조회/수정(웹 클라이언트) API 모델.

ProfileResponse는 플랫폼 수준 공통 프로필(public.profiles)만 담는다.
서비스 전용 필드(dj_name/dj_id 등)는 IidxProfileResponse처럼 서비스별 응답에만 포함된다.
요청 모델에 dj_name/dj_id/profile_image_url은 없다 — API로 직접 수정하지 않는 필드다.
"""

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.account.user import UserRole

# DB의 user_profiles_handle_format_chk 제약과 동일한 규칙(영문/숫자/밑줄 2~20자)
HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_]{2,20}$")


class SocialLink(BaseModel):
    """소셜 링크 1건 — {platform, url}."""

    platform: str = Field(..., min_length=1, max_length=30)
    url: str = Field(..., min_length=1, max_length=500)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
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


class IidxProfileResponse(ProfileResponse):
    """GET /profile/iidx/{identifier} 응답 — 플랫폼 프로필 + IIDX 서비스 전용 필드.

    is_public은 플랫폼 수준, iidx_is_public은 IIDX 서비스 수준 공개 여부다.
    가시성 게이트는 iidx_is_public 기준.
    """

    iidx_is_public: bool = True
    dj_name: str | None = None
    dj_id: str | None = None


class IidxProfileUpdateRequest(BaseModel):
    """PATCH /profile/iidx/me 요청 — 명시적으로 보낸 필드만 갱신한다(부분 업데이트)."""

    is_public: bool | None = None


class IidxProfileSyncRequest(BaseModel):
    """POST /profile/iidx/me/sync 요청 — 북마크릿이 수집한 IIDX 프로필 데이터."""

    dj_name: str | None = None
    dj_id: str | None = None


class FollowUserSummary(BaseModel):
    """팔로워/팔로잉 목록의 사용자 1명 요약."""

    id: str
    handle: str | None = None
    profile_image_url: str | None = None


class FollowListResponse(BaseModel):
    """GET /profile/{identifier}/followers, /following 공통 응답."""

    users: list[FollowUserSummary]
    page: int
    per_page: int
    total: int


class ProfileUpdateRequest(BaseModel):
    """PATCH /profile/me 요청 — 명시적으로 보낸 필드만 갱신한다(부분 업데이트).

    handle을 null로 보내면 핸들을 해제(release)한다. social_links는 보낸 목록
    전체로 치환된다(부분 추가/삭제가 아니라 통째로 교체). is_public은 프로필
    공개 여부를 전환한다.
    """

    handle: str | None = None
    social_links: list[SocialLink] | None = None
    is_public: bool | None = None

    @field_validator("handle")
    @classmethod
    def _validate_handle(cls, v: str | None) -> str | None:
        if v is not None and not HANDLE_PATTERN.match(v):
            raise ValueError("handle은 영문/숫자/밑줄 2~20자여야 합니다.")
        return v
