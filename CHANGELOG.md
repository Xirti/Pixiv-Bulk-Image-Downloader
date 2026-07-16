# Changelog

All notable changes are documented here. The project follows Semantic Versioning.

## [1.0.0] - 2026-07-16

### Added

- Windows pywebview/WebView2 desktop host with a second official Pixiv login window.
- Public, R-18, and combined search scopes with multi-tag OR aggregation.
- Bounded historical search, paging prefetch, sliding cache eviction, and selective batch download.
- Native folder selection, offline usage guide, and anonymous parallel network diagnosis.
- Portable PyInstaller build, release ZIP generation, SHA-256 manifests, and Windows CI.
- A fail-closed build manifest binding source/build inputs to every portable-package file.

### Security

- Loopback host, `Sec-Fetch-Site`, and same-origin checks for every API GET.
- Per-process request tokens for every non-health API request; image URLs use separate high-entropy capabilities.
- Strict Content Security Policy and same-origin resource headers on local HTTP responses.
- Bounded JSON-object parsing for mutating requests.
- Explicit Pixiv/API/image host allowlists and loopback-only proxy selection.
- Query parameters, cookies, request bodies, and image tokens excluded from HTTP logs.
- Content-derived backend generation IDs prevent a new client from reusing stale code.
- Test-only synthetic gallery routes disabled by default.
- Release generation rejects stale source, changed licenses, modified support files, linked or undeclared files/directories, non-Windows-x64 product artifacts, missing pywebview loader runtimes, unlocked top-level package metadata, and archives that fail round-trip verification.
- Runtime and build dependencies are locked to verified artifact SHA-256 values; the legacy `proxy-tools` source is reproduced as an audited deterministic local wheel.
- Build and release validation use a signed CPython 3.12 executable, a shared exclusive mutex, source rechecks, and schema 3 full file/directory manifests.

### Changed

- Replaced the legacy external Edge `--app` host with pywebview/WebView2.
- Replaced sequential port probing with Windows-assigned ephemeral loopback ports.
- Extracted synthetic test fixtures from the production HTTP module.
- Added bounded LRU artwork caching and safe refresh of expired image authorization.
- Synchronized artwork/image-capability state and revalidate in-flight R-18 image/download authorization after network reads.
- Complete staging cleanup before returning download success or failure responses, so the HTTP result matches the final filesystem state.
- Replaced unbounded logs with 5 MiB rotation and removed temporary WebView2 paths from cleanup warnings.
- Removed Android, non-Windows UI backends, unnecessary x86/ARM64 product components, and debug symbols from the Windows x64 frozen closure while retaining the small pywebview loader runtimes required during import.
