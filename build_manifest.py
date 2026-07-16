from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import server
from version import __version__

SCHEMA_VERSION = 3
MANIFEST_NAME = "BUILD_MANIFEST.json"
FORBIDDEN_WINDOWS_X64_DISTRIBUTION_MARKERS = (
    "pywebview-android.jar",
    "platforms/android/",
    "platforms/cef.py",
    "platforms/cocoa.py",
    "platforms/gtk.py",
    "platforms/mshtml.py",
    "platforms/qt.py",
    "webbrowserinterop.x86.dll",
    "ffi/dlls/x86/",
    ".pdb",
)
PYWEBVIEW_REQUIRED_LOADER_MARKERS = (
    "runtimes/win-arm64/native/webview2loader.dll",
    "runtimes/win-x64/native/webview2loader.dll",
    "runtimes/win-x86/native/webview2loader.dll",
)
ALLOWED_TOP_LEVEL_DISTRIBUTION_METADATA = (
    "clr_loader-0.3.1.dist-info",
    "pythonnet-3.1.0.dist-info",
    "pywebview-6.2.1.dist-info",
)
BUILD_INPUT_FILES = (
    *server.CODE_GENERATION_FILES,
    "MOKU.spec",
    "requirements.lock",
    "requirements-dev.lock",
    "build-portable.ps1",
    "make-release.ps1",
    "build_manifest.py",
    "run_tests.py",
    "generate-third-party-licenses.py",
    "CHANGELOG.md",
    ".github/workflows/ci.yml",
    "PRIVACY.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "third_party/proxy-tools/LICENSE.txt",
    "third_party/proxy-tools/PROVENANCE.md",
    "third_party/wheels/proxy_tools-0.1.0-py3-none-any.whl",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_input_files(source_root: Path) -> tuple[str, ...]:
    root = Path(source_root)
    files = list(BUILD_INPUT_FILES)
    tests_root = root / "tests"
    if tests_root.is_dir():
        files.extend(
            path.relative_to(root).as_posix()
            for path in tests_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".js", ".ps1"}
        )
    if (root / "LICENSE").is_file():
        files.append("LICENSE")
    return tuple(sorted(set(files), key=lambda value: (value.casefold(), value)))


def source_generation(*, source_root: Path | None = None) -> str:
    root = Path(source_root or Path(__file__).resolve().parent)
    return server.compute_code_generation(
        root=root,
        files=build_input_files(root),
        frozen=False,
    )


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)()
    )


def distribution_snapshot(distribution_root: Path) -> tuple[dict[str, str], list[str]]:
    root = Path(distribution_root)
    if not root.is_dir() or _is_link_or_junction(root):
        raise RuntimeError("Distribution root is missing or is a link")

    rows: dict[str, str] = {}
    directory_rows: list[str] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            path = current_path / directory
            if _is_link_or_junction(path):
                raise RuntimeError(
                    f"Distribution contains a linked directory: {path.name}"
                )
            directory_rows.append(path.relative_to(root).as_posix())
        for filename in filenames:
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            if relative == MANIFEST_NAME:
                continue
            folded = relative.casefold()
            if any(marker in folded for marker in FORBIDDEN_WINDOWS_X64_DISTRIBUTION_MARKERS):
                raise RuntimeError(
                    f"Distribution contains a non-Windows-x64 artifact: {relative}"
                )
            if _is_link_or_junction(path) or not path.is_file():
                raise RuntimeError(
                    f"Distribution contains a non-regular file: {relative}"
                )
            rows[relative] = file_sha256(path)

    files = dict(sorted(rows.items(), key=lambda row: (row[0].casefold(), row[0])))
    folded_files = {relative.casefold() for relative in files}
    top_level_metadata = sorted(
        {
            parts[1]
            for relative in folded_files
            for parts in (relative.split("/", 2),)
            if len(parts) >= 2
            and parts[0] == "_internal"
            and parts[1].endswith(".dist-info")
        }
    )
    unexpected_metadata = sorted(
        set(top_level_metadata) - set(ALLOWED_TOP_LEVEL_DISTRIBUTION_METADATA)
    )
    if unexpected_metadata:
        raise RuntimeError(
            "Distribution contains unlocked top-level package metadata: "
            + ", ".join(unexpected_metadata)
        )
    has_pywebview_runtime = any("webview/lib/" in relative for relative in folded_files)
    missing_loaders = [
        marker
        for marker in PYWEBVIEW_REQUIRED_LOADER_MARKERS
        if has_pywebview_runtime
        and not any(relative.endswith(marker) for relative in folded_files)
    ]
    if missing_loaders:
        raise RuntimeError(
            "Distribution is missing pywebview WebView2 loader runtime: "
            + ", ".join(missing_loaders)
        )
    directories = sorted(set(directory_rows), key=lambda value: (value.casefold(), value))
    return files, directories


def distribution_files(distribution_root: Path) -> dict[str, str]:
    return distribution_snapshot(distribution_root)[0]


def expected_manifest(
    executable: Path,
    *,
    source_root: Path | None = None,
    distribution_root: Path | None = None,
) -> dict:
    executable = Path(executable)
    distribution = Path(distribution_root or executable.parent)
    files, directories = distribution_snapshot(distribution)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "version": __version__,
        "sourceGeneration": source_generation(source_root=source_root),
        "exeSha256": file_sha256(executable),
        "distributionFiles": files,
        "distributionDirectories": directories,
    }


def write_manifest(
    manifest: Path,
    executable: Path,
    *,
    source_root: Path | None = None,
    expected_source_generation: str | None = None,
) -> None:
    manifest = Path(manifest)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_suffix(manifest.suffix + ".tmp")
    temporary.unlink(missing_ok=True)

    payload = expected_manifest(executable, source_root=source_root)
    if (
        expected_source_generation is not None
        and payload["sourceGeneration"] != expected_source_generation
    ):
        raise RuntimeError("Build inputs changed during build; rebuild first")

    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, manifest)


def verify_manifest(
    manifest: Path,
    executable: Path,
    *,
    source_root: Path | None = None,
) -> None:
    try:
        actual = json.loads(Path(manifest).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("BUILD_MANIFEST.json is missing or invalid") from exc

    expected = expected_manifest(executable, source_root=source_root)
    if actual != expected:
        raise RuntimeError(
            "Build manifest does not match the current source and executable; rebuild first"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("source", "write", "verify"))
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("executable", type=Path, nargs="?")
    parser.add_argument("--expected-source-generation")
    args = parser.parse_args()

    if args.action == "source":
        print(source_generation())
        return
    if args.manifest is None or args.executable is None:
        parser.error("manifest and executable are required for write and verify")
    if args.action == "write":
        write_manifest(
            args.manifest,
            args.executable,
            expected_source_generation=args.expected_source_generation,
        )
        print(args.manifest)
    else:
        verify_manifest(args.manifest, args.executable)
        print("build-manifest=verified")


if __name__ == "__main__":
    main()
