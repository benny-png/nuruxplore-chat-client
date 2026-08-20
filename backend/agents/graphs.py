"""Concrete multi-agent graph recipes built on the orchestration primitives.

These are the clean, consistent integration points that wrap the nuruxplore-like
data (a chat project's message history; an approved research profile + topic) in
an agentic DeepSeek workflow.

* :func:`chat_reply`   — prompt-chain: retrieve relevant context, then draft a
                         reply. Two bounded flash calls -> chat stays snappy.
* :func:`run_research` — orchestrator-workers: a lead plans a <=3 section
                         outline, section writers draft in parallel, a composer
                         merges them into one coherent document. ~6 flash calls,
                         no loops.

Both return ``(result, ledger)`` so the API layer can surface the exact
tokens/time spent (the cost chip in the UI).
"""

from __future__ import annotations

import asyncio
import json
import re

from . import config
from . import agents as A
from .client import LLMClient
from .orchestrator import Ledger, run_parallel


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of an LLM reply (tolerates prose/fences)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


async def _complete(client: LLMClient, ledger: Ledger, name: str, agent: A.Agent, prompt: str) -> str:
    """Run one agent's system prompt against the user text, off the event loop."""
    call = await asyncio.to_thread(
        client.complete,
        agent.system,
        prompt,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
    )
    ledger.add(name, call)
    return call.text


# ------------------------------------------------------------------ chat


async def chat_reply(
    client: LLMClient, ledger: Ledger, user_message: str, prior: list | None = None
) -> tuple[str, Ledger]:
    """Prompt-chain chat reply: retrieve relevant context, then draft.

    ``prior`` is a list of ``{"role", "content"}`` messages (usually the project's
    recent history). When there's no history we skip retrieval and save a call.
    """
    prior = prior or []
    transcript = "\n".join(f"[{m.get('role', 'user')}] {m.get('content', '')}" for m in prior[-8:])

    if prior:
        retr_prompt = (
            "Below is the recent conversation and the user's new message. Output the "
            "snippets most relevant to answering the new message, verbatim and trimmed. "
            "Return 'none' if nothing is needed.\n\n--- conversation ---\n"
            f"{transcript}\n--- new message ---\n{user_message}"
        )
        ctx = await _complete(client, ledger, "retrieve", A.RETRIEVER, retr_prompt)
        ctx = "" if ctx.strip().lower() == "none" else ctx
    else:
        ctx = ""

    draft_prompt = f"{user_message}\n\nRelevant context:\n{ctx or 'none'}"
    reply = await _complete(client, ledger, "draft", A.DRAFTER, draft_prompt)
    return reply, ledger


# ------------------------------------------------------------------ research


async def run_research(
    client: LLMClient,
    ledger: Ledger,
    topic: str,
    profile: dict,
) -> tuple[str, str, Ledger]:
    """Orchestrator-workers: plan -> parallel section drafts -> compose.

    Returns ``(title, document_markdown, ledger)``. Bounded to <=3 sections and a
    single compose pass — predictable credit spend and latency.
    """
    profile_block = json.dumps(profile, default=str)[:2500]

    # 1) title (cheap, single call)
    title = await _complete(
        client,
        ledger,
        "doc-title",
        A.DOC_TITLE,
        f"Topic:\n{topic}\n\nProfile summary:\n{profile_block}",
    )
    title = title or "Research Document"

    # 2) planning lead -> split into section jobs
    plan_prompt = (
        f"Topic:\n{topic}\n\nApproved research profile:\n{profile_block}\n\n"
        "Plan the outline (max 3 sections) as JSON: "
        '{"title": str, "sections": [{"title": str, "brief": str}]}.'
    )
    plan_text = await _complete(client, ledger, "scope-lead", A.SCOPE_LEAD, plan_prompt)

    def _jobs_from_plan(text: str) -> list[tuple[str, tuple]]:
        parsed = _extract_json(text)
        sections = (parsed or {}).get("sections") or []
        jobs = []
        for i, sec in enumerate(sections[: config.max_workers()]):
            sec_title = sec.get("title", f"Section {i + 1}")
            brief = sec.get("brief", "")
            prompt = (
                f"Topic: {topic}\n\nApproved research profile:\n{profile_block}\n\n"
                f"Your section: {sec_title}\nBrief: {brief}\n\n"
                "Write this section in markdown, grounded in the profile above. "
                "Never invent citations or data."
            )
            jobs.append(
                (
                    f"section {sec_title}",
                    (A.SECTION_WRITER.system, prompt,
                     {"max_tokens": A.SECTION_WRITER.max_tokens,
                      "temperature": A.SECTION_WRITER.temperature}),
                )
            )
        return jobs

    # 3) workers write sections in parallel; composer merges
    jobs = _jobs_from_plan(plan_text)
    sections = await run_parallel(
        client, ledger, jobs, concurrency=config.max_workers()
    )

    def _merge(body: str):
        prompt = (
            f"Title: {title}\n\nMerge these drafted sections into one coherent academic "
            "document in markdown — continuous numbering, brief intro + conclusion, "
            "remove duplication. Keep all substance.\n\nSections:\n{body}"
        )
        return _complete(client, ledger, "compose", A.COMPOSER, prompt)

    body = "\n\n---\n\n".join(sections if sections else ["<empty>"])
    doc = await _merge(body)
    return title, doc, ledger
