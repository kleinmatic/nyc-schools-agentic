# NYC Schools Agentic

Interactive school-data site/server for NYC public schools. Serves HTML pages to humans and (planned) MCP / A2A / ACP surfaces to agents — agents are intended consumers, not an afterthought. Live runtime, not a static build.

## Architecture

### Two-phase data flow

```
upstream sources   →   school-data/   →   data/   →   running app
              (fetch_data.py)     (build_db.py)
                [build only]      [committed via Git LFS]
```

The running app has **zero runtime dependency** on the upstream `nycschools` package or on `school-data/`. It reads from `data/data.sqlite` (LFS-tracked) plus a few small geo/feather files in `data/`. Cold start is ~1s.

The data refresh workflow (`scripts/fetch_data.py` + `scripts/build_db.py`) runs locally, rarely (~once a year when NYSED publishes a new School Report Card), then commits the rebuilt `data/` files. Production never re-pulls; every refresh is a deliberate, reviewable git commit. See README "Refreshing data" for the full sequence.

This repo is **AGPL-3.0** to match upstream. Every deployed surface is a network service under AGPL §13, so the site footer links to corresponding source on GitHub.

### The keystone: unified service layer

Every data-access operation is defined **once** as a transport-agnostic Python function in `app/services/`. The function takes primitives and returns Pydantic models — no FastAPI Request, no MCP Context, no transport leakage. Thin adapters in `app/web/` (and, later, MCP/A2A/ACP servers) wrap those functions for each protocol.

```
                 search_schools(query) -> list[SchoolSummary]
                              |
       ┌──────────┬───────────┼───────────┬──────────┐
       v          v           v           v          v
     /search   /api/...    /mcp        /a2a       /acp
     (HTML)    (JSON)      (tool)      (skill)   (handler)
```

A new operation (e.g. `list_by_attendance_zone`) shows up across all surfaces by editing one file. **Never import transport types into `services/`.**

### Module layout

```
app/
├── main.py            FastAPI app, lifespan-loaded data, mounts web + MCP
├── config.py          paths to data/ (committed) + school-data/ (build-only)
├── data.py            reads data/data.sqlite + geo files into in-memory dataframes
├── services/
│   ├── models.py      Pydantic schemas — the contract surfaced everywhere
│   ├── schools.py     search_schools, get_school, peer ranks, etc. (one-school)
│   ├── zoning.py      geocode + find_zoned_schools (address search)
│   └── analytics.py   top_schools, bulk_metrics, list_high_schools (cross-school)
├── web/
│   ├── routes.py      thin Jinja-rendering adapters
│   └── templates/     base, search, school, find, partials/
└── mcp_server/
    └── server.py      thin FastMCP adapter; mounted at /mcp/ over Streamable HTTP
```

Future siblings of `web/` and `mcp_server/` will be `a2a_server/`, `acp_server/`. Each is a thin adapter — same shape.

## Repo boundaries

The upstream **`nycschools`** package (Matthew X. Curinga / Adelphi Ed Tech, AGPL-3.0) is treated as **read-only / upstream-track**. We work against our **fork at `github.com/kleinmatic/nycschools`** for any data-layer additions. Currently the `nysed-src-loader` branch on that fork has the `nysed_src` module (NYSED School Report Card loader) pending PR back to Adelphi.

Upstream nycschools is a **build-time-only** dependency — used by `scripts/fetch_data.py` and `scripts/build_db.py` to assemble `data/`, never imported by the running app.

| Goes upstream (kleinmatic/nycschools fork → PR to Adelphi) | Goes here |
|---|---|
| New data loaders, schema fixes, dataset modules | Service-layer functions over the SQLite |
| Bug fixes in existing loaders | HTTP / MCP / A2A / ACP route adapters |
| New tests for the data layer | Tests for service & route layers |
| Documentation for the package itself | Site templates, frontend, deploy, project notes |

When in doubt: if another NYC-schools-data project could reuse it, it's upstream. Otherwise it's here.

## Build-time data source reference

Used only by `scripts/fetch_data.py` and `scripts/build_db.py`, never at runtime:

- `schools.load_school_demographics()` — demographics by DBN/year (race %, ELL, SWD, poverty, ENI, enrollment)
- `schools.search(df, qry)` — fuzzy school lookup; we inline an equivalent in `app/services/schools.py` so runtime doesn't need this import
- `exams.load_math()` / `load_ela()` — NYS grades 3-8 state tests
- `exams.load_regents()` — NYS Regents exams
- `class_size.load_class_size()` / `load_ptr()` — class size + pupil:teacher ratio
- `snapshot.load_snapshots()` — DOE official snapshot (attendance, chronic absence, principal, admissions method, quality review)
- `geo.load_school_locations()` / `load_zipcodes()` / `load_neighborhoods()` — point/polygon geodata
- `schools.load_hs_directory(ay)` — HS programs and admissions criteria
- `shsat.load_admission_offers()` — SHSAT outcomes by sending school
- `budgets.load_galaxy_budgets()` — Galaxy budget portal scrape
- `nysed_src.load_*()` (on the `nysed-src-loader` branch of `kleinmatic/nycschools`) — NYSED School Report Card Database: ESSA accountability, chronic absenteeism, per-pupil expenditures, teacher quality, HS graduation rate, CCCR

The upstream bulk-archive Drive URL is dead; we lazy-fetch per file from `data.mixi.nyc` instead. NYSED publishes its database as a Microsoft Access `.mdb` inside a ZIP, requiring `mdbtools` to extract — see README "Refreshing data" prerequisites.

## Conventions

- **DBN is the primary key** everywhere. URLs use it: `/school/15K321`.
- **ENI is the equity proxy of choice for ranking and peer comparison; `poverty_pct` is for direct interpretability.** Don't rank schools by `poverty_pct` — NYC's 2017 CEP transition broke that signal's continuity. Detail in README "ENI vs poverty_pct".
- **Don't import transport types into `app/services/`.** Functions take primitives and return Pydantic models. The adapter wraps; the service computes.
- **Async at the edge, sync inside.** FastAPI routes are `async def`; service functions are sync (pandas isn't async). That's fine — they don't block long enough to matter.
- **Don't commit secrets.** `SECRETS.md` and several other patterns are gitignored; deploy keys, vendor info, scratch SQL go there or in a sibling private repo (see README "License & private state").
- **Don't bypass the SQLite at runtime.** If a new data source is needed, add it to the upstream fork (or to this repo if it's truly app-specific), surface it through `scripts/build_db.py`, and read it via `app/data.py`. The running app should never call upstream loaders or hit the network for static data.
- **`scripts/find_school.py` and `scripts/inspect_school.py`** still use the upstream loaders directly (pre-SQLite design). They're useful for ad-hoc inspection of raw upstream data; require `uv sync --group build` to run.
