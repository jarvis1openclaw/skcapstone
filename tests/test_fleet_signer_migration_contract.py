import json
from pathlib import Path

CONTRACT = Path(__file__).parents[1] / "config/capauth/fleet-signer-migration.json"


def test_contract_forbids_private_sync_and_requires_scoped_identity() -> None:
    value = json.loads(CONTRACT.read_text())
    assert value["mode"] == "simulation_only"
    assert {"private_key_sync", "shared_agent_key", "passphrase_argument"} <= set(
        value["forbidden"]
    )
    assert value["identity_requirements"]["one_identity_per_node_or_service"]
    assert value["identity_requirements"]["secret_reference_only"]


def test_contract_has_rotation_failover_and_rollback_gates() -> None:
    value = json.loads(CONTRACT.read_text())
    assert value["stages"] == [
        "inventory",
        "enroll",
        "shadow_verify",
        "human_approved_cutover",
        "revoke_legacy",
    ]
    assert "human_approved" in value["failover"]
    assert "last_active_issuer_revision" in value["rollback"]
