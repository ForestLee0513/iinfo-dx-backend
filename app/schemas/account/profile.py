"""사용자 프로필 조회/수정(웹 클라이언트) API 모델.

프로필은 public.profiles(handle/social_links/is_public/profile_image_url)와
iidx.profiles(dj_name/dj_id)에 걸쳐 있으나, 응답은 단일 프로필로 합쳐 내려준다
(crud_profiles가 합성). dj_name/dj_id/profile_image_url은 요청 모델에 없다 —
사용자가 API로 직접 수정하지 않는 필드다(DB 직접 등록, 추후 북마크릿 데이터 갱신
파이프라인이 채운다).
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
    """GET /profile/{identifier} 응답.

    email/provider는 본인 조회(is_mine=True)일 때만 채워진다 — 요청자의 인증
    토큰 클레임에서 가져오며, 타인의 이메일은 노출하지 않는다.
    """

    id: str
    handle: str | None = None
    role: UserRole = UserRole.USER
    is_public: bool = True
    social_links: list[SocialLink] = Field(default_factory=list)
    dj_name: str | None = None
    dj_id: str | None = None
    profile_image_url: str | None = None
    updated_at: datetime | None = None
    is_mine: bool = False
    email: str | None = None
    provider: str | None = None
    followers_count: int = 0
    following_count: int = 0
    # 익명 요청이거나 본인 프로필을 볼 때는 의미가 없으므로 None
    is_following: bool | None = None


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
    전체로 치환된다(부분 추가/삭제가 아니라 통째로 교체).
    """

    handle: str | None = None
    social_links: list[SocialLink] | None = None

    @field_validator("handle")
    @classmethod
    def _validate_handle(cls, v: str | None) -> str | None:
        if v is not None and not HANDLE_PATTERN.match(v):
            raise ValueError("handle은 영문/숫자/밑줄 2~20자여야 합니다.")
        return v
