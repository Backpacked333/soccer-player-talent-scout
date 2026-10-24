"""Offline CLI and private JSON artifact tests using synthetic fixtures only."""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.intelligence.action_value import artifact_fingerprint, load_artifact_json
from app.intelligence.action_value_cli import POLICY_BYTES, SNAPSHOT_BYTES, _read_bounded, main
from app.intelligence.contracts import EvaluationReport
from test_action_value import policy, snapshot


def inputs(tmp_path, matches=4):
    source = tmp_path / "private-input.json"
    configuration = tmp_path / "policy.json"
    source.write_text(snapshot(matches=matches).model_dump_json(), encoding="utf-8")
    configuration.write_text(policy().model_dump_json(), encoding="utf-8")
    return ["--input", str(source), "--policy", str(configuration)]


def test_train_evaluate_cli_no_network_private_output_no_holdout_leakage(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline trainer attempted network access")
    monkeypatch.setattr(socket, "socket", forbidden)
    arguments = inputs(tmp_path)
    output = tmp_path / "artifact.json"
    assert main(["train", *arguments, "--output", str(output), "--evaluate"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "fitted_not_activated"
    assert summary["mode"] == "demo" and summary["validation_status"] == "unvalidated_research"
    assert summary["training_matches"] == 3 and summary["training_actions"] == 18
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    artifact = load_artifact_json(output.read_bytes(), authorized_scope=snapshot().scope)
    assert artifact.training_match_ids == ["m0", "m1", "m2"]
    assert artifact.evaluation.status == "evaluated"
    assert artifact.evaluation.test_match_ids == ["m3"]
    assert not set(artifact.training_match_ids) & set(artifact.evaluation.test_match_ids)
    assert artifact_fingerprint(artifact) == summary["artifact_fingerprint"]
    assert artifact_fingerprint(artifact.model_copy(update={"evaluation": None})) == summary["artifact_fingerprint"]
    # Refitting the same input makes the same model ID, not a time-based ID.
    second = tmp_path / "second.json"
    assert main(["train", *arguments, "--output", str(second), "--evaluate"]) == 0
    assert json.loads(capsys.readouterr().out)["artifact_fingerprint"] == summary["artifact_fingerprint"]
    report_file = tmp_path / "report.json"
    assert main(["evaluate", *arguments, "--output", str(report_file)]) == 0
    report = EvaluationReport.model_validate_json(report_file.read_bytes())
    assert report == artifact.evaluation


def test_train_without_evaluation_uses_all_matches_without_claiming_validation(tmp_path, capsys):
    output = tmp_path / "artifact.json"
    assert main(["train", *inputs(tmp_path), "--output", str(output)]) == 0
    artifact = load_artifact_json(output.read_bytes(), authorized_scope=snapshot().scope)
    assert artifact.training_match_ids == ["m0", "m1", "m2", "m3"]
    assert artifact.evaluation is None
    assert json.loads(capsys.readouterr().out)["validation_status"] == "unvalidated_research"


def test_existing_path_input_and_symlink_never_overwritten(tmp_path, capsys):
    arguments = inputs(tmp_path)
    original = Path(arguments[1]).read_bytes()
    assert main(["train", *arguments, "--output", arguments[1]]) == 2
    assert Path(arguments[1]).read_bytes() == original
    target = tmp_path / "target.json"
    target.write_text("do not replace", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    assert main(["train", *arguments, "--output", str(link)]) == 2
    assert target.read_text() == "do not replace"
    assert json.loads(capsys.readouterr().err.splitlines()[-1])["status"] == "refused"


def test_invalid_input_errors_do_not_echo_player_rows(tmp_path, capsys):
    arguments = inputs(tmp_path)
    source = Path(arguments[1])
    data = json.loads(source.read_text())
    data["events"][0]["start_x"] = "sensitive-freeform-text-not-a-number"
    source.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "should-not-exist.json"
    assert main(["train", *arguments, "--output", str(output)]) == 2
    error = capsys.readouterr().err
    assert "sensitive-freeform" not in error
    assert "events.0.start_x" in error
    assert not output.exists()


@pytest.mark.parametrize("limit,kind", [(SNAPSHOT_BYTES, "snapshot"), (POLICY_BYTES, "policy")])
def test_file_size_is_capped_before_json_parsing(tmp_path, capsys, limit, kind):
    arguments = inputs(tmp_path)
    target = Path(arguments[1 if kind == "snapshot" else 3])
    with target.open("wb") as handle:
        handle.truncate(limit + 1)  # sparse file, not a large fixture allocation
    assert main(["train", *arguments, "--output", str(tmp_path / "unused.json")]) == 2
    assert "JSON limit" in capsys.readouterr().err


def test_nonregular_input_rejected_without_blocking(tmp_path):
    fifo = tmp_path / "not-a-json-file"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="ordinary local file"):
        _read_bounded(fifo, SNAPSHOT_BYTES)


@pytest.mark.parametrize("fraction", ["nan", "inf", "0", "1", "-1"])
def test_invalid_holdout_fraction_creates_no_artifact(tmp_path, capsys, fraction):
    output = tmp_path / "unused.json"
    assert main(["train", *inputs(tmp_path), "--output", str(output), "--test-fraction", fraction]) == 2
    assert not output.exists()
    assert "strictly inside" in capsys.readouterr().err


def test_unavailable_evaluation_is_a_report_not_numeric_success(tmp_path, capsys):
    output = tmp_path / "diagnostic.json"
    assert main(["evaluate", *inputs(tmp_path, matches=2), "--output", str(output)]) == 0
    report = EvaluationReport.model_validate_json(output.read_bytes())
    assert report.status == "unavailable" and report.model_brier is None
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"


def test_module_entrypoint_runs_in_existing_python_environment(tmp_path):
    output = tmp_path / "artifact.json"
    result = subprocess.run([sys.executable, "-m", "app.intelligence.action_value_cli", "train", *inputs(tmp_path), "--output", str(output), "--evaluate"], cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "fitted_not_activated"
    assert load_artifact_json(output.read_bytes(), authorized_scope=snapshot().scope).evaluation is not None
