"""사용자 프로필 조회/수정/팔로우 API (웹 클라이언트).

- GET /{identifier} — 인증 불필요. identifier는 UUID(user_id) 또는 handle
  둘 다 받는다(둘 다 아닌 형식이면 조회 없이 404). user_profiles 테이블에
  있는 데이터만 응답한다 — email 등 auth.users 정보는 본인 조회(is_mine)일
  때만 요청 토큰의 클레임에서 채워 넣는다(타인의 이메일은 노출하지 않는다).
  본인 프로필 조회도 같은 엔드포인트로 처리한다(identifier에 자기 자신의
  id/handle을 넣어 호출).
- PATCH /me — 인증 필수. handle/social_links만 본인이 직접 수정할 수 있다.
  dj_name/dj_id/profile_image_url은 API로 수정하지 않는다(DB 직접 등록,
  추후 북마크릿 데이터 갱신 파이프라인이 채운다).
- POST/DELETE /{identifier}/follow — 인증 필수. 팔로우/언팔로우(둘 다 멱등).
- GET /{identifier}/followers, /following — 인증 불필요(옵셔널). 대상
  프로필이 비공개면 본인만 조회 가능 — GET /{identifier}와 동일한 규칙.
"""

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, OptionalIdentity, TokenIdentity
from app.core.openapi import PUBLIC
from app.crud.account import follows as crud_follows, profiles as crud_profiles
from app.crud.account.profiles import HandleTakenError
from app.schemas.account.profile import (
    HANDLE_PATTERN,
    FollowListResponse,
    FollowUserSummary,
    ProfileResponse,
    ProfileUpdateRequest,
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


def _require_visible_row(
    identifier: str, identity: TokenIdentity | None
) -> tuple[dict, bool]:
    """대상 프로필을 찾아 (row, is_mine)을 반환한다. 없거나 비공개면 404."""
    row = _resolve_row(identifier)
    if row is None:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")
    user_id = row["user_id"]
    is_mine = identity is not None and identity.id == user_id
    if not row["is_public"] and not is_mine:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")
    return row, is_mine


def _to_response(
    row: dict,
    *,
    is_mine: bool,
    email: str | None,
    provider: str | None,
    followers_count: int,
    following_count: int,
    is_following: bool | None,
) -> ProfileResponse:
    return ProfileResponse(
        id=row["user_id"],
        handle=row.get("handle"),
        role=UserRole(row.get("role", "USER")),
        is_public=bool(row["is_public"]),
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
    summary="프로필 조회 (UUID 또는 handle)",
    response_model=ProfileResponse,
    openapi_extra=PUBLIC,
)
def get_profile(identifier: str, identity: OptionalIdentity):
    """user_profiles 기준 프로필 조회.

    - identifier가 UUID면 user_id로, 아니면 handle로 조회한다(UUID도 handle
      패턴도 아니면 DB 조회 없이 바로 404).
    - 프로필 행이 없으면(가입 트리거 도입 이전 계정 등) 404.
    - is_public=False인 비공개 프로필은 본인만 조회 가능 — 그 외엔 존재 여부를
      노출하지 않기 위해 403 대신 404.
    - 요청에 유효한 Authorization 토큰이 있고 그 sub가 조회된 user_id와 같으면
      is_mine=true와 함께 email/provider를 채운다.
    - is_following은 로그인한 타인이 볼 때만 값이 채워진다(익명/본인 조회는 null).
    """
    row, is_mine = _require_visible_row(identifier, identity)
    user_id = row["user_id"]
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
    summary="내 프로필 수정 (handle/social_links)",
    response_model=ProfileResponse,
    openapi_extra=PUBLIC,
)
def update_profile(body: ProfileUpdateRequest, user: CurrentUser):
    """본인 프로필 중 handle/social_links만 수정한다(둘 다 부분 업데이트).

    요청 본문에 없는 필드는 그대로 유지된다. handle을 null로 보내면 핸들을
    해제하고, 이미 다른 사용자가 쓰는 handle이면 409를 반환한다.
    """
    fields = body.model_fields_set
    kwargs = {}
    if "handle" in fields:
        kwargs["handle"] = body.handle
    if "social_links" in fields:
        kwargs["social_links"] = (
            [link.model_dump() for link in body.social_links]
            if body.social_links is not None
            else []
        )

    if kwargs:
        try:
            row = crud_profiles.update_editable_fields(user.id, **kwargs)
        except HandleTakenError:
            raise HTTPException(status_code=409, detail="이미 사용 중인 handle입니다.")
    else:
        row = crud_profiles.get_profile_row(user.id) or {
            "user_id": user.id,
            "is_public": user.is_public,
            "role": user.app_role.value,
        }

    return _to_response(
        row,
        is_mine=True,
        email=user.email,
        provider=user.provider,
        followers_count=crud_follows.followers_count(user.id),
        following_count=crud_follows.following_count(user.id),
        is_following=None,
    )


@router.post(
    "/{identifier}/follow",
    summary="팔로우",
    status_code=204,
    openapi_extra=PUBLIC,
)
def follow_user(identifier: str, user: CurrentUser):
    """identifier가 가리키는 사용자를 팔로우한다. 이미 팔로우 중이면 그대로 성공(멱등)."""
    target = _resolve_row(identifier)
    if target is None:
        raise HTTPException(status_code=404, detail="대상을 찾을 수 없습니다.")
    if target["user_id"] == user.id:
        raise HTTPException(status_code=400, detail="자기 자신은 팔로우할 수 없습니다.")
    crud_follows.follow(user.id, target["user_id"])


@router.delete(
    "/{identifier}/follow",
    summary="언팔로우",
    status_code=204,
    openapi_extra=PUBLIC,
)
def unfollow_user(identifier: str, user: CurrentUser):
    """identifier가 가리키는 사용자를 언팔로우한다. 팔로우 중이 아니었어도 성공(멱등)."""
    target = _resolve_row(identifier)
    if target is None:
        raise HTTPException(status_code=404, detail="대상을 찾을 수 없습니다.")
    crud_follows.unfollow(user.id, target["user_id"])


def _to_follow_list(
    ids: list[str], total: int, page: int, per_page: int
) -> FollowListResponse:
    summaries = crud_profiles.get_profile_summaries(ids)
    users = [
        FollowUserSummary(
            id=uid,
            handle=summaries.get(uid, {}).get("handle"),
            profile_image_url=summaries.get(uid, {}).get("profile_image_url"),
        )
        for uid in ids
    ]
    return FollowListResponse(users=users, page=page, per_page=per_page, total=total)


@router.get(
    "/{identifier}/followers",
    summary="팔로워 목록",
    response_model=FollowListResponse,
    openapi_extra=PUBLIC,
)
def get_followers(
    identifier: str,
    identity: OptionalIdentity,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    row, _ = _require_visible_row(identifier, identity)
    ids, total = crud_follows.list_followers(row["user_id"], page, per_page)
    return _to_follow_list(ids, total, page, per_page)


@router.get(
    "/{identifier}/following",
    summary="팔로잉 목록",
    response_model=FollowListResponse,
    openapi_extra=PUBLIC,
)
def get_following(
    identifier: str,
    identity: OptionalIdentity,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    row, _ = _require_visible_row(identifier, identity)
    ids, total = crud_follows.list_following(row["user_id"], page, per_page)
    return _to_follow_list(ids, total, page, per_page)
