"""
Zero-dependency `.env` loader for AegisSRE.

Automatically loads environment variables from:
1. Project root `.env` (`c:/Users/gopup/bengaluru hackathon/.env`)
2. User home `~/.env` (fallback)

Never overwrites existing non-empty environment variables unless `override=True`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parent
PROJECT_ENV_PATH = PROJECT_ROOT / ".env"
HOME_ENV_PATH = Path.home() / ".env"


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                values[key] = val
    except OSError:
        pass
    return values


def load_env_files(override: bool = False) -> Dict[str, bool]:
    """Loads `.env` files into `os.environ` and returns presence flags (never values)."""
    for env_path in (PROJECT_ENV_PATH, HOME_ENV_PATH):
        parsed = parse_env_file(env_path)
        for k, v in parsed.items():
            if v and (override or not os.environ.get(k)):
                os.environ[k] = v

    return {
        "has_groq_api_key": bool(os.environ.get("GROQ_API_KEY", "").strip()),
        "has_hindsight_api_key": bool(os.environ.get("HINDSIGHT_API_KEY", "").strip()),
        "env_file_exists": PROJECT_ENV_PATH.is_file(),
    }


def save_keys_to_project_env(
    groq_api_key: str | None = None,
    hindsight_api_key: str | None = None,
    hindsight_base_url: str | None = None,
) -> Dict[str, bool]:
    """Safely updates `.env` in the project root (gitignored) and reloads `os.environ`."""
    existing = parse_env_file(PROJECT_ENV_PATH)

    if groq_api_key is not None and groq_api_key.strip():
        existing["GROQ_API_KEY"] = groq_api_key.strip()
    if hindsight_api_key is not None and hindsight_api_key.strip():
        existing["HINDSIGHT_API_KEY"] = hindsight_api_key.strip()
    if hindsight_base_url is not None and hindsight_base_url.strip():
        existing["HINDSIGHT_BASE_URL"] = hindsight_base_url.strip()

    existing.setdefault("HINDSIGHT_BASE_URL", "https://api.hindsight.vectorize.io")
    existing.setdefault("GROQ_API_KEY", "")
    existing.setdefault("HINDSIGHT_API_KEY", "")

    lines = [
        "# Local AegisSRE Environment Secrets (Git-Ignored — Never Committed to GitHub)",
        f"GROQ_API_KEY={existing.get('GROQ_API_KEY', '')}",
        f"HINDSIGHT_BASE_URL={existing.get('HINDSIGHT_BASE_URL', 'https://api.hindsight.vectorize.io')}",
        f"HINDSIGHT_API_KEY={existing.get('HINDSIGHT_API_KEY', '')}",
        "",
    ]
    PROJECT_ENV_PATH.write_text("\n".join(lines), encoding="utf-8")
    return load_env_files(override=True)
