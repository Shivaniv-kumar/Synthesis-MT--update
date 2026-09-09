"""Abstract base classes for project-management integrations.

Each integration adapter (Jira, Asana, Linear, ClickUp) subclasses
``PMIntegration`` and implements the three abstract methods.  All HTTP I/O
is done with ``httpx.AsyncClient`` so adapters are fully non-blocking.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.models.action_item import ActionItem


@dataclass
class ExternalRef:
    """A handle to an item that has been pushed to an external platform."""

    external_id: str
    """The platform's own identifier for the created item (e.g. Jira issue key, Asana GID)."""

    external_url: str
    """A browser-accessible link to the item on the external platform."""

    platform: str
    """Short platform slug: "jira" | "asana" | "linear" | "clickup"."""


class PMIntegration(ABC):
    """Abstract adapter that every PM-platform integration must implement.

    ``config`` dicts carry platform-specific credentials and target settings
    that are stored (encrypted) in ``IntegrationConfig``.  The adapters are
    stateless; instantiate once and reuse.
    """

    @abstractmethod
    async def push_item(self, item: ActionItem, config: dict) -> ExternalRef:
        """Create a new task/issue on the external platform.

        Parameters
        ----------
        item:
            The local ``ActionItem`` to push.
        config:
            Platform config dict containing credentials (already decrypted)
            and target settings (project key, list id, team id, …).

        Returns
        -------
        ExternalRef
            Identifies the newly-created external item.
        """

    @abstractmethod
    async def update_item_status(
        self,
        external_ref: str,
        new_status: str,
        config: dict,
    ) -> None:
        """Transition the external item to match the local status.

        Parameters
        ----------
        external_ref:
            The ``external_id`` stored in ``ItemIntegrationRef``.
        new_status:
            One of ``"Open"``, ``"In progress"``, ``"Done"``.
        config:
            Platform credentials + target settings.
        """

    @abstractmethod
    async def get_item_status(
        self,
        external_ref: str,
        config: dict,
    ) -> Optional[str]:
        """Fetch the current status of an external item.

        Parameters
        ----------
        external_ref:
            The ``external_id`` stored in ``ItemIntegrationRef``.
        config:
            Platform credentials + target settings.

        Returns
        -------
        str or None
            The external platform's status label, or ``None`` if the item was
            deleted / not found (allows the caller to skip or tombstone it).
        """
