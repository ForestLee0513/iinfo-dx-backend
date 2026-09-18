# ForestLee 배포

`development` 푸시는 개발 API를 중지하고 새 이미지를 빌드한 뒤 다시 시작합니다. `main` 푸시는 운영 API의 대기 슬롯을 빌드하고 건강 상태를 확인한 다음 **기존 Nginx Proxy Manager**의 `iinfo-dx-api.forestlee.me` 프록시 대상을 전환합니다. 이전 슬롯은 30초 동안 유지해 기존 요청을 처리한 뒤 중지합니다.

## 서버 배치

- 저장소: `/home/forestlee/deploy/iinfo-dx/repo` (Actions가 HTTPS로 클론하고 해당 커밋을 체크아웃)
- 개발 설정: `/home/forestlee/deploy/iinfo-dx/env/development.env`
- 운영 설정: `/home/forestlee/deploy/iinfo-dx/env/production.env`
- NPM API 자격증명: `/home/forestlee/deploy/iinfo-dx/npm-credentials.json` (권한 `600`)
- 상태 파일: `/home/forestlee/deploy/iinfo-dx/active-slot`

두 환경파일은 `.env.example`을 바탕으로 서버에서 따로 만들고 권한을 `600`으로 지정합니다. `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, OAuth 리다이렉트 허용 목록, `CORS_ORIGINS`를 각 환경에 맞게 채웁니다. `SUPABASE_SERVICE_ROLE_KEY`를 GitHub Actions에 넣거나 저장소에 커밋하지 않습니다. Redis 주소와 `ENVIRONMENT`는 Compose가 지정합니다.

| 환경 | Supabase 프로젝트 | 프로젝트 ref |
| --- | --- | --- |
| development | IInfo DX dev | `gooxuqvpxpzmofddcuow` |
| production | IInfo DX | `byfyglcaoclsjugliphe` |

배포 스크립트는 환경파일의 프로젝트 ref와 서비스 역할 키 존재 여부를 검사합니다. 개발 자격증명은 서버에 반영되어 REST API 응답 200을 확인했습니다. 운영 환경파일에는 운영 URL만 준비했으며 서비스 역할 키는 서버에서 직접 입력해야 합니다.

운영 API는 NPM의 `iinfo-dx-api.forestlee.me` 호스트가 현재 슬롯(`iinfo-dx-production-blue` 또는 `iinfo-dx-production-green`)의 8000번 포트로 직접 전달합니다. NPM API 자격증명 파일은 `{"identity":"관리자 이메일","secret":"관리자 비밀번호"}` 형식입니다. 첫 운영 배포에서 프록시 호스트가 없으면 기존 Let's Encrypt `*.forestlee.me` 인증서로 생성합니다. 개발 API는 `iinfo-dx-development-api:8000`이며 서버 내부의 `127.0.0.1:18081`로도 확인할 수 있습니다.

NPM 호스트의 고급 설정에는 `/internal`, 관리자 경로, 크롤 작업·스케줄·대상 상세 경로의 공개 접근 차단 규칙을 추가합니다. 대상 목록의 쓰기 요청은 앱의 ADMIN 인증으로 보호됩니다.

ForestLee의 `~/services/stacks/cloudflare-ddns/compose.yaml`에는 이 도메인을 추가해 두었습니다. 실제 DNS 레코드는 해당 DDNS 컨테이너를 갱신하면 생성됩니다. API와 NPM 프록시 호스트가 준비되기 전에는 갱신하지 않습니다.

## GitHub Actions 준비

Repository Actions secrets에 다음 값을 등록합니다.

| 이름 | 값 |
| --- | --- |
| `TAILSCALE_AUTHKEY` | `tag:ci`가 붙은 재사용 가능·임시 Tailscale auth key. Tailnet ACL에서 ForestLee의 TCP 22 접근 허용 |
| `FORESTLEE_SSH_PRIVATE_KEY` | Actions 전용 Ed25519 개인 키 전체 내용 |

ForestLee의 확인된 Ed25519 호스트 키는 워크플로에 고정해 두었습니다. SSH 키가 변경되면 새 키를 별도로 검증한 뒤 워크플로를 갱신합니다.

Actions는 Tailscale로 ForestLee에 접속하고, 서버에서 공개 저장소를 클론합니다. `development`와 `main` 각각의 최신 커밋 SHA를 배포합니다. GitHub Environments `development`, `production`을 만들고 허용 브랜치를 각각 제한하면 운영 배포 권한을 좁힐 수 있습니다. 워크플로 파일은 **두 브랜치 모두에 있어야** 해당 브랜치 푸시에서 동작합니다.

## 확인과 복구

```bash
curl --resolve iinfo-dx-api.forestlee.me:443:127.0.0.1 \
  https://iinfo-dx-api.forestlee.me/api/v1/health
curl -fsS http://127.0.0.1:18081/api/v1/health
docker compose -f ~/deploy/iinfo-dx/repo/deploy/production.compose.yaml ps
cat ~/deploy/iinfo-dx/active-slot
```

대기 슬롯 건강 검사 또는 NPM 전환 후 검사에 실패하면 스크립트가 이전 슬롯으로 되돌립니다. 이미 성공한 배포를 되돌릴 때는 이전 정상 커밋으로 `main`을 다시 배포합니다. 운영 Redis 볼륨은 두 슬롯이 공유하며 슬롯 전환 시 재시작하지 않습니다.

운영 슬롯 둘은 전환 과정에서 잠시 함께 살아 있습니다. 현재 앱은 각 인스턴스에서 APScheduler를 시작합니다. 겹치는 30초 동안 스케줄 시각이 도래하면 두 인스턴스가 시도할 수 있으며, Redis의 작업 중복 검사에 의존합니다. 이 구간의 크롤 작업을 엄격히 한 번만 실행해야 한다면 별도 단일 스케줄러 프로세스로 분리해야 합니다.
