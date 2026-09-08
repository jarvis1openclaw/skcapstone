from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/fleet/skfleet-pi-model-catalog.py"


def _module():
    spec = importlib.util.spec_from_file_location("pi_model_catalog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _document():
    return {
        "providers": {
            "skgateway": {
                "opaqueReference": "preserve-me",
                "models": [
                    {
                        "id": "glm-4.6",
                        "name": "raw small",
                        "reasoning": True,
                        "input": ["text"],
                        "contextWindow": 200000,
                    },
                    {
                        "id": "glm-4.7",
                        "name": "raw large",
                        "reasoning": True,
                        "input": ["text"],
                        "contextWindow": 200000,
                    },
                ],
            }
        }
    }


def test_reconcile_adds_only_six_routes_and_preserves_secret_fields():
    module = _module()
    original = _document()
    updated, changed = module.reconcile(original)
    models = updated["providers"]["skgateway"]["models"]
    by_id = {item["id"]: item for item in models}
    assert changed == list(module.ALIASES)
    assert updated["providers"]["skgateway"]["opaqueReference"] == "preserve-me"
    assert original == _document()
    assert set(module.ALIASES) <= by_id.keys()
    assert by_id["sk-glm-l"]["contextWindow"] == 200000
    assert by_id["sk-zai-s"]["reasoning"] is True
    assert len(models) == 8


def test_reconcile_is_idempotent_and_repairs_drift():
    module = _module()
    updated, _ = module.reconcile(_document())
    second, changed = module.reconcile(updated)
    assert changed == []
    assert second == updated
    by_id = {item["id"]: item for item in second["providers"]["skgateway"]["models"]}
    by_id["sk-zai-l"]["contextWindow"] = 1
    repaired, changed = module.reconcile(second)
    assert changed == ["sk-zai-l"]
    repaired_by_id = {item["id"]: item for item in repaired["providers"]["skgateway"]["models"]}
    assert repaired_by_id["sk-zai-l"]["contextWindow"] == 200000


def test_atomic_write_preserves_mode_and_unrelated_fields(tmp_path: Path):
    module = _module()
    path = tmp_path / "models.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")
    path.chmod(0o600)
    updated, changed, info = module.load_and_reconcile(path)
    assert changed
    module.write_atomic(path, updated, info)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (
        json.loads(path.read_text())["providers"]["skgateway"]["opaqueReference"] == "preserve-me"
    )


def test_rejects_insecure_or_incomplete_catalog(tmp_path: Path):
    module = _module()
    path = tmp_path / "models.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="group or other"):
        module.load_and_reconcile(path)
    path.chmod(0o600)
    document = _document()
    document["providers"]["skgateway"]["models"].pop()
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="missing source model metadata"):
        module.load_and_reconcile(path)


def test_rejects_symlink(tmp_path: Path):
    module = _module()
    target = tmp_path / "target.json"
    target.write_text(json.dumps(_document()), encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "models.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        module.load_and_reconcile(link)
