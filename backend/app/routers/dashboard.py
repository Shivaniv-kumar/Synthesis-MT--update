"""Dashboard router — re-exports the canonical router from app.api.dashboard.

The router is defined in app.api.dashboard; we re-export it here so that
app.main continues to import from app.routers without changes.
"""

from __future__ import annotations

from app.api.dashboard import router  # noqa: F401
