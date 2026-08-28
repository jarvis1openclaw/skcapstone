from __future__ import annotations

from .helpers import PRINCIPAL, fixture


def test_public_synthetic_fixture_is_exact_and_nonsecret() -> None:
    projection, sources = fixture()
    assert projection.current is True
    assert projection.qdrant_compatibility == "metadata_only"
    assert projection.falkordb_compatibility == "metadata_only"
    assert len(sources) == 2
    assert all(PRINCIPAL in source.permitted_principal_ids for source in sources)
    assert all(source.tenant_id == sources[0].tenant_id for source in sources)
    assert all(source.matter_id == sources[0].matter_id for source in sources)
    assert {source.source_role for source in sources} == {
        "matter_evidence",
        "official_authority",
    }
