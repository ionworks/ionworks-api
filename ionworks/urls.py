"""Frontend URL helpers for the Ionworks web app.

This module provides the :class:`UrlsClient`, which builds links to pages in
the Ionworks web app (``https://app.ionworks.com``) without requiring callers
to hand-construct URLs from entity IDs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._project_id import resolve_project_id

if TYPE_CHECKING:
    from .client import Ionworks


_APP_BASE_URL = "https://app.ionworks.com"


class UrlsClient:
    """Build links to pages in the Ionworks web app.

    Returned URLs always point to ``https://app.ionworks.com``.
    """

    def __init__(self, client: Ionworks) -> None:
        self._client = client

    def measurement(
        self,
        measurement_id: str,
        project_id: str | None = None,
    ) -> str:
        """Build a link to a cell measurement detail page.

        Parameters
        ----------
        measurement_id : str
            The cell measurement ID.
        project_id : str | None, optional
            The project ID the measurement belongs to. Defaults to the
            project_id set on the Ionworks client (resolved from the
            ``IONWORKS_PROJECT_ID`` env var if not passed to the client).

        Returns
        -------
        str
            URL of the measurement detail page on
            ``https://app.ionworks.com``.
        """
        project_id = resolve_project_id(self._client, project_id)
        return (
            f"{_APP_BASE_URL}/dashboard/projects/{project_id}"
            f"/data/measurements/{measurement_id}"
        )
