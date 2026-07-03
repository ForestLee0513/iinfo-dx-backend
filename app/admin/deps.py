"""어드민 전용 인증 의존성.

어드민 여부는 Supabase 계정의 app_metadata.role(= AuthUser.app_role)로 판정한다.
권한 부여는 Supabase에서 처리 (대시보드 또는 SQL — README '어드민 권한 부여' 참고).
역할 체계가 확장되면 UserRole/ROLE_LEVELS(app/common/schemas.py)에만 추가하면 된다.
"""

from typing import Annotated

from fastapi import Depends

from app.common.auth import require_role
from app.common.schemas import AuthUser, UserRole

get_admin_user = require_role(UserRole.ADMIN)

AdminUser = Annotated[AuthUser, Depends(get_admin_user)]
