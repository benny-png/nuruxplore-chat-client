#!/usr/bin/env python3
"""Run ONE full proposal-generation cycle through the DeepSeek agent engine.

The mirror-image of ``run_proposal_demo.py``: the same login, project creation,
source upload, research-profile build (3 nuruxplore credits) and approval — but
the final full-document generation is routed through the DeepSeek multi-agent
graph instead of nuruxplore's queued, ~108-credit engine.

Reads nuruxplore credentials from the gitignored ``.env`` (via Config) and the
DeepInfra key from the ``DEEPINFRA_API_KEY`` environment variable (required).

Usage:
  DEEPINFRA_API_KEY=... NURUXPLORE_AGENTS_ENABLED=1 \
    python scripts/run_agent_demo.py [--topic "..."] [--source ./samples/survey_wellbeing.csv]
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

from nuruxplore.client import NuruXploreClient
from nuruxplore.config import Config
from nuruxplore.errors import ApiError

from agents import run_research
from agents.client import LLMClient
from agents.orchestrator import Ledger


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topic", default=(
        "Impact of mobile money adoption on the financial inclusion of "
        "smallholder farmers in Tanzania"))
    ap.add_argument("--source", default=None,
                    help="source file to upload before building the profile")
    ap.add_argument("--role", choices=["proposal", "dataset", "reference"], default="proposal")
    ap.add_argument("--out", default=None, help="write the generated markdown to this file")
    args = ap.parse_args()

    cfg = Config()
    if not cfg.is_configured:
        print("error: NURUXPLORE_EMAIL / NURUXPLORE_PASSWORD missing in .env", file=sys.stderr)
        return 2
    if not args.source or not pathlib.Path(args.source).is_file():
        print("error: --source file required (e.g. ./samples/survey_wellbeing.csv)", file=sys.stderr)
        return 2

    client = NuruXploreClient.from_credentials(cfg.base_url, cfg.email, cfg.password, timeout=cfg.timeout)
    try:
        login = client.login(cfg.email, cfg.password)
    except ApiError as exc:
        print(f"error: login failed: {exc.message}", file=sys.stderr)
        return 2

    balance = (login.get("user") or {}).get("credits_balance")
    print(f"logged in as {cfg.email} — credits_balance={balance}")
    start = time.monotonic()

    t0 = time.monotonic()
    project_uuid = client.create_project(title=args.topic, type="proposal", auto_title=True)
    print(f"create project: {time.monotonic()-t0:.1f}s  uuid={project_uuid}")

    t0 = time.monotonic()
    up = client.upload_source(project_uuid, args.source, title=args.topic, document_role=args.role, type=args.role)
    print(f"upload source:  {time.monotonic()-t0:.1f}s  id={up.get('source') or up.get('id') or up.get('source_id')}")

    t0 = time.monotonic()
    profile = (client.build_research_profile(project_uuid) or {}).get("profile") or {}
    print(f"build profile:  {time.monotonic()-t0:.1f}s  (+3 CR) title={profile.get('title', '(none)')}")

    t0 = time.monotonic()
    client.approve_research_profile(project_uuid)
    print(f"approve profile: {time.monotonic()-t0:.1f}s (free)")

    # ---- DeepSeek agent generation (the measured stage) ----
    import asyncio

    print("generating via DeepSeek multi-agent graph (no nuruxplore charge)...")
    agent_client = LLMClient()
    ledger = Ledger()
    t0 = time.monotonic()
    async def _go():
        return await run_research(agent_client, ledger, args.topic, profile)
    title, doc, ledger = asyncio.run(_go())
    wall = time.monotonic() - t0

    usage = ledger.summary()
    words = len(doc.split())
    total_wall = time.monotonic() - start

    print("\n=== DEEPSEEK AGENT RESULTS ===")
    print(f"generation wall time:   {wall:.1f}s")
    print(f"generation calls:       {usage['calls']}")
    print(f"aggregate model time:   {usage['duration_s']:.1f}s")
    print(f"tokens in / out:        {usage['tokens_in']} / {usage['tokens_out']}")
    print(f"DeepSeek cost:          ~${usage['cost_est']:.5f}")
    print(f"output words:           {words}")
    print(f"steps:                  {[s['name'] for s in usage['steps']]}")
    print(f"nuruxplore credits spent in this run: 3 (profile) — generation was free")
    print(f"total run wall time (incl. profile):  {total_wall:.1f}s")

    if args.out:
        outdir = pathlib.Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        dest = outdir / "proposal_agent.md"
        dest.write_text(f"# {title}\n\n{doc}", encoding="utf-8")
        print(f"saved -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
