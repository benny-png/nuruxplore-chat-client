"""Command-line entry point for the NuruXplore chat client.

Examples::

    python -m nuruxplore.cli "What's a good way to summarize survey response frequencies?"
    python -m nuruxplore.cli            # prompts for the message interactively
    echo "a question" | python -m nuruxplore.cli

Runs fail with a clear one-line message and a distinct exit code per failure
kind (no raw stack traces).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .client import NuruXploreClient
from .config import Config
from .errors import ApiError

EXIT_CODES = {
    "auth": 2,
    "rate_limit": 3,
    "out_of_credits": 4,
    "http": 5,
    "network": 6,
}

STATE_FILE = Path(__file__).resolve().parent.parent / ".state.json"


def _load_state(client: NuruXploreClient) -> None:
    if not STATE_FILE.is_file():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        client.token = data.get("token") or None
        client.user = data.get("user") or None
        client.project_uuid = data.get("project_uuid") or None
    except (ValueError, OSError):
        # Corrupt/missing state is not fatal; we'll just re-auth and re-create.
        pass


def _save_state(client: NuruXploreClient) -> None:
    payload = {
        "token": client.token,
        "user": client.user,
        "project_uuid": client.project_uuid,
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _resolve_message(raw: str | None, stdin_is_tty: bool) -> str:
    if raw:
        return raw
    if not stdin_is_tty:
        # Piped input (e.g. `echo "hello" | python -m nuruxplore.cli`).
        piped = sys.stdin.read().strip()
        if piped:
            return piped
    return input("Message: ").strip()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nuruxplore-chat-client",
        description="Send a chat message to the NuruXplore AI and print its reply.",
    )
    p.add_argument("message", nargs="?", help="The message to send (else prompted/read from stdin).")
    p.add_argument("-e", "--email", help="Override the account email (default: .env).")
    p.add_argument("-p", "--password", help="Override the account password (default: .env).")
    p.add_argument("--project", help="Reuse a specific project uuid instead of the saved one.")
    p.add_argument("--no-persist", action="store_true", help="Don't write .state.json (no reuse).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config(email=args.email, password=args.password)

    if not cfg.is_configured:
        print("Missing credentials. Copy .env.example to .env and fill in NURUXPLORE_EMAIL/PASSWORD.",
              file=sys.stderr)
        return EXIT_CODES["auth"]

    client = NuruXploreClient(cfg.base_url, timeout=cfg.timeout)
    client.email = cfg.email
    client.password = cfg.password

    if not args.no_persist:
        _load_state(client)
        if args.project:
            client.project_uuid = args.project

    try:
        # Authenticate (reuse cached token; auto-reauth on 401 via stored creds).
        if not client.token:
            client.login(cfg.email, cfg.password)

        message = _resolve_message(args.message, sys.stdin.isatty())
        if not message:
            print("Empty message; nothing to send.", file=sys.stderr)
            return EXIT_CODES["http"]

        project = client.ensure_project()
        result = client.send_message(project, message)

        if not args.no_persist:
            _save_state(client)

        print("AI reply:")
        print(result["reply"])
        credits = result.get("credits_remaining")
        if credits is not None:
            print(f"\n[credits remaining: {credits}]")
        return 0

    except ApiError as exc:
        print(f"Error ({exc.kind}): {exc.message}", file=sys.stderr)
        return EXIT_CODES.get(exc.kind, 1)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
