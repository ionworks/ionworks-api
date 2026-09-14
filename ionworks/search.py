"""Global search client for the Ionworks API.

This module provides the :class:`SearchClient`, a thin wrapper over the
``GET /search`` endpoint that searches across every entity type in the
authenticated organization (projects, models, cell specifications,
instances, measurements, parameterized models, templates, materials,
studies, optimizations, pipelines, cyclers, and channels).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from .models import SearchResponse

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .client import Ionworks


class SearchClient:
    """Run global search across all entity types in the organization.

    Matching uses case-insensitive substring (ilike) matching on name fields
    plus Postgres prefix full-text search on free-form text (descriptions,
    notes). Substring matches are ranked ahead of full-text matches within
    each entity type.
    """

    _BASE = "/search"

    def __init__(self, client: Ionworks) -> None:
        self.client = client

    def query(
        self,
        q: str,
        *,
        limit: int = 25,
        offset: int = 0,
        per_type: int = 5,
        entity_types: Sequence[str] | None = None,
        project_id: str | None = None,
    ) -> SearchResponse:
        """Search across all entity types in the authenticated organization.

        Parameters
        ----------
        q : str
            Search query. Must be at least 2 characters; a shorter query
            raises ``IonworksError`` with HTTP 422, and a query that is only
            whitespace raises HTTP 400.
        limit : int, optional
            Maximum number of results to return in this page (1–100).
            Defaults to 25.
        offset : int, optional
            Number of results to skip before this page, for pagination.
            Defaults to 0.
        per_type : int, optional
            Maximum results to return per entity type (1–20). Prevents a
            single entity type from crowding out the others. Defaults to 5.
        entity_types : Sequence[str] | None, optional
            When provided, restrict results to these entity type strings
            (e.g. ``["cell_measurement", "cell_instance"]``). Pass ``None``
            (the default) to search every type; an explicit empty sequence
            restricts to no types and returns an empty response without a
            request. Searchable types: ``project``, ``model``,
            ``cell_specification``, ``cell_instance``, ``cell_measurement``,
            ``parameterized_model``, ``experiment_template``,
            ``optimization_template``, ``material``, ``study``,
            ``optimization``, ``pipeline``.
        project_id : str | None, optional
            When set, scope project-scoped entities (``study``,
            ``optimization``, ``pipeline``) to this project. Org-scoped
            entities are unaffected. Unlike most sub-clients, this does NOT
            default to the client's ``project_id`` — global search spans the
            whole organization by default.

        Returns
        -------
        SearchResponse
            Paginated results. Iterate it directly for the
            :class:`~ionworks.models.SearchResult` items, or read
            ``.results``, ``.total``, ``.limit``, and ``.offset``.

        Examples
        --------
        >>> hits = client.search.query("lithium")
        >>> for hit in hits:
        ...     print(hit.entity_type, hit.name, hit.id)

        Restrict to cell data and page through results::

        >>> page = client.search.query(
        ...     "NMC811",
        ...     entity_types=["cell_specification", "cell_instance"],
        ...     limit=50,
        ... )
        >>> print(page.total)
        """
        # An explicit empty list means "restrict to no types" -> no matches.
        # The backend can't distinguish an empty list from None over the wire
        # (both send zero params), so short-circuit here rather than silently
        # falling back to searching every type.
        if entity_types is not None and len(entity_types) == 0:
            return SearchResponse(
                results=[], query=q.strip(), total=0, limit=limit, offset=offset
            )

        params: list[tuple[str, str | int]] = [
            ("q", q),
            ("limit", limit),
            ("offset", offset),
            ("per_type", per_type),
        ]
        if entity_types is not None:
            params.extend(("entity_types", et) for et in entity_types)
        if project_id is not None:
            params.append(("project_id", project_id))

        endpoint = f"{self._BASE}?{urlencode(params)}"
        response_data: Any = self.client.get(endpoint)
        return SearchResponse(**response_data)
