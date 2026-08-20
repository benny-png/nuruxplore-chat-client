#!/usr/bin/env python3
"""Run one full proposal-generation cycle against the live NuruXplore API.

Reads credentials from the gitignored ``.env`` (via the client ``Config``).
Steps: create a proposal project -> build research profile -> approve as-is ->
generate outline -> generate-complete (the ~100-credit charge, billed up front)
-> poll generation-status to completion -> export PDF + Word.

Safety rails for a fixed, non-refillable credit budget:
  * ``--check-only`` just logs in and prints the live credit balance (free).
  * Before the expensive call we refuse to proceed if the balance is below the
    documented proposal cost, so we never spend against an empty account.
  * ``--no-generate`` stops after the outline (only ~8 credits) as a dry run.
  * No secrets are ever printed; the bearer token stays in memory.

Usage:
  python scripts/run_proposal_demo.py --check-only
  python scripts/run_proposal_demo.py --no-generate
  python scripts/run_proposal_demo.py --yes --out ./exports
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
import urllib.request

from nuruxplore.client import NuruXploreClient
from nuruxplore.config import Config
from nuruxplore.errors import ApiError

POLL_INTERVAL = 3.0
POLL_MAX = 300          # ~15 minute cap so we never poll forever
PROPOSAL_MIN_COST = 108  # documented cost of one proposal cycle


def poll_until_done(client: NuruXploreClient, project_uuid: str) -> dict:
    for attempt in range(1, POLL_MAX + 1):
        body = client.generation_status(project_uuid)
        status = body.get("status")
        print(f"  poll#{attempt:>3} status={status} "
              f"progress={body.get('progress')} step={body.get('current_step')}")
        if status == "completed":
            return body
        if status == "failed":
            raise RuntimeError(f"Generation job failed: {body}")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError("Timed out polling generation status.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topic", default=(
        "Impact of mobile money adoption on the financial inclusion of "
        "smallholder farmers in Tanzania"))
    ap.add_argument("--check-only", action="store_true",
                    help="log in and print the credit balance, then stop (free)")
    ap.add_argument("--yes", action="store_true",
                    help="proceed with the expensive generate call (still balance-guarded)")
    ap.add_argument("--no-generate", action="store_true",
                    help="stop after the outline (dry run, ~8 credits)")
    ap.add_argument("--out", default=None,
                    help="directory to download the exported files into")
    ap.add_argument("--source", default=None,
                    help="source file (PDF/DOCX/TXT/CSV/XLSX) to upload before building "
                         "the profile (the live API refuses to build a profile without one)")
    ap.add_argument("--role", choices=["proposal", "dataset", "reference"], default="proposal",
                    help="how the uploaded source is treated")
    args = ap.parse_args()

    cfg = Config()
    if not cfg.is_configured:
        print("error: NURUXPLORE_EMAIL / NURUXPLORE_PASSWORD missing. "
              "Copy .env.example to .env and fill them in.", file=sys.stderr)
        return 2

    client = NuruXploreClient.from_credentials(
        cfg.base_url, cfg.email, cfg.password, timeout=cfg.timeout)
    try:
        login = client.login(cfg.email, cfg.password)
    except ApiError as exc:
        print(f"error: login failed: {exc.message}", file=sys.stderr)
        return 2

    balance = (login.get("user") or {}).get("credits_balance")
    print(f"logged in as {cfg.email} — credits_balance={balance}")
    if args.check_only:
        return 0

    if args.no_generate:
        print("(--no-generate: stopping before the expensive call)")

    if not args.source:
        print("error: a source file is required via --source (the live API refuses to "
              "build a research profile without uploaded proposal/dataset context).",
              file=sys.stderr)
        return 2
    if not pathlib.Path(args.source).is_file():
        print(f"error: source file not found: {args.source}", file=sys.stderr)
        return 2

    print("creating proposal project...")
    project_uuid = client.create_project(title=args.topic, type="proposal", auto_title=True)
    print(f"  project_uuid={project_uuid}")

    # The live API needs an uploaded, extracted source before it will build a
    # research profile (otherwise build-research-profile returns 422). Upload
    # the provided sample so the AI has proposal/dataset context to work from.
    if args.source:
        print(f"uploading source ({args.role})...")
        up = client.upload_source(
            project_uuid, args.source,
            title=args.topic, document_role=args.role, type=args.role,
        )
        print(f"  source id={up.get('source') or up.get('id') or up.get('source_id')}")

    print("building research profile (3 credits)...")
    profile = (client.build_research_profile(project_uuid) or {}).get("profile") or {}
    print(f"  profile title: {profile.get('title', '(none)')}")

    print("approving research profile (free)...")
    client.approve_research_profile(project_uuid)  # approve as generated

    print("generating outline (5 credits)...")
    outline = client.generate_outline(project_uuid)
    print(f"  {outline.get('message', 'outline ready')}")

    if args.no_generate:
        print(f"DRY-RUN DONE — project_uuid={project_uuid}")
        return 0

    # The expensive, up-front-charged call. Guard on live balance so a fixed
    # budget is never burned against an empty account (the API would 402).
    if balance is not None and balance < PROPOSAL_MIN_COST:
        print(f"error: balance {balance} < ~{PROPOSAL_MIN_COST} credits needed "
              f"for a proposal. Nothing was charged; stopping here.", file=sys.stderr)
        return 1
    print(f"queueing full proposal generation (charges ~{PROPOSAL_MIN_COST} credits up front)...")
    queued = client.generate_complete(project_uuid, "proposal")
    print(f"  queued={queued.get('queued')}")

    print("polling generation status...")
    try:
        poll_until_done(client, project_uuid)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("exporting PDF (free)...")
    pdf_url = (client.export_document(project_uuid, "pdf") or {}).get("download_url")
    print(f"  pdf_url={pdf_url}")
    print("exporting Word (1 credit)...")
    word_url = (client.export_document(project_uuid, "word") or {}).get("download_url")
    print(f"  word_url={word_url}")

    if args.out:
        outdir = pathlib.Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        for name, url in (("proposal.pdf", pdf_url), ("proposal.docx", word_url)):
            if not url:
                continue
            dest = outdir / name
            urllib.request.urlretrieve(url, dest)
            print(f"  saved -> {dest}")

    print(f"DONE — project_uuid={project_uuid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
