"""Process-wide settings."""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# Committed working set the running app reads from. Built by scripts/build_db.py.
COMMITTED_DATA_DIR = REPO_ROOT / "data"
DB_PATH = COMMITTED_DATA_DIR / "data.sqlite"

# The upstream/raw cache used only by the build scripts; gitignored, never
# touched by the running app. Kept here for build_db / fetch_data convenience.
RAW_DATA_DIR = Path(os.environ.get("NYC_SCHOOLS_DATA_DIR") or REPO_ROOT / "school-data").resolve()

TEMPLATES_DIR = REPO_ROOT / "app" / "web" / "templates"
