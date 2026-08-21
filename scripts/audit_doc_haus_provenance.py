#!/usr/bin/env python3
"""Create and validate a read-only doc-haus provenance inventory."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

DISPOSITIONS = {
    "approved_for_attributed_extraction",
    "rejected",
    "requires_permission",
}
ADOPTION_STATES = {
    "attributed_extract",
    "dependency_only",
    "excluded",
    "quarantined",
}
REQUIRED_CATEGORIES = {"citation", "review_grid", "docx", "workbench"}
ASSET_SUFFIXES = {
    ".aac",
    ".docx",
    ".eot",
    ".gif",
    ".ico",
    ".icns",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".otf",
    ".pdf",
    ".png",
    ".pptx",
    ".svg",
    ".ttf",
    ".wav",
    ".webm",
    ".webmanifest",
    ".webp",
    ".woff",
    ".woff2",
    ".xlsx",
    ".zip",
}
IMPLEMENTATION_SUFFIXES = {
    ".bash",
    ".c",
    ".cjs",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".less",
    ".mjs",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".svelte",
    ".scss",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
    ".zig",
}
CANDIDATE_TEXT_SUFFIXES = IMPLEMENTATION_SUFFIXES | {".patch"}
FINGERPRINT_WINDOW = 32
FINGERPRINT_STRIDE = 1
LIST_INVENTORY_SECTIONS = (
    "package_manifests",
    "lockfiles",
    "components",
    "implementation_sources",
    "tracked_files",
    "assets",
    "generated_files",
    "patches",
)
INVENTORY_SECTIONS = (*LIST_INVENTORY_SECTIONS, "candidate_source_fingerprints")
SEMVER = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
RESOLVED_PACKAGE = re.compile(r"^(@[^/]+/[^@]+|[^@]+)@(.+)$")
GENERATED_EXACT_PATHS = {
    "packages/docs/openapi.json",
    "packages/core/src/database/migration.gen.ts",
    "packages/sdk/openapi.json",
    "packages/storybook/debug-storybook.log",
    "packages/ui/src/components/app-icons/types.ts",
    "packages/ui/src/components/file-icons/types.ts",
    "packages/ui/src/components/provider-icons/types.ts",
    "packages/ui/src/styles/tailwind/colors.css",
}
GENERATED_PREFIXES = (
    "packages/console/core/migrations/",
    "packages/core/migration/",
    "packages/core/src/database/migration/",
    "packages/opencode/migration/",
    "packages/stats/core/migrations/",
)


class AuditError(RuntimeError):
    """Raised when an audit invariant fails."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_value_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return sha256_bytes(encoded)


def inventory_expectations(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expectations = {
        section: {
            "count": len(inventory[section]),
            "sha256": canonical_value_sha256(inventory[section]),
        }
        for section in LIST_INVENTORY_SECTIONS
    }
    fingerprints = inventory["candidate_source_fingerprints"]
    records = fingerprints["records"]
    expectations["candidate_source_fingerprints"] = {
        "count": len(records),
        "fingerprint_count": sum(len(record["fingerprints"]) for record in records),
        "verbatim_fingerprint_count": sum(
            len(record["verbatim_fingerprints"]) for record in records
        ),
        "wrapper_stripped_fingerprint_count": sum(
            len(record["wrapper_stripped_fingerprints"]) for record in records
        ),
        "sha256": canonical_value_sha256(fingerprints),
    }
    return expectations


def run_git(root: Path, *arguments: str, binary: bool = False) -> str | bytes:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
    )
    if binary:
        return process.stdout
    return process.stdout.decode("utf-8", errors="strict").strip()


def git_paths(root: Path, *arguments: str) -> list[str]:
    raw = run_git(root, *arguments, "-z", binary=True)
    assert isinstance(raw, bytes)
    return sorted(
        part.decode("utf-8", errors="strict") for part in raw.split(b"\0") if part
    )


def working_tree_snapshot(root: Path) -> dict[str, Any]:
    raw = run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "-z",
        binary=True,
    )
    assert isinstance(raw, bytes)
    entries: list[dict[str, str | None]] = []
    records = [part for part in raw.split(b"\0") if part]
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4:
            raise AuditError("malformed git status record")
        status = record[:2].decode("ascii", errors="strict")
        relative = record[3:].decode("utf-8", errors="strict")
        entry: dict[str, str | None] = {
            "path": relative,
            "sha256": None,
            "status": status,
        }
        path = root / relative
        if path.is_symlink():
            entry["sha256"] = sha256_bytes(path.readlink().as_posix().encode())
        elif path.is_file():
            entry["sha256"] = sha256_file(path)
        if "R" in status or "C" in status:
            index += 1
            if index >= len(records):
                raise AuditError("git rename status is missing its source path")
            entry["source_path"] = records[index].decode("utf-8", errors="strict")
        entries.append(entry)
        index += 1
    return {
        "clean": not entries,
        "entries": sorted(
            entries, key=lambda item: (str(item["path"]), str(item["status"]))
        ),
        "porcelain_sha256": sha256_bytes(raw),
    }


def git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    process = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
    )
    if process.returncode not in {0, 1}:
        raise subprocess.CalledProcessError(
            process.returncode,
            process.args,
            output=process.stdout,
            stderr=process.stderr,
        )
    return process.returncode == 0


def diff_statistics(root: Path, base: str, head: str) -> dict[str, int]:
    raw = run_git(root, "diff", "--numstat", f"{base}..{head}")
    assert isinstance(raw, str)
    files_changed = 0
    insertions = 0
    deletions = 0
    for line in raw.splitlines():
        added, removed, _ = line.split("\t", 2)
        files_changed += 1
        if added != "-":
            insertions += int(added)
        if removed != "-":
            deletions += int(removed)
    return {
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
    }


def configured_remotes(root: Path) -> list[dict[str, Any]]:
    names_raw = run_git(root, "remote")
    assert isinstance(names_raw, str)
    records: list[dict[str, Any]] = []
    for name in sorted(line for line in names_raw.splitlines() if line):
        url = run_git(root, "remote", "get-url", name)
        push_url = run_git(root, "remote", "get-url", "--push", name)
        fetch_raw = run_git(root, "config", "--get-all", f"remote.{name}.fetch")
        assert isinstance(url, str)
        assert isinstance(push_url, str)
        assert isinstance(fetch_raw, str)
        records.append(
            {
                "fetch_refspecs": sorted(fetch_raw.splitlines()),
                "name": name,
                "push_url": push_url,
                "url": url,
            }
        )
    return records


def origin_default_branch(root: Path) -> str:
    symbolic = run_git(root, "symbolic-ref", "refs/remotes/origin/HEAD")
    assert isinstance(symbolic, str)
    prefix = "refs/remotes/origin/"
    if not symbolic.startswith(prefix) or len(symbolic) == len(prefix):
        raise AuditError("origin default branch symbolic ref is invalid")
    return symbolic[len(prefix) :]


def first_ancestry_commit(root: Path, base: str, head: str) -> str:
    raw = run_git(root, "rev-list", "--ancestry-path", "--reverse", f"{base}..{head}")
    assert isinstance(raw, str)
    commits = raw.splitlines()
    if not commits:
        raise AuditError("upstream base has no ancestry path to HEAD")
    return commits[0]


def repository_snapshot(
    root: Path, lineage: dict[str, Any] | None = None
) -> dict[str, Any]:
    submodules_raw = run_git(root, "submodule", "status", "--recursive")
    assert isinstance(submodules_raw, str)
    tags_raw = run_git(root, "tag", "--points-at", "HEAD")
    assert isinstance(tags_raw, str)
    head = run_git(root, "rev-parse", "HEAD")
    branch = run_git(root, "branch", "--show-current")
    remote = run_git(root, "remote", "get-url", "origin")
    head_commit_time = run_git(root, "show", "-s", "--format=%cI", "HEAD")
    assert isinstance(head, str)
    assert isinstance(branch, str)
    assert isinstance(remote, str)
    assert isinstance(head_commit_time, str)
    result: dict[str, Any] = {
        "branch": branch,
        "configured_remotes": configured_remotes(root),
        "head": head,
        "head_commit_time": head_commit_time,
        "origin": remote,
        "origin_default_branch": origin_default_branch(root),
        "submodules": sorted(line for line in submodules_raw.splitlines() if line),
        "tags_at_head": sorted(line for line in tags_raw.splitlines() if line),
        "working_tree": working_tree_snapshot(root),
    }
    if lineage is not None:
        base = require_nonempty_string(lineage, "upstream_base", "lineage pin")
        first_fork = first_ancestry_commit(root, base, head)
        origin_head = run_git(
            root, "rev-parse", f"refs/remotes/origin/{lineage['branch']}"
        )
        first_parent = run_git(root, "rev-parse", f"{first_fork}^")
        commit_count = run_git(root, "rev-list", "--count", f"{base}..{head}")
        assert isinstance(origin_head, str)
        assert isinstance(first_parent, str)
        assert isinstance(commit_count, str)
        result.update(
            {
                "commits_after_upstream_base": int(commit_count),
                "delta_from_upstream_base": diff_statistics(root, base, head),
                "first_fork_ancestor": git_is_ancestor(root, first_fork, head),
                "first_fork_commit": first_fork,
                "first_fork_parent": first_parent,
                "origin_branch_head": origin_head,
                "upstream_base": base,
                "upstream_base_ancestor": git_is_ancestor(root, base, head),
                "upstream_remote_configured": any(
                    item["name"] == "upstream" for item in result["configured_remotes"]
                ),
            }
        )
    return result


def dependency_records(payload: dict[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for scope in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    ):
        values = payload.get(scope) or {}
        if not isinstance(values, dict):
            raise AuditError(f"package manifest {scope} must be an object")
        records.extend(
            {"name": str(name), "scope": scope, "requirement": str(requirement)}
            for name, requirement in sorted(values.items())
        )
    return records


def package_manifests(root: Path, tracked: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative in tracked:
        if not relative.endswith("package.json"):
            continue
        path = root / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.append(
            {
                "dependencies": dependency_records(payload),
                "imports": payload.get("imports"),
                "license": payload.get("license") or "NOASSERTION",
                "name": payload.get("name") or "NOASSERTION",
                "overrides": payload.get("overrides"),
                "path": relative,
                "patched_dependencies": payload.get("patchedDependencies"),
                "peer_dependencies_meta": payload.get("peerDependenciesMeta"),
                "private": bool(payload.get("private", False)),
                "sha256": sha256_file(path),
                "trusted_dependencies": payload.get("trustedDependencies"),
                "version": payload.get("version") or "NOASSERTION",
                "workspaces": payload.get("workspaces"),
            }
        )
    return records


LOCK_RECORD = re.compile(r'^\s{4}("(?:[^"\\]|\\.)*"):\s*\[("(?:[^"\\]|\\.)*")')
INTEGRITY = re.compile(r'"(sha(?:256|512)-[A-Za-z0-9+/=]+)"\]\s*,?\s*$')


def bun_lock_packages(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    in_packages = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line in {"[packages]", '  "packages": {'}:
            in_packages = True
            continue
        if in_packages and (line.startswith("[") or line == "  },"):
            break
        if not in_packages:
            continue
        match = LOCK_RECORD.match(line)
        if not match:
            continue
        integrity = INTEGRITY.search(line)
        records.append(
            {
                "integrity": integrity.group(1) if integrity else "NOASSERTION",
                "package_key": json.loads(match.group(1)),
                "resolved": json.loads(match.group(2)),
            }
        )
    return sorted(records, key=lambda item: (item["package_key"], item["resolved"]))


def lockfiles(root: Path, tracked: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative in tracked:
        if not relative.endswith("bun.lock"):
            continue
        path = root / relative
        packages = bun_lock_packages(path)
        records.append(
            {
                "format": "bun-lock-text-v1",
                "package_record_count": len(packages),
                "packages": packages,
                "path": relative,
                "sha256": sha256_file(path),
            }
        )
    return records


def tracked_sha256(path: Path) -> str:
    if path.is_symlink():
        return sha256_bytes(path.readlink().as_posix().encode("utf-8"))
    return sha256_file(path)


def is_asset_path(path: str) -> bool:
    value = Path(path)
    return value.suffix.lower() in ASSET_SUFFIXES or (
        path.startswith("packages/desktop/icons/") and value.suffix.lower() == ".xml"
    )


def is_generated_path(path: str) -> bool:
    return (
        path in GENERATED_EXACT_PATHS
        or path.startswith(GENERATED_PREFIXES)
        or path == "sst-env.d.ts"
        or path.endswith("/sst-env.d.ts")
        or "/gen/" in path
        or ".generated." in path
        or path.endswith(".snap")
    )


def classified_files(root: Path, tracked: list[str], kind: str) -> list[dict[str, str]]:
    if kind == "asset":
        selected = [path for path in tracked if is_asset_path(path)]
    elif kind == "generated":
        selected = [path for path in tracked if is_generated_path(path)]
    elif kind == "patch":
        selected = [path for path in tracked if path.endswith(".patch")]
    elif kind == "implementation":
        selected = [
            path
            for path in tracked
            if Path(path).suffix.lower() in IMPLEMENTATION_SUFFIXES
        ]
    elif kind == "tracked":
        selected = tracked
    else:
        raise AuditError(f"unknown file classification: {kind}")
    return [{"path": path, "sha256": tracked_sha256(root / path)} for path in selected]


def resolved_licenses(
    manifest: dict[str, Any],
) -> dict[tuple[str, str, str], dict[str, str]]:
    result: dict[tuple[str, str, str], dict[str, str]] = {}
    for item in manifest.get("resolved_third_party_components", []):
        result[(item["name"], item["version"], item["integrity"])] = item
    return result


def parse_resolution(value: str, package_key: str) -> dict[str, str]:
    match = RESOLVED_PACKAGE.fullmatch(value)
    if match is None:
        return {
            "name": package_key,
            "raw_resolution": value,
            "resolution_type": "unknown",
            "version": "NOASSERTION",
        }
    name, specifier = match.groups()
    if SEMVER.fullmatch(specifier):
        resolution_type = "registry"
        version = specifier.removeprefix("v")
    elif specifier.startswith("workspace:"):
        resolution_type = "workspace"
        version = "NOASSERTION"
    elif specifier.startswith(("github:", "git+", "git://")):
        resolution_type = "git"
        version = "NOASSERTION"
    elif specifier.startswith(("http://", "https://")):
        resolution_type = "url"
        version = "NOASSERTION"
    else:
        resolution_type = "unknown"
        version = "NOASSERTION"
    return {
        "name": name,
        "raw_resolution": value,
        "resolution_type": resolution_type,
        "version": version,
    }


def component_inventory(
    locks: list[dict[str, Any]], manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    evidence = resolved_licenses(manifest)
    components: list[dict[str, Any]] = []
    for lock in locks:
        for package in lock["packages"]:
            resolution = parse_resolution(package["resolved"], package["package_key"])
            name = resolution["name"]
            version = resolution["version"]
            resolved = evidence.get((name, version, package["integrity"]))
            license_value = resolved["license"] if resolved else "NOASSERTION"
            components.append(
                {
                    "integrity": package["integrity"],
                    "license": license_value,
                    "lockfile": lock["path"],
                    "name": name,
                    "package_key": package["package_key"],
                    "quarantined_for_extraction": license_value == "NOASSERTION",
                    "raw_resolution": resolution["raw_resolution"],
                    "resolution_type": resolution["resolution_type"],
                    "version": version,
                }
            )
    return sorted(
        components,
        key=lambda item: (
            item["lockfile"],
            item["name"],
            item["version"],
            item["raw_resolution"],
            item["package_key"],
        ),
    )


def normalized_tokens(value: str) -> list[str]:
    with_string_digests = re.sub(
        r"""(?s)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)""",
        lambda match: f" STR_{sha256_bytes(match.group(0).encode('utf-8'))[:16]} ",
        value,
    )
    without_comments = re.sub(r"/\*.*?\*/", " ", with_string_digests, flags=re.DOTALL)
    without_comments = re.sub(r"//[^\n]*", " ", without_comments)
    without_comments = re.sub(r"(?m)^\s*#[^\n]*", " ", without_comments)
    return re.findall(
        r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|===|!==|=>|==|!=|<=|>=|&&|\|\||[{}()[\].,:;?+*/%<>=!-]",
        without_comments,
    )


def fingerprints_from_tokens(tokens: list[str]) -> list[str]:
    if len(tokens) < 12:
        return []
    if len(tokens) < FINGERPRINT_WINDOW:
        return [sha256_bytes("\x1f".join(tokens).encode("utf-8"))]
    return sorted(
        {
            sha256_bytes(
                "\x1f".join(tokens[index : index + FINGERPRINT_WINDOW]).encode("utf-8")
            )
            for index in range(
                0,
                len(tokens) - FINGERPRINT_WINDOW + 1,
                FINGERPRINT_STRIDE,
            )
        }
    )


def token_fingerprints(value: str) -> list[str]:
    return fingerprints_from_tokens(normalized_tokens(value))


def verbatim_token_fingerprints(value: str) -> list[str]:
    tokens = re.findall(
        r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|===|!==|=>|==|!=|<=|>=|&&|\|\||[{}()[\].,:;?+*/%<>=!-]",
        value,
    )
    return fingerprints_from_tokens(tokens)


def wrapper_stripped_token_fingerprints(value: str) -> list[str]:
    without_delimiters = value.replace("<!--", " ").replace("-->", " ")
    without_delimiters = without_delimiters.replace("/*", " ").replace("*/", " ")
    without_delimiters = without_delimiters.replace("//", " ")
    without_delimiters = re.sub(r"(?m)^[ \t]*(?:--|#)[ \t]?", " ", without_delimiters)
    tokens = re.findall(
        r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|===|!==|=>|==|!=|<=|>=|&&|\|\||[{}()[\].,:;?+*/%<>=!-]",
        without_delimiters,
    )
    return fingerprints_from_tokens(tokens)


def fingerprint_segments(
    lines: list[str], segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for segment in segments:
        selected = "\n".join(lines[segment["start_line"] - 1 : segment["end_line"]])
        records.append(
            {
                **segment,
                "fingerprints": token_fingerprints(selected),
                "verbatim_fingerprints": verbatim_token_fingerprints(selected),
                "wrapper_stripped_fingerprints": (
                    wrapper_stripped_token_fingerprints(selected)
                ),
            }
        )
    return records


def candidate_fingerprints(
    root: Path, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for candidate in manifest["candidates"]:
        for item in candidate["paths"]:
            path = root / item["path"]
            if path.suffix.lower() not in CANDIDATE_TEXT_SUFFIXES:
                continue
            try:
                value = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            lines = value.splitlines()
            segments = item.get("segments") or [
                {
                    "start_line": 1,
                    "end_line": len(lines),
                    "purpose": "Whole pinned file",
                }
            ]
            segment_records = fingerprint_segments(lines, segments)
            records.append(
                {
                    "candidate_id": candidate["id"],
                    "file_sha256": item["sha256"],
                    "fingerprints": sorted(
                        {
                            fingerprint
                            for segment in segment_records
                            for fingerprint in segment["fingerprints"]
                        }
                    ),
                    "verbatim_fingerprints": sorted(
                        {
                            fingerprint
                            for segment in segment_records
                            for fingerprint in segment["verbatim_fingerprints"]
                        }
                    ),
                    "wrapper_stripped_fingerprints": sorted(
                        {
                            fingerprint
                            for segment in segment_records
                            for fingerprint in segment["wrapper_stripped_fingerprints"]
                        }
                    ),
                    "path": item["path"],
                    "line_count": len(lines),
                    "segment_fingerprints": segment_records,
                    "segments": segments,
                }
            )
    return sorted(records, key=lambda item: (item["candidate_id"], item["path"]))


def manifest_digest(manifest_path: Path) -> str:
    return sha256_file(manifest_path)


def build_rights_inventory(
    root: Path, manifest: dict[str, Any], manifest_sha256: str
) -> dict[str, Any]:
    tracked = git_paths(root, "ls-files")
    manifests = package_manifests(root, tracked)
    locks = lockfiles(root, tracked)
    return {
        "assets": classified_files(root, tracked, "asset"),
        "bom_format": "SKLegal deterministic package and rights inventory",
        "components": component_inventory(locks, manifest),
        "candidate_source_fingerprints": {
            "algorithm": "normalized-token-window-sha256-string-digest-v2",
            "verbatim_algorithm": "verbatim-lexical-window-sha256-v1",
            "wrapper_stripped_algorithm": ("wrapper-stripped-lexical-window-sha256-v1"),
            "minimum_tokens": 12,
            "records": candidate_fingerprints(root, manifest),
            "stride": FINGERPRINT_STRIDE,
            "window_tokens": FINGERPRINT_WINDOW,
        },
        "generated_files": classified_files(root, tracked, "generated"),
        "implementation_sources": classified_files(root, tracked, "implementation"),
        "lockfiles": locks,
        "metadata": {
            "audit_card": "SKL-S0-05",
            "license_default_for_unresolved_components": "NOASSERTION",
            "manifest_sha256": manifest_sha256,
            "schema_version": 1,
            "tool": "scripts/audit_doc_haus_provenance.py",
        },
        "package_manifests": manifests,
        "patches": classified_files(root, tracked, "patch"),
        "repository": repository_snapshot(root, manifest["repository"]),
        "tracked_files": classified_files(root, tracked, "tracked"),
    }


def integrity_hash(integrity: str) -> list[dict[str, str]]:
    if not integrity.startswith(("sha256-", "sha512-")):
        return []
    algorithm, encoded = integrity.split("-", 1)
    try:
        content = base64.b64decode(encoded, validate=True).hex()
    except (ValueError, binascii.Error):
        return []
    return [{"alg": algorithm.upper().replace("SHA", "SHA-"), "content": content}]


def component_bom_ref(component: dict[str, Any]) -> str:
    identity = "\x1f".join(
        [
            component["name"],
            component["version"],
            component["integrity"],
            component["raw_resolution"],
            component["resolution_type"],
        ]
    )
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, identity)}"


def component_purl(component: dict[str, Any]) -> str | None:
    name = component["name"]
    version = component["version"]
    if (
        component["resolution_type"] != "registry"
        or name == "NOASSERTION"
        or version == "NOASSERTION"
    ):
        return None
    return f"pkg:npm/{quote(name, safe='/')}@{quote(version, safe='.+-_:')}"


def build_cyclonedx(
    rights: dict[str, Any], manifest: dict[str, Any], manifest_sha256: str
) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for rights_component in rights["components"]:
        key = (
            rights_component["name"],
            rights_component["version"],
            rights_component["integrity"],
            rights_component["raw_resolution"],
            rights_component["resolution_type"],
        )
        record = grouped.setdefault(
            key,
            {
                "integrity": rights_component["integrity"],
                "license": rights_component["license"],
                "lockfiles": set(),
                "name": rights_component["name"],
                "package_keys": set(),
                "quarantined": rights_component["quarantined_for_extraction"],
                "raw_resolution": rights_component["raw_resolution"],
                "resolution_type": rights_component["resolution_type"],
                "version": rights_component["version"],
            },
        )
        record["lockfiles"].add(rights_component["lockfile"])
        record["package_keys"].add(rights_component["package_key"])
    components: list[dict[str, Any]] = []
    for key in sorted(grouped):
        item = grouped[key]
        component: dict[str, Any] = {
            "bom-ref": component_bom_ref(item),
            "hashes": integrity_hash(item["integrity"]),
            "licenses": [{"license": {"id": item["license"]}}]
            if item["license"] != "NOASSERTION"
            else [{"license": {"name": "NOASSERTION"}}],
            "name": item["name"],
            "properties": [
                {
                    "name": "sklegal:audit:integrity",
                    "value": item["integrity"],
                },
                {
                    "name": "sklegal:audit:lockfiles",
                    "value": ",".join(sorted(item["lockfiles"])),
                },
                {
                    "name": "sklegal:audit:package-keys",
                    "value": ",".join(sorted(item["package_keys"])),
                },
                {
                    "name": "sklegal:audit:quarantined-for-extraction",
                    "value": str(item["quarantined"]).lower(),
                },
                {
                    "name": "sklegal:audit:raw-resolution",
                    "value": item["raw_resolution"],
                },
                {
                    "name": "sklegal:audit:resolution-type",
                    "value": item["resolution_type"],
                },
            ],
            "type": "library",
        }
        if item["version"] != "NOASSERTION":
            component["version"] = item["version"]
        purl = component_purl(item)
        if purl is not None:
            component["purl"] = purl
        if not component["hashes"]:
            del component["hashes"]
        components.append(component)
    repository = rights["repository"]
    serial = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "\x1f".join(
            [
                repository["origin"],
                repository["head"],
                manifest_sha256,
            ]
        ),
    )
    return {
        "$schema": "https://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "components": components,
        "metadata": {
            "component": {
                "bom-ref": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, repository['origin'] + repository['head'])}",
                "externalReferences": [
                    {"type": "vcs", "url": manifest["repository"]["origin"]}
                ],
                "name": "doc-haus",
                "properties": [
                    {
                        "name": "sklegal:audit:branch",
                        "value": repository["branch"],
                    },
                    {
                        "name": "sklegal:audit:manifest-sha256",
                        "value": manifest_sha256,
                    },
                    {
                        "name": "sklegal:audit:source-head",
                        "value": repository["head"],
                    },
                ],
                "type": "application",
                "version": repository["head"],
            },
            "properties": [
                {
                    "name": "sklegal:audit:dependency-graph",
                    "value": "absent-flat-locked-component-inventory",
                },
                {
                    "name": "sklegal:audit:source-commit-time",
                    "value": repository["head_commit_time"],
                },
            ],
            "tools": {
                "components": [
                    {
                        "name": "audit_doc_haus_provenance.py",
                        "type": "application",
                        "version": "1",
                    }
                ]
            },
        },
        "serialNumber": f"urn:uuid:{serial}",
        "specVersion": "1.6",
        "version": 1,
    }


def candidate_paths(manifest: dict[str, Any]) -> dict[str, str]:
    paths: dict[str, str] = {}
    for candidate in manifest["candidates"]:
        for item in candidate["paths"]:
            current = paths.get(item["path"])
            if current is not None and current != item["sha256"]:
                raise AuditError(f"conflicting candidate digest: {item['path']}")
            paths[item["path"]] = item["sha256"]
    return paths


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def require_nonempty_string(record: dict[str, Any], field: str, context: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AuditError(f"{context} is missing {field}")
    return value


def validate_file_record(record: object, context: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise AuditError(f"{context} must be an object")
    path = require_nonempty_string(record, "path", context)
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise AuditError(f"{context} path must be repository relative")
    if not is_sha256(record.get("sha256")):
        raise AuditError(f"{context} has an invalid sha256")
    return record


def validate_license_evidence(manifest: dict[str, Any]) -> None:
    evidence = manifest.get("license_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise AuditError("license_evidence must be a nonempty list")
    for index, raw in enumerate(evidence):
        context = f"license_evidence[{index}]"
        record = validate_file_record(raw, context)
        require_nonempty_string(record, "license", context)
        require_nonempty_string(record, "scope", context)
        notices = record.get("notices")
        if (
            not isinstance(notices, list)
            or not notices
            or any(
                not isinstance(notice, str) or not notice.strip() for notice in notices
            )
        ):
            raise AuditError(f"{context} notices must be nonempty strings")


def validate_supplemental_boundaries(manifest: dict[str, Any]) -> None:
    for field in (
        "third_party_content_boundaries",
        "copied_adapted_source_boundaries",
    ):
        records = manifest.get(field)
        if not isinstance(records, list) or not records:
            raise AuditError(f"{field} must be a nonempty list")
        for index, raw in enumerate(records):
            context = f"{field}[{index}]"
            record = validate_file_record(raw, context)
            for required in (
                "id",
                "exact_upstream_provenance",
                "rule",
                "rights",
            ):
                require_nonempty_string(record, required, context)
            claims = record.get("declared_rights")
            if not isinstance(claims, list) or not claims:
                raise AuditError(f"{context} declared_rights must be nonempty")
            for claim_index, claim in enumerate(claims):
                claim_context = f"{context} declared_rights[{claim_index}]"
                if not isinstance(claim, dict):
                    raise AuditError(f"{claim_context} must be an object")
                require_nonempty_string(claim, "source", claim_context)
                require_nonempty_string(claim, "claim", claim_context)
            if (
                record["rights"] != "NOASSERTION"
                or record.get("quarantined") is not True
            ):
                raise AuditError(f"{context} must remain NOASSERTION and quarantined")


def validate_exact_hash_exceptions(manifest: dict[str, Any]) -> None:
    records = manifest.get("exact_hash_exceptions")
    if not isinstance(records, list):
        raise AuditError("exact_hash_exceptions must be a list")
    seen_project_paths: set[str] = set()
    candidate_sources = set(candidate_paths(manifest))
    for index, raw in enumerate(records):
        context = f"exact_hash_exceptions[{index}]"
        if not isinstance(raw, dict):
            raise AuditError(f"{context} must be an object")
        project_path = require_nonempty_string(raw, "project_path", context)
        source_path = require_nonempty_string(raw, "source_path", context)
        reason = require_nonempty_string(raw, "reason", context)
        if not reason:
            raise AuditError(f"{context} reason is empty")
        if not is_sha256(raw.get("sha256")):
            raise AuditError(f"{context} has invalid sha256")
        for path in (project_path, source_path):
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise AuditError(f"{context} paths must be repository relative")
            if Path(path).suffix.lower() in IMPLEMENTATION_SUFFIXES:
                raise AuditError(f"{context} cannot exempt implementation source")
        if source_path in candidate_sources:
            raise AuditError(f"{context} cannot exempt a candidate source")
        if project_path in seen_project_paths:
            raise AuditError(
                f"duplicate exact hash exception project path: {project_path}"
            )
        seen_project_paths.add(project_path)


def validate_resolved_components(
    manifest: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    raw_components = manifest.get("resolved_third_party_components")
    if not isinstance(raw_components, list):
        raise AuditError("resolved_third_party_components must be a list")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    absent_license_notices: set[str] = set()
    for index, raw in enumerate(raw_components):
        context = f"resolved_third_party_components[{index}]"
        if not isinstance(raw, dict):
            raise AuditError(f"{context} must be an object")
        name = require_nonempty_string(raw, "name", context)
        version = require_nonempty_string(raw, "version", context)
        license_value = require_nonempty_string(raw, "license", context)
        integrity = require_nonempty_string(raw, "integrity", context)
        require_nonempty_string(raw, "evidence", context)
        if license_value == "NOASSERTION":
            raise AuditError(f"{context} cannot claim to resolve NOASSERTION")
        if not integrity_hash(integrity):
            raise AuditError(f"{context} has invalid package integrity")
        if raw.get("tarball_license_notice_absent") is True:
            if not is_sha256(raw.get("tarball_package_json_sha256")):
                raise AuditError(f"{context} lacks tarball package.json evidence")
            require_nonempty_string(raw, "tarball_license_notice", context)
            if "tarball_license_path" in raw or "tarball_license_sha256" in raw:
                raise AuditError(
                    f"{context} has contradictory tarball license evidence"
                )
            absent_license_notices.add(f"{name}@{version}")
        else:
            require_nonempty_string(raw, "tarball_license_path", context)
            if not is_sha256(raw.get("tarball_license_sha256")):
                raise AuditError(f"{context} has invalid tarball license digest")
        notices = raw.get("notices", [])
        if not isinstance(notices, list) or any(
            not isinstance(notice, str) or not notice.strip() for notice in notices
        ):
            raise AuditError(f"{context} notices must be strings")
        key = (name, version)
        if key in result:
            raise AuditError(f"duplicate resolved component: {name}@{version}")
        result[key] = raw
    scope = manifest["resolved_component_evidence_scope"]
    if absent_license_notices != set(scope["tarball_license_notice_absences"]):
        raise AuditError("tarball license notice absence inventory is inconsistent")
    return result


def validate_segments(record: dict[str, Any], context: str) -> None:
    if "segments" not in record:
        return
    segments = record["segments"]
    if not isinstance(segments, list) or not segments:
        raise AuditError(f"{context} segments must be a nonempty list")
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise AuditError(f"{context} segment {index} must be an object")
        start = segment.get("start_line")
        end = segment.get("end_line")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < 1
            or end < start
        ):
            raise AuditError(f"{context} segment {index} has an invalid line range")
        require_nonempty_string(segment, "purpose", f"{context} segment {index}")


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != "sklegal-doc-haus-provenance/v1":
        raise AuditError("unsupported provenance manifest schema")
    repository = manifest.get("repository")
    if not isinstance(repository, dict):
        raise AuditError("repository pin is required")
    for field in (
        "head",
        "branch",
        "origin",
        "origin_branch_head",
        "upstream_base",
        "first_fork_commit",
        "head_commit_time",
    ):
        require_nonempty_string(repository, field, "repository pin")
    for field in (
        "origin_default_branch",
        "origin_branch_observed_at",
        "origin_branch_observation_role",
    ):
        require_nonempty_string(repository, field, "repository pin")
    configured = repository.get("configured_remotes")
    if not isinstance(configured, list) or not configured:
        raise AuditError("repository configured_remotes must be a nonempty list")
    remote_names: set[str] = set()
    for index, remote in enumerate(configured):
        context = f"repository configured_remotes[{index}]"
        if not isinstance(remote, dict):
            raise AuditError(f"{context} must be an object")
        name = require_nonempty_string(remote, "name", context)
        require_nonempty_string(remote, "url", context)
        require_nonempty_string(remote, "push_url", context)
        refspecs = remote.get("fetch_refspecs")
        if (
            not isinstance(refspecs, list)
            or not refspecs
            or any(not isinstance(value, str) or not value for value in refspecs)
        ):
            raise AuditError(f"{context} fetch_refspecs must be nonempty strings")
        if name in remote_names:
            raise AuditError(f"duplicate configured remote: {name}")
        remote_names.add(name)
    if repository.get("upstream_remote_configured") != ("upstream" in remote_names):
        raise AuditError("repository upstream_remote_configured is inconsistent")
    for field in (
        "head",
        "origin_branch_head",
        "origin_branch_observed_head",
        "upstream_base",
        "first_fork_commit",
    ):
        if not re.fullmatch(r"[0-9a-f]{40}", str(repository[field])):
            raise AuditError(f"repository pin has invalid {field}")
    commit_count = repository.get("commits_after_upstream_base")
    if not isinstance(commit_count, int) or commit_count < 1:
        raise AuditError("repository pin has invalid commits_after_upstream_base")
    delta = repository.get("delta_from_upstream_base")
    if not isinstance(delta, dict) or set(delta) != {
        "files_changed",
        "insertions",
        "deletions",
    }:
        raise AuditError("repository pin has invalid delta_from_upstream_base")
    if any(not isinstance(value, int) or value < 0 for value in delta.values()):
        raise AuditError("repository delta values must be nonnegative integers")
    if "upstream_dev_observed_head" in repository:
        if not re.fullmatch(
            r"[0-9a-f]{40}", str(repository["upstream_dev_observed_head"])
        ):
            raise AuditError("repository pin has invalid observed upstream dev head")
        for field in (
            "upstream_dev_observed_at",
            "upstream_dev_observation_role",
        ):
            require_nonempty_string(repository, field, "upstream dev observation")
    expected_inventory = manifest.get("expected_inventory")
    if not isinstance(expected_inventory, dict) or set(expected_inventory) != set(
        INVENTORY_SECTIONS
    ):
        raise AuditError("expected_inventory must cover every audited section")
    for section in INVENTORY_SECTIONS:
        expectation = expected_inventory[section]
        required_keys = {"count", "sha256"}
        if section == "candidate_source_fingerprints":
            required_keys.update(
                {
                    "fingerprint_count",
                    "verbatim_fingerprint_count",
                    "wrapper_stripped_fingerprint_count",
                }
            )
        if not isinstance(expectation, dict) or set(expectation) != required_keys:
            raise AuditError(f"expected_inventory {section} record is invalid")
        if not isinstance(expectation["count"], int) or expectation["count"] < 0:
            raise AuditError(f"expected_inventory {section} count is invalid")
        if not is_sha256(expectation["sha256"]):
            raise AuditError(f"expected_inventory {section} digest is invalid")
        if section == "candidate_source_fingerprints":
            for count_field in (
                "fingerprint_count",
                "verbatim_fingerprint_count",
                "wrapper_stripped_fingerprint_count",
            ):
                if (
                    not isinstance(expectation[count_field], int)
                    or expectation[count_field] < 0
                ):
                    raise AuditError(
                        f"candidate {count_field} expected count is invalid"
                    )
    if manifest.get("source_approval_default") != "not_approved":
        raise AuditError("source approval must fail closed to not_approved")
    require_nonempty_string(manifest, "source_approval_rule", "source approval")
    future_gate = manifest.get("future_attributed_extraction_gate")
    if not isinstance(future_gate, dict):
        raise AuditError("future attributed extraction gate is required")
    for field in ("current_card", "required_future_control"):
        require_nonempty_string(future_gate, field, "future attributed extraction gate")
    evidence_scope = manifest.get("resolved_component_evidence_scope")
    if not isinstance(evidence_scope, dict):
        raise AuditError("resolved component evidence scope is required")
    for field in ("verification", "distribution_limitation"):
        require_nonempty_string(
            evidence_scope, field, "resolved component evidence scope"
        )
    absences = evidence_scope.get("tarball_license_notice_absences")
    if not isinstance(absences, list) or any(
        not isinstance(item, str) or not item for item in absences
    ):
        raise AuditError("tarball license notice absences must be strings")
    validate_license_evidence(manifest)
    validate_supplemental_boundaries(manifest)
    validate_exact_hash_exceptions(manifest)
    resolved = validate_resolved_components(manifest)
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise AuditError("at least one candidate is required")
    categories: set[str] = set()
    identifiers: set[str] = set()
    path_ranges: dict[str, list[tuple[int, int, str, str]]] = {}
    for candidate in candidates:
        identifier = candidate.get("id")
        if not identifier or identifier in identifiers:
            raise AuditError("candidate identifiers must be present and unique")
        identifiers.add(identifier)
        disposition = candidate.get("disposition")
        if disposition not in DISPOSITIONS:
            raise AuditError(f"invalid candidate disposition: {disposition}")
        category = candidate.get("category")
        if not isinstance(category, str) or category not in REQUIRED_CATEGORIES:
            raise AuditError(f"invalid candidate category: {category}")
        categories.add(category)
        for field in (
            "copyright_notice_obligations",
            "engineering_fit",
            "extraction_boundary",
            "license_basis",
            "unresolved_risks",
        ):
            if not candidate.get(field):
                raise AuditError(f"candidate {identifier} is missing {field}")
        paths = candidate.get("paths")
        if not isinstance(paths, list) or not paths:
            raise AuditError(f"candidate {identifier} has no covered paths")
        for index, item in enumerate(paths):
            record = validate_file_record(item, f"candidate {identifier} path {index}")
            validate_segments(record, f"candidate {identifier} path {index}")
            ranges = record.get("segments") or [
                {"start_line": 1, "end_line": sys.maxsize}
            ]
            for segment in ranges:
                current = (
                    segment["start_line"],
                    segment["end_line"],
                    disposition,
                    identifier,
                )
                for prior in path_ranges.setdefault(record["path"], []):
                    intersects = current[0] <= prior[1] and prior[0] <= current[1]
                    if intersects and current[2] != prior[2]:
                        raise AuditError(
                            "candidate source ranges overlap across dispositions: "
                            f"{record['path']} {prior[3]} and {identifier}"
                        )
                path_ranges[record["path"]].append(current)
        dependencies = candidate.get("dependencies")
        if not isinstance(dependencies, list):
            raise AuditError(f"candidate {identifier} dependencies must be a list")
        for index, dependency in enumerate(dependencies):
            context = f"candidate {identifier} dependency {index}"
            if not isinstance(dependency, dict):
                raise AuditError(f"{context} must be an object")
            name = require_nonempty_string(dependency, "name", context)
            version = require_nonempty_string(dependency, "version", context)
            license_value = require_nonempty_string(dependency, "license", context)
            adoption = dependency.get("adoption")
            if adoption not in ADOPTION_STATES:
                raise AuditError(f"{context} has invalid adoption state: {adoption}")
            carried = adoption in {"attributed_extract", "dependency_only"}
            resolved_record = resolved.get((name, version))
            if license_value != "NOASSERTION" and (
                resolved_record is None or resolved_record["license"] != license_value
            ):
                raise AuditError(
                    f"candidate {identifier} dependency lacks exact resolved evidence: "
                    f"{name}@{version}"
                )
            if disposition == "approved_for_attributed_extraction" and carried:
                if license_value == "NOASSERTION":
                    raise AuditError(
                        f"approved candidate {identifier} carries NOASSERTION"
                    )
                if (
                    resolved_record is None
                    or resolved_record["license"] != license_value
                ):
                    raise AuditError(
                        f"approved candidate {identifier} carries unresolved {name}@{version}"
                    )
            if (
                disposition == "approved_for_attributed_extraction"
                and adoption == "quarantined"
            ):
                raise AuditError(
                    f"approved candidate {identifier} carries quarantined dependency"
                )
    missing = REQUIRED_CATEGORIES - categories
    if missing:
        raise AuditError(f"candidate categories are missing: {sorted(missing)}")
    expected_text_paths = {
        item["path"]
        for candidate in candidates
        for item in candidate["paths"]
        if Path(item["path"]).suffix.lower() in CANDIDATE_TEXT_SUFFIXES
    }
    candidate_text_line_counts = manifest.get("candidate_text_line_counts")
    if (
        not isinstance(candidate_text_line_counts, dict)
        or set(candidate_text_line_counts) != expected_text_paths
    ):
        raise AuditError(
            "candidate_text_line_counts must exactly cover candidate text paths"
        )
    if any(
        not isinstance(value, int) or value < 1
        for value in candidate_text_line_counts.values()
    ):
        raise AuditError("candidate text line counts must be positive integers")
    boundaries = manifest.get("quarantined_boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        raise AuditError("quarantined boundaries are required")
    for boundary in boundaries:
        if not isinstance(boundary, dict):
            raise AuditError("quarantined boundary must be an object")
        for field in ("id", "scope", "rule"):
            require_nonempty_string(boundary, field, "quarantined boundary")
        if (
            boundary.get("rights") != "NOASSERTION"
            or boundary.get("quarantined") is not True
        ):
            raise AuditError(
                "unknown boundaries must remain NOASSERTION and quarantined"
            )
    candidate_paths(manifest)


def compare_repository_pin(manifest: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected = manifest["repository"]
    for key in (
        "head",
        "head_commit_time",
        "branch",
        "configured_remotes",
        "origin",
        "origin_default_branch",
        "upstream_remote_configured",
    ):
        if snapshot[key] != expected[key]:
            raise AuditError(
                f"repository {key} mismatch: expected {expected[key]}, got {snapshot[key]}"
            )
    if snapshot["submodules"] != expected["submodules"]:
        raise AuditError("repository submodule inventory mismatch")
    if snapshot["tags_at_head"] != expected["tags_at_head"]:
        raise AuditError("repository tag inventory mismatch")
    if snapshot["working_tree"] != expected["working_tree"]:
        raise AuditError("repository working-tree snapshot mismatch")
    if snapshot.get("origin_branch_head") != expected.get("origin_branch_head"):
        raise AuditError("repository origin branch head mismatch")
    if snapshot.get("upstream_base") != expected.get("upstream_base"):
        raise AuditError("repository upstream base mismatch")
    if snapshot.get("upstream_base_ancestor") is not True:
        raise AuditError("repository upstream base is not an ancestor of HEAD")
    if snapshot.get("first_fork_commit") != expected.get("first_fork_commit"):
        raise AuditError("repository first fork commit mismatch")
    if snapshot.get("first_fork_ancestor") is not True:
        raise AuditError("repository first fork commit is not an ancestor of HEAD")
    if snapshot.get("first_fork_parent") != expected.get("upstream_base"):
        raise AuditError("repository first fork parent does not match upstream base")
    if snapshot.get("commits_after_upstream_base") != expected.get(
        "commits_after_upstream_base"
    ):
        raise AuditError("repository commit count after upstream base mismatch")
    if snapshot.get("delta_from_upstream_base") != expected.get(
        "delta_from_upstream_base"
    ):
        raise AuditError("repository delta from upstream base mismatch")


def verify_evidence_hashes(root: Path, manifest: dict[str, Any]) -> None:
    records = list(manifest.get("license_evidence", []))
    records.extend(manifest.get("third_party_content_boundaries", []))
    records.extend(manifest.get("copied_adapted_source_boundaries", []))
    for path, digest in candidate_paths(manifest).items():
        records.append({"path": path, "sha256": digest})
    for record in records:
        path = root / record["path"]
        if not path.is_file():
            raise AuditError(f"pinned evidence is missing: {record['path']}")
        actual = sha256_file(path)
        if actual != record["sha256"]:
            raise AuditError(f"pinned evidence digest mismatch: {record['path']}")
    for path, expected_line_count in manifest["candidate_text_line_counts"].items():
        try:
            observed_line_count = len(
                (root / path).read_text(encoding="utf-8").splitlines()
            )
        except UnicodeDecodeError as error:
            raise AuditError(f"candidate text is not UTF-8: {path}") from error
        if observed_line_count != expected_line_count:
            raise AuditError(f"candidate text line count mismatch: {path}")
    for candidate in manifest["candidates"]:
        for item in candidate["paths"]:
            segments = item.get("segments")
            if not segments:
                continue
            path = root / item["path"]
            try:
                line_count = len(path.read_text(encoding="utf-8").splitlines())
            except UnicodeDecodeError as error:
                raise AuditError(
                    f"segmented evidence is not UTF-8 text: {item['path']}"
                ) from error
            if any(segment["end_line"] > line_count for segment in segments):
                raise AuditError(
                    f"candidate segment exceeds pinned source: {item['path']}"
                )


def project_files(root: Path) -> list[Path]:
    try:
        top_level = run_git(root, "rev-parse", "--show-toplevel")
    except subprocess.CalledProcessError:
        ignored_parts = {
            ".git",
            ".mypy_cache",
            ".ruff_cache",
            ".tools",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
        }
        values = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if (path.is_file() or path.is_symlink())
            and not ignored_parts.intersection(path.relative_to(root).parts)
            and not any(
                part.startswith(".cleanroom-") for part in path.relative_to(root).parts
            )
        )
    else:
        assert isinstance(top_level, str)
        if Path(top_level).resolve() != root.resolve():
            raise AuditError("project root is nested inside another Git worktree")
        values = git_paths(
            root, "ls-files", "--cached", "--others", "--exclude-standard"
        )
    return [root / value for value in values]


def verify_no_code_copy(
    project_root: Path, manifest: dict[str, Any], rights: dict[str, Any]
) -> tuple[int, int]:
    tracked_by_digest: dict[str, set[str]] = {}
    for item in rights.get("tracked_files", []):
        tracked_by_digest.setdefault(item["sha256"], set()).add(item["path"])
    candidate_digests = set(candidate_paths(manifest).values())
    exception_by_project = {
        item["project_path"]: item for item in manifest["exact_hash_exceptions"]
    }
    used_exceptions: set[str] = set()
    fingerprint_sources: dict[str, list[str]] = {}
    verbatim_sources: dict[str, list[str]] = {}
    wrapper_stripped_sources: dict[str, list[str]] = {}
    fingerprint_section = rights.get("candidate_source_fingerprints", {})
    for record in fingerprint_section.get("records", []):
        for fingerprint in record.get("fingerprints", []):
            fingerprint_sources.setdefault(fingerprint, []).append(record["path"])
        for fingerprint in record.get("verbatim_fingerprints", []):
            verbatim_sources.setdefault(fingerprint, []).append(record["path"])
        for fingerprint in record.get("wrapper_stripped_fingerprints", []):
            wrapper_stripped_sources.setdefault(fingerprint, []).append(record["path"])
    inspected = 0
    windows = 0
    for path in project_files(project_root):
        relative = path.relative_to(project_root).as_posix()
        if path.is_symlink():
            raise AuditError(
                f"Git-eligible symlink cannot pass no-copy audit: {relative}"
            )
        if not path.is_file():
            continue
        digest = sha256_file(path)
        if digest in candidate_digests:
            raise AuditError(f"exact doc-haus candidate copy detected: {relative}")
        if digest in tracked_by_digest:
            exception = exception_by_project.get(relative)
            if (
                exception is None
                or exception["sha256"] != digest
                or exception["source_path"] not in tracked_by_digest[digest]
            ):
                raise AuditError(
                    f"exact doc-haus tracked-file copy detected: {relative}"
                )
            used_exceptions.add(relative)
        if path.suffix.lower() not in IMPLEMENTATION_SUFFIXES:
            continue
        try:
            source_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        fingerprints = token_fingerprints(source_text)
        verbatim_fingerprints = verbatim_token_fingerprints(source_text)
        wrapper_stripped_fingerprints = wrapper_stripped_token_fingerprints(source_text)
        inspected += 1
        windows += (
            len(fingerprints)
            + len(verbatim_fingerprints)
            + len(wrapper_stripped_fingerprints)
        )
        overlap = sorted(set(fingerprints) & fingerprint_sources.keys())
        if overlap:
            sources = sorted(
                {source for value in overlap for source in fingerprint_sources[value]}
            )
            raise AuditError(
                f"normalized candidate-source overlap detected: {relative} matches {sources}"
            )
        verbatim_overlap = sorted(set(verbatim_fingerprints) & verbatim_sources.keys())
        if verbatim_overlap:
            sources = sorted(
                {
                    source
                    for value in verbatim_overlap
                    for source in verbatim_sources[value]
                }
            )
            raise AuditError(
                f"verbatim candidate-source overlap detected: {relative} matches {sources}"
            )
        wrapper_stripped_overlap = sorted(
            set(wrapper_stripped_fingerprints) & wrapper_stripped_sources.keys()
        )
        if wrapper_stripped_overlap:
            sources = sorted(
                {
                    source
                    for value in wrapper_stripped_overlap
                    for source in wrapper_stripped_sources[value]
                }
            )
            raise AuditError(
                f"wrapper-stripped candidate-source overlap detected: {relative} matches {sources}"
            )
    unused = set(exception_by_project) - used_exceptions
    if unused:
        raise AuditError(f"unused exact hash exceptions: {sorted(unused)}")
    return inspected, windows


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AuditError(f"JSON root must be an object: {path}")
    return payload


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def validate_rights_inventory(
    rights: dict[str, Any], manifest: dict[str, Any], manifest_sha256: str
) -> None:
    metadata = rights.get("metadata")
    if (
        not isinstance(metadata, dict)
        or metadata.get("manifest_sha256") != manifest_sha256
    ):
        raise AuditError("rights inventory manifest digest mismatch")
    if metadata.get("license_default_for_unresolved_components") != "NOASSERTION":
        raise AuditError("rights inventory must fail closed to NOASSERTION")
    repository = rights.get("repository")
    if not isinstance(repository, dict):
        raise AuditError("rights inventory repository snapshot is missing")
    compare_repository_pin(manifest, repository)
    for field in (
        "package_manifests",
        "lockfiles",
        "patches",
        "assets",
        "generated_files",
        "implementation_sources",
        "tracked_files",
    ):
        if not isinstance(rights.get(field), list):
            raise AuditError(f"rights inventory {field} must be a list")
    for section in LIST_INVENTORY_SECTIONS:
        expectation = manifest["expected_inventory"][section]
        observed = rights[section]
        if len(observed) != expectation["count"]:
            raise AuditError(f"rights inventory {section} count mismatch")
        if canonical_value_sha256(observed) != expectation["sha256"]:
            raise AuditError(f"rights inventory {section} digest mismatch")
    for field in (
        "patches",
        "assets",
        "generated_files",
        "implementation_sources",
        "tracked_files",
    ):
        for index, record in enumerate(rights[field]):
            validate_file_record(record, f"rights inventory {field}[{index}]")
    tracked_identities = {
        (item["path"], item["sha256"]) for item in rights["tracked_files"]
    }
    for exception in manifest["exact_hash_exceptions"]:
        if (exception["source_path"], exception["sha256"]) not in tracked_identities:
            raise AuditError(
                "exact hash exception is absent from tracked source inventory"
            )
    components = rights.get("components")
    if not isinstance(components, list):
        raise AuditError("rights inventory components must be a list")
    if components != component_inventory(rights["lockfiles"], manifest):
        raise AuditError("rights components are not the exact lockfile projection")
    exact_resolved = {
        (item["name"], item["version"], item["license"], item["integrity"])
        for item in manifest["resolved_third_party_components"]
    }
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise AuditError(f"rights component {index} must be an object")
        for field in (
            "name",
            "version",
            "integrity",
            "license",
            "lockfile",
            "package_key",
            "raw_resolution",
            "resolution_type",
        ):
            require_nonempty_string(component, field, f"rights component {index}")
        unknown = component["license"] == "NOASSERTION"
        if component.get("quarantined_for_extraction") is not unknown:
            raise AuditError(
                f"rights component {index} quarantine does not match license"
            )
        component_identity = (
            component["name"],
            component["version"],
            component["license"],
            component["integrity"],
        )
        if not unknown and component_identity not in exact_resolved:
            raise AuditError(
                f"rights component {index} does not match exact resolved evidence"
            )
    component_identities = {
        (item["name"], item["version"], item["license"], item["integrity"])
        for item in components
    }
    missing_resolved = exact_resolved - component_identities
    if missing_resolved:
        raise AuditError("resolved component evidence is absent from exact locks")
    resolved_by_name_version = {
        (item["name"], item["version"]): item
        for item in manifest["resolved_third_party_components"]
    }
    for candidate in manifest["candidates"]:
        for dependency in candidate["dependencies"]:
            if dependency["license"] == "NOASSERTION":
                continue
            resolved = resolved_by_name_version.get(
                (dependency["name"], dependency["version"])
            )
            if resolved is None:
                raise AuditError(
                    f"candidate {candidate['id']} uses an unlocked dependency"
                )
            component_identity = (
                resolved["name"],
                resolved["version"],
                resolved["license"],
                resolved["integrity"],
            )
            if component_identity not in component_identities:
                raise AuditError(
                    f"candidate {candidate['id']} dependency is absent from exact lock inventory"
                )
    fingerprint_section = rights.get("candidate_source_fingerprints")
    if not isinstance(fingerprint_section, dict):
        raise AuditError("candidate source fingerprints are missing")
    if (
        fingerprint_section.get("algorithm")
        != "normalized-token-window-sha256-string-digest-v2"
    ):
        raise AuditError("candidate source fingerprint algorithm mismatch")
    if (
        fingerprint_section.get("verbatim_algorithm")
        != "verbatim-lexical-window-sha256-v1"
    ):
        raise AuditError("candidate verbatim fingerprint algorithm mismatch")
    if (
        fingerprint_section.get("wrapper_stripped_algorithm")
        != "wrapper-stripped-lexical-window-sha256-v1"
    ):
        raise AuditError("candidate wrapper-stripped fingerprint algorithm mismatch")
    records = fingerprint_section.get("records")
    if not isinstance(records, list):
        raise AuditError("candidate source fingerprint records must be a list")
    fingerprint_expectation = manifest["expected_inventory"][
        "candidate_source_fingerprints"
    ]
    if len(records) != fingerprint_expectation["count"]:
        raise AuditError("candidate source fingerprint record count mismatch")
    observed_fingerprint_count = sum(
        len(record.get("fingerprints", []))
        for record in records
        if isinstance(record, dict)
    )
    if observed_fingerprint_count != fingerprint_expectation["fingerprint_count"]:
        raise AuditError("candidate source fingerprint count mismatch")
    observed_verbatim_count = sum(
        len(record.get("verbatim_fingerprints", []))
        for record in records
        if isinstance(record, dict)
    )
    if observed_verbatim_count != fingerprint_expectation["verbatim_fingerprint_count"]:
        raise AuditError("candidate verbatim fingerprint count mismatch")
    observed_wrapper_stripped_count = sum(
        len(record.get("wrapper_stripped_fingerprints", []))
        for record in records
        if isinstance(record, dict)
    )
    if (
        observed_wrapper_stripped_count
        != fingerprint_expectation["wrapper_stripped_fingerprint_count"]
    ):
        raise AuditError("candidate wrapper-stripped fingerprint count mismatch")
    if canonical_value_sha256(fingerprint_section) != fingerprint_expectation["sha256"]:
        raise AuditError("candidate source fingerprint digest mismatch")
    manifest_records = {
        (candidate["id"], item["path"]): item
        for candidate in manifest["candidates"]
        for item in candidate["paths"]
        if Path(item["path"]).suffix.lower() in CANDIDATE_TEXT_SUFFIXES
    }
    expected = set(manifest_records)
    actual: set[tuple[str, str]] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise AuditError(f"candidate fingerprint record {index} must be an object")
        candidate_id = require_nonempty_string(
            record, "candidate_id", f"fingerprint {index}"
        )
        path = require_nonempty_string(record, "path", f"fingerprint {index}")
        identity = (candidate_id, path)
        if identity in actual:
            raise AuditError(f"duplicate candidate fingerprint record: {identity}")
        manifest_record = manifest_records.get(identity)
        if manifest_record is None:
            raise AuditError(f"fingerprint {index} is not bound to a manifest path")
        if not is_sha256(record.get("file_sha256")):
            raise AuditError(f"fingerprint {index} has invalid file digest")
        if record["file_sha256"] != manifest_record["sha256"]:
            raise AuditError(f"fingerprint {index} file digest differs from manifest")
        line_count = record.get("line_count")
        if not isinstance(line_count, int) or line_count < 1:
            raise AuditError(f"fingerprint {index} has invalid line count")
        manifest_line_count = manifest["candidate_text_line_counts"][path]
        if line_count != manifest_line_count:
            raise AuditError(f"fingerprint {index} line count differs from manifest")
        segments = record.get("segments")
        if not isinstance(segments, list) or not segments:
            raise AuditError(f"fingerprint {index} segments must be nonempty")
        manifest_segments = manifest_record.get("segments")
        if manifest_segments is None:
            expected_segments = [
                {
                    "start_line": 1,
                    "end_line": manifest_line_count,
                    "purpose": "Whole pinned file",
                }
            ]
        else:
            expected_segments = manifest_segments
        if segments != expected_segments:
            raise AuditError(f"fingerprint {index} segments differ from manifest")
        if any(segment["end_line"] > line_count for segment in segments):
            raise AuditError(f"fingerprint {index} segment exceeds line count")
        fingerprints = record.get("fingerprints")
        if not isinstance(fingerprints, list) or any(
            not is_sha256(item) for item in fingerprints
        ):
            raise AuditError(f"fingerprint {index} has invalid token digests")
        verbatim_fingerprints = record.get("verbatim_fingerprints")
        if not isinstance(verbatim_fingerprints, list) or any(
            not is_sha256(item) for item in verbatim_fingerprints
        ):
            raise AuditError(f"fingerprint {index} has invalid verbatim digests")
        wrapper_stripped_fingerprints = record.get("wrapper_stripped_fingerprints")
        if not isinstance(wrapper_stripped_fingerprints, list) or any(
            not is_sha256(item) for item in wrapper_stripped_fingerprints
        ):
            raise AuditError(
                f"fingerprint {index} has invalid wrapper-stripped digests"
            )
        segment_records = record.get("segment_fingerprints")
        if not isinstance(segment_records, list) or len(segment_records) != len(
            record.get("segments", [])
        ):
            raise AuditError(f"fingerprint {index} segment coverage mismatch")
        segment_union: set[str] = set()
        verbatim_segment_union: set[str] = set()
        wrapper_stripped_segment_union: set[str] = set()
        for segment_index, segment_record in enumerate(segment_records):
            if not isinstance(segment_record, dict):
                raise AuditError(
                    f"fingerprint {index} segment {segment_index} must be an object"
                )
            segment_values = segment_record.get("fingerprints")
            if not isinstance(segment_values, list) or any(
                not is_sha256(item) for item in segment_values
            ):
                raise AuditError(
                    f"fingerprint {index} segment {segment_index} has invalid digests"
                )
            verbatim_values = segment_record.get("verbatim_fingerprints")
            if not isinstance(verbatim_values, list) or any(
                not is_sha256(item) for item in verbatim_values
            ):
                raise AuditError(
                    f"fingerprint {index} segment {segment_index} has invalid verbatim digests"
                )
            wrapper_stripped_values = segment_record.get(
                "wrapper_stripped_fingerprints"
            )
            if not isinstance(wrapper_stripped_values, list) or any(
                not is_sha256(item) for item in wrapper_stripped_values
            ):
                raise AuditError(
                    f"fingerprint {index} segment {segment_index} has invalid wrapper-stripped digests"
                )
            expected_segment = record["segments"][segment_index]
            observed_segment = {
                key: value
                for key, value in segment_record.items()
                if key
                not in {
                    "fingerprints",
                    "verbatim_fingerprints",
                    "wrapper_stripped_fingerprints",
                }
            }
            if observed_segment != expected_segment:
                raise AuditError(
                    f"fingerprint {index} segment {segment_index} boundary mismatch"
                )
            segment_union.update(segment_values)
            verbatim_segment_union.update(verbatim_values)
            wrapper_stripped_segment_union.update(wrapper_stripped_values)
        if sorted(segment_union) != fingerprints:
            raise AuditError(f"fingerprint {index} spans segment boundaries")
        if sorted(verbatim_segment_union) != verbatim_fingerprints:
            raise AuditError(f"verbatim fingerprint {index} spans segment boundaries")
        if sorted(wrapper_stripped_segment_union) != wrapper_stripped_fingerprints:
            raise AuditError(
                f"wrapper-stripped fingerprint {index} spans segment boundaries"
            )
        actual.add(identity)
    if actual != expected:
        raise AuditError("candidate source fingerprint coverage mismatch")


def properties(component: dict[str, Any]) -> dict[str, str]:
    values = component.get("properties")
    if not isinstance(values, list):
        raise AuditError("CycloneDX component properties must be a list")
    result: dict[str, str] = {}
    for item in values:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("value"), str)
        ):
            raise AuditError("CycloneDX component property is invalid")
        result[item["name"]] = item["value"]
    return result


def validate_cyclonedx(
    sbom: dict[str, Any],
    rights: dict[str, Any],
    manifest: dict[str, Any],
    manifest_sha256: str,
) -> None:
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6":
        raise AuditError("SBOM must be CycloneDX 1.6")
    if sbom.get("$schema") != "https://cyclonedx.org/schema/bom-1.6.schema.json":
        raise AuditError("CycloneDX schema URI mismatch")
    components = sbom.get("components")
    if not isinstance(components, list):
        raise AuditError("CycloneDX components must be a list")
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise AuditError(f"CycloneDX component {index} must be an object")
        licenses = component.get("licenses")
        if not isinstance(licenses, list) or not licenses:
            raise AuditError(f"CycloneDX component {index} lacks license evidence")
        props = properties(component)
        for field in (
            "sklegal:audit:integrity",
            "sklegal:audit:lockfiles",
            "sklegal:audit:quarantined-for-extraction",
            "sklegal:audit:raw-resolution",
            "sklegal:audit:resolution-type",
        ):
            if not props.get(field):
                raise AuditError(f"CycloneDX component {index} lacks {field}")
        unknown = any(
            item.get("license", {}).get("name") == "NOASSERTION"
            for item in licenses
            if isinstance(item, dict)
        )
        if unknown and props["sklegal:audit:quarantined-for-extraction"] != "true":
            raise AuditError(
                f"CycloneDX component {index} leaves NOASSERTION unquarantined"
            )
        if props["sklegal:audit:resolution-type"] != "registry" and "purl" in component:
            raise AuditError(
                f"CycloneDX component {index} assigns a purl to a non-registry resolution"
            )
    metadata = sbom.get("metadata")
    if not isinstance(metadata, dict):
        raise AuditError("CycloneDX metadata is missing")
    metadata_properties = properties(metadata)
    if (
        metadata_properties.get("sklegal:audit:dependency-graph")
        != "absent-flat-locked-component-inventory"
    ):
        raise AuditError("CycloneDX flat inventory limitation is missing")
    expected = build_cyclonedx(rights, manifest, manifest_sha256)
    if canonical_json(sbom) != canonical_json(expected):
        raise AuditError(
            "CycloneDX SBOM is not the deterministic projection of rights inventory"
        )


def validate(
    manifest_path: Path,
    rights_path: Path,
    sbom_path: Path,
    target: Path | None,
    project_root: Path | None,
) -> str:
    manifest = load_json(manifest_path)
    validate_manifest(manifest)
    manifest_sha256 = manifest_digest(manifest_path)
    rights = load_json(rights_path)
    sbom = load_json(sbom_path)
    validate_rights_inventory(rights, manifest, manifest_sha256)
    validate_cyclonedx(sbom, rights, manifest, manifest_sha256)
    evidence = [f"{len(manifest['candidates'])} candidate decisions"]
    evidence.append(f"{len(rights.get('components', []))} package records")
    evidence.append(f"{len(sbom.get('components', []))} CycloneDX components")
    if target is not None:
        snapshot = repository_snapshot(target, manifest["repository"])
        compare_repository_pin(manifest, snapshot)
        verify_evidence_hashes(target, manifest)
        current = build_rights_inventory(target, manifest, manifest_sha256)
        if canonical_json(current) != rights_path.read_text(encoding="utf-8"):
            raise AuditError("rights inventory does not match the pinned target")
        current_sbom = build_cyclonedx(current, manifest, manifest_sha256)
        if canonical_json(current_sbom) != sbom_path.read_text(encoding="utf-8"):
            raise AuditError("CycloneDX SBOM does not match the pinned target")
        evidence.append("live target pin")
    if project_root is not None:
        inspected, windows = verify_no_code_copy(project_root, manifest, rights)
        evidence.append(
            f"{inspected} SKLegal implementation files and {windows} token windows checked"
        )
    return "doc-haus provenance valid: " + ", ".join(evidence)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subcommands = result.add_subparsers(dest="command", required=True)
    inventory = subcommands.add_parser("rights-inventory")
    inventory.add_argument("--target", type=Path, required=True)
    inventory.add_argument("--manifest", type=Path, required=True)
    sbom = subcommands.add_parser("sbom")
    sbom.add_argument("--target", type=Path, required=True)
    sbom.add_argument("--manifest", type=Path, required=True)
    check = subcommands.add_parser("validate")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--rights-inventory", type=Path, required=True)
    check.add_argument("--sbom", type=Path, required=True)
    check.add_argument("--target", type=Path)
    check.add_argument("--project-root", type=Path)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        manifest = load_json(arguments.manifest)
        validate_manifest(manifest)
        manifest_sha256 = manifest_digest(arguments.manifest)
        if arguments.command == "rights-inventory":
            print(
                canonical_json(
                    build_rights_inventory(arguments.target, manifest, manifest_sha256)
                ),
                end="",
            )
            return 0
        if arguments.command == "sbom":
            rights = build_rights_inventory(arguments.target, manifest, manifest_sha256)
            print(
                canonical_json(build_cyclonedx(rights, manifest, manifest_sha256)),
                end="",
            )
            return 0
        print(
            validate(
                arguments.manifest,
                arguments.rights_inventory,
                arguments.sbom,
                arguments.target,
                arguments.project_root,
            )
        )
        return 0
    except (
        AuditError,
        OSError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as error:
        print(f"doc-haus provenance invalid: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
