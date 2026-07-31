-- ─────────────────────────────────────────────────────────────
-- 팔로우 관계 테이블
--
-- follower_id가 followee_id를 팔로우한다. 자기 자신 팔로우는 금지.
-- 팔로우/언팔로우, 팔로워·팔로잉 목록·카운트 조회는 전부 백엔드 API
-- (서비스 롤)를 통해서만 처리한다 — user_bans와 동일하게 RLS는 켜두되
-- 별도 정책은 두지 않는다.
-- ─────────────────────────────────────────────────────────────

create table public.user_follows (
    follower_id uuid        not null references auth.users (id) on delete cascade,
    followee_id uuid        not null references auth.users (id) on delete cascade,
    created_at  timestamptz not null default now(),
    primary key (follower_id, followee_id),
    check (follower_id <> followee_id)
);

-- "누가 이 사용자를 팔로우하는가" 조회/카운트용 인덱스.
-- "내가 팔로우하는 목록"은 PK 선두 컬럼(follower_id)이 이미 커버한다.
create index user_follows_followee_idx on public.user_follows (followee_id);

alter table public.user_follows enable row level security;
-- service_role은 RLS를 우회하므로 별도 정책 없이 백엔드에서만 접근 가능
