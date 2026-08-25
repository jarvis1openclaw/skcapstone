import copy
import hashlib
import shutil
import subprocess
import sys

import pytest

import scripts.validate_drafting_style_profiles as style_profiles


def write_json(path, value):
    path.write_text(style_profiles.json.dumps(value))
    return path


def test_official_drafting_style_profiles_validate():
    style_profiles.validate()


def test_style_profile_validation_is_checkout_local(tmp_path):
    isolated = tmp_path / "sklegal"
    shutil.copytree(
        style_profiles.DATA.parent,
        isolated / "config/drafting_styles",
    )
    (isolated / "scripts").mkdir()
    shutil.copy2(style_profiles.Path(style_profiles.__file__), isolated / "scripts")

    result = subprocess.run(
        [sys.executable, "scripts/validate_drafting_style_profiles.py"],
        cwd=isolated,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "hammerTime").exists()


def test_locator_evidence_contains_no_unproven_line_counts():
    evidence = style_profiles.json.loads(style_profiles.LOCATOR_EVIDENCE.read_text())

    assert "line_count" not in evidence
    assert "artifacts" not in evidence


def test_spr_001_source_provenance_hash_fails_closed(monkeypatch, tmp_path):
    profile = style_profiles.json.loads(style_profiles.DATA.read_text())
    profile["rules"][0]["source"]["source_sha256"] = "0" * 64
    path = write_json(tmp_path / "profiles.json", profile)
    monkeypatch.setattr(style_profiles, "DATA", path)

    with pytest.raises(ValueError, match="style profile hash mismatch"):
        style_profiles.validate()


def test_spr_001_locator_bound_fails_closed(monkeypatch, tmp_path):
    profile = style_profiles.json.loads(style_profiles.DATA.read_text())
    profile["rules"][0]["source"]["locator"]["line_end"] += 1
    path = write_json(tmp_path / "profiles.json", profile)
    monkeypatch.setattr(style_profiles, "DATA", path)

    with pytest.raises(ValueError, match="style profile hash mismatch"):
        style_profiles.validate()


def test_each_profile_locator_fails_closed(monkeypatch, tmp_path):
    profile = style_profiles.json.loads(style_profiles.DATA.read_text())
    profile["rules"][-1]["source"]["locator"]["normalized_path"] = "changed"
    path = write_json(tmp_path / "profiles.json", profile)
    monkeypatch.setattr(style_profiles, "DATA", path)

    with pytest.raises(ValueError, match="style profile hash mismatch"):
        style_profiles.validate()


def test_provenance_pin_fails_closed(monkeypatch, tmp_path):
    evidence = copy.deepcopy(
        style_profiles.json.loads(style_profiles.LOCATOR_EVIDENCE.read_text())
    )
    evidence["provenance_sha256"] = "0" * 64
    path = write_json(tmp_path / "locator-evidence.json", evidence)
    monkeypatch.setattr(style_profiles, "LOCATOR_EVIDENCE", path)
    monkeypatch.setattr(
        style_profiles,
        "LOCATOR_EVIDENCE_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    with pytest.raises(ValueError, match="semantic provenance evidence mismatch"):
        style_profiles.validate()


def test_routing_pin_fails_closed(monkeypatch, tmp_path):
    profile = style_profiles.json.loads(style_profiles.DATA.read_text())
    profile["semantic_provenance"]["output_sha256"] = "0" * 64
    path = write_json(tmp_path / "profiles.json", profile)
    monkeypatch.setattr(style_profiles, "DATA", path)

    with pytest.raises(ValueError, match="style profile hash mismatch"):
        style_profiles.validate()
