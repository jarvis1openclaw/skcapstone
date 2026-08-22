"""Tenant, client, and engagement mapping entries."""

from .contract import (
    INTERVAL,
    PROTECTED,
    EntityMapping,
    _m,
    _stateful,
)

SECURITY_MAPPINGS: tuple[EntityMapping, ...] = (
    _m(
        "Tenant",
        "sklegal_identity.tenants",
        _stateful(PROTECTED) + (("name", "name"),),
        persistence_columns=("slug",),
        required_persistence_columns=("slug",),
    ),
    _m(
        "Client",
        "sklegal_legal.clients",
        _stateful(PROTECTED)
        + (("display_name", "display_name"), ("client_kind", "client_kind")),
    ),
    _m(
        "Engagement",
        "sklegal_legal.engagements",
        _stateful(PROTECTED)
        + (("client_id", "client_id"), ("title", "title"), ("scope", "scope")),
        composites=(INTERVAL,),
    ),
)
