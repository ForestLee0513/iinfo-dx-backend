-- ─────────────────────────────────────────────────────────────
-- songs.title UNIQUE 제약 제거
--
-- IIDX에는 동명이곡(리메이크, 시리즈 재수록 등)이 실제로 존재한다. sync_song_master
-- RPC는 textage_tag(안정 식별자, CLAUDE.md 참고)로 upsert하므로, 서로 다른 곡이
-- 같은 title을 가지면 songs_title_key UNIQUE 제약에 걸려 전체 동기화가
-- "duplicate key value violates unique constraint songs_title_key"로 실패한다.
--
-- title은 유일성을 강제할 이유가 없으므로 제약만 제거하고, 검색 성능을 위해
-- (제약이 제공하던 인덱스를 대체하는) 일반 인덱스를 새로 만든다.
-- ─────────────────────────────────────────────────────────────

alter table public.songs drop constraint if exists songs_title_key;

create index if not exists idx_songs_title on public.songs (title);
