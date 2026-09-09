"""Action items router — re-exports the canonical router from app.api.items.

The router is defined in app.api.items; we re-export it here so that
app.main continues to import from app.routers without changes.
"""

from __future__ import annotations

from app.api.items import router  # noqa: F401
