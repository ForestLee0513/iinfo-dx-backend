# iinfo-dx-backend

FastAPI 기반 백엔드입니다. 로컬 실행과 Docker 실행을 모두 지원하며, **Supabase Auth**(구글 OAuth / 이메일 로그인) 기반 인증과 **곡 마스터(textage.cc) + 난이도표 크롤링 → Supabase 주간 동기화** 기능을 제공합니다.

## 기술 스택

- **Python** 3.12
- **FastAPI** 0.115.x / **Uvicorn** (ASGI 서버)
- **Pydantic v2** / **pydantic-settings** (스키마 및 환경설정)
- **PyJWT** (Supabase 액세스 토큰 검증)
- **httpx** + **BeautifulSoup4** (크롤링/파싱)
- **supabase-py** (크롤링 결과 동기화)
- **APScheduler** (주 1회 스케줄링)
- **Redis** (크롤 작업 상태 저장 — 서버 재기동 시 이어하기, 스케줄 오버라이드)

## 프로젝트 구조

```
.
├── app/                     # 모듈러 모놀리식 (단일 앱, 모듈별 라우터를 /api/v1/{모듈}로 마운트)
│   ├── main.py              # FastAPI 엔트리포인트 (web/songs-crawl/difficulty-crawl/admin/health 마운트 + 스케줄러 기동)
│   ├── core/
│   │   └── config.py        # 환경설정 (.env 로드) — 전역 공유
│   ├── common/              # 모듈 공유 코드
│   │   ├── auth.py          # Supabase 토큰 검증 의존성 (CurrentUser) + 역할 검사 (require_role)
│   │   ├── schemas.py       # 인증 사용자 스키마 (AuthUser, UserRole/ROLE_LEVELS)
│   │   ├── supabase.py      # service role Supabase 클라이언트 (get_supabase)
│   │   ├── redis.py         # Redis 비동기 클라이언트 (get_redis)
│   │   ├── openapi.py       # 공개/비공개 OpenAPI 문서 분리 (PUBLIC 마커, setup_docs)
│   │   └── health.py        # 헬스체크 (공개)
│   ├── web/                 # /web/*  : 사용자 client(별도 프론트 레포)용 API
│   │   ├── router.py        # web 모듈 라우터
│   │   ├── queries.py       # 난이도표 조회(읽기)
│   │   └── endpoints/
│   │       ├── tables.py    # 난이도표 목록/상세 (공개)
│   │       └── me.py        # 내 정보 조회 (인증 필수)
│   ├── songs_crawl/         # /songs-crawl/* : 곡 마스터 크롤링 (textage.cc)
│   │   ├── router.py        # targets/preview (공개)
│   │   ├── pipeline.py      # 크롤링 → 동기화 파이프라인 (run_song_sync)
│   │   ├── sync.py          # sync_song_master RPC 호출(쓰기, service role)
│   │   └── crawlers/        # 곡 마스터 크롤러 레지스트리 (base/textage)
│   ├── difficulty_crawl/    # /difficulty-crawl/* : 난이도표 크롤링
│   │   ├── router.py        # targets/preview (공개)
│   │   ├── pipeline.py      # 크롤링 → 동기화 파이프라인 (run_table_sync)
│   │   ├── sync.py          # sync_table_result RPC 호출(쓰기, service role)
│   │   ├── scheduler.py     # APScheduler 주간 크론 (곡 마스터 → 난이도표 순차 실행)
│   │   ├── crawlers/        # 크롤러 레지스트리 (base/sheet_5ch/numeric_example)
│   │   └── parsers/         # pubhtml 파싱 (지력/개인차 판별)
│   └── admin/               # /admin/* : 어드민 API (별도 어드민 FE 전용, ADMIN 역할 보호)
│       ├── router.py        # 크롤 수동 실행/작업 조회 + 스케줄 조회/변경
│       ├── jobs.py          # 작업 실행기 (Redis 체크포인트 — 재기동 시 이어하기)
│       ├── store.py         # Redis 저장소 (작업 상태/스케줄 오버라이드)
│       ├── deps.py          # 어드민 인증 (AdminUser — app_metadata.role == "ADMIN")
│       └── schemas.py       # 작업/스케줄 요청·응답 모델
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
[백엔드] 토큰 서명·만료·발급자 검증 후 사용자 정보 추출 (app/common/auth.py)
```

- 구글 로그인: `supabase.auth.signInWithOAuth({ provider: "google" })`
- 이메일 로그인: `supabase.auth.signInWithPassword({ email, password })`
- 두 방식 모두 동일한 형식의 액세스 토큰이 발급되며, 백엔드 검증 로직은 동일합니다. 로그인 수단은 토큰의 `app_metadata.provider` 클레임으로 구분됩니다.

### 토큰 검증 방식

| 프로젝트 유형 | 설정 | 검증 방식 |
|--------------|------|----------|
| 신규 (비대칭 키, 권장) | `SUPABASE_JWT_SECRET` 비워둠 | JWKS 엔드포인트 공개 키 (RS256/ES256) |
| 레거시 (대칭 키) | `SUPABASE_JWT_SECRET` 설정 | JWT Secret (HS256) |

### 모듈 구성 & 인증

단일 앱(모듈러 모놀리식)에 모듈별 라우터를 `/api/v1/{모듈}` 프리픽스로 마운트합니다 (`app/main.py`):

```python
app.include_router(health.router,           prefix="/api/v1/health",           tags=["Health"])
app.include_router(web_router,              prefix="/api/v1/web",              tags=["Web"])
app.include_router(songs_crawl_router,      prefix="/api/v1/songs-crawl",      tags=["SongsCrawl"])
app.include_router(difficulty_crawl_router, prefix="/api/v1/difficulty-crawl", tags=["DifficultyCrawl"])
app.include_router(admin_router,            prefix="/api/v1/admin",            tags=["Admin"])  # 어드민 FE 전용
```

인증이 필요한 엔드포인트는 `CurrentUser` 타입을 파라미터로 받습니다 (라우터 전체를 보호하려면 `dependencies=[Depends(get_current_user)]`):

```python
from app.common.auth import CurrentUser

@router.get("/me")
def read_current_user(current_user: CurrentUser):
    return current_user  # id(UUID), email, provider, role
```

| 엔드포인트 | 인증 |
|-----------|------|
| `GET /api/v1/health` | 공개 |
| `GET /api/v1/web/tables` | 공개 |
| `GET /api/v1/web/tables/{slug}` | 공개 |
| `GET /api/v1/web/me` | 인증 필수 |
| `GET /api/v1/songs-crawl/targets` | 공개 |
| `GET /api/v1/songs-crawl/preview` | 공개 |
| `GET /api/v1/difficulty-crawl/targets` | 공개 |
| `POST /api/v1/difficulty-crawl/preview` | 공개 |
| `POST /api/v1/admin/crawl/jobs` | 어드민 (인증 + `app_metadata.role == "ADMIN"`) |
| `GET /api/v1/admin/crawl/jobs` | 어드민 |
| `GET /api/v1/admin/crawl/jobs/{id}` | 어드민 |
| `GET /api/v1/admin/crawl/schedule` | 어드민 |
| `PUT /api/v1/admin/crawl/schedule` | 어드민 |

## 크롤링 & 주간 동기화

곡 마스터(textage.cc)와 난이도표를 크롤링해 Supabase에 반영하는 파이프라인입니다. 반영은 **주간 스케줄러** 또는 **어드민 API의 수동 실행**(`POST /api/v1/admin/crawl/jobs`)으로 이뤄집니다. 크롤 모듈(songs-crawl/difficulty-crawl) 자체에는 수동 트리거 엔드포인트가 없습니다. 프론트엔드는 공개 `GET /api/v1/web/tables` 로 결과를 조회합니다.

주간 스케줄러 하나가 **곡 마스터 → 난이도표** 순서로 실행합니다. 순서는 시간차가 아니라 코드로 보장되며, 곡 마스터가 실패해도 난이도표 동기화는 기존 곡 마스터 기준으로 계속 진행합니다.

```
[APScheduler 주 1회 크론] ──→ run_weekly_sync
    ① run_song_sync (곡 마스터)
       → SONG_CRAWL_TARGETS 순회 → 타깃의 "crawler" 값으로 크롤러 선택·실행 (textage)
       → SongMasterResult 단위로 sync_song_master RPC 호출
       → versions/songs/charts upsert + 크롤에 없는 곡/채보 in_ac=false (단일 트랜잭션, 삭제 없음)
    ② run_table_sync (난이도표)
       → TABLE_CRAWL_TARGETS 순회 → 타깃의 "crawler" 값으로 크롤러 선택·실행
       → 표(TableResult) 단위로 sync_table_result RPC 호출
       → difficulty_tables upsert + difficulty_entries 전체 교체 + crawl_sync_logs 기록 (단일 트랜잭션)
```

### 1. Supabase 마이그레이션 적용

`supabase/migrations/` 아래 SQL(`20260702000000_crawl_sync.sql`, `20260703000000_song_master.sql`)을 적용합니다. 두 방법 중 택 1:

```bash
# 방법 A: Supabase CLI
supabase link --project-ref <project-ref>
supabase db push

# 방법 B: Supabase 대시보드 > SQL Editor에 파일 내용 붙여넣고 실행
```

생성되는 것: `difficulty_tables`(표 정의), `difficulty_entries`(곡 엔트리), `crawl_sync_logs`(동기화 이력), `sync_table_result` RPC + 곡 마스터용 `versions`/`songs`/`charts`, `sync_song_master` RPC. 데이터는 공개 읽기(RLS), 쓰기는 service role 전용입니다.

### 2. 환경변수 설정

```dotenv
SUPABASE_SERVICE_ROLE_KEY=eyJ...   # Project Settings > API > service_role
SONG_CRAWL_TARGETS=[{"crawler":"textage"}]
TABLE_CRAWL_TARGETS=[{"crawler":"5ch_sheet","url":"https://docs.google.com/spreadsheets/.../pubhtml","play_style":"SP","level":12}]
```

### 3. 주간 스케줄

서버 기동 시 APScheduler가 등록되며, 기본값은 **매주 월요일 05:00 (Asia/Seoul)** 입니다. `.env`로 변경할 수 있습니다:

```dotenv
CRAWL_SCHEDULE_ENABLED=true   # false로 끄기
CRAWL_SCHEDULE_DAY=mon        # mon/tue/wed/thu/fri/sat/sun
CRAWL_SCHEDULE_HOUR=5
CRAWL_SCHEDULE_MINUTE=0
```

어드민 API(`PUT /api/v1/admin/crawl/schedule`)로 변경하면 Redis에 저장되어 재기동 후에도 유지되며, 위 `.env` 기본값보다 우선합니다.

## 어드민 API (별도 어드민 FE 전용)

크롤 수동 실행과 스케줄 변경을 제공합니다. 모든 엔드포인트는 Supabase 토큰 인증에 더해 **계정의 `app_metadata.role`이 `ADMIN`** 이어야 합니다(미설정 계정은 `USER`로 취급되어 403).

```dotenv
CORS_ORIGINS=["http://localhost:3000"]   # 어드민 FE 오리진 (브라우저 호출 허용)
REDIS_URL=redis://localhost:6379/0       # docker compose에서는 자동으로 redis 서비스를 사용
```

### 어드민 권한 부여

권한은 Supabase 계정 정보(`auth.users.raw_app_meta_data.role`)로 관리합니다. `app_metadata`는 사용자가 스스로 수정할 수 없는 영역이라 안전합니다. SQL Editor에서:

```sql
-- 부여
update auth.users
set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb) || '{"role": "ADMIN"}'::jsonb
where email = 'admin@example.com';

-- 회수
update auth.users
set raw_app_meta_data = raw_app_meta_data - 'role'
where email = 'admin@example.com';
```

역할은 JWT 클레임으로 실려 오므로 **변경은 다음 토큰 갱신(재로그인 또는 만료 후 refresh) 시점부터** 반영됩니다. 역할 체계는 `app/common/schemas.py`의 `UserRole`/`ROLE_LEVELS`(서열 기반 — 상위 역할이 하위 권한 포함)에 정의되어 있어, 역할이 늘어나면 여기에만 추가하면 됩니다. 엔드포인트 보호는 `require_role(UserRole.ADMIN)` 의존성을 사용합니다.

```bash
TOKEN="<supabase-access-token>"

# 크롤 수동 실행 (scope: full=곡 마스터→난이도표 순차 | songs | tables)
curl -X POST http://localhost:8000/api/v1/admin/crawl/jobs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"scope": "full"}'
# → 202 + 작업 객체 (이미 실행 중이면 409)

# 작업 목록 / 단건 조회 (FE는 이걸로 진행 상황 폴링)
curl http://localhost:8000/api/v1/admin/crawl/jobs -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/api/v1/admin/crawl/jobs/<job-id> -H "Authorization: Bearer $TOKEN"

# 스케줄 조회 / 변경 (변경값은 Redis에 저장 → 재기동 후에도 유지)
curl http://localhost:8000/api/v1/admin/crawl/schedule -H "Authorization: Bearer $TOKEN"
curl -X PUT http://localhost:8000/api/v1/admin/crawl/schedule \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled": true, "day": "wed", "hour": 3, "minute": 30}'
```

### 중단 시 이어하기 (Redis 체크포인트)

작업은 스텝(`song_sync` → `table_sync`) 단위로 진행 상태를 Redis에 기록합니다. 서버가 실행 도중 중단되면 **재기동 시 완료되지 않은 스텝부터 자동으로 이어서 실행**합니다(크롤→RPC 반영은 upsert/전체 교체라 스텝 재실행이 안전). 주간 스케줄 실행도 같은 실행기를 거치므로 동일하게 이어하기가 적용됩니다. Redis는 AOF 영속화가 켜져 있어(`docker-compose.yml`) 호스트 재부팅 후에도 상태가 남습니다.

### 4. 표 조회 / 미리보기

```bash
# 난이도표 목록 조회 (공개)
curl http://localhost:8000/api/v1/web/tables

# 표 1개 + 엔트리 조회 (공개)
curl http://localhost:8000/api/v1/web/tables/5ch-sp12-strength

# 크롤러 목록 확인 (등록된 크롤러 이름)
curl http://localhost:8000/api/v1/difficulty-crawl/targets

# 선택한 크롤러로 미리보기 (Supabase 반영 없음, 공개)
# 실제 크롤 경로를 그대로 태우므로 반환값 = 동기화될 표/엔트리
curl -X POST http://localhost:8000/api/v1/difficulty-crawl/preview \
  -H "Content-Type: application/json" \
  -d '{"crawler": "5ch_sheet", "target": {"url": "<pubhtml-url>", "play_style": "SP", "level": 12}}'

# 곡 마스터 크롤 타깃 확인 / 미리보기 (textage SLOTS 검증용, Supabase 반영 없음)
curl http://localhost:8000/api/v1/songs-crawl/targets
curl "http://localhost:8000/api/v1/songs-crawl/preview?title=AA&limit=5"
```

### 새 난이도표 추가하기

1. `app/difficulty_crawl/crawlers/`에 크롤러 클래스를 구현하고 `@register("이름")`을 붙입니다 (숫자형 표는 `numeric_example.py` 템플릿의 `_parse`만 구현).
2. `.env`의 `TABLE_CRAWL_TARGETS`에 `{"crawler": "이름", ...설정}`을 추가합니다.

스키마/동기화 코드는 수정할 필요 없습니다. 곡 마스터 소스를 추가할 때도 동일한 패턴으로 `app/songs_crawl/crawlers/` + `SONG_CRAWL_TARGETS`만 수정합니다 (두 모듈은 레지스트리가 분리되어 있습니다).

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
| Swagger UI — 공개(클라이언트) API | http://localhost:8000/docs |
| ReDoc — 공개(클라이언트) API | http://localhost:8000/redoc |
| Swagger UI — 비공개(내부) API 전체 | http://localhost:8000/internal/docs |
| 헬스체크 | http://localhost:8000/api/v1/health |

Swagger UI에서 인증 필수 API를 테스트하려면 우측 상단 **Authorize** 버튼에 Supabase 액세스 토큰을 입력하세요.

### OpenAPI 문서 분리 (공개 / 비공개)

- **공개 문서(`/docs`, `/openapi.json`)** 에는 엔드포인트에 `openapi_extra=PUBLIC`(`app/common/openapi.py`)을 붙인 것만 실립니다 — **opt-in 방식**이라 새 엔드포인트는 기본적으로 클라이언트 스펙에 노출되지 않습니다. 현재 공개: `GET /web/tables`, `GET /web/tables/{slug}`, `GET /web/me`.
- **비공개 문서(`/internal/docs`)** 에는 어드민·크롤 진단을 포함한 전체 엔드포인트가 실립니다.
- 이 분리는 **문서 노출 제어일 뿐 접근 제어가 아닙니다**. 실제 차단은 인증(ADMIN 역할)과 리버스 프록시에서 처리하며, 공개 도메인 쪽 프록시(NPM)에서 `/internal` 경로를 차단해야 합니다 (`/api/v1/admin`, `/api/v1/*-crawl`과 동일하게).

### API 호출 예시

```bash
# 헬스체크 (공개)
curl http://localhost:8000/api/v1/health

# 내 정보 조회 (인증 필수 — 토큰 없으면 401)
curl http://localhost:8000/api/v1/web/me \
  -H "Authorization: Bearer <supabase-access-token>"
```
