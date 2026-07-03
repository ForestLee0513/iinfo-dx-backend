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

    # OAuth 로그인 완료 후 브라우저를 돌려보낼 FE URL.
    # 설정하면 /auth/callback이 JSON 대신 이 주소로 303 리다이렉트한다
    # (FE는 랜딩 후 POST /auth/refresh로 access token을 받는다).
    # 비워두면 콜백이 세션 JSON을 그대로 반환한다 (개발용).
    AUTH_SUCCESS_REDIRECT_URL: str = ""

    # 인증 쿠키(refresh token 등) SameSite 정책.
    # FE가 API와 다른 사이트(등록 도메인 자체가 다름)에서 붙으면 "none"으로
    # (Secure가 자동 강제됨). 같은 도메인/서브도메인 구성이면 "lax"면 충분하다.
    AUTH_COOKIE_SAMESITE: str = "lax"

    # 크롤링 설정
    REQUEST_TIMEOUT: int = 15
    MAX_CONCURRENT: int = 10
    # 곡 마스터 크롤 타깃 (.env에서 JSON 배열 한 줄).
    # 예: SONG_CRAWL_TARGETS=[{"crawler":"textage"}]
    SONG_CRAWL_TARGETS: list[dict] = [{"crawler": "textage"}]
    # 난이도표 크롤 타깃 (.env에서 JSON 배열 한 줄, 구 CRAWL_TARGETS).
    # 예: TABLE_CRAWL_TARGETS=[{"crawler":"5ch_sheet","url":"https://...","play_style":"SP","level":12}]
    TABLE_CRAWL_TARGETS: list[dict] = []

    # 주간 크롤링 스케줄 (곡 마스터 → 난이도표 순차, 기본: 매주 월요일 05:00, TIMEZONE 기준)
    # 어드민 API로 변경하면 Redis에 저장되어 아래 기본값보다 우선한다.
    CRAWL_SCHEDULE_ENABLED: bool = True
    CRAWL_SCHEDULE_DAY: str = "mon"  # mon/tue/wed/thu/fri/sat/sun
    CRAWL_SCHEDULE_HOUR: int = 5
    CRAWL_SCHEDULE_MINUTE: int = 0

    # Redis — 크롤 작업 상태(재기동 시 이어하기) + 스케줄 오버라이드 저장
    REDIS_URL: str = "redis://localhost:6379/0"

    # 별도 FE(클라이언트/어드민) 오리진 CORS 허용 목록 (.env에서 JSON 배열 한 줄)
    CORS_ORIGINS: list[str] = []

    # 어드민 권한은 env가 아니라 Supabase 계정의 app_metadata.role로 판정한다
    # (app/common/schemas.py UserRole). 구 ADMIN_EMAILS 등 잔여 env 키는 무시(extra="ignore").
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
