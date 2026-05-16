# NYC Schools Agentic

Interactive school-data site/server for NYC public schools. Serves HTML pages to humans **and an MCP server to agents** at `/mcp/` (Streamable HTTP) — A2A / ACP surfaces are planned siblings. Agents are intended consumers, not an afterthought. Live runtime, not a static build.

## Architecture

### Two-phase data flow

```
upstream sources   →   school-data/   →   data/   →   running app
              (fetch_data.py)     (build_db.py)
                [build only]      [committed via Git LFS]
```

The running app has **zero runtime dependency** on the upstream `nycschools` package or on `school-data/`. It reads from `data/data.sqlite` (LFS-tracked) plus a few small geo / CSV files in `data/` (school locations, school-zone polygons, NTA boundaries, co-location report). Cold-start data load is ~1s; the FastAPI lifespan additionally pre-warms `aggregate_by_neighborhood` (~8s) so the first user request to `/neighborhood/*` or the homepage doesn't pay the cost — see `analytics.warm_caches()`. On Fly the warm-up is hidden by the rolling-deploy strategy (healthcheck blocks traffic until ready).

The data refresh workflow (`scripts/fetch_data.py` + `scripts/build_db.py`) runs locally, rarely (~once a year when NYSED publishes a new School Report Card), then commits the rebuilt `data/` files. Production never re-pulls; every refresh is a deliberate, reviewable git commit. See README "Refreshing data" for the full sequence.

This repo is **AGPL-3.0** to match upstream. Every deployed surface is a network service under AGPL §13, so the site footer links to corresponding source on GitHub.

### The keystone: unified service layer

Every data-access operation is defined **once** as a transport-agnostic Python function in `app/services/`. The function takes primitives and returns Pydantic models — no FastAPI Request, no MCP Context, no transport leakage. Thin adapters in `app/web/` and `app/mcp_server/` (and, later, A2A / ACP servers) wrap those functions for each protocol.

```
                 search_schools(query) -> list[SchoolSummary]
                              |
       ┌──────────┬───────────┼───────────┬──────────┐
       v          v           v           v          v
     /search   /school/X    /mcp/      /a2a       /acp
     (HTML)    (HTML)       (tool)     (skill)    (handler)
```

A new operation (e.g. `list_by_attendance_zone`) shows up across all surfaces by editing one file. **Never import transport types into `services/`.**

Same pattern powers cross-surface reuse in concrete cases that already shipped: `top_schools` is both an MCP tool *and* drives the homepage school leaderboards; `aggregate_by_neighborhood` is both an MCP tool *and* drives the homepage NTA leaderboards; `school_peers` is both an MCP tool *and* renders the "Schools Nearby" section on the school page; `get_neighborhood` is both an MCP tool *and* powers the `/neighborhood/{nta}` page. Adding a new surface (e.g. an `/api/v1/...` JSON layer or A2A) is a matter of writing a new adapter directory next to `web/` and `mcp_server/`, not new business logic.

### Module layout

```
app/
├── main.py            FastAPI app, lifespan-loaded data, mounts web + MCP
├── config.py          paths to data/ (committed) + school-data/ (build-only)
├── data.py            reads data/data.sqlite + geo files into in-memory dataframes
├── services/
│   ├── models.py      Pydantic schemas — the contract surfaced everywhere
│   ├── schools.py     one-school: search_schools, get_school, peer ranks,
│   │                  school_staffing (GC + SW), co_located_schools
│   ├── zoning.py      address → lat/lon → zoned ES/MS (NYC GeoSearch + point-in-polygon)
│   └── analytics.py   cross-school: top_schools, bulk_metrics, list_high_schools,
│                      aggregate_by_neighborhood, borough_summary, school_peers,
│                      schools_in_neighborhood, get_neighborhood, plus homepage_*
│                      curated sets and warm_caches() for the lifespan hook
├── web/
│   ├── routes.py      thin Jinja-rendering adapters; registers `level` + `pretty`
│   │                  Jinja filters (display-only label and apostrophe polish)
│   ├── charts.py      view-layer data shaping for client-side Observable Plot charts
│   └── templates/
│       ├── base, search, school, find, neighborhood, sources    page templates
│       └── partials/  results, leaderboard, neighborhood_leaderboard,
│                      borough_grid, peer_cohort                — small reusable units
└── mcp_server/
    ├── __init__.py    re-exports the FastMCP server
    └── server.py      thin FastMCP adapter; mounted at /mcp/ over Streamable HTTP
```

Future siblings of `web/` and `mcp_server/` will be `a2a_server/`, `acp_server/`. Each is a thin adapter — same shape.

## Repo boundaries

The upstream **`nycschools`** package (Matthew X. Curinga / Adelphi Ed Tech, AGPL-3.0) is treated as **read-only / upstream-track**. We work against our **fork at `github.com/kleinmatic/nycschools`** for any data-layer additions. Two branches currently have new loaders pending PR back to Adelphi: `nysed-src-loader` (NYSED Report Card Database) and `staffing-loader` (Guidance Counselor + Social Worker FTE counts).

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
- `staffing.load_staffing(ay)` (on the `staffing-loader` branch of `kleinmatic/nycschools`) — DOE annual Guidance Counselor + Social Worker FTE report from InfoHub. Per-school GC / SW FTE counts plus DOE-computed pupils-per-staff ratios.

Two non-upstream geo / CSV data files we fetch ourselves (NYC Open Data Socrata endpoints): the 2024-25 ES + MS attendance-zone polygons (`scripts.fetch_data.fetch_zone_polygons`), the 2020-21 Co-Location Report (`fetch_co_location` → `data/co-locations.csv`), and the 2010 NTA polygon set (`fetch_nta_polygons` — pulled from a GitHub mirror because NYC retired the 2010 NTA dataset from Open Data when 2020 NTAs shipped, but `school-locations.geojson` still uses 2010-era NTA names).

The upstream bulk-archive Drive URL is dead; we lazy-fetch per file from `data.mixi.nyc` instead. NYSED publishes its database as a Microsoft Access `.mdb` inside a ZIP, requiring `mdbtools` to extract — see README "Refreshing data" prerequisites.

## Conventions

- **DBN is the primary key** everywhere. URLs use it: `/school/15K321`.
- **Neighborhood = NTA (Neighborhood Tabulation Area).** NYC's 195 official neighborhood boundaries — the closest formal proxy to colloquial neighborhood names. Per-school NTA name lives in `data/school-locations.geojson` (`nta_name` column); 93% coverage. **District = the natural "zone"** (1-32, geographic). The pair powers `aggregate_by_neighborhood`, `school_peers(scope="district")`, the homepage "By place" section, and the school page "Schools nearby" section. HS is city-wide choice — district-as-zone is meaningful for ES/MS only.
- **Active schools = latest demographics row `ay >= 2022`.** Closed/inactive schools have ancient demographics rows with sentinel zeros (e.g. ENI=0 in 2005 export); excluding them keeps leaderboards from being polluted by zombie schools. Filter applied in `_candidate_schools` in `analytics.py`.
- **`demographics.beds` is `int64`; NYSED `ENTITY_CD` is a 12-char string.** Always convert to zero-padded string before joining. Helper: `_beds_to_str` in `analytics.py`. Direct `==` between the two silently fails to match.
- **ENI is the equity proxy of choice for ranking and peer comparison; `poverty_pct` is for direct interpretability.** Don't rank schools by `poverty_pct` — NYC's 2017 CEP transition broke that signal's continuity. Detail in README "ENI vs poverty_pct". The full 13-metric vocabulary used by `top_schools` / `bulk_metrics` / `top_neighborhoods` lives in `METRIC_DESCRIPTIONS` in `analytics.py` — single source of truth surfaced into MCP tool descriptions and README.
- **Don't import transport types into `app/services/`.** Functions take primitives and return Pydantic models. The adapter wraps; the service computes.
- **Async at the edge, sync inside.** FastAPI routes are `async def`; service functions are sync (pandas isn't async). That's fine — they don't block long enough to matter.
- **Don't commit secrets.** `SECRETS.md` and several other patterns are gitignored; deploy keys, vendor info, scratch SQL go there or in a sibling private repo (see README "License & private state").
- **Don't bypass the SQLite at runtime.** If a new data source is needed, add it to the upstream fork (or to this repo if it's truly app-specific), surface it through `scripts/build_db.py`, and read it via `app/data.py`. The running app should never call upstream loaders or hit the network for static data.
- **`scripts/find_school.py` and `scripts/inspect_school.py`** still use the upstream loaders directly (pre-SQLite design). They're useful for ad-hoc inspection of raw upstream data; require `uv sync --group build` to run.
- **Client-side dataviz uses Observable Plot** (UMD via CDN in `base.html`, with d3 alongside since Plot's UMD doesn't re-export d3 helpers). Server-side shaping lives in `app/web/charts.py` — same boundary rule as templates: takes service-layer Pydantic models, returns chart-ready plain dicts. Pass to Jinja contexts and inline `<script>` with `| tojson`. The school-page grade × year × proficiency-level chart is the canonical example: per-cell stacked bars, a NYC '22 cohort comparator column at reduced opacity, and a gray cell placeholder for COVID-cancelled (AY 2019, 2020) years.
- **Slippy maps use Leaflet + CARTO Positron**, opt-in per template via `base.html`'s `head_extra` block (so only `/neighborhood/{nta}` pays the ~40KB library cost). MapTiler / Stadia / Mapbox would all need API keys for prod; CARTO Positron is key-free and visually right for data journalism. The `/neighborhood/` map proves out the pattern: NTA polygon outline via `L.geoJSON`, school points as `L.divIcon` squares (9px, white halo) so they read like data marks rather than location pins. Compute bounds via `L.geoJSON(boundary).getBounds()` — the manual coord-flatten approach silently breaks on MultiPolygon NTAs.
- **House style for headlines and bylines** follows the ProPublica News Apps style guide at `~/Code/guides/news-apps.md`. Key rules to remember: every headline and subhead in AP title case (capitalize first/last words, capitalize nouns/verbs/adjectives/adverbs/pronouns, lowercase short prepositions and articles); every news app gets a byline under the top headline ("By Scott Klein · Updated <date>"); sources go under every chart, not in a global "credits" page only. The `/sources` page is the readers' catalog of every dataset and its vintage, linked from the footer.
- **Display helpers in `routes.py`**: `LEVEL_LABELS` + the `{{ value|level }}` filter (turns `"high"` into `"High School"` etc. — internal codes stay raw in MCP and SQLite); `{{ value|pretty }}` (straight ASCII apostrophe → U+2019 curly). Both are display-only — never apply to fuzzy-search input or URL slugs.
- **NTA URL slug convention**: spaces become dashes (`/neighborhood/Park-Slope-Gowanus`). `app/services/analytics._fuzzy_match_ntas` matches dash- and space-form transparently. Apply `|replace(' ', '-')|urlencode` in templates that build NTA links.
