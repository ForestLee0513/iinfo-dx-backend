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

`cp .env.example .env` and fill in Supabase values before running. `requirements.txt` change → `docker compose up --build`. The app boots and crawls fine without Redis (warnings only) — but manual jobs / schedule overrides / resume need it.

**No test suite and no linter are configured** (no pytest/ruff/pyproject). Don't claim tests pass. For static checking without a full environment, `python3 -m py_compile $(find app -name '*.py')` catches syntax errors (works even on 3.9); real import/runtime checks need the 3.12 venv.

## Architecture — modular monolith

Single FastAPI app; each **module** is a package with its own router, mounted by prefix in `app/main.py` (the composition root):

| Prefix | Module | Role |
|--------|--------|------|
| `/api/v1/web` | `app/web` | User-client API: public table reads (`/tables`, `/tables/{slug}`) + `/me` (auth). |
| `/api/v1/songs-crawl` | `app/songs_crawl` | Song-master crawler (textage): `/targets`, `/preview` diagnostics (public, read-only). |
| `/api/v1/difficulty-crawl` | `app/difficulty_crawl` | Difficulty-table crawler: `/targets`, `/preview` **+ the weekly scheduler**. |
| `/api/v1/admin` | `app/admin` | Admin API for the separate admin FE: manual crawl jobs (`/crawl/jobs`) + schedule get/put (`/crawl/schedule`). Auth + `ADMIN_EMAILS` whitelist. |
| `/api/v1/health` | `app/common/health.py` | Health check. |

`app/common` = shared infra (`auth.py` token verification, `supabase.py` service-role client, `redis.py` async Redis client, `schemas.py` AuthUser). `app/core/config.py` = pydantic-settings loaded from `.env`.

### Data flow (one direction)
One weekly APScheduler job (`app/difficulty_crawl/scheduler.py`, default Mon 05:00 Asia/Seoul) runs `run_song_sync` (songs_crawl → `sync_song_master` RPC) **then** `run_table_sync` (difficulty_crawl → `sync_table_result` RPC), in that order — the song master must land before table mapping. Ordering is guaranteed in code, never by staggered cron times. A failed song-master sync does not block the table sync (it proceeds on last week's master). The `web` module reads back from Supabase (public RLS read). **There is intentionally no manual sync endpoint in the crawl modules** — manual runs go through `POST /api/v1/admin/crawl/jobs` only (RPCs are service-role only: `sync_song_master`, `sync_table_result`).

### Admin module — crawl jobs & schedule (Redis-backed)
`app/admin` (`jobs.py` runner, `store.py` Redis persistence, `deps.py` AdminUser guard). Both manual and scheduled runs go through `jobs.create_job`/`execute_job`, which checkpoints per step (`song_sync` → `table_sync`) in Redis: on startup `resume_interrupted_job` re-runs unfinished steps of a job that was cut off mid-run (safe because crawl→RPC is upsert/full-replace, i.e. idempotent). One job at a time — the "current job" key in Redis is the lock; stale RUNNING leftovers are auto-failed by the next `create_job`. Schedule changes via `PUT /admin/crawl/schedule` are stored in Redis and **override** the `CRAWL_SCHEDULE_*` env defaults across restarts (`scheduler.get_effective_schedule`). If Redis is down, the weekly cron still crawls (untracked fallback in `run_weekly_sync`); admin endpoints return 503. Admin access = valid Supabase token **and** `app_metadata.role == "ADMIN"` on the Supabase account (granted via SQL on `auth.users.raw_app_meta_data` — see README; takes effect on next token refresh). Roles live in `app/common/schemas.py` (`UserRole` + `ROLE_LEVELS`, hierarchy-based so higher roles include lower ones); protect endpoints with `require_role(UserRole.X)` from `app/common/auth.py`. Unknown/missing role ⇒ `USER`.

### Crawler plugin pattern
To add a difficulty table you normally touch **only** `app/difficulty_crawl/crawlers/` and `TABLE_CRAWL_TARGETS` — schema and sync code stay untouched:
1. Implement a crawler class in `app/difficulty_crawl/crawlers/` decorated with `@register("name")` (`base.py` defines the `Crawler` protocol, `TableDef`/`TableResult`, and the registry). NUMERIC tables usually only need to fill in `numeric_example.py`'s `_parse`.
2. Add `{"crawler": "name", ...}` to `TABLE_CRAWL_TARGETS`. The `crawler` key selects the registered crawler; other keys are that crawler's `target` config.

Two rating systems, carried through everywhere: **GRADE** (ordered `grades[]`, e.g. F..S+) vs **NUMERIC** (float `rating`). A `TableResult` is `TableDef` + a list of entry dicts; `/difficulty-crawl/preview` returns exactly what would be synced.

The two crawl modules have **separate registries and target lists**: `SONG_CRAWL_TARGETS` (songs_crawl, `SongMasterResult`) vs `TABLE_CRAWL_TARGETS` (difficulty_crawl, `TableDef`/`TableResult`). Adding a difficulty table touches only `app/difficulty_crawl/crawlers/` + `TABLE_CRAWL_TARGETS`; adding a song source touches only `app/songs_crawl/crawlers/` + `SONG_CRAWL_TARGETS`.

### textage song master crawler
`textage` (`app/songs_crawl/crawlers/textage.py`) fetches `titletbl.js` / `actbl.js` (Shift-JIS — decode with `cp932`; titles contain HTML tags) and returns a `SongMasterResult` → `sync_song_master` RPC (upsert `versions` / `songs` / `charts`; rows missing from the crawl get `in_ac=false`, never deleted). `songs.textage_tag` is the stable song identifier.

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
`app/difficulty_crawl/parsers/sheet_parser.py` finds the series-header row by matching `NNN譜面` (a **song count that changes over time**) with `SERIES_HEADER_RE`, never a hardcoded number. If you touch header detection, keep it pattern-based or a table will silently parse to empty.

### Auth
Frontend logs in with Supabase directly (Google OAuth / email) and sends `Authorization: Bearer <access_token>`. The backend only **verifies** tokens (`app/common/auth.py`): JWKS/RS256 by default, or HS256 when `SUPABASE_JWT_SECRET` is set (legacy). Protect an endpoint by taking a `CurrentUser` param (or `dependencies=[Depends(get_current_user)]` for a whole router); for role-gated endpoints use `require_role(UserRole.ADMIN)` (reads `app_metadata.role` from the JWT — no DB lookup).

## Conventions
- Comments and docstrings are in **Korean** — match this.
- The Supabase Python client is **synchronous**; in async paths wrap calls in `asyncio.to_thread` (see `pipeline.py`).
- Reads and writes share one service-role client (`app/common/supabase.py get_supabase`); `SUPABASE_SERVICE_ROLE_KEY` is required for both.
- Endpoints declare `response_model` so the docs show real schemas; request bodies use typed models (not bare `dict`) for the same reason.
- **Two OpenAPI docs** (`app/common/openapi.py`, mounted by `setup_docs` in `main.py`): `/docs`+`/openapi.json` = public client spec, **opt-in only** — an endpoint appears there iff decorated with `openapi_extra=PUBLIC`. `/internal/docs` = everything (admin/crawl included). New endpoints are internal-only by default; this controls documentation, not access — `/internal` (like `/api/v1/admin`) must be blocked at the reverse proxy on the public domain.
- `SONG_CRAWL_TARGETS` / `TABLE_CRAWL_TARGETS` / `CORS_ORIGINS` in `.env` are each a **JSON array on one line**.
- Inside docker compose, `REDIS_URL` is overridden to `redis://redis:6379/0`; the `.env` value (`localhost`) is for local venv runs.

## Supabase schema
Migrations in `supabase/migrations/`. Difficulty tables: `difficulty_tables`, `difficulty_entries` (fully **replaced** per sync), `crawl_sync_logs`. `sync_table_result` is a `security definer` RPC doing table upsert + entry replace + log in one transaction. Song master: `versions`, `songs` (`textage_tag` unique), `charts`; `sync_song_master` is a `security definer` RPC doing versions/songs/charts upsert + `in_ac=false` for rows missing from the crawl (never deletes). RLS: public read on all of the above; all writes are service-role only.
