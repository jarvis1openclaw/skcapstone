#!/usr/bin/env python3
"""Reconcile non-secret SKGateway logical GLM routes into Pi's model catalog."""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
import tempfile
from pathlib import Path

ALIASES = {
    "sk-glm-s": ("glm-4.6", "GLM small via SKGateway"),
    "sk-glm-m": ("glm-4.6", "GLM medium via SKGateway"),
    "sk-glm-l": ("glm-4.7", "GLM large via SKGateway"),
    "sk-zai-s": ("glm-4.6", "Z.ai small via SKGateway"),
    "sk-zai-m": ("glm-4.6", "Z.ai medium via SKGateway"),
    "sk-zai-l": ("glm-4.7", "Z.ai large via SKGateway"),
}


def catalog_path() -> Path:
    root = Path(os.environ.get("PI_CODING_AGENT_DIR", "~/.pi/agent")).expanduser()
    return root / "models.json"


def _secure_regular_file(path: Path) -> os.stat_result:
    if path.is_symlink():
        raise ValueError("catalog must not be a symlink")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("catalog must be a regular file")
    if info.st_uid != os.getuid():
        raise ValueError("catalog must be owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("catalog must not be accessible by group or other")
    return info


def reconcile(document: dict) -> tuple[dict, list[str]]:
    providers = document.get("providers")
    gateway = providers.get("skgateway") if isinstance(providers, dict) else None
    models = gateway.get("models") if isinstance(gateway, dict) else None
    if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
        raise ValueError("providers.skgateway.models must be a list of objects")
    by_id = {item.get("id"): item for item in models if isinstance(item.get("id"), str)}
    missing = sorted({source for source, _ in ALIASES.values()} - by_id.keys())
    if missing:
        raise ValueError("missing source model metadata: " + ",".join(missing))

    updated = copy.deepcopy(document)
    target_models = updated["providers"]["skgateway"]["models"]
    target_by_id = {item.get("id"): item for item in target_models}
    changed = []
    for alias, (source, name) in ALIASES.items():
        expected = copy.deepcopy(by_id[source])
        expected["id"] = alias
        expected["name"] = name
        current = target_by_id.get(alias)
        if current == expected:
            continue
        if current is None:
            target_models.append(expected)
        else:
            current.clear()
            current.update(expected)
        changed.append(alias)
    return updated, changed


def load_and_reconcile(path: Path) -> tuple[dict, list[str], os.stat_result]:
    info = _secure_regular_file(path)
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError("catalog root must be an object")
    updated, changed = reconcile(document)
    return updated, changed, info


def write_atomic(path: Path, document: dict, info: os.stat_result) -> None:
    payload = (json.dumps(document, indent=2, ensure_ascii=True) + "\n").encode()
    json.loads(payload)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(info.st_mode))
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=catalog_path())
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    updated, changed, info = load_and_reconcile(args.catalog)
    if changed and args.apply:
        write_atomic(args.catalog, updated, info)
    state = "changed" if changed else "current"
    print(f"PI_MODEL_CATALOG|{state}|aliases={len(ALIASES)}|pending={len(changed)}")
    return 1 if changed and not args.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
