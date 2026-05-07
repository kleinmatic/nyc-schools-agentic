# NYC Schools Agentic

An interactive school-data site/server for NYC public schools. Serves HTML pages to humans and (planned) MCP/A2A/ACP surfaces to agents — agents are intended consumers, not an afterthought. Live runtime, not a static build.

## Architecture

This repo consumes the upstream **`nycschools`** Python package (`~/Code/nycschools/`, Matthew X. Curinga / Adelphi Ed Tech, AGPL-3.0). Upstream is treated as **read-only / upstream-track**: data-layer code goes there as a PR. Everything else — service layer, web routes, agentic surfaces, deploy — lives here.

This repo is also AGPL-3.0 (matches upstream; see `LICENSE`). Every deployed surface is a network service under AGPL §13, so the site footer links to corresponding source.

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

This means a new operation (e.g. `list_by_attendance_zone`) shows up across all surfaces by editing one file. **Never import transport types into `services/`.**

### Module layout

```
app/
├── main.py            FastAPI app, lifespan-loaded data
├── config.py          .env loading, NYC_SCHOOLS_DATA_DIR resolution
├── data.py            in-memory dataframes (loaded once at startup)
├── services/
│   ├── models.py      Pydantic schemas — the contract surfaced everywhere
│   └── schools.py     pure-Python data-access functions
└── web/
    ├── routes.py      thin Jinja-rendering adapters
    └── templates/     base, search, school, partials/
```

Future siblings of `web/` will be `mcp_server/`, `a2a_server/`, `acp_server/`. Each is a thin adapter — same shape as `web/`.

### Data layer for v1

Dataframes are loaded into memory at startup via FastAPI lifespan (~5s, ~150 MB RAM). Pandas serves filter/select fast enough for our scale. We'll migrate to Postgres / PostGIS when we need attendance-zone polygons or scale demands it — not before.

## Repo boundaries

| Goes in `~/Code/nycschools/` | Goes here |
|---|---|
| New data loaders, schema fixes, new dataset modules | Service-layer functions over existing data |
| Bug fixes in existing loaders | HTTP / MCP / A2A / ACP route adapters |
| New tests for the data layer | Tests for service & route layers |
| Documentation for the package | Site templates, frontend, deploy, project notes |

When in doubt: if another data-analysis project could reuse it, it's upstream. Otherwise it's here.

## How to read the upstream package

Key entry points in `~/Code/nycschools/nycschools/`:
- `schools.load_school_demographics()` — demographics by DBN/year (race %, ELL, SWD, poverty, ENI, enrollment)
- `schools.search(df, qry)` — fuzzy school lookup by name / short-name / DBN
- `exams.load_math()` / `load_ela()` — grades 3-8 state tests
- `class_size.load_class_size()` / `load_ptr()` — class size + pupil:teacher ratio
- `snapshot.load_snapshots()` — DOE official snapshot (attendance, chronic absence, principal, admissions method, quality review)
- `geo.load_*` — locations, district/zip/neighborhood boundaries
- `schools.load_hs_directory(ay)` — HS programs and admissions criteria
- Data root: env var `NYC_SCHOOLS_DATA_DIR`. The upstream bulk-archive Drive URL is dead; we lazy-fetch per file from `data.mixi.nyc` instead — see `scripts/fetch_data.py`.

## Conventions

- **DBN is the primary key** everywhere. URLs use it: `/school/15K321`.
- **Don't import transport types into `app/services/`.** Functions take primitives and return Pydantic models. The adapter wraps; the service computes.
- **Don't fork upstream.** Install editable (`uv sync` does this against `../nycschools`) and import.
- **Don't commit secrets.** `SECRETS.md` is gitignored; deploy keys, vendor info, scratch SQL go there or in a sibling private repo (see README "License & private state").
- **Async at the edge, sync inside.** FastAPI routes are `async def`; service functions are sync (pandas isn't async). That's fine — they don't block long enough to matter.
