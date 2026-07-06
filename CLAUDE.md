# NYC Schools Agentic

Interactive school-data site/server for NYC public schools. Serves HTML pages to humans **and an MCP server to agents** at `/mcp/` (Streamable HTTP) — A2A / ACP surfaces are planned siblings. Agents are intended consumers, not an afterthought. Live runtime, not a static build.

## Architecture

### Two-phase data flow

```
upstream sources   →   school-data/   →   data/   →   running app
              (fetch_data.py)     (build_db.py)
                [build only]      [committed via Git LFS]
```

The running app has **zero runtime dependency** on the upstream `nycschools` package or on `school-data/`. It reads from `data/data.sqlite` (LFS-tracked) plus a few small geo / CSV files in `data/` (school locations, school-zone polygons, NTA boundaries, co-location report). Cold-start data load is ~1.5s, on the lifespan critical path. `analytics.warm_caches()` runs in a background thread (`asyncio.to_thread`) — `/healthz` returns the moment uvicorn binds and reports the warm state (`caches_warming`, `caches_warm_s`). Warm-up takes ~10s locally and ~40s on Fly's `performance-1x`; the first user hit to `/`, `/neighborhood/*`, or any other heavy aggregation may arrive mid-warm and pay an un-amortized cost, but every request after is cached. Re-blocking warm on the lifespan regressed prod to 230s with healthcheck flap on `shared-cpu-1x` — don't.

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
├── main.py            FastAPI app, lifespan-loaded data, background-thread
│                      warm_caches(), mounts web + MCP
├── config.py          paths to data/ (committed) + school-data/ (build-only)
├── data.py            reads data/data.sqlite + geo files into in-memory dataframes
├── services/
│   ├── models.py      Pydantic schemas — the contract surfaced everywhere
│   ├── schools.py     one-school: search_schools, get_school, peer ranks,
│   │                  school_staffing (GC + SW), co_located_schools,
│   │                  school_swd_outcomes (SWD-subgroup metrics + cohort context)
│   ├── zoning.py      address → lat/lon → zoned ES/MS (NYC GeoSearch + point-in-polygon)
│   ├── analytics.py   cross-school: top_schools, bulk_metrics, list_high_schools,
│   │                  aggregate_by_neighborhood, borough_summary, school_peers,
│   │                  schools_in_neighborhood, get_neighborhood,
│   │                  schools_in_district (district-rolled school list with
│   │                  per-school MS admission methods), plus homepage_* curated
│   │                  sets and warm_caches() (called from lifespan in a
│   │                  background thread; do not re-block on it)
│   └── metrics.py     dynamic capability discovery — SCHOOL_METRICS +
│                      NEIGHBORHOOD_METRICS registries, list_*_metrics +
│                      get_*_metric. Additive over METRIC_DESCRIPTIONS today.
├── web/
│   ├── routes.py      thin Jinja-rendering adapters; registers `level` + `pretty`
│   │                  Jinja filters and `commit_sha_short`/`commit_sha_full`
│   │                  globals (deploy stamp in footer, from GIT_COMMIT_SHA env)
│   ├── charts.py      view-layer data shaping for client-side Observable Plot charts
│   └── templates/
│       ├── base, search, school, zoned, neighborhood, sources    page templates
│       └── partials/  results, leaderboard, neighborhood_leaderboard,
│                      borough_grid, peer_cohort, _webmcp_global_forms (site-wide
│                      WebMCP-annotated forms included via base.html block)
└── mcp_server/
    ├── __init__.py    re-exports the FastMCP server
    └── server.py      thin FastMCP adapter; mounted at /mcp/ over Streamable HTTP.
                       20 tools + 1 prompt (iep_or_special_needs) — 16 curated
                       plus 4 dynamic-discovery (list_*_metrics, get_*_metric)
```

Future siblings of `web/` and `mcp_server/` will be `a2a_server/`, `acp_server/`. Each is a thin adapter — same shape.

## Repo boundaries

The upstream **`nycschools`** package (Matthew X. Curinga / Adelphi Ed Tech, AGPL-3.0) is treated as **read-only / upstream-track**. We work against our **fork at `github.com/kleinmatic/nycschools`** for any data-layer additions. Three branches currently have new loaders pending PR back to Adelphi: `nysed-src-loader` (NYSED Report Card Database), `staffing-loader` (Guidance Counselor + Social Worker FTE counts), and `ms-directory-loader` (Middle School Directory with per-program admission methods).

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
- `ms_directory.load_ms_directory(ay)` (on the `ms-directory-loader` branch of `kleinmatic/nycschools`) — NYC DOE annual Middle School Directory from InfoHub. **Long format**: one row per (DBN, program) — schools commonly carry 1-4 programs each with its own `admission_method` (one of: Open, Zone Priority, Zoned Only, Screened, Screened With Assessment, Language Criteria, Audition, Talent Test, ASD/ACES Program, D75 Special Education Inclusive Services) and a priority cascade. Source xlsx hits an SSL chain locally for some Python builds; `scripts/fetch_data.py` downloads via requests then passes the local path to `load_ms_directory(path=...)` so the build survives those envs.

Two non-upstream geo / CSV data files we fetch ourselves (NYC Open Data Socrata endpoints): the 2024-25 ES + MS attendance-zone polygons (`scripts.fetch_data.fetch_zone_polygons`), the 2020-21 Co-Location Report (`fetch_co_location` → `data/co-locations.csv`), and the 2010 NTA polygon set (`fetch_nta_polygons` — pulled from a GitHub mirror because NYC retired the 2010 NTA dataset from Open Data when 2020 NTAs shipped, but `school-locations.geojson` still uses 2010-era NTA names).

The upstream bulk-archive Drive URL is dead; we lazy-fetch per file from `data.mixi.nyc` instead. NYSED publishes its database as a Microsoft Access `.mdb` inside a ZIP, requiring `mdbtools` to extract — see README "Refreshing data" prerequisites.

## Conventions

- **DBN is the primary key** everywhere. URLs use it: `/school/15K321`.
- **Neighborhood = NTA (Neighborhood Tabulation Area).** NYC's 195 official neighborhood boundaries — the closest formal proxy to colloquial neighborhood names. Per-school NTA name lives in `data/school-locations.geojson` (`nta_name` column); 93% coverage. **District = the natural "zone"** (1-32, geographic). The pair powers `aggregate_by_neighborhood`, `school_peers(scope="district")`, the homepage "By place" section, and the school page "Schools nearby" section. HS is city-wide choice — district-as-zone is meaningful for ES/MS only.
- **Active schools = latest demographics row `ay >= 2022`.** Closed/inactive schools have ancient demographics rows with sentinel zeros (e.g. ENI=0 in 2005 export); excluding them keeps leaderboards from being polluted by zombie schools. Filter applied in `_candidate_schools` in `analytics.py`.
- **`demographics.beds` is `int64`; NYSED `ENTITY_CD` is a 12-char string.** Always convert to zero-padded string before joining. Helper: `_beds_to_str` in `analytics.py`. Direct `==` between the two silently fails to match.
- **ENI is the equity proxy of choice for ranking and peer comparison; `poverty_pct` is for direct interpretability.** Don't rank schools by `poverty_pct` — NYC's 2017 CEP transition broke that signal's continuity. Detail in README "ENI vs poverty_pct". The full 13-metric vocabulary used by `top_schools` / `bulk_metrics` / `top_neighborhoods` lives in `METRIC_DESCRIPTIONS` in `analytics.py` — single source of truth surfaced into MCP tool descriptions and README.
- **D75 is derived from `district == 75`**, exposed as `SchoolSummary.is_d75`. District 75 is NYC's citywide specialized special-ed district; placement is by the Committee on Special Education (not by zone or choice) and accountability is reported under different rules. The flag drives the amber pill badge on the school page and the placement caveat in `school_swd_outcomes(...).notes`. Don't treat D75 schools as peers of comprehensive schools in leaderboards or peer-rank cohorts. The DBN's "75" prefix is perfectly correlated with `district == 75`, but the column is the source of truth.
- **MS admission is district-based choice, NOT strict zoning** — the most common modeling mistake in this data. `find_schools_for_address` returns school-specific zone-priority polygon matches (numeric `label` like "297") in `middle`, but that's a priority *tier* within a district-choice process, not a placement. Read `ms_admission_type` to interpret: `zone_priority_choice` = address falls in a school-priority polygon; `district_choice` = address only hits a whole-district fallback polygon (label like `D15`). The `ms_admission_note` is a pre-composed string telling the consumer to follow up with `schools_in_district(ms_district, level="middle")`. **Important about priority cascades**: across multiple D2 schools, "residents of the middle school zone" is priority **4** (below siblings, district residents, NYC residents) — NOT priority 1. Never assert "you're in the top priority tier" — surface the published priority strings verbatim and let the consumer read them. Source of truth for per-school MS admission methods is `store.ms_directory` (the Fall-2025 InfoHub xlsx, long-format: one row per (DBN, program)). The 10 method values: Open, Zone Priority, Zoned Only, Screened, Screened With Assessment, Language Criteria, Audition, Talent Test, ASD/ACES Program, D75 Special Education Inclusive Services. A single school commonly carries multiple programs with different methods (M.S. 131 has Zone Priority + Language Criteria + Screened + ASD/ACES under one DBN). The MS Directory is the **authoritative cohort** for "what MS schools exist in District X" — broader than `demographics.school_level == "middle"`, which excludes K-8 and 6-12 schools that enroll middle-school students. Don't use the demographics filter for MS rollups.
- **`schools_in_district(district, level)` is the answer to "tell me about district N {middle | elementary | high} schools."** For `level="middle"` it joins the MS Directory with demographics — each school's `admission_methods` lists the methods it admits by, and `ms_programs[*].priorities` carries the per-program cascade strings as published. Pairs with `find_schools_for_address`: when that returns `ms_admission_type in {"zone_priority_choice", "district_choice"}`, `schools_in_district(ms_district, "middle")` is the follow-up. For `level="elementary"`/`"high"` the result is a basic listing with an `admission_overview` note — ES has no MS-shaped directory, HS is city-wide choice (so district grouping is geographic-only). D75 + middle falls back to the basic listing — D75 schools aren't in the MS Directory because their placement is by the Committee on Special Education.
- **NYSED subgroup tables (`nysed_chronic`, `nysed_hs_grad`, `nysed_hs_cccr`, `nysed_essa_subgroup`) carry every subgroup; consumers must filter.** Existing helpers in `schools.py` (`_chronic_for`, etc.) return all subgroups mixed — the All-Students-only views downstream (analytics rank metrics, exam tables) filter to `SUBGROUP_NAME == "All Students"`. The SWD-outcomes path filters to `"Students with Disabilities"`. The literal `"s"` suppression sentinel becomes NaN at load time; combined with cohort_count, that's how `school_swd_outcomes` distinguishes "suppressed" from "missing".
- **Don't import transport types into `app/services/`.** Functions take primitives and return Pydantic models. The adapter wraps; the service computes.
- **Async at the edge, sync inside.** FastAPI routes are `async def`; service functions are sync (pandas isn't async). That's fine — they don't block long enough to matter.
- **Don't commit secrets.** `SECRETS.md` and several other patterns are gitignored; deploy keys, vendor info, scratch SQL go there or in a sibling private repo (see README "License & private state").
- **Don't bypass the SQLite at runtime.** If a new data source is needed, add it to the upstream fork (or to this repo if it's truly app-specific), surface it through `scripts/build_db.py`, and read it via `app/data.py`. The running app should never call upstream loaders or hit the network for static data.
- **`scripts/find_school.py` and `scripts/inspect_school.py`** still use the upstream loaders directly (pre-SQLite design). They're useful for ad-hoc inspection of raw upstream data; require `uv sync --group build` to run.
- **Client-side dataviz uses Observable Plot** (UMD via CDN in `base.html`, with d3 alongside since Plot's UMD doesn't re-export d3 helpers). Server-side shaping lives in `app/web/charts.py` — same boundary rule as templates: takes service-layer Pydantic models, returns chart-ready plain dicts. Pass to Jinja contexts and inline `<script>` with `| tojson`. The school-page grade × year × proficiency-level chart is the canonical example: per-cell stacked bars, a NYC '22 cohort comparator column at reduced opacity, and a gray cell placeholder for COVID-cancelled (AY 2019, 2020) years.
- **GeoJSON fed to Plot.geo / d3 must be rewound clockwise.** d3-geo interprets polygons spherically: an RFC 7946 counterclockwise exterior ring reads as enclosing the whole globe and renders as a solid full-frame fill (the homepage NTA choropleths shipped blank until this was caught). `_orient_for_d3` in `app/web/charts.py` rewinds exteriors CW; shoelace-pinned by `test_homepage_nta_map_exterior_rings_wind_clockwise`. Related: homepage chart payloads (`homepage_citywide`, `homepage_nta_map`) are lru_cached and warmed from `main.py`'s lifespan thread — they can't be warmed from `analytics.warm_caches()` because services must never import the web layer. NTA geometry is simplified (~50m) + coordinate-rounded to keep the inline FeatureCollection near 100KB (payload-size test caps it at 250KB).
- **Slippy maps use Leaflet + CARTO Positron**, opt-in per template via `base.html`'s `head_extra` block (so only `/neighborhood/{nta}` pays the ~40KB library cost). MapTiler / Stadia / Mapbox would all need API keys for prod; CARTO Positron is key-free and visually right for data journalism. The `/neighborhood/` map proves out the pattern: NTA polygon outline via `L.geoJSON`, school points as `L.divIcon` squares (9px, white halo) so they read like data marks rather than location pins. Compute bounds via `L.geoJSON(boundary).getBounds()` — the manual coord-flatten approach silently breaks on MultiPolygon NTAs.
- **House style for headlines and bylines** follows the ProPublica News Apps style guide at `~/Code/guides/news-apps.md`. Key rules to remember: every headline and subhead in AP title case (capitalize first/last words, capitalize nouns/verbs/adjectives/adverbs/pronouns, lowercase short prepositions and articles); leaderboard / chart / section titles too — not just `<h1>` / `<h2>` (so `_HOMEPAGE_LEADERBOARDS["title"]` in analytics.py also follows AP title case); every news app gets a byline under the top headline ("By Scott Klein · Updated <date>"); sources go under every chart, not in a global "credits" page only. The `/sources` page is the readers' catalog of every dataset and its vintage, linked from the footer.
- **Display helpers in `routes.py`**: `LEVEL_LABELS` + the `{{ value|level }}` filter (turns `"high"` into `"High School"` etc. — internal codes stay raw in MCP and SQLite); `{{ value|pretty }}` (straight ASCII apostrophe → U+2019 curly). Both are display-only — never apply to fuzzy-search input or URL slugs.
- **NTA URL slug convention**: spaces become dashes (`/neighborhood/Park-Slope-Gowanus`). `app/services/analytics._fuzzy_match_ntas` matches dash- and space-form transparently. Apply `|replace(' ', '-')|urlencode` in templates that build NTA links.
- **Typography uses Unicode, not HTML entities.** `—` `←` `→` `’` `©` `↗` go in as literal characters in templates. The exceptions: `&amp;` inside URL query strings in `href` attributes (HTML attribute syntax requirement), `&lt;` for a literal `<` in copy that would otherwise parse as a tag start, and the JS `escapeHtml` map's own output values. Per the style guide.
- **Missing-value cells render `<span class="text-slate-300">N/A</span>`**, not em-dash. Macros (`pct()`, `num()`, `money()`, `fmt()`) return that Markup; inline `or "N/A"` patterns must be rewritten as `{% if %} … {% else %}<span class="text-slate-300">N/A</span>{% endif %}` so the span isn't escaped. Style-guide rule plus visual: light-gray N/A recedes when scanning a column of real numbers.
- **Mixed-year disclosure rule:** if a single page combines metrics from multiple vintages (e.g. neighborhood school-by-school table uses ENI from 2024-25, ELA from 2021-22, attendance from ~2016), the section caption must say so. If everything on the page is the same year, no note needed. The 2025-26 staffing data is **mid-year** reporting (DOE publishes in February of the AY) — section heading + provenance call this out explicitly.
- **Per-page meta + OG/Twitter tags.** `base.html` defines `{% block meta_description %}`, `{% block og_title %}`, `{% block og_description %}` with defaults; every concrete page overrides `meta_description` with content-specific copy. Dynamic pages (school, neighborhood) interpolate context into the description so a share preview surfaces the school name or NTA. Required by the style guide ("Every page in an app should have a unique title tag, a unique meta-description tag and the correct social media tags").
- **Neighborhood-page metric set:** the per-school **table** uses the **UNION** of `_PEER_METRICS_BY_LEVEL` entries for every school level represented in the NTA, so a HS in a mostly-ES neighborhood (Forest Hills HS in Forest Hills) doesn't appear empty. The **peer-rank cards** use only the **dominant level's** set, since each card ranks the NTA on one coherent metric. Two separate helpers in `analytics.py`: `_table_metrics_for_neighborhood` vs. `_peer_rank_metrics_for_neighborhood`.
- **`_candidate_schools` keep-cols gotcha:** it slices `demographics` down to a small column set; if a downstream consumer (a `SchoolSummary` field, a service helper, a template) needs another column, add it to the `cols` list or the downstream value falls through silently as None. Cost us an "Enroll: N/A" bug on every neighborhood-page row when `total_enrollment` wasn't in the keep-list.
- **`tests/test_routes.py` substring assertions must track AP-title-case headings.** When you rename a section header (e.g. `"Quick stats"` → `"Quick Stats"`), grep `test_routes.py` for the old form — the case-sensitive `assert fragment in r.text` lines fail silently if they were missed.
- **Lifespan warm-up is non-blocking.** `data.load()` is synchronous on the critical path (~1.5s); `warm_caches()` runs in `asyncio.to_thread(...)` so `/healthz` answers the moment uvicorn binds, reporting `caches_warming` / `caches_warm_s` for deploy monitoring. Trade-off: a first-mid-warm hit pays an un-amortized cost; every hit after is cached. Re-blocking on warm flapped Fly healthchecks on `shared-cpu-1x` (230s startup) — don't.
- **Every `homepage_*` helper has `@lru_cache`.** They each call `aggregate_by_neighborhood` (also cached) but the leaderboard/grid construction layer wasn't — local homepage was 2.3s/request without function-level caching, ~1ms with. Caches are per-process and invalidated by deploy — fine, the data is immutable per-deploy. If you add a new homepage helper, cache it.
- **Fly VM = `performance-1x` + 2GB, `min_machines_running = 1`.** Dedicated vCPU avoids `shared-cpu-1x`'s ~1/16-vCPU burst baseline (which throttled startup to 230s and flapped healthchecks). One machine is enough for pre-launch traffic. Deploy commit SHA flows via `--build-arg COMMIT_SHA=${{ github.sha }}` in `.github/workflows/main.yml` → `GIT_COMMIT_SHA` env in the image → `commit_sha_short` / `commit_sha_full` Jinja globals → footer link to the GitHub commit page. Local runs show `dev`.
- **Neutral journalistic language in user-facing copy and generated narratives.** No best / worst / better / worse anywhere — copy, agent payloads, MCP tool descriptions, leaderboard subtitles, generated `narrative` strings. Use positional: highest / lowest, above / below the median, higher than / lower than. Pinning test `test_swd_cohort_context_narrative_uses_neutral_journalistic_language` in `tests/test_services.py` catches regressions. Reader forms the judgment; the journalist reports position.
- **Public-launch access gates (`app/gates.py`) — dormant locally, armed via Fly secrets.** `/mcp/*` requires header `X-Schools-Token` matching `MCP_ACCESS_TOKEN` (else 401); with `EDGE_TOKEN` set, non-MCP traffic must arrive through Cloudflare (which stamps `X-Edge-Token` via a Transform Rule) or gets 301'd to the canonical public host `nycschools.datatribune.io` (403 for non-GET). Both gates no-op when their env var is unset, so local dev / tests / CI are unchanged. Middleware order is deliberate: MCP gate OUTSIDE edge gate so a bad-token MCP call gets a clean 401, never a 301 to HTML. The rate limiter trusts `CF-Connecting-IP` only on requests that also carry a valid edge token. Matrix pinned by `tests/test_public_launch.py`. Cutover coordination lives in gitignored `tmp/todo-cloudflare.md` — it holds the live secret values; never commit it or copy tokens into tracked files or issues.
- **WebMCP exposure: three patterns.** (1) **Declarative forms** — exactly 4 spec attributes (`toolname` / `tooldescription` / `toolautosubmit` / `toolparamdescription`); inline forms on `/` and `/zoned` carry them, plus `partials/_webmcp_global_forms.html` reproduces both via `base.html`'s `{% block global_webmcp_forms %}` so every other page (school, neighborhood, sources, …) exposes them too. Override the block to empty on pages with inline duplicates — WebMCP requires unique toolnames per document. (2) **Imperative tools** — `navigator.modelContext.registerTool()` inline in templates that need to hand the agent page-specific context (DOM/URL aren't visible to in-browser chat panels). School pages register two: `get_current_school_details` (always-first; identity + `peer_ranks` + `peer_neighborhood` + `peer_district` + `co_located_schools` + staffing) and `get_swd_outcomes_for_current_school` (called only on IEP / special-ed questions per its description). (3) **Pre-visit manifest** at `/.well-known/webmcp` — declares the same two declarative-form tools as JSON so an agent can discover them without rendering the page. Source of truth is `_WEB_MCP_TOOLS` in `app/web/routes.py`; drift between manifest and form strings is caught by `test_webmcp_manifest_strings_match_form_partial` in `tests/test_webmcp.py`. Spec status: the official [WebMCP spec](https://webmachinelearning.github.io/webmcp/) does NOT mention `/.well-known/webmcp`; the Chrome team has discussed it as future work and the [freeCodeCamp WebMCP guide](https://www.freecodecamp.org/news/a-developers-guide-to-webmcp/) documents the manifest shape. We adopted it as an early-adopter convention because the alignment with the site's agent-first design is structural.
- **Inspector-extension audit checks, what to take seriously.** The community [WebMCP Inspector](https://chromewebstore.google.com/detail/webmcp-inspector/edfjnadfiapmddgplgnphlflgafmcino) by Shitij Agrawal (the one with built-in Gemini/Claude/OpenAI/Ollama chat) flags several non-spec checks; here is how to triage each:
   - `/.well-known/webmcp` missing → addressed; we ship one (see above bullet).
   - `/llms.txt` missing → addressed; we ship one at `/llms.txt` per [llmstxt.org](https://llmstxt.org). Jeremy Howard / Answer.AI convention; root-level markdown orienting LLMs to the site.
   - `toolaction` attribute → confirmed NOT in the WebMCP spec. The declarative API has only the 4 attributes named above plus the `toolactivated` DOM event. Ignore this audit check.
   - `window.ai` / Gemini Nano detection → this is a Chrome browser-capability check (whether the user's browser can run on-device Gemini Nano). A site cannot "add" `window.ai`; it's a browser API exposed by the user's Chrome. Non-actionable from the site side. Ignore.
   - `Canonical URL` / `Schema.org JSON-LD` missing → SEO checks, not WebMCP; out of scope unless we decide to target organic search (we currently block crawlers via robots.txt — see `_ROBOTS_TXT` in `routes.py`). Google's own spec-accurate Inspector (`beaufortfrancois/model-context-tool-inspector`) sticks to spec-defined checks; the Shitij Agrawal extension layers additional convention checks on top, which is where the noise comes from.
- **Cohort comparison is the journalism layer; pair every stat with position.** `SchoolDetail.peer_ranks` carries rank/total/extremes vs same-school-level NYC peers on All-Students metrics; `school_peers(scope="neighborhood"|"district")` gives same-NTA / same-district cohorts; `SwdCohortContext` on `SwdOutcomes` does the same for SWD-subgroup outcomes with pre-computed neutral `narrative` strings. Agent payloads on school pages include all four comparison dimensions so an in-browser agent can answer "vs other Park Slope ES" or "what other schools share this building" without round-tripping. Service-layer rule: when you add a new metric to a page, also surface the comparison.
- **Dynamic capability discovery via `app/services/metrics.py`.** Two registries — `SCHOOL_METRICS` and `NEIGHBORHOOD_METRICS` — drive the `list_*_metrics` / `get_*_metric` MCP pair. School and neighborhood are distinct data universes per intentional design; they don't share a single dispatcher. Adding a new column in `scripts/build_db.py` → analytics.py loader → one registry entry surfaces it to every agent without an MCP code change. The 15 curated tools (`top_schools`, `bulk_metrics`, `school_swd_outcomes`, …) keep their existing `METRIC_DESCRIPTIONS`-based path — the registry is **additive**, and `metrics.py` delegates computation to `analytics._compute_metric` so the numbers are guaranteed identical. A future pass can fold the curated tools onto the registry; the test `test_school_registry_covers_every_compute_metric_path` in `tests/test_metrics.py` keeps the two surfaces in lockstep meanwhile. Origin: Shipper's "agent-native architectures" piece — *domain tools are shortcuts, not gates.*
- **The demo regression test (`tests/test_services.py::test_demo_chain_428_w_26_st_to_chelsea_prep_to_d75_neighbor`) pins the address → zoned ES → SWD outcomes → co-located D75 → staffing chain.** The narrative Scott uses in every demo. Before refactoring anything across `services/zoning.py` + `services/schools.py`, run this test first; if it fails, the demo would too. Geocode call is mocked via respx; everything downstream hits real SQLite + geo data. Also asserts the MS admission context: `ms_admission_type == "zone_priority_choice"`, `02M297` in `middle`, and the `ms_admission_note` points the agent at `schools_in_district(2, "middle")`.
