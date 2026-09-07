"""CLI coverage for atomic create-and-claim."""

from click.testing import CliRunner

from skcapstone.card_store import CardStore
from skcapstone.cli import main


def test_coord_create_claim_for_me_reports_exact_fold(tmp_path, monkeypatch):
    monkeypatch.setenv("SKCOORD_CARD_STORE", "1")
    monkeypatch.setattr("skcapstone.active_agent_name", lambda: "maker")

    result = CliRunner().invoke(
        main,
        [
            "coord",
            "create",
            "--home",
            str(tmp_path),
            "--id",
            "a1b2c3e1",
            "--title",
            "Atomic CLI",
            "--claim-for-me",
        ],
    )

    assert result.exit_code == 0, result.output
    card = CardStore(tmp_path).fold("a1b2c3e1")
    assert card is not None
    revision = card.meta["_claim_revision"]
    assert f"owner=maker status=doing claim_revision={revision}" in result.output


def test_coord_create_claim_for_me_fails_before_write_without_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("SKCOORD_CARD_STORE", "1")
    monkeypatch.setattr("skcapstone.active_agent_name", lambda: None)

    result = CliRunner().invoke(
        main,
        [
            "coord",
            "create",
            "--home",
            str(tmp_path),
            "--id",
            "a1b2c3e2",
            "--title",
            "No identity",
            "--claim-for-me",
        ],
    )

    assert result.exit_code != 0
    assert "no active agent could be resolved" in result.output
    assert CardStore(tmp_path).fold("a1b2c3e2") is None


def test_coord_create_help_documents_atomic_example():
    result = CliRunner().invoke(main, ["coord", "create", "--help"])

    assert result.exit_code == 0
    assert "--claim-for-me" in result.output
    assert "without exposing an unowned card" in result.output
