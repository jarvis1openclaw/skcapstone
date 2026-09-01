"""Static contract for governed seat runtime wiring in fleet rotation."""

from pathlib import Path


def test_rotation_wires_link_jarvis_and_mero_in_order() -> None:
    """Review launches use all three governed runtime stages."""

    source = (Path(__file__).parents[1] / "scripts/fleet/skfleet-rotate.py").read_text()
    link = source.index("recommend_reviewer(")
    jarvis = source.index("authorize_review_launch(", link)
    claim = source.index('claim=subprocess.run([SKC,"coord","claim"', jarvis)
    receipt = source.index("append_review_launch_receipt(", claim)
    mero = source.index("MeroObservation(", receipt)
    assert link < jarvis < claim < receipt < mero


def test_non_review_cards_bypass_assignment() -> None:
    """The integration returns unchanged ownership outside review cards."""

    source = (Path(__file__).parents[1] / "scripts/fleet/skfleet-rotate.py").read_text()
    assert 'if "review" not in {str(label).strip().lower() for label in labels}:' in source
    assert "return reviewer, None, None" in source


def test_dry_run_exits_before_link_writes() -> None:
    """A selector dry run never appends a recommendation or observation."""

    source = (Path(__file__).parents[1] / "scripts/fleet/skfleet-rotate.py").read_text()
    loop = source.index("for _LANE,")
    dry = source.index("if DRY:", loop)
    assignment = source.index("_review_assignment(", dry)
    assert dry < assignment
