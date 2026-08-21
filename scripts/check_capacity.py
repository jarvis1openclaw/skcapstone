#!/usr/bin/env python3
"""Evaluate a captured host snapshot without changing the host."""

from __future__ import annotations

import argparse
import json
import posixpath
import sys
from pathlib import Path
from typing import Any


GIB = 1024**3
SEVERITY = {"PASS": 0, "WARNING": 1, "CRITICAL": 2}


class InputError(ValueError):
    """Raised when a policy or snapshot cannot be evaluated safely."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InputError(f"top-level JSON in {path} must be an object")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{field} must be numeric")
    return float(value)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(f"{field} must be an integer")
    return value


def _absolute_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise InputError(f"{field} must be an absolute path")
    return posixpath.normpath(value)


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    if _integer(snapshot.get("schema_version"), "snapshot.schema_version") != 1:
        raise InputError("snapshot.schema_version must be 1")
    if not isinstance(snapshot.get("host"), str):
        raise InputError("snapshot.host must be a string")
    mounts = snapshot.get("mounts")
    if not isinstance(mounts, list) or not mounts:
        raise InputError("snapshot.mounts must be a non-empty list")
    for index, mount in enumerate(mounts):
        if not isinstance(mount, dict):
            raise InputError(f"snapshot.mounts[{index}] must be an object")
        _absolute_path(mount.get("target"), f"snapshot.mounts[{index}].target")
        total = _integer(
            mount.get("total_bytes"), f"snapshot.mounts[{index}].total_bytes"
        )
        available = _integer(
            mount.get("available_bytes"),
            f"snapshot.mounts[{index}].available_bytes",
        )
        used_percent = _number(
            mount.get("used_percent"),
            f"snapshot.mounts[{index}].used_percent",
        )
        if total <= 0 or available < 0 or available > total:
            raise InputError(f"snapshot.mounts[{index}] has invalid byte counts")
        if not 0 <= used_percent <= 100:
            raise InputError(f"snapshot.mounts[{index}].used_percent is invalid")

    ports = snapshot.get("listening_tcp_ports")
    if not isinstance(ports, list):
        raise InputError("snapshot.listening_tcp_ports must be a list")
    for index, port in enumerate(ports):
        value = _integer(port, f"snapshot.listening_tcp_ports[{index}]")
        if not 1 <= value <= 65535:
            raise InputError(f"snapshot.listening_tcp_ports[{index}] is invalid")

    docker = snapshot.get("docker")
    if not isinstance(docker, dict):
        raise InputError("snapshot.docker must be an object")
    volumes = docker.get("volumes")
    if not isinstance(volumes, list):
        raise InputError("snapshot.docker.volumes must be a list")
    if not all(isinstance(name, str) and name for name in volumes):
        raise InputError("snapshot.docker.volumes must contain non-empty strings")
    if not isinstance(docker.get("active_containers"), list):
        raise InputError("snapshot.docker.active_containers must be a list")


def validate_policy(policy: dict[str, Any]) -> None:
    if _integer(policy.get("schema_version"), "policy.schema_version") != 1:
        raise InputError("policy.schema_version must be 1")
    checks = policy.get("mount_checks")
    if not isinstance(checks, list) or not checks:
        raise InputError("policy.mount_checks must be a non-empty list")
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or not isinstance(check.get("name"), str):
            raise InputError(f"policy.mount_checks[{index}] must have a name")
        _absolute_path(check.get("path"), f"policy.mount_checks[{index}].path")
        warning_used = _number(
            check.get("warning_used_percent"),
            f"policy.mount_checks[{index}].warning_used_percent",
        )
        critical_used = _number(
            check.get("critical_used_percent"),
            f"policy.mount_checks[{index}].critical_used_percent",
        )
        warning_free = _number(
            check.get("warning_available_after_reserve_gib"),
            f"policy.mount_checks[{index}].warning_available_after_reserve_gib",
        )
        critical_free = _number(
            check.get("critical_available_after_reserve_gib"),
            f"policy.mount_checks[{index}].critical_available_after_reserve_gib",
        )
        reserve = _number(
            check.get("deployment_reserve_gib"),
            f"policy.mount_checks[{index}].deployment_reserve_gib",
        )
        if not 0 <= warning_used < critical_used <= 100:
            raise InputError(f"policy.mount_checks[{index}] used thresholds are invalid")
        if not 0 <= critical_free < warning_free or reserve < 0:
            raise InputError(f"policy.mount_checks[{index}] free thresholds are invalid")

    proposed = policy.get("proposed_tcp_ports")
    if not isinstance(proposed, list):
        raise InputError("policy.proposed_tcp_ports must be a list")
    proposed_ports: set[int] = set()
    for index, item in enumerate(proposed):
        if not isinstance(item, dict) or not isinstance(item.get("service"), str):
            raise InputError(f"policy.proposed_tcp_ports[{index}] is invalid")
        port = _integer(item.get("port"), f"policy.proposed_tcp_ports[{index}].port")
        if not 1 <= port <= 65535 or port in proposed_ports:
            raise InputError(f"policy.proposed_tcp_ports[{index}].port is invalid or duplicated")
        proposed_ports.add(port)


def select_mount(path: str, mounts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the most specific mount containing path."""
    normalized_path = _absolute_path(path, "path")
    candidates = []
    for mount in mounts:
        target = _absolute_path(mount.get("target"), "mount.target")
        if target == "/" or normalized_path == target or normalized_path.startswith(target + "/"):
            candidates.append((len(target), mount))
    if not candidates:
        raise InputError(f"no mount contains {normalized_path}")
    return max(candidates, key=lambda item: item[0])[1]


def _max_status(statuses: list[str]) -> str:
    return max(statuses, key=SEVERITY.__getitem__) if statuses else "PASS"


def evaluate(policy: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    validate_policy(policy)
    validate_snapshot(snapshot)
    expected_host = policy.get("host")
    if expected_host and snapshot["host"] != expected_host:
        raise InputError(
            f"snapshot host {snapshot['host']!r} does not match policy host {expected_host!r}"
        )

    mount_results = []
    for check in policy["mount_checks"]:
        mount = select_mount(check["path"], snapshot["mounts"])
        available_gib = mount["available_bytes"] / GIB
        available_after_reserve_gib = available_gib - check["deployment_reserve_gib"]
        used_percent = float(mount["used_percent"])
        if (
            used_percent >= check["critical_used_percent"]
            or available_after_reserve_gib
            < check["critical_available_after_reserve_gib"]
        ):
            status = "CRITICAL"
        elif (
            used_percent >= check["warning_used_percent"]
            or available_after_reserve_gib
            < check["warning_available_after_reserve_gib"]
        ):
            status = "WARNING"
        else:
            status = "PASS"
        mount_results.append(
            {
                "name": check["name"],
                "path": check["path"],
                "selected_mount": mount["target"],
                "filesystem": mount.get("filesystem", "unknown"),
                "status": status,
                "used_percent": used_percent,
                "available_gib": round(available_gib, 2),
                "deployment_reserve_gib": check["deployment_reserve_gib"],
                "available_after_reserve_gib": round(available_after_reserve_gib, 2),
            }
        )

    listening = set(snapshot["listening_tcp_ports"])
    conflicts = [
        item for item in policy["proposed_tcp_ports"] if item["port"] in listening
    ]
    port_status = "CRITICAL" if conflicts else "PASS"

    docker = snapshot["docker"]
    volume_names = set(docker["volumes"])
    referenced: set[str] = set()
    for index, container in enumerate(docker["active_containers"]):
        if not isinstance(container, dict):
            raise InputError(f"snapshot.docker.active_containers[{index}] must be an object")
        refs = container.get("named_volumes")
        if not isinstance(refs, list) or not all(isinstance(name, str) for name in refs):
            raise InputError(
                f"snapshot.docker.active_containers[{index}].named_volumes must be a string list"
            )
        referenced.update(refs)
    orphan_volumes = sorted(volume_names - referenced)
    missing_volumes = sorted(referenced - volume_names)
    if missing_volumes:
        docker_status = "CRITICAL"
    elif orphan_volumes:
        docker_status = "WARNING"
    else:
        docker_status = "PASS"

    statuses = [item["status"] for item in mount_results]
    statuses.extend([port_status, docker_status])
    return {
        "host": snapshot["host"],
        "observed_at": snapshot.get("observed_at"),
        "status": _max_status(statuses),
        "mounts": mount_results,
        "ports": {"status": port_status, "conflicts": conflicts},
        "docker_volumes": {
            "status": docker_status,
            "declared": sorted(volume_names),
            "referenced_by_active_containers": sorted(referenced),
            "orphan_volumes": orphan_volumes,
            "missing_volumes": missing_volumes,
        },
    }


def render_text(result: dict[str, Any]) -> str:
    lines = [
        f"host={result['host']}",
        f"observed_at={result.get('observed_at') or 'unknown'}",
        f"status={result['status']}",
    ]
    for mount in result["mounts"]:
        lines.append(
            "mount "
            f"name={mount['name']} status={mount['status']} "
            f"selected={mount['selected_mount']} used={mount['used_percent']:.1f}% "
            f"available={mount['available_gib']:.2f}GiB "
            f"reserve={mount['deployment_reserve_gib']}GiB "
            f"after_reserve={mount['available_after_reserve_gib']:.2f}GiB"
        )
    conflicts = result["ports"]["conflicts"]
    conflict_text = ",".join(
        f"{item['service']}:{item['port']}" for item in conflicts
    ) or "none"
    lines.append(
        f"ports status={result['ports']['status']} conflicts={conflict_text}"
    )
    docker = result["docker_volumes"]
    lines.append(
        "docker-volumes "
        f"status={docker['status']} "
        f"orphans={','.join(docker['orphan_volumes']) or 'none'} "
        f"missing={','.join(docker['missing_volumes']) or 'none'}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        result = evaluate(_load_json(args.policy), _load_json(args.snapshot))
    except InputError as exc:
        print(f"input-error: {exc}", file=sys.stderr)
        return 3
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_text(result))
    return SEVERITY[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
