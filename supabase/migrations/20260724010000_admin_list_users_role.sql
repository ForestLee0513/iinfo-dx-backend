-- ─────────────────────────────────────────────────────────────
-- 어드민 회원 목록 조회 RPC — 역할(role) 포함 버전
--
-- 권한 관리 화면이 상세를 일일이 조회하지 않고 목록 API 하나로
-- 각 회원의 역할을 파악·필터링할 수 있도록, user_profiles.role을
-- 조인해 응답에 role을 추가하고 p_role 필터 파라미터를 더한다.
-- 프로필 행이 없는 회원(트리거 도입 전 가입자)은 'USER'로 간주한다.
--
-- 파라미터가 늘어 시그니처가 바뀌므로 기존 5-인자 함수를 먼저 드롭한다.
-- ─────────────────────────────────────────────────────────────

drop function if exists public.admin_list_users(text, text, boolean, int, int);

create or replace function public.admin_list_users(
    p_email     text    default null,   -- 이메일 부분 일치 (ilike)
    p_provider  text    default null,   -- 가입 경로 (대표 provider 또는 providers 배열 포함 여부)
    p_is_banned boolean default null,   -- true = 정지 중, false = 활성
    p_role      text    default null,   -- 역할 필터 (USER | ADMIN | SUPER_ADMIN)
    p_page      int     default 1,
    p_per_page  int     default 20
)
returns jsonb
language sql
security definer
set search_path = public
as $$
with filtered as (
    select
        u.id,
        u.email,
        u.raw_app_meta_data ->> 'provider' as provider,
        u.created_at,
        u.last_sign_in_at,
        coalesce(p.role, 'USER') as role,
        (b.user_id is not null) as is_banned,
        b.reason as ban_reason,
        b.ban_until
    from auth.users u
    left join public.user_profiles p on p.user_id = u.id
    -- 현재 유효한 정지 레코드 (해제되지 않았고 만료 전인 것 중 최신 1건)
    left join lateral (
        select user_id, reason, ban_until
        from public.user_bans
        where user_id = u.id
          and lifted_at is null
          and (ban_until is null or ban_until > now())
        order by banned_at desc
        limit 1
    ) b on true
    where u.deleted_at is null
      and (p_email is null or u.email ilike '%' || p_email || '%')
      and (p_provider is null
           or u.raw_app_meta_data ->> 'provider' = p_provider
           or u.raw_app_meta_data -> 'providers' ? p_provider)
      and (p_is_banned is null or (b.user_id is not null) = p_is_banned)
      and (p_role is null or coalesce(p.role, 'USER') = p_role)
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

-- 함수는 기본적으로 public 실행 권한이 부여되므로 명시적으로 회수하고
-- service_role에만 부여한다.
revoke execute on function public.admin_list_users(text, text, boolean, text, int, int)
    from public, anon, authenticated;
grant execute on function public.admin_list_users(text, text, boolean, text, int, int)
    to service_role;
