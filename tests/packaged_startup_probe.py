"""Native startup regression, optionally retaining simulated download markers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from packaged_native_click_login_probe import child_windows, top_windows, user32, WM_CLOSE


def copy_package(exe: Path, destination: Path) -> Path:
    source = exe.parent
    manifest = json.loads((source / "BUILD_MANIFEST.json").read_text(encoding="utf-8-sig"))
    for relative in manifest["distributionFiles"]:
        input_path = (source / relative).resolve(strict=True)
        output_path = (destination / relative).resolve()
        if not input_path.is_relative_to(source) or not output_path.is_relative_to(destination):
            raise ValueError("Distribution file escaped the package")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(input_path, output_path)
    shutil.copyfile(source / "BUILD_MANIFEST.json", destination / "BUILD_MANIFEST.json")
    return destination / exe.name


def download_markers(root: Path, *, apply: bool = False) -> int:
    env = os.environ.copy()
    env["MOKU_MOTW_PROBE_ROOT"] = str(root)
    script = """
        $ErrorActionPreference = 'Stop'
        $taskFiles = @(Get-ChildItem -LiteralPath $env:MOKU_MOTW_PROBE_ROOT -File -Recurse)
    """
    if apply:
        script += """
            foreach ($taskFile in $taskFiles) {
                Set-Content -LiteralPath $taskFile.FullName -Stream Zone.Identifier -Encoding Ascii -Value "[ZoneTransfer]`r`nZoneId=3"
            }
        """
    script += """
        $taskMarked = @($taskFiles | Where-Object {
            Get-Item -LiteralPath $_.FullName -Stream Zone.Identifier -ErrorAction SilentlyContinue
        })
        Write-Output $taskMarked.Count
    """
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, check=True, capture_output=True, text=True,
    )
    return int(completed.stdout.strip())


def probe_startup(exe: Path, root: Path) -> dict:
    local_app_data = root / "localappdata"
    local_app_data.mkdir()
    env = os.environ.copy()
    env.update({
        "LOCALAPPDATA": str(local_app_data),
        "MOKU_RUNTIME_DIR": str(root / "runtime"),
        "MOKU_MUTEX_NAME": "Local\\MOKU.StartupProbe." + os.urandom(12).hex(),
        "MOKU_DISABLE_PERSISTENT_SESSION": "1",
    })
    for name in ("MOKU_NO_BROWSER", "MOKU_TEST_EXIT_AFTER_SECONDS", "MOKU_ENABLE_TEST_FIXTURES", "MOKU_CODE_GENERATION"):
        env.pop(name, None)
    result = {"ok": False, "verdict": "TIMEOUT", "exactDllError": False, "message": ""}
    process = subprocess.Popen([str(exe)], cwd=exe.parent, env=env)
    started = time.monotonic()
    main_window = None
    try:
        while time.monotonic() - started < 25:
            for window in top_windows(process.pid):
                children = child_windows(window["hwnd"])
                if window["class"] == "#32770":
                    message = "\n".join(row["title"] for row in children if row["title"])
                    result.update({
                        "verdict": "FAIL",
                        "exactDllError": "Failed to resolve Python.Runtime.Loader.Initialize" in message,
                        "message": message,
                    })
                    break
                if window["title"].startswith("MOKU") and any(row["class"] == "Chrome_RenderWidgetHostHWND" for row in children):
                    main_window = window["hwnd"]
                    result.update({"ok": True, "verdict": "PASS"})
                    break
            if result["verdict"] != "TIMEOUT":
                break
            if process.poll() is not None:
                result.update({"verdict": "FAIL", "message": f"Early process exit: {process.returncode}"})
                break
            time.sleep(0.2)
        result["elapsedSeconds"] = round(time.monotonic() - started, 2)
    finally:
        if process.poll() is None and main_window:
            user32.PostMessageW(main_window, WM_CLOSE, 0, 0)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--mark-of-web", action="store_true")
    args = parser.parse_args()
    exe = args.exe.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="moku-startup-probe-") as temporary:
        root = Path(temporary).resolve()
        if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("moku-startup-probe-"):
            raise ValueError("Unexpected probe directory")
        marked = 0
        if args.mark_of_web:
            package = root / "package"
            exe = copy_package(exe, package)
            marked = download_markers(package, apply=True)
            if not marked:
                raise RuntimeError("Download marker fixture is empty")
        result = probe_startup(exe, root)
        if args.mark_of_web:
            remaining = download_markers(package)
            result.update({"markedBefore": marked, "markedAfter": remaining})
            if remaining != marked:
                result.update({"ok": False, "verdict": "FAIL", "message": "Program removed download markers"})
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
