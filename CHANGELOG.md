# Changelog

All notable changes are documented here. The project follows Semantic Versioning.

## [1.0.16] - 2026-10-01

### MOKU Lite

- 修复动图信息已返回、后台仍在收尾时，紧接着获取帧包偶发报请求编号冲突的问题；两阶段使用独立编号，取消仍对应当前请求。
- 缓存动图再次悬停立即播放；解码追不上时等待下一帧，不提前循环前半段。
- 合并并发的动图资源请求，短时复用信息及小 ZIP；移开时取消无人使用的后台请求，资源缓存与并发保持有界，账号变更清理缓存。
- 单作品与采集篮新增 GIF／MP4 动图导出，混合下载中的静态图片仍保留源格式；默认保留原始帧 ZIP + JSON。
- GIF 内置、保留透明背景；MP4 使用本机 FFmpeg，不随包附带、不自动安装，缺少时明确提示。转换保留帧时长并限制内存、暂存与处理预算，沿用原有安全文件发布和失败回滚。
- 新增精确锁定的 Pillow 图像依赖，按需加载；打包只主动收集 JPEG／PNG／GIF 插件，避免引入无关编码器。
- 当前版本统一改名为 MOKU Lite，同步桌面标题、文档和 Windows 包名。
- 缓存淘汰不再禁用回退；上一页和第 1 页始终可重新加载，页码条按当前位置收起长列表。
- 所有作品、插画、漫画、动图使用匹配的 Pixiv 搜索接口和数据结构，翻页与预取保留类型条件，避免从混合类型中少量抽样。
- 画师索引先区分漫画与插画／动图，避免把类型筛选的请求预算浪费在无关作品上；具体类型和权限继续校验。
- 动图 ZIP 取回后先显示已解码的第一帧，再继续加载其余帧；兼容不支持 raw DEFLATE 的旧 WebView，不增加解压依赖或额外请求。
- 动图显示加载／失败状态，移开鼠标清理提示；保留具体网络错误和超时提示，取消后的最后一帧不进入缓存，内存限制与原始帧下载不变。
- 首页五个中文字与 MOKU LITE 按窗口中心对齐，问号不占布局宽度；日夜和窄屏布局一起检查。

- 移除首页模型 Logo、粒子及装饰动画，减少启动资源与持续渲染开销。
- 首页改为居中标题、宽搜索框和紧凑筛选选项，保留搜索、选图、下载三页分工及右侧圆点导航。
- 新增全局日间／夜间切换，背景、文字、按钮、输入框、详情、采集篮和弹窗同步换色；当前窗口刷新保留选择。
- 搜索先返回当前页，再后台预取后续三页元数据；切换搜索、翻页或隐藏页面会取消旧预取，迟到响应不能改写当前结果。
- 后台预取不加载未打开页的缩略图，也不撤销当前页的预览授权。
- 暂时只返回半页结果时，先“继续加载本页”补齐再翻页，避免跳过后来补出的作品；严格 AND、单安全范围搜索已有足够合格结果时，不再等待冗余标签来源。
- 动图悬停约 180 毫秒后才开始请求和解码，快速划过即取消，保留正常动图预览与下载格式。
- 将采集篮选择、页码、来源和归档状态收拢到一个模块，统一容量限制、下载锁定和账号失效清理规则。
- 普通作品详情与采集篮详情共用卡片渲染、悬停和固定交互，各入口状态独立。
- 保留来源标记兼容启动配置、1,000 张选择上限、下载分批和失败续传；未改动 TLS 校验、代理选择、登录或文件写入安全边界。
- 发布说明只列本版变化，使用中文。

## [1.0.15] - 2026-09-30

### Fixed

- Load bundled .NET runtimes when downloaded ZIP contents retain Internet zone markers, using an application-local configuration without removing markers or changing Windows protection settings.
- Clear the previous artwork's selection controls while loading a new detail and cancel stale detail requests when entering batch mode.
- Keep basket pagination on freshly renewed preview URLs and prevent late image errors from clearing newer previews.
- Split single-artwork downloads into requests of at most 200 selected pages; resume failed requests without repeating completed chunks.
- Report native folder-picker errors instead of treating them as user cancellation.
- Clear transparent animation frames before playback so previous pixels do not linger.
- Generate release notes for the current version only, without repeating the full changelog.
- Prevent a second desktop window from depending on an embedded backend that exits with the first window; independent preloaded backends remain reusable.
- Use one source fingerprint algorithm in the launcher and backend, including preview scripts and logo assets, even when launched from another directory.
- Recover lost download responses using stable chunk identities: coalesce in-flight retries, replay successful results, and allow failed operations to retry.
- Share search request and time budgets fairly across aliases and safety scopes, rotate overflowing sources, and prioritize the user's original tag spelling.
- Clear restricted views on account changes, stop old tasks before their next chunk, and prevent old failures from restoring stale retry state.
- Stop hover animation work in hidden pages and release decoded preview resources when leaving the page.

### Changed

- Share detail invalidation, artwork-cache updates, and resumable task execution across existing flows.
- Add focused state regressions and an offline Edge browser probe for download retry, basket pagination, settings preservation, and responsive layout.
- Verify actual packaged WebView2 startup with simulated Internet markers retained on every bundled file.
- Bound download recovery to 64 records and at most 30 idle minutes, with earlier idle eviction at capacity; do not change secure file publication, dependency versions, or logo choreography.

## [1.0.14] - 2026-09-18

### Changed

- Replace the home headline with six locally rendered interactive brand logos: GPT spin and galaxy, Claude breathing light, Grok logo refraction, DeepSeek elastic water motion, Kimi lunar trails, and GLM slicing.
- Move collection controls to the results-page bottom dock and open the selection basket directly; use three right-side navigation dots for search, preview, and download pages.
- Remove the planetary page background and retain a quiet graphite backdrop.
- Pause decorative animation offscreen, behind overlays, and in hidden tabs; respect reduced-motion settings.

## [Unreleased]

### Added

- Download ugoira works as the official Pixiv frame ZIP plus a `_ugoira.frames.json` delay manifest, labelled as 动图清晰度 options in the detail panel; ugoira cards and details carry a 动图 badge.
- Animate ugoira works on hover in the result grid and both basket pickers: a bounded `/api/pixiv/ugoira/{id}` endpoint serves the frame manifest and ZIP, and the page decodes frames via ImageBitmap (CSP-safe), attaches the canvas only once frames are ready, and cover-fits playback to the card.
- Give the collection basket a dedicated flow: multi-selection turns the artwork detail page into a batch-download page with only the shared download options and a jump panel, the fullscreen basket page handles artwork and per-image selection, and returning lands back on the batch options. Single-artwork downloads remain available when nothing is selected.
- Add floating chrome: an animated show/hide for the pagination dock, a download-progress task dock, a back-to-top chip, and a basket quick-entry chip.

### Changed

- Keep the download settings (save root, context folder, grouping) on the artwork detail page as the single source for single and batch downloads; the basket page no longer carries its own settings panel.
- Pin the basket header with a sticky bar so the scrollbar tracks the page correctly.
- Restyle the floating chips and task dock as translucent glass surfaces without `backdrop-filter`, and move back-to-top to the bottom-left corner away from the basket entry.

### Fixed

- Ugoira artworks no longer fail detail loading: Pixiv returns no original still image for them, so empty page URLs are tolerated instead of rejecting the whole artwork, and no image capability is issued for empty slots.
- The ugoira badge no longer covers the card select checkbox.
- Restricted (R-18) previews no longer linger in the hidden basket page DOM after authorization loss or view switches.

## [1.0.13] - 2026-09-09

### Fixed

- Respect selected pages for single multi-page Pixiv downloads and refresh the basket when reopening it after selection changes.
- Release staged animation files on write failure; share exception-safe staging cleanup across download paths.
- Cancel inactive animation previews, release decoded frames on failure or logout, and bound preview memory use.
- Keep batch download options independent of the last viewed artwork and count animation pages separately from output files.

### Changed

- Extract animation preview lifecycle management into a dedicated module and share download options and page validation.
- Run only MOKU application tests during portable builds; the optional experimental suite is now explicit via `run_tests.py --subject-mvp0`.

## [1.0.11] - 2026-07-26

### Added

- Add exact `pid:` artwork lookup and `uid:` creator lookup while retaining exact-name `author:` search, with ASCII and full-width colon support.
- Add an explicit search cancel action backed by bounded request tracking and cancellation propagation through session waits, network reads, and result commits.

### Changed

- Apply work type, AI, fuzzy, and safety filters only after the user submits the search form; filter changes no longer start large searches automatically.
- Bound Pixiv search traffic to four process-wide workers, cap fuzzy source expansion to the retained cache budget, and keep cancelled connection attempts from accumulating unbounded work.
- Reuse valid page-image capabilities for downloads even when an unused thumbnail capability was evicted, avoiding unnecessary artwork-detail refreshes.

### Fixed

- Reject mismatched upstream artwork IDs, preserve cursor state across cancelled creator searches, and rebuild evicted deep-page sessions instead of returning empty pages.
- Preserve loaded detail pages and current previews across result, viewer, and basket transitions; update basket selections in place and resume failed download chunks without repeating completed batches.
- Keep mobile basket titles ellipsized inside their cards, and show the fixed pagination dock only while it overlaps the gallery without losing it on short result pages or return scrolling.

## [1.0.10] - 2026-07-25

### Security

- Linearize account connect, disconnect, and replacement so stale login work cannot restore a cleared session; bind R-18 image capabilities to the active authorization generation and return protected responses with `no-store`.
- Keep direct and TUN requests fail-closed by disabling implicit environment and Windows proxy bypasses, while accepting only explicitly selected loopback proxies.
- Stage downloads transactionally, recheck authorization before publication, and roll back every file and directory created by a failed or revoked task.

### Changed

- Use the project-provided artwork as a multi-resolution Windows executable icon and bind both its source PNG and generated ICO into the release fingerprint.
- Limit active download tasks to two, reject saturation with HTTP 429, cap one-artwork downloads at 200 pages, and cache bounded author resolution results in a 64-entry five-minute LRU.
- Window large detail, continuous-viewer, and collection-basket renders to reduce DOM, image, and layout pressure while preserving current selections.
- Make the synthetic fixture gallery drive the complete interface for repeatable desktop and responsive-layout verification.

### Fixed

- Recover expired preview capabilities once per failed URL with deduplicated refreshes and cooldown, without reusing stale tokens after logout or cache eviction.
- Prevent stale search, detail, logout, selection, and multi-request download state from overwriting the latest page or task context.
- Keep long remote labels, navigation, pagination, detail views, and basket controls usable down to 320-pixel-wide layouts.

## [1.0.9] - 2026-07-22

### Changed

- Replace the integrated batch panel with a consistent three-level collection basket: summary, artwork selection, and per-image selection.
- Allow any number of artworks within the existing 1,000-image basket limit and split large single artworks into bounded 200-image requests.
- Use compact artwork and image pickers, report search/basket cache state, and replace the bright ribbon treatment with restrained Saturn rings around the dark moon.

### Fixed

- Build download payloads only from authoritative selected-page sets, so deselected preview images are never submitted.
- Freeze both basket selections and all download options for the lifetime of a multi-request task, including dynamically rebuilt controls.
- Return budget-limited partial search pages instead of hiding valid sparse results, and bound large-creator filtering to resumable request/time budgets.
- Abort stale detail requests and clear every basket view class on back, clear, normal-detail, and replacement transitions.

## [1.0.8] - 2026-07-21

### Fixed

- Restore the one-step batch workflow: opening batch download now selects the current page and shows the integrated artwork picker immediately.
- Preserve per-page selections and original search contexts while keeping result-card and batch-card state synchronized, including stale-detail response protection.

## [1.0.7] - 2026-07-20

### Fixed

- Reject junctions and other reparse points before canonicalization or publication, including those nested below the selected root, and remove every directory created by a failed publish.

## [1.0.6] - 2026-07-20

### Fixed

- Canonicalize user-selected download roots and final publication paths before containment checks, so equivalent Windows path aliases cannot produce false 502 responses or bypass the save-root boundary.
- Isolate threaded download-integrity test network seams and stabilize the publish-identity test path on hosted Windows runners.

## [1.0.5] - 2026-07-18

### Added

- Strict multi-tag AND search using `;` or `；` separators; spaces remain part of one tag.
- Optional bounded anime-oriented tag aliases, disabled by default.
- A collection basket for any number of artworks within a 1,000-image selection limit, with windowed page selection for large works.
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
