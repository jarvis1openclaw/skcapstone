#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
export UV_CACHE_DIR="$repo_root/.tools/uv-cache"
export UV_LINK_MODE=copy
uv="$repo_root/.tools/bin/uv"

run_format_check() {
  "$uv" run --locked ruff format --check scripts tests services packages
  npm run format:check
}

run_lock_check() {
  "$uv" lock --check
  npm ls --all --ignore-scripts >/dev/null
}

run_lint() {
  "$uv" run --locked ruff check scripts tests services packages
  npm run lint
}

run_type_check() {
  "$uv" run --locked mypy \
    scripts/check_capacity.py \
    scripts/evaluate_security_policy.py \
    scripts/security_snapshot.py \
    scripts/check_migrations.py \
    scripts/manage_migrations.py \
    scripts/provision_postgres_principal.py \
    scripts/check_fixture_safety.py \
    scripts/check_secrets.py \
    scripts/clean_room_check.py \
    scripts/audit_doc_haus_provenance.py \
    services packages
  npm run typecheck
  npm run build
}

run_unit_test() {
  "$uv" run --locked python -m unittest -v \
    tests.test_capacity_policy \
    tests.test_security_policy \
    tests.test_doc_haus_provenance \
    tests.test_domain_entities \
    tests.test_persistence_mapping \
    tests.test_capauth_authorization \
    tests.test_capauth_delegation \
    tests.test_capauth_boundaries \
    tests.test_capauth_hotpath_benchmark \
    tests.test_party_conflicts \
    tests.test_policy_corrections \
    tests.test_policy_engine \
    tests.test_audit \
    tests.test_clean_room_check \
    tests.test_validation_subject \
    tests.test_status_page \
    tests.test_external_action_state_docs \
    tests.test_worker_workflows \
    tests.test_foundation
  npm test
}

run_integration_test() {
  "$uv" run --locked python -m unittest -v \
    tests.integration.test_foundation_contract \
    tests.integration.test_domain_contract \
    tests.integration.test_capauth_contract \
    tests.integration.test_persistence_contract
}

run_migration_check() {
  "$uv" run --locked python scripts/check_migrations.py
}

run_fixture_check() {
  "$uv" run --locked python scripts/check_fixture_safety.py
}

run_sbom() {
  mkdir -p build/sbom
  "$uv" export \
    --preview-features sbom-export \
    --locked \
    --all-packages \
    --format cyclonedx1.5 \
    --output-file build/sbom/python.cdx.json \
    >/dev/null
  ./node_modules/.bin/cyclonedx-npm \
    --package-lock-only \
    --output-reproducible \
    --output-format JSON \
    --output-file build/sbom/node.cdx.json \
    package.json
}

run_secret_scan() {
  "$uv" run --locked python scripts/check_secrets.py
}

run_vulnerability_scan() {
  mkdir -p build/audit
  capauth_requirement='capauth @ git+https://github.com/smilinTux/capauth.git@183c04a7c623e8abcf37bd705bf8bca1deb4a364'
  "$uv" export \
    --locked \
    --all-packages \
    --no-emit-workspace \
    --format requirements-txt \
    --output-file build/audit/python-requirements.txt \
    >/dev/null
  capauth_match_count=$(grep -Fxc \
    "$capauth_requirement" \
    build/audit/python-requirements.txt || true)
  if [[ "$capauth_match_count" != "1" ]]; then
    echo "locked CapAuth VCS requirement is missing or ambiguous" >&2
    return 1
  fi
  grep -Fvx \
    "$capauth_requirement" \
    build/audit/python-requirements.txt \
    >build/audit/python-registry-requirements.txt
  "$uv" run --locked pip-audit \
    --strict \
    --require-hashes \
    --disable-pip \
    --requirement build/audit/python-registry-requirements.txt
  printf '%s\n' 'capauth==0.3.1' >build/audit/capauth-release-requirement.txt
  "$uv" run --locked pip-audit \
    --strict \
    --no-deps \
    --disable-pip \
    --requirement build/audit/capauth-release-requirement.txt
  npm audit --audit-level=high
}

run_design_hashes() {
  sha256sum --check docs/approval/DESIGN-HASHES.sha256
}

run_license_audit() {
  "$uv" run --locked python scripts/audit_doc_haus_provenance.py validate \
    --manifest config/provenance/doc-haus-audit.json \
    --rights-inventory docs/evidence/licensing/DOC-HAUS-RIGHTS-INVENTORY.json \
    --sbom docs/evidence/licensing/DOC-HAUS-SBOM.cdx.json \
    --project-root "$repo_root"
  node scripts/validate_cyclonedx.mjs \
    docs/evidence/licensing/DOC-HAUS-SBOM.cdx.json
}

run_license_audit_live() {
  if [[ -z "${DOC_HAUS_ROOT:-}" ]]; then
    echo "DOC_HAUS_ROOT is required for live license reconciliation" >&2
    return 2
  fi
  run_license_audit
  "$uv" run --locked python scripts/audit_doc_haus_provenance.py validate \
    --manifest config/provenance/doc-haus-audit.json \
    --rights-inventory docs/evidence/licensing/DOC-HAUS-RIGHTS-INVENTORY.json \
    --sbom docs/evidence/licensing/DOC-HAUS-SBOM.cdx.json \
    --project-root "$repo_root" \
    --target "$DOC_HAUS_ROOT"
}

run_compose_check() {
  ./scripts/dev_dependencies.sh check
}

run_all() {
  run_design_hashes
  run_compose_check
  run_lock_check
  run_license_audit
  run_format_check
  run_lint
  run_type_check
  run_unit_test
  run_integration_test
  run_migration_check
  run_fixture_check
  run_sbom
  run_secret_scan
  run_vulnerability_scan
}

case "${1:-}" in
  all) run_all ;;
  format-check) run_format_check ;;
  lint) run_lint ;;
  type-check) run_type_check ;;
  unit-test) run_unit_test ;;
  integration-test) run_integration_test ;;
  migration-check) run_migration_check ;;
  fixture-check) run_fixture_check ;;
  license-audit) run_license_audit ;;
  license-audit-live) run_license_audit_live ;;
  sbom) run_sbom ;;
  secret-scan) run_secret_scan ;;
  vulnerability-scan) run_vulnerability_scan ;;
  *) echo "unknown check: ${1:-}" >&2; exit 2 ;;
esac
