-- ─────────────────────────────────────────────────────────────
-- 사용자 접근 제한(정지) 이력 테이블
--
-- 어드민이 부여한 정지 처리를 기록한다.
--   - lifted_at IS NULL AND (ban_until IS NULL OR ban_until > now()) → 현재 정지 중
--   - ban_until IS NULL → 영구 정지
--   - lifted_at IS NOT NULL → 어드민이 명시적으로 해제한 기록
--
-- 모든 읽기/쓰기는 service_role을 통하므로 RLS 활성화 후 별도 정책 불필요.
-- (service_role은 RLS를 우회하므로 백엔드 service_role 클라이언트로만 접근 가능)
-- ─────────────────────────────────────────────────────────────

create table public.user_bans (
    id         uuid        default gen_random_uuid() primary key,
    user_id    uuid        not null references auth.users (id) on delete cascade,
    reason     text        not null,
    ban_until  timestamptz,                     -- null = 영구 정지
    banned_by  text        not null,            -- 처리한 어드민 이메일
    banned_at  timestamptz default now() not null,
    lifted_at  timestamptz,                     -- null = 아직 해제되지 않음
    lifted_by  text                             -- 해제한 어드민 이메일
);

-- 활성 정지 조회 성능 인덱스 (get_active_ban에서 lifted_at IS NULL 조건으로 필터)
create index user_bans_user_id_active_idx
    on public.user_bans (user_id)
    where lifted_at is null;

-- 이력 조회 성능 인덱스
create index user_bans_user_id_idx on public.user_bans (user_id, banned_at desc);

alter table public.user_bans enable row level security;
-- service_role은 RLS를 우회하므로 별도 정책 없이 백엔드에서만 접근 가능
