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

import os
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from nuruxplore.client import NuruXploreClient
from nuruxplore.errors import ApiError

BASE_URL = os.environ.get("NURUXPLORE_BASE_URL", "https://nuruxplore.com").rstrip("/")
TIMEOUT = float(os.environ.get("NURUXPLORE_TIMEOUT", "30"))
ENV_EMAIL = os.environ.get("NURUXPLORE_EMAIL")
ENV_PASSWORD = os.environ.get("NURUXPLORE_PASSWORD")
SESS_COOKIE = "nurux_session"

app = FastAPI(title="NuruXplore Web App")
# Built React frontend (Vite) — `npm run build` in web/ produces web/dist.
STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"

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

    def ensure_login(self) -> NuruXploreClient:
        if not self._logged_in:
            self.client.login(self.email, self.password)
            self._logged_in = True
        return self.client


_sessions: dict[str, _Session] = {}


def _load_session(request: Request) -> tuple[_Session | None, str]:
    """Return (session, sid) for this request.

    A cookie-bound session is reused if present. Otherwise, if environment
    credentials are configured, provision one automatically. If neither, return
    (None, None) so the caller can reply 401 and the UI shows the login form.
    """
    sid = request.cookies.get(SESS_COOKIE)
    if sid and sid in _sessions:
        return _sessions[sid], sid
    if not (ENV_EMAIL and ENV_PASSWORD):
        return None, None
    sid = uuid.uuid4().hex
    session = _Session(ENV_EMAIL, ENV_PASSWORD)
    _sessions[sid] = session
    return session, sid


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
    return await _run(request, lambda c, s: c.build_research_profile(project_uuid))


class ApproveBody(BaseModel):
    research_profile: dict | None = None


@app.post("/api/local/projects/{project_uuid}/approve-research-profile")
async def approve_profile(project_uuid: str, body: ApproveBody, request: Request):
    return await _run(request, lambda c, s: c.approve_research_profile(project_uuid, body.research_profile))


@app.post("/api/local/projects/{project_uuid}/generate-outline")
async def outline(project_uuid: str, request: Request):
    return await _run(request, lambda c, s: c.generate_outline(project_uuid))


class GenerateBody(BaseModel):
    type: str = "proposal"


@app.post("/api/local/projects/{project_uuid}/generate-complete")
async def generate_complete(project_uuid: str, body: GenerateBody, request: Request):
    return await _run(request, lambda c, s: c.generate_complete(project_uuid, body.type))


@app.get("/api/local/projects/{project_uuid}/generation-status")
async def generation_status(project_uuid: str, request: Request):
    result = await _run(request, lambda c, s: c.generation_status(project_uuid))
    if isinstance(result, JSONResponse):
        return result
    if result.get("status") == "failed":
        return {**result, **FAILED_JOB}
    return result


@app.post("/api/local/projects/{project_uuid}/export/pdf")
async def export_pdf(project_uuid: str, request: Request):
    return await _run(request, lambda c, s: c.export_document(project_uuid, "pdf"))


@app.post("/api/local/projects/{project_uuid}/export/word")
async def export_word(project_uuid: str, request: Request):
    return await _run(request, lambda c, s: c.export_document(project_uuid, "word"))


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
