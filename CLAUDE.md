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

`cp .env.example .env` and fill in Supabase values before running. `requirements.txt` change → `docker compose up --build`. Crawl targets and their schedules live in Supabase (`crawl_targets`/`crawl_schedules` tables, see `app/crud/iidx/crawl_targets.py`) — register targets via `POST /api/v1/iidx/crawl/targets` before anything will run. Redis is still required for **job execution state** (checkpoints, resume-on-restart) — see the crawl API note below.

**No test suite and no linter are configured** (no pytest/ruff/pyproject). Don't claim tests pass. For static checking without a full environment, `python3 -m py_compile $(find app -name '*.py')` catches syntax errors (works even on 3.9); real import/runtime checks need the 3.12 venv.

## Architecture — layered (fastapi-template style), domain services kept intact

Single FastAPI app, but organized **MSA-style by domain to mirror the 2-schema DB split** (`public`/`iidx`). Routers live in `app/api/v1/routes/`, split into `account/` (public schema, shared across services) and `iidx/` (the IIDX service), aggregated by `app/api/v1/api.py` into one `api_router` that `app/main.py` mounts at `/api/v1`. `crud/`, `schemas/`, `services/` are each likewise split into `account/` + `iidx/` subpackages (data-domain = which schema the data lives in). Dependency direction is one-way: `iidx` may import `account` (the shared base), never the reverse. Adding a service `<svc>` = add `routes/<svc>/`, `crud/<svc>/`, `schemas/<svc>/`, `services/<svc>/`, mount an `APIRouter(prefix="/<svc>")`, and create a `<svc>` schema.

| Prefix | Route module | Role |
|--------|-----------|------|
| `/api/v1/auth`, `/api/v1/profile` | `routes/account/auth.py`, `routes/account/profile.py` | **Shared account layer** (public schema): auth (`/auth/...`, incl. `/auth/me`, cookie-based refresh) + profile read/update + follows (`/profile/...`). Serves every service, not just IIDX. |
| `/api/v1/iidx/tables` | `routes/iidx/tables.py` | Public difficulty-table reads (`/tables`, `/tables/{slug}`). |
| `/api/v1/iidx/crawl` | `routes/iidx/crawl.py` | Unified song-master + difficulty-table crawl API: `/targets`, `/preview` are public diagnostics (on `/docs`); `/jobs`, `/schedules[/{target_key}]` are `ADMIN`-only (**scheduler in `app/services/iidx/difficulty_crawl/scheduler.py`**). |
| `/api/v1/admin`, `/api/v1/admin/auth` | `routes/admin/users.py`, `routes/admin/auth.py` | **Platform admin console** (governs all services, so *not* under `/iidx`): admin login/session (`/admin/auth/*`) + user list/detail, bans, role updates (`/admin/users*`). Operates on account-layer data (public schema); zero iidx dependency. Auth + `ADMIN`/`SUPER_ADMIN`. |
| `/api/v1/iidx/admin` | `routes/iidx/admin_catalog.py` | IIDX catalog reads (songs/tables/versions), `ADMIN`-gated — IIDX service data, so stays under `/iidx`. |
| `/api/v1/health` | `routes/health.py` | Health check (service-agnostic). |

Layers: `app/api/deps.py` = FastAPI auth/role dependencies (`get_current_user`, `require_role`, `AdminUser`). `app/core` = `config.py` (pydantic-settings from `.env`), `security.py` (token verify primitives — `decode_token`/`bearer_scheme`; `extract_app_role` is legacy/unused), `openapi.py` (public/internal docs). `app/db` = `session.py` (two schema-pinned Supabase service-role clients — `get_supabase` = public, `get_supabase_svc` = iidx) + `redis.py` (async Redis). `app/crud` = Supabase data access, split `account/` (`bans`/`follows`/`profiles`/`users`) + `iidx/` (`songs`/`tables`/`difficulty` sync RPC wrappers + `crawl_targets`); imported with `as crud_<name>` aliases so call sites read `crud_bans.` etc. `app/schemas` = Pydantic models, split `account/` + `iidx/`. `app/services` = domain logic, split `account/` (`auth_service.py` GoTrue gateway) + `iidx/` (`admin/` job runner+store+targets, `songs_crawl/` + `difficulty_crawl/` crawlers/parsers/pipeline/scheduler). There is **no `app/models`** — no ORM; data lives in Supabase, accessed via RPCs + PostgREST (see Supabase schema below).

### Data flow (one direction)

Crawl scheduling is **per-target**, not a single weekly job: `run_song_sync` (`services/iidx/songs_crawl` → `sync_song_master` RPC) and `run_table_sync` (`services/iidx/difficulty_crawl` → `sync_table_result` RPC) each accept an optional `target_id` (a target registered in Supabase, looked up via `app/services/iidx/admin/targets.py`) or `target` (ad-hoc dict, `crawler` key required — bypasses the registry entirely, never persisted) and run independently, on whatever schedule an admin configured for that target (`app/services/iidx/difficulty_crawl/scheduler.py`). There is **no code-enforced ordering** between song-master and table crawls anymore — a table crawl can run before that week's song-master update. This is tolerated: table sync always proceeds on the last-synced song master, song-master failures never block it. The table-read endpoints (`routes/iidx/tables.py` → `crud/iidx/tables.py`) read back from Supabase (public RLS read). Manual runs go through `POST /api/v1/iidx/crawl/jobs` only (RPCs are service-role only: `sync_song_master`, `sync_table_result`) — `target_id` references a registered target, `target` lets an ADMIN hand-supply the crawl config (crawler + url etc.) at request time and sync it immediately without ever registering it (deliberate expansion of the ADMIN trust boundary — the server will fetch whatever URL the request specifies).

### Crawl API — jobs & per-target schedules (Redis-backed)

`app/api/v1/routes/iidx/crawl.py` is the single router for both song and table crawling: `GET /targets` and `POST /preview` are public (no auth, `openapi_extra=PUBLIC`); `POST /targets`, `GET/PUT/DELETE /targets/{target_key}`, `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET/PUT/DELETE /schedules[/{target_key}]` require `AdminUser`. Backing services live in `app/services/iidx/admin` (`jobs.py` runner, `store.py` persistence facade, `targets.py` the crawl-target registry) plus `app/crud/iidx/crawl_targets.py` (the actual Supabase I/O, sync — called via `asyncio.to_thread` from `store.py`). **Crawl targets and their schedules are Supabase data** — `iidx.crawl_targets`/`iidx.crawl_schedules` tables (baseline migration; service-role-only, accessed via `get_supabase_svc`), row key = `target_key = f"{kind}:{id}"` (e.g. `table:5ch_sp12`); `crawl_targets` holds kind/id/label/crawler + a `config` jsonb column for crawler-specific keys (url/play_style/level/...), `crawl_schedules.target_key` references `crawl_targets.target_key` `on delete cascade`. `store.py` exposes the exact same async function names for targets/schedules regardless of backend, so callers (`targets.py`, `scheduler.py`, `routes/iidx/crawl.py`) don't know or care that Redis isn't involved here. A target must be created via `POST /iidx/crawl/targets` before it can be referenced by `target_id`, scheduled, or shown in `GET /iidx/crawl/targets` (dropdown source for the admin dashboard; the minimal public response there omits crawler-specific config, exposed only via the ADMIN-only `GET /iidx/crawl/targets/{target_key}`). Deleting a target (`DELETE /iidx/crawl/targets/{target_key}`) cascades at the DB level (its schedule row goes with it) and the endpoint also unregisters its live APScheduler job. Both manual and scheduled runs go through `jobs.create_job`/`execute_job`, which checkpoints per step **in Redis** (job execution state is intentionally still Redis-only — it's volatile "is this running right now" data, not config): on startup `resume_interrupted_job` re-runs unfinished steps of a job that was cut off mid-run (safe because crawl→RPC is upsert/full-replace, i.e. idempotent). One job at a time — the "current job" key in Redis is the lock; stale RUNNING leftovers are auto-failed by the next `create_job`. Each target's schedule (`enabled` + a list of `{day, hour, minute}` triggers — one target can have several weekly slots, combined into a single APScheduler job via `OrTrigger`) is set via `PUT /iidx/crawl/schedules/{target_key}`. If Supabase is unreachable at startup, `scheduler.start()` can't enumerate targets/schedules so nothing gets scheduled (logged, not fatal); if Redis (not Supabase) goes down mid-run, a scheduled run still crawls but untracked (fallback in `scheduler.run_target_sync`), and job-tracking endpoints (`/iidx/crawl/jobs*`) return 503 — target/schedule CRUD endpoints are unaffected by Redis outages since they no longer touch it. Admin access = valid Supabase token **and** effective role ≥ `ADMIN`, i.e. `iidx.profiles.service_role == 'ADMIN'` (or `public.profiles.platform_role == 'SUPER_ADMIN'`) — a DB lookup per request, not a JWT claim, so grants take effect immediately (see README "어드민 권한 부여"). Roles live in `app/schemas/account/user.py` (`UserRole` + `ROLE_LEVELS`, hierarchy-based so higher roles include lower ones); protect endpoints with `require_role(UserRole.X)` from `app/api/deps.py`. Unknown/missing role ⇒ `USER`. **Reverse-proxy note**: `GET /api/v1/iidx/crawl/targets` and `/api/v1/iidx/crawl/preview` are meant to be public, but `POST/GET/PUT/DELETE /api/v1/iidx/crawl/targets/*` (create/detail/update/delete), `/api/v1/iidx/crawl/jobs*`, and `/api/v1/iidx/crawl/schedules*` need the same public-domain blocking as `/api/v1/admin`, `/api/v1/iidx/admin`, and `/internal` — since they no longer live under `/admin`, the proxy rule must match these sub-paths explicitly rather than blocking by a single prefix.

### Crawler plugin pattern

To add a difficulty table you normally touch **only** `app/services/iidx/difficulty_crawl/crawlers/` and register a target — schema and sync code stay untouched:

1. Implement a crawler class in `app/services/iidx/difficulty_crawl/crawlers/` decorated with `@register("name")` (`base.py` defines the `Crawler` protocol, `TableDef`/`TableResult`, and the registry). NUMERIC tables usually only need to fill in `numeric_example.py`'s `_parse`.
2. Register a target via `POST /iidx/crawl/targets` with `{"kind": "table", "id": "unique-id", "crawler": "name", "label": "...", ...}`. The `crawler` key selects the registered crawler, `id` must be unique within `kind="table"` (it's the admin per-target schedule key), other keys are that crawler's `target` config.

Two rating systems, carried through everywhere: **GRADE** (ordered `grades[]`, e.g. F..S+) vs **NUMERIC** (float `rating`). A `TableResult` is `TableDef` + a list of entry dicts; `POST /iidx/crawl/preview` (`kind="table"`) returns exactly what would be synced.

The two crawl subsystems have **separate registries**: song crawlers (`services/iidx/songs_crawl`, `SongMasterResult`) vs table crawlers (`services/iidx/difficulty_crawl`, `TableDef`/`TableResult`) — both draw their targets from the same Supabase-backed registry (`app/services/iidx/admin/targets.py`), filtered by `kind`. Adding a difficulty table touches only `app/services/iidx/difficulty_crawl/crawlers/` + a `POST /iidx/crawl/targets` call (`kind="table"`); adding a song source touches only `app/services/iidx/songs_crawl/crawlers/` + a `POST /iidx/crawl/targets` call (`kind="song"`).

### textage song master crawler

`textage` (`app/services/iidx/songs_crawl/crawlers/textage.py`) fetches `titletbl.js` / `actbl.js` (Shift-JIS — decode with `cp932`; titles contain HTML tags) and returns a `SongMasterResult` → `sync_song_master` RPC (upsert `versions` / `songs` / `charts`; rows missing from the crawl get `in_ac=false`, never deleted). `songs.textage_tag` is the stable song identifier.

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

`app/services/iidx/difficulty_crawl/parsers/sheet_parser.py` finds the series-header row by matching `NNN譜面` (a **song count that changes over time**) with `SERIES_HEADER_RE`, never a hardcoded number. If you touch header detection, keep it pattern-based or a table will silently parse to empty.

### Auth

Frontend logs in with Supabase directly (Google OAuth / email) and sends `Authorization: Bearer <access_token>`. The backend only **verifies** tokens (`app/core/security.py` + `app/api/deps.py`): JWKS/RS256 by default, or HS256 when `SUPABASE_JWT_SECRET` is set (legacy). Protect an endpoint by taking a `CurrentUser` param (or `dependencies=[Depends(get_current_user)]` for a whole router); for role-gated endpoints use `require_role(UserRole.ADMIN)`. The role is **not** from the JWT — `get_current_user` looks up the DB profile (`crud_profiles.get_profile`) and computes the effective `UserRole` from `public.profiles.platform_role` + `iidx.profiles.service_role` (see Supabase schema). `security.extract_app_role` (JWT `app_metadata.role`) is legacy/unused.

## Conventions

- Comments and docstrings are in **Korean** — match this.
- The Supabase Python client is **synchronous**; in async paths wrap calls in `asyncio.to_thread` (see `pipeline.py`).
- Reads and writes share one service-role client (`app/db/session.py get_supabase`); `SUPABASE_SERVICE_ROLE_KEY` is required for both.
- Endpoints declare `response_model` so the docs show real schemas; request bodies use typed models (not bare `dict`) for the same reason.
- **Two OpenAPI docs** (`app/core/openapi.py`, mounted by `setup_docs` in `main.py`): `/docs`+`/openapi.json` = public client spec, **opt-in only** — an endpoint appears there iff decorated with `openapi_extra=PUBLIC` (used by `/iidx/crawl/targets`, `/iidx/crawl/preview`). `/internal/docs` = everything (admin + crawl jobs/schedules included). New endpoints are internal-only by default; this controls documentation, not access — `/internal`, `/api/v1/admin`, and `/api/v1/iidx/admin` must be blocked at the reverse proxy on the public domain, and now so must `/api/v1/iidx/crawl/jobs*`/`/api/v1/iidx/crawl/schedules*` (see the crawl API note above — they moved out of `/admin`).
- `OAUTH_PROVIDERS` / `OAUTH_ALLOWED_REDIRECT_URLS` / `ADMIN_ALLOWED_REDIRECT_URLS` / `CORS_ORIGINS` in `.env` are each a **JSON array on one line**.
- Inside docker compose, `REDIS_URL` is overridden to `redis://redis:6379/0`; the `.env` value (`localhost`) is for local venv runs.

## Supabase schema

Single squashed baseline in `supabase/migrations/20260803000000_baseline.sql` (no incremental history — 0 users, full teardown). **Two schemas** (`public` + `iidx`), and the Python layer routes to each via a dedicated cached client in `app/db/session.py` (`get_supabase` = `public`, `get_supabase_svc` = `iidx`; each fixes its schema at construction via `ClientOptions(schema=...)`, so the shared singletons are threadpool-safe):

- **`public`** (account layer, `get_supabase`): `profiles` (id = auth.users.id, `handle`/`social_links`/`is_public` + `platform_role` USER|SUPER_ADMIN), `user_bans` (`service` null=platform / 'iidx'=this service; `banned_by`/`lifted_by` are uuid → `profiles.id`), `user_follows`. `handle_new_user` trigger auto-creates a `public.profiles` row on signup. `admin_list_users` RPC lives here (joins auth.users + both profile tables for the effective role).
- **`iidx`** (IIDX service, `get_supabase_svc`): `profiles` (user_id, `dj_name`/`dj_id` + `service_role` USER|ADMIN — row exists ⇔ onboarded), `versions`/`songs` (`textage_tag` + `series` unique, `bpm`)/`charts` (`notes`, unique `(song_id,play_style,difficulty)`), `difficulty_tables`/`difficulty_entries` (now with nullable `chart_id` FK; entries fully **replaced** per sync). Sync RPCs `sync_song_master` / `sync_table_result` are `security definer` here (upsert + `in_ac=false` for missing rows / table upsert + entry replace + success log to `iidx.crawl_sync_logs`).
- **Crawl ops tables live in `iidx` too** (service-owned, no separate schema): `crawl_targets`/`crawl_schedules`/`crawl_sync_logs` (log column is `target_key`, not the old `table_slug`; no `service` column — schema *is* the service boundary). Unlike the public-read tables above they get **no read policy + `anon`/`authenticated` SELECT revoked** → service-role-only. Same `get_supabase_svc` client.

RLS: public read + service-admin write on account/service data; `crawl_*` tables are service-role-only. The baseline explicitly grants `service_role` usage+table privileges on `iidx` (Supabase only wires those defaults for `public`). **Expose `iidx`** in Settings > API > Exposed schemas (`public` is included by default). There is intentionally no separate `ops` schema: with supabase-py/PostgREST you must expose any schema you read, so a "hidden" schema buys nothing — RLS + grants (not schema-hiding) are the protection. Effective role is computed in `crud_profiles._effective_role` (SUPER_ADMIN > ADMIN > USER); the whole app reads it through `crud_profiles.get_profile`.
