# iinfo-dx-backend

FastAPI 기반 백엔드입니다. 로컬 실행과 Docker 실행을 모두 지원하며, **Supabase Auth**(구글 OAuth / 이메일 로그인) 기반 인증과 **난이도표 크롤링 → Supabase 주간 동기화** 기능을 제공합니다.

## 기술 스택

- **Python** 3.12
- **FastAPI** 0.115.x / **Uvicorn** (ASGI 서버)
- **Pydantic v2** / **pydantic-settings** (스키마 및 환경설정)
- **PyJWT** (Supabase 액세스 토큰 검증)
- **httpx** + **BeautifulSoup4** (크롤링/파싱)
- **supabase-py** (크롤링 결과 동기화)
- **APScheduler** (주 1회 스케줄링)

## 프로젝트 구조

```
.
├── app/
│   ├── main.py              # FastAPI 앱 엔트리포인트 (lifespan에서 스케줄러 기동)
│   ├── core/
│   │   └── config.py        # 환경설정 (.env 로드)
│   ├── api/
│   │   ├── deps.py          # 공통 의존성 (Supabase 토큰 검증)
│   │   ├── router.py        # API 라우터 집합 (/api/v1) — 공개/인증필수 구분
│   │   └── endpoints/
│   │       ├── health.py    # 헬스체크 (공개)
│   │       ├── auth.py      # 내 정보 조회 (인증 필수)
│   │       └── crawl.py     # 크롤링 수동 동기화/미리보기 (인증 필수)
│   ├── crawlers/            # 크롤러 레지스트리
│   │   ├── base.py          # TableDef/TableResult/레지스트리
│   │   ├── sheet_5ch.py     # 5ch Google Sheets 크롤러 (GRADE)
│   │   └── numeric_example.py # 숫자형 표 크롤러 템플릿 (NUMERIC)
│   ├── parsers/
│   │   └── sheet_parser.py  # pubhtml 파싱 (지력/개인차 판별)
│   ├── services/
│   │   ├── crawl_service.py # 크롤링 → 동기화 파이프라인 (run_full_sync)
│   │   ├── supabase_sync.py # sync_table_result RPC 호출 (service role)
│   │   └── scheduler.py     # APScheduler 주간 크론
│   └── schemas/
│       └── user.py          # 인증 사용자 스키마
├── supabase/
│   └── migrations/          # Supabase 마이그레이션 SQL
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
└── .env.example             # 환경변수 템플릿
```

## 인증 구조 (Supabase Auth)

로그인/회원가입은 **프론트엔드가 Supabase와 직접** 처리합니다. 백엔드는 요청 헤더의 액세스 토큰을 검증만 합니다.

```
[프론트엔드] --(구글 OAuth / 이메일 로그인)--> [Supabase Auth] --> access_token 발급
[프론트엔드] --(Authorization: Bearer <access_token>)--> [이 백엔드]
[백엔드] 토큰 서명·만료·발급자 검증 후 사용자 정보 추출 (app/api/deps.py)
```

- 구글 로그인: `supabase.auth.signInWithOAuth({ provider: "google" })`
- 이메일 로그인: `supabase.auth.signInWithPassword({ email, password })`
- 두 방식 모두 동일한 형식의 액세스 토큰이 발급되며, 백엔드 검증 로직은 동일합니다. 로그인 수단은 토큰의 `app_metadata.provider` 클레임으로 구분됩니다.

### 토큰 검증 방식

| 프로젝트 유형 | 설정 | 검증 방식 |
|--------------|------|----------|
| 신규 (비대칭 키, 권장) | `SUPABASE_JWT_SECRET` 비워둠 | JWKS 엔드포인트 공개 키 (RS256/ES256) |
| 레거시 (대칭 키) | `SUPABASE_JWT_SECRET` 설정 | JWT Secret (HS256) |

### 공개 API vs 인증 필수 API

`app/api/router.py`에서 라우터 단위로 구분합니다:

```python
# 공개 API — dependencies 없이 등록
api_router.include_router(health.router, prefix="/health", tags=["Health"])

# 인증 필수 API — get_current_user를 dependencies에 추가하면 라우터 전체가 보호됨
api_router.include_router(
    example.router, prefix="/example", tags=["Example"],
    dependencies=[Depends(get_current_user)],
)
```

개별 엔드포인트에서 사용자 정보가 필요하면 `CurrentUser` 타입을 파라미터로 받습니다:

```python
from app.api.deps import CurrentUser

@router.get("/me")
def read_current_user(current_user: CurrentUser):
    return current_user  # id(UUID), email, provider, role
```

| 엔드포인트 | 인증 |
|-----------|------|
| `GET /api/v1/health` | 공개 |
| `GET /api/v1/tables` | 공개 |
| `GET /api/v1/tables/{slug}` | 공개 |
| `GET /api/v1/crawl/targets` | 공개 |
| `GET /api/v1/crawl/preview` | 공개 |
| `GET /api/v1/auth/me` | 인증 필수 |

## 크롤링 & 주간 동기화

난이도표를 크롤링해 Supabase에 반영하는 파이프라인입니다. 반영은 **주간 스케줄러로만** 이뤄지며 수동 트리거 엔드포인트는 없습니다(수동 갱신은 별도 레포에서 처리). 프론트엔드는 공개 `GET /api/v1/tables` 로 결과를 조회합니다.

```
[APScheduler 주 1회 크론] ──→ run_full_sync
    → CRAWL_TARGETS 순회 → 타깃의 "crawler" 값으로 크롤러 선택·실행
    → 표(TableResult) 단위로 sync_table_result RPC 호출
    → difficulty_tables upsert + difficulty_entries 전체 교체 + crawl_sync_logs 기록 (단일 트랜잭션)
```

### 1. Supabase 마이그레이션 적용

`supabase/migrations/20260702000000_crawl_sync.sql`을 적용합니다. 두 방법 중 택 1:

```bash
# 방법 A: Supabase CLI
supabase link --project-ref <project-ref>
supabase db push

# 방법 B: Supabase 대시보드 > SQL Editor에 파일 내용 붙여넣고 실행
```

생성되는 것: `difficulty_tables`(표 정의), `difficulty_entries`(곡 엔트리), `crawl_sync_logs`(동기화 이력), `sync_table_result` RPC. 표 데이터는 공개 읽기(RLS), 쓰기는 service role 전용입니다.

### 2. 환경변수 설정

```dotenv
SUPABASE_SERVICE_ROLE_KEY=eyJ...   # Project Settings > API > service_role
CRAWL_TARGETS=[{"crawler":"5ch_sheet","url":"https://docs.google.com/spreadsheets/.../pubhtml","play_style":"SP","level":12}]
```

### 3. 주간 스케줄

서버 기동 시 APScheduler가 등록되며, 기본값은 **매주 월요일 05:00 (Asia/Seoul)** 입니다. `.env`로 변경할 수 있습니다:

```dotenv
CRAWL_SCHEDULE_ENABLED=true   # false로 끄기
CRAWL_SCHEDULE_DAY=mon        # mon/tue/wed/thu/fri/sat/sun
CRAWL_SCHEDULE_HOUR=5
CRAWL_SCHEDULE_MINUTE=0
```

### 4. 표 조회 / 미리보기

```bash
# 난이도표 목록 조회 (공개)
curl http://localhost:8000/api/v1/tables

# 표 1개 + 엔트리 조회 (공개)
curl http://localhost:8000/api/v1/tables/5ch-sp12-strength

# 크롤러 목록 확인 (등록된 크롤러 이름)
curl http://localhost:8000/api/v1/crawl/targets

# 선택한 크롤러로 미리보기 (Supabase 반영 없음, 공개)
# 실제 크롤 경로를 그대로 태우므로 반환값 = 동기화될 표/엔트리
curl -X POST http://localhost:8000/api/v1/crawl/preview \
  -H "Content-Type: application/json" \
  -d '{"crawler": "5ch_sheet", "target": {"url": "<pubhtml-url>", "play_style": "SP", "level": 12}}'
```

### 새 난이도표 추가하기

1. `app/crawlers/`에 크롤러 클래스를 구현하고 `@register("이름")`을 붙입니다 (숫자형 표는 `numeric_example.py` 템플릿의 `_parse`만 구현).
2. `.env`의 `CRAWL_TARGETS`에 `{"crawler": "이름", ...설정}`을 추가합니다.

스키마/동기화 코드는 수정할 필요 없습니다.

## 사전 준비

환경변수 파일을 생성하고 Supabase 프로젝트 정보를 입력합니다:

```bash
cp .env.example .env
```

```dotenv
SUPABASE_URL=https://<project-ref>.supabase.co   # Supabase 대시보드 > Project Settings
SUPABASE_JWT_SECRET=                              # 레거시 프로젝트만 설정, 신규는 비워둠
```

> 구글 로그인을 쓰려면 Supabase 대시보드 > Authentication > Providers 에서 Google을 활성화하고 OAuth 클라이언트 ID/Secret을 등록해야 합니다. 이메일 로그인은 기본 활성화되어 있습니다.

---

## 방법 1: Docker로 실행 (권장)

> Docker Desktop 또는 Docker Engine + Compose 플러그인이 설치되어 있어야 합니다.

### 실행

```bash
docker compose up --build
```

백그라운드 실행:

```bash
docker compose up --build -d
```

### 중지

```bash
docker compose down
```

### 로그 확인

```bash
docker compose logs -f api
```

`docker-compose.yml`에 `./app` 볼륨 마운트와 `--reload` 옵션이 설정되어 있어, 코드를 수정하면 컨테이너 재시작 없이 즉시 반영됩니다. 단, `requirements.txt` 변경 시에는 `docker compose up --build`로 재빌드가 필요합니다.

---

## 방법 2: 로컬(가상환경)로 실행

### 설치

```bash
# 1. 가상환경 생성 및 활성화
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. 의존성 설치
pip install -r requirements.txt
```

### 실행

```bash
uvicorn app.main:app --reload
```

기본 포트는 8000이며, 변경하려면 `--port <포트>` 옵션을 추가하세요.

---

## 접속 확인

| 항목 | URL |
|------|-----|
| 루트 | http://localhost:8000/ |
| Swagger UI (API 문서) | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| 헬스체크 | http://localhost:8000/api/v1/health |

Swagger UI에서 인증 필수 API를 테스트하려면 우측 상단 **Authorize** 버튼에 Supabase 액세스 토큰을 입력하세요.

### API 호출 예시

```bash
# 헬스체크 (공개)
curl http://localhost:8000/api/v1/health

# 내 정보 조회 (인증 필수 — 토큰 없으면 401)
curl http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer <supabase-access-token>"
```
