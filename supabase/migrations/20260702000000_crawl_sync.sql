-- ─────────────────────────────────────────────────────────────
-- 난이도표 크롤링 동기화 스키마
--
-- difficulty_tables : 난이도표 정의 (5ch 지력표/개인차표, CPI 등)
-- difficulty_entries: 표별 곡 엔트리 (동기화 시 전체 교체)
-- crawl_sync_logs   : 동기화 이력 (성공/실패)
-- sync_table_result : 표 1개를 원자적으로 반영하는 RPC (service role 전용)
-- ─────────────────────────────────────────────────────────────

create table if not exists public.difficulty_tables (
  id          uuid primary key default gen_random_uuid(),
  slug        text not null unique,
  name        text not null,
  source      text not null,                    -- '5ch', 'cpi' 등
  play_style  text not null check (play_style in ('SP', 'DP')),
  rating_type text not null check (rating_type in ('GRADE', 'NUMERIC')),
  level       int,
  grades      text[],                           -- GRADE 방식일 때만 (서열 순서)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table if not exists public.difficulty_entries (
  id         bigint generated always as identity primary key,
  table_id   uuid not null references public.difficulty_tables (id) on delete cascade,
  title      text not null,
  series     text,
  play_style text not null check (play_style in ('SP', 'DP')),
  difficulty text not null,                     -- NORMAL/HYPER/ANOTHER/LEGGENDARIA
  level      int,
  grade      text,                              -- GRADE 방식: 'S+', 'A' 등
  rating     numeric,                           -- NUMERIC 방식: 12.3 등
  created_at timestamptz not null default now()
);

create index if not exists idx_difficulty_entries_table_id
  on public.difficulty_entries (table_id);
create index if not exists idx_difficulty_entries_title
  on public.difficulty_entries (title);

create table if not exists public.crawl_sync_logs (
  id           bigint generated always as identity primary key,
  table_slug   text,
  crawler      text,
  url          text,
  triggered_by text not null,                   -- 'schedule' | 'manual:<email>'
  status       text not null check (status in ('SUCCESS', 'FAILED')),
  entry_count  int not null default 0,
  error        text,
  created_at   timestamptz not null default now()
);

create index if not exists idx_crawl_sync_logs_created_at
  on public.crawl_sync_logs (created_at desc);

-- ── RLS: 표 데이터는 공개 읽기, 쓰기는 service role만 ─────────
alter table public.difficulty_tables  enable row level security;
alter table public.difficulty_entries enable row level security;
alter table public.crawl_sync_logs    enable row level security;

create policy "difficulty_tables_public_read"
  on public.difficulty_tables for select
  to anon, authenticated
  using (true);

create policy "difficulty_entries_public_read"
  on public.difficulty_entries for select
  to anon, authenticated
  using (true);

-- crawl_sync_logs는 공개 정책 없음 → service role만 접근 가능

-- ── RPC: 표 1개 반영 (upsert + 엔트리 전체 교체 + 로그, 단일 트랜잭션) ──
create or replace function public.sync_table_result(
  p_table        jsonb,
  p_entries      jsonb,
  p_triggered_by text default 'schedule'
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_table_id uuid;
  v_count    int;
begin
  insert into difficulty_tables (slug, name, source, play_style, rating_type, level, grades)
  values (
    p_table->>'slug',
    p_table->>'name',
    p_table->>'source',
    p_table->>'play_style',
    p_table->>'rating_type',
    (p_table->>'level')::int,
    case
      when jsonb_typeof(p_table->'grades') = 'array'
      then array(select jsonb_array_elements_text(p_table->'grades'))
    end
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

  delete from difficulty_entries where table_id = v_table_id;

  insert into difficulty_entries (table_id, title, series, play_style, difficulty, level, grade, rating)
  select v_table_id, e.title, e.series, e.play_style, e.difficulty, e.level, e.grade, e.rating
  from jsonb_to_recordset(p_entries) as e(
    title text, series text, play_style text, difficulty text,
    level int, grade text, rating numeric
  );

  get diagnostics v_count = row_count;

  insert into crawl_sync_logs (table_slug, triggered_by, status, entry_count)
  values (p_table->>'slug', p_triggered_by, 'SUCCESS', v_count);

  return jsonb_build_object(
    'slug', p_table->>'slug',
    'status', 'SUCCESS',
    'entry_count', v_count
  );
end;
$$;

-- service role 전용: 일반 클라이언트에서 호출 차단
revoke execute on function public.sync_table_result(jsonb, jsonb, text)
  from anon, authenticated;
