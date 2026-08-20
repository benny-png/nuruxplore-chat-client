"""Agent definitions for the DeepSeek multi-agent framework.

An :class:`Agent` is a small, single-purpose flash-model role: a name, a role,
a system prompt and per-call limits. Agents stay cheap by design — narrow
scope, bounded ``max_tokens`` — so orchestrating several of them costs far less
than one monolithic generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config


@dataclass
class Agent:
    name: str
    role: str
    system: str
    model: str = field(default_factory=config.model)
    temperature: float = 0.4
    max_tokens: int = 2048

    def spec(self) -> str:
        return f"{self.name} ({self.role})"


# ------------------------------------------------------------------ chat roles
PLANNER = Agent(
    name="planner",
    role="read the request, pick the cheapest sufficient plan",
    system=(
        "You are the orchestrator in a multi-agent research system. Given the user's "
        "message and available context, decide the minimal set of steps needed to answer "
        "well. Be economical: produce only the steps that are strictly necessary. "
        "Return a short numbered plan."
    ),
    max_tokens=512,
)

RETRIEVER = Agent(
    name="retriever",
    role="gather relevant prior context for the reply",
    system=(
        "You select the snippets of prior conversation and research context that are most "
        "relevant to the current user message. Output them verbatim, trimmed, in a compact "
        "block. Return 'none' if nothing is needed."
    ),
    max_tokens=1024,
)

DRAFTER = Agent(
    name="drafter",
    role="write the final assistant reply",
    system=(
        "You are a helpful research assistant. Write a clear, accurate, well-structured "
        "reply to the user's latest message, using the retrieved context. Match the tone "
        "of an AI academic writing assistant. Do not mention the retrieval step."
    ),
    max_tokens=2048,
)

# ------------------------------------------------------------------ research roles

SCOPE_LEAD = Agent(
    name="scope-lead",
    role="plan and split the research document into full-length chapters",
    system=(
        "You are the planning lead of a multi-agent research writer. From the user's topic "
        "and their approved research profile, produce a complete, publishable academic "
        "document outline of 5 to 6 chapters (e.g. Introduction/Background, Problem & "
        "Objectives, Literature Review, Methodology, Findings/Expected Results, "
        "Discussion/Significance/Timeline). For each chapter give a title and a 3-4 sentence "
        "brief stating exactly what to cover, so a writer can produce a substantial, "
        "rigorous chapter from it. Return the outline as JSON: {\"title\": str, \"sections\": "
        "[{\"title\": str, \"brief\": str}]}. No padding — every chapter must add substance."
    ),
    max_tokens=1024,
)

SECTION_WRITER = Agent(
    name="section-writer",
    role="draft one full chapter of the document",
    system=(
        "You are a chapter writer in a multi-agent research team. From the given topic, "
        "research profile and your chapter brief, write a substantial, academically rigorous "
        "chapter in markdown with headers and flowing, detailed prose — aim for a full-length "
        "chapter, not a summary. Ground everything in the provided profile/context; never "
        "invent citations or data."
    ),
    max_tokens=8192,
)

COMPOSER = Agent(
    name="editor",
    role="write front & back matter to frame the drafted full-length chapters",
    system=(
        "You are the editor of a multi-agent research document. The chapter writers have "
        "already produced full-length chapters. From the title and the chapter plan, write "
        "only the editorial frame: a front matter (a strong abstract + a short "
        "introduction that sets up the document) and a back matter (a conclusion that "
        "synthesizes, plus a references section using only the cited works). Do NOT rewrite "
        "or reproduce the chapter bodies. Return exactly JSON: {\"front_matter\": str, "
        "\"back_matter\": str} using markdown."
    ),
    max_tokens=2048,
)

REVIEWER = Agent(
    name="reviewer",
    role="critique the draft against the brief and flag concrete fixes",
    system=(
        "You are a rigorous editor. Critique the drafted document against the research "
        "profile and topic. List only concrete, actionable problems (max 5) — clarity, "
        "completeness, grounding, internal consistency. If the draft meets the brief, "
        "return exactly: APPROVED."
    ),
    max_tokens=1024,
)

DOC_TITLE = Agent(
    name="doc-title",
    role="propose a clear academic title from the topic and profile",
    system=(
        "From the research topic and profile, propose one concise, academic document title. "
        "Return only the title text."
    ),
    max_tokens=120,
)
