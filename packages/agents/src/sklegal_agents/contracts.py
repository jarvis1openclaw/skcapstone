"""Pinned execution contracts for every known domain tool.

A ToolContract binds one catalog tool to the exact capability and purpose
its invocation requires, plus the pinned input and output schema artifacts
used for argument and result validation. Contracts are immutable code
constants: adding a tool requires a code change, a schema artifact, and a
review, never a runtime registration. The contract set must cover the
known-tool catalog exactly; any drift fails closed at import time.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints
from sklegal_capauth import Capability, Purpose

from .errors import UnboundedAuthorityError, UnknownToolError
from .models import AgentSpecValue, SchemaPin, ShortCode
from .tools import KNOWN_TOOL_IDS

ArtifactName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[a-z0-9][a-z0-9.-]*\.schema\.json$",
    ),
]


class ToolContract(AgentSpecValue):
    """Exact execution contract for one known domain tool."""

    tool_id: ShortCode
    capability: Capability
    purpose: Purpose
    input_artifact: ArtifactName
    input_schema: SchemaPin
    output_artifact: ArtifactName
    output_schema: SchemaPin


TOOL_CONTRACTS: tuple[ToolContract, ...] = (
    ToolContract(
        tool_id="matter.read",
        capability=Capability.MATTER_READ,
        purpose=Purpose.MATTER_MANAGEMENT,
        input_artifact="tool-matter-read-input.v1.schema.json",
        input_schema=SchemaPin(
            schema_id="sklegal.tool.matter-read-input/v1",
            sha256="d3fe32ed9cda51988d8eb4855de198509f8605c044f8ca1cd9c2fd581d0edfba",
        ),
        output_artifact="tool-matter-read-output.v1.schema.json",
        output_schema=SchemaPin(
            schema_id="sklegal.tool.matter-read-output/v1",
            sha256="6cc7be0e4db94eeda81474b8236b90f3e640274041f69a6af47b89e895fc5914",
        ),
    ),
    ToolContract(
        tool_id="evidence.search",
        capability=Capability.EVIDENCE_READ,
        purpose=Purpose.EVIDENCE_REVIEW,
        input_artifact="tool-evidence-search-input.v1.schema.json",
        input_schema=SchemaPin(
            schema_id="sklegal.tool.evidence-search-input/v1",
            sha256="ca79d1be3f1e22da470e8dda8c41a3ecdc6afc1eeb993e9a14d95f2c6a43e1ce",
        ),
        output_artifact="tool-evidence-search-output.v1.schema.json",
        output_schema=SchemaPin(
            schema_id="sklegal.tool.evidence-search-output/v1",
            sha256="5c38e18f28d96c2015611dea23833f09c17b17ec3f43880ccb1ecc4be1b07804",
        ),
    ),
    ToolContract(
        tool_id="evidence.read",
        capability=Capability.EVIDENCE_READ,
        purpose=Purpose.EVIDENCE_REVIEW,
        input_artifact="tool-evidence-read-input.v1.schema.json",
        input_schema=SchemaPin(
            schema_id="sklegal.tool.evidence-read-input/v1",
            sha256="27165f5ea5eb8bc1fd986e24ae6bd372e192a435810204a618ab4610ccd0b0ed",
        ),
        output_artifact="tool-evidence-read-output.v1.schema.json",
        output_schema=SchemaPin(
            schema_id="sklegal.tool.evidence-read-output/v1",
            sha256="97aabf99fe27be69479a097d41458ce90f06c3b6a23a1a565d349b813ea474bd",
        ),
    ),
    ToolContract(
        tool_id="authority.search",
        capability=Capability.CORPUS_SEARCH,
        purpose=Purpose.LEGAL_RESEARCH,
        input_artifact="tool-authority-search-input.v1.schema.json",
        input_schema=SchemaPin(
            schema_id="sklegal.tool.authority-search-input/v1",
            sha256="92fae7a603f6e039694d17d713dacb2a896367fab2ccbef70bda2f76904d282e",
        ),
        output_artifact="tool-authority-search-output.v1.schema.json",
        output_schema=SchemaPin(
            schema_id="sklegal.tool.authority-search-output/v1",
            sha256="eccf33573c2b6ce3c12ac2d5d5b20b23d86c740b431ccff6d6065dff867a5066",
        ),
    ),
    ToolContract(
        tool_id="deadline.compute",
        capability=Capability.MATTER_READ,
        purpose=Purpose.MATTER_MANAGEMENT,
        input_artifact="tool-deadline-compute-input.v1.schema.json",
        input_schema=SchemaPin(
            schema_id="sklegal.tool.deadline-compute-input/v1",
            sha256="c3cd372fc73cb0e75839b297add0ed7a21182523e960e5caa943caa1811dfc2c",
        ),
        output_artifact="tool-deadline-compute-output.v1.schema.json",
        output_schema=SchemaPin(
            schema_id="sklegal.tool.deadline-compute-output/v1",
            sha256="6ec14c8eab14937af3f851b91dbe8442e25ed3803f431d560bd3dc50ec0b67df",
        ),
    ),
    ToolContract(
        tool_id="workproduct.draft",
        capability=Capability.WORK_PRODUCT_DRAFT,
        purpose=Purpose.WORK_PRODUCT_PREPARATION,
        input_artifact="tool-workproduct-draft-input.v1.schema.json",
        input_schema=SchemaPin(
            schema_id="sklegal.tool.workproduct-draft-input/v1",
            sha256="fe49e62a45dc48617acf6e11a24c3c9bc85c5a16d663d75dee51651e216c3a6e",
        ),
        output_artifact="tool-workproduct-draft-output.v1.schema.json",
        output_schema=SchemaPin(
            schema_id="sklegal.tool.workproduct-draft-output/v1",
            sha256="d93ef90b9b2cfb2decc6c27149d2ff3c28cff2b7405cc2ebd47f5856735a7e3b",
        ),
    ),
)


def _build_contract_index() -> dict[str, ToolContract]:
    index: dict[str, ToolContract] = {}
    for contract in TOOL_CONTRACTS:
        if contract.tool_id in index:
            raise UnboundedAuthorityError(
                f"duplicate tool contract: {contract.tool_id}"
            )
        index[contract.tool_id] = contract
    if frozenset(index) != KNOWN_TOOL_IDS:
        raise UnboundedAuthorityError(
            "tool contracts must cover the known-tool catalog exactly"
        )
    return index


TOOL_CONTRACT_INDEX: dict[str, ToolContract] = _build_contract_index()


def tool_contract(tool_id: str) -> ToolContract:
    """Return the pinned contract for a catalog tool or fail closed."""

    contract = TOOL_CONTRACT_INDEX.get(tool_id)
    if contract is None:
        raise UnknownToolError(f"no pinned contract for tool: {tool_id}")
    return contract
