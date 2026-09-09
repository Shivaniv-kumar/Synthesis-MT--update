"""Meetings router — re-exports the canonical router from app.api.meetings.

The router is defined in app.api.meetings; we re-export it here so that
app.main continues to import from app.routers without changes.
"""

from __future__ import annotations

from app.api.meetings import router  # noqa: F401
