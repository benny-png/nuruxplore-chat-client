"""Load configuration from a local ``.env`` file and/or environment variables.

We deliberately keep credentials out of source control. The convention here:
copy ``.env.example`` to ``.env`` and fill in real values; ``.env`` is
gitignored. Environment variables take precedence over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULTS = {
    # Host only (no path): the documented endpoints already carry the /api prefix,
    # e.g. /api/auth/login. Setting the base here with a trailing "/api" would
    # produce a doubled /api/api/... and 404.
    "NURUXPLORE_BASE_URL": "https://nuruxplore.com",
    "NURUXPLORE_TIMEOUT": "30",
}


def _dotenv_path() -> Path:
    # Look for .env next to the project root (two levels up from this file).
    return Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` lines from ``.env`` into the process environment.

    Only sets keys that aren't already present so real env vars always win.
    Lines are simple ``KEY=VALUE`` (no quoting/expansion) which is more than
    enough for this client.
    """
    path = path or _dotenv_path()
    if not path.is_file():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


class Config:
    """Typed view of the client's configuration."""

    def __init__(self, **overrides: str | None):
        load_env_file()  # no-op if a caller passed values some other way
        self.base_url = (
            overrides.get("base_url")
            or os.environ.get("NURUXPLORE_BASE_URL")
            or DEFAULTS["NURUXPLORE_BASE_URL"]
        ).rstrip("/")
        self.email = overrides.get("email") or os.environ.get("NURUXPLORE_EMAIL")
        self.password = overrides.get("password") or os.environ.get("NURUXPLORE_PASSWORD")
        try:
            self.timeout = float(
                overrides.get("timeout")
                or os.environ.get("NURUXPLORE_TIMEOUT")
                or DEFAULTS["NURUXPLORE_TIMEOUT"]
            )
        except ValueError:
            self.timeout = 30.0

    @property
    def is_configured(self) -> bool:
        return bool(self.email and self.password)
