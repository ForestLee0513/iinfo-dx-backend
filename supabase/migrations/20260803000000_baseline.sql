-- ============================================================================
--  통합 계정 + 서비스별 스키마 (단일 베이스라인)
--
--  이 파일 하나가 전체 스키마의 시작점이다. 사용자 0명 / 다운타임 무관 전제로
--  과거 증분 마이그레이션을 모두 스쿼시했다 (오픈소스 공개 시 모순되는 죽은
--  마이그레이션을 남기지 않기 위함).
--
--  public = 모든 서비스가 공유하는 계정 계층
--  iidx   = IIDX 서비스 (프로필 + 악곡 마스터 + 난이도표 + 크롤 운영 + 동기화 RPC)
--
--  크롤 대상/스케줄/로그도 iidx 스키마 안에 둔다(서비스가 자기 크롤을 소유).
--  RLS는 테이블 단위라, 공개 데이터(songs/tables 등)와 service_role 전용 테이블
--  (crawl_*)이 같은 스키마에 공존해도 안전하다 — crawl_*에는 public read 정책을
--  주지 않고 anon/authenticated SELECT를 회수한다. 별도 미노출 스키마(ops)는
--  supabase-py(PostgREST)로 접근하려면 어차피 노출해야 해서 의미가 없어 제거했다.
--
--  백엔드 접근 방식: 모든 읽기/쓰기는 service_role 키를 쓰는 supabase-py(PostgREST).
--  service_role은 RLS를 우회하므로, 이 파일 하단에서 iidx 스키마의 usage/table
--  권한을 service_role에 명시적으로 부여한다. (Exposed schemas 설정은 맨 끝 주석 참고.)
-- ============================================================================

begin;

-- ---------------------------------------------------------------------------
-- 0. 기존 테이블 정리
-- ---------------------------------------------------------------------------
drop table if exists public.user_follows       cascade;
drop table if exists public.user_bans          cascade;
drop table if exists public.user_profiles      cascade;
drop table if exists public.difficulty_entries cascade;
drop table if exists public.difficulty_tables  cascade;
drop table if exists public.crawl_sync_logs    cascade;
drop table if exists public.crawl_schedules    cascade;
drop table if exists public.crawl_targets      cascade;
drop table if exists public.charts             cascade;
drop table if exists public.songs              cascade;
drop table if exists public.versions           cascade;

-- iidx는 스키마째 드롭 후 재생성한다(구 ops도 함께 드롭) → 이 파일을 통째로 재실행해도
-- 항상 동일한 최종 상태(테이블·RPC·권한 포함)가 보장된다(사용자 0명 전제).
drop schema if exists iidx cascade;
drop schema if exists ops  cascade;  -- 구 버전 잔재 정리 (크롤 테이블이 iidx로 이동, 더 이상 사용 안 함)

create schema iidx;


-- ---------------------------------------------------------------------------
-- 1. 공용 유틸
-- ---------------------------------------------------------------------------

-- JWT의 sub 클레임을 읽는다. auth.uid() 대신 이것만 쓰면
-- 나중에 인증 발급자가 바뀌어도 정책을 고칠 필요가 없다.
create or replace function public.current_user_id()
returns uuid
language sql stable
set search_path = ''
as $$ select nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'sub', '')::uuid $$;

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end $$;


-- ---------------------------------------------------------------------------
-- 2. public — 공통 계정 계층
-- ---------------------------------------------------------------------------

create table public.profiles (
  id                uuid primary key references auth.users(id) on delete cascade,
  handle            text unique
                      check (handle is null or handle ~ '^[A-Za-z0-9_]{2,20}$'),
  display_name      text,
  profile_image_url text,
  social_links      jsonb not null default '[]'::jsonb
                      check (jsonb_typeof(social_links) = 'array'),
  is_public         boolean not null default true,
  -- 플랫폼 전체 관리자만. 서비스 단위 운영자는 각 서비스 프로필에 둔다.
  platform_role     text not null default 'USER'
                      check (platform_role = any (array['USER', 'SUPER_ADMIN'])),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create trigger profiles_touch
  before update on public.profiles
  for each row execute function public.touch_updated_at();

-- 가입 즉시 공통 프로필 생성. 서비스 프로필은 각 서비스 온보딩 시점에 만든다.
create or replace function public.handle_new_user()
returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, new.raw_user_meta_data ->> 'name');
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();


-- 제재. service가 null이면 플랫폼 전체, 값이 있으면 해당 서비스에만 적용.
create table public.user_bans (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references public.profiles(id) on delete cascade,
  service    text,
  reason     text not null,
  ban_until  timestamptz,
  banned_by  uuid references public.profiles(id),  -- null = 시스템 자동 제재
  banned_at  timestamptz not null default now(),
  lifted_at  timestamptz,
  lifted_by  uuid references public.profiles(id)
);

create index user_bans_active_idx
  on public.user_bans (user_id, service)
  where lifted_at is null;

create or replace function public.is_banned(p_service text default null)
returns boolean
language sql stable security invoker set search_path = ''
as $$
  select exists (
    select 1 from public.user_bans b
    where b.user_id = public.current_user_id()
      and b.lifted_at is null
      and (b.ban_until is null or b.ban_until > now())
      and (b.service is null or b.service = p_service)
  )
$$;


-- 팔로우는 플랫폼 단위. 서비스마다 별도 그래프가 필요해지면
-- 해당 서비스 스키마에 자체 follows 테이블을 두면 된다.
create table public.user_follows (
  follower_id uuid not null references public.profiles(id) on delete cascade,
  followee_id uuid not null references public.profiles(id) on delete cascade,
  created_at  timestamptz not null default now(),
  primary key (follower_id, followee_id),
  constraint no_self_follow check (follower_id <> followee_id)
);

create index user_follows_followee_idx on public.user_follows (followee_id);


-- ---------------------------------------------------------------------------
-- 3. iidx — IIDX 서비스
-- ---------------------------------------------------------------------------

create table iidx.profiles (
  user_id      uuid primary key references public.profiles(id) on delete cascade,
  dj_name      text check (dj_name is null or dj_name ~ '^[A-Za-z0-9]{1,6}$'),
  dj_id        text check (dj_id   is null or dj_id   ~ '^[0-9]{4}-[0-9]{4}$'),
  service_role text not null default 'USER'
                 check (service_role = any (array['USER', 'ADMIN'])),
  is_public    boolean not null default true,
  joined_at    timestamptz not null default now()
);

-- 이 행의 존재 여부가 곧 "이 서비스에 온보딩했는가"의 답이다.
create or replace function iidx.is_member()
returns boolean
language sql stable security invoker set search_path = ''
as $$
  select exists (
    select 1 from iidx.profiles
    where user_id = public.current_user_id()
  )
$$;

create or replace function iidx.is_admin()
returns boolean
language sql stable security invoker set search_path = ''
as $$
  select exists (
    select 1 from iidx.profiles p
    where p.user_id = public.current_user_id()
      and p.service_role = 'ADMIN'
  ) or exists (
    select 1 from public.profiles p
    where p.id = public.current_user_id()
      and p.platform_role = 'SUPER_ADMIN'
  )
$$;


create table iidx.versions (
  id     smallint primary key,
  name   text not null,
  abbrev text
);

create table iidx.songs (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  series      text unique,
  textage_tag text unique,
  genre       text,
  artist      text,
  bpm         text,
  version     smallint references iidx.versions(id),
  in_ac       boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create trigger songs_touch
  before update on iidx.songs
  for each row execute function public.touch_updated_at();

create table iidx.charts (
  id         uuid primary key default gen_random_uuid(),
  song_id    uuid not null references iidx.songs(id) on delete cascade,
  play_style text not null check (play_style = any (array['SP', 'DP'])),
  difficulty text not null check (difficulty = any (array[
               'BEGINNER', 'NORMAL', 'HYPER', 'ANOTHER', 'LEGGENDARIA'])),
  level      smallint not null check (level between 1 and 12),
  notes      integer,
  in_ac      boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- 크롤러 upsert의 충돌 대상. 원본 스키마에 없어서 중복 보면이 들어갈 수 있었다.
  constraint charts_song_style_diff_uk unique (song_id, play_style, difficulty)
);

create trigger charts_touch
  before update on iidx.charts
  for each row execute function public.touch_updated_at();

create index charts_level_idx on iidx.charts (play_style, level);


create table iidx.difficulty_tables (
  id          uuid primary key default gen_random_uuid(),
  slug        text not null unique,
  name        text not null,
  source      text not null,
  play_style  text not null,
  rating_type text not null check (rating_type = any (array['GRADE', 'NUMERIC'])),
  level       integer,
  grades      jsonb,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create trigger difficulty_tables_touch
  before update on iidx.difficulty_tables
  for each row execute function public.touch_updated_at();

create table iidx.difficulty_entries (
  id         bigint generated always as identity primary key,
  table_id   uuid not null references iidx.difficulty_tables(id) on delete cascade,
  -- 같은 스키마 안이므로 FK를 걸어도 서비스 분리에 지장이 없다.
  -- 크롤 시점에 매칭 실패할 수 있으니 nullable로 둔다.
  chart_id   uuid references iidx.charts(id) on delete set null,
  title      text not null,
  series     text,
  play_style text not null,
  difficulty text not null,
  level      integer,
  grade      text,
  rating     numeric,
  table_type text,
  created_at timestamptz not null default now()
);

create index difficulty_entries_table_idx on iidx.difficulty_entries (table_id);
create index difficulty_entries_chart_idx on iidx.difficulty_entries (chart_id);
-- 매칭 실패분 추적용
create index difficulty_entries_unmatched_idx
  on iidx.difficulty_entries (table_id) where chart_id is null;


-- ---------------------------------------------------------------------------
-- 4. iidx — 크롤 운영 테이블 (서비스가 자기 크롤을 소유)
--    공개 데이터와 같은 iidx 스키마지만, 아래 RLS/권한에서 public read를 주지 않아
--    service_role 전용이 된다. 스키마가 곧 서비스 경계라 service 컬럼은 두지 않는다.
-- ---------------------------------------------------------------------------

create table iidx.crawl_targets (
  target_key text primary key,
  kind       text not null check (kind = any (array['song', 'table'])),
  target_id  text not null,
  label      text not null,
  crawler    text not null,
  config     jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table iidx.crawl_schedules (
  target_key text primary key references iidx.crawl_targets(target_key) on delete cascade,
  enabled    boolean not null default false,
  triggers   jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now()
);

create trigger crawl_schedules_touch
  before update on iidx.crawl_schedules
  for each row execute function public.touch_updated_at();

create table iidx.crawl_sync_logs (
  id           bigint generated always as identity primary key,
  target_key   text,
  crawler      text,
  url          text,
  triggered_by text not null,
  status       text not null check (status = any (array['SUCCESS', 'FAILED'])),
  entry_count  integer not null default 0,
  error        text,
  created_at   timestamptz not null default now()
);

create index crawl_sync_logs_recent_idx
  on iidx.crawl_sync_logs (created_at desc);


-- ---------------------------------------------------------------------------
-- 5. RLS
-- ---------------------------------------------------------------------------

alter table public.profiles              enable row level security;
alter table public.user_bans             enable row level security;
alter table public.user_follows          enable row level security;
alter table iidx.profiles            enable row level security;
alter table iidx.songs               enable row level security;
alter table iidx.charts              enable row level security;
alter table iidx.versions            enable row level security;
alter table iidx.difficulty_tables   enable row level security;
alter table iidx.difficulty_entries  enable row level security;
-- 크롤 운영 테이블: RLS만 켜고 정책을 안 만든다 = service_role 외 전면 차단.
-- (아래 public-read 루프에 포함하지 않으므로 anon/authenticated는 0행)
alter table iidx.crawl_targets           enable row level security;
alter table iidx.crawl_schedules         enable row level security;
alter table iidx.crawl_sync_logs         enable row level security;

create policy profiles_read on public.profiles
  for select using (is_public or id = public.current_user_id());
create policy profiles_update_self on public.profiles
  for update using (id = public.current_user_id())
  with check (id = public.current_user_id() and platform_role = 'USER');

create policy bans_read_self on public.user_bans
  for select using (user_id = public.current_user_id());

create policy follows_read on public.user_follows
  for select using (true);
create policy follows_write_self on public.user_follows
  for insert with check (follower_id = public.current_user_id());
create policy follows_delete_self on public.user_follows
  for delete using (follower_id = public.current_user_id());

create policy iidx_profiles_read on iidx.profiles
  for select using (true);
create policy iidx_profiles_join on iidx.profiles
  for insert with check (
    user_id = public.current_user_id() and not public.is_banned('iidx'));
create policy iidx_profiles_update_self on iidx.profiles
  for update using (user_id = public.current_user_id())
  with check (user_id = public.current_user_id() and service_role = 'USER');

-- 악곡 마스터와 난이도표는 전체 공개 읽기, 쓰기는 서비스 관리자만
do $$
declare t text;
begin
  foreach t in array array['songs', 'charts', 'versions',
                           'difficulty_tables', 'difficulty_entries']
  loop
    execute format(
      'create policy %I_read on iidx.%I for select using (true)', t, t);
    execute format(
      'create policy %I_admin_write on iidx.%I for all
         using (iidx.is_admin()) with check (iidx.is_admin())', t, t);
  end loop;
end $$;


-- ---------------------------------------------------------------------------
-- 6. 권한
-- ---------------------------------------------------------------------------

grant usage on schema iidx to anon, authenticated;
grant select on all tables in schema iidx to anon, authenticated;
grant insert, update, delete on iidx.profiles to authenticated;
grant insert, update, delete on iidx.songs, iidx.charts,
      iidx.versions, iidx.difficulty_tables,
      iidx.difficulty_entries to authenticated;

alter default privileges in schema iidx
  grant select on tables to anon, authenticated;

-- 크롤 운영 테이블은 위 blanket SELECT까지 회수해 service_role 전용으로 확실히 막는다
-- (RLS 정책이 없어 행은 이미 안 보이지만, grant까지 없애 defense-in-depth).
revoke select on iidx.crawl_targets, iidx.crawl_schedules, iidx.crawl_sync_logs
  from anon, authenticated;

-- 트리거 전용 함수: REST API(/rpc/...)를 통한 직접 호출 차단.
-- 트리거는 role 권한이 아닌 트리거 오너 권한으로 실행되므로 동작에 영향 없음.
revoke execute on function public.handle_new_user() from public;
revoke execute on function public.touch_updated_at() from public;

-- 백엔드 전용: service_role은 iidx 스키마 전체에 접근한다. Supabase는 public
-- 스키마에 대해서만 service_role 기본 권한을 세팅해 두므로, 커스텀 스키마(iidx)에는
-- usage/table/sequence 권한을 명시적으로 부여해야 supabase-py(service_role)가 읽고 쓴다.
grant usage on schema iidx to service_role;
grant all privileges on all tables    in schema iidx to service_role;
grant all privileges on all sequences in schema iidx to service_role;
-- ALTER DEFAULT PRIVILEGES는 객체 타입당 한 문장씩(tables/sequences를 한 문장에 못 묶는다).
alter default privileges in schema iidx grant all privileges on tables    to service_role;
alter default privileges in schema iidx grant all privileges on sequences to service_role;


-- ---------------------------------------------------------------------------
-- 7. 동기화/조회 RPC (백엔드 크롤 파이프라인 + 어드민 회원 목록)
--
--  security definer 함수로, 소유자(postgres) 권한으로 실행되어 스키마 경계와
--  RLS를 넘나든다. service_role만 실행할 수 있게 잠근다(일반 롤 차단).
--  곡/표 크롤 결과는 upsert/전량교체라 멱등하므로 재기동 중 재실행에 안전하다.
-- ---------------------------------------------------------------------------

-- ── 곡 마스터 전체 반영 (iidx) ──
-- p_payload = {"versions":[{"id","name"}],
--              "songs":[{"tag","title","genre","artist","version","in_ac"?,"charts":[...]}]}
create or replace function iidx.sync_song_master(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = iidx, public
as $$
declare
  v_songs_total  int;
  v_charts_total int;
begin
  -- 1) 버전 upsert
  insert into versions (id, name)
  select (v->>'id')::smallint, v->>'name'
  from jsonb_array_elements(p_payload->'versions') as v
  on conflict (id) do update set name = excluded.name;

  -- 2) 곡 upsert (textage_tag 기준). in_ac은 크롤러가 판별한 값을 사용
  --    (actbl 상태플래그 bit0 — textage에서 tt2/firebrick로 표시되는 AC 삭제곡).
  insert into songs (textage_tag, title, genre, artist, version, in_ac)
  select s->>'tag', s->>'title', s->>'genre', s->>'artist', (s->>'version')::smallint,
         coalesce((s->>'in_ac')::boolean, true)
  from jsonb_array_elements(p_payload->'songs') as s
  on conflict (textage_tag) do update set
    title      = excluded.title,
    genre      = excluded.genre,
    artist     = excluded.artist,
    version    = excluded.version,
    in_ac      = excluded.in_ac,
    updated_at = now();

  -- 3) 채보 upsert
  insert into charts (song_id, play_style, difficulty, level, in_ac)
  select sg.id, c->>'play_style', c->>'difficulty', (c->>'level')::smallint, true
  from jsonb_array_elements(p_payload->'songs') as s
  join songs sg on sg.textage_tag = s->>'tag'
  cross join lateral jsonb_array_elements(s->'charts') as c
  on conflict (song_id, play_style, difficulty) do update set
    level      = excluded.level,
    in_ac      = true,
    updated_at = now();

  -- 4) 이번 크롤에 없는 곡은 in_ac=false (삭제하지 않음)
  update songs
  set in_ac = false, updated_at = now()
  where in_ac
    and textage_tag is not null
    and textage_tag not in (
      select s->>'tag' from jsonb_array_elements(p_payload->'songs') as s
    );

  -- 5) 이번 크롤에 없는 채보도 in_ac=false
  update charts ch
  set in_ac = false, updated_at = now()
  where ch.in_ac
    and not exists (
      select 1
      from jsonb_array_elements(p_payload->'songs') as s
      join songs sg on sg.textage_tag = s->>'tag'
      cross join lateral jsonb_array_elements(s->'charts') as c
      where sg.id = ch.song_id
        and c->>'play_style' = ch.play_style
        and c->>'difficulty' = ch.difficulty
    );

  select count(*) into v_songs_total  from songs  where in_ac;
  select count(*) into v_charts_total from charts where in_ac;

  return jsonb_build_object(
    'songs_total', v_songs_total,
    'charts_total', v_charts_total
  );
end;
$$;


-- ── 난이도표 1개 전체 반영 (iidx) — 표 upsert + 엔트리 전량 교체 + 로그 ──
-- p_table   = {"slug","name","source","play_style","rating_type","level","grades"}
-- p_entries = [{"title","series","play_style","difficulty","level","grade"?,"rating"?,"table_type"?}, ...]
create or replace function iidx.sync_table_result(
  p_table jsonb,
  p_entries jsonb,
  p_triggered_by text
)
returns jsonb
language plpgsql
security definer
set search_path = iidx, public
as $$
declare
  v_slug     text := p_table->>'slug';
  v_table_id uuid;
  v_count    int;
begin
  -- 1) 표 upsert (slug 기준), id 확보
  insert into difficulty_tables
    (slug, name, source, play_style, rating_type, level, grades, updated_at)
  values (
    v_slug,
    p_table->>'name',
    p_table->>'source',
    p_table->>'play_style',
    p_table->>'rating_type',
    nullif(p_table->>'level', '')::int,
    case when jsonb_typeof(p_table->'grades') = 'array' then p_table->'grades' else null end,
    now()
  )
  on conflict (slug) do update set
    name        = excluded.name,
    source      = excluded.source,
    play_style  = excluded.play_style,
    rating_type = excluded.rating_type,
    level       = excluded.level,
    grades      = excluded.grades,
    updated_at  = now()
  returning id into v_table_id;

  -- 2) 기존 엔트리 전량 삭제 (표 단위 full-replace)
  delete from difficulty_entries where table_id = v_table_id;

  -- 3) 새 엔트리 insert (chart_id 매칭은 후속 파이프라인 몫 — 여기선 null)
  insert into difficulty_entries
    (table_id, title, series, play_style, difficulty, level, grade, rating, table_type)
  select
    v_table_id,
    e->>'title',
    e->>'series',
    e->>'play_style',
    e->>'difficulty',
    nullif(e->>'level', '')::int,
    e->>'grade',
    nullif(e->>'rating', '')::numeric,
    e->>'table_type'
  from jsonb_array_elements(coalesce(p_entries, '[]'::jsonb)) as e;

  get diagnostics v_count = row_count;

  -- 4) 동기화 성공 로그 (iidx.crawl_sync_logs — search_path로 해석)
  insert into crawl_sync_logs
    (target_key, crawler, url, triggered_by, status, entry_count)
  values (v_slug, p_table->>'source', null, p_triggered_by, 'SUCCESS', v_count);

  return jsonb_build_object(
    'slug', v_slug,
    'table_id', v_table_id,
    'entry_count', v_count
  );
end;
$$;


-- ── 어드민 회원 목록 (public) — auth.users + 프로필(플랫폼/서비스 역할) + 활성 제재 ──
-- 역할은 두 테이블에 분산되어 있으므로 유효 역할(effective role)을 합성한다:
--   platform_role='SUPER_ADMIN' → SUPER_ADMIN
--   iidx.service_role='ADMIN' → ADMIN
--   그 외 → USER
create or replace function public.admin_list_users(
    p_email     text    default null,
    p_provider  text    default null,
    p_is_banned boolean default null,
    p_role      text    default null,
    p_page      int     default 1,
    p_per_page  int     default 20
)
returns jsonb
language sql
security definer
set search_path = public, iidx
as $$
with filtered as (
    select
        u.id,
        u.email,
        u.raw_app_meta_data ->> 'provider' as provider,
        u.created_at,
        u.last_sign_in_at,
        case
          when p.platform_role = 'SUPER_ADMIN' then 'SUPER_ADMIN'
          when sp.service_role = 'ADMIN'       then 'ADMIN'
          else 'USER'
        end as role,
        (b.user_id is not null) as is_banned,
        b.reason as ban_reason,
        b.ban_until
    from auth.users u
    left join public.profiles     p  on p.id = u.id
    left join iidx.profiles   sp on sp.user_id = u.id
    -- 현재 유효한 정지 레코드 (플랫폼 전체 또는 iidx 대상, 미해제·미만료 중 최신 1건)
    left join lateral (
        select user_id, reason, ban_until
        from public.user_bans
        where user_id = u.id
          and lifted_at is null
          and (ban_until is null or ban_until > now())
          and (service is null or service = 'iidx')
        order by banned_at desc
        limit 1
    ) b on true
    where u.deleted_at is null
      and (p_email is null or u.email ilike '%' || p_email || '%')
      and (p_provider is null
           or u.raw_app_meta_data ->> 'provider' = p_provider
           or u.raw_app_meta_data -> 'providers' ? p_provider)
      and (p_is_banned is null or (b.user_id is not null) = p_is_banned)
      and (p_role is null or (case
             when p.platform_role = 'SUPER_ADMIN' then 'SUPER_ADMIN'
             when sp.service_role = 'ADMIN'       then 'ADMIN'
             else 'USER' end) = p_role)
)
select jsonb_build_object(
    'total', (select count(*) from filtered),
    'users', coalesce(
        (
            select jsonb_agg(to_jsonb(page.*))
            from (
                select *
                from filtered
                order by created_at desc
                limit p_per_page
                offset (p_page - 1) * p_per_page
            ) page
        ),
        '[]'::jsonb
    )
);
$$;


-- 세 함수 모두 service_role 전용: 일반 롤에서 호출 차단
revoke execute on function iidx.sync_song_master(jsonb)             from public, anon, authenticated;
revoke execute on function iidx.sync_table_result(jsonb, jsonb, text) from public, anon, authenticated;
revoke execute on function public.admin_list_users(text, text, boolean, text, int, int)
  from public, anon, authenticated;
grant execute on function iidx.sync_song_master(jsonb)             to service_role;
grant execute on function iidx.sync_table_result(jsonb, jsonb, text) to service_role;
grant execute on function public.admin_list_users(text, text, boolean, text, int, int)
  to service_role;

-- ---------------------------------------------------------------------------
-- 사용자 성적 CSV 업로드 · 스냅샷 · 파싱 데이터
--
-- baseline(20260803000000_baseline.sql)이 이미 적용된 환경에 대한 증분 마이그레이션.
-- 새 환경이라면 baseline에 이 내용을 합쳐 적용해도 된다.
--
-- 테이블 구조:
--   score_uploads      : CSV 업로드 이력 (Storage 경로 + 메타, 스냅샷 단위)
--   score_current      : 사용자 × 스타일별 현재 활성 스냅샷 포인터
--   user_chart_scores  : 파싱된 성적 데이터 (upload_id 단위 보관)
--
-- 스냅샷 복구는 score_current.upload_id 를 바꾸는 것으로 충분하다.
-- 원본 CSV 는 Supabase Storage(iidx-score-csv 버킷)에 보관된다.
-- ---------------------------------------------------------------------------

create table iidx.score_uploads (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references public.profiles(id) on delete cascade,
  play_style    text not null check (play_style = any(array['SP', 'DP'])),
  -- official: eagate score_download 원본 / crawled: 북마크릿 재조합 CSV
  source        text not null check (source = any(array['official', 'crawled'])),
  content_hash  text not null,   -- SHA-256 hex (동일 내용 재업로드 감지)
  storage_path  text not null,   -- Supabase Storage 버킷 내 상대 경로
  song_count    integer not null default 0,
  uploaded_at   timestamptz not null default now(),
  -- 동일 사용자·스타일·내용 중복 업로드 방지 (→ 스냅샷 미생성)
  unique (user_id, play_style, content_hash)
);

create table iidx.score_current (
  user_id    uuid not null references public.profiles(id) on delete cascade,
  play_style text not null check (play_style = any(array['SP', 'DP'])),
  upload_id  uuid not null references iidx.score_uploads(id),
  applied_at timestamptz not null default now(),
  primary key (user_id, play_style)
);

create table iidx.user_chart_scores (
  upload_id      uuid not null references iidx.score_uploads(id) on delete cascade,
  user_id        uuid not null references public.profiles(id) on delete cascade,
  play_style     text not null check (play_style = any(array['SP', 'DP'])),
  title          text not null,
  difficulty     text not null
                 check (difficulty = any(array['BEGINNER','NORMAL','HYPER','ANOTHER','LEGGENDARIA'])),
  -- 공식 CSV 전용 필드 — 크롤 방식이면 null
  version        text,
  genre          text,
  artist         text,
  play_count     integer,
  last_played_at timestamptz,
  miss_count     integer,
  -- 공통 성적
  level          smallint not null,
  ex_score       integer not null default 0,
  pgreat         integer not null default 0,
  great          integer not null default 0,
  clear_type     text,
  dj_level       text,
  -- 곡 마스터 매칭 성공 시 채워짐 (타이틀 불일치 등으로 null 가능)
  song_id        uuid references iidx.songs(id),
  primary key (upload_id, title, difficulty)
);

-- RLS (service_role은 RLS 우회 — 백엔드는 service_role 키만 사용)
alter table iidx.score_uploads        enable row level security;
alter table iidx.score_current        enable row level security;
alter table iidx.user_chart_scores    enable row level security;

-- authenticated 사용자는 본인 데이터만 읽기/쓰기
create policy score_uploads_self on iidx.score_uploads
  for all using (user_id = public.current_user_id())
  with check (user_id = public.current_user_id());

create policy score_current_self on iidx.score_current
  for all using (user_id = public.current_user_id())
  with check (user_id = public.current_user_id());

create policy user_chart_scores_self on iidx.user_chart_scores
  for all using (user_id = public.current_user_id())
  with check (user_id = public.current_user_id());

-- score 테이블은 "grant all on all tables in schema iidx"(line 앞쪽) 이후에 생성되므로
-- blanket grant 적용 범위에 들지 않는다. 명시적으로 재부여.
grant all privileges on iidx.score_uploads     to service_role;
grant all privileges on iidx.score_current     to service_role;
grant all privileges on iidx.user_chart_scores to service_role;

-- authenticated: 읽기(select)는 blanket grant가 이미 앞에 있으나 같은 이유로 미적용.
-- 쓰기는 별도 명시(insert/update/delete).
grant select, insert, update, delete on iidx.score_uploads     to authenticated;
grant select, insert, update, delete on iidx.score_current     to authenticated;
grant select, insert, update, delete on iidx.user_chart_scores to authenticated;

-- 성적 데이터는 비공개 — anon에겐 select 차단 (defense-in-depth, crawl 테이블과 동일 패턴)
revoke select on iidx.score_uploads, iidx.score_current, iidx.user_chart_scores from anon;

commit;

-- ============================================================================
--  실행 후 대시보드에서 할 일
--  1) Settings > API > Exposed schemas 에 iidx 를 추가한다 (public 은 기본 포함).
--     ops 스키마는 더 이상 없다 — 크롤 운영 테이블(crawl_*)도 iidx 안으로 들어왔다.
--     crawl_* 는 iidx에 있지만 public read 정책이 없고 anon/authenticated SELECT가
--     회수돼 있어 service_role 외에는 어떤 행도 볼 수 없다(노출돼도 안전).
--     리버스 프록시에서 /api/v1/iidx/crawl/jobs*, /schedules*, /targets/* 와
--     /api/v1/admin, /api/v1/iidx/admin, /internal 은 공개 도메인 차단 대상 — README 참고.
--  2) service_role 키는 백엔드(API 서버) 환경변수로만 관리하고 클라이언트에
--     절대 노출하지 않는다.
-- ============================================================================
