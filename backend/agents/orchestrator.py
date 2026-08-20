"""Orchestration engine: compose small DeepSeek agents into a coherent run.

Implements four workflow primitives from the "building effective agents"
playbook, all executed against a single :class:`LLMClient` and tracked on a
shared cost/time ledger:

* :func:`prompt_chain`        — sequential steps, each fed the previous output.
* :func:`run_parallel`        — fan out N jobs concurrently, merge results.
* :func:`orchestrator_workers`— a lead plans + workers run in parallel + a lead
                               composes.
* :func:`evaluator_optimizer` — generate, critique, regenerate (capped rounds).

Everything is bounded ([config.max_workers()] parallel, [optimizer_rounds()]
regen rounds) so spend and latency never run away — the user's standing
constraint is that tokens AND time both matter.

Primitives are ``async`` so they can be awaited directly from FastAPI routes;
the DeepSeek calls themselves are run off the event loop via
``asyncio.to_thread`` so the server stays responsive while a generation runs.
A :func:`run` helper lets sync tests drive a graph with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import config
from .client import LLMClient


@dataclass
class Ledger:
    """Cumulative token/time accounting for one orchestration run."""

    steps: list[dict] = field(default_factory=list)

    def add(self, name: str, call, extra: dict | None = None) -> None:
        self.steps.append(
            {
                "name": name,
                "model": call.model,
                "in_tokens": call.in_tokens,
                "out_tokens": call.out_tokens,
                "cost_est": call.cost_est,
                "duration_s": call.duration_s,
                **(extra or {}),
            }
        )

    def summary(self) -> dict:
        in_tok = sum(s["in_tokens"] for s in self.steps)
        out_tok = sum(s["out_tokens"] for s in self.steps)
        return {
            "calls": len(self.steps),
            "tokens_in": in_tok,
            "tokens_out": out_tok,
            "cost_est": round(sum(s["cost_est"] for s in self.steps), 6),
            "duration_s": round(sum(s["duration_s"] for s in self.steps), 2),
            "steps": [{"name": s["name"], "duration_s": s["duration_s"]} for s in self.steps],
        }


# A job is an LLM call spec (system, user, kwargs-dict) or a plain callable.
async def _run_job(client: LLMClient, ledger: Ledger, name: str, job) -> str:
    if isinstance(job, tuple):
        system, user, kwargs = job
        call = await asyncio.to_thread(client.complete, system, user, **kwargs)
        ledger.add(name, call)
        return call.text
    # A plain callable that makes its own client calls.
    return await asyncio.to_thread(job)


async def prompt_chain(client: LLMClient, ledger: Ledger, steps: list) -> str:
    """Run ``steps`` sequentially, threading the previous output into the next.

    Each step is either an LLM call spec ``(system, user, kwargs)`` whose ``user``
    may reference ``{prev}``, or a callable ``(prev_text) -> str``.
    """
    prev = ""
    for i, step in enumerate(steps):
        if isinstance(step, tuple):
            system, user, kwargs = step
            user = user.format(prev=prev)
            call = await asyncio.to_thread(
                client.complete, system, user, **(kwargs or {})
            )
            ledger.add(f"step{i}", call)
            prev = call.text
        else:
            prev = step(prev)
    return prev


async def run_parallel(
    client: LLMClient,
    ledger: Ledger,
    jobs: list[tuple[str, object]],
    concurrency: int | None = None,
) -> list[str]:
    """Run each ``(name, job)`` concurrently (bounded); results keep input order."""
    concurrency = concurrency or config.max_workers()
    results: dict[int, str] = {}
    sem = asyncio.Semaphore(concurrency)

    async def bound(idx, name, job):
        async with sem:
            results[idx] = await _run_job(client, ledger, name, job)

    await asyncio.gather(*(bound(i, n, j) for i, (n, j) in enumerate(jobs)))
    return [results[i] for i in range(len(jobs))]


async def orchestrator_workers(
    client: LLMClient,
    ledger: Ledger,
    *,
    plan_spec: tuple,
    split_fn: Callable,
    work_fn: Callable,
    merge_fn: Callable,
    concurrency: int | None = None,
) -> str:
    """Lead plans -> workers run in parallel -> composer merges.

    ``plan_spec`` is ``(system, user, kwargs)`` for the planning lead; it returns
    a plan that :func:`split_fn` turns into a list of ``(name, spec)`` jobs;
    :func:`work_fn`` is optional (defaults to identity); workers run in parallel
    under :func:`run_parallel`; :func:`merge_fn(sections)`` produces the final
    text.
    """
    plan_text = await prompt_chain(client, ledger, [plan_spec])
    plan = split_fn(plan_text)
    jobs = [(name, (work_fn(spec) if work_fn else spec)) for name, spec in plan]
    sections = await run_parallel(client, ledger, jobs, concurrency=concurrency)
    return merge_fn(sections)


async def evaluator_optimizer(
    client: LLMClient,
    ledger: Ledger,
    *,
    generate_fn: Callable[[str | None], Awaitable[str]],
    evaluate_fn: Callable[[str], Awaitable[str]],
    max_rounds: int | None = None,
) -> str:
    """Generate, critique, regenerate up to ``max_rounds`` times, then return.

    The evaluator returns ``APPROVED`` to stop early, or a list of concrete fixes
    to feed the next regen. Rounds are capped to bound token spend.
    """
    rounds = max_rounds or config.optimizer_rounds()
    doc = await generate_fn(None)
    for _ in range(rounds):
        verdict = await evaluate_fn(doc)
        if verdict.strip().upper() == "APPROVED":
            break
        doc = await generate_fn(verdict)
    return doc


def run(awaitable: Awaitable[str]) -> str:
    """Drive an async graph from a sync (test) context."""
    return asyncio.run(awaitable)
