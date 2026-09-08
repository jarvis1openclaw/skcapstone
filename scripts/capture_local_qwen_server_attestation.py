#!/usr/bin/env python3
"""Capture non-secret server-side evidence for the local Qwen-only transport."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config/processes/howtowinincourt/local-qwen-server-attestation.v1.json"
MODELS_URL = os.environ.get(
    "SKLEGAL_QWEN_MODELS_URL", "http://127.0.0.1:11439/v1/models"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def command(*args: str) -> bytes:
    return subprocess.check_output(args)


def main() -> None:
    pid = (
        command(
            "systemctl",
            "--user",
            "show",
            "vllm-qwen38.service",
            "--property=MainPID",
            "--value",
        )
        .decode()
        .strip()
    )
    if not pid or pid == "0":
        raise RuntimeError("authoritative Qwen server is not running")
    cmdline = (
        Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode().strip()
    )
    unit = command("systemctl", "--user", "cat", "vllm-qwen38.service")
    parts = cmdline.split()
    model_root = Path(parts[parts.index("serve") + 1])
    model_config = (model_root / "config.json").read_bytes()
    model_index = (model_root / "model.safetensors.index.json").read_bytes()
    model_revision = (
        f"config-sha256:{sha(model_config)};index-sha256:{sha(model_index)}"
    )
    with urllib.request.urlopen(MODELS_URL, timeout=10) as response:
        models_raw = response.read()
    model_inventory = json.loads(models_raw)
    model_ids = sorted(item["id"] for item in model_inventory["data"])
    if not model_ids or any(
        not model_id.startswith("qwen3.8") for model_id in model_ids
    ):
        raise RuntimeError("direct model server exposes a non-Qwen model")
    if "vllm serve" not in cmdline or "--served-model-name" not in cmdline:
        raise RuntimeError("unexpected local server process")
    attestation = {
        "schema": "sklegal.local-model-server-attestation/v1",
        "captured_at": datetime.now(UTC).isoformat(),
        "host": command("hostname").decode().strip(),
        "service": "vllm-qwen38.service",
        "service_main_pid": int(pid),
        "service_unit_sha256": sha(unit),
        "process_command_sha256": sha(cmdline.encode()),
        "transport": "direct-local-vllm",
        "models_endpoint": "/v1/models",
        "models_response_sha256": sha(models_raw),
        "model_config_sha256": sha(model_config),
        "model_index_sha256": sha(model_index),
        "model_revision": model_revision,
        "served_model_ids": model_ids,
        "router_present": False,
        "frontier_catalog_entries": [],
        "frontier_dispatch_possible_on_this_server": False,
        "evidence_basis": "The serving process is direct vLLM with one Qwen model root, and its server-side model inventory contains only Qwen3.8 aliases.",
        "protected_traffic_sent": False,
        "credentials_used": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes((json.dumps(attestation, indent=2, sort_keys=True) + "\n").encode())


if __name__ == "__main__":
    main()
