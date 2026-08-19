"""Tiny client for the NuruXplore chat API.

The HTTP layer is injected via ``session`` (a ``requests.Session``-like object
with a ``request()`` method). Tests swap in a fake session so every failure
mode can be exercised without ever touching the live API or spending credits.

Error handling philosophy: every failure is raised as an :class:`ApiError`
with a distinct ``kind`` so the caller can react to each situation (bad
credentials, rate limit, out of credits, server/network trouble) separately
instead of one generic message.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from .errors import ApiError

MAX_RATE_LIMIT_ATTEMPTS = 3
RATE_LIMIT_BASE_BACKOFF_SECONDS = 1.0
RATE_LIMIT_MAX_BACKOFF_SECONDS = 8.0


class NuruXploreClient:
    """A minimal, stateful client for the NuruXplore chat endpoints."""

    def __init__(self, base_url: str, session: Any | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self.timeout = timeout
        self.token: str | None = None
        self.project_uuid: str | None = None
        self.user: dict | None = None
        # Injectable for deterministic tests.
        self.sleep_fn = time.sleep

    # ------------------------------------------------------------------ auth

    def login(self, email: str, password: str) -> dict:
        """Authenticate and store the Bearer token. Returns ``{"token", "user"}``."""
        body = self._request(
            "POST",
            "/api/auth/login",
            json={"email": email, "password": password},
            authenticated=False,
        )
        token = body.get("token") or (body.get("data") or {}).get("token")
        if not token:
            raise ApiError("http", "Login response did not contain a token.")

        self.token = str(token)
        self.user = body.get("user") or (body.get("data") or {}).get("user")
        return {"token": self.token, "user": self.user}

    # --------------------------------------------------------------- project

    def create_project(
        self,
        title: str = "API integration test session",
        type: str = "chat",
        auto_title: bool | None = None,
    ) -> str:
        """Create a project (``chat``, ``proposal`` or ``thesis``) and remember its uuid.

        ``auto_title`` defaults to True server-side (the AI generates an academic
        title from the prompt); pass ``False`` to keep ``title`` exactly as typed.
        """
        body = self._request(
            "POST",
            "/api/projects",
            json={**{"title": title, "type": type}, **({"auto_title": auto_title} if auto_title is not None else {})},
        )
        project = body.get("project") or {}
        uuid = project.get("uuid") or body.get("uuid")
        if not uuid:
            raise ApiError("http", "Project response did not contain a uuid.")
        self.project_uuid = str(uuid)
        return self.project_uuid

    # ------------------------------------------------------- research profile

    def build_research_profile(self, project_uuid: str) -> dict:
        """Ask the AI to build a structured research profile from the topic (3 credits).

        Returns the parsed body, including the ``profile`` dict under review.
        """
        return self._request(
            "POST", f"/api/projects/{project_uuid}/build-research-profile"
        )

    def approve_research_profile(
        self, project_uuid: str, research_profile: dict | None = None
    ) -> dict:
        """Approve the research profile, optionally with edits (free)."""
        return self._request(
            "POST",
            f"/api/projects/{project_uuid}/approve-research-profile",
            json={"research_profile": research_profile} if research_profile is not None else {},
        )

    def generate_outline(self, project_uuid: str) -> dict:
        """Generate the chapter outline from the approved profile (5 credits)."""
        return self._request("POST", f"/api/projects/{project_uuid}/generate-outline")

    def generate_complete(self, project_uuid: str, type: str) -> dict:
        """Queue full document generation (100 proposal / 400-600 thesis credits).

        Charged up front. Returns the parsed 202 body (``{"queued": true, ...}``).
        A 402 surfaces as ``ApiError("out_of_credits", ...)`` via ``_ensure_success``.
        """
        return self._request(
            "POST",
            f"/api/projects/{project_uuid}/generate-complete",
            json={"type": type},
        )

    def generation_status(self, project_uuid: str) -> dict:
        """Return the current background-generation status.

        Includes ``status`` (queued/building_profile/generating/completed/failed),
        ``progress``, ``current_step``, ``steps`` and ``word_count``.
        """
        return self._request("GET", f"/api/projects/{project_uuid}/generation-status")

    def export_document(self, project_uuid: str, fmt: str) -> dict:
        """Export the finished document as ``pdf`` (free) or ``word`` (1 credit).

        Returns the body including a signed ``download_url``.
        """
        return self._request("POST", f"/api/projects/{project_uuid}/export/{fmt}")

    # ------------------------------------------------------------- sources

    def upload_source(
        self,
        project_uuid: str,
        file_path: str,
        *,
        title: str | None = None,
        document_role: str | None = None,
        type: str | None = None,
    ) -> dict:
        """Upload a research source file (PDF/DOCX/TXT/CSV/XLSX) to a project.

        The live API requires an uploaded, extracted source before it will
        build a research profile (otherwise build-research-profile returns
        422). ``document_role``/``type`` select how the file is treated
        (``proposal``, ``dataset`` or ``reference``); ``title`` defaults to the
        filename. Returns the parsed body incl. the created source id.
        """
        if not self.token:
            raise ApiError("auth", "Not authenticated. Call login() first.")
        url = self.base_url + "/api/sources/upload"
        headers = {"Authorization": f"Bearer {self.token}"}
        data: dict = {"project_uuid": project_uuid}
        if title:
            data["title"] = title
        if document_role:
            data["document_role"] = document_role
        if type:
            data["type"] = type
        with open(file_path, "rb") as fh:
            files = {"file": (Path(file_path).name, fh)}
            try:
                response = self._session.request(
                    "POST", url, headers=headers, data=data, files=files, timeout=self.timeout
                )
            except requests.exceptions.Timeout as exc:
                raise ApiError("network", f"Request timed out after {self.timeout:g}s.", None) from exc
            except requests.exceptions.RequestException as exc:
                raise ApiError("network", f"Network error: {exc}") from exc
        self._ensure_success(response)
        try:
            return response.json()
        except ValueError:
            raise ApiError(
                "http",
                f"Unexpected response (HTTP {response.status_code}, not JSON)",
                response.status_code,
            )

    def upload_proposal(self, project_uuid: str, file_path: str, title: str | None = None) -> dict:
        """Upload a proposal source file so it can drive research-profile building."""
        return self.upload_source(
            project_uuid,
            file_path,
            title=title or str(Path(file_path).name),
            document_role="proposal",
            type="proposal",
        )

    def upload_dataset(self, project_uuid: str, file_path: str, title: str | None = None) -> dict:
        """Upload a dataset source file (surveys/results) to the project."""
        return self.upload_source(
            project_uuid,
            file_path,
            title=title or str(Path(file_path).name),
            document_role="dataset",
            type="dataset",
        )

    def verify_source(self, source_id: str) -> dict:
        """Ask the API to re-verify/extract an already-uploaded source (optional)."""
        return self._request("POST", f"/api/sources/{source_id}/verify")

    def ensure_project(self, title: str = "API integration test session") -> str:
        """Return the remembered project, creating one if we don't have it yet."""
        if not self.project_uuid:
            self.create_project(title)
        return self.project_uuid

    # -------------------------------------------------------------- messages

    def send_message(self, project_uuid: str, message: str) -> dict:
        """Send one chat message and return the parsed result.

        Returns a dict with ``reply``, ``credits_remaining`` and the raw body.
        Raises :class:`ApiError` (kind ``out_of_credits``) when the account is
        out of credits.
        """
        body = self._request(
            "POST",
            f"/api/projects/{project_uuid}/messages",
            json={"message": message},
        )

        credits = body.get("credits_remaining")
        if credits is not None and int(credits) <= 0:
            raise ApiError(
                "out_of_credits",
                "You are out of credits. The budget is fixed and won't be topped up.",
            )

        reply = (
            body.get("message")
            or (body.get("assistant_message") or {}).get("content")
            or ""
        )
        return {
            "reply": reply,
            "credits_remaining": credits,
            "body": body,
        }

    # ------------------------------------------------------------------ core

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        authenticated: bool = True,
        _reauthed: bool = False,
    ) -> dict:
        url = self.base_url + path
        headers = {"Content-Type": "application/json"}
        if authenticated:
            if not self.token:
                raise ApiError("auth", "Not authenticated. Call login() first.")
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            response = self._session.request(
                method,
                url,
                headers=headers,
                json=json,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise ApiError(
                "network", f"Request timed out after {self.timeout:g}s.", None
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ApiError("network", f"Network error: {exc}") from exc

        # A 401 can mean an expired token; retry fresh auth exactly once.
        if response.status_code == 401 and authenticated and not _reauthed:
            if self._try_relogin() is True:
                return self._request(method, path, json=json, _reauthed=True)

        if response.status_code == 429:
            return self._handle_rate_limit(method, path, json=json, authenticated=authenticated)

        self._ensure_success(response)

        try:
            return response.json()
        except ValueError:
            raise ApiError(
                "http",
                f"Unexpected response (HTTP {response.status_code}, not JSON)",
                response.status_code,
            )

    def _handle_rate_limit(self, method, path, *, json, authenticated) -> dict:
        for attempt in range(1, MAX_RATE_LIMIT_ATTEMPTS + 1):
            # Respect Retry-After when the server sends it, else exponential backoff.
            wait = RATE_LIMIT_BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            wait = min(wait, RATE_LIMIT_MAX_BACKOFF_SECONDS)
            self.sleep_fn(wait)

            headers = {"Content-Type": "application/json"}
            if authenticated:
                headers["Authorization"] = f"Bearer {self.token}"
            response = self._session.request(
                method,
                self.base_url + path,
                headers=headers,
                json=json,
                timeout=self.timeout,
            )
            if response.status_code != 429:
                self._ensure_success(response)
                return response.json()
        raise ApiError(
            "rate_limit",
            "Rate limit hit repeatedly. Try again in a minute — the limit is low "
            "and a tight retry loop burns budget.",
            429,
        )

    def _ensure_success(self, response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in (401, 403):
            raise ApiError("auth", "Invalid credentials or session expired.", status)
        if status in (402,):
            raise ApiError("out_of_credits", "Out of credits.", status)
        raise ApiError(
            "http",
            f"Unexpected HTTP {status}: {response.text[:200]}",
            status,
        )

    def _try_relogin(self) -> bool:
        """Try to refresh the token from stored credentials. Requires login() creds."""
        # We only auto-reauth when the caller provided credentials up front via
        # ``from_credentials``; login() alone doesn't store them.
        if not (self.email and self.password):
            return False
        try:
            self.login(self.email, self.password)
            return True
        except ApiError:
            return False

    @classmethod
    def from_credentials(
        cls, base_url: str, email: str, password: str, **kwargs
    ) -> "NuruXploreClient":
        """Build a client that can auto-reauthenticate using its own credentials."""
        client = cls(base_url, **kwargs)
        client.email = email
        client.password = password
        return client
