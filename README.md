# NYC Schools Agentic

Interactive site/server for NYC public school data, keyed by **DBN** (e.g. `15K321`). Serves HTML pages to humans and (planned) MCP/A2A/ACP surfaces to agents — the service layer is designed so a single function powers all of them.

This repo is the **app** (FastAPI server, service layer, frontend, future agentic surfaces, deployment). It consumes the upstream [`nycschools`](https://github.com/adelphi-ed-tech/nycschools) Python package as a read-only data layer. See [CLAUDE.md](./CLAUDE.md) for architecture and repo-boundary policy.

## Prerequisites

- **macOS or Linux** (Windows untested)
- **[uv](https://docs.astral.sh/uv/)** — `brew install uv`
- **unixODBC** — `brew install unixodbc` (one of upstream's deps, `pyodbc`, fails to build without it; we never actually call ODBC at runtime)
- **The upstream `nycschools` repo cloned as a sibling**:
  ```
  ~/Code/
    nycschools/        # https://github.com/adelphi-ed-tech/nycschools
    nyc-report-card/   # this repo
  ```
  `pyproject.toml` references it as `../nycschools` (editable install).

## Bootstrap

```bash
# 1. Clone both repos as siblings
cd ~/Code
git clone https://github.com/adelphi-ed-tech/nycschools.git
git clone <this-repo-url> nyc-report-card
cd nyc-report-card

# 2. Install (creates .venv/, populates uv.lock)
uv sync

# 3. Pre-warm the local data cache (~250 MB, a couple of minutes)
uv run scripts/fetch_data.py
```

That's it. After step 3, `./school-data/` contains 13 cleaned datasets — see [Data inventory](#data-inventory) below for the full breakdown.

The `.env` file (gitignored) sets `NYC_SCHOOLS_DATA_DIR=./school-data`. If you want to use the data from a different working directory, set the env var explicitly:

```bash
export NYC_SCHOOLS_DATA_DIR=$(pwd)/school-data
```

### Why we don't use upstream's `python -m nycschools.dataloader -d`

Upstream's bootstrap downloads a single `.7z` archive from a Google Drive link. **That link is dead** as of 2026 and returns a 404 HTML page (which the loader tries to unpack as 7z and crashes with `Bad7zFile: not a 7z file`).

We bypass it. The package's own `dataloader.load()` already has a per-file fallback to `https://data.mixi.nyc/<filename>` — that host is alive and serves all the cleaned files individually. `scripts/fetch_data.py` just calls each loader once to pre-warm the cache via that fallback.

## Run the site locally

```bash
uv run uvicorn app.main:app --reload
# → http://localhost:8000
```

Cold start takes ~5s while the dataframes load into memory; you'll see a "Data loaded: ..." log line. After that, `--reload` watches Python files and restarts on save.

Routes you'll have:
- `/` — search page (htmx live results as you type)
- `/search?q=...` — same search, also returns htmx partial when called with `HX-Request: true`
- `/school/{dbn}` — school detail (e.g. `/school/15K321`)
- `/healthz` — liveness check
- `/docs` — auto-generated OpenAPI / Swagger UI (FastAPI default)

### Run the tests

```bash
uv run pytest          # full suite, ~2s after the dataframes load
uv run pytest -k routes
```

Tests use FastAPI's `TestClient` and load the real on-disk data once per session. If you haven't run `scripts/fetch_data.py` yet, tests will fail with "Data not loaded."

## Exploring the data

Two CLIs (independent of the web server):

```bash
# Find a school's DBN. Accepts full names, short names, or partial DBNs.
uv run scripts/find_school.py "Midwood High School"   # → 22K405
uv run scripts/find_school.py "PS 321"                # → 15K321
uv run scripts/find_school.py "Stuyvesant"            # → 02M475 + nearby fuzzy hits
uv run scripts/find_school.py K405                    # DBN substring search

# Dump everything we have on a DBN across all loaders.
uv run scripts/inspect_school.py 15K321 --year 2024   # transposed view, one year
uv run scripts/inspect_school.py 15K321               # all years
```

Or open a Python REPL:

```bash
uv run python
```
```python
from nycschools import schools, snapshot, exams, class_size, geo
demo = schools.load_school_demographics()
schools.search(demo, "Midwood")          # fuzzy lookup
demo[demo["dbn"] == "22K405"]            # all years for one school
```

### DBN cheatsheet

A DBN is `<district><borough><school#>`:
- District: 1-32 (geographic), 75 (D75 special-needs), 79 (alt programs), 84 (charters)
- Borough: `M` Manhattan, `X` Bronx, `K` Brooklyn, `Q` Queens, `R` Staten Island
- School number: 3–4 digits, mostly unique within a borough

So `15K321` = District 15, Brooklyn, school 321 (P.S. 321 William Penn).

## Data inventory

`app/data.py` holds **11 dataframes plus 2 GeoDataFrames** in process memory after startup, totaling ~250 MB on disk and ~600 MB in RAM. Every dataset is keyed by **DBN** (`<district><borough><school#>`, e.g. `15K321`). The year column is always **`ay`** (academic year start, integer — `2024` means 2024-25).

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
Widest annual coverage in the project. Use this to answer "how has this school changed over time?" Includes total + per-grade enrollment (3K–12), gender (female/male/non-binary as N and pct), 7 race/ethnicity buckets, ELL, SWD, poverty count + pct, and **ENI** (Economic Need Index — NYC's wealth-adjusted poverty proxy on a 0–1 scale). Latest year cached: 2024-25.

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

**`hs_directory`** — `schools.load_hs_directory(ay=2021)` → `hs-directory-2021.feather`
The 8th-grader-facing High School Directory data. **442 schools × 449 columns.** Includes overview paragraph, all admissions programs (up to 12 per school) with seats/applicants/applicants-per-seat ratios, AP courses, language classes, PSAL sports (boys/girls/coed), graduation rate, attendance rate, college-career rate, accessibility status, transit info, etc. **AY 2021 only loaded by default**; the upstream loader supports 2013–2021.

**Caching quirk:** `hs_directory` is the *only* dataset NOT cached on `data.mixi.nyc`. We pull from the NYC Open Data SODA API once and persist locally as feather. `scripts/fetch_data.py` primes this cache; `app.data` falls back to the network if the file is missing.

**`zipcodes` / `neighborhoods`** — `geo.load_zipcodes()` / `load_neighborhoods()`
Boundary polygons (zipcodes) and labeled points (neighborhoods). Loaded for future borough/zone pages; not currently surfaced on the school page.

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

The school page (`/school/{dbn}`) renders each section conditionally on data presence — an elementary school gets ~8 sections, a high school gets ~12.

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

To add one, follow the pattern in CLAUDE.md → "Adding a new operation": loader call in `app/data.py`, Pydantic model in `app/services/models.py`, helper in `app/services/schools.py`, template block in `app/web/templates/school.html`.

## Repo layout

```
.
├── pyproject.toml         # uv-managed; FastAPI + nycschools (editable @ ../nycschools)
├── uv.lock                # committed; reproducible installs
├── .env                   # NYC_SCHOOLS_DATA_DIR=./school-data (gitignored)
├── school-data/           # cached data files, ~150 MB (gitignored)
├── app/
│   ├── main.py            # FastAPI app, lifespan-loaded data
│   ├── config.py          # .env loading, NYC_SCHOOLS_DATA_DIR resolution
│   ├── data.py            # in-memory dataframes (loaded once at startup)
│   ├── services/          # transport-agnostic data-access functions
│   │   ├── models.py      # Pydantic schemas (the cross-surface contract)
│   │   └── schools.py     # search_schools, get_school
│   └── web/               # thin Jinja-rendering adapters
│       ├── routes.py
│       └── templates/     # base, search, school, partials/
├── tests/
│   ├── test_services.py
│   └── test_routes.py
├── scripts/
│   ├── fetch_data.py      # pre-warm ./school-data/ from data.mixi.nyc
│   ├── find_school.py     # name → DBN lookup
│   └── inspect_school.py  # everything we know about one DBN
├── CLAUDE.md              # architecture & repo-boundary policy
└── README.md
```

## Where to put new code

(Repeated from CLAUDE.md, since this is the most-asked question.)

| Goes in `~/Code/nycschools/` | Goes here |
|---|---|
| New data loaders, schema fixes, dataset modules | New service-layer functions over existing data |
| Bug fixes in existing loaders | HTTP / MCP / A2A / ACP route adapters |
| New tests for the data layer | Tests for the service & route layers |
| Documentation for the package | Site templates, frontend, deploy, project notes |

If another data-analysis project could reuse it, it's upstream. Otherwise it's here.

**Adding a new operation:** define it once in `app/services/schools.py` returning a Pydantic model from `app/services/models.py`. It's automatically available to every surface (HTML, JSON API, future MCP/A2A/ACP). Adapters wrap; services compute. Don't import `Request`, `Context`, or other transport types into `services/`.

## License & private state

This project is **AGPL-3.0** (matching upstream `nycschools`). Full text in [LICENSE](./LICENSE).

In practice that means:
- Anyone we share code with — and anyone interacting with a deployed service that runs this code — has the right to obtain the full source, including any modifications. If we deploy a server-side instance (a live API, an admin tool, etc.), it must link to the source.
- A static-build deployment (the path CLAUDE.md prefers) still ships AGPL'd build tooling, but the deployed artifact is just JSON + JS, so the disclosure obligation is satisfied by keeping this repo public.
- Any code that imports from this project (or from `nycschools`) has to be AGPL-compatible. Plan dependencies with that in mind.

**Don't commit anything you wouldn't put in a public repo.** Deploy keys, internal notes, vendor credentials, scratch SQL, etc. should either:
1. Land in `.env`, `secrets/`, `private/`, `notes/`, or any `*.local` file (all gitignored), **or**
2. Live in a sibling private repo (e.g. `~/Code/nyc-report-card-private/`). Cleaner, harder to leak by accident.

## Gotchas

- `urllib3 NotOpenSSLWarning: ... LibreSSL 2.8.3` on macOS — benign, comes from system Python's TLS stack. Ignore.
- `pandas DtypeWarning: Columns (18) have mixed types` from `school-demographics.csv` — also benign; column 18 is `school_type` which has stringy values.
- The first call to a `load_*` function may take a few seconds (HTTP fetch + cache write). Subsequent calls are local-disk fast.
- `school-data/` is gitignored on purpose — don't commit it.
