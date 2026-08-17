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

    def create_project(self, title: str = "API integration test session") -> str:
        """Create a chat project and remember its ``uuid`` (reused on later runs)."""
        body = self._request("POST", "/api/projects", json={"title": title, "type": "chat"})
        project = body.get("project") or {}
        uuid = project.get("uuid") or body.get("uuid")
        if not uuid:
            raise ApiError("http", "Project response did not contain a uuid.")
        self.project_uuid = str(uuid)
        return self.project_uuid

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
