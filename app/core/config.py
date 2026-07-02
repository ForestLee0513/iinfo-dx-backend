from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "iinfo-dx-backend"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "local"
    DEBUG: bool = True

    # Supabase 인증 설정
    # SUPABASE_URL: https://<project-ref>.supabase.co
    # SUPABASE_JWT_SECRET: 레거시 HS256 프로젝트에서만 설정.
    #   비워두면 JWKS 엔드포인트(비대칭 키, 권장)로 검증한다.
    SUPABASE_URL: str = ""
    SUPABASE_JWT_SECRET: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
