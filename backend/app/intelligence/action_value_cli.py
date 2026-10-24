"""Offline operator CLI: python -m app.intelligence.action_value_cli --help.

No network, database, model download, pickle or automatic model activation.
All outputs are private, exclusive-created local JSON files (mode 0600).
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ValidationError

from .action_value import (
    ActionValueUnavailable, artifact_document, artifact_fingerprint, evaluate,
    fit, subset_snapshot,
)
from .contracts import ActionValuePolicy, EvidenceSnapshot, chronological_match_split

SNAPSHOT_BYTES = 32 * 1024 * 1024
POLICY_BYTES = 64 * 1024


def _read_bounded(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ActionValueUnavailable("Input must be an ordinary local file, not a pipe/device.")
        if info.st_size > limit:
            raise ActionValueUnavailable(f"Input exceeds the {limit}-byte JSON limit.")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ActionValueUnavailable(f"Input exceeds the {limit}-byte JSON limit.")
    return data


def _write_private(path: Path, model: BaseModel) -> None:
    data = model.model_dump_json(indent=2) + "\n"
    # Exclusive creation prevents overwrite races, following existing symlinks,
    # accidental replacement of the training input and silent artifact mutation.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Offline provisional spatial Markov trainer. Only authorized complete reviewed ordinary-action streams; no shipped youth model.")
    commands = result.add_subparsers(dest="command", required=True)
    for command in ("train", "evaluate"):
        child = commands.add_parser(command)
        child.add_argument("--input", type=Path, required=True, help="Private EvidenceSnapshot JSON, maximum 32 MiB; no external upload.")
        child.add_argument("--policy", type=Path, help="Explicit ActionValuePolicy JSON, maximum 64 KiB; defaults are provisional engineering gates.")
        child.add_argument("--output", type=Path, required=True, help="NEW private JSON path; existing files are never overwritten.")
        child.add_argument("--test-fraction", type=float, default=0.25, help="Whole-date chronological holdout fraction; never a random event split.")
        if command == "train":
            child.add_argument("--evaluate", action="store_true", help="Reserve later whole matches; artifact trains ONLY on the training partition and includes the diagnostic report.")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        snapshot = EvidenceSnapshot.model_validate_json(_read_bounded(arguments.input, SNAPSHOT_BYTES))
        policy = ActionValuePolicy.model_validate_json(_read_bounded(arguments.policy, POLICY_BYTES)) if arguments.policy else ActionValuePolicy()
        if not 0 < arguments.test_fraction < 1:
            raise ActionValueUnavailable("test-fraction must be finite and strictly inside (0,1).")
        if arguments.command == "evaluate":
            report = evaluate(snapshot, policy=policy, test_fraction=arguments.test_fraction)
            _write_private(arguments.output, report)
            print(json.dumps({"status": report.status, "mode": report.mode, "validation_status": "unvalidated_research", "sample_count": report.sample_count, "excluded_count": report.excluded_count}))
        else:
            training = snapshot
            if arguments.evaluate:
                train_ids, _ = chronological_match_split(snapshot.matches, test_fraction=arguments.test_fraction)
                training = subset_snapshot(snapshot, set(train_ids))
            artifact = fit(training, policy=policy)
            if arguments.evaluate:
                report = evaluate(snapshot, policy=policy, test_fraction=arguments.test_fraction)
                artifact = type(artifact).model_validate(artifact.model_dump() | {"evaluation": report})
            _write_private(arguments.output, artifact_document(artifact))
            print(json.dumps({"status": "fitted_not_activated", "mode": artifact.scope.mode, "validation_status": artifact.validation_status, "artifact_fingerprint": artifact_fingerprint(artifact), "training_matches": len(artifact.training_match_ids), "training_actions": artifact.training_action_count}))
        return 0
    except ValidationError as error:
        # Avoid echoing rows containing minors' identifiers to process logs.
        locations = [".".join(map(str, item["loc"])) for item in error.errors(include_input=False)[:10]]
        print(json.dumps({"status": "refused", "reason": "Invalid JSON contract", "fields": locations}), file=sys.stderr)
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "refused", "reason": str(error)}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
