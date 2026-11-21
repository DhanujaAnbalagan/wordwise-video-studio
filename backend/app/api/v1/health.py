"""Application health‑check utilities for VideoFlow AI.

Provides two lightweight FastAPI endpoints used by deployment infrastructure
and the test‑suite:

- ``/health`` – reports that the FastAPI application started successfully.
- ``/ready`` – verifies that the process is ready to handle media‑related
  requests. It checks that the Python interpreter satisfies the minimum version
  requirement (>= 3.11) and that the ``ffmpeg`` and ``ffprobe`` binaries are
  discoverable via the configured paths or the system ``PATH``.

Both endpoints are deliberately tiny and have *no* side‑effects. They are
registered in ``backend/app/api/v1/router.py`` so that they are reachable at
``/api/v1/health`` and ``/api/v1/ready``.
"""

from __future__ import annotations

import shutil
import sys
from fastapi import APIRouter, HTTPException, status

# Import settings from the top‑level app config module.
from ...config import get_settings

router = APIRouter()


def _check_python_version() -> None:
    """Raise ``RuntimeError`` if the interpreter is older than Python 3.11.
    The project declares a requirement for Python 3.11+, but the CI may run on
    an earlier version. Tests that depend on newer language features can import
    this helper to fail fast with a clear message.
    """
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 11):
        raise RuntimeError(
            f"Python 3.11+ is required (detected {major}.{minor})."
        )


def _check_binary(name: str, path: str) -> str:
    """Return the absolute path to *name*.

    ``path`` may be a plain executable name (e.g. ``"ffmpeg"``) which is then
    resolved via ``shutil.which``. If the binary cannot be found a ``RuntimeError``
    is raised with a helpful message.
    """
    resolved = shutil.which(path) if path else None
    if not resolved:
        raise RuntimeError(
            f"{name} binary not found. Set `{name.upper()}_PATH` in the environment "
            "or ensure it is on the system PATH."
        )
    return resolved


@router.get("/health", tags=["health"], summary="Liveness probe")
def health() -> dict:
    """Simple liveness endpoint.

    Returns a static JSON payload confirming that the FastAPI application is
    up. No external resources are accessed, making this safe for quick
    health‑checks.
    """
    return {"status": "ok"}


@router.get("/ready", tags=["health"], summary="Readiness probe")
def ready() -> dict:
    """Readiness endpoint.

    Performs three checks:
    1. Python version >= 3.11.
    2. ``ffmpeg`` binary is available.
    3. ``ffprobe`` binary is available.

    On success a JSON payload with the resolved binary paths is returned.
    If any check fails an HTTP 503 response is raised so that orchestration
    tools can treat the service as not ready.
    """
    settings = get_settings()
    try:
        _check_python_version()
        ffmpeg_path = _check_binary("ffmpeg", settings.ffmpeg_path)
        ffprobe_path = _check_binary("ffprobe", settings.ffprobe_path)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return {
        "status": "ready",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "ffmpeg_path": ffmpeg_path,
        "ffprobe_path": ffprobe_path,
    }
