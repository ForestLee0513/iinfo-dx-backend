# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Backend API for an IIDX difficulty-table service. It **crawls** a song master (textage.cc) and difficulty tables (mostly Google Sheets `pubhtml`), stores them in **Supabase**, and serves them to a **user-facing client** and an **admin FE** — both of which live in **separate repos**. This repo is the API only (including the admin API); the Next.js client and admin UI are not here.

## Run / develop

Requires **Python 3.12** (code uses 3.10+ `X | None` unions and evaluates them at runtime via pydantic). Note: a bare `python3` on the host may be 3.9 and will fail to import the app — use the venv or Docker.

```bash
# Docker (recommended) — hot-reload via ./app volume mount + --reload; starts Redis too
docker compose up --build            # add -d for background
docker compose logs -f api

# Local venv (needs a reachable Redis at REDIS_URL for admin/job features)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload        # /docs = client spec, /internal/docs = full spec
```

`cp .env.example .env` and fill in Supabase values before running. `requirements.txt` change → `docker compose up --build`. Crawl targets and their schedules live in Supabase (`crawl_targets`/`crawl_schedules` tables, see `app/crud/crud_crawl_targets.py`) with no env fallback — register targets via `POST /api/v1/crawl/targets` before anything will run. This means they survive a Redis flush/restart; Redis is still required for **job execution state** (checkpoints, resume-on-restart) — see the crawl API note below.

**No test suite and no linter are configured** (no pytest/ruff/pyproject). Don't claim tests pass. For static checking without a full environment, `python3 -m py_compile $(find app -name '*.py')` catches syntax errors (works even on 3.9); real import/runtime checks need the 3.12 venv.

## Architecture — layered (fastapi-template style), domain services kept intact

Single FastAPI app. Routers live in `app/api/v1/endpoints/`, aggregated by `app/api/v1/api.py` into one `api_router` that `app/main.py` (the composition root) mounts at `/api/v1`. Technical concerns are split into layers (`api` / `core` / `db` / `crud` / `schemas`), and the crawler/parser/scheduler subsystems stay as **domain packages** under `app/services/` (the layered template has no slot for them). URL paths are unchanged from the old modular layout.

| Prefix | Endpoint router | Role |
|--------|-----------------|------|
| `/api/v1/web/tables`, `/web/auth` | `endpoints/tables.py`, `endpoints/auth.py` | User-client API: public table reads (`/tables`, `/tables/{slug}`) + auth (`/auth/...`, incl. `/auth/me`). |
| `/api/v1/crawl` | `endpoints/crawl.py` | Unified crawl API for both song-master and difficulty-table crawling: `/targets`, `/preview` are public diagnostics (no auth, exposed on `/docs`); `/jobs`, `/jobs/{id}`, `/schedules`, `/schedules/{target_key}` are `ADMIN`-only manual-run + per-target schedule CRUD (**the scheduler lives in `app/services/difficulty_crawl/scheduler.py`**). |
| `/api/v1/admin` | `endpoints/admin.py` | Admin API for the separate admin FE, **user management only** (crawl moved to `/api/v1/crawl`): user list/detail (`/users`), bans (`/users/{id}/ban`), role updates (`/users/{id}/role`). Auth + `ADMIN`/`SUPER_ADMIN` role. |
| `/api/v1/health` | `endpoints/health.py` | Health check. |

Layers: `app/api/deps.py` = FastAPI auth/role dependencies (`get_current_user`, `require_role`, `AdminUser`). `app/core` = `config.py` (pydantic-settings from `.env`), `security.py` (token verify primitives — `decode_token`/`extract_app_role`/`bearer_scheme`), `openapi.py` (public/internal docs). `app/db` = `session.py` (Supabase service-role client) + `redis.py` (async Redis). `app/crud` = Supabase data access (`crud_tables.py` reads, `crud_songs.py`/`crud_difficulty.py` the `sync_*` RPC wrappers). `app/schemas` = all Pydantic models. `app/services` = domain logic (`auth_service.py` GoTrue gateway, `admin/` job runner+store, `songs_crawl/` + `difficulty_crawl/` crawlers/parsers/pipeline/scheduler). There is **no `app/models`** — this app has no ORM; data lives in Supabase and is accessed via RPCs (see Supabase schema below).

### Data flow (one direction)
Crawl scheduling is **per-target**, not a single weekly job: `run_song_sync` (`services/songs_crawl` → `sync_song_master` RPC) and `run_table_sync` (`services/difficulty_crawl` → `sync_table_result` RPC) each accept an optional `target_id` (a target registered in Redis, looked up via `app/services/admin/targets.py`) or `target` (ad-hoc dict, `crawler` key required — bypasses the registry entirely, never persisted) and run independently, on whatever schedule an admin configured for that target (`app/services/difficulty_crawl/scheduler.py`). There is **no code-enforced ordering** between song-master and table crawls anymore — a table crawl can run before that week's song-master update. This is tolerated: table sync always proceeds on the last-synced song master, song-master failures never block it. The table-read endpoints (`endpoints/tables.py` → `crud/crud_tables.py`) read back from Supabase (public RLS read). Manual runs go through `POST /api/v1/crawl/jobs` only (RPCs are service-role only: `sync_song_master`, `sync_table_result`) — `target_id` references a registered target, `target` lets an ADMIN hand-supply the crawl config (crawler + url etc.) at request time and sync it immediately without ever registering it (deliberate expansion of the ADMIN trust boundary — the server will fetch whatever URL the request specifies).

### Crawl API — jobs & per-target schedules (Redis-backed)
`app/api/v1/endpoints/crawl.py` is the single router for both song and table crawling: `GET /targets` and `POST /preview` are public (no auth, `openapi_extra=PUBLIC`); `POST /targets`, `GET/PUT/DELETE /targets/{target_key}`, `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET/PUT/DELETE /schedules[/{target_key}]` require `AdminUser`. Backing services live in `app/services/admin` (`jobs.py` runner, `store.py` persistence facade, `targets.py` the crawl-target registry) plus `app/crud/crud_crawl_targets.py` (the actual Supabase I/O, sync — called via `asyncio.to_thread` from `store.py`). **Crawl targets and their schedules are Supabase data** — `crawl_targets`/`crawl_schedules` tables (migration `20260718000000_crawl_targets_schedules.sql`), row key = `target_key = f"{kind}:{id}"` (e.g. `table:5ch_sp12`); `crawl_targets` holds kind/id/label/crawler + a `config` jsonb column for crawler-specific keys (url/play_style/level/...), `crawl_schedules.target_key` references `crawl_targets.target_key` `on delete cascade`. `store.py` exposes the exact same async function names for targets/schedules regardless of backend, so callers (`targets.py`, `scheduler.py`, `endpoints/crawl.py`) don't know or care that Redis isn't involved here. There is no `SONG_CRAWL_TARGETS`/`TABLE_CRAWL_TARGETS` env fallback — a target must be created via `POST /crawl/targets` before it can be referenced by `target_id`, scheduled, or shown in `GET /crawl/targets` (dropdown source for the admin dashboard; the minimal public response there omits crawler-specific config, exposed only via the ADMIN-only `GET /crawl/targets/{target_key}`). Deleting a target (`DELETE /crawl/targets/{target_key}`) cascades at the DB level (its schedule row goes with it) and the endpoint also unregisters its live APScheduler job. Both manual and scheduled runs go through `jobs.create_job`/`execute_job`, which checkpoints per step **in Redis** (job execution state is intentionally still Redis-only — it's volatile "is this running right now" data, not config): on startup `resume_interrupted_job` re-runs unfinished steps of a job that was cut off mid-run (safe because crawl→RPC is upsert/full-replace, i.e. idempotent). One job at a time — the "current job" key in Redis is the lock; stale RUNNING leftovers are auto-failed by the next `create_job`. Each target's schedule (`enabled` + a list of `{day, hour, minute}` triggers — one target can have several weekly slots, combined into a single APScheduler job via `OrTrigger`) is set via `PUT /crawl/schedules/{target_key}`. If Supabase is unreachable at startup, `scheduler.start()` can't enumerate targets/schedules so nothing gets scheduled (logged, not fatal); if Redis (not Supabase) goes down mid-run, a scheduled run still crawls but untracked (fallback in `scheduler.run_target_sync`), and job-tracking endpoints (`/crawl/jobs*`) return 503 — target/schedule CRUD endpoints are unaffected by Redis outages since they no longer touch it. Admin access = valid Supabase token **and** `app_metadata.role == "ADMIN"` on the Supabase account (granted via SQL on `auth.users.raw_app_meta_data` — see README; takes effect on next token refresh). Roles live in `app/schemas/user.py` (`UserRole` + `ROLE_LEVELS`, hierarchy-based so higher roles include lower ones); protect endpoints with `require_role(UserRole.X)` from `app/api/deps.py`. Unknown/missing role ⇒ `USER`. **Reverse-proxy note**: `GET /api/v1/crawl/targets` and `/api/v1/crawl/preview` are meant to be public, but `POST/GET/PUT/DELETE /api/v1/crawl/targets/*` (create/detail/update/delete), `/api/v1/crawl/jobs*`, and `/api/v1/crawl/schedules*` need the same public-domain blocking as `/api/v1/admin`/`/internal` — since they no longer live under `/admin`, the proxy rule must match these sub-paths explicitly rather than blocking by a single prefix.

### Crawler plugin pattern
To add a difficulty table you normally touch **only** `app/services/difficulty_crawl/crawlers/` and register a target — schema and sync code stay untouched:
1. Implement a crawler class in `app/services/difficulty_crawl/crawlers/` decorated with `@register("name")` (`base.py` defines the `Crawler` protocol, `TableDef`/`TableResult`, and the registry). NUMERIC tables usually only need to fill in `numeric_example.py`'s `_parse`.
2. Register a target via `POST /crawl/targets` with `{"kind": "table", "id": "unique-id", "crawler": "name", "label": "...", ...}`. The `crawler` key selects the registered crawler, `id` must be unique within `kind="table"` (it's the admin per-target schedule key), other keys are that crawler's `target` config.

Two rating systems, carried through everywhere: **GRADE** (ordered `grades[]`, e.g. F..S+) vs **NUMERIC** (float `rating`). A `TableResult` is `TableDef` + a list of entry dicts; `POST /crawl/preview` (`kind="table"`) returns exactly what would be synced.

The two crawl subsystems have **separate registries**: song crawlers (`services/songs_crawl`, `SongMasterResult`) vs table crawlers (`services/difficulty_crawl`, `TableDef`/`TableResult`) — both draw their targets from the same Redis-backed registry (`app/services/admin/targets.py`), filtered by `kind`. Adding a difficulty table touches only `app/services/difficulty_crawl/crawlers/` + a `POST /crawl/targets` call (`kind="table"`); adding a song source touches only `app/services/songs_crawl/crawlers/` + a `POST /crawl/targets` call (`kind="song"`).

### textage song master crawler
`textage` (`app/services/songs_crawl/crawlers/textage.py`) fetches `titletbl.js` / `actbl.js` (Shift-JIS — decode with `cp932`; titles contain HTML tags) and returns a `SongMasterResult` → `sync_song_master` RPC (upsert `versions` / `songs` / `charts`; rows missing from the crawl get `in_ac=false`, never deleted). `songs.textage_tag` is the stable song identifier.

`actbl` parsing gotchas (all verified against textage's own `scrlist.js` rendering code — re-check there if the site changes):
- **`actbl` is the canonical AC song list** (`scrlist.js` iterates `for (tag in mt)` over actbl). `titletbl` has ~87 extra rows (CS-only songs, the `__dmy__` dummy, chart-viewer-only variants) that must be skipped, or the song count won't match textage's own list page.
- Row bodies can contain `]` inside title strings (`Friction[!]Function`, `[ ]DENTITY`, artist `"[x]"`) — the entry regex must skip quoted strings, not just stop at the first `]`, or those rows silently truncate and drop.
- Slot layout: `actbl[tag][type*2+1]` = level, `[type*2+2]` = option bits (`get_level`). Levels 10–15 appear as bare identifiers `A`–`F`, and `titletbl` uses `SS`(=35) for substream — the tokenizer maps these; dropping them silently shifts rows and caps every level at 9.
- Deleted-song detection: `actbl[tag][0]` bit0 == 0 (rendered as `class=tt2`/firebrick on textage) → `in_ac=false`. **Only** this flag decides deletion — inline title colors like `.fontcolor("#ff4080")` are decoration (new-song markers etc.), never deletion markers.
- Titles carry JS `.fontcolor("...")` calls; strip the call pattern only, never `#`-prefixed strings themselves — real song titles look like color codes (`#CMFLG`, `#The_Relentless`).
- Chart inclusion: option bit `&4` = "in AC" (`get_sdata`'s `acin`); charts without it are omitted so the RPC marks them `in_ac=false`.
- Full-line `//` comments in the JS files contain plausible-looking rows — strip them before parsing.

textage is a single-maintainer hobby site — two file fetches per weekly run, nothing more aggressive.

### Google Sheets parser gotcha
`app/services/difficulty_crawl/parsers/sheet_parser.py` finds the series-header row by matching `NNN譜面` (a **song count that changes over time**) with `SERIES_HEADER_RE`, never a hardcoded number. If you touch header detection, keep it pattern-based or a table will silently parse to empty.

### Auth
Frontend logs in with Supabase directly (Google OAuth / email) and sends `Authorization: Bearer <access_token>`. The backend only **verifies** tokens (`app/core/security.py` + `app/api/deps.py`): JWKS/RS256 by default, or HS256 when `SUPABASE_JWT_SECRET` is set (legacy). Protect an endpoint by taking a `CurrentUser` param (or `dependencies=[Depends(get_current_user)]` for a whole router); for role-gated endpoints use `require_role(UserRole.ADMIN)` (reads `app_metadata.role` from the JWT — no DB lookup).

## Conventions
- Comments and docstrings are in **Korean** — match this.
- The Supabase Python client is **synchronous**; in async paths wrap calls in `asyncio.to_thread` (see `pipeline.py`).
- Reads and writes share one service-role client (`app/db/session.py get_supabase`); `SUPABASE_SERVICE_ROLE_KEY` is required for both.
- Endpoints declare `response_model` so the docs show real schemas; request bodies use typed models (not bare `dict`) for the same reason.
- **Two OpenAPI docs** (`app/core/openapi.py`, mounted by `setup_docs` in `main.py`): `/docs`+`/openapi.json` = public client spec, **opt-in only** — an endpoint appears there iff decorated with `openapi_extra=PUBLIC` (used by `/crawl/targets`, `/crawl/preview`). `/internal/docs` = everything (admin + crawl jobs/schedules included). New endpoints are internal-only by default; this controls documentation, not access — `/internal` and `/api/v1/admin` must be blocked at the reverse proxy on the public domain, and now so must `/api/v1/crawl/jobs*`/`/api/v1/crawl/schedules*` (see the crawl API note above — they moved out of `/admin`).
- `OAUTH_PROVIDERS` / `OAUTH_ALLOWED_REDIRECT_URLS` / `ADMIN_ALLOWED_REDIRECT_URLS` / `CORS_ORIGINS` in `.env` are each a **JSON array on one line**. (Crawl targets are no longer env-configured — see the crawl API note above.)
- Inside docker compose, `REDIS_URL` is overridden to `redis://redis:6379/0`; the `.env` value (`localhost`) is for local venv runs.

## Supabase schema
Migrations in `supabase/migrations/`. Difficulty tables: `difficulty_tables`, `difficulty_entries` (fully **replaced** per sync), `crawl_sync_logs`. `sync_table_result` is a `security definer` RPC doing table upsert + entry replace + log in one transaction. Song master: `versions`, `songs` (`textage_tag` unique), `charts`; `sync_song_master` is a `security definer` RPC doing versions/songs/charts upsert + `in_ac=false` for rows missing from the crawl (never deletes). RLS: public read on all of the above; all writes are service-role only.
