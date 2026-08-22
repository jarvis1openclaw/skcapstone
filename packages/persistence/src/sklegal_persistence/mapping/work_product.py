"""Work product, template, and bracketed-unknown mapping entries."""

from .contract import (
    PROTECTED,
    SCOPED,
    CompositeField,
    EntityMapping,
    _m,
    _stateful,
)

_VERSION_BINDING = CompositeField(
    "version_binding",
    "artifact_binding",
    (
        "work_product_version_id",
        "work_product_version_number",
        "work_product_content_sha256",
    ),
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
    _m(
        "WorkProductTemplate",
        "sklegal_legal.work_product_templates",
        _stateful(PROTECTED)
        + (
            ("name", "name"),
            ("work_product_kind", "work_product_kind"),
            ("current_version_id", "current_version_id"),
        ),
        persistence_columns=("current_version_number", "current_content_sha256"),
        required_persistence_columns=(
            "current_version_number",
            "current_content_sha256",
        ),
    ),
    _m(
        "WorkProductTemplateVersion",
        "sklegal_legal.work_product_template_versions",
        _stateful(PROTECTED)
        + (
            ("template_id", "template_id"),
            ("version_number", "version_number"),
            ("content_sha256", "content_sha256"),
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
    _m(
        "WorkProductUnknown",
        "sklegal_legal.work_product_unknowns",
        _stateful(SCOPED)
        + (
            ("placeholder_key", "placeholder_key"),
            ("hint", "hint"),
            ("resolved_by_principal_id", "resolved_by_principal_id"),
            ("resolved_at", "resolved_at"),
        ),
        composites=(_VERSION_BINDING,),
    ),
)
