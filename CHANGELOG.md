# Changelog

All notable changes are documented here. The project follows Semantic Versioning.

## [1.0.6] - 2026-07-20

### Fixed

- Canonicalize user-selected download roots and final publication paths before containment checks, so equivalent Windows path aliases cannot produce false 502 responses or bypass the save-root boundary.
- Isolate threaded download-integrity test network seams and stabilize the publish-identity test path on hosted Windows runners.

## [1.0.5] - 2026-07-18

### Added

- Strict multi-tag AND search using `;` or `；` separators; spaces remain part of one tag.
- Optional bounded anime-oriented tag aliases, disabled by default.
- A collection basket for up to 100 artworks and 1,000 selected images, with windowed page selection for large works.
- One-click select/clear controls for all artworks and images on the current result page.
- Image-first adaptive download chunks and optional artwork grouping.

### Changed

- Save one search batch into a shared tag, author, or artwork context folder instead of creating one folder per artwork.
- Apply the same context-folder rule to single-artwork downloads.
- Restyle the interface with a restrained black-and-white lunar theme, a highlighted moon edge, and one clean orbital ring while preserving the existing workflow and startup budget.
- Keep result pagination docked to the viewport bottom while the gallery scrolls.
- Defer the collection retention decision until forward navigation would actually evict selected result pages.

### Fixed

- Keep exact and alias-expanded search sessions in separate cache namespaces.
- Validate every multi-tag result against all requested tag groups after Pixiv response normalization.

## [1.0.4] - 2026-07-17

### Fixed

- Use Pixiv's current JSON `/ajax/search/users?nick=...` response for exact `author:` resolution, including both list and keyed user payloads.
- Do not expose the loopback request capability to headerless health probes; same-origin desktop/browser readiness checks now identify themselves explicitly.
- Return download paths relative to the selected save directory instead of leaking local absolute paths through the HTTP API.
- Reject malformed or negative remote `Content-Length` values before reading a Pixiv response.

## [1.0.3] - 2026-07-17

### Fixed

- Resolve exact `author:` queries through Pixiv's current `/search/users` page instead of the removed AJAX user-search route.
- Parse the bounded `__NEXT_DATA__` user result set and keep exact creator-name and user-ID filtering.

## [1.0.2] - 2026-07-17

### Added

- Exact Pixiv creator search with `pid:` / `pid：` and `author:` / `author：` queries.

### Fixed

- Parse Pixiv's nested `userPreviews[].user` response before exact author-name matching.
- Reject works whose `userId` does not match the resolved creator.
- Replace overlapping absolute-positioned deck cards with a non-overlapping flex row.
- Reduce pointer preview travel and keep every other card stationary while one card is locked.

## [1.0.1] - 2026-07-16

### Fixed

- Restored Windows PowerShell module discovery in `-NoProfile` CI subprocesses while preserving fail-closed Python Authenticode verification.
- Preserved the vendored `proxy-tools` license byte hash across Windows and CI checkouts with explicit LF normalization.
- Updated pinned GitHub Actions to Node.js 24-compatible major versions.

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
