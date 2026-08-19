# NuruXplore Chat Client & Web App

A Python client **and** a deployable FastAPI web app for the live NuruXplore AI
academic-writing API (`https://nuruxplore.com/api`). It covers both halves of
the product:

- **Chat** — log in, create a project, send conversational messages (1 credit each).
- **Proposal / thesis generation** — the core feature: turn a topic + uploaded
  source context into a full academic proposal or thesis via a multi-step flow
  (create → upload source → build research profile → approve → outline →
  generate as a background job → poll to completion → export PDF/Word).

Everything AI goes through the NuruXplore API — **never** Groq or any other AI
provider directly. Written against the real, live API, so it has real
authentication, real rate limits, a real finite credit budget, and real failure
modes — all surfaced distinctly in the UI.

---

## Architecture

```
Browser (vanilla JS SPA)
   │  /api/local/*            (only origin-relative calls, no token)
   ▼
FastAPI app (app/main.py)
   │  holds the Bearer token server-side, proxies to the live API
   ▼
NuruXplore API (https://nuruxplore.com/api)
```

The Bearer token is created and held **server-side only** — it is never sent to
the browser and never committed to source. Credentials are read from the
environment (Render secrets) or the gitignored `.env`. The app auto-provisions a
session when `NURUXPLORE_EMAIL` / `NURUXPLORE_PASSWORD` are set; otherwise it
shows a login form.

## Requirements

- Python 3.9+

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                   # fill in your test-account credentials (gitignored)
```

`NURUXPLORE_BASE_URL` is documented as the **host** (`https://nuruxplore.com`);
the documented endpoints already carry the `/api` prefix (e.g.
`/api/auth/login`), so do _not_ add a trailing `/api`.

## Run the web app locally

```bash
uvicorn app.main:app --reload --port 8000
# open http://localhost:8000
```

## UI flows

**Research Expert (proposal / thesis)** — a guided multi-step flow:

1. **Create project** (type `proposal` or `thesis`).
2. **Upload source context** — the live API refuses to build a research profile
   without an uploaded, extracted source (PDF/DOCX/TXT/CSV/XLSX). Upload a
   proposal or dataset here.
3. **Build research profile** (+3) — the AI builds a structured profile from
   your topic + uploaded source.
4. **Approve profile** (free) — editable JSON, review before continuing.
5. **Generate outline** (+5).
6. **Generate document** (charged up front) — confirm before you fire it.
7. **Poll** — a progress bar + step list from the status endpoint; never hammers
   the status endpoint (poll every 2s, hard stop after ~12 minutes or on a
   failed job).
8. **Export** the finished document as **PDF** (free) or **Word** (1 credit).

Distinct error banners cover: bad credentials (401), rate limiting (429),
insufficient credits (402), a failed generation job (`generation_failed`), and
network/timeout errors.

## Cost table

| Step | Cost |
|------|------|
| Build research profile | 3 credits |
| Approve research profile | Free |
| Generate outline | 5 credits |
| Generate complete — proposal | 100 credits |
| Generate complete — thesis | 400–600 credits |
| Chat message | 1 credit |
| PDF export | Free |
| Word export | 1 credit |

The test budget is fixed and won't be topped up, so the UI confirms before the
expensive generate call, and the demo client refuses to fire it below the
documented cost.

## Tests

All HTTP is mocked — they run offline, spend **zero** credits, and never touch
the live API. Failure modes (401/429/402/network/failed-job) are exercised
deterministically for chat, the full proposal flow, and upload.

```bash
python -m pytest -q        # 37 passed
```

## Deploy (Render)

`render.yaml` declares a free Python web service (`uvicorn app.main:app`).
Connect this repo to Render and set `NURUXPLORE_EMAIL` / `NURUXPLORE_PASSWORD`
as secrets. Alternatively, deploy anywhere that runs Python and set the same env
vars.

## Demo script

`scripts/run_proposal_demo.py` runs one full proposal cycle from the CLI (also
useful as a smoke test against the live API):

```bash
# free: just check the live credit balance
python scripts/run_proposal_demo.py --check-only

# dry run through the profile/outline steps (~8 credits)
python scripts/run_proposal_demo.py --no-generate --source ./my_proposal.pdf

# one full proposal (upload → profile → approve → outline → generate → poll → export)
python scripts/run_proposal_demo.py --yes --source ./my_proposal.pdf --out ./exports
```

It prints the credit balance, never prints a token, and refuses the expensive
call if the balance can't cover it.

---

## The command-line client (original)

`python -m nuruxplore.cli "message"` — a small CLI that logs in, creates/reuses a
chat project and prints the AI reply with the running credit balance. Exit codes:

| Code | Meaning |
|------|---------|
| 0 | Success |
| 2 | Auth failure |
| 3 | Rate limit (429) exhausted |
| 4 | Out of credits |
| 5 | Other HTTP error |
| 6 | Network / timeout |

---

## Live verification notes

During development I ran the real flow against the live API:

- **Finding — undocumented upload step.** The brief's 12 endpoints don't include
  file upload, but the live `build-research-profile` endpoint returns **HTTP
  422** (`"No extracted proposal/source text found. Upload a PDF/DOCX/TXT/CSV/
  XLSX file first…"`) unless a source is uploaded first. I discovered the upload
  contract from the product's own frontend client (`POST /api/sources/upload`,
  multipart `project_uuid` + `file` + `title` + `document_role` + `type`) and
  added it to the client, the web app, and the demo script.
- **One real, successful proposal generation ran end-to-end** against the live
  API (test account, 150-credit budget, project
  `64cb1fc2-3a44-4311-ab52-ca5126112151`): uploaded a sample research proposal
  (extraction OK), built + approved the profile, generated a 9-chapter outline,
  queued full generation, polled `queued → generating_sections (38→100%) →
  completed`, and exported both PDF and Word.

## AI assistance

This exercise permits and expects AI tooling. Built with Claude Code (Claude Code
CLI). Prompts used (paraphrased):

1. "Implement Task 3 (UPDATE 2): add a proposal/thesis generation flow to the
   existing client — create, build/approve research profile, generate outline,
   generate-complete (background job), poll status, export PDF/Word — with
   distinct 401/429/402/network/failed-job handling."
2. "Stand it up as a reusable web app that proxies the live API: FastAPI backend
   holding the token server-side, vanilla-JS single-page UI with a progress bar /
   step list, confirm-before-fire on the expensive call, and error-kind banners."
3. "Write mocked-HTTP tests so every failure mode is exercised without spending
   credits or depending on the live API; keep the app and client under test."
4. "The live API rejects build-research-profile with 422 without uploaded source
   context — find the upload contract from the product's own frontend client and
   wire upload into the client, app, UI, tests, and demo script."
5. "Deploy on Render (render.yaml), push to a public GitHub repo, document setup /
   cost table / AI prompts in the README, and verify one real proposal runs."

All decisions (language, architecture, token handling, error taxonomy, polling
policy, the upload addition, not committing secrets) were mine; the assistant
executed and reviewed code as I directed.
