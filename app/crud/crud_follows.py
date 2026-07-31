"""팔로우 관계 CRUD — user_follows 테이블 접근."""

from app.db.session import get_supabase


def follow(follower_id: str, followee_id: str) -> None:
    """팔로우한다 — 이미 팔로우 중이면 그대로 둔다(멱등, upsert)."""
    sb = get_supabase()
    sb.table("user_follows").upsert(
        {"follower_id": follower_id, "followee_id": followee_id}
    ).execute()


def unfollow(follower_id: str, followee_id: str) -> None:
    """언팔로우한다 — 팔로우 중이 아니었어도 에러 없이 끝난다(멱등)."""
    sb = get_supabase()
    sb.table("user_follows").delete().eq("follower_id", follower_id).eq(
        "followee_id", followee_id
    ).execute()


def is_following(follower_id: str, followee_id: str) -> bool:
    sb = get_supabase()
    result = (
        sb.table("user_follows")
        .select("follower_id")
        .eq("follower_id", follower_id)
        .eq("followee_id", followee_id)
        .maybe_single()
        .execute()
    )
    return bool(result and result.data)


def followers_count(user_id: str) -> int:
    sb = get_supabase()
    result = (
        sb.table("user_follows")
        .select("follower_id", count="exact")
        .eq("followee_id", user_id)
        .range(0, 0)
        .execute()
    )
    return result.count or 0


def following_count(user_id: str) -> int:
    sb = get_supabase()
    result = (
        sb.table("user_follows")
        .select("followee_id", count="exact")
        .eq("follower_id", user_id)
        .range(0, 0)
        .execute()
    )
    return result.count or 0


def list_followers(user_id: str, page: int, per_page: int) -> tuple[list[str], int]:
    """user_id를 팔로우하는 사람들의 user_id 목록(최신 순) + 전체 개수."""
    sb = get_supabase()
    start = (page - 1) * per_page
    result = (
        sb.table("user_follows")
        .select("follower_id", count="exact")
        .eq("followee_id", user_id)
        .order("created_at", desc=True)
        .range(start, start + per_page - 1)
        .execute()
    )
    return [row["follower_id"] for row in result.data], result.count or 0


def list_following(user_id: str, page: int, per_page: int) -> tuple[list[str], int]:
    """user_id가 팔로우하는 사람들의 user_id 목록(최신 순) + 전체 개수."""
    sb = get_supabase()
    start = (page - 1) * per_page
    result = (
        sb.table("user_follows")
        .select("followee_id", count="exact")
        .eq("follower_id", user_id)
        .order("created_at", desc=True)
        .range(start, start + per_page - 1)
        .execute()
    )
    return [row["followee_id"] for row in result.data], result.count or 0
