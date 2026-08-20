"""NuruXplore look-alike web app — FastAPI backend.

Serves a single-page UI and proxies every request to the live NuruXplore API
(https://nuruxplore.com/api). The app is the consumer of the API: the Bearer
token is created and held server-side only, never sent to the browser and never
committed to source. If ``NURUXPLORE_EMAIL`` / ``NURUXPLORE_PASSWORD`` are set
in the environment (Render secrets, or ``.env`` locally) the app provisions a
session automatically; otherwise the UI shows a login form.

Each session owns its own :class:`NuruXploreClient` (bound to credentials, so a
401 expiry auto-reauthenticates once). Every ``ApiError`` is mapped to an HTTP
status + ``{kind, message}`` so the frontend can render a distinct message per
failure mode (401 auth, 429 rate limit, 402 insufficient credits, network).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from nuruxplore.client import NuruXploreClient
from nuruxplore.errors import ApiError

import agents as agents_mod
from agents.client import AgentsError, LLMClient
from agents.orchestrator import Ledger
from agents.graphs import chat_reply as agent_chat_reply, run_research as agent_run_research

BASE_URL = os.environ.get("NURUXPLORE_BASE_URL", "https://nuruxplore.com").rstrip("/")
TIMEOUT = float(os.environ.get("NURUXPLORE_TIMEOUT", "30"))
ENV_EMAIL = os.environ.get("NURUXPLORE_EMAIL")
ENV_PASSWORD = os.environ.get("NURUXPLORE_PASSWORD")
SESS_COOKIE = "nurux_session"

app = FastAPI(title="NuruXplore Web App")
# Built React frontend (Vite) — `npm run build` in web/ produces web/dist.
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

# A failed background-generation job is a *successful* HTTP response whose
# body carries status="failed". We surface it as this structured dict so the UI
# can render a distinct "generation failed" panel, separate from HTTP errors.
FAILED_JOB = {"kind": "generation_failed", "message": "Document generation failed. Your credits were refunded."}


class _Session:
    """One browser session = one API client (holding one Bearer token)."""

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.client = NuruXploreClient.from_credentials(BASE_URL, email, password, timeout=TIMEOUT)
        self._logged_in = False
        # DeepSeek agent toggle, per session (mirrors the UI switch). Defaults
        # to ON when the server is configured for agents, so the agentic path is
        # the out-of-the-box experience; users can still flip it OFF.
        self.use_agents = agents_mod.available()

    def ensure_login(self) -> NuruXploreClient:
        if not self._logged_in:
            self.client.login(self.email, self.password)
            self._logged_in = True
        return self.client


_sessions: dict[str, _Session] = {}

# In-memory agent-generated document state per project: the approved research
# profile captured at approve-time, and the finished draft once generated.
_agent_state: dict[str, dict] = {}


def _agent_client() -> LLMClient | None:
    """Return a configured DeepSeek client, or None if the toggle can't run."""
    return LLMClient() if agents_mod.available() else None


def _uses_agents(session) -> bool:
    """True when this session's toggle is on AND the agent layer is configured.

    Uses ``getattr`` so injected test fakes (which lack ``use_agents``) degrade
    safely to the existing nuruxplore path.
    """
    return bool(getattr(session, "use_agents", False)) and agents_mod.available()


def _agent_step_writer(project_uuid: str):
    """Return an ``on_step`` callback that writes live progress into state."""
    def writer(label: str) -> None:
        st = _agent_state.setdefault(project_uuid, {})
        st["current_step"] = label
        idx = st.get("step_index", 0) + 1
        st["step_index"] = idx
        # ~6 phases for a research graph -> step up toward 94%, then 100 at done.
        st["progress"] = max(2, min(94, int(idx * 16)))
    return writer


async def _run_research_bg(project_uuid: str, agent_client: LLMClient, topic: str, profile: dict) -> None:
    """Run the full-length research graph in the background, reporting progress."""
    started = time.monotonic()
    try:
        ledger = Ledger()
        title, doc, ledger = await agent_run_research(
            agent_client, ledger, topic, profile,
            on_step=_agent_step_writer(project_uuid),
        )
        _agent_state[project_uuid].update({
            "title": title,
            "text": doc,
            "status": "completed",
            "progress": 100,
            "current_step": "done",
            "word_count": len(doc.split()),
            "wall_time_s": round(time.monotonic() - started, 1),
            "usage": {**ledger.summary(), "wall_time_s": round(time.monotonic() - started, 1)},
        })
    except (AgentsError, ApiError) as exc:
        _agent_state[project_uuid].update({"status": "failed", "message": str(exc)})


def _load_session(request: Request) -> tuple[_Session | None, str]:
    """Return (session, sid) for this request.

    A cookie-bound session is reused if present. Otherwise we return
    (None, None) so the caller replies 401 and the UI shows the login form.

    Auto-provisioning is intentionally DISABLED: every user must log in with
    their own nuruxplore.com credentials via /api/local/login. The env
    credentials (if any) are deliberately NOT used to mint anonymous sessions,
    so a bare public deployment cannot spend the configured account's credits
    for unauthenticated visitors.
    """
    sid = request.cookies.get(SESS_COOKIE)
    if sid and sid in _sessions:
        return _sessions[sid], sid
    return None, None


def _error_response(exc: ApiError, status_map: dict[str, int] | None = None) -> JSONResponse:
    map_ = status_map or {
        "auth": 401,
        "rate_limit": 429,
        "out_of_credits": 402,
        "network": 502,
        "http": 502,
    }
    return JSONResponse(
        status_code=map_.get(exc.kind, 500),
        content={"kind": exc.kind, "message": exc.message, "status": map_.get(exc.kind, 500)},
    )


# ------------------------------------------------------------------ auth


class LoginBody(BaseModel):
    email: str
    password: str


@app.post("/api/local/login")
async def login(body: LoginBody, request: Request, response: JSONResponse):
    sid = uuid.uuid4().hex
    session = _Session(body.email, body.password)
    try:
        session.ensure_login()
    except ApiError as exc:
        return _error_response(exc)
    _sessions[sid] = session
    response.set_cookie(SESS_COOKIE, sid, httponly=True, samesite="lax")
    return {"success": True, "user": session.client.user, "credits_balance": (session.client.user or {}).get("credits_balance")}


@app.get("/api/local/me")
async def me(request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Not authenticated. Please log in."})
    try:
        client = session.ensure_login()
    except ApiError as exc:
        return _error_response(exc)
    return {"user": client.user, "credits_balance": (client.user or {}).get("credits_balance")}


# ---------------------------------------------------------- prefs (agent toggle)


class PrefsBody(BaseModel):
    use_agents: bool


def _prefs_payload(session: _Session) -> dict:
    return {
        "use_agents": bool(getattr(session, "use_agents", False)),
        "agents_available": agents_mod.available(),
        "model": agents_mod.model_name() if agents_mod.available() else None,
    }


@app.get("/api/local/prefs")
async def get_prefs(request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    return _prefs_payload(session)


@app.post("/api/local/prefs")
async def set_prefs(body: PrefsBody, request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    session.use_agents = bool(body.use_agents)
    return _prefs_payload(session)


# --------------------------------------------------------------- chat


class ChatMessageBody(BaseModel):
    project_uuid: str
    message: str


class ProjectBody(BaseModel):
    title: str
    type: str = "chat"
    auto_title: bool | None = None


@app.post("/api/local/projects")
async def create_project(body: ProjectBody, request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    try:
        client = session.ensure_login()
        kwargs = {"title": body.title, "type": body.type}
        if body.auto_title is not None:
            kwargs["auto_title"] = body.auto_title
        uuid_ = client.create_project(**kwargs)
    except ApiError as exc:
        return _error_response(exc)
    return {"project_uuid": uuid_, "type": body.type}


@app.post("/api/local/chat")
async def chat(body: ChatMessageBody, request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    try:
        client = session.ensure_login()
    except ApiError as exc:
        return _error_response(exc)

    # DeepSeek agent path (opt-in): answers locally via the multi-agent graph,
    # skipping nuruxplore's paid send_message entirely.
    if _uses_agents(session):
        agent_client = _agent_client()
        try:
            prior = _recent_messages(client, body.project_uuid, n=8)
            reply, ledger = await agent_chat_reply(
                agent_client, Ledger(), body.message, prior
            )
        except AgentsError as exc:
            return JSONResponse(status_code=502, content={"kind": "agent", "message": str(exc)})
        except ApiError as exc:
            return _error_response(exc)
        return {
            "reply": reply,
            "credits_remaining": (client.user or {}).get("credits_balance"),
            "agent": "deepseek",
            "usage": ledger.summary(),
        }

    try:
        result = client.send_message(body.project_uuid, body.message)
    except ApiError as exc:
        return _error_response(exc)
    return {"reply": result["reply"], "credits_remaining": result["credits_remaining"]}


@app.get("/api/local/projects/{project_uuid}/messages")
async def list_messages(project_uuid: str, request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    try:
        client = session.ensure_login()
        body = client._request("GET", f"/api/projects/{project_uuid}/messages")
    except ApiError as exc:
        return _error_response(exc)
    return body


# ------------------------------------------------- research generation flow


@app.post("/api/local/projects/{project_uuid}/upload")
async def upload_source(
    project_uuid: str,
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    document_role: str | None = Form(None),
    type: str | None = Form(None),
):
    """Upload a source file (proposal/dataset/reference) to a project.

    The live API requires an uploaded, extracted source before it will build a
    research profile. We buffer the upload to a temp file, forward it to the
    API as multipart, then remove the temp file — the file never stays on disk.
    """
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    try:
        client = session.ensure_login()
    except ApiError as exc:
        return _error_response(exc)

    suffix = Path(file.filename or "upload.bin").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        return client.upload_source(
            project_uuid, tmp_path, title=title, document_role=document_role, type=type
        )
    except ApiError as exc:
        return _error_response(exc)
    finally:
        os.unlink(tmp_path)


@app.post("/api/local/projects/{project_uuid}/build-research-profile")
async def build_profile(project_uuid: str, request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    result = await _run(request, lambda c, s: c.build_research_profile(project_uuid))
    if isinstance(result, dict) and result.get("profile"):
        _agent_state.setdefault(project_uuid, {})["profile"] = result["profile"]
    return result


class ApproveBody(BaseModel):
    research_profile: dict | None = None


@app.post("/api/local/projects/{project_uuid}/approve-research-profile")
async def approve_profile(project_uuid: str, body: ApproveBody, request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    # Remember the approved profile so the agent graph can run from it.
    if body.research_profile:
        _agent_state.setdefault(project_uuid, {})["profile"] = body.research_profile
    return await _run(request, lambda c, s: c.approve_research_profile(project_uuid, body.research_profile))


@app.post("/api/local/projects/{project_uuid}/generate-outline")
async def outline(project_uuid: str, request: Request):
    return await _run(request, lambda c, s: c.generate_outline(project_uuid))


class GenerateBody(BaseModel):
    type: str = "proposal"


def _topic_from_profile(profile) -> str:
    """Derive a research topic from the approved profile, with a safe fallback."""
    if not isinstance(profile, dict):
        return "the research document specified by the approved research profile"
    for key in ("topic", "title", "research_topic", "research_title", "objectives", "goal"):
        val = profile.get(key)
        if val:
            return str(val)[:300]
    return "the research document specified by the approved research profile"


@app.post("/api/local/projects/{project_uuid}/generate-complete")
async def generate_complete(project_uuid: str, body: GenerateBody, request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})

    # DeepSeek agent path: generate the full document from the approved profile
    # via orchestrator-workers, replacing nuruxplore's queued generation. The
    # graph runs in the background and reports live progress to
    # generation-status, so the UI can show a progress bar + current step.
    if _uses_agents(session):
        state = _agent_state.get(project_uuid, {})
        profile = state.get("profile")
        if not profile:
            return JSONResponse(
                status_code=400,
                content={"kind": "need_profile", "message": "Build and approve a research profile first."},
            )
        agent_client = _agent_client()
        topic = _topic_from_profile(profile)
        _agent_state[project_uuid] = {
            "profile": profile,
            "topic": topic,
            "status": "queued",
            "progress": 0,
            "current_step": "Queued…",
        }
        asyncio.create_task(_run_research_bg(project_uuid, agent_client, topic, profile))
        return {"queued": True, "agent": "deepseek", "status": "queued"}

    return await _run(request, lambda c, s: c.generate_complete(project_uuid, body.type))


@app.get("/api/local/projects/{project_uuid}/generation-status")
async def generation_status(project_uuid: str, request: Request):
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    # Agent docs generate in the background; surface live progress + steps.
    if _uses_agents(session):
        st = _agent_state.get(project_uuid)
        if st and st.get("status") == "completed":
            return {
                "status": "completed",
                "progress": 1.0,
                "steps": 1,
                "current_step": "done",
                "word_count": st.get("word_count", 0),
                "title": st.get("title"),
                "agent": "deepseek",
            }
        if st and st.get("status") == "failed":
            return {**st, "kind": "generation_failed"}
        if st:
            return {
                "status": st.get("status", "queued"),
                "progress": (st.get("progress", 0) or 0) / 100.0,
                "current_step": st.get("current_step", "Working…"),
                "word_count": None,
                "agent": "deepseek",
            }
    result = await _run(request, lambda c, s: c.generation_status(project_uuid))
    if isinstance(result, JSONResponse):
        return result
    if result.get("status") == "failed":
        return {**result, **FAILED_JOB}
    return result


@app.get("/api/local/projects/{project_uuid}/content")
async def project_content(project_uuid: str, request: Request):
    """Return the agent-generated document text (DeepSeek path only)."""
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    state = _agent_state.get(project_uuid)
    if _uses_agents(session) and state and state.get("text"):
        return {
            "title": state.get("title", ""),
            "text": state["text"],
            "word_count": state.get("word_count", 0),
            "agent": "deepseek",
        }
    return JSONResponse(
        status_code=404,
        content={"kind": "not_found", "message": "No agent-generated content for this project."},
    )


@app.post("/api/local/projects/{project_uuid}/export/pdf")
async def export_pdf(project_uuid: str, request: Request):
    return await _run(request, lambda c, s: c.export_document(project_uuid, "pdf"))


@app.post("/api/local/projects/{project_uuid}/export/word")
async def export_word(project_uuid: str, request: Request):
    return await _run(request, lambda c, s: c.export_document(project_uuid, "word"))


def _recent_messages(client, project_uuid: str, n: int = 8) -> list[dict]:
    """Pull the project's recent message list for agent context (defensive)."""
    try:
        body = client._request("GET", f"/api/projects/{project_uuid}/messages")
    except ApiError:
        return []
    msgs = body.get("messages") if isinstance(body, dict) else body
    if not isinstance(msgs, list):
        return []
    out = []
    for m in msgs[-n:]:
        if not isinstance(m, dict):
            continue
        content = (
            m.get("content")
            or m.get("message")
            or (m.get("assistant_message") or {}).get("content")
            or ""
        )
        role = m.get("role") or m.get("sender") or "assistant"
        if content:
            out.append({"role": role, "content": str(content)})
    return out


# ------------------------------------------------------------- helpers


async def _run(request: Request, fn):
    """Resolve the session, ensure login and run ``fn``, mapping ApiError to HTTP."""
    session, sid = _load_session(request)
    if session is None and sid is None:
        return JSONResponse(status_code=401, content={"kind": "auth", "message": "Please log in."})
    try:
        client = session.ensure_login()
    except ApiError as exc:
        return _error_response(exc)
    try:
        return fn(client, session)
    except ApiError as exc:
        return _error_response(exc)


# ------------------------------------------------------------ static UI
# Serve the built React SPA when it is present. Assets live under /assets
# (Vite output); the root returns index.html. The API lives under /api/local/*
# (registered above). In the split deployment the React frontend is served by
# its own nginx container and FastAPI runs API-only, so these routes are
# optional: they only mount when a web/dist build actually exists.
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")
