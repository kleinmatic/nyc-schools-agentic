"""Process-wide settings. Loads .env and resolves NYC_SCHOOLS_DATA_DIR."""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# Default data dir to ./school-data relative to repo root, regardless of cwd.
DATA_DIR = Path(os.environ.get("NYC_SCHOOLS_DATA_DIR") or REPO_ROOT / "school-data").resolve()
os.environ["NYC_SCHOOLS_DATA_DIR"] = str(DATA_DIR)

TEMPLATES_DIR = REPO_ROOT / "app" / "web" / "templates"
