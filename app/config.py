from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", APP_ROOT / "data"))
DB_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "feedforge.db"))
CAMPAIGNS_DIR = Path(os.getenv("CAMPAIGNS_DIR", DATA_DIR / "campaigns"))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/owl-alpha")
OPENROUTER_TIMEOUT_SECONDS = float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "45"))
OPENROUTER_MAX_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "2200"))

DEFAULT_CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
DEFAULT_CORS_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1):\d+"


def parse_csv_env(name: str, fallback: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return fallback
    return [item.strip() for item in value.split(",") if item.strip()]
