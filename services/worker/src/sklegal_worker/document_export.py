"""Deterministic DOCX tracked-change export and PDF preview validation.

This is an independent OOXML implementation. It does not use the quarantined
doc.haus Docxodus adapter. Temporal payloads carry references and hashes only;
the injected store resolves protected Work Product content inside the activity.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol
from uuid import UUID
from xml.etree import ElementTree as ET

from .errors import PolicyDeniedError, WorkflowInvariantError
from .models import WorkProductExportRequest, WorkProductExportResult

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS = "http://www.w3.org/XML/1998/namespace"
PDF_BINDING_PREFIX = b"% SKLegal-Source-DOCX-SHA256: "
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

ET.register_namespace("w", W_NS)


def _w(local: str) -> str:
    return f"{{{W_NS}}}{local}"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class DocumentRun:
    """One text run with its named character style."""

    text: str
    style_id: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentParagraph:
    """One paragraph with exact ordered runs and a named paragraph style."""

    runs: tuple[DocumentRun, ...]
    style_id: str = "Normal"

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass(frozen=True, slots=True)
class WorkProductDocument:
    """Activity-side protected document snapshot bound to one exact version."""

    tenant_id: UUID
    matter_id: UUID
    work_product_id: UUID
    version_id: UUID
    version_number: int
    paragraphs: tuple[DocumentParagraph, ...]
    content_sha256: str

    def __post_init__(self) -> None:
        if self.version_number < 1:
            raise ValueError("document version_number must be positive")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("document content_sha256 must be lowercase SHA-256")
        if document_content_sha256(self) != self.content_sha256:
            raise ValueError("document content does not match content_sha256")


def document_content_sha256(document: WorkProductDocument) -> str:
    """Hash the canonical protected content and style projection."""

    return _paragraph_content_sha256(document.paragraphs)


def _paragraph_content_sha256(
    paragraphs: tuple[DocumentParagraph, ...],
) -> str:
    payload = [
        {
            "style_id": paragraph.style_id,
            "runs": [
                {"style_id": run.style_id, "text": run.text} for run in paragraph.runs
            ],
        }
        for paragraph in paragraphs
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256(encoded)


def make_work_product_document(
    *,
    tenant_id: UUID,
    matter_id: UUID,
    work_product_id: UUID,
    version_id: UUID,
    version_number: int,
    paragraphs: tuple[DocumentParagraph, ...],
) -> WorkProductDocument:
    """Build a document snapshot with its canonical exact content hash."""

    return WorkProductDocument(
        tenant_id=tenant_id,
        matter_id=matter_id,
        work_product_id=work_product_id,
        version_id=version_id,
        version_number=version_number,
        paragraphs=paragraphs,
        content_sha256=_paragraph_content_sha256(paragraphs),
    )


@dataclass(frozen=True, slots=True)
class WorkProductExportApproval:
    """Approved exact version and deterministic DOCX digest."""

    approval_id: UUID
    tenant_id: UUID
    matter_id: UUID
    work_product_id: UUID
    version_id: UUID
    version_number: int
    content_sha256: str
    docx_sha256: str
    status: str = "approved"


class ExportApprovalGate(Protocol):
    def exact_approval(self, approval_id: UUID) -> WorkProductExportApproval: ...


class StaticExportApprovalGate:
    """Fail-closed approval lookup for tests and immutable snapshots."""

    def __init__(self, approvals: Mapping[UUID, WorkProductExportApproval]) -> None:
        self._approvals = dict(approvals)

    def exact_approval(self, approval_id: UUID) -> WorkProductExportApproval:
        approval = self._approvals.get(approval_id)
        if approval is None or approval.status != "approved":
            raise PolicyDeniedError("Work Product export Approval is not approved")
        return approval


class DocumentExportStore(Protocol):
    """Protected source lookup and idempotent derived-artifact persistence."""

    def load_document(self, artifact_ref: str) -> WorkProductDocument: ...

    def record_export(
        self,
        request: WorkProductExportRequest,
        *,
        docx: bytes,
        preview_pdf: bytes,
        page_count: int,
        completed_at: datetime,
    ) -> WorkProductExportResult: ...


class InMemoryDocumentExportStore:
    """Hermetic store with conflict-detecting idempotency semantics."""

    def __init__(self) -> None:
        self._documents: dict[str, WorkProductDocument] = {}
        self._results: dict[str, WorkProductExportResult] = {}
        self._fingerprints: dict[str, tuple[str, str]] = {}
        self._artifacts: dict[str, bytes] = {}

    def add_document(self, artifact_ref: str, document: WorkProductDocument) -> None:
        self._documents[artifact_ref] = document

    def load_document(self, artifact_ref: str) -> WorkProductDocument:
        try:
            return self._documents[artifact_ref]
        except KeyError as exc:
            raise WorkflowInvariantError(
                f"document artifact reference not found: {artifact_ref}"
            ) from exc

    def artifact_bytes(self, artifact_ref: str) -> bytes:
        return self._artifacts[artifact_ref]

    @property
    def recorded_count(self) -> int:
        return len(self._results)

    def record_export(
        self,
        request: WorkProductExportRequest,
        *,
        docx: bytes,
        preview_pdf: bytes,
        page_count: int,
        completed_at: datetime,
    ) -> WorkProductExportResult:
        fingerprint = (_sha256(docx), _sha256(preview_pdf))
        existing = self._results.get(request.idempotency_key)
        if existing is not None:
            if self._fingerprints[request.idempotency_key] != fingerprint:
                raise WorkflowInvariantError(
                    "export idempotency key reused with different artifacts"
                )
            return existing
        docx_ref = f"derived/{request.version_id}/{fingerprint[0]}.docx"
        preview_ref = f"derived/{request.version_id}/{fingerprint[1]}.pdf"
        result = WorkProductExportResult(
            idempotency_key=request.idempotency_key,
            work_product_id=request.work_product_id,
            version_id=request.version_id,
            version_number=request.version_number,
            docx_artifact_ref=docx_ref,
            docx_sha256=fingerprint[0],
            preview_artifact_ref=preview_ref,
            preview_pdf_sha256=fingerprint[1],
            preview_page_count=page_count,
            approval_id=request.approval_id,
            completed_at=completed_at,
        )
        self._artifacts[docx_ref] = docx
        self._artifacts[preview_ref] = preview_pdf
        self._fingerprints[request.idempotency_key] = fingerprint
        self._results[request.idempotency_key] = result
        return result


class PdfPreviewRenderer(Protocol):
    def render(self, docx: bytes) -> bytes: ...


class LibreOfficePreviewRenderer:
    """Render DOCX with an isolated headless LibreOffice user profile."""

    def __init__(self, executable: str = "libreoffice", timeout_seconds: int = 60):
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def render(self, docx: bytes) -> bytes:
        executable = shutil.which(self._executable)
        if executable is None:
            raise WorkflowInvariantError("LibreOffice preview renderer unavailable")
        with tempfile.TemporaryDirectory(prefix="sklegal-docx-preview-") as raw:
            root = Path(raw)
            source = root / "work-product.docx"
            output = root / "output"
            profile = root / "profile"
            output.mkdir()
            profile.mkdir()
            source.write_bytes(docx)
            command = [
                executable,
                "--headless",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output),
                str(source),
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    timeout=self._timeout_seconds,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise WorkflowInvariantError(
                    "LibreOffice preview rendering failed"
                ) from exc
            rendered = output / "work-product.pdf"
            if completed.returncode != 0 or not rendered.is_file():
                raise WorkflowInvariantError(
                    "LibreOffice preview rendering did not produce a PDF"
                )
            return rendered.read_bytes()


def _xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _text_element(parent: ET.Element, name: str, text: str) -> None:
    element = ET.SubElement(parent, _w(name))
    if text[:1].isspace() or text[-1:].isspace():
        element.set(f"{{{XML_NS}}}space", "preserve")
    element.text = text


def _run_element(
    text: str, *, style_id: str | None, deleted: bool = False
) -> ET.Element:
    run = ET.Element(_w("r"))
    if style_id is not None:
        properties = ET.SubElement(run, _w("rPr"))
        ET.SubElement(properties, _w("rStyle"), {_w("val"): style_id})
    _text_element(run, "delText" if deleted else "t", text)
    return run


def _style_at(paragraph: DocumentParagraph, offset: int) -> str | None:
    cursor = 0
    for run in paragraph.runs:
        limit = cursor + len(run.text)
        if offset < limit:
            return run.style_id
        cursor = limit
    return paragraph.runs[-1].style_id if paragraph.runs else None


def _tokens(text: str) -> list[str]:
    return re.findall(r"\s+|[^\s]+", text)


def _append_plain_runs(parent: ET.Element, paragraph: DocumentParagraph) -> None:
    for run in paragraph.runs:
        if run.text:
            parent.append(_run_element(run.text, style_id=run.style_id))


def _append_changed_runs(
    parent: ET.Element,
    old: DocumentParagraph,
    new: DocumentParagraph,
    *,
    author: str,
    revision_date: str,
    next_change_id: Callable[[], int],
) -> None:
    old_tokens = _tokens(old.text)
    new_tokens = _tokens(new.text)
    matcher = SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    old_offsets: list[int] = []
    new_offsets: list[int] = []
    cursor = 0
    for token in old_tokens:
        old_offsets.append(cursor)
        cursor += len(token)
    cursor = 0
    for token in new_tokens:
        new_offsets.append(cursor)
        cursor += len(token)
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation == "equal":
            for index in range(new_start, new_end):
                parent.append(
                    _run_element(
                        new_tokens[index],
                        style_id=_style_at(new, new_offsets[index]),
                    )
                )
        if operation in {"delete", "replace"}:
            change = ET.SubElement(
                parent,
                _w("del"),
                {
                    _w("id"): str(next_change_id()),
                    _w("author"): author,
                    _w("date"): revision_date,
                },
            )
            for index in range(old_start, old_end):
                change.append(
                    _run_element(
                        old_tokens[index],
                        style_id=_style_at(old, old_offsets[index]),
                        deleted=True,
                    )
                )
        if operation in {"insert", "replace"}:
            change = ET.SubElement(
                parent,
                _w("ins"),
                {
                    _w("id"): str(next_change_id()),
                    _w("author"): author,
                    _w("date"): revision_date,
                },
            )
            for index in range(new_start, new_end):
                change.append(
                    _run_element(
                        new_tokens[index],
                        style_id=_style_at(new, new_offsets[index]),
                    )
                )


def _paragraph_element(
    paragraph: DocumentParagraph,
    *,
    old: DocumentParagraph | None = None,
    author: str,
    revision_date: str,
    next_change_id: Callable[[], int],
    deleted: bool = False,
) -> ET.Element:
    element = ET.Element(_w("p"))
    properties = ET.SubElement(element, _w("pPr"))
    ET.SubElement(properties, _w("pStyle"), {_w("val"): paragraph.style_id})
    if old is not None:
        _append_changed_runs(
            element,
            old,
            paragraph,
            author=author,
            revision_date=revision_date,
            next_change_id=next_change_id,
        )
    elif deleted:
        mark_properties = ET.SubElement(properties, _w("rPr"))
        ET.SubElement(
            mark_properties,
            _w("del"),
            {
                _w("id"): str(next_change_id()),
                _w("author"): author,
                _w("date"): revision_date,
            },
        )
        change = ET.SubElement(
            element,
            _w("del"),
            {
                _w("id"): str(next_change_id()),
                _w("author"): author,
                _w("date"): revision_date,
            },
        )
        for run in paragraph.runs:
            change.append(_run_element(run.text, style_id=run.style_id, deleted=True))
    else:
        _append_plain_runs(element, paragraph)
    return element


def _document_xml(
    base: WorkProductDocument,
    current: WorkProductDocument,
    *,
    author: str,
    revision_date: str,
) -> bytes:
    document = ET.Element(_w("document"))
    body = ET.SubElement(document, _w("body"))
    change_id = 0

    def next_change_id() -> int:
        nonlocal change_id
        change_id += 1
        return change_id

    matcher = SequenceMatcher(
        a=[paragraph.text for paragraph in base.paragraphs],
        b=[paragraph.text for paragraph in current.paragraphs],
        autojunk=False,
    )
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation == "equal":
            for index in range(new_start, new_end):
                body.append(
                    _paragraph_element(
                        current.paragraphs[index],
                        author=author,
                        revision_date=revision_date,
                        next_change_id=next_change_id,
                    )
                )
            continue
        if operation == "replace":
            paired = min(old_end - old_start, new_end - new_start)
            for offset in range(paired):
                body.append(
                    _paragraph_element(
                        current.paragraphs[new_start + offset],
                        old=base.paragraphs[old_start + offset],
                        author=author,
                        revision_date=revision_date,
                        next_change_id=next_change_id,
                    )
                )
            old_start += paired
            new_start += paired
        for index in range(old_start, old_end):
            body.append(
                _paragraph_element(
                    base.paragraphs[index],
                    author=author,
                    revision_date=revision_date,
                    next_change_id=next_change_id,
                    deleted=True,
                )
            )
        for index in range(new_start, new_end):
            paragraph = _paragraph_element(
                current.paragraphs[index],
                author=author,
                revision_date=revision_date,
                next_change_id=next_change_id,
            )
            runs = list(paragraph)[1:]
            properties = paragraph.find(_w("pPr"))
            if properties is None:
                raise WorkflowInvariantError("inserted paragraph lacks properties")
            mark_properties = ET.SubElement(properties, _w("rPr"))
            ET.SubElement(
                mark_properties,
                _w("ins"),
                {
                    _w("id"): str(next_change_id()),
                    _w("author"): author,
                    _w("date"): revision_date,
                },
            )
            for run in runs:
                paragraph.remove(run)
            insertion = ET.SubElement(
                paragraph,
                _w("ins"),
                {
                    _w("id"): str(next_change_id()),
                    _w("author"): author,
                    _w("date"): revision_date,
                },
            )
            for run in runs:
                insertion.append(run)
            body.append(paragraph)
    section = ET.SubElement(body, _w("sectPr"))
    ET.SubElement(section, _w("pgSz"), {_w("w"): "12240", _w("h"): "15840"})
    ET.SubElement(
        section,
        _w("pgMar"),
        {
            _w("top"): "1440",
            _w("right"): "1440",
            _w("bottom"): "1440",
            _w("left"): "1440",
        },
    )
    return _xml_bytes(document)


def _styles_xml(documents: tuple[WorkProductDocument, ...]) -> bytes:
    root = ET.Element(_w("styles"))
    paragraph_styles = {"Normal"}
    character_styles: set[str] = set()
    for document in documents:
        for paragraph in document.paragraphs:
            paragraph_styles.add(paragraph.style_id)
            character_styles.update(
                run.style_id for run in paragraph.runs if run.style_id is not None
            )
    for style_id in sorted(paragraph_styles):
        style = ET.SubElement(
            root,
            _w("style"),
            {_w("type"): "paragraph", _w("styleId"): style_id},
        )
        ET.SubElement(style, _w("name"), {_w("val"): style_id})
    for style_id in sorted(character_styles):
        style = ET.SubElement(
            root,
            _w("style"),
            {_w("type"): "character", _w("styleId"): style_id},
        )
        ET.SubElement(style, _w("name"), {_w("val"): style_id})
    return _xml_bytes(root)


def _settings_xml() -> bytes:
    settings = ET.Element(_w("settings"))
    ET.SubElement(settings, _w("trackRevisions"))
    return _xml_bytes(settings)


def _content_types_xml() -> bytes:
    root = ET.Element(f"{{{CONTENT_TYPES_NS}}}Types")
    ET.SubElement(
        root,
        f"{{{CONTENT_TYPES_NS}}}Default",
        {
            "Extension": "rels",
            "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
        },
    )
    ET.SubElement(
        root,
        f"{{{CONTENT_TYPES_NS}}}Default",
        {"Extension": "xml", "ContentType": "application/xml"},
    )
    for part, content_type in (
        (
            "/word/document.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        ),
        (
            "/word/styles.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
        ),
        (
            "/word/settings.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
        ),
    ):
        ET.SubElement(
            root,
            f"{{{CONTENT_TYPES_NS}}}Override",
            {"PartName": part, "ContentType": content_type},
        )
    return _xml_bytes(root)


def _relationships_xml() -> bytes:
    root = ET.Element(f"{{{REL_NS}}}Relationships")
    ET.SubElement(
        root,
        f"{{{REL_NS}}}Relationship",
        {
            "Id": "rId1",
            "Type": f"{DOC_REL_NS}/officeDocument",
            "Target": "word/document.xml",
        },
    )
    return _xml_bytes(root)


def _document_relationships_xml() -> bytes:
    root = ET.Element(f"{{{REL_NS}}}Relationships")
    ET.SubElement(
        root,
        f"{{{REL_NS}}}Relationship",
        {
            "Id": "rId1",
            "Type": f"{DOC_REL_NS}/styles",
            "Target": "styles.xml",
        },
    )
    ET.SubElement(
        root,
        f"{{{REL_NS}}}Relationship",
        {
            "Id": "rId2",
            "Type": f"{DOC_REL_NS}/settings",
            "Target": "settings.xml",
        },
    )
    return _xml_bytes(root)


def _write_docx(parts: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    timestamp = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name in sorted(parts):
            info = zipfile.ZipInfo(name, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            package.writestr(info, parts[name])
    return output.getvalue()


def render_tracked_docx(
    base: WorkProductDocument,
    current: WorkProductDocument,
    *,
    author: str = "SKLegal",
    revision_date: str = "2000-01-01T00:00:00Z",
) -> bytes:
    """Create deterministic WordprocessingML with genuine insert/delete markup."""

    if (
        base.tenant_id != current.tenant_id
        or base.matter_id != current.matter_id
        or base.work_product_id != current.work_product_id
    ):
        raise WorkflowInvariantError(
            "tracked-change documents must share Tenant, Matter, and Work Product"
        )
    if current.version_number <= base.version_number:
        raise WorkflowInvariantError("tracked-change current version must be newer")
    parts = {
        "[Content_Types].xml": _content_types_xml(),
        "_rels/.rels": _relationships_xml(),
        "word/_rels/document.xml.rels": _document_relationships_xml(),
        "word/document.xml": _document_xml(
            base,
            current,
            author=author,
            revision_date=revision_date,
        ),
        "word/settings.xml": _settings_xml(),
        "word/styles.xml": _styles_xml((base, current)),
    }
    return _write_docx(parts)


def _rewrite_docx(docx: bytes, replacements: Mapping[str, bytes]) -> bytes:
    with zipfile.ZipFile(io.BytesIO(docx)) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    parts.update(replacements)
    return _write_docx(parts)


def accept_tracked_changes(docx: bytes) -> bytes:
    """Return a valid DOCX with deletions removed and insertions accepted."""

    try:
        with zipfile.ZipFile(io.BytesIO(docx)) as package:
            document = ET.fromstring(package.read("word/document.xml"))
            settings = ET.fromstring(package.read("word/settings.xml"))
    except (KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise WorkflowInvariantError("DOCX package is invalid") from exc

    def accept(parent: ET.Element) -> None:
        for child in list(parent):
            if (
                child.tag == _w("p")
                and child.find(f"{_w('pPr')}/{_w('rPr')}/{_w('del')}") is not None
            ):
                parent.remove(child)
                continue
            if child.tag == _w("del"):
                parent.remove(child)
            elif child.tag == _w("ins"):
                index = list(parent).index(child)
                parent.remove(child)
                for nested in list(child):
                    parent.insert(index, nested)
                    index += 1
            else:
                accept(child)

    accept(document)
    for tracked in list(settings.findall(_w("trackRevisions"))):
        settings.remove(tracked)
    return _rewrite_docx(
        docx,
        {
            "word/document.xml": _xml_bytes(document),
            "word/settings.xml": _xml_bytes(settings),
        },
    )


def docx_paragraph_text(
    docx: bytes, *, include_deleted: bool = False
) -> tuple[str, ...]:
    """Read visible paragraph text for round-trip and acceptance validation."""

    try:
        with zipfile.ZipFile(io.BytesIO(docx)) as package:
            root = ET.fromstring(package.read("word/document.xml"))
    except (KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise WorkflowInvariantError("DOCX package is invalid") from exc
    paragraphs: list[str] = []
    for paragraph in root.iter(_w("p")):
        chunks: list[str] = []

        def visit(element: ET.Element, deleted: bool = False) -> None:
            now_deleted = deleted or element.tag == _w("del")
            if element.tag == _w("t") and not now_deleted:
                chunks.append(element.text or "")
            elif element.tag == _w("delText") and include_deleted:
                chunks.append(element.text or "")
            for child in element:
                visit(child, now_deleted)

        visit(paragraph)
        if chunks:
            paragraphs.append("".join(chunks))
    return tuple(paragraphs)


def bind_pdf_preview(pdf: bytes, docx_sha256: str) -> bytes:
    """Bind a rendered PDF to the exact DOCX with a PDF comment marker."""

    if not re.fullmatch(r"[0-9a-f]{64}", docx_sha256):
        raise ValueError("docx_sha256 must be lowercase SHA-256")
    eof = pdf.rfind(b"%%EOF")
    if eof < 0:
        raise WorkflowInvariantError("rendered preview lacks PDF EOF marker")
    marker = PDF_BINDING_PREFIX + docx_sha256.encode("ascii") + b"\n"
    return pdf[:eof] + marker + pdf[eof:]


def validate_pdf_preview(pdf: bytes, *, docx_sha256: str) -> int:
    """Validate PDF structure and its exact DOCX derivation marker."""

    if not pdf.startswith(b"%PDF-") or b"%%EOF" not in pdf[-1024:]:
        raise WorkflowInvariantError("rendered preview is not a complete PDF")
    marker = PDF_BINDING_PREFIX + docx_sha256.encode("ascii")
    if marker not in pdf:
        raise WorkflowInvariantError("PDF preview is not bound to the exact DOCX")
    page_count = len(re.findall(rb"/Type\s*/Page(?!s)\b", pdf))
    if page_count < 1:
        raise WorkflowInvariantError("rendered PDF preview has no pages")
    return page_count


class WorkProductExporter:
    """Exact-hash export service called by the Temporal activity."""

    def __init__(
        self,
        *,
        store: DocumentExportStore,
        approval_gate: ExportApprovalGate,
        preview_renderer: PdfPreviewRenderer,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._approval_gate = approval_gate
        self._preview_renderer = preview_renderer
        self._clock = clock

    def export(self, request: WorkProductExportRequest) -> WorkProductExportResult:
        base = self._store.load_document(request.base_artifact_ref)
        current = self._store.load_document(request.current_artifact_ref)
        if base.content_sha256 != request.base_content_sha256:
            raise WorkflowInvariantError("base document changed after export request")
        expected_current = (
            request.tenant_id,
            request.matter_id,
            request.work_product_id,
            request.version_id,
            request.version_number,
            request.current_content_sha256,
        )
        actual_current = (
            current.tenant_id,
            current.matter_id,
            current.work_product_id,
            current.version_id,
            current.version_number,
            current.content_sha256,
        )
        if actual_current != expected_current:
            raise WorkflowInvariantError(
                "current Work Product no longer matches the requested exact version"
            )
        docx = render_tracked_docx(base, current)
        docx_sha256 = _sha256(docx)
        if docx_sha256 != request.expected_docx_sha256:
            raise WorkflowInvariantError("deterministic DOCX hash differs from request")
        approval = self._approval_gate.exact_approval(request.approval_id)
        approved_binding = (
            approval.approval_id,
            approval.tenant_id,
            approval.matter_id,
            approval.work_product_id,
            approval.version_id,
            approval.version_number,
            approval.content_sha256,
            approval.docx_sha256,
        )
        requested_binding = (request.approval_id, *expected_current, docx_sha256)
        if approved_binding != requested_binding:
            raise PolicyDeniedError(
                "Approval does not bind the exact Work Product version and DOCX hash"
            )
        raw_pdf = self._preview_renderer.render(docx)
        preview_pdf = bind_pdf_preview(raw_pdf, docx_sha256)
        page_count = validate_pdf_preview(
            preview_pdf,
            docx_sha256=docx_sha256,
        )
        return self._store.record_export(
            request,
            docx=docx,
            preview_pdf=preview_pdf,
            page_count=page_count,
            completed_at=self._clock(),
        )
