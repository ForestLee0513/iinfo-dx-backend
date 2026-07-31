-- ─────────────────────────────────────────────────────────────
-- 유저 프로필 테이블
--
-- raw_app_meta_data 대신 별도 테이블로 프로필 설정을 관리한다.
-- is_public: 프로필 공개 여부 (기본 true)
-- role: 서비스 권한 (기본 'USER', CHECK 제약으로 허용 값 강제)
--       USER/ADMIN/SUPER_ADMIN — app/schemas/user.py UserRole 와 1:1.
--       SUPER_ADMIN 은 개발자 본인 전용이라 부분 UNIQUE 인덱스로 최대 1명만 허용.
-- handle: 닉네임 핸들. UUID 대신 이 값으로도 프로필을 조회할 수 있게 한다.
--         GET /web/profile/{identifier} 경로에 그대로 쓰이므로 영문/숫자/
--         밑줄만 허용한다. 대소문자만 다른 중복 가입을 막기 위해 유니크
--         인덱스는 lower(handle) 기준(표시용 원본 대소문자는 그대로 저장).
-- social_links: [{"platform": "...", "url": "..."}] 형태의 jsonb 배열 —
--         본인이 API로 자유롭게 추가/수정.
-- dj_name / dj_id: IIDX 인게임 DJ NAME(최대 6자, 영문/숫자)과
--         DJ ID(NNNN-NNNN, 숫자만). 사용자가 API로 직접 수정하지 않는다 —
--         북마크릿 데이터 갱신 파이프라인이 도입되면 그때 채워지고, 지금은
--         DB에 직접 등록만 한다.
-- profile_image_url: Supabase Storage에 업로드된 프로필 이미지의 URL.
--         업로드 플로우는 아직 없다 — 지금은 DB에 직접 등록만 한다.
--
-- 신규 가입 시 트리거가 기본값 행을 자동 삽입한다.
-- RLS: 본인은 읽기·수정 가능, 타인은 공개 프로필만 읽기 가능.
-- ─────────────────────────────────────────────────────────────

create table public.user_profiles (
    user_id           uuid        primary key references auth.users (id) on delete cascade,
    is_public         boolean     not null default true,
    role              text        not null default 'USER'
                                      check (role in ('USER', 'ADMIN', 'SUPER_ADMIN')),
    handle            text        check (handle is null or handle ~ '^[A-Za-z0-9_]{2,20}$'),
    social_links      jsonb       not null default '[]'::jsonb
                                      check (jsonb_typeof(social_links) = 'array'),
    dj_name           text        check (dj_name is null or dj_name ~ '^[A-Za-z0-9]{1,6}$'),
    dj_id             text        check (dj_id is null or dj_id ~ '^[0-9]{4}-[0-9]{4}$'),
    profile_image_url text,
    updated_at        timestamptz not null default now()
);

-- SUPER_ADMIN(개발자 본인)은 최대 1명만 존재하도록 DB에서 강제한다.
create unique index user_profiles_single_super_admin_idx
    on public.user_profiles (role)
    where role = 'SUPER_ADMIN';

-- handle 중복 방지(대소문자 무시) — NULL은 무제한 허용(아직 핸들을 정하지 않은 사용자)
create unique index user_profiles_handle_lower_key
    on public.user_profiles (lower(handle))
    where handle is not null;

alter table public.user_profiles enable row level security;

-- 본인 프로필 읽기
create policy "user_profiles_select_own"
    on public.user_profiles for select
    to authenticated
    using (auth.uid() = user_id);

-- 공개 프로필은 누구나 읽기
create policy "user_profiles_select_public"
    on public.user_profiles for select
    to anon, authenticated
    using (is_public = true);

-- 본인 프로필 수정
create policy "user_profiles_update_own"
    on public.user_profiles for update
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- ── 트리거: 신규 가입 시 기본 프로필 행 삽입 ─────────────────
create or replace function public.create_default_user_profile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.user_profiles (user_id)
  values (new.id)
  on conflict (user_id) do nothing;
  return new;
end;
$$;

drop trigger if exists create_default_user_profile_on_signup on auth.users;
create trigger create_default_user_profile_on_signup
    after insert on auth.users
    for each row
    execute function public.create_default_user_profile();
