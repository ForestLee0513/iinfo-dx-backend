-- ─────────────────────────────────────────────────────────────
-- 어드민 회원 목록 조회 RPC
--
-- GoTrue Admin API는 provider·정지 여부 필터를 지원하지 않아
-- auth.users를 직접 조회한다. 이메일 부분 일치 / 가입 경로 /
-- 정지 여부 필터와 페이지네이션을 DB에서 한 번에 처리하고,
-- {"users": [...], "total": n} 형태의 jsonb를 반환한다.
--
-- security definer로 auth 스키마에 접근하므로 service_role만
-- 실행할 수 있게 권한을 제한한다 (백엔드 전용).
-- ─────────────────────────────────────────────────────────────

create or replace function public.admin_list_users(
    p_email     text    default null,   -- 이메일 부분 일치 (ilike)
    p_provider  text    default null,   -- 가입 경로 (대표 provider 또는 providers 배열 포함 여부)
    p_is_banned boolean default null,   -- true = 정지 중, false = 활성
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
        (b.user_id is not null) as is_banned,
        b.reason as ban_reason,
        b.ban_until
    from auth.users u
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
revoke execute on function public.admin_list_users(text, text, boolean, int, int)
    from public, anon, authenticated;
grant execute on function public.admin_list_users(text, text, boolean, int, int)
    to service_role;
