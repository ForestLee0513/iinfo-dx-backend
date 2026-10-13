# 백엔드 배포

`development` 푸시는 개발 API를 다시 빌드하고 시작합니다. `main` 푸시는 운영 API의 대기 슬롯을 빌드하고 건강 상태를 확인한 뒤 Nginx Proxy Manager 대상을 전환합니다. 이전 슬롯은 30초 동안 유지한 후 중지합니다.

## 서버 배치

배포 파일은 SSH 사용자의 `$HOME/deploy/iinfo-dx-backend` 아래에 설치합니다. `repo`에는 배포할 커밋을 체크아웃하고, `env/development.env`와 `env/production.env`에는 환경별 설정을, `npm-credentials.json`에는 NPM 로그인 JSON을 저장합니다. 자격증명 파일 권한은 `600`입니다. `active-slot`은 운영 슬롯 상태를 기록합니다.

환경파일은 `.env.example`을 바탕으로 작성하며 `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, OAuth 리다이렉트 허용 목록, `CORS_ORIGINS`를 각 환경에 맞게 채웁니다. GitHub Actions가 환경별 `DEPLOY_DOMAIN`을 추가해 서버에 설치합니다. Redis 주소와 `ENVIRONMENT`는 Compose가 지정합니다.

배포 전에 Supabase URL 형식, 환경별 프로젝트 ref, 서비스 역할 키, 도메인 및 NPM 자격증명 JSON 형식을 검사합니다. 운영 설정에도 운영 프로젝트의 서비스 역할 키가 필요합니다.

NPM은 개발 API를 `iinfo-dx-development-api:8000`으로, 운영 API를 현재 슬롯(`iinfo-dx-production-blue` 또는 `iinfo-dx-production-green`)의 8000번 포트로 전달합니다. 호스트가 없으면 도메인과 일치하는 Let's Encrypt 와일드카드 인증서로 생성합니다. NPM 자격증명은 `{"identity":"관리자 이메일","secret":"관리자 비밀번호"}` 형식입니다.

NPM 호스트의 고급 설정은 `/internal` 공개 접근을 차단합니다. 대상 목록의 쓰기 요청은 앱의 ADMIN 인증으로 보호됩니다.

## GitHub Actions secrets

Repository Actions secrets:

| 이름 | 값 |
| --- | --- |
| `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` | Tailscale OAuth 클라이언트 자격증명 (`auth_keys` Write, `tag:ci`) |
| `DEPLOY_HOST` | 배포 서버의 Tailscale 주소 |
| `DEPLOY_SSH_USER` | SSH 사용자 |
| `DEPLOY_SSH_KNOWN_HOSTS` | 검증한 SSH 호스트 공개 키의 known_hosts 한 줄 |
| `NPM_CREDENTIALS_JSON` | NPM API 로그인 JSON |

GitHub Environments `development`, `production` 각각에 다음 secret을 등록합니다. 환경별 허용 브랜치는 각각 `development`, `main`으로 제한합니다.

| 이름 | 값 |
| --- | --- |
| `DEPLOY_ENV` | 해당 환경의 `.env` 전체 내용 |
| `EXPECTED_SUPABASE_REF` | 해당 환경의 Supabase 프로젝트 ref |
| `DEPLOY_DOMAIN` | 해당 환경의 공개 API 도메인 |

워크플로는 환경별 값을 실행 중에만 사용하고 SSH/SCP로 서버에 전송합니다. Tailscale 접근 정책에서 `tag:ci`가 배포 서버의 TCP 22에 연결할 수 있어야 합니다. SSH 호스트 키가 바뀌면 새 키를 별도로 검증한 뒤 `DEPLOY_SSH_KNOWN_HOSTS` secret을 갱신합니다. 워크플로 파일은 두 배포 브랜치 모두에 있어야 합니다.

## 확인과 복구

서버에서 해당 환경의 도메인으로 `/api/v1/health`를 확인합니다. 운영 대기 슬롯 건강 검사나 NPM 전환 후 검사가 실패하면 스크립트가 이전 슬롯으로 되돌립니다. 이미 성공한 배포를 되돌릴 때는 이전 정상 커밋을 `main`에 다시 배포합니다. 운영 Redis 볼륨은 두 슬롯이 공유하며 슬롯 전환 시 재시작하지 않습니다.

운영 슬롯 둘은 전환 과정에서 잠시 함께 살아 있습니다. 현재 앱은 각 인스턴스에서 APScheduler를 시작합니다. 겹치는 30초 동안 스케줄 시각이 도래하면 두 인스턴스가 시도할 수 있으며, Redis의 작업 중복 검사에 의존합니다.
