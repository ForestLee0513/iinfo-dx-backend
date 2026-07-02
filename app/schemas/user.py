from pydantic import BaseModel


class AuthUser(BaseModel):
    """Supabase 액세스 토큰 클레임에서 추출한 인증 사용자 정보."""

    id: str  # Supabase user UUID (sub 클레임)
    email: str | None = None
    provider: str | None = None  # "google" | "email" 등
    role: str | None = None  # 일반적으로 "authenticated"
