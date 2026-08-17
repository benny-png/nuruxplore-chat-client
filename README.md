# NuruXplore Chat API Client

A small command-line client for the live NuruXplore AI chat API (Part 2 of the
intern take-home exercise). It logs in with a test account, creates (or reuses) a
chat project, sends a message you type (or pass as an argument) and prints the
AI's reply — while handling the ways a real API actually fails: bad credentials,
rate limits, running out of credits, and network/timeout errors.

This is written against the *live* API at `https://nuruxplore.com`, so it has
real authentication, real rate limits, a real finite credit budget and real
error responses.

---

## Requirements

- Python 3.9+
- `pip` (to install `requests` and `pytest`)

## Setup

```bash
# 1. Clone / cd into the repo, then create a virtualenv
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your config from the template (never commit real credentials)
cp .env.example .env
#    then edit .env and fill in NURUXPLORE_EMAIL / NURUXPLORE_PASSWORD
```

> **Important:** `.env` and `.state.json` are gitignored. `.env` holds real
> credentials; `.state.json` caches the bearer token and the chat project's
> `uuid` so later runs reuse the project instead of creating new ones (and
> spending credits) each time. Neither is ever committed.

## Usage

```bash
# Send a message passed as an argument
python -m nuruxplore.cli "What's a good way to summarize survey response frequencies?"

# Interactive prompt
python -m nuruxplore.cli

# Read the message from stdin
echo "Hello there" | python -m nuruxplore.cli

# Override credentials / force a specific project (advanced)
python -m nuruxplore.cli "hi" -e you@example.com -p 'your-password' --project SOME_UUID
```

On success you'll see the AI's reply followed by the running credit balance:

```
AI reply:
A histogram plus a sorted frequency table works well.

[credits remaining: 23]
```

### Exit codes

Failure is reported as a single clear line (never a raw stack trace) with a
distinct exit code per failure kind, so the client is scriptable:

| Code | Meaning |
|------|---------|
| 0    | Success |
| 2    | Auth failure (invalid credentials / expired session) |
| 3    | Rate limit (429) hit repeatedly |
| 4    | Out of credits |
| 5    | Other HTTP error (e.g. 500) |
| 6    | Network / timeout error |

### Runtime safety

- The chat endpoint detects research-writing intent and can switch into full
  document-generation mode, which burns many credits in one call. This client
  sends ordinary conversational messages only (see the example above).
- 429 responses are retried with **bounded exponential backoff** (max 3 retries,
  honoring the server's `Retry-After` when present) rather than hammering the
  endpoint — a tight retry loop would end a fixed-budget session early.
- Credentials are only used in memory and for the login request; they are never
  written to disk except in the gitignored `.env`.

## Tests

All tests mock the HTTP layer — they run offline, spend **zero** credits, and
never touch the live API.

```bash
python -m pytest -q
```

Covered failure modes:

- login success, bad credentials (401), missing token in response
- project creation, and reusing a remembered project without any HTTP call
- send-message success (reply + credit balance)
- out-of-credits (via `credits_remaining: 0` and via HTTP 402)
- rate limiting: 429 retries with backoff, recovering after a 429, and failing
  cleanly after the retry budget is exhausted
- expired-token re-authentication (401 → re-login → retry) without an infinite
  loop, plus a genuine credential failure
- network timeouts and connection errors
- unexpected 5xx

---

## Notes on the live verification

During development I ran the client against the real API. Login, project
creation, message dispatch and the credit balance all worked end-to-end
(`credits_balance` started at 25 and decremented by 1 per message). At the time
of that run the upstream AI backend was itself returning
`"Unable to connect to AI service."`, so I did not get a substantive AI reply and
deliberately stopped spending the fixed credit budget rather than retrying
against a broken service. The client surfaces whatever the API returns verbatim,
including upstream error strings.

## What I'd do differently with more time

- **Transport**: a typed, schema-validated client (e.g. OpenAPI/`openapi-python-client`)
  would catch contract drift up front. The 404 I hit here (doubled `/api/api`)
  is exactly the kind of thing a generated client or a quick contract test
  prevents.
- **Auth**: real refresh-token handling and secure credential storage
  (keyring / env secrets) instead of a plain `.env`.
- **Retries**: distinguish idempotent (GET, safe) vs non-idempotent (message POST)
  requests and only auto-retry non-idempotent calls after an explicit
  "confirm re-send" — a blind retry of a POST can double-spend a credit.
- **Rate limiting**: an in-client token bucket paced to the documented limit,
  not just backoff on 429.
- **Observability**: structured logging + a `--dry-run`/`--cost` flag so the
  credit spend is visible before a real call.
- **Detecting upstream AI failures**: heuristics for "the API returned 200 but
  the reply is an upstream error string", rather than treating it as a success.

## AI assistance

This exercise permits and expects AI tooling. I built it with Claude Code
(Claude Code CLI). Prompts I used (paraphrased):

1. "Read the API-integration PDF spec and plan a Python CLI client for
   https://nuruxplore.com, with distinct handling for 401/429/out-of-credits/
   network errors, mocked HTTP tests, a README, and a push to a public GitHub
   repo."
2. "Structure it as a small package with an injected HTTP session so tests can
   stub the HTTP layer — `config.py` (`.env` loader), `errors.py` (typed
   `ApiError` kinds), `client.py`, `cli.py`, and `tests/`."
3. "Why did the first live run 404? What's the right base-URL/endpoint split?"
   → turned out the base URL must be the host (`https://nuruxplore.com`) with
   `/api/*` as part of the endpoint paths; I fixed config + `.env`.
4. "Add edge-case tests: expired-token re-auth without infinite loop, rate-limit
   retry then recovery/failure, out-of-credits via header and status."

All decisions (language, structure, error taxonomy, retry policy, base-URL
handling, the decision not to burn the credit budget against a broken upstream)
were mine; the assistant executed and reviewed code as I directed it.
