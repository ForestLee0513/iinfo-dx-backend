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
    # 크롤링 대상 목록. .env에서 JSON 배열 문자열로 설정한다.
    # 예: CRAWL_TARGETS=[{"crawler":"5ch_sheet","url":"https://...","play_style":"SP","level":12}]
    CRAWL_TARGETS: list[dict] = []

    # 주간 크롤링 스케줄 (기본: 매주 월요일 05:00, TIMEZONE 기준)
    CRAWL_SCHEDULE_ENABLED: bool = True
    CRAWL_SCHEDULE_DAY: str = "mon"  # mon/tue/wed/thu/fri/sat/sun
    CRAWL_SCHEDULE_HOUR: int = 5
    CRAWL_SCHEDULE_MINUTE: int = 0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
