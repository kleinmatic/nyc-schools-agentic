# NYC Schools Agentic

**Live at https://nycschools.fly.dev**

Interactive site/server for NYC public school data, keyed by **DBN** (e.g. `15K321`). Serves HTML pages to humans and (planned) MCP/A2A/ACP surfaces to agents — the service layer is designed so a single function powers all of them.

This repo is the **app** (FastAPI server, service layer, frontend, future agentic surfaces, deployment). The running app reads from a committed SQLite database in `data/`. The upstream [`nycschools`](https://github.com/adelphi-ed-tech/nycschools) package (Adelphi Ed Tech, AGPL-3.0) — and our [fork at `kleinmatic/nycschools`](https://github.com/kleinmatic/nycschools/tree/nysed-src-loader) — is a **build-time-only** dependency, used by `scripts/build_db.py` to assemble that SQLite. See [CLAUDE.md](./CLAUDE.md) for architecture and repo-boundary policy.

## Prerequisites

- **macOS or Linux** (Windows untested)
- **[uv](https://docs.astral.sh/uv/)** — `brew install uv`
- **Git LFS** — `brew install git-lfs && git lfs install` (one-time, per machine). The committed data files in `data/` are stored in Git LFS; without LFS, your clone gets the small pointer files and the app fails to load the SQLite.

That's it for *running* the app. Refreshing the data from upstream is a separate, rarer workflow with extra requirements; see ["Refreshing data"](#refreshing-data) below.

## Bootstrap

```bash
git clone <this-repo-url>
cd nyc-schools-agentic
uv sync
uv run uvicorn app.main:app --reload
# → http://localhost:8000
```

That's the whole bootstrap. The app reads from `data/data.sqlite` (LFS-tracked) and a few small geo/feather files, all committed to the repo. Cold-start is ~1 second.

If you forget `git lfs install` before cloning, run `git lfs pull` from inside the repo to fetch the actual data files.

## Run the site locally

```bash
uv run uvicorn app.main:app --reload
# → http://localhost:8000
```

Cold start ~1s. `--reload` watches Python files and restarts on save.

Routes you'll have:
- `/` — search page (htmx live results as you type)
- `/search?q=...` — same search, also returns htmx partial when called with `HX-Request: true`
- `/school/{dbn}` — school detail (e.g. `/school/15K321`)
- `/find?address=...` — address-based zoned-school lookup
- `/mcp/` — MCP server over Streamable HTTP (see [MCP server](#mcp-server) below)
- `/healthz` — liveness check
- `/docs` — auto-generated OpenAPI / Swagger UI (FastAPI default)

## MCP server

The same FastAPI process serves an [**MCP**](https://modelcontextprotocol.io) endpoint at `/mcp/` (note the trailing slash) over [Streamable HTTP](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports#streamable-http). It's a sibling adapter to the HTML routes — same `app/services/` functions, same Pydantic models, separate transport. Built with [FastMCP](https://gofastmcp.com).

- **Production:** `https://nycschools.fly.dev/mcp/`
- **Local:** `http://localhost:8000/mcp/` (after `uv run uvicorn app.main:app --reload`)
- **Auth:** none. Public data, public endpoint.
- **Source (AGPL §13):** [github.com/kleinmatic/nyc-schools-agentic](https://github.com/kleinmatic/nyc-schools-agentic) — the MCP tool definitions live in [`app/mcp_server/server.py`](./app/mcp_server/server.py).

### Tools at a glance

**One-school lookup**

| Tool | Args | Returns | Use for |
|---|---|---|---|
| `search_schools` | `query: str`, `limit: int = 10` | `list[SchoolSummary]` | Resolve a school name to a DBN (the primary key for everything else). |
| `get_school` | `dbn: str` | `SchoolDetail \| None` | Full report for one school: demographics by year, exams, Regents, class size, budget, NYSED accountability, peer ranks. **Heavy** — 8–14 K tokens per call. |
| `find_schools_for_address` | `address: str` | `FindSchoolsForAddressResult \| None` | Geocode a NYC address → return its zoned ES + MS schools. The natural entry point for "where should I send my kid?" |
| `geocode_address` | `address: str` | `GeocodingResult \| None` | Plain geocode escape hatch. Most callers want `find_schools_for_address` instead. |

**Cross-school analytics & browse**

| Tool | Args | Returns | Use for |
|---|---|---|---|
| `list_high_schools` | `borough?`, `accessibility?`, `program_keyword?`, `limit=50` | `list[HsListing]` | Browse / filter HS Directory: "performing arts HS in Brooklyn", "fully accessible HS in Bronx." |
| `top_schools` | `metric`, `level="high"`, `limit=20`, `borough?`, `ascending=False` | `list[RankedSchool]` | Ranked leaderboards by accountability metric: "top HS by Regents passing rate", "lowest chronic absence." See [metric vocabulary](#metric-vocabulary) below. |
| `bulk_metrics` | `level="high"`, `metrics?`, `borough?` | `list[MetricRow]` | One row per active school × N metrics. For correlations and dashboards — ~10 K tokens for HS-level full dump. |

**Place-based aggregations**

| Tool | Args | Returns | Use for |
|---|---|---|---|
| `top_neighborhoods` | `metric`, `level="high"`, `limit=10`, `ascending=False`, `min_schools=5` | `list[NeighborhoodAggregate]` | Rank NYC NTAs (Neighborhood Tabulation Areas) by mean of a metric across their schools: "best neighborhoods for ES", "neighborhoods with highest chronic absence." |
| `borough_summary` | `metrics?`, `level="high"` | `BoroughGrid` | 5-borough × N-metric overview: side-by-side ENI / outcomes across Manhattan, Brooklyn, Queens, Bronx, Staten Island. |
| `school_peers` | `dbn`, `scope="neighborhood"\|"district"`, `limit=20` | `PeerCohort \| None` | Same-NTA or same-district peer schools for a given DBN. Focal school flagged via `is_self=true`. District scope is most useful for ES/MS — HS is city-wide choice. |

> **Neighborhood / zone vocabulary used across these tools:** *Neighborhood* = NTA (Neighborhood Tabulation Area), NYC's official 195 neighborhoods — closest formal proxy to colloquial neighborhood names. *District* = one of NYC's 32 geographic school districts; the natural admissions zone for elementary and middle schools.

Tool input/output schemas are auto-generated from the Pydantic models in [`app/services/models.py`](./app/services/models.py) and exposed via the standard MCP `list_tools` / `tools/list` calls.

#### Metric vocabulary

Used by `top_schools` and `bulk_metrics`. All values are 0..1 fractions except `per_pupil_expenditure` (USD).

| Metric | Source | Applies to | Notes |
|---|---|---|---|
| `eni` | DOE demographics | all levels | Economic Need Index; equity-proxy of choice (see [ENI vs poverty_pct](#eni-vs-poverty_pct--which-to-use-for-equity-comparisons)). |
| `poverty_pct` | DOE demographics | all levels | Direct certification (HRA / SNAP / Medicaid / temp housing). |
| `attendance_rate` | DOE snapshots | all levels | Mostly AY 2016 vintage. |
| `chronic_absent_rate` | NYSED SRC | all levels | ≥18 days absent. **Lower is better** — pass `ascending=True` to top_schools. |
| `ela_pct_proficient` / `math_pct_proficient` | NYS 3-8 exams | ES/MS/K-8/6-12 only | Level 3-4, All Grades, latest year. |
| `regents_pct_above_64` / `regents_pct_above_79` | DOE Regents | HS / 6-12 only | Mean across all exams, latest year. ≥65 = passing; ≥80 = mastery. |
| `graduation_rate_4yr` | NYSED SRC | HS / 6-12 only | All Students, 4-year cohort. |
| `pupil_teacher_ratio` | DOE | all levels | Lower is generally seen as better. |
| `pct_inexperienced_teachers` | NYSED SRC | all levels | Teachers with <4 years experience. |
| `pct_out_of_cert_teachers` | NYSED SRC | all levels | Teaching outside their certification area. |
| `per_pupil_expenditure` | NYSED SRC | all levels | Federal + state + local combined, USD. |

**Conventions to know when calling these tools:**
- DBN (e.g. `15K321`) is the primary key everywhere.
- Percentages are `0..1` fractions (`0.83` = 83%), not `0..100`.
- Years are academic-year start integers — `2024` means 2024-25.
- `eni` (Economic Need Index) is the equity proxy of choice for ranking; `poverty_pct` is for direct interpretability. See [ENI vs poverty_pct](#eni-vs-poverty_pct--which-to-use-for-equity-comparisons) below.

### Quick smoke test

Easiest interactive check is the official **[MCP Inspector](https://github.com/modelcontextprotocol/inspector)** — it handles the Streamable HTTP `initialize` → session-id handshake for you and gives you a browser UI to list tools and call them:

```bash
npx @modelcontextprotocol/inspector
# In the UI: Transport = "Streamable HTTP", URL = https://nycschools.fly.dev/mcp/
```

For a scripted check without Node, the Python snippet below works.

### Connect from Python (FastMCP `Client`)

The simplest path. `pip install fastmcp` (or `uv add fastmcp`), then:

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("https://nycschools.fly.dev/mcp/") as c:
        tools = await c.list_tools()
        print("tools:", [t.name for t in tools])

        # Find Park Slope's zoned ES + MS by address.
        r = await c.call_tool(
            "find_schools_for_address",
            {"address": "180 7th Avenue, Brooklyn"},
        )
        print(r.data.geocoding.label)
        for s in r.data.schools.elementary:
            print("ES:", s.dbn, s.school_name)
        for s in r.data.schools.middle:
            print("MS:", s.dbn, s.school_name)

asyncio.run(main())
```

`r.data` is a parsed Pydantic model — same shape the HTML site renders against.

### Connect from LibreChat

Add to `librechat.yaml`:

```yaml
mcpServers:
  nyc-schools:
    type: streamable-http
    url: https://nycschools.fly.dev/mcp/
    timeout: 30000
```

Restart LibreChat; the four tools appear in the model's tool list.

### Connect from Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nyc-schools": {
      "type": "http",
      "url": "https://nycschools.fly.dev/mcp/"
    }
  }
}
```

Restart Claude Desktop. (Note: at the time of writing, Claude Desktop's `http` transport is Streamable HTTP. If you have an older build that only supports stdio, run a small bridge — easiest is the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) npm package: `npx mcp-remote https://nycschools.fly.dev/mcp/`.)

### Run the tests

```bash
uv run pytest                    # full suite, ~2s
uv run pytest --cov=app          # with coverage
uv run pytest -k routes
```

Tests load `data/data.sqlite` (committed to the repo) once per session — no setup beyond `uv sync` required.

## Refreshing data

The running app reads from `data/` (committed). Those files are *built* from `school-data/` (gitignored upstream cache). You only need to refresh when upstream sources change — typically once a year when NYSED publishes a new School Report Card.

**Extra prerequisites for a refresh:**
- **unixODBC** — `brew install unixodbc` (upstream's `pyodbc` won't build without it)
- **mdbtools** — `brew install mdbtools` (reads NYSED's Microsoft Access `.mdb`)
- **The upstream `nycschools` fork cloned as a sibling**:
  ```
  ~/Code/
    nycschools/          # https://github.com/kleinmatic/nycschools
                         # branch: nysed-src-loader (PR pending upstream)
    nyc-schools-agentic/ # this repo
  ```
- **Build-only deps installed:** `uv sync --group build`

**Refresh sequence (human-driven; the production server NEVER updates data on its own):**

```bash
# 1. Pull raw upstream into school-data/ (~600 MB, several minutes; downloads
#    the 367 MB NYSED SRC zip and extracts the 1.5 GB Access database).
uv run --group build scripts/fetch_data.py

# 2. Filter + write data/data.sqlite + copy small geo files into data/.
uv run --group build scripts/build_db.py

# 3. Verify locally — full suite must pass.
uv run pytest

# 4. MANUAL smoke test — boot the app, click through a few schools.
uv run uvicorn app.main:app --reload
# Visit http://localhost:8000, search "Midwood High School", click through.
# Visit http://localhost:8000/find?address=180+7+Ave+Brooklyn — should show PS 321.
# Verify the school page renders all sections you expect.

# 5. Commit. Files in data/ go through Git LFS automatically (see .gitattributes).
git add data/
git commit -m "Refresh data from upstream (NYSED SRC YYYY)"
git push           # pushes LFS blobs to the remote LFS store
```

After CI runs and passes, the deploy workflow picks up the new commit, fetches the LFS blobs at build time, bakes them into the deploy artifact, and ships. **The running server never re-pulls data** — every refresh is a deliberate, reviewable git commit.

After step 2, the 1.5 GB `school-data/SRC2025_Group3.mdb` can be deleted to reclaim disk — the feathers extracted from it are the working source.

### Why we don't use upstream's `python -m nycschools.dataloader -d`

Upstream's bulk-archive bootstrap downloads a single `.7z` from a Google Drive link. **That link is dead** as of 2026 and returns a 404 HTML page. The package's own `dataloader.load()` already has a per-file fallback to `https://data.mixi.nyc/<filename>` — that host is alive and serves all the cleaned files individually. `scripts/fetch_data.py` calls each loader once to pre-warm the cache via that fallback.

## Exploring the data

Easiest path is the running app — `/`, `/search?q=...`, `/school/15K321`, `/find?address=...`. For ad-hoc inspection of the SQLite directly:

```bash
sqlite3 data/data.sqlite
> .schema demographics
> SELECT dbn, school_name, eni FROM demographics
    WHERE ay = 2024 AND school_level = 'elementary'
    ORDER BY eni DESC LIMIT 5;
```

Two ad-hoc CLI tools that hit the **upstream raw data** (require `uv sync --group build` and a populated `school-data/`):

```bash
uv run --group build scripts/find_school.py "Midwood High School"   # → 22K405
uv run --group build scripts/inspect_school.py 15K321 --year 2024
```

These are pre-SQLite tooling kept for inspecting the upstream side during data-refresh debugging; the web app's own search (`/search?q=Midwood`) is the easier path for everyday lookups.

### DBN cheatsheet

A DBN is `<district><borough><school#>`:
- District: 1-32 (geographic), 75 (D75 special-needs), 79 (alt programs), 84 (charters)
- Borough: `M` Manhattan, `X` Bronx, `K` Brooklyn, `Q` Queens, `R` Staten Island
- School number: 3–4 digits, mostly unique within a borough

So `15K321` = District 15, Brooklyn, school 321 (P.S. 321 William Penn).

## Data inventory

`app/data.py` reads the committed `data/data.sqlite` (~50 MB) and four small geo / feather files from `data/` at startup, holding everything in pandas/geopandas dataframes in memory (~250 MB RAM total). Every dataset is keyed by **DBN** (`<district><borough><school#>`, e.g. `15K321`). The year column is always **`ay`** (academic year start, integer — `2024` means 2024-25).

The tables described below are the **on-disk and in-memory** shapes after `scripts/build_db.py` has filtered the raw upstream data. For the per-loader semantics of the upstream side (which categories, years, gotchas), this section also documents what each table represents.

### Quick reference

| Dataset | Rows | Granularity | Year coverage | Key fields |
|---|---:|---|---|---|
| `demographics` | ~32k | dbn × ay | 2005–2024 | enrollment by grade, race %, ELL %, SWD %, poverty %, ENI, zip |
| `snapshots` | ~7.7k | dbn (mostly latest) | mostly 2016 vintage | principal, address, attendance, chronic absence, admissions method, quality review |
| `ela` | ~594k | dbn × grade × category × ay | 2013–2018, 2021–2022 | mean scale score, % at each level, % proficient |
| `math` | ~586k | dbn × grade × category × ay | 2013–2018, 2021–2022 | same as ELA, math version |
| `regents` | ~534k | dbn × exam × category × ay | 2014–2022 | mean score, % below 65, % ≥ 65, % ≥ 80, % college-ready |
| `class_size` | ~45k | dbn × grade × subject × ay | 2022 only | avg/min/max class size, students_n, classes_n |
| `ptr` | ~1.5k | dbn × ay | 2022 only | pupil:teacher ratio |
| `locations` | ~1.95k | dbn (latest) | 2019–2020 | lat/lon, address, NTA, council district, building code |
| `shsat` | ~3.6k | dbn × ay | 2015–2020 | applicants, testers, offers (8th-graders → specialized HS) |
| `budgets` | ~108k | dbn × line item × ay | 2022 only | item, positions, budget¹, category |
| `hs_directory` | ~442 | dbn (HS only) | 2021 only | overview, programs, AP, sports, etc. (449 columns) |
| `zipcodes` (geo) | 409 | zip code polygons | — | borough boundaries by USPS zip |
| `neighborhoods` (geo) | 299 | neighborhood points | — | NYT-derived neighborhood names |

¹ `budget` ships as currency strings (`'$ 187,530'`); we parse to floats in `app/services/schools.py`. **TODO: PR back to upstream.**

### Per-dataset notes

**`demographics`** — `schools.load_school_demographics()` → `school-demographics.csv`
Widest annual coverage in the project. Use this to answer "how has this school changed over time?" Includes total + per-grade enrollment (3K–12), gender (female/male/non-binary as N and pct), 7 race/ethnicity buckets, ELL, SWD, poverty count + pct, and **ENI** (Economic Need Index — NYC's wealth-adjusted poverty proxy on a 0–1 scale). Latest year cached: 2024-25. See "ENI vs poverty_pct" below for which to use as the equity proxy.

**`snapshots`** — `snapshot.load_snapshots()` → `snapshot.feather`
DOE official school portal snapshot, scraped at one point in time (most rows are dated `ay=2016`). Useful for: principal name + tenure + phone, full address, attendance, chronic absence, official admissions method (Zoned, Screened, etc.), quality review URL + year, teacher-3yr-experience pct, co-location info. **Caveat:** not all DBNs are present (e.g., Midwood `22K405` has no row).

**`ela` / `math`** — `exams.load_ela()` / `load_math()` → `nyc-ela.csv`, `nyc-math.csv`
NYS grades 3–8 standardized exams in long format. **Categories** include All Students plus 14 demographic breakdowns (Asian, Black, Hispanic, White, Multi-Racial, Native American, Female, Male, Current ELL, Ever ELL, Never ELL, Econ Disadv, Not Econ Disadv, SWD, Not SWD). **Grades** 3–8 individually plus an "All Grades" aggregate row per category × year. **COVID gap:** no 2019 or 2020 testing. Charter schools are merged in (look at the `charter` column).

**`regents`** — `exams.load_regents()` → `nyc-regents.csv`
HS Regents exams, same demographic-category structure as 3–8. Exams covered: Common Core English, Algebra, Algebra2, Geometry, Living Environment, Earth Science, Chemistry, Physics, Global History, US History, plus older non-Common-Core variants and language exams (Spanish, French, Italian, Latin, Chinese, Hebrew, etc.). **Score thresholds:** 65 = passing; 80 = "mastery" (Advanced Designation). College-ready cutoffs vary by exam.

**`class_size`** — `class_size.load_class_size()` → `school-class-size.csv`
Average / min / max class size per (DBN, grade, subject, program). Currently only 2022-23. Subject is broad ("Elementary", "English", "Math", "Science"). Program type breaks out Gen Ed / ICT (integrated co-teaching) / self-contained.

**`ptr`** — `class_size.load_ptr()` → `ptr.csv`
Pupil-to-teacher ratio per (DBN, ay). 2022-23 only. Single number per row.

**`locations`** — `geo.load_school_locations()` → `school_locations.geojson`
GeoDataFrame with point geometry. Provides the canonical mapping of DBN → lat/lon, full address, zip, **NTA** (Neighborhood Tabulation Area — NYC's official neighborhood definitions), council district, community district, BBL (block-block-lot for tying to property records), and building code.

**`shsat`** — `shsat.load_admission_offers()` → `shsat-applicants.csv`
SHSAT outcomes per (sending DBN, ay) — i.e. how many of *this* school's 8th-graders applied to, tested for, and got offers from one of the eight specialized high schools (Stuyvesant, Bronx Science, Brooklyn Tech, etc.). Most relevant for middle schools. **Caveat:** small-cell suppression — values that were "0–5" in raw data are encoded as `2`.

**`budgets`** — `budgets.load_galaxy_budgets()` → `galaxy-budget.csv`
Galaxy budget portal data, scraped per line item per (DBN, ay). Currently only 2022-23. Categories include Classroom Teacher, Leadership, OTPS (Other Than Personal Services — supplies, contracts), Paraprofessionals, Guidance/Social Workers, etc. **Caveat:** the `budget` column contains strings like `'$ 187,530'`; we parse them client-side. This belongs upstream as a PR.

**`hs_directory`** — `schools.load_hs_directory(ay=2021)` → `data/hs-directory.feather`
The 8th-grader-facing High School Directory data. **442 schools × 449 columns.** Includes overview paragraph, all admissions programs (up to 12 per school) with seats/applicants/applicants-per-seat ratios, AP courses, language classes, PSAL sports (boys/girls/coed), graduation rate, attendance rate, college-career rate, accessibility status, transit info, etc. **AY 2021 only loaded by default**; the upstream loader supports 2013–2021.

**Build quirk:** `hs_directory` is the *only* dataset NOT cached on `data.mixi.nyc`. `scripts/fetch_data.py` pulls from the NYC Open Data SODA API once and persists as feather; `scripts/build_db.py` then copies it into `data/`.

**`zipcodes` / `neighborhoods`** — `geo.load_zipcodes()` / `load_neighborhoods()`
Boundary polygons (zipcodes) and labeled points (neighborhoods). Loaded for future borough/zone pages; not currently surfaced on the school page.

### NYSED School Report Card Database (SRC 2025)

Layered on top of the nycschools data, we also pull NYSED's annual ESSA Report Card Database. This is the **freshest data we have** (April 30, 2026 release; covers 2024-25 results and 2025-26 designations). During refresh: downloaded as a Microsoft Access (`.mdb`) inside a 367 MB ZIP from `https://data.nysed.gov/files/essa/24-25/SRC2025.zip`, extracted via `mdbtools` (build-time system dep), and persisted as one feather per table under `school-data/nysed-src-2025-*.feather`. `scripts/build_db.py` then loads the relevant tables into `data/data.sqlite` as `nysed_*` tables.

The `nysed_src` loader lives on the [`nysed-src-loader` branch of `kleinmatic/nycschools`](https://github.com/kleinmatic/nycschools/tree/nysed-src-loader), pending PR back to Adelphi upstream.

Filtering: NYSED data covers all of NY State; the loader filters to NYC public schools by `ENTITY_CD` (12-digit BEDS code) prefix in `("31", "32", "33", "34", "35")` — one prefix per borough.

| NYSED dataset | Granularity | Year | Notable fields |
|---|---|---|---|
| `nysed_essa_status` | school × year | 2024, 2025 | OVERALL_STATUS (CSI/TSI/ATSI/Local Support) |
| `nysed_essa_subgroup` | school × year × subgroup | 2024, 2025 | which subgroups triggered TSI/ATSI |
| `nysed_chronic` | school × year × level × subgroup | 2024, 2025 | enrollment, absent count, absent rate |
| `nysed_expenditures` | school × year | 2024, 2025 | per-pupil federal, state/local, combined |
| `nysed_inexp_teachers` | school × year | 2024, 2025 | num + pct of inexperienced teachers/principals |
| `nysed_out_of_cert` | school × year | 2024, 2025 | num + pct of teachers teaching out of certification |
| `nysed_hs_grad` | HS × year × subgroup × cohort | 2024, 2025 | 4-yr / 5-yr / 6-yr / Combined cohort grad rates |
| `nysed_hs_cccr` | HS × year × subgroup | 2024, 2025 | College/Career/Civic Readiness index + level (1–4) |

(Plus 8 additional NYSED tables — Annual EM ELA/Math/Science, Annual Regents, Total Cohort Regents, Accountability Levels, Institution Grouping — extracted to feathers and ready to use, but not currently surfaced on the school page since the older nycschools versions of these still drive the existing exam tables.)

#### Suppression and types

Numeric fields in the source Access database are stored as Text. NYSED uses the literal string `"s"` to indicate values suppressed for small-cell privacy (fewer than 5 students in a subgroup). The build-time loader (`nycschools.nysed_src._to_numeric`, on the fork's `nysed-src-loader` branch) coerces these to NaN before we write to SQLite. Percentages come back in 0–100 units; our service layer divides by 100 to match the rest of the app's 0–1 fraction convention.

### ENI vs poverty_pct — which to use for equity comparisons

The demographics file ships two related signals of school disadvantage. They're not interchangeable; the choice has real implications for any ranking, equity analysis, or "compared to peers" view we build.

**`poverty_pct`** is the share of students "directly certified" — meaning their families are currently enrolled in HRA cash assistance, SNAP, Medicaid, or are in temporary housing. It's a binary count (in or out, on the day NYC DOE pulls it). Historically NYC used Free or Reduced-Price Lunch (FRPL) eligibility, which captured a wider population including the working poor. But in 2017 NYC moved to universal free meals (Community Eligibility Provision), which broke FRPL as a school-poverty signal — every student now gets free lunch regardless of income — so DOE switched to direct certification. The new metric is *stricter* than FRPL was; it misses families who would have qualified for reduced-price meals but aren't actively in social-service programs.

**`eni`** (Economic Need Index, 0–1) is a purpose-built composite that NYC DOE designed specifically to allocate Fair Student Funding. Its components:
- Students in temporary housing
- Students whose families receive HRA / public assistance
- Students living in low-income census tracts (Census ACS data)
- Recent-arrival ELL students
- Students directly certified for free meals via SNAP / Medicaid

ENI is the better default for ranking and equity work, for four reasons:

1. **NYC DOE itself uses ENI** to allocate Fair Student Funding to schools. The agency closest to the data thinks ENI is the right measure for resource decisions.
2. **More dimensions of disadvantage.** A school where 40% of students live in low-income census tracts but only 25% are direct-certified is meaningfully different from one with the reverse — ENI surfaces both.
3. **More stable across program-eligibility shifts.** When a single program's rules change (CEP transition is the textbook case), a composite metric absorbs the change better than any single indicator.
4. **Better correlates with achievement gaps.** Research on NYC schools shows ENI predicts academic outcomes more reliably than direct certification alone.

**Where each shows up in this app:**
- `poverty_pct` is shown in the school page's **Quick stats** card and the **Demographics by year** history table — concrete and directly interpretable for parents who want a literal "% of kids in HRA/SNAP" reading.
- `eni` drives the **"Compared to peers"** marker, since ranking is where the better signal matters most. Future borough/zone equity views should also default to ENI.

Both are kept in the demographics dataframe, so callers can request either. Don't treat them as redundant.

### Coverage matrix

Which datasets have meaningful per-DBN data for which school types:

| Dataset | Elementary | Middle | High |
|---|:-:|:-:|:-:|
| demographics | ✓ | ✓ | ✓ |
| snapshots | usually ✓ | usually ✓ | sometimes (gaps) |
| ELA / math (3–8) | ✓ | ✓ | — |
| Regents | — | — | ✓ |
| class_size, ptr | ✓ | ✓ | ✓ |
| locations | ✓ | ✓ | ✓ |
| SHSAT | — | ✓ | — |
| budgets | ✓ | ✓ | ✓ |
| HS directory | — | — | ✓ |
| NYSED ESSA + chronic + expenditures + teacher-quality | ✓ | ✓ | ✓ |
| NYSED HS grad rate + CCCR | — | — | ✓ |

The school page (`/school/{dbn}`) renders each section conditionally on data presence — an elementary school gets ~14 sections, a high school gets ~20.

### What we *don't* load

Available in upstream `nycschools` but not currently wired into the app:

- `geo.load_school_footprints()` — building polygons. Worth adding when we ship a real map.
- `geo.load_city_footprints()` — every NYC building. Hundreds of MB; only worth loading if needed.
- `geo.load_districts()` — district boundaries. Stale URL in upstream.
- `exams.load_charter_ela()` / `load_charter_math()` — charter rows are already merged into our regular ELA/math via the `charter` flag.
- `exams.load_regents_excel()`, `load_math_excel()`, `load_ela_excel()` — legacy paths that re-fetch from DOE InfoHub Excel files. We already have the cleaned versions.
- `cep.get_ceps()` — Capital Expenditure Plan (Selenium-scraped from iPlan, fragile).
- `nysed.load_nyc_nysed()` / `load_nys_nysed()` — overlaps with our existing exam data.
- `segregation.*` — analytical functions, not data sources.
- `budgets.get_galaxy_budgets()` — the live Selenium scraper. We use the cached output, not the scraper.

**To add a new dataset:** add the upstream loader call in `scripts/build_db.py` (write a new SQLite table or file under `data/`), then add a load step in `app/data.py`, a Pydantic model in `app/services/models.py`, a helper in `app/services/schools.py`, and a template block in `app/web/templates/school.html`. Same shape for every dataset; takes about an hour each.

## Repo layout

```
.
├── pyproject.toml         # runtime deps + [build] group for refresh scripts
├── uv.lock                # committed; reproducible installs
├── .gitattributes         # routes data/* through Git LFS
├── Dockerfile             # production runtime image
├── fly.toml               # Fly.io app config
├── .github/workflows/
│   └── main.yml           # CI (tests on push/PR) + Deploy (push to main)
├── data/                  # LFS-tracked working set the app reads at startup
│   ├── data.sqlite                # ~50 MB; tabular data
│   ├── school-locations.geojson   # school point locations
│   ├── school-zones-{es,ms}.geojson  # attendance zone polygons
│   └── hs-directory.feather       # AY 2021 HS directory (wide format)
├── school-data/           # gitignored; raw upstream cache for refresh only
├── app/
│   ├── main.py            # FastAPI app, lifespan-loaded data
│   ├── config.py          # paths to data/ and (for build) school-data/
│   ├── data.py            # reads data/data.sqlite + geo files into memory
│   ├── services/          # transport-agnostic data-access functions
│   │   ├── models.py      # Pydantic schemas (the cross-surface contract)
│   │   ├── schools.py     # search_schools, get_school, peer ranks, etc.
│   │   └── zoning.py      # geocode + find_zoned_schools (address search)
│   └── web/               # thin Jinja-rendering adapters
│       ├── routes.py      # /, /search, /school/{dbn}, /find
│       └── templates/     # base, search, school, find, partials/
├── tests/
│   ├── conftest.py        # session-scoped data load
│   ├── test_services.py
│   ├── test_routes.py
│   ├── test_zoning.py
│   └── test_helpers.py
├── scripts/
│   ├── fetch_data.py      # build-time: pull upstream → school-data/
│   ├── build_db.py        # build-time: filter → data/data.sqlite + geo
│   ├── find_school.py     # ad-hoc: name → DBN lookup (build group)
│   └── inspect_school.py  # ad-hoc: dump everything we know about a DBN
├── CLAUDE.md              # architecture & repo-boundary policy
├── SECRETS.md             # gitignored; ops details (Fly app name, secrets map)
└── README.md
```

## Where to put new code

(Repeated from CLAUDE.md, since this is the most-asked question.)

| Goes in our fork [`kleinmatic/nycschools`](https://github.com/kleinmatic/nycschools) (PR back to Adelphi when ready) | Goes here |
|---|---|
| New data loaders, schema fixes, dataset modules | New service-layer functions over the SQLite |
| Bug fixes in existing loaders | HTTP / MCP / A2A / ACP route adapters |
| New tests for the data layer | Tests for the service & route layers |
| Documentation for the package | Site templates, frontend, deploy, project notes |

If another NYC-schools-data project could reuse it, it's upstream. Otherwise it's here.

**Adding a new operation:** define it once in `app/services/schools.py` returning a Pydantic model from `app/services/models.py`. It's automatically available to every surface (HTML, JSON API, future MCP/A2A/ACP). Adapters wrap; services compute. Don't import `Request`, `Context`, or other transport types into `services/`.

## License & private state

This project is **AGPL-3.0** (matching upstream `nycschools`). Full text in [LICENSE](./LICENSE).

In practice that means:
- Anyone we share code with — and anyone interacting with a deployed service that runs this code — has the right to obtain the full source, including any modifications. The site footer links to this GitHub repo to satisfy the disclosure obligation.
- Any code that imports from this project (or from `nycschools`) has to be AGPL-compatible. Plan dependencies with that in mind.

**Don't commit anything you wouldn't put in a public repo.** Deploy keys, internal notes, vendor credentials, scratch SQL, etc. should either:
1. Land in `.env`, `secrets/`, `private/`, `notes/`, or any `*.local` file (all gitignored), **or**
2. Live in a sibling private repo (e.g. `~/Code/nyc-report-card-private/`). Cleaner, harder to leak by accident.

## Deployment

**Live at https://nycschools.fly.dev**

CI/CD via GitHub Actions, Fly.io for hosting. One workflow file (`.github/workflows/main.yml`) with two jobs:

- **`test`** runs on every push and PR (~1 min). Pulls LFS-tracked data (cached by pointer hash so we don't burn LFS bandwidth), installs system libs (libgdal/libgeos/libproj for geopandas), `uv sync --frozen --no-group build`, `uv run --no-sync pytest --cov=app`.
- **`deploy`** runs only on push to `main`, only if `test` passed (~2-5 min). Pulls LFS data fully, then `flyctl deploy --remote-only` — Fly's remote builder builds the Dockerfile and rolls the machines.

The Dockerfile bakes `data/data.sqlite` and the geo files into the image at build time, so the deployed container is fully self-contained: no runtime data fetch, no upstream API access, no `mdbtools` system dep. Currently runs as `shared-cpu-1x@2gb` in `ewr` (Newark) with auto-stop when idle (sub-2s cold start).

Operational details (Fly account, dashboard URL, common admin commands, token rotation, recovery scenarios) live in `SECRETS.md` (gitignored). For a typical iteration: branch or push to main, watch the [Actions tab](https://github.com/kleinmatic/nyc-schools-agentic/actions), then verify at https://nycschools.fly.dev.

## Gotchas

- `urllib3 NotOpenSSLWarning: ... LibreSSL 2.8.3` on macOS during data refresh — benign, comes from system Python's TLS stack. Ignore.
- `pandas DtypeWarning: Columns (18) have mixed types` during build_db.py — also benign; column 18 is `school_type` which has stringy values.
- `school-data/` is gitignored on purpose — don't commit it. The committed working set is `data/` (with LFS).
- If you run `git status` and see `data/*.sqlite` showing as modified after a fresh clone: you forgot `git lfs install` before cloning. Run `git lfs install && git lfs pull` from inside the repo.
