# MOKU — Pixiv Tag Gallery

MOKU is a local-first Windows desktop app for browsing and saving Pixiv artwork by tag. The desktop UI runs in pywebview with the Microsoft Edge WebView2 runtime, while a loopback-only Python service handles Pixiv requests, image proxying, native folder selection, and file writes.

Current source version: **1.0.1**.

This project is independent and is not affiliated with Pixiv.

## Features

- Real Pixiv tag search with bounded historical date windows
- Space-separated multi-tag search with OR semantics (`cat night city`)
- Three content scopes:
  - public all-ages
  - R-18 after local account connection
  - all types, merging all-ages and R-18 results with ID deduplication
- Illustration, manga, and ugoira filters
- Optional exclusion of AI-generated work
- Thirty-six results per page
- Three pages prefetched ahead of the current page
- Sliding result cache: six previous pages are retained; older pages and their temporary search-preview tokens are released
- Result-data prefetch without downloading thumbnails from unopened pages; search previews use `no-store`
- Multi-page artwork preview and selective batch download
- Native Windows folder picker
- An offline in-app Usage Guide with an explicit anonymous Pixiv/CDN network diagnosis
- Direct, local Windows system-proxy, environment-proxy, and TUN-compatible network paths

R-18G is not supported. MOKU does not change Pixiv age settings or bypass account permissions.

The network diagnosis runs only after the user clicks its button. It checks the Pixiv site and image CDN in parallel without sending the Pixiv session. MOKU does not modify Windows proxy settings, start VPN software, or scan local ports. On another PC, it reads that Windows user's currently enabled local HTTP system proxy; TUN is optional, not required.

The embedded backend initializes network selection before serving requests and rechecks the current setting at Pixiv operation boundaries. Only loopback HTTP proxies (`127.0.0.1`, `localhost`, or `::1`) are accepted. Rejected remote `HTTP_PROXY` / `ALL_PROXY` values cannot be silently reintroduced by Python's default proxy handling. When no accepted proxy is selected, the request path is genuinely direct/TUN.

## Desktop login

Account connection is available only in the desktop app:

1. MOKU opens the official Pixiv login page in a second WebView2 window.
2. Passwords, CAPTCHA, and 2FA stay on Pixiv's page.
3. After the window reaches the HTTPS Pixiv home page, MOKU accepts one strictly validated Secure and HttpOnly `PHPSESSID`.
4. The session is stored with Windows Credential Manager when persistence is selected.

MOKU does not log cookie values and does not send the Pixiv session to the image CDN or another domain. It no longer uses external Edge automation, CDP, remote-debugging ports, or `/ajax/user/self` as a blocking login probe.

A local session may appear connected until a real Pixiv request rejects an expired cookie. Reconnect at that point.

## Runtime boundaries

- The HTTP service binds only to `127.0.0.1`.
- Every API request requires a loopback client, loopback `Host`, a non-cross-site fetch context, and an absent or same-origin `Origin`.
- `/api/health` is the only headerless API handshake. Every other API request requires a per-process request token; image URLs use separate high-entropy capabilities.
- Mutating requests additionally require bounded JSON-object bodies.
- Pixiv API traffic is restricted to approved Pixiv HTTPS hosts.
- Image traffic is restricted to `i.pximg.net` and carries no account cookie.
- Download paths must be absolute when supplied by the user.
- R-18 pages, image tokens, and artwork cache entries are cleared on disconnect.

LAN and Internet exposure are intentionally unsupported. Do not change the bind address to `0.0.0.0` without adding authentication, TLS, explicit filesystem scoping, and a new threat model.

## Run from source

Requirements:

- Windows 10 or 11
- Python 3.12
- Microsoft Edge WebView2 Runtime

Install runtime dependencies:

```powershell
python -m pip install -r requirements.lock
```

Run the desktop host:

```powershell
python moku_app.py
```

Or use `MOKU启动.vbs` / `MOKU启动.bat`. The PowerShell launcher can also preload and reuse the loopback backend:

```powershell
powershell -ExecutionPolicy Bypass -File .\launch-moku.ps1 -Mode Desktop
```

`Browser` mode is useful for public browsing and frontend diagnostics, but account login is intentionally disabled outside the desktop host.

## Tests

Install development-only dependencies when running native UI probes:

```powershell
python -m pip install -r requirements-dev.lock
```

Run the unit and integration suite:

```powershell
python -m unittest discover -s tests -v
```

The test suite covers multi-tag paging, result/image-token cache eviction, unopened-page thumbnail behavior, offline guide interaction, anonymous parallel network diagnosis, embedded-backend proxy initialization, WebView2 cookie handling, DNS-rebinding and same-origin defenses, bounded request parsing, download integrity, content-derived backend generations, frozen-resource lookup, and launcher contracts.

## Build

Build the portable onedir package:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-portable.ps1
```

Output:

```text
dist\MOKU\MOKU.exe
dist\MOKU\SHA256.txt
dist\MOKU\BUILD_MANIFEST.json
```

The build script fingerprints its inputs before and after PyInstaller, runs the test suite, and performs frozen-service smoke checks. Schema 3 `BUILD_MANIFEST.json` binds the source/build inputs to every file and directory in the portable folder and rejects linked, undeclared, or non-Windows-x64 entries. It contains hashes and relative filenames only, never local absolute paths or account data.

### Current verified portable artifact

The current `1.0.1` portable build is produced from a clean, hash-locked Python 3.12 environment after all 178 tests pass. Frozen-service, native folder selection, file-write, official login-window, and usage-guide/network probes are also exercised before release. The live Pixiv multi-tag page 1/2/8 probe requires a currently usable Pixiv network route.

The authoritative executable and archive hashes are generated after each verified build in `dist\MOKU\SHA256.txt` and `release\v1.0.1\SHA256SUMS.txt`. Keeping generated hashes out of this source file avoids a self-referential build fingerprint.

The build script verifies that the frozen backend generation is `exe-sha256:<MOKU.exe hash>`, generates third-party license notices, removes smoke-test logs, and writes the authoritative `SHA256.txt`.

`SHA256.txt` contains only the one-way executable fingerprint and the filename `MOKU.exe`; it does not contain account, cookie, path, or identity data.

## Distribution status

The generated `dist\MOKU` folder is suitable for private Windows distribution after testing on a clean machine. Distribute the whole folder, not `MOKU.exe` alone.

The repository includes the MIT License, pinned Windows CI, `SECURITY.md`, `PRIVACY.md`, third-party notices, a release checklist, and a release-asset generator. Before making a public release:

- rebuild after any source, build-input, or license change; the release script refuses a license changed after the portable build;
- enable GitHub private vulnerability reporting;
- run the clean build and packaged desktop probes;
- test the ZIP on a clean Windows 10/11 x64 machine;
- consider Authenticode signing to reduce SmartScreen warnings.

Do not publish logs, downloads, Windows Credential Manager data, runtime descriptors, build caches, or temporary WebView2 profiles.

## License

MOKU is released under the [MIT License](LICENSE).

## Service and copyright notice

Pixiv artwork belongs to its respective creators. Users are responsible for Pixiv's current terms, applicable law, and creator permissions. Do not redistribute downloaded work without authorization.
