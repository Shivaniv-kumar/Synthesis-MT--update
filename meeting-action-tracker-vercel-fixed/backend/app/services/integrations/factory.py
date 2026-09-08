"""Factory function for obtaining a ``PMIntegration`` adapter by platform slug.

Usage::

    integration = get_integration("jira")
    ref = await integration.push_item(item, config)
"""

from __future__ import annotations

from app.services.integrations.base import PMIntegration


def get_integration(platform: str) -> PMIntegration:
    """Return the ``PMIntegration`` adapter for *platform*.

    Parameters
    ----------
    platform:
        One of ``"jira"``, ``"asana"``, ``"linear"``, ``"clickup"``.

    Raises
    ------
    ValueError
        If *platform* is not a recognised integration slug.
    """
    # Lazy imports keep module-level startup cost near zero and avoid circular
    # imports when the models are not yet initialised.
    match platform.lower():
        case "jira":
            from app.services.integrations.jira import JiraIntegration
            return JiraIntegration()

        case "asana":
            from app.services.integrations.asana import AsanaIntegration
            return AsanaIntegration()

        case "linear":
            from app.services.integrations.linear import LinearIntegration
            return LinearIntegration()

        case "clickup":
            from app.services.integrations.clickup import ClickUpIntegration
            return ClickUpIntegration()

        case _:
            raise ValueError(
                f"Unknown integration platform: '{platform}'. "
                "Supported values are: jira, asana, linear, clickup."
            )
