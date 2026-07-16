from __future__ import annotations

import hashlib
import importlib.metadata
import sys
from pathlib import Path

DISTRIBUTIONS = (
    "altgraph",
    "bottle",
    "cffi",
    "clr-loader",
    "packaging",
    "pefile",
    "proxy-tools",
    "pycparser",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "pythonnet",
    "pywebview",
    "pywin32-ctypes",
    "setuptools",
    "typing-extensions",
    "websocket-client",
)
LICENSE_NAMES = ("license", "copying", "notice", "authors")
VENDORED_LICENSES = {
    ("proxy-tools", "0.1.0"): (
        Path(__file__).resolve().parent / "third_party" / "proxy-tools" / "LICENSE.txt",
        "a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4",
    ),
}


def license_files(distribution: importlib.metadata.Distribution) -> list[Path]:
    found: list[Path] = []
    for item in distribution.files or ():
        name = Path(str(item)).name.casefold()
        if any(name == prefix or name.startswith(prefix + ".") for prefix in LICENSE_NAMES):
            path = Path(distribution.locate_file(item))
            if path.is_file() and path not in found:
                found.append(path)
    return found


def render() -> str:
    sections = [
        "MOKU THIRD-PARTY LICENSES",
        "Generated from the exact Python environment used for this build.",
    ]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError(f"Python license not found: {python_license}")
    sections.extend([
        "=" * 78,
        f"CPython {sys.version.split()[0]}",
        "=" * 78,
        python_license.read_text(encoding="utf-8", errors="replace").strip(),
    ])
    for requested in DISTRIBUTIONS:
        distribution = importlib.metadata.distribution(requested)
        paths = license_files(distribution)
        if not paths:
            key = (requested.casefold(), distribution.version)
            fallback = VENDORED_LICENSES.get(key)
            if fallback is None:
                raise FileNotFoundError(
                    f"No license file found in {distribution.metadata['Name']} {distribution.version}"
                )
            path, expected_hash = fallback
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise RuntimeError(f"Vendored license hash mismatch: {path}")
            paths = [path]
        sections.extend([
            "=" * 78,
            f"{distribution.metadata['Name']} {distribution.version}",
            "=" * 78,
        ])
        for path in paths:
            sections.append(f"--- {path.name} ---")
            sections.append(path.read_text(encoding="utf-8", errors="replace").strip())
    return "\n\n".join(sections).rstrip() + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate-third-party-licenses.py OUTPUT")
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(), encoding="utf-8", newline="\n")
    print(output)


if __name__ == "__main__":
    main()
