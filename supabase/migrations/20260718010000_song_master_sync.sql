-- ─────────────────────────────────────────────────────────────
-- 곡 마스터 동기화 RPC + 동기화 이력 테이블 복원
--
-- 2026-07-07 커밋(c4629a0 "임시로 적용한 마이그레이션 sql 삭제")에서 곡 마스터/
-- 크롤 동기화 관련 마이그레이션 파일이 저장소에서 삭제됐지만, 실제 Supabase에는
-- sync_song_master RPC와 crawl_sync_logs 테이블이 없는 채로 남아 있었다
-- (곡 크롤링 시 PGRST202/PGRST205로 실패). versions/songs/charts 테이블은
-- 이미 존재하며 삭제됐던 초안과 컬럼명이 다르다 — 특히 songs.version_id가
-- 아니라 songs.version이다. 여기서는 기존 테이블 구조를 그대로 두고,
-- 없는 두 가지(RPC, crawl_sync_logs)만 실제 스키마에 맞춰 다시 만든다.
--
-- 실제 테이블 구조(참고, 여기서 재생성하지 않음):
--   versions(id smallint pk, name text, abbrev text)
--   songs(id uuid pk, title text unique, series text unique null,
--         textage_tag text unique null, genre, artist, bpm text,
--         version smallint references versions(id), in_ac bool,
--         created_at, updated_at)
--   charts(id uuid pk, song_id uuid references songs(id) on delete cascade,
--          play_style, difficulty, level smallint, notes int null,
--          in_ac bool, created_at, updated_at,
--          unique(song_id, play_style, difficulty))
-- ─────────────────────────────────────────────────────────────

create table if not exists public.crawl_sync_logs (
  id           bigint generated always as identity primary key,
  table_slug   text,
  crawler      text,
  url          text,
  triggered_by text not null,                   -- 'schedule' | 'admin:<email>'
  status       text not null check (status in ('SUCCESS', 'FAILED')),
  entry_count  int not null default 0,
  error        text,
  created_at   timestamptz not null default now()
);

create index if not exists idx_crawl_sync_logs_created_at
  on public.crawl_sync_logs (created_at desc);

alter table public.crawl_sync_logs enable row level security;
-- crawl_sync_logs는 공개 정책 없음 → service role만 접근 가능

-- ── RPC: 곡 마스터 전체 반영 (upsert + 누락분 in_ac=false, 단일 트랜잭션) ──
-- p_payload = {"versions":[{"id","name"}], "songs":[{"tag","title","genre","artist","version","charts":[...]}]}
create or replace function public.sync_song_master(
  p_payload jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
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

-- service role 전용: 일반 클라이언트에서 호출 차단
revoke execute on function public.sync_song_master(jsonb)
  from anon, authenticated;
