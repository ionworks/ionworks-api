"""Frontend URL helpers for the Ionworks web app.

This module provides the :class:`UrlsClient`, which builds links to pages in
the Ionworks web app (``https://app.ionworks.com``) without requiring callers
to hand-construct URLs from entity IDs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import Ionworks


_APP_BASE_URL = "https://app.ionworks.com"


class UrlsClient:
    """Build links to pages in the Ionworks web app.

    Returned URLs always point to ``https://app.ionworks.com``.
    """

    def __init__(self, client: Ionworks) -> None:
        self._client = client

    def measurement(self, measurement_id: str, project_id: str) -> str:
        """Build a link to a cell measurement detail page.

        Parameters
        ----------
        measurement_id : str
            The cell measurement ID.
        project_id : str
            The project ID the measurement belongs to.

        Returns
        -------
        str
            URL of the measurement detail page on
            ``https://app.ionworks.com``.
        """
        return (
            f"{_APP_BASE_URL}/dashboard/projects/{project_id}"
            f"/data/measurements/{measurement_id}"
        )
