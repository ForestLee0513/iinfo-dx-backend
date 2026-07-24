-- ─────────────────────────────────────────────────────────────
-- 난이도표 동기화 RPC + 표/엔트리 테이블 복원
--
-- 곡 마스터(20260718010000)와 동일한 사고: 원본 마이그레이션
-- 20260702000000_crawl_sync.sql 이 저장소에서 삭제되면서 sync_table_result
-- RPC 가 실제 Supabase 에 존재하지 않게 됐다 — 표 크롤링 시 PGRST202
-- ("Could not find the function public.sync_table_result ...")로 실패한다.
--
-- 여기서는 난이도표 파이프라인이 필요로 하는 세 가지를 실제 코드(읽기 CRUD /
-- 크롤러 엔트리 포맷 / 스키마)에 맞춰 재정의한다:
--   1) difficulty_tables / difficulty_entries 테이블 (없을 때만 생성)
--   2) public read RLS 정책 (쓰기는 service role 전용)
--   3) sync_table_result RPC (표 upsert + 엔트리 전량 교체 + 성공 로그)
--
-- 컬럼 근거:
--   difficulty_tables → app/crud/crud_tables.py, app/schemas/table.py(TableDetail),
--                       app/crud/crud_difficulty.py(p_table)
--   difficulty_entries → app/schemas/table.py(DifficultyEntry),
--                        app/services/difficulty_crawl/crawlers/base.py(entries 포맷)
--
-- 주의: crawl_sync_logs 는 20260718010000_song_master_sync.sql 에서 이미
--       생성되므로 여기서 다시 만들지 않는다.
-- ─────────────────────────────────────────────────────────────

-- ── 표 메타데이터 (난이도표 1행) ──
create table if not exists public.difficulty_tables (
  id          uuid primary key default gen_random_uuid(),
  slug        text not null unique,                       -- 사용자 조회 키 (/web/tables/{slug})
  name        text not null,
  source      text not null,                              -- 출처 (예: '5ch')
  play_style  text not null,                              -- 'SP' | 'DP'
  rating_type text not null check (rating_type in ('GRADE', 'NUMERIC')),
  level       int,                                        -- NUMERIC/일부 표는 null 가능
  grades      jsonb,                                      -- GRADE 방식 서열 배열 (NUMERIC 은 null)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- ── 표에 속한 곡 엔트리 (표 단위로 전량 교체됨) ──
create table if not exists public.difficulty_entries (
  id          bigint generated always as identity primary key,
  table_id    uuid not null references public.difficulty_tables(id) on delete cascade,
  title       text not null,
  series      text,
  play_style  text not null,
  difficulty  text not null,                              -- NORMAL/HYPER/ANOTHER/LEGGENDARIA
  level       int,
  grade       text,                                       -- GRADE 방식: 'S+','A' 등
  rating      numeric,                                    -- NUMERIC 방식: 12.3 등
  table_type  text,                                       -- 'STRENGTH'=지력 / 'PERSONAL'=개인차 (5ch)
  created_at  timestamptz not null default now()
);

create index if not exists idx_difficulty_entries_table_id
  on public.difficulty_entries (table_id);

-- ── RLS: 공개 읽기, 쓰기는 service role 전용 ──
alter table public.difficulty_tables  enable row level security;
alter table public.difficulty_entries enable row level security;

drop policy if exists "public read difficulty_tables" on public.difficulty_tables;
create policy "public read difficulty_tables"
  on public.difficulty_tables for select using (true);

drop policy if exists "public read difficulty_entries" on public.difficulty_entries;
create policy "public read difficulty_entries"
  on public.difficulty_entries for select using (true);

-- ── RPC: 표 1개 전체 반영 (표 upsert + 엔트리 전량 교체 + 로그, 단일 트랜잭션) ──
-- p_table   = {"slug","name","source","play_style","rating_type","level","grades"}
-- p_entries = [{"title","series","play_style","difficulty","level","grade"?,"rating"?,"table_type"?}, ...]
create or replace function public.sync_table_result(
  p_table jsonb,
  p_entries jsonb,
  p_triggered_by text
)
returns jsonb
language plpgsql
security definer
set search_path = public
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

  -- 3) 새 엔트리 insert
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

  -- 4) 동기화 성공 로그
  insert into crawl_sync_logs
    (table_slug, crawler, url, triggered_by, status, entry_count)
  values (v_slug, p_table->>'source', null, p_triggered_by, 'SUCCESS', v_count);

  return jsonb_build_object(
    'slug', v_slug,
    'table_id', v_table_id,
    'entry_count', v_count
  );
end;
$$;

-- service role 전용: 일반 클라이언트에서 호출 차단
revoke execute on function public.sync_table_result(jsonb, jsonb, text)
  from anon, authenticated;
