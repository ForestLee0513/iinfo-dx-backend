-- ─────────────────────────────────────────────────────────────
-- 곡 마스터 스키마 (versions / songs / charts) 복원
--
-- 2026-07-07 커밋(c4629a0 "임시로 적용한 마이그레이션 sql 삭제")에서 곡 마스터
-- 테이블을 만드는 원본 마이그레이션(20260703000000_song_master.sql)이 저장소에서
-- 삭제됐고, 이후 20260718010000_song_master_sync.sql 은 RPC(sync_song_master)와
-- crawl_sync_logs 만 복원했다. 그 결과 이 저장소의 마이그레이션만으로는 빈 DB를
-- 재구성할 수 없었다 — RPC/드롭 마이그레이션이 존재하지 않는 테이블을 참조한다.
--
-- 여기서는 실제 운영 Supabase 에 존재하는 구조 그대로 세 테이블을 (없을 때만)
-- 만든다. 삭제됐던 초안과 다른 실제 컬럼을 반영한다:
--   - songs.version  (version_id 아님, smallint) → versions(id) 참조
--   - versions.id smallint, versions.abbrev 존재
--   - songs.bpm(text) / songs.series 존재, songs.title 은 UNIQUE 아님(동명이곡 허용)
--   - charts.id uuid, charts.notes(int), charts.level smallint
--
-- 이 마이그레이션은 sync_song_master RPC(20260718010000)보다 먼저 실행되도록
-- 타임스탬프를 앞에 둔다. 운영 DB에는 이미 세 테이블이 있으므로 create if not
-- exists 로 안전하게 no-op 이 된다.
-- ─────────────────────────────────────────────────────────────

-- ── IIDX 시리즈 버전 (textage VERINDEX 기준, substream=35 / CS=0) ──
create table if not exists public.versions (
  id     smallint primary key,                     -- textage VERINDEX
  name   text not null,                            -- '1st style', 'SINOBUZ' 등
  abbrev text                                       -- 약칭 (미사용 시 null)
);

-- ── 곡 마스터 (textage_tag 가 안정 식별자) ──
create table if not exists public.songs (
  id          uuid primary key default gen_random_uuid(),
  textage_tag text unique,                          -- textage 태그 (안정 식별자)
  title       text not null,                        -- 동명이곡이 있으므로 UNIQUE 아님
  series      text,
  genre       text,
  artist      text,
  bpm         text,                                 -- 'min-max' 형태가 있어 text
  version     smallint references public.versions (id),
  in_ac       boolean not null default true,        -- 현행 AC 수록 여부 (크롤에서 빠지면 false)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists idx_songs_title   on public.songs (title);
create index if not exists idx_songs_version on public.songs (version);

-- ── 곡별 채보 (SP/DP × 난이도) ──
create table if not exists public.charts (
  id         uuid primary key default gen_random_uuid(),
  song_id    uuid not null references public.songs (id) on delete cascade,
  play_style text not null,                         -- 'SP' | 'DP'
  difficulty text not null,                         -- BEGINNER/NORMAL/HYPER/ANOTHER/LEGGENDARIA
  level      smallint,
  notes      int,                                   -- 노트 수 (미수집 시 null)
  in_ac      boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (song_id, play_style, difficulty)
);

create index if not exists idx_charts_song_id on public.charts (song_id);

-- ── RLS: 곡 마스터는 공개 읽기, 쓰기는 service role 전용 ──
alter table public.versions enable row level security;
alter table public.songs    enable row level security;
alter table public.charts   enable row level security;

drop policy if exists "public read versions" on public.versions;
create policy "public read versions"
  on public.versions for select using (true);

drop policy if exists "public read songs" on public.songs;
create policy "public read songs"
  on public.songs for select using (true);

drop policy if exists "public read charts" on public.charts;
create policy "public read charts"
  on public.charts for select using (true);
