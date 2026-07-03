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
