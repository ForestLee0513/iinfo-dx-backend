-- ─────────────────────────────────────────────────────────────
-- 곡 마스터 스키마 (textage 크롤러)
--
-- versions        : IIDX 시리즈 버전 (textage VERINDEX 기준)
-- songs           : 곡 마스터 (textage_tag가 안정 식별자)
-- charts          : 곡별 채보 (SP/DP × 난이도, 레벨)
-- sync_song_master: 곡 마스터 전체를 원자적으로 반영하는 RPC (service role 전용)
--
-- 삭제 정책: 이번 크롤에 없는 곡/채보는 지우지 않고 in_ac=false 처리.
-- ─────────────────────────────────────────────────────────────

create table if not exists public.versions (
  id         int primary key,                    -- textage VERINDEX (substream=35)
  name       text not null,                      -- '1st style', 'RESIDENT' 등
  created_at timestamptz not null default now()
);

create table if not exists public.songs (
  id          uuid primary key default gen_random_uuid(),
  textage_tag text not null unique,              -- textage 태그 (안정 식별자)
  title       text not null,
  genre       text,
  artist      text,
  version_id  int references public.versions (id),
  in_ac       boolean not null default true,     -- 현행 AC 수록 여부 (크롤에서 빠지면 false)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists idx_songs_title on public.songs (title);
create index if not exists idx_songs_version_id on public.songs (version_id);

create table if not exists public.charts (
  id         bigint generated always as identity primary key,
  song_id    uuid not null references public.songs (id) on delete cascade,
  play_style text not null check (play_style in ('SP', 'DP')),
  difficulty text not null,                      -- BEGINNER/NORMAL/HYPER/ANOTHER/LEGGENDARIA
  level      int not null check (level between 1 and 12),
  in_ac      boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (song_id, play_style, difficulty)
);

create index if not exists idx_charts_song_id on public.charts (song_id);

-- ── RLS: 곡 마스터는 공개 읽기, 쓰기는 service role만 ─────────
alter table public.versions enable row level security;
alter table public.songs    enable row level security;
alter table public.charts   enable row level security;

create policy "versions_public_read"
  on public.versions for select
  to anon, authenticated
  using (true);

create policy "songs_public_read"
  on public.songs for select
  to anon, authenticated
  using (true);

create policy "charts_public_read"
  on public.charts for select
  to anon, authenticated
  using (true);

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
  select (v->>'id')::int, v->>'name'
  from jsonb_array_elements(p_payload->'versions') as v
  on conflict (id) do update set name = excluded.name;

  -- 2) 곡 upsert (textage_tag 기준). in_ac은 크롤러가 판별한 값을 사용
  --    (actbl 상태플래그 bit0 — textage에서 tt2/firebrick로 표시되는 AC 삭제곡).
  insert into songs (textage_tag, title, genre, artist, version_id, in_ac)
  select s->>'tag', s->>'title', s->>'genre', s->>'artist', (s->>'version')::int,
         coalesce((s->>'in_ac')::boolean, true)
  from jsonb_array_elements(p_payload->'songs') as s
  on conflict (textage_tag) do update set
    title      = excluded.title,
    genre      = excluded.genre,
    artist     = excluded.artist,
    version_id = excluded.version_id,
    in_ac      = excluded.in_ac,
    updated_at = now();

  -- 3) 채보 upsert
  insert into charts (song_id, play_style, difficulty, level, in_ac)
  select sg.id, c->>'play_style', c->>'difficulty', (c->>'level')::int, true
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
