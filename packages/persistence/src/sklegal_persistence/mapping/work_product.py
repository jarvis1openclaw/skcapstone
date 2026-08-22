"""Work product and work product version mapping entries."""

from .contract import (
    SCOPED,
    EntityMapping,
    _m,
    _stateful,
)

WORK_PRODUCT_MAPPINGS: tuple[EntityMapping, ...] = (
    _m(
        "WorkProduct",
        "sklegal_legal.work_products",
        _stateful(SCOPED)
        + (
            ("title", "title"),
            ("work_product_kind", "work_product_kind"),
            ("current_version_id", "current_version_id"),
            ("validation_result_id", "validation_result_id"),
            ("approval_id", "approval_id"),
        ),
        persistence_columns=("current_version_number", "current_content_sha256"),
        required_persistence_columns=(
            "current_version_number",
            "current_content_sha256",
        ),
    ),
    _m(
        "WorkProductVersion",
        "sklegal_legal.work_product_versions",
        _stateful(SCOPED)
        + (
            ("work_product_id", "work_product_id"),
            ("version_number", "version_number"),
            ("content_sha256", "content_sha256"),
            ("source_artifact_id", "source_artifact_id"),
        ),
        persistence_columns=(
            "encrypted_content",
            "encryption_key_ref",
            "encryption_algorithm",
            "encrypted_at",
        ),
        required_persistence_columns=(
            "encrypted_content",
            "encryption_key_ref",
            "encryption_algorithm",
            "encrypted_at",
        ),
    ),
)
