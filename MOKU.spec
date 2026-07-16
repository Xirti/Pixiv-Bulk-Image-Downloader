# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


WINDOWS_X64_EXCLUDED_PATHS = (
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
WINDOWS_X64_EXCLUDED_MODULES = (
    "webview.platforms.android",
    "webview.platforms.cef",
    "webview.platforms.cocoa",
    "webview.platforms.gtk",
    "webview.platforms.mshtml",
    "webview.platforms.qt",
)
WINDOWS_X64_WEBVIEW_BACKENDS = (
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.platforms.win32",
)
# pywebview 6.2.1 resolves every WebView2Loader runtime directory while
# importing edgechromium, even on x64. Keep these small loader directories;
# the x64 process only loads the matching win-x64 binary.
PYWEBVIEW_REQUIRED_LOADER_RUNTIMES = (
    "runtimes/win-arm64/",
    "runtimes/win-x64/",
    "runtimes/win-x86/",
)


def filter_windows_x64(rows):
    filtered = []
    for row in rows:
        values = row if isinstance(row, (tuple, list)) else (row,)
        normalized = " ".join(str(value) for value in values).replace("\\", "/").lower()
        module_name = str(row).lower() if isinstance(row, str) else ""
        if any(marker in normalized for marker in WINDOWS_X64_EXCLUDED_PATHS):
            continue
        if any(
            module_name == prefix or module_name.startswith(prefix + ".")
            for prefix in WINDOWS_X64_EXCLUDED_MODULES
        ):
            continue
        filtered.append(row)
    return filtered


pythonnet_datas, pythonnet_binaries, pythonnet_hiddenimports = collect_all('pythonnet')
clr_datas, clr_binaries, clr_hiddenimports = collect_all('clr_loader')
webview_datas, webview_binaries, webview_hiddenimports = collect_all('webview')

pythonnet_datas = filter_windows_x64(pythonnet_datas)
pythonnet_binaries = filter_windows_x64(pythonnet_binaries)
clr_datas = filter_windows_x64(clr_datas)
clr_binaries = filter_windows_x64(clr_binaries)
webview_datas = filter_windows_x64(webview_datas)
webview_binaries = filter_windows_x64(webview_binaries)
webview_hiddenimports = filter_windows_x64(webview_hiddenimports)
webview_hiddenimports = sorted(set(webview_hiddenimports + list(WINDOWS_X64_WEBVIEW_BACKENDS)))

a = Analysis(
    ['moku_app.py'],
    pathex=[],
    binaries=pythonnet_binaries + clr_binaries + webview_binaries,
    datas=[('web', 'web')] + pythonnet_datas + clr_datas + webview_datas,
    hiddenimports=pythonnet_hiddenimports + clr_hiddenimports + webview_hiddenimports + ['clr'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# PyInstaller hooks may append platform assets after the explicit inputs above.
# Filter the completed Analysis tables as a second layer before PYZ/COLLECT.
a.binaries = filter_windows_x64(a.binaries)
a.datas = filter_windows_x64(a.datas)
a.pure = filter_windows_x64(a.pure)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MOKU',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MOKU',
)
