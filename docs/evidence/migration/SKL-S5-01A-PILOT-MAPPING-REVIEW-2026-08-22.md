# SKL-S5-01A pilot dry run and human mapping review

Run id: `2413c223-c54c-557d-866a-1e175f68b8b0`
Source snapshot: `hammertime-working-tree-2026-08-22`
Adapter version: `0.1.0`
Import batch proposal: `528fad8e-aaea-5a56-9879-c32d314bec83`
Matter root: `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order`
Observed at: `2026-08-22T07:06:00.781581+00:00`

## Scope

- Matter proposal: `PRB-2026-009` maps to Matter.
- Matter Event proposal: `INC-016` maps to a transaction-review Matter Event.
- This slice performs no import writes; every mapping is proposed only.

## Provenance posture

- The source snapshot label denotes a pinned HammerTime working tree, not a corpus release: pilot matter records live under `incidents/`, which release manifests do not cover.
- Every file is pinned by content SHA-256 and observation time, so any later source change produces a new batch id and a visible diff.

## Source hash capture

- Files inventoried and hashed: 112 (426962378 bytes)
- Skipped entries (never followed): 0

## Zero-source-change proof

- Pre-run files hashed: 112
- Post-run files hashed: 112
- Unchanged content: 112
- Changed content: 0
- Added paths: 0
- Removed paths: 0
- Modification time changed: 0
- Zero source changes: True
- Import write operations: 0

## Proposed mappings (human review pending)

| Legacy record | Legacy path | Target | Mapping rule | Status | Decision |
|---|---|---|---|---|---|
| `PRB-2026-009` | `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/PROBLEM.md` | matter | `problem.matter@1` | proposed | pending |
| `INC-016` | `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/INCIDENT.md` | matter_event | `incident.transaction_review@1` | proposed | pending |

## Fact assertions and tension groups

- Atomic fact assertions proposed: 54
- Tension groups (all unresolved, review required): 2
  - `date_or_deadline`: 7 assertions, status unresolved
  - `identity_assertion`: 5 assertions, status unresolved

## Work product version lineage

| Packet version | Source path | Historical | Current review baseline |
|---|---|---|---|
| v2 | `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/packet-v2-facts.json` | True | False |
| v3 | `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/packet-v3-facts.json` | True | False |
| v4 | `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/packet-v4-facts.json` | False | True |

## Source keys observed

- `INC-016` frontmatter keys: amount_disputed, amount_received, artifacts_count, asserted_equitable_beneficial_owner, assigned_to, category, co_owner_profile_slugs, communications_count, controlling_incident_date, controlling_incident_fact, counterparty, counterparty_contact, counterparty_entity_source, created_date, dealer_state, due_date, incident_id, intended_forum_state, owner_profile_path, owner_profile_slug, printed_payment_date, priority, problem_id, profile_role, reference_number, related_profile_slugs, residence_state, resolutions_count, review_note, signer_capacity, slug, state_jurisdiction, status, subcategory, tags, title, transaction_total, unpaid_balance, updated_date, wave1_packet_revision, wave1_packet_status
- `PRB-2026-009` frontmatter keys: category, co_owner_profile_slugs, counterparty, created_date, incidents, owner_profile_path, owner_profile_slug, priority, problem_id, profile_role, related_profile_slugs, root_cause, slug, status, subcategory, tags, title, updated_date
- `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/artifacts/inbound/source-manifest.json` JSON keys: copy_hash_mismatches, files, hash_algorithm, incident_id, intake_folder, pdf_page_count, pdf_source_count, problem_id, processed_date, source_file_count, standalone_image_count
- `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/case-facts.json` JSON keys: counterparty, evidence, execution, incident_id, jurisdiction, next_action, owner, problem_id, record_gaps, transaction, vehicle, wave1_v2, wave1_v3, wave1_v4
- `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/packet-v2-facts.json` JSON keys: authority_record, buyers_order_date, buyers_order_execution_state, cash_total, controlling_incident_dates, dealer_legal_name, dealer_trade_name, incident_id, output_dir, packet_slug, problem_id, prospective_fees, signer_name, source_exhibit_path, source_exhibit_sha256, transaction_specific_condition, trust_name, vehicle_make, vehicle_model, vehicle_year, vin
- `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/packet-v3-facts.json` JSON keys: authority_record, buyers_order_date, buyers_order_execution_state, cash_total, controlling_incident_dates, dealer_legal_name, dealer_price_label, dealer_trade_name, incident_id, incurred_internal_costs, output_dir, packet_slug, packet_version, phase0_findings, preservation_start_date, problem_id, proposed_consideration_method, prospective_fees, signer_capacity, signer_name, source_exhibit_label, source_exhibit_path, source_exhibit_sha256, transaction_specific_condition, trust_name, vehicle_make, vehicle_model, vehicle_year, vin
- `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/packet-v4-facts.json` JSON keys: authority_record, buyers_order_date, buyers_order_execution_state, cash_total, controlling_incident_dates, dealer_legal_name, dealer_price_label, dealer_trade_name, incident_id, incurred_internal_costs, output_dir, packet_slug, packet_version, phase0_findings, preservation_start_date, problem_id, proposed_consideration_method, prospective_fees, signer_capacity, signer_name, source_exhibit_label, source_exhibit_path, source_exhibit_sha256, transaction_specific_condition, trust_name, vehicle_make, vehicle_model, vehicle_year, vin
- `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/superseded/pre-v2-2026-08/generated/2026-08/wave-1-execution-packet/execution-packet-validation.json` JSON keys: continuous_drafted_page_numbering_verified, docx_sha256, drafted_document_page_count, executed, incident_id, jurats_present, lawful_nonresponse_limitation_present, mailed, main_order_sha256, pdf_page_count, pdf_sha256, prospective_fee_schedule_present, required_transaction_fields_present, response_period_present, served, signature_and_notary_fields_blank, source_original_hashes_unchanged, supplemental_order_sha256, validated_date, visual_pages_reviewed
- `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/superseded/pre-v2-2026-08/generated/2026-08/wave-1-review-draft/packet-validation.json` JSON keys: approved_for_execution, arithmetic, executed, execution_documents, incident_id, independent_packet, jurats, mailed, main_order_buyer_signature_state, nonresponse_effect, prospective_fee_schedule, regulation_z_three_day_rescission_claimed, response_period, served, source_exhibits, status, supplemental_order_treated_as_standalone, trust_is_sole_asserted_ebo, trustee_is_individual_ebo, unresolved, validated_date, vin_consistent
- `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/validation-report.json` JSON keys: errors, fact_checks, image_review, incident_id, notices, pdf_review, phase0_wave1_v3_migration, qwen38_review, source_preservation, valid, validated_date, wave1_review_draft, wave1_v2_migration

## Validation report

- Legacy record: `INC-016`
- Valid: True
- Validated date: 2026-08-16
- Errors: 0
- Notices: 5
- Report hash: `debd1c84221a0704e1a1dda8f1795687703b7b1bb0826af2f89ff7ba03ce0b68`
- Owner directions pinned: `incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order/incidents/INC-016-august-2026-liberty-auto-plaza-nissan-purchase-order/correspondence/OWNER-DIRECTIONS.md`

## Count reconciliation

- approval_states_advanced: 0
- excluded_from_plan: []
- execution_states_advanced: 0
- fact_assertions_proposed: 54
- inventoried_files: 112
- mapping_statuses: ['proposed']
- packet_versions: [2, 3, 4]
- plan_source_files: 113
- plan_sources_outside_matter_tree: ['incidents/_incident-registry.md']
- records_proposed: 2
- review_decisions_pending: 2
- skipped_entries: []
- tension_groups: 2

## Semantic verification (pilot TDD section 9 subset)

- [x] `PRB-2026-009` maps to Matter, not Problem
- [x] `INC-016` maps to a transaction-review Matter Event, not a generic Incident
- [x] Tension groups remain unresolved and visible
- [x] Latest packet version is the current review baseline; earlier versions stay historical
- [x] No approval or execution state advanced
- [x] Every mapping stays proposed with no reviewer recorded
- [x] Zero source changes proven by pre/post hash and mtime sweep

## Human mapping review decision

Status: pending. No mapping is approved by this artifact.

- Reviewer: (to be completed by the human reviewer)
- Decision: approve mappings for structured import / request revision
- Decision date: (to be completed by the human reviewer)
