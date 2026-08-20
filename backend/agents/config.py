"""Agent-framework configuration, read from the environment.

Nothing secret is committed to git — the DeepInfra key comes from the server's
``.env`` (docker-compose ``env_file``) at runtime. We read it lazily via
:func:`enabled` so a server without the key simply keeps today's (nuruxplore)
behavior instead of crashing.

The agent layer is a *toggle*: ``NURUXPLORE_AGENTS_ENABLED=1`` opts chat and
research into the DeepSeek multi-agent orchestration; unset/``0`` keeps the
existing thin-proxy path byte-identical.
"""

from __future__ import annotations

import os

DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
DEFAULT_BASE_URL = "https://api.deepinfra.com/v1/openai"


def _flag(name: str) -> bool:
    val = os.environ.get(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def api_key() -> str | None:
    """The DeepInfra API key, or ``None`` when not configured."""
    return os.environ.get("DEEPINFRA_API_KEY") or None


def base_url() -> str:
    return os.environ.get("DEEPINFRA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def model() -> str:
    return os.environ.get("DEEPINFRA_MODEL", DEFAULT_MODEL)


def enabled() -> bool:
    """Master toggle: are agent orchestration and DeepInfra available?"""
    return _flag("NURUXPLORE_AGENTS_ENABLED") and api_key() is not None


def max_workers() -> int:
    """Cap on parallel worker fan-out (bounds both time and tokens)."""
    return max(1, int(os.environ.get("NURUXPLORE_AGENTS_MAX_WORKERS", "3")))


def optimizer_rounds() -> int:
    """Bounded 'evaluator-optimizer' regeneration rounds (cap on spend)."""
    return max(1, int(os.environ.get("NURUXPLORE_AGENTS_MAX_ROUNDS", "2")))


def timeout() -> float:
    return float(os.environ.get("DEEPINFRA_TIMEOUT", "60"))
