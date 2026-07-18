-- ─────────────────────────────────────────────────────────────
-- 크롤 대상(target) 및 대상별 스케줄 영구 저장
--
-- 기존에는 Redis 해시(crawl:targets/crawl:schedules)에만 저장되어 Redis가
-- 유실되면 등록해둔 크롤 대상 정의와 스케줄이 통째로 사라졌다. 어드민 API
-- (POST/GET/PUT/DELETE /api/v1/crawl/targets, /crawl/schedules)로 관리하는
-- 대상/스케줄을 여기로 옮겨 영구 저장한다. 크롤 작업(job) 실행 상태(체크포인트/
-- 재기동 이어하기)는 휘발성 데이터라 계속 Redis에 남는다.
--
-- 모든 읽기/쓰기는 service_role을 통하므로 RLS 활성화 후 별도 정책 불필요.
-- (service_role은 RLS를 우회하므로 백엔드 service_role 클라이언트로만 접근 가능)
-- ─────────────────────────────────────────────────────────────

create table public.crawl_targets (
    target_key  text        primary key,                  -- f"{kind}:{id}" (예: "table:5ch_sp12")
    kind        text        not null check (kind in ('song', 'table')),
    target_id   text        not null,                      -- 원본 id (kind 안에서 고유, target_key에 이미 반영됨)
    label       text        not null,
    crawler     text        not null,                      -- 등록된 크롤러 이름 (song/table 레지스트리)
    config      jsonb       not null default '{}'::jsonb,   -- 크롤러별 추가 설정 (url/play_style/level/slug/name/source 등)
    created_at  timestamptz not null default now()
);

create index crawl_targets_kind_idx on public.crawl_targets (kind);

alter table public.crawl_targets enable row level security;
-- service_role은 RLS를 우회하므로 별도 정책 없이 백엔드에서만 접근 가능

create table public.crawl_schedules (
    target_key  text        primary key references public.crawl_targets (target_key) on delete cascade,
    enabled     boolean     not null default false,
    triggers    jsonb       not null default '[]'::jsonb,   -- [{"day":"mon","hour":3,"minute":0}, ...]
    updated_at  timestamptz not null default now()
);

alter table public.crawl_schedules enable row level security;
-- service_role은 RLS를 우회하므로 별도 정책 없이 백엔드에서만 접근 가능
-- 대상 삭제 시(on delete cascade) 스케줄 행도 함께 삭제된다.
