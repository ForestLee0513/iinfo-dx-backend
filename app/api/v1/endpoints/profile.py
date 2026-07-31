"""사용자 프로필 조회/수정 API (웹 클라이언트).

- GET /{identifier} — 인증 불필요. identifier는 UUID(user_id) 또는 handle
  둘 다 받는다(둘 다 아닌 형식이면 조회 없이 404). user_profiles 테이블에
  있는 데이터만 응답한다 — email 등 auth.users 정보는 본인 조회(is_mine)일
  때만 요청 토큰의 클레임에서 채워 넣는다(타인의 이메일은 노출하지 않는다).
  본인 프로필 조회도 같은 엔드포인트로 처리한다(identifier에 자기 자신의
  id/handle을 넣어 호출).
- PATCH /me — 인증 필수. handle/social_links만 본인이 직접 수정할 수 있다.
  dj_name/dj_id/profile_image_url은 API로 수정하지 않는다(DB
  직접 등록, 추후 북마크릿 데이터 갱신 파이프라인이 채운다).
"""

import uuid

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, OptionalIdentity
from app.core.openapi import PUBLIC
from app.crud import crud_profiles
from app.crud.crud_profiles import HandleTakenError
from app.schemas.profile import HANDLE_PATTERN, ProfileResponse, ProfileUpdateRequest
from app.schemas.user import UserRole

router = APIRouter()


def _to_response(
    row: dict, *, is_mine: bool, email: str | None, provider: str | None
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
    """
    try:
        uuid.UUID(identifier)
    except ValueError:
        row = (
            crud_profiles.get_profile_row_by_handle(identifier)
            if HANDLE_PATTERN.match(identifier)
            else None
        )
    else:
        row = crud_profiles.get_profile_row(identifier)

    if row is None:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    user_id = row["user_id"]
    is_mine = identity is not None and identity.id == user_id
    if not row["is_public"] and not is_mine:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다.")

    return _to_response(
        row,
        is_mine=is_mine,
        email=identity.email if is_mine else None,
        provider=identity.provider if is_mine else None,
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

    return _to_response(row, is_mine=True, email=user.email, provider=user.provider)
