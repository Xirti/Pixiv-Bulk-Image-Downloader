# Third-Party Notices

MOKU is built with third-party open-source software. The release build generates `THIRD_PARTY_LICENSES.txt` from the exact installed distributions and places it beside `MOKU.exe`.

Primary runtime components:

| Component | Version | License | Project |
| --- | ---: | --- | --- |
| altgraph | 0.17.5 | MIT | https://altgraph.readthedocs.io |
| bottle | 0.13.4 | MIT | http://bottlepy.org/ |
| cffi | 2.1.0 | MIT-0 | https://github.com/python-cffi/cffi |
| clr-loader | 0.3.1 | MIT | https://github.com/pythonnet/clr-loader |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| pefile | 2024.8.26 | MIT | https://github.com/erocarrera/pefile |
| proxy-tools | 0.1.0 | Upstream BSD terms; PyPI metadata says MIT | https://github.com/jtushman/proxy_tools |
| pycparser | 3.0 | BSD-3-Clause | https://github.com/eliben/pycparser |
| PyInstaller | 6.21.0 | GPL-2.0-or-later with bootloader exception | https://github.com/pyinstaller/pyinstaller |
| pyinstaller-hooks-contrib | 2026.6 | Apache-2.0 OR GPL-2.0-or-later | https://github.com/pyinstaller/pyinstaller-hooks-contrib |
| pythonnet | 3.1.0 | MIT | https://github.com/pythonnet/pythonnet |
| pywebview | 6.2.1 | BSD-3-Clause | https://github.com/r0x0r/pywebview |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | https://github.com/enthought/pywin32-ctypes |
| setuptools | 83.0.0 | MIT | https://github.com/pypa/setuptools |
| typing-extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| websocket-client | 1.9.0 | Apache-2.0 | https://github.com/websocket-client/websocket-client |
| CPython | 3.12.10 | PSF License | https://www.python.org/ |

The generated license bundle intentionally covers every distribution in the exact runtime and build lock files. This conservative closure includes build helpers that PyInstaller may analyze or embed, even when they are not direct application imports.

`proxy-tools==0.1.0` omits its license file from both the wheel and PyPI source archive. Its PyPI metadata says MIT, while code-identical upstream commit `db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6` contains BSD terms. MOKU includes that exact upstream license with a fixed hash and records the provenance in `third_party/proxy-tools/PROVENANCE.md`.

Pixiv and artwork displayed or downloaded through MOKU are not third-party software dependencies. Artwork remains the property of its respective creators.
