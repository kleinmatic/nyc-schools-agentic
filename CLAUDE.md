# NYC Schools Report Card

A parent-facing website that produces a "report card" for any NYC public school.

## Architecture

This project is a consumer of the upstream **`nycschools`** Python package, which lives at `~/Code/nycschools/` (Matthew X. Curinga / Adelphi Ed Tech, AGPL). That repo is treated as **read-only / upstream-track**: any code we write that belongs in the data layer goes there as a PR back. Everything else — the report-card assembler, API, frontend, deployment — lives here.

## Repo boundaries

| Goes in `~/Code/nycschools/` | Goes here (`~/Code/nyc-report-card/`) |
|---|---|
| New data loaders, schema fixes, new dataset modules | Per-DBN report assembler |
| Bug fixes in existing loaders | API server / static JSON build |
| New tests for the data layer | Frontend (school search, report page) |
| Documentation for the package | Deployment, infra, project notes |

When in doubt: if a parent visiting the site would never see it, and another data-analysis project could reuse it, it's upstream. Otherwise it's here.

## How to read the upstream package

Key entry points in `~/Code/nycschools/nycschools/`:
- `schools.load_school_demographics()` — demographics by DBN/year (race %, ELL, SWD, poverty, ENI, enrollment)
- `schools.search(df, qry)` — fuzzy school lookup by name / short-name / DBN
- `exams.load_math()` / `load_ela()` — grades 3-8 state tests
- `class_size.load_class_size()` / `load_ptr()` — class size + pupil:teacher ratio
- `snapshot.load_snapshots()` — DOE official snapshot (attendance, chronic absence, principal, admissions method, quality review)
- `geo.load_*` — locations, district/zip/neighborhood boundaries
- `schools.load_hs_directory(ay)` — HS programs and admissions criteria
- Data root: env var `NYC_SCHOOLS_DATA_DIR`; bulk archive URL is in `nycschools/datasets.py` under `school-data-archive`

## Conventions

- **DBN is the primary key** for everything. Site URLs should use it (e.g. `/school/01M015`).
- The underlying data is annual, not live. Prefer **build-time static JSON per DBN** over a live API unless we have a reason to go dynamic.
- Don't fork the upstream package — install it (`pip install -e ~/Code/nycschools` for dev) and import.
