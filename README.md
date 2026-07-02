# iinfo-dx-backend

FastAPI 기반 백엔드 템플릿입니다. 로컬 실행과 Docker 실행을 모두 지원하며, **Supabase Auth**(구글 OAuth / 이메일 로그인) 기반 인증을 사용합니다.

## 기술 스택

- **Python** 3.12
- **FastAPI** 0.115.x
- **Uvicorn** (ASGI 서버)
- **Pydantic v2** / **pydantic-settings** (스키마 및 환경설정)
- **PyJWT** (Supabase 액세스 토큰 검증)

## 프로젝트 구조

```
.
├── app/
│   ├── main.py              # FastAPI 앱 엔트리포인트
│   ├── core/
│   │   └── config.py        # 환경설정 (.env 로드)
│   ├── api/
│   │   ├── deps.py          # 공통 의존성 (Supabase 토큰 검증)
│   │   ├── router.py        # API 라우터 집합 (/api/v1) — 공개/인증필수 구분
│   │   └── endpoints/
│   │       ├── health.py    # 헬스체크 (공개)
│   │       └── auth.py      # 내 정보 조회 (인증 필수)
│   └── schemas/
│       └── user.py          # 인증 사용자 스키마
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
| `GET /api/v1/auth/me` | 인증 필수 |

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
