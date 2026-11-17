"""Bind formal builds to successful, source-matched engineering evidence.

The record is a local quality gate, separate from release signatures. Evidence
stays beside the record outside the distribution; development builds need none.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time

from build_manifest import file_sha256, source_generation
from version import __version__


REQUIRED_CHECKS = frozenset({"static-contracts", "core-regressions", "browser-workflows"})
MAX_RECORD_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
CLOCK_SKEW_SECONDS = 300


class VerificationError(ValueError):
    pass


def _read_json(path: Path, maximum: int) -> dict:
    with path.open("rb") as stream:
        encoded = stream.read(maximum + 1)
    if len(encoded) > maximum:
        raise VerificationError(f"Verification input is too large: {path.name}")
    try:
        value = json.loads(encoded)
    except (UnicodeError, ValueError) as error:
        raise VerificationError(f"Verification input is not valid JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise VerificationError(f"Verification input must be an object: {path.name}")
    return value


def verify_record(
    path: Path, *, source_root: Path | None = None,
    manifest: Path | None = None, now: float | None = None,
) -> str:
    """Return the verified source generation, or reject incomplete/stale evidence.

    Source and evidence changes invalidate a record. Time alone does not expire
    an unchanged revision. Native build scripts separately verify the full
    distribution manifest and its release signature before publication.
    """
    path = Path(path)
    record = _read_json(path, MAX_RECORD_BYTES)
    if set(record) != {"schemaVersion", "version", "sourceGeneration", "startedAt", "completedAt", "checks"}:
        raise VerificationError("Verification record fields do not match schema 1")
    if type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1:
        raise VerificationError("Unsupported verification record schema")
    if record["version"] != __version__:
        raise VerificationError("Verification version differs from current source; run verification again")
    generation = record["sourceGeneration"]
    if not isinstance(generation, str) or not re.fullmatch(r"source-sha256:[0-9a-f]{64}", generation):
        raise VerificationError("Invalid verification source generation")
    started, completed = record["startedAt"], record["completedAt"]
    if type(started) is not int or type(completed) is not int or not 0 < started <= completed:
        raise VerificationError("Invalid verification time range")
    if completed > (time.time() if now is None else now) + CLOCK_SKEW_SECONDS:
        raise VerificationError("Verification completion time is in the future")
    groups = record["checks"]
    if not isinstance(groups, list) or len(groups) != len(REQUIRED_CHECKS):
        raise VerificationError("Verification requires all three engineering check groups")
    remaining = set(REQUIRED_CHECKS)
    evidence = []
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"name", "passed", "evidence"}:
            raise VerificationError("Invalid verification check group")
        name = group["name"]
        if not isinstance(name, str) or name not in remaining or group["passed"] is not True:
            raise VerificationError("A required engineering check is missing, repeated or failed")
        remaining.remove(name)
        rows = group["evidence"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
            raise VerificationError(f"Check group has no bounded evidence list: {name}")
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
                raise VerificationError("Invalid verification evidence entry")
            relative, digest = row["path"], row["sha256"]
            if (not isinstance(relative, str) or not relative or "\0" in relative
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
                raise VerificationError("Invalid verification evidence path or digest")
            candidate = Path(relative)
            if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
                raise VerificationError("Verification evidence must stay beside its record")
            resolved = (path.parent / candidate).resolve()
            if not resolved.is_relative_to(path.parent.resolve()) or not resolved.is_file():
                raise VerificationError(f"Verification evidence is missing or outside its record directory: {relative}")
            evidence.append((resolved, digest.upper()))
    current = source_generation(source_root=source_root)
    if generation != current:
        raise VerificationError("Source inputs changed after verification; run verification again")
    for candidate, digest in evidence:
        if file_sha256(candidate) != digest:
            raise VerificationError(f"Verification evidence changed: {candidate.name}")
    if manifest is not None:
        package = _read_json(Path(manifest), MAX_MANIFEST_BYTES)
        if package.get("sourceGeneration") != generation or package.get("version") != record["version"]:
            raise VerificationError("Package was built from a different verified revision; rebuild first")
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify",))
    parser.add_argument("record", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        print(verify_record(args.record, manifest=args.manifest))
    except (OSError, VerificationError) as error:
        parser.exit(1, f"Release verification failed: {error}\n")


if __name__ == "__main__":
    main()
