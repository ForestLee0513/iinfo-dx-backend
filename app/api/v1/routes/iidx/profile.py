"""IIDX 서비스 프로필 조회/수정 API.

- GET /{identifier} — 인증 불필요(옵셔널). iidx.profiles.is_public 기준으로
  비공개면 본인만 조회 가능. iidx.profiles 행이 없으면(미온보딩) 404.
- PATCH /me — 인증 필수. iidx.profiles.is_public(IIDX 서비스 공개 여부)을
  수정한다. 미온보딩(iidx.profiles 행 없음) 상태면 404.

플랫폼 수준 공개 여부(public.profiles.is_public)는 PATCH /profile/me에서 제어.
IIDX 서비스 수준 공개 여부(iidx.profiles.is_public)는 이 라우터에서 제어.
"""

import uuid

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, OptionalIdentity
from app.core.openapi import PUBLIC
from app.crud.account import follows as crud_follows, profiles as crud_profiles
from app.crud.account.profiles import NotMemberError
from app.schemas.account.profile import (
    HANDLE_PATTERN,
    IidxProfileResponse,
    IidxProfileUpdateRequest,
)
from app.schemas.account.user import UserRole

router = APIRouter()


def _resolve_row(identifier: str) -> dict | None:
    """identifier(UUID 또는 handle)로 user_profiles 행을 찾는다. 없으면 None."""
    try:
        uuid.UUID(identifier)
    except ValueError:
        if not HANDLE_PATTERN.match(identifier):
            return None
        return crud_profiles.get_profile_row_by_handle(identifier)
    return crud_profiles.get_profile_row(identifier)


def _to_response(
    row: dict,
    *,
    is_mine: bool,
    email: str | None,
    provider: str | None,
    followers_count: int,
    following_count: int,
    is_following: bool | None,
) -> IidxProfileResponse:
    return IidxProfileResponse(
        id=row["user_id"],
        handle=row.get("handle"),
        role=UserRole(row.get("role", "USER")),
        is_public=bool(row["is_public"]),
        iidx_is_public=bool(row.get("iidx_is_public", True)),
        social_links=row.get("social_links") or [],
        dj_name=row.get("dj_name"),
        dj_id=row.get("dj_id"),
        profile_image_url=row.get("profile_image_url"),
        updated_at=row.get("updated_at"),
        is_mine=is_mine,
        email=email if is_mine else None,
        provider=provider if is_mine else None,
        followers_count=followers_count,
        following_count=following_count,
        is_following=is_following,
    )


@router.get(
    "/{identifier}",
    summary="IIDX 서비스 프로필 조회 (UUID 또는 handle)",
    response_model=IidxProfileResponse,
    openapi_extra=PUBLIC,
)
def get_iidx_profile(identifier: str, identity: OptionalIdentity):
    """IIDX 서비스 프로필 조회.

    - iidx.profiles 행이 없으면(미온보딩) 404.
    - iidx_is_public=False인 비공개 프로필은 본인만 조회 가능(404로 은닉).
    - 플랫폼 공개 여부(is_public)와 별개로, IIDX 서비스 공개 여부(iidx_is_public)로
      가시성을 제어한다.
    """
    row = _resolve_row(identifier)
    if row is None:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    user_id = row["user_id"]
    if not crud_profiles.is_iidx_member(user_id):
        raise HTTPException(status_code=404, detail="IIDX 서비스에 가입하지 않은 사용자입니다.")

    is_mine = identity is not None and identity.id == user_id
    if not row.get("iidx_is_public", True) and not is_mine:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    is_following = (
        crud_follows.is_following(identity.id, user_id)
        if identity is not None and not is_mine
        else None
    )
    return _to_response(
        row,
        is_mine=is_mine,
        email=identity.email if is_mine else None,
        provider=identity.provider if is_mine else None,
        followers_count=crud_follows.followers_count(user_id),
        following_count=crud_follows.following_count(user_id),
        is_following=is_following,
    )


@router.patch(
    "/me",
    summary="내 IIDX 서비스 프로필 공개 여부 수정",
    response_model=IidxProfileResponse,
    openapi_extra=PUBLIC,
)
def update_iidx_profile(body: IidxProfileUpdateRequest, user: CurrentUser):
    """IIDX 서비스 프로필의 공개 여부(is_public)를 수정한다(부분 업데이트).

    iidx.profiles 행이 없으면(미온보딩) 404를 반환한다.
    플랫폼 수준 공개 여부 수정은 PATCH /profile/me를 사용할 것.
    """
    fields = body.model_fields_set
    kwargs = {}
    if "is_public" in fields:
        kwargs["is_public"] = body.is_public

    try:
        row = crud_profiles.update_iidx_editable_fields(user.id, **kwargs)
    except NotMemberError:
        raise HTTPException(status_code=404, detail="IIDX 서비스에 가입하지 않은 사용자입니다.")

    return _to_response(
        row,
        is_mine=True,
        email=user.email,
        provider=user.provider,
        followers_count=crud_follows.followers_count(user.id),
        following_count=crud_follows.following_count(user.id),
        is_following=None,
    )
