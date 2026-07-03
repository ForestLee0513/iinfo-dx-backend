# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Backend API for an IIDX difficulty-table service. It **crawls** difficulty tables (mostly Google Sheets `pubhtml`), stores them in **Supabase**, and serves them to a **user-facing client** and a future **admin** — both of which live in **separate repos**. This repo is the API only; the Next.js client and admin UI are not here.

## Run / develop

Requires **Python 3.12** (code uses 3.10+ `X | None` unions and evaluates them at runtime via pydantic). Note: a bare `python3` on the host may be 3.9 and will fail to import the app — use the venv or Docker.

```bash
# Docker (recommended) — hot-reload via ./app volume mount + --reload
docker compose up --build            # add -d for background
docker compose logs -f api

# Local venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload        # http://localhost:8000/docs
```

`cp .env.example .env` and fill in Supabase values before running. `requirements.txt` change → `docker compose up --build`.

**No test suite and no linter are configured** (no pytest/ruff/pyproject). Don't claim tests pass. For static checking without a full environment, `python3 -m py_compile $(find app -name '*.py')` catches syntax errors (works even on 3.9); real import/runtime checks need the 3.12 venv.

## Architecture — modular monolith

Single FastAPI app; each **module** is a package with its own router, mounted by prefix in `app/main.py` (the composition root):

| Prefix | Module | Role |
|--------|--------|------|
| `/api/v1/web` | `app/web` | User-client API: public table reads (`/tables`, `/tables/{slug}`) + `/me` (auth). |
| `/api/v1/crawl` | `app/crawl` | Crawler service: `/targets`, `/preview` diagnostics (public, read-only) **+ the weekly scheduler**. |
| `/api/v1/admin` | `app/admin` | Placeholder only — namespace reserved, **do not implement here** (separate admin repo). |
| `/api/v1/health` | `app/common/health.py` | Health check. |

`app/common` = shared infra (`auth.py` token verification, `supabase.py` service-role client, `schemas.py` AuthUser). `app/core/config.py` = pydantic-settings loaded from `.env`.

### Data flow (one direction)
Crawlers run **only** on the weekly APScheduler cron (`app/crawl/scheduler.py`, default Mon 05:00 Asia/Seoul) → `run_full_sync` (`pipeline.py`) → `sync_table_result` RPC (`sync.py`, service role) → Supabase. The `web` module reads back from Supabase (public RLS read). **There is intentionally no manual sync endpoint** — ad-hoc/manual table updates are handled in a separate repo. Don't add one.

### Crawler plugin pattern
To add a difficulty table you normally touch **only** `app/crawl/crawlers/` and `CRAWL_TARGETS` — schema and sync code stay untouched:
1. Implement a crawler class in `app/crawl/crawlers/` decorated with `@register("name")` (`base.py` defines the `Crawler` protocol, `TableDef`/`TableResult`, and the registry). NUMERIC tables usually only need to fill in `numeric_example.py`'s `_parse`.
2. Add `{"crawler": "name", ...}` to `CRAWL_TARGETS`. The `crawler` key selects the registered crawler; other keys are that crawler's `target` config.

Two rating systems, carried through everywhere: **GRADE** (ordered `grades[]`, e.g. F..S+) vs **NUMERIC** (float `rating`). A `TableResult` is `TableDef` + a list of entry dicts; `/crawl/preview` returns exactly what would be synced.

### Google Sheets parser gotcha
`app/crawl/parsers/sheet_parser.py` finds the series-header row by matching `NNN譜面` (a **song count that changes over time**) with `SERIES_HEADER_RE`, never a hardcoded number. If you touch header detection, keep it pattern-based or a table will silently parse to empty.

### Auth
Frontend logs in with Supabase directly (Google OAuth / email) and sends `Authorization: Bearer <access_token>`. The backend only **verifies** tokens (`app/common/auth.py`): JWKS/RS256 by default, or HS256 when `SUPABASE_JWT_SECRET` is set (legacy). Protect an endpoint by taking a `CurrentUser` param (or `dependencies=[Depends(get_current_user)]` for a whole router).

## Conventions
- Comments and docstrings are in **Korean** — match this.
- The Supabase Python client is **synchronous**; in async paths wrap calls in `asyncio.to_thread` (see `pipeline.py`).
- Reads and writes share one service-role client (`app/common/supabase.py get_supabase`); `SUPABASE_SERVICE_ROLE_KEY` is required for both.
- Endpoints declare `response_model` so `/docs` shows real schemas; request bodies use typed models (not bare `dict`) for the same reason.
- `CRAWL_TARGETS` in `.env` is a **JSON array on one line**.

## Supabase schema
Migrations in `supabase/migrations/`. `difficulty_tables`, `difficulty_entries` (fully **replaced** per sync), `crawl_sync_logs`. `sync_table_result` is a `security definer` RPC doing table upsert + entry replace + log in one transaction. RLS: public read on tables/entries; all writes are service-role only.
