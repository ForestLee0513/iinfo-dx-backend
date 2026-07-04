-- ─────────────────────────────────────────────────────────────
-- difficulty_entries.table_type 추가
--
-- 5ch 표는 지력표/개인차표를 별도 표로 나누지 않고 하나로 합친다.
-- 대신 곡 엔트리마다 table_type으로 구분한다:
--   'STRENGTH' = 지력, 'PERSONAL' = 개인차
-- 구분이 없는 표(NUMERIC 등)는 null.
--
-- sync_table_result RPC도 table_type을 함께 반영하도록 갱신한다.
-- ─────────────────────────────────────────────────────────────

alter table public.difficulty_entries
  add column if not exists table_type text
    check (table_type in ('STRENGTH', 'PERSONAL'));

-- ── RPC 갱신: 엔트리 교체 시 table_type 포함 ──
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

  insert into difficulty_entries (table_id, title, series, play_style, difficulty, level, grade, rating, table_type)
  select v_table_id, e.title, e.series, e.play_style, e.difficulty, e.level, e.grade, e.rating, e.table_type
  from jsonb_to_recordset(p_entries) as e(
    title text, series text, play_style text, difficulty text,
    level int, grade text, rating numeric, table_type text
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

revoke execute on function public.sync_table_result(jsonb, jsonb, text)
  from anon, authenticated;
