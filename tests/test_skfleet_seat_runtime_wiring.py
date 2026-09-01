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


def test_link_and_jarvis_use_distinct_fresh_process_reads() -> None:
    """Assignment observes once, then authorization reads the process again."""

    source = (Path(__file__).parents[1] / "scripts/fleet/skfleet-rotate.py").read_text()
    assignment = source[
        source.index("def _review_assignment(") : source.index("# Load this dependency-free")
    ]
    assert assignment.count("_card_process_snapshot(cid)") == 2
    assert 'if observed_process["sessions"]:' in assignment


def test_mero_tracks_review_lifecycle_without_mutation() -> None:
    """Oversight classifies active, complete, blocked, stale, and waiting."""

    source = (Path(__file__).parents[1] / "scripts/fleet/skfleet-rotate.py").read_text()
    monitor = source[source.index("def _observe_assigned_reviews()") :]
    for state in ("complete", "blocked", "active", "stale", "waiting"):
        assert f'state = "{state}"' in monitor
    assert "MeroObservation(" in monitor
    assert "coord claim" not in monitor
    assert "release-claim" not in monitor
