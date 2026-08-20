"""DeepSeek multi-agent orchestration framework (toggleable).

Public surface the FastAPI app uses:

* :func:`available`      — is the agent layer on AND configured (key present)?
* :func:`chat_reply`     — multi-agent chat reply (prompt-chain).
* :func:`run_research`   — multi-agent full-document generation (workers).
* :func:`LLMClient`      — low-level DeepSeek client (for tests/demos).
* :func:`Ledger`         — token/time accounting, surfaced to the UI.

When :func:`available` is False the app falls back to today's nuruxplore proxy
byte-for-byte — the agent layer is strictly an opt-in on top.
"""

from __future__ import annotations

from . import config
from .client import LLMClient
from .graphs import chat_reply, run_research
from .orchestrator import Ledger, prompt_chain, run_parallel, evaluator_optimizer

__all__ = [
    "available",
    "chat_reply",
    "run_research",
    "LLMClient",
    "Ledger",
    "prompt_chain",
    "run_parallel",
    "evaluator_optimizer",
]


def available() -> bool:
    return config.enabled()


def model_name() -> str:
    return config.model()
