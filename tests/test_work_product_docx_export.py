from __future__ import annotations

import asyncio
import hashlib
import io
import unittest
import zipfile
from datetime import UTC, datetime
from uuid import UUID
from xml.etree import ElementTree as ET

from sklegal_worker import (
    DocumentParagraph,
    DocumentRun,
    InMemoryDocumentExportStore,
    PolicyDeniedError,
    StaticExportApprovalGate,
    WorkflowInvariantError,
    WorkProductExportApproval,
    WorkProductExporter,
    WorkProductExportRequest,
    accept_tracked_changes,
    docx_paragraph_text,
    make_work_product_document,
    render_tracked_docx,
    validate_pdf_preview,
)
from sklegal_worker.activities import WorkerActivities

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
TENANT_ID = UUID("44000000-0000-4000-8000-000000000001")
MATTER_ID = UUID("44000000-0000-4000-8000-000000000002")
WORK_PRODUCT_ID = UUID("44000000-0000-4000-8000-000000000003")
VERSION_1_ID = UUID("44000000-0000-4000-8000-000000000004")
VERSION_2_ID = UUID("44000000-0000-4000-8000-000000000005")
VERSION_3_ID = UUID("44000000-0000-4000-8000-000000000006")
APPROVAL_ID = UUID("44000000-0000-4000-8000-000000000007")


def paragraph(
    text: str,
    *,
    paragraph_style: str = "BodyText",
    run_style: str | None = None,
) -> DocumentParagraph:
    return DocumentParagraph(
        runs=(DocumentRun(text=text, style_id=run_style),),
        style_id=paragraph_style,
    )


def document(
    version_number: int,
    version_id: UUID,
    paragraphs: tuple[DocumentParagraph, ...],
):
    return make_work_product_document(
        tenant_id=TENANT_ID,
        matter_id=MATTER_ID,
        work_product_id=WORK_PRODUCT_ID,
        version_id=version_id,
        version_number=version_number,
        paragraphs=paragraphs,
    )


def base_and_current():
    base = document(
        1,
        VERSION_1_ID,
        (
            paragraph("Motion for Relief", paragraph_style="Heading1"),
            paragraph("The hearing is June 1.", run_style="CitationText"),
        ),
    )
    current = document(
        2,
        VERSION_2_ID,
        (
            paragraph("Motion for Relief", paragraph_style="Heading1"),
            paragraph("The hearing is June 12.", run_style="CitationText"),
            paragraph("Respectfully submitted.", run_style="SignatureText"),
        ),
    )
    return base, current


class OnePagePdfRenderer:
    """Hermetic real PDF renderer used to exercise preview validation."""

    def __init__(self) -> None:
        self.rendered_text: tuple[str, ...] = ()

    def render(self, docx: bytes) -> bytes:
        self.rendered_text = docx_paragraph_text(accept_tracked_changes(docx))
        return (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type /Pages /Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type /Page /Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n"
            b"trailer<</Root 1 0 R /Size 4>>\nstartxref\n0\n%%EOF\n"
        )


class DocxRoundTripTests(unittest.TestCase):
    def test_docx_contains_genuine_tracked_changes_and_accepts_to_current(self) -> None:
        base, current = base_and_current()
        rendered = render_tracked_docx(base, current)
        self.assertTrue(rendered.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(rendered)) as package:
            document_xml = ET.fromstring(package.read("word/document.xml"))
            settings_xml = ET.fromstring(package.read("word/settings.xml"))
        self.assertGreaterEqual(len(document_xml.findall(f".//{{{W_NS}}}del")), 1)
        self.assertGreaterEqual(len(document_xml.findall(f".//{{{W_NS}}}ins")), 1)
        self.assertIsNotNone(settings_xml.find(f"{{{W_NS}}}trackRevisions"))
        self.assertEqual(
            tuple(item.text for item in current.paragraphs),
            docx_paragraph_text(rendered),
        )
        accepted = accept_tracked_changes(rendered)
        self.assertEqual(
            tuple(item.text for item in current.paragraphs),
            docx_paragraph_text(accepted),
        )
        with zipfile.ZipFile(io.BytesIO(accepted)) as package:
            accepted_xml = ET.fromstring(package.read("word/document.xml"))
            accepted_settings = ET.fromstring(package.read("word/settings.xml"))
        self.assertEqual([], accepted_xml.findall(f".//{{{W_NS}}}del"))
        self.assertEqual([], accepted_xml.findall(f".//{{{W_NS}}}ins"))
        self.assertEqual(
            len(current.paragraphs),
            len(accepted_xml.findall(f".//{{{W_NS}}}p")),
        )
        self.assertIsNone(accepted_settings.find(f"{{{W_NS}}}trackRevisions"))

    def test_paragraph_and_run_styles_survive_export_and_acceptance(self) -> None:
        base, current = base_and_current()
        accepted = accept_tracked_changes(render_tracked_docx(base, current))
        with zipfile.ZipFile(io.BytesIO(accepted)) as package:
            document_xml = ET.fromstring(package.read("word/document.xml"))
            styles_xml = ET.fromstring(package.read("word/styles.xml"))
        paragraph_styles = {
            item.get(f"{{{W_NS}}}val")
            for item in document_xml.findall(f".//{{{W_NS}}}pStyle")
        }
        run_styles = {
            item.get(f"{{{W_NS}}}val")
            for item in document_xml.findall(f".//{{{W_NS}}}rStyle")
        }
        declared_styles = {
            item.get(f"{{{W_NS}}}styleId")
            for item in styles_xml.findall(f"{{{W_NS}}}style")
        }
        self.assertEqual({"Heading1", "BodyText"}, paragraph_styles)
        self.assertEqual({"CitationText", "SignatureText"}, run_styles)
        self.assertTrue(paragraph_styles | run_styles <= declared_styles)

    def test_export_is_byte_deterministic(self) -> None:
        base, current = base_and_current()
        self.assertEqual(
            render_tracked_docx(base, current),
            render_tracked_docx(base, current),
        )


class ExactApprovalActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base, self.current = base_and_current()
        self.store = InMemoryDocumentExportStore()
        self.store.add_document("artifact/base-v1", self.base)
        self.store.add_document("artifact/current-v2", self.current)
        self.docx = render_tracked_docx(self.base, self.current)
        self.docx_sha256 = hashlib.sha256(self.docx).hexdigest()
        self.request = WorkProductExportRequest(
            idempotency_key="work-product-export-v2",
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            work_product_id=WORK_PRODUCT_ID,
            version_id=VERSION_2_ID,
            version_number=2,
            base_artifact_ref="artifact/base-v1",
            base_content_sha256=self.base.content_sha256,
            current_artifact_ref="artifact/current-v2",
            current_content_sha256=self.current.content_sha256,
            expected_docx_sha256=self.docx_sha256,
            approval_id=APPROVAL_ID,
        )
        self.approval = WorkProductExportApproval(
            approval_id=APPROVAL_ID,
            tenant_id=TENANT_ID,
            matter_id=MATTER_ID,
            work_product_id=WORK_PRODUCT_ID,
            version_id=VERSION_2_ID,
            version_number=2,
            content_sha256=self.current.content_sha256,
            docx_sha256=self.docx_sha256,
        )

    def exporter(self, renderer: OnePagePdfRenderer | None = None):
        return WorkProductExporter(
            store=self.store,
            approval_gate=StaticExportApprovalGate({APPROVAL_ID: self.approval}),
            preview_renderer=renderer or OnePagePdfRenderer(),
            clock=lambda: AT,
        )

    def test_temporal_activity_records_exact_hash_and_validated_pdf_preview(
        self,
    ) -> None:
        renderer = OnePagePdfRenderer()
        activities = WorkerActivities(
            work_product_exporter=self.exporter(renderer),
            clock=lambda: AT,
            heartbeat_seconds=None,
        )
        result = asyncio.run(activities.export_tracked_work_product(self.request))
        self.assertEqual(self.docx_sha256, result.docx_sha256)
        self.assertEqual(1, result.preview_page_count)
        self.assertEqual(1, self.store.recorded_count)
        self.assertEqual(
            tuple(item.text for item in self.current.paragraphs),
            renderer.rendered_text,
        )
        preview = self.store.artifact_bytes(result.preview_artifact_ref)
        self.assertEqual(
            1,
            validate_pdf_preview(preview, docx_sha256=self.docx_sha256),
        )
        repeated = asyncio.run(activities.export_tracked_work_product(self.request))
        self.assertEqual(result, repeated)
        self.assertEqual(1, self.store.recorded_count)

    def test_edit_after_approval_requires_successor_validation_and_approval(
        self,
    ) -> None:
        edited = document(
            3,
            VERSION_3_ID,
            (*self.current.paragraphs, paragraph("New unapproved paragraph.")),
        )
        self.store.add_document("artifact/current-v2", edited)
        with self.assertRaisesRegex(
            WorkflowInvariantError,
            "no longer matches the requested exact version",
        ):
            self.exporter().export(self.request)

        self.store.add_document("artifact/current-v3", edited)
        edited_docx = render_tracked_docx(self.current, edited)
        edited_request = self.request.model_copy(
            update={
                "idempotency_key": "work-product-export-v3",
                "base_artifact_ref": "artifact/current-v2-approved",
                "base_content_sha256": self.current.content_sha256,
                "current_artifact_ref": "artifact/current-v3",
                "current_content_sha256": edited.content_sha256,
                "version_id": VERSION_3_ID,
                "version_number": 3,
                "expected_docx_sha256": hashlib.sha256(edited_docx).hexdigest(),
            }
        )
        self.store.add_document("artifact/current-v2-approved", self.current)
        with self.assertRaisesRegex(PolicyDeniedError, "does not bind"):
            self.exporter().export(edited_request)

    def test_wrong_expected_docx_hash_fails_before_preview_or_persistence(self) -> None:
        wrong = self.request.model_copy(update={"expected_docx_sha256": "f" * 64})
        with self.assertRaisesRegex(WorkflowInvariantError, "hash differs"):
            self.exporter().export(wrong)
        self.assertEqual(0, self.store.recorded_count)

    def test_pdf_without_page_fails_closed(self) -> None:
        class EmptyPdfRenderer:
            def render(self, docx: bytes) -> bytes:
                del docx
                return b"%PDF-1.4\n%%EOF\n"

        with self.assertRaisesRegex(WorkflowInvariantError, "has no pages"):
            self.exporter(EmptyPdfRenderer()).export(self.request)  # type: ignore[arg-type]
        self.assertEqual(0, self.store.recorded_count)
