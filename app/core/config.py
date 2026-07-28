from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "iinfo-dx-backend"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "local"
    DEBUG: bool = True
    TIMEZONE: str = "Asia/Seoul"

    # Supabase 인증 설정
    # SUPABASE_URL: https://<project-ref>.supabase.co
    # SUPABASE_JWT_SECRET: 레거시 HS256 프로젝트에서만 설정.
    #   비워두면 JWKS 엔드포인트(비대칭 키, 권장)로 검증한다.
    # SUPABASE_SERVICE_ROLE_KEY: 크롤링 결과 동기화(쓰기)에 사용.
    #   RLS를 우회하므로 서버 환경변수로만 관리할 것.
    SUPABASE_URL: str = ""
    SUPABASE_JWT_SECRET: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    # 백엔드 주도 OAuth 허용 프로바이더 (.env에서 JSON 배열 한 줄).
    # 애플 등 추가 시 OAUTH_PROVIDERS=["google","apple"]처럼 이름만 늘리면 된다
    # — 코드 수정 불필요. 단 Supabase 대시보드에서 해당 프로바이더 활성화와
    # 콜백 URL(Redirect URLs) 등록이 선행돼야 한다.
    OAUTH_PROVIDERS: list[str] = ["google"]

    # OAuth 로그인 완료 후 브라우저를 돌려보낼 FE URL은 .env가 아니라 FE가
    # /auth/login/{provider}?redirect= 로 넘긴다. 이 허용 목록의 오리진과 일치하는
    # redirect만 사용하고, 그 외(미지정·불일치)에는 목록의 첫 URL(홈)으로 돌려보낸다
    # (open redirect 방지). 지금은 로컬 FE만 허용 — 운영 도메인은 여기에 추가한다.
    OAUTH_ALLOWED_REDIRECT_URLS: list[str] = ["http://localhost:3000"]

    # 어드민 전용 로그인(/admin/auth)의 OAuth 완료 후 허용 리다이렉트 URL 목록.
    # 어드민 FE는 사용자 클라이언트와 별도 오리진이므로 위 목록과 분리한다
    # (.env에서 JSON 배열 한 줄). 지금은 로컬 어드민 FE만 허용 — 운영 도메인은 여기에 추가.
    ADMIN_ALLOWED_REDIRECT_URLS: list[str] = ["http://localhost:3001"]

    # 인증 쿠키(refresh token 등) SameSite 정책.
    # FE가 API와 다른 사이트(등록 도메인 자체가 다름)에서 붙으면 "none"으로
    # (Secure가 자동 강제됨). 같은 도메인/서브도메인 구성이면 "lax"면 충분하다.
    AUTH_COOKIE_SAMESITE: str = "lax"

    # 크롤링 설정
    REQUEST_TIMEOUT: int = 15
    MAX_CONCURRENT: int = 10

    # 크롤 대상(곡 마스터/난이도표)과 스케줄은 어드민 API로 관리하며 Supabase
    # (crawl_targets, crawl_schedules 테이블)에 저장된다.
    # 대상 등록: POST /api/v1/crawl/targets. 스케줄 설정: PUT /api/v1/crawl/schedules/{target_key}.
    # 배포 직후에는 등록된 대상/스케줄이 하나도 없으므로 어드민이 등록하기 전까지
    # 아무 것도 자동 크롤되지 않는다.

    # Redis — 크롤 작업 실행 상태(재기동 시 이어하기)
    REDIS_URL: str = "redis://localhost:6379/0"

    # 별도 FE(클라이언트/어드민) 오리진 CORS 허용 목록 (.env에서 JSON 배열 한 줄)
    CORS_ORIGINS: list[str] = []

    # 어드민 권한은 env가 아니라 Supabase 계정의 app_metadata.role로 판정한다
    # (app/schemas/user.py UserRole). 구 ADMIN_EMAILS 등 잔여 env 키는 무시(extra="ignore").
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
