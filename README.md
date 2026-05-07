# NYC Schools Report Card

Parent-facing site that produces a "report card" for any NYC public school, keyed by **DBN** (e.g. `15K321`).

This repo is the **app** (per-school assembler, future API, frontend, deployment). It consumes the upstream [`nycschools`](https://github.com/adelphi-ed-tech/nycschools) Python package as a read-only data layer. See [CLAUDE.md](./CLAUDE.md) for the full repo-boundary policy.

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

# 3. Pre-warm the local data cache (~150 MB, a couple of minutes)
uv run scripts/fetch_data.py
```

That's it. After step 3, `./school-data/` contains nine datasets covering demographics, the DOE school snapshot, 3-8 ELA & math exams, class size, pupil:teacher ratio, school locations, zipcodes, and neighborhoods.

The `.env` file (gitignored) sets `NYC_SCHOOLS_DATA_DIR=./school-data`. If you want to use the data from a different working directory, set the env var explicitly:

```bash
export NYC_SCHOOLS_DATA_DIR=$(pwd)/school-data
```

### Why we don't use upstream's `python -m nycschools.dataloader -d`

Upstream's bootstrap downloads a single `.7z` archive from a Google Drive link. **That link is dead** as of 2026 and returns a 404 HTML page (which the loader tries to unpack as 7z and crashes with `Bad7zFile: not a 7z file`).

We bypass it. The package's own `dataloader.load()` already has a per-file fallback to `https://data.mixi.nyc/<filename>` — that host is alive and serves all the cleaned files individually. `scripts/fetch_data.py` just calls each loader once to pre-warm the cache via that fallback.

## Exploring the data

Two CLIs:

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

### What each loader gives you

| Loader | Rows | Keyed by | Notable fields |
|---|---|---|---|
| `schools.load_school_demographics()` | ~32k | dbn × ay | enrollment by grade, race %, ELL %, SWD %, poverty %, ENI, zip |
| `snapshot.load_snapshots()` | ~7.7k | dbn (latest) | attendance, chronic absence, principal, admissions method, quality review |
| `exams.load_ela()` / `load_math()` | ~590k each | dbn × grade × category × ay | mean scale score, % proficient, broken out by demographic category |
| `class_size.load_class_size()` | ~45k | dbn × grade × subject × ay | avg class size |
| `class_size.load_ptr()` | ~1.5k | dbn × ay | pupil:teacher ratio |
| `geo.load_school_locations()` | ~1.95k | dbn (latest) | lat/lon, address, building code |
| `geo.load_zipcodes()`, `load_neighborhoods()` | — | — | borough/neighborhood polygons |

The year column is **`ay`** = academic year start, integer (e.g. `2024` means 2024-25). Latest cached year is currently 2024.

## Repo layout

```
.
├── pyproject.toml          # uv-managed; nycschools is the only declared dep
├── uv.lock                 # committed; reproducible installs
├── .env                    # NYC_SCHOOLS_DATA_DIR=./school-data (gitignored)
├── school-data/            # cached data files, 150ish MB (gitignored)
├── scripts/
│   ├── fetch_data.py       # pre-warm ./school-data/ from data.mixi.nyc
│   ├── find_school.py      # name → DBN lookup
│   └── inspect_school.py   # everything we know about one DBN
├── CLAUDE.md               # architecture & repo-boundary policy
└── README.md
```

## Where to put new code

(Repeated from CLAUDE.md, since this is the most-asked question.)

| Goes in `~/Code/nycschools/` | Goes here |
|---|---|
| New data loaders, schema fixes, dataset modules | Per-DBN report assembler |
| Bug fixes in existing loaders | API server / static JSON build |
| New tests for the data layer | Frontend (school search, report page) |
| Documentation for the package | Deployment, infra, project notes |

If a parent visiting the site would never see it, and another data-analysis project could reuse it, it's upstream. Otherwise it's here.

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
