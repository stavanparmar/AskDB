"""Application configuration loaded from the environment and local .env file."""

import os
from pathlib import Path


def _load_env_file() -> None:
    """Load simple KEY=VALUE pairs without overriding existing environment values."""
    env_file = Path(__file__).with_name(".env")
    if not env_file.is_file():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", maxsplit=1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip("\"'"))


_load_env_file()

API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL_NAME = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
