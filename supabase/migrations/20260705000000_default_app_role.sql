-- ─────────────────────────────────────────────────────────────
-- 신규 가입자 기본 역할(app_metadata.role) = USER 부여
--
-- OAuth(구글 등)·이메일 가입 상관없이 auth.users에 행이 생길 때
-- raw_app_meta_data.role이 비어 있으면 'USER'를 채운다.
-- (백엔드 extract_app_role은 이미 미설정 시 USER로 강등하지만,
--  계정 레코드에 역할을 명시적으로 남겨 대시보드·관리에서 보이게 한다.)
--
-- BEFORE INSERT 트리거로 NEW 행을 직접 수정한다. 이미 role이 지정된
-- 경우(예: 관리자가 ADMIN으로 생성)는 덮어쓰지 않는다.
-- ─────────────────────────────────────────────────────────────

create or replace function public.set_default_app_role()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.raw_app_meta_data ->> 'role' is null then
    new.raw_app_meta_data =
      coalesce(new.raw_app_meta_data, '{}'::jsonb)
      || jsonb_build_object('role', 'USER');
  end if;
  return new;
end;
$$;

drop trigger if exists set_default_app_role_on_signup on auth.users;
create trigger set_default_app_role_on_signup
  before insert on auth.users
  for each row
  execute function public.set_default_app_role();
