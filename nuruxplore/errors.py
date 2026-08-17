"""Error types for the NuruXplore client.

Every failure mode we care about is surfaced as an ``ApiError`` with a
distinct :attr:`~ApiError.kind` so the CLI (or a caller) can react to each
situation differently instead of one generic catch-all.
"""

from __future__ import annotations


class ApiError(Exception):
    """An API call failed in a way we understand and can name.

    Attributes:
        kind: One of ``auth``, ``rate_limit``, ``out_of_credits``,
            ``http`` or ``network``.
        status: The HTTP status code, if the failure came from the API.
        message: A human-readable explanation (no stack trace intended).
    """

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message
