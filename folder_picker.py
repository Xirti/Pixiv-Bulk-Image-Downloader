from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path


_HELPER_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $raw = [Console]::In.ReadToEnd()
    if ($raw.Length -gt 8192) { throw 'request too large' }
    $request = $raw | ConvertFrom-Json
    $initial = [string]$request.initial

    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()

    $owner = New-Object Windows.Forms.Form
    $owner.ShowInTaskbar = $false
    $owner.StartPosition = [Windows.Forms.FormStartPosition]::CenterScreen
    $owner.Size = New-Object Drawing.Size(1, 1)
    $owner.Opacity = 0
    $owner.TopMost = $true

    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = '选择 MOKU 图片保存文件夹'
    $dialog.ShowNewFolderButton = $true
    if ($initial -and [IO.Path]::IsPathRooted($initial) -and [IO.Directory]::Exists($initial)) {
        $dialog.SelectedPath = $initial
    }

    $owner.Show()
    $owner.Activate()
    $answer = $dialog.ShowDialog($owner)
    $selected = if ($answer -eq [Windows.Forms.DialogResult]::OK) { [string]$dialog.SelectedPath } else { '' }
    $result = [ordered]@{ selected = $selected; cancelled = ($answer -ne [Windows.Forms.DialogResult]::OK) }
    $dialog.Dispose()
    $owner.Close()
    $owner.Dispose()
    [Console]::Out.Write(($result | ConvertTo-Json -Compress))
} catch {
    $result = [ordered]@{ selected = ''; cancelled = $true; error = ('目录选择器启动失败：' + $_.Exception.Message) }
    [Console]::Out.Write(($result | ConvertTo-Json -Compress))
    exit 1
}
'''


def _helper_command() -> list[str]:
    encoded = base64.b64encode(_HELPER_SCRIPT.encode("utf-16le")).decode("ascii")
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Sta",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


def _error(message: str) -> dict[str, str | bool]:
    return {"selected": "", "cancelled": True, "error": message}


def select_folder(initial: str = "", timeout: float = 300.0) -> dict[str, str | bool]:
    request = json.dumps({"initial": str(initial or "")}, ensure_ascii=False)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            _helper_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except OSError as exc:
        return _error(f"目录选择器启动失败：{exc}")

    try:
        stdout, stderr = process.communicate(input=request, timeout=max(0.01, float(timeout)))
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return _error("目录选择超时")

    if len(stdout) > 8192:
        return _error("目录选择器返回数据过大")
    try:
        result = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        detail = (stderr or "").strip()[:300]
        return _error("目录选择器返回无效数据" + (f"：{detail}" if detail else ""))
    if not isinstance(result, dict):
        return _error("目录选择器返回无效数据")

    selected = str(result.get("selected") or "")
    cancelled = bool(result.get("cancelled", not selected))
    error = str(result.get("error") or "")
    if selected:
        path = Path(selected)
        if not path.is_absolute():
            return _error("目录选择器返回了非绝对路径")
        return {"selected": str(path), "cancelled": False}
    response: dict[str, str | bool] = {"selected": "", "cancelled": cancelled}
    if error:
        response["error"] = error
    return response


if __name__ == "__main__":
    import sys

    initial = sys.argv[1] if len(sys.argv) > 1 else ""
    print(json.dumps(select_folder(initial), ensure_ascii=False))
