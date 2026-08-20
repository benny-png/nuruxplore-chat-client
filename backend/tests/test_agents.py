"""Unit tests for the DeepSeek multi-agent framework — no network, no credits.

A fake LLM client returns deterministic text so we can assert that the
orchestration *control flow* (prompt-chain, parallel fan-out, worker plan/split,
evaluator-optimizer round caps) and the cost/time ledger behave correctly.

The async graph primitives are driven through :func:`_run` (``asyncio.run``) so
the tests need no async plugin.
"""

from __future__ import annotations

import asyncio

from agents.agents import DRAFTER, SCOPE_LEAD
from agents.client import Call
from agents.orchestrator import Ledger, evaluator_optimizer, orchestrator_workers, prompt_chain, run_parallel
from agents.graphs import _extract_json, _extract_references, chat_reply, run_research


def _run(coro):
    return asyncio.run(coro)


class FakeClient:
    """Returns a line per call so each step is distinguishable."""

    model = "fake-flash"

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def complete(self, system, user, **kwargs):
        self.calls.append((system, user))
        text = self.responses.get(system[:24]) or f"R[{len(self.calls)}]"
        return Call(text=text, model=self.model, in_tokens=7, out_tokens=3, duration_s=0.01)


def test_prompt_chain_threads_previous_output():
    client = FakeClient()
    ledger = Ledger()
    _run(prompt_chain(client, ledger, [(DRAFTER.system, "seed {prev}", {"max_tokens": 40})]))
    assert client.calls[0][1] == "seed "  # {prev} replaced with '' on first step
    assert ledger.summary()["calls"] == 1


def test_run_parallel_is_ordered_and_accounted():
    client = FakeClient()
    ledger = Ledger()
    jobs = [("a", (DRAFTER.system, "A", {})), ("b", (DRAFTER.system, "B", {})), ("c", (DRAFTER.system, "C", {}))]
    out = _run(run_parallel(client, ledger, jobs, concurrency=2))
    assert len(out) == 3
    assert ledger.summary()["calls"] == 3


def test_orchestrator_workers_plan_split_merge():
    client = FakeClient(
        responses={
            SCOPE_LEAD.system[:24]: '{"sections": [{"title": "S1", "brief": "b1"}, {"title": "S2", "brief": "b2"}]}'
        }
    )
    ledger = Ledger()

    def split(text):
        import json

        secs = json.loads(text)["sections"]
        return [(s["title"], (DRAFTER.system, s["brief"], {})) for s in secs]

    def merge(sections):
        return "|".join(sections)

    plan_spec = (SCOPE_LEAD.system, "plan", {"max_tokens": 40})
    out = _run(
        orchestrator_workers(client, ledger, plan_spec=plan_spec, split_fn=split, work_fn=None, merge_fn=merge)
    )
    assert "|" in out
    assert ledger.summary()["calls"] == 3  # 1 planner + 2 workers


def test_evaluator_optimizer_stops_on_approval():
    ledger = Ledger()
    seen = []

    async def generate(fix):
        seen.append(fix)
        return "final"

    async def evaluate(doc):
        return "APPROVED"

    _run(evaluator_optimizer(FakeClient(), ledger, generate_fn=generate, evaluate_fn=evaluate, max_rounds=5))
    assert len(seen) == 1  # no regen after approval


def test_evaluator_optimizer_bounds_rounds():
    ledger = Ledger()
    seen = []

    async def generate(fix):
        seen.append(fix)
        return "draft"

    async def evaluate(doc):
        return "needs work"

    _run(evaluator_optimizer(FakeClient(), ledger, generate_fn=generate, evaluate_fn=evaluate, max_rounds=2))
    # initial + 2 regens capped
    assert len(seen) <= 3


def test_extract_references_from_profile():
    refs = _extract_references({
        "title": "x",
        "references": [
            {"title": "Chen, X., & Zhang, Y. (2024). Generative AI and student learning."},
            {"title": "Wiley, D. (2023). Academic integrity in the age of generative AI."},
        ],
    })
    assert len(refs) == 2
    assert "Chen" in refs[0]
    # never returns fabricated data when there are no references
    assert _extract_references({"title": "no refs here"}) == []


def test_extract_json_tolerates_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('prefix {"b": 2} suffix') == {"b": 2}
    assert _extract_json("no json") is None


def test_chat_reply_skips_retrieval_without_history():
    client = FakeClient()
    ledger = Ledger()
    reply, _ = _run(chat_reply(client, ledger, "hello"))
    assert reply
    assert ledger.summary()["calls"] == 1  # no prior -> just the draft call


def test_run_research_composes():
    client = FakeClient(
        responses={
            SCOPE_LEAD.system[:24]: '{"title":"T","sections":[{"title":"A","brief":"a"},{"title":"B","brief":"b"}]}'
        }
    )
    ledger = Ledger()
    title, doc, out_ledger = _run(run_research(client, ledger, "topic", {"title": "profile"}))
    assert title or doc
    assert doc
    # title + plan + 2 workers + compose
    assert out_ledger.summary()["calls"] >= 5
