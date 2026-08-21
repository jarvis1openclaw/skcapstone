from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/retrieval/tenant-partition-contract.json"
DOC_PATH = ROOT / "docs/development/RETRIEVAL-PARTITIONS.md"
AMENDMENT_PATH = ROOT / "docs/approval/AMENDMENT-SKL-S2-10.md"
HIGH_LEVEL_TDD = ROOT / "docs/architecture/SKLEGAL-HIGH-LEVEL-TDD.md"
TASK_TDDS = ROOT / "docs/tasks/SUBAGENT-TASK-TTDS.md"

BASE_SCOPE_FIELDS = {
    "tenant_id",
    "scope_kind",
    "matter_id",
    "release_id",
    "source_id",
    "source_sha256",
    "source_locator",
    "classification",
    "rights_revision",
    "policy_revision",
    "projection_generation",
    "projection_watermark",
}


class RetrievalPartitionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_core_retrieval_and_skmemory_failure_domains_are_separate(self) -> None:
        self.assertEqual("sklegal-retrieval-partition/v2", self.contract["schema"])
        topology = self.contract["topology"]
        self.assertEqual("sklegal-core-pg", topology["core"]["service"])
        self.assertEqual("sklegal-retrieval-pg", topology["retrieval"]["service"])
        self.assertEqual("forbidden", topology["live_skmem_pg_reuse"])
        self.assertEqual("forbidden", topology["shared_core_retrieval_cluster"])
        self.assertFalse(topology["core"]["age_extension_allowed"])

    def test_supply_chain_requires_immutable_qualified_extensions(self) -> None:
        supply_chain = self.contract["supply_chain"]
        for key in (
            "immutable_base_image_digest_required",
            "extension_source_revision_and_checksum_required",
            "sbom_required",
            "vulnerability_scan_required",
            "license_inventory_required",
            "runtime_extension_version_readback_required",
        ):
            with self.subTest(key=key):
                self.assertTrue(supply_chain[key])
        self.assertFalse(
            supply_chain["mutable_branch_or_release_candidate_build_allowed"]
        )
        self.assertEqual(
            {"vector", "age"}, set(supply_chain["allowed_non_core_extensions"])
        )
        self.assertFalse(supply_chain["unapproved_extra_extensions_allowed"])
        self.assertFalse(supply_chain["unresolved_critical_findings_allowed"])
        self.assertTrue(supply_chain["extension_source_sha256_required"])
        self.assertIn("2475", supply_chain["apache_age_revision_requirement"])
        registry = self.contract["registry"]
        self.assertGreaterEqual(
            set(registry["required_fields"]),
            {
                "retrieval_image_digest",
                "postgresql_version",
                "postgresql_version_readback",
                "sbom_sha256",
                "vulnerability_scan_sha256",
                "vulnerability_scan_observed_at",
                "vulnerability_advisory_database_revision",
                "license_inventory_sha256",
            },
        )
        self.assertGreaterEqual(
            set(registry["conditional_fields"]["vector"]),
            {
                "pgvector_source_revision",
                "pgvector_source_sha256",
                "pgvector_runtime_version_readback",
            },
        )
        self.assertGreaterEqual(
            set(registry["conditional_fields"]["graph"]),
            {
                "apache_age_source_revision",
                "apache_age_source_sha256",
                "apache_age_runtime_version_readback",
                "apache_age_qualification_status",
                "apache_age_qualification_evidence_sha256",
            },
        )
        self.assertEqual(
            {"high_risk_acceptance_ref", "high_risk_acceptance_expires_at"},
            set(registry["conditional_fields"]["high_findings"]),
        )

    def test_query_templates_are_runtime_pins_not_projection_row_fields(self) -> None:
        fields = set(self.contract["required_projection_fields"])
        self.assertNotIn("query_template_version", fields)
        self.assertNotIn("query_template_sha256", fields)
        graph_fields = set(self.contract["graph"]["required_entity_fields"])
        self.assertNotIn("query_template_version", graph_fields)
        self.assertNotIn("query_template_sha256", graph_fields)
        self.assertIn("query_template_id", self.contract["trace_fields"])
        self.assertGreaterEqual(
            set(self.contract["trace_fields"]),
            {
                "retrieval_adapter_version",
                "projection_adapter_version",
                "projector_version",
                "projection_schema_version",
                "credential_binding_event_sequence",
                "credential_binding_event_sha256",
                "query_template_version",
                "query_template_sha256",
            },
        )
        self.assertIn(
            "replica_replay_lsn",
            self.contract["conditional_trace_fields"]["replica"],
        )
        self.assertGreaterEqual(
            set(self.contract["cache_key_fields"]),
            {
                "physical_partition_id",
                "credential_binding_event_sha256",
                "projection_adapter_version",
                "retrieval_adapter_version",
                "query_template_id",
                "query_template_version",
                "query_template_sha256",
            },
        )

    def test_database_identity_preserves_approved_session_user_boundary(self) -> None:
        identity = self.contract["database_identity"]
        self.assertEqual("session_user", identity["authorization_identity"])
        self.assertEqual(
            "separate_per_principal_scope_generation_login",
            identity["retrieval_cluster_login"],
        )
        self.assertTrue(identity["core_and_retrieval_credentials_are_distinct"])
        self.assertFalse(identity["shared_runtime_login_allowed"])
        self.assertEqual(
            "capauth_mediated_broker_after_policy_approval",
            identity["credential_resolution"],
        )
        self.assertFalse(identity["raw_credential_exposed_to_caller_model_or_log"])
        self.assertGreaterEqual(
            set(identity["credential_binding_required_fields"]),
            {
                "database_principal",
                "principal_id",
                "tenant_id",
                "scope_kind",
                "matter_id",
                "projection_set_id",
                "projection_generation",
                "policy_revision",
                "rights_revision",
                "authorization_event_sequence",
                "authorization_event_sha256",
                "revoked_at",
            },
        )
        self.assertTrue(
            identity[
                "credential_binding_policy_and_revocation_watermark_must_equal_current_core"
            ]
        )
        self.assertFalse(identity["caller_set_guc_is_authorization_fact"])
        self.assertGreaterEqual(
            set(identity["runtime_role_attributes"]),
            {"nosuperuser", "nobypassrls", "noinherit", "noreplication"},
        )
        self.assertFalse(identity["runtime_role_owns_objects"])
        self.assertFalse(identity["runtime_role_has_role_graph"])
        self.assertTrue(identity["locked_search_path"])
        self.assertFalse(identity["runtime_writable_schema_on_search_path"])
        self.assertTrue(identity["public_schema_create_revoked"])
        self.assertTrue(identity["age_ddl_and_bulk_load_execute_denied_to_runtime"])
        self.assertGreaterEqual(
            set(identity["connection_pool_key_fields"]),
            {
                "database_principal",
                "tenant_id",
                "scope_kind",
                "matter_id_or_null",
                "projection_set_id",
                "projection_generation",
            },
        )
        self.assertFalse(
            identity[
                "authenticated_connection_reuse_across_principal_scope_or_generation"
            ]
        )
        self.assertTrue(
            identity["all_gateway_and_template_database_references_schema_qualified"]
        )
        self.assertFalse(identity["pg_temp_may_shadow_referenced_relation_or_function"])

    def test_vector_rows_are_tenant_partitioned_and_matter_scoped(self) -> None:
        vector = self.contract["vector"]
        self.assertEqual("exact", vector["initial_search"])
        self.assertEqual(
            "physical_list_partition_per_tenant_generation",
            vector["tenant_partition"],
        )
        self.assertEqual(
            "force_rls_plus_exact_matter_filter", vector["matter_boundary"]
        )
        self.assertGreaterEqual(set(vector["required_fields"]), BASE_SCOPE_FIELDS)
        self.assertGreaterEqual(
            set(vector["required_server_controls"]),
            {
                "force_row_level_security",
                "runtime_role_is_nobypassrls",
                "runtime_cannot_access_child_partitions_directly",
                "principal_scope_generation_credential_reference",
            },
        )
        self.assertGreaterEqual(
            set(vector["required_fields"]),
            set(self.contract["required_projection_fields"]),
        )
        self.assertFalse(vector["caller_may_supply_raw_sql"])
        self.assertFalse(vector["caller_may_supply_raw_filter"])

    def test_graphs_are_physical_matter_partitions_and_age_rls_is_not_authority(
        self,
    ) -> None:
        graph = self.contract["graph"]
        self.assertEqual(
            "physical_graph_per_matter_generation",
            graph["protected_matter_partition"],
        )
        self.assertEqual(
            "physical_graph_per_tenant_shared_generation",
            graph["tenant_shared_partition"],
        )
        self.assertFalse(graph["age_rls_is_authorization_boundary"])
        self.assertTrue(graph["relational_manifest_with_force_rls_required"])
        self.assertGreaterEqual(set(graph["required_entity_fields"]), BASE_SCOPE_FIELDS)
        self.assertFalse(graph["caller_may_supply_raw_cypher"])
        self.assertFalse(graph["caller_may_supply_graph_name"])
        self.assertTrue(
            graph["relationship_inherits_required_entity_scope_and_provenance_fields"]
        )
        common_graph_fields = set(graph["required_scope_and_provenance_fields"])
        self.assertGreaterEqual(
            set(graph["required_entity_fields"]), common_graph_fields
        )
        self.assertGreaterEqual(
            set(graph["required_relationship_fields"]),
            common_graph_fields
            | {
                "relationship_id",
                "relationship_type",
                "start_entity_id",
                "end_entity_id",
                "start_endpoint_scope_sha256",
                "end_endpoint_scope_sha256",
            },
        )
        self.assertGreaterEqual(
            set(graph["required_server_controls"]),
            {
                "runtime_direct_graph_schema_access_denied",
                "security_definer_gateway_owner_is_nologin_nosuperuser_nobypassrls",
                "security_definer_gateway_owner_has_no_role_graph",
                "security_definer_gateway_owner_acl_is_exact_read_only_graph_generation",
                "security_definer_gateway_owner_other_graph_access_denied",
                "security_definer_gateway_has_locked_search_path",
                "security_definer_gateway_uses_only_schema_qualified_references",
                "security_definer_gateway_pg_temp_shadowing_denied",
                "security_definer_gateway_public_execute_revoked",
                "security_definer_gateway_definition_hash_registry_pinned",
                "security_definer_gateway_has_no_dynamic_sql_query_text_or_raw_cypher_argument",
                "security_definer_gateway_normalizes_exceptions_without_graph_existence_oracle",
            },
        )

    def test_lexical_search_has_equal_partition_and_scope_backstops(self) -> None:
        lexical = self.contract["lexical"]
        self.assertEqual(
            "physical_list_partition_per_tenant_generation",
            lexical["tenant_partition"],
        )
        self.assertEqual(
            "force_rls_plus_exact_matter_filter", lexical["matter_boundary"]
        )
        for key in (
            "caller_may_supply_raw_sql",
            "caller_may_supply_raw_tsquery",
            "caller_may_supply_text_search_configuration",
            "caller_may_supply_raw_filter",
            "caller_may_supply_order_expression",
        ):
            with self.subTest(key=key):
                self.assertFalse(lexical[key])
        self.assertEqual(
            ["text_search_configuration", "text_search_configuration_sha256"],
            self.contract["registry"]["conditional_fields"]["lexical"],
        )
        self.assertGreaterEqual(
            set(lexical["required_fields"]),
            set(self.contract["required_projection_fields"]),
        )
        self.assertIn("retrieval_record_id", lexical["required_fields"])

    def test_projection_set_activation_is_atomic_across_required_components(
        self,
    ) -> None:
        registry = self.contract["registry"]
        self.assertTrue(registry["active_projection_set_unique_per_tenant_scope"])
        self.assertEqual("projection_set", registry["activation_unit"])
        self.assertEqual({"lexical", "vector"}, set(registry["required_components"]))
        self.assertTrue(registry["all_required_components_ready_before_activation"])
        self.assertEqual(
            "compare_and_swap_ready_projection_set",
            registry["activation_transition"],
        )
        self.assertEqual(
            "deny", registry["mixed_projection_set_release_or_generation_query"]
        )
        self.assertTrue(registry["optional_graph_must_match_selected_projection_set"])
        self.assertEqual(
            {"tenant_id", "scope_kind", "matter_id_or_null"},
            set(registry["activation_key_fields"]),
        )
        self.assertTrue(registry["active_projection_set_manifest_immutable"])
        self.assertTrue(
            registry["later_optional_component_requires_new_projection_set_cutover"]
        )
        self.assertTrue(
            registry["activation_requires_running_backend_supply_chain_evidence_match"]
        )

    def test_authorization_precedes_registry_and_backend_access(self) -> None:
        scope = self.contract["query_scope"]
        self.assertTrue(scope["authorization_before_registry_read"])
        self.assertTrue(scope["authorization_before_backend_call"])
        self.assertTrue(scope["authorization_after_backend_call"])
        self.assertEqual("reject_entire_response", scope["mixed_scope_response"])
        self.assertFalse(scope["denial_shape_reveals_partition_existence"])
        self.assertGreaterEqual(
            set(self.contract["trace_fields"]),
            {"scope_kind", "matter_id", "physical_partition_id"},
        )

    def test_outbox_watermark_and_replica_rules_fail_closed(self) -> None:
        consistency = self.contract["projection_consistency"]
        self.assertTrue(consistency["core_commit_precedes_projection"])
        self.assertEqual("transactional_outbox", consistency["transport"])
        self.assertTrue(consistency["projector_is_idempotent"])
        self.assertTrue(consistency["required_watermark_must_be_replayed"])
        self.assertFalse(consistency["distributed_transaction_allowed"])
        replication = self.contract["replication"]
        self.assertFalse(replication["replication_is_backup"])
        self.assertFalse(replication["replication_provides_write_scaling"])

    def test_legacy_qdrant_and_falkordb_are_not_new_runtime_backends(self) -> None:
        legacy = self.contract["legacy_backends"]
        self.assertFalse(legacy["new_protected_projection_allowed"])
        self.assertFalse(legacy["direct_protected_runtime_query_allowed"])
        self.assertEqual(
            "rebuild_from_pinned_hammertime_release", legacy["migration_method"]
        )

    def test_adapter_leak_matrix_covers_vector_graph_and_replica_races(self) -> None:
        tests = set(self.contract["required_adapter_leak_tests"])
        self.assertGreaterEqual(
            tests,
            {
                "authorization_denied_before_registry_read",
                "lexical_cross_tenant_partition_denied",
                "lexical_cross_matter_row_denied_with_omitted_filter",
                "lexical_count_and_existence_keep_mandatory_scope",
                "vector_cross_tenant_partition_denied",
                "vector_cross_matter_row_denied_with_omitted_filter",
                "vector_direct_child_partition_access_denied",
                "graph_cross_tenant_partition_denied",
                "graph_cross_matter_partition_denied",
                "graph_catalog_enumeration_denied",
                "graph_exact_gateway_execute_acl_denies_every_other_graph",
                "graph_raw_cypher_and_graph_name_rejected",
                "graph_gateway_owner_acl_denies_every_other_graph_generation",
                "graph_gateway_schema_qualification_and_pg_temp_shadowing_proven",
                "graph_relationship_endpoint_mismatch_rejects_entire_response",
                "policy_revision_change_denies_cached_or_inflight_result",
                "rights_revision_change_denies_cached_or_inflight_result",
                "broker_and_mapping_wrong_principal_denied",
                "broker_and_pool_connection_reuse_across_principal_scope_or_generation_denied",
                "credential_wrong_matter_denied",
                "credential_other_partition_or_generation_denied",
                "stale_credential_policy_binding_denied",
                "revoked_credential_binding_denied",
                "replica_lsn_before_required_watermark_denied",
                "retired_generation_never_selected",
                "required_components_from_mixed_projection_sets_denied",
                "optional_graph_from_nonselected_projection_set_denied",
                "active_projection_set_manifest_mutation_denied",
                "optional_component_addition_requires_new_projection_set_cutover",
                "shared_physical_generation_deletion_blocked_by_any_referencing_scope",
                "retrieval_image_digest_or_postgresql_version_mismatch_denies_activation",
                "extension_revision_checksum_or_runtime_version_mismatch_denies_activation",
                "extension_source_sha256_algorithm_or_digest_mismatch_denies_activation",
                "sbom_vulnerability_or_license_evidence_mismatch_denies_activation",
                "legacy_aliases_never_route",
                "denial_shape_does_not_reveal_partition_existence",
            },
        )

    def test_shared_physical_generation_retirement_checks_every_scope(self) -> None:
        retirement = self.contract["retirement"]
        self.assertTrue(
            retirement[
                "shared_physical_generation_delete_requires_every_referencing_scope_retirable"
            ]
        )
        self.assertEqual(
            {
                "retention_clear",
                "legal_hold_clear",
                "backup_and_restore_evidence",
                "human_approval",
            },
            set(retirement["shared_physical_generation_reference_checks"]),
        )

    def test_approved_documents_and_contract_use_ascii_dashes(self) -> None:
        for path in (DOC_PATH, AMENDMENT_PATH, HIGH_LEVEL_TDD, TASK_TDDS):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)

    def test_documents_link_the_replacement_contract_and_approval(self) -> None:
        doc = DOC_PATH.read_text(encoding="utf-8")
        normalized_doc = " ".join(doc.split())
        amendment = AMENDMENT_PATH.read_text(encoding="utf-8")
        tdd = TASK_TDDS.read_text(encoding="utf-8")
        for required in (
            "c4ef2a90",
            "required_adapter_leak_tests",
            "sklegal-core-pg",
            "sklegal-retrieval-pg",
            "AGE row-level security is not an authorization boundary",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized_doc)
        self.assertIn("Status: approved", amendment)
        self.assertIn("SKL-S2-10", tdd)
        self.assertIn("PostgreSQL retrieval and graph adapters", tdd)


if __name__ == "__main__":
    unittest.main()
