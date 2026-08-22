"""Machine-checked coverage of the S2-10 adapter leak and qualification matrix.

Every entry in the contract's ``required_adapter_leak_tests`` and
``required_qualification_tests`` maps to at least one concrete test. The
mapping is exact: a missing or extra key fails, and every referenced test
function must exist in its module.
"""

from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/retrieval/tenant-partition-contract.json"

ORCH = "tests.test_retrieval_orchestrator"
MODELS = "tests.test_retrieval_models"
LOADER = "tests.test_retrieval_contract_loader"
POSTGRES = "tests.test_retrieval_postgres"
BROKER = "tests.test_retrieval_broker"
REGISTRY = "tests.test_retrieval_registry"
PROJECTOR = "tests.test_retrieval_projector"
TRACE = "tests.test_retrieval_trace"
DISPOSABLE = "tests.integration.test_retrieval_partition_disposable"
DISPOSABLE_CLASS = "RetrievalDisposablePostgresTests"
LOADER_CLASS = "ClosedQueryTemplateRegistryTests"

_RAW_CONTROLS = f"{LOADER}:{LOADER_CLASS}.test_raw_query_controls_and_unknown_parameters_are_rejected"
_SUPPLY_CHAIN = f"{REGISTRY}:test_supply_chain_mismatch_denies_activation"
_SCOPE_INVARIANTS = (
    f"{MODELS}:test_exact_scope_invariants_and_explicit_tenant_shared_decision"
)
_STALE_WATERMARK = (
    f"{ORCH}:test_stale_watermark_and_release_mismatch_deny_before_backend"
)
_REVISION_RACE = f"{ORCH}:test_revision_change_after_backend_rejects_result"
_NEVER_SELECTED = (
    f"{REGISTRY}:test_retired_and_candidate_generations_are_never_selected"
)
_READ_ONLY_TEMPLATES = f"{TRACE}:test_graph_templates_are_read_only_and_closed"
_GATEWAY_OWNER = f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_gateway_owner_is_hardened_and_denied_other_generations"
_GATEWAY_DEF = f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_gateway_definition_is_pinnable_and_free_of_dynamic_sql"
_GATEWAY_ACL = f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_gateway_public_execute_is_revoked_and_acl_is_exact"
_ANN_DENIED = (
    f"{TRACE}:test_approximate_vector_search_is_denied_before_registry_promotion"
)
_DELETION_GATE = f"{REGISTRY}:test_shared_physical_generation_deletion_gate"
_REFERENCING_SCOPE = (
    f"{REGISTRY}:test_deletion_blocked_while_any_referencing_scope_is_not_retired"
)

COVERAGE: dict[str, tuple[str, ...]] = {
    "authorization_denied_before_registry_read": (
        f"{ORCH}:test_authorization_denial_precedes_binding_registry_and_backend",
    ),
    "authorization_denied_before_vector_query": (
        f"{TRACE}:test_authorization_denial_precedes_any_vector_backend_access",
    ),
    "authorization_denied_before_graph_query": (
        f"{TRACE}:test_authorization_denial_precedes_any_graph_backend_access",
    ),
    "retrieval_image_digest_or_postgresql_version_mismatch_denies_activation": (
        _SUPPLY_CHAIN,
    ),
    "extension_revision_checksum_or_runtime_version_mismatch_denies_activation": (
        _SUPPLY_CHAIN,
    ),
    "extension_source_sha256_algorithm_or_digest_mismatch_denies_activation": (
        f"{REGISTRY}:test_non_sha256_extension_checksum_cannot_be_constructed",
        _SUPPLY_CHAIN,
    ),
    "sbom_vulnerability_or_license_evidence_mismatch_denies_activation": (
        _SUPPLY_CHAIN,
    ),
    "vulnerability_scan_revision_or_expired_high_risk_acceptance_denies_activation": (
        _SUPPLY_CHAIN,
        f"{REGISTRY}:test_expired_high_risk_acceptance_denies_activation",
    ),
    "age_unqualified_or_evidence_mismatch_denies_activation": (
        f"{REGISTRY}:test_unqualified_age_denies_graph_set_activation",
        f"{REGISTRY}:test_age_evidence_mismatch_denies_activation",
    ),
    "shared_runtime_login_rejected": (
        f"{BROKER}:test_shared_runtime_login_is_rejected",
    ),
    "broker_and_mapping_wrong_principal_denied": (
        f"{BROKER}:test_broker_denies_a_binding_for_the_wrong_principal",
    ),
    "broker_and_pool_connection_reuse_across_principal_scope_or_generation_denied": (
        f"{BROKER}:test_pool_denies_reuse_across_principal_scope_set_or_generation",
    ),
    "credential_wrong_matter_denied": (
        f"{BROKER}:test_broker_denies_a_binding_for_the_wrong_matter_or_generation",
    ),
    "credential_other_partition_or_generation_denied": (
        f"{BROKER}:test_broker_denies_a_binding_for_the_wrong_matter_or_generation",
        f"{TRACE}:test_credential_binding_mismatch_denies_before_registry_access",
    ),
    "stale_credential_policy_binding_denied": (
        f"{BROKER}:test_broker_denies_a_stale_policy_binding",
    ),
    "revoked_credential_binding_denied": (
        f"{BROKER}:test_broker_denies_a_revoked_or_missing_binding",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_revocation_closes_access_immediately",
    ),
    "lexical_cross_tenant_partition_denied": (
        f"{TRACE}:test_cross_tenant_partition_rows_reject_the_whole_lexical_response",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_cross_tenant_partition_rows_are_invisible",
    ),
    "lexical_cross_matter_row_denied_with_omitted_filter": (
        f"{TRACE}:test_cross_matter_rows_without_a_filter_reject_the_whole_response",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_omitted_matter_filter_returns_only_the_bound_matter",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_same_tenant_unassigned_matter_is_denied_without_a_filter",
    ),
    "lexical_raw_tsquery_config_sql_filter_and_order_rejected": (_RAW_CONTROLS,),
    "lexical_count_and_existence_keep_mandatory_scope": (
        f"{ORCH}:test_lexical_count_returns_scoped_aggregate_and_leak_mode_denies",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_lexical_search_and_count_share_the_mandatory_scope",
    ),
    "lexical_wrong_scope_result_rejects_entire_response": (
        f"{ORCH}:test_wrong_scope_row_rejects_the_whole_response",
    ),
    "vector_cross_tenant_partition_denied": (
        f"{TRACE}:test_cross_tenant_partition_rows_reject_the_whole_vector_response",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_vector_scope_denies_cross_matter_and_cross_tenant_rows",
    ),
    "vector_cross_matter_row_denied_with_omitted_filter": (
        f"{TRACE}:test_vector_cross_matter_rows_without_a_filter_reject_the_response",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_vector_scope_denies_cross_matter_and_cross_tenant_rows",
    ),
    "vector_raw_sql_and_filter_override_rejected": (
        f"{POSTGRES}:test_forged_template_definition_and_raw_controls_fail_before_runner",
    ),
    "vector_direct_child_partition_access_denied": (
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_direct_child_partition_access_is_denied",
    ),
    "vector_wrong_scope_result_rejects_entire_response": (
        f"{POSTGRES}:test_wrong_scope_or_extra_column_rejects_entire_backend_response",
    ),
    "ann_denied_before_registry_promotion": (_ANN_DENIED,),
    "exact_vector_tie_order_is_deterministic": (
        f"{ORCH}:test_exact_vector_ties_use_retrieval_record_id",
    ),
    "vector_dimension_model_metric_nan_and_infinity_rejected": (
        f"{MODELS}:test_vector_request_requires_finite_exact_dimension",
        f"{PROJECTOR}:test_projected_rows_enforce_component_shape_and_finite_embeddings",
    ),
    "graph_cross_tenant_partition_denied": (
        f"{REGISTRY}:test_cross_tenant_and_mixed_active_sets_are_denied",
    ),
    "graph_cross_matter_partition_denied": (
        f"{PROJECTOR}:test_graph_cross_scope_edges_are_prohibited",
    ),
    "graph_catalog_enumeration_denied": (
        _GATEWAY_ACL,
        _READ_ONLY_TEMPLATES,
    ),
    "graph_exact_gateway_execute_acl_denies_every_other_graph": (
        _GATEWAY_ACL,
        _GATEWAY_OWNER,
    ),
    "graph_raw_cypher_and_graph_name_rejected": (_RAW_CONTROLS,),
    "graph_gateway_owner_and_search_path_are_hardened": (
        _GATEWAY_OWNER,
        _GATEWAY_DEF,
    ),
    "graph_gateway_owner_acl_denies_every_other_graph_generation": (_GATEWAY_OWNER,),
    "graph_gateway_schema_qualification_and_pg_temp_shadowing_proven": (_GATEWAY_DEF,),
    "graph_gateway_public_execute_denied": (_GATEWAY_ACL,),
    "graph_gateway_definition_hash_mismatch_denied": (
        f"{REGISTRY}:test_qualified_age_with_gateway_hash_mismatch_denies_activation",
        _GATEWAY_DEF,
    ),
    "graph_gateway_dynamic_sql_query_text_ddl_and_mutation_denied": (
        _GATEWAY_DEF,
        _READ_ONLY_TEMPLATES,
    ),
    "graph_gateway_exception_shape_hides_graph_existence": (
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_gateway_rechecks_the_binding_and_normalizes_every_failure",
    ),
    "graph_write_templates_denied_to_runtime": (_READ_ONLY_TEMPLATES,),
    "graph_wrong_scope_entity_rejects_entire_response": (
        f"{MODELS}:test_result_rejects_entire_mixed_scope_or_stale_response",
    ),
    "graph_relationship_endpoint_mismatch_rejects_entire_response": (
        f"{PROJECTOR}:test_graph_relationship_endpoint_digest_mismatch_rejects_the_build",
    ),
    "graph_mixed_scope_path_rejects_entire_response": (
        f"{PROJECTOR}:test_graph_cross_scope_edges_are_prohibited",
    ),
    "graph_count_aggregate_and_existence_keep_mandatory_scope": (
        f"{TRACE}:test_graph_count_and_existence_keep_mandatory_scope",
    ),
    "tenant_shared_scope_requires_explicit_decision": (_SCOPE_INVARIANTS,),
    "tenant_shared_scope_rejects_non_null_matter_id": (_SCOPE_INVARIANTS,),
    "matter_scope_rejects_null_matter_id": (_SCOPE_INVARIANTS,),
    "invalid_registry_partition_identifier_rejected": (
        f"{MODELS}:test_projection_requires_exact_component_and_replica_pins",
    ),
    "policy_revision_change_denies_cached_or_inflight_result": (
        _REVISION_RACE,
        f"{TRACE}:test_cache_keys_never_collide_across_revisions",
    ),
    "rights_revision_change_denies_cached_or_inflight_result": (
        _REVISION_RACE,
        f"{TRACE}:test_cache_keys_never_collide_across_revisions",
    ),
    "release_or_generation_mismatch_degrades_explicitly": (_STALE_WATERMARK,),
    "replica_lsn_before_required_watermark_denied": (
        f"{TRACE}:test_replica_behind_the_required_lsn_is_denied",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_replica_must_replay_the_required_lsn_before_serving",
    ),
    "stale_required_component_never_returns_content": (_STALE_WATERMARK,),
    "optional_unqualified_graph_is_explicitly_unavailable": (
        f"{TRACE}:test_optional_unqualified_graph_is_explicitly_incomplete",
        f"{ORCH}:test_optional_age_unavailable_is_explicit_but_mandatory_denies",
    ),
    "retired_generation_never_selected": (_NEVER_SELECTED,),
    "required_components_from_mixed_projection_sets_denied": (
        f"{REGISTRY}:test_cross_tenant_and_mixed_active_sets_are_denied",
    ),
    "required_components_from_mixed_release_or_generation_denied": (
        f"{REGISTRY}:test_activation_denies_mixed_sets_releases_generations_and_scopes",
    ),
    "optional_graph_from_nonselected_projection_set_denied": (
        f"{REGISTRY}:test_graph_from_a_nonselected_projection_set_is_denied",
    ),
    "active_projection_set_manifest_mutation_denied": (
        f"{REGISTRY}:test_active_manifest_mutation_is_denied",
    ),
    "optional_component_addition_requires_new_projection_set_cutover": (
        f"{REGISTRY}:test_graph_addition_to_an_active_set_requires_a_new_set_cutover",
    ),
    "candidate_and_partial_generations_never_selected": (
        _NEVER_SELECTED,
        f"{REGISTRY}:test_activation_denies_partial_candidate_sets",
    ),
    "cache_key_collision_and_revision_invalidation": (
        f"{TRACE}:test_cache_keys_never_collide_across_revisions",
    ),
    "legacy_aliases_never_route": (
        f"{REGISTRY}:test_legacy_aliases_are_provenance_only_and_never_route",
    ),
    "denial_shape_does_not_reveal_partition_existence": (
        f"{REGISTRY}:test_denial_shape_is_identical_for_missing_and_denied_selection",
        f"{TRACE}:test_replica_lag_denial_shares_the_sanitized_external_shape",
        f"{ORCH}:test_sensitive_backend_causes_have_identical_sanitized_shape_and_no_chain",
    ),
    "shared_physical_generation_deletion_blocked_by_any_referencing_scope": (
        _DELETION_GATE,
        _REFERENCING_SCOPE,
    ),
    "exact_vector_recall_baseline": (
        f"{TRACE}:test_exact_vector_recall_baseline_matches_brute_force",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_exact_vector_order_matches_the_brute_force_baseline",
    ),
    "approximate_vector_recall_and_partition_isolation_before_activation": (
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_each_partition_has_independent_indexes",
        _ANN_DENIED,
    ),
    "age_revision_and_extension_version_readback": (_SUPPLY_CHAIN,),
    "age_backend_crash_does_not_interrupt_core_cluster": (
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_retrieval_backend_crash_does_not_interrupt_the_core_cluster",
    ),
    "graph_dump_restore_and_catalog_registration": (
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_dump_restore_preserves_the_graph_manifest_and_catalog",
    ),
    "outbox_replay_rebuild_is_idempotent": (
        f"{PROJECTOR}:test_replay_is_idempotent_under_duplicate_delivery",
        f"{PROJECTOR}:test_rebuild_from_the_pinned_log_equals_incremental_state",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_outbox_replay_is_idempotent",
    ),
    "legacy_shadow_parity_preserves_provenance": (
        f"{PROJECTOR}:test_shadow_parity_preserves_provenance_and_reports_mismatches",
    ),
    "atomic_registry_cutover_and_rollback": (
        f"{REGISTRY}:test_activation_cutover_is_atomic_and_selectable",
        f"{REGISTRY}:test_rollback_restores_the_prior_generation_atomically",
        f"{DISPOSABLE}:{DISPOSABLE_CLASS}.test_registry_cutover_is_atomic_and_rollback_restores_the_prior_set",
    ),
    "retired_generation_deletion_gate": (_DELETION_GATE,),
    "shared_physical_generation_reference_retirement_gate": (_REFERENCING_SCOPE,),
}


def _resolve(reference: str) -> object:
    module_name, _, qualname = reference.partition(":")
    module = importlib.import_module(module_name)
    target: object = module
    for attribute in qualname.split("."):
        target = getattr(target, attribute)
    return target


class AdapterCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_every_contract_leak_and_qualification_entry_is_mapped(self) -> None:
        required = set(self.contract["required_adapter_leak_tests"]) | set(
            self.contract["required_qualification_tests"]
        )
        mapped = set(COVERAGE)
        self.assertEqual(required, mapped)

    def test_every_mapped_test_exists(self) -> None:
        for entry, references in COVERAGE.items():
            self.assertTrue(references, entry)
            for reference in references:
                with self.subTest(entry=entry, reference=reference):
                    self.assertTrue(callable(_resolve(reference)))

    def test_mapping_uses_only_retrieval_test_modules(self) -> None:
        for references in COVERAGE.values():
            for reference in references:
                module_name = reference.partition(":")[0]
                self.assertIn("retrieval", module_name)


if __name__ == "__main__":
    unittest.main()
