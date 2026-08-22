from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sklegal_hammertime import HammerTimeSubmissionBridge
from sklegal_hammertime.errors import (
    CompletionEvidenceMissingError,
    PromotionRejectedError,
)


class Gateway:
    def __init__(self, poll_result=None, promote_result=None):
        self.calls = []
        self.poll_result = poll_result or {
            "status": "completed",
            "qc": "accepted",
            "completion_evidence": {"receipt": "qc-1"},
        }
        self.promote_result = promote_result or {
            "artifact_reference": "release/artifact-1",
            "release_reference": "release-1",
        }

    def submit(self, **kwargs):
        self.calls.append(("submit", kwargs))
        return "submission-1"

    def poll(self, submission_id):
        self.calls.append(("poll", submission_id))
        return self.poll_result

    def promote(self, submission_id):
        self.calls.append(("promote", submission_id))
        return self.promote_result


class BridgeTests(unittest.TestCase):
    def test_dry_run_is_hash_pinned_and_does_not_call_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "matter.md").write_text("source\n")
            gateway = Gateway()
            bridge = HammerTimeSubmissionBridge(gateway=gateway, fixture_root=root)
            plan = bridge.plan(source_paths=["matter.md"], destination="staging/intake")
            result = bridge.submit(plan)
            self.assertEqual(result.status, "dry_run")
            self.assertEqual(gateway.calls, [])
            self.assertEqual(plan.sources[0].relative_path, "matter.md")

    def test_success_requires_qc_completion_and_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "matter.json").write_text("{}")
            gateway = Gateway()
            bridge = HammerTimeSubmissionBridge(gateway=gateway, fixture_root=root)
            result = bridge.submit(
                bridge.plan(source_paths=["matter.json"], destination="staging"),
                dry_run=False,
            )
            self.assertEqual(result.status, "receipt_verified")
            self.assertEqual(
                [call[0] for call in gateway.calls], ["submit", "poll", "promote"]
            )

    def test_missing_evidence_and_rejected_qc_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "matter.txt").write_text("source")
            plan = HammerTimeSubmissionBridge(
                gateway=Gateway(poll_result={"status": "completed", "qc": "accepted"}),
                fixture_root=root,
            ).plan(source_paths=["matter.txt"], destination="staging")
            with self.assertRaises(CompletionEvidenceMissingError):
                HammerTimeSubmissionBridge(
                    gateway=Gateway(
                        poll_result={"status": "completed", "qc": "accepted"}
                    ),
                    fixture_root=root,
                ).submit(plan, dry_run=False)
            with self.assertRaises(PromotionRejectedError):
                HammerTimeSubmissionBridge(
                    gateway=Gateway(
                        poll_result={
                            "status": "completed",
                            "qc": "rejected",
                            "completion_evidence": {"receipt": "x"},
                        }
                    ),
                    fixture_root=root,
                ).submit(plan, dry_run=False)
