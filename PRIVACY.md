# Privacy

MOKU is a local Windows application. It has no analytics, advertising SDK, crash-upload service, or application-operated cloud backend.

## Data stored locally

- Downloads are written to the folder selected by the user.
- Successful downloads are recorded in `%LOCALAPPDATA%\MOKU\library\downloads.sqlite3` for the current Windows user. Up to 5,000 artwork records contain titles, artist names, artwork IDs, selected pages, quality/format, completion times and saved file paths. No cookies, tokens or image copies are stored in this database. It is machine-local history, shared across Pixiv accounts used by that Windows user. History is loaded only on entry; it does not scan old downloads or contact Pixiv.
- The same library directory holds an index of downloaded file paths, sizes and modification times; the collection basket; the latest 8 searches and their filters; and up to 20 unfinished download tasks. Tasks store artwork IDs, selected pages, output options and completed-batch progress. Restricted selections and saved tasks are bound to a non-secret account identifier. These records contain no login credentials or expiring image URLs. Restored tasks stay paused until the user resumes them.
- Discovery snapshots (recommendations, following updates and rankings) use the account cache, isolated by account and login method. They retain ordering, pagination and source metadata without credentials or expiring local image capabilities.
- The image viewer reuses a bounded set of decoded previews and may prepare the next preview of the opened multi-page work. Originals load only after an explicit request, remain in memory for up to five minutes under a 96 MiB / six-image budget, and are never stored as a persistent preview cache. An expired image currently being displayed is released when the viewer leaves it. Downloaded originals remain in the user-selected download folder.
- Animation previews use bounded in-memory caches, cleared on account changes. GIF conversion runs locally. Optional MP4 conversion uses a local FFmpeg registered in PATH and an owned temporary folder, which is removed after conversion; no artwork is sent to a conversion service.
- Runtime logs are written beside the application. HTTP logs omit query parameters, cookies, request bodies, and image authorization tokens.
- Temporary WebView2 profiles are removed when the desktop window closes; stale `session-*` profiles older than 24 hours are cleaned on startup.
- When **Keep me signed in** is selected, only Pixiv's `PHPSESSID` is stored for the current Windows user in Windows Credential Manager under `MOKU.Pixiv.PHPSESSID`.
- Without that option, the session remains in process memory only and is removed on exit.
- App authorization stores access/refresh tokens in Windows Credential Manager under `MOKU.Pixiv.AppOAuth`. A previous development-sample credential is migrated after verifying the App identity. Tokens are automatically refreshed and never exposed to page scripts, page snapshots, or logs. App authorization does not require a website session. Logging out removes both website and App credentials.
- `auth-method.json` in the library directory stores only the selected login method (`app` or `web`). App is the default; Web is an optional compatibility route. The methods may retain separate saved credentials but only the selected route supplies data and account identity. Switching revokes old page/image capabilities and clears cached data; it does not perform cloud writes or copy credentials between routes. Active downloads prevent switching.
- `account-browse.sqlite3` in the library directory retains account/author IDs and observed/seen artwork timestamps for local NEW markers until manually cleared. It stores no credentials or images. The profile can show the account's returned background and up to six recent visible own works, using preview images rather than originals.
- `account-cache.sqlite3` stores account-scoped profile, own-work, following, follower, bookmark and tag page metadata, plus validated pagination continuations. Page data is retained across navigation and restarts until explicitly refreshed or cleared. Expiring local image capabilities are generated when serving a page and are not saved in this database. Within a session, loaded account-page image elements are reused. Switching accounts detaches the old account's visible state; durable metadata remains isolated by account and login method. **Clear account cache** in account settings removes the current account's page/relationship caches and local NEW observations, while preserving login credentials, preferences, downloads, selection and task recovery records. Operation identifiers used to prevent duplicate writes remain protected.
- After login restoration, account metadata is restored locally. With automatic synchronization enabled, MOKU prepares profile, supported follower data, own-work previews, following updates, bookmark tags and initial display pages in the background. Each bookmark/follow scan response also supplies the display cache; public/private pages alternate. Only initial low-resolution previews and the next browsed page are warmed, under the shared request budget. Automatic synchronization can be disabled; manual synchronization remains available. A page refresh reloads that page and does not start an unrelated full relationship scan. Manually clearing account caches cancels preparation and pauses automatic rebuilding until the next configured interval. Artwork originals are not prefetched by account synchronization.
- `cloud-state.sqlite3` stores account-scoped bookmark/follow IDs, known privacy and personal bookmark tags, confirmation times, partial synchronization cursors, and recent operation identifiers used to avoid repeating writes after a lost response. It stores no credentials or preview images. It also stores local download defaults and account-specific interaction/synchronization preferences. Automatic metadata synchronization is enabled by default, runs in paced pages while MOKU is in the foreground, and can be disabled or assigned any whole-minute interval of at least three minutes. A completed round reads both public and private lists. Disabling automatic synchronization retains local state; explicit refreshes and operation verification still contact Pixiv.
- Bookmark/follow creation, editing and removal occur only after user action. Opening an action menu uses locally confirmed state and cached tag suggestions, without reading Pixiv. Conflicts are checked when the user submits the action; unknown local fields are not treated as confirmed negative states. New actions initially ask for visibility with private preselected, then remember the last successful choice. Users may choose fixed public/private defaults in account settings. Download selection is independent. In Web mode, a user-requested official window can restore the current website session for normal Pixiv verification/interaction; the official document receives no MOKU native bridge. The application reads back state afterward and does not silently fall back to a public action.
- Starting App authorization temporarily registers an unclaimed `pixiv:` callback for the current Windows user and saves a loopback-only callback descriptor in the library directory. It removes its own registration and descriptor after completion, cancellation, timeout or normal exit; existing protocol associations are not overwritten.

## Network requests

MOKU contacts only the following service hosts during normal use:

- `https://www.pixiv.net` for search, metadata, and the official login page;
- `https://app-api.pixiv.net` for App-mode search, artwork/artist metadata, account data, bookmark/follow operations, and official browser authorization;
- `https://oauth.secure.pixiv.net` for App authorization exchange and token refresh;
- `https://i.pximg.net` for artwork images.

The website session is used only in Web mode and sent only to `www.pixiv.net`. App bearer tokens are sent only to the App API; token exchange and refresh go only to the OAuth host. Neither credential is sent to the image CDN. Transient read-only App requests may retry once under the existing request budget; token exchange and cloud writes are not replayed automatically. The optional network diagnosis runs only after a user click and uses anonymous requests without the Pixiv session.

MOKU may use an enabled loopback HTTP proxy from the current Windows proxy settings or `HTTPS_PROXY`. It rejects non-loopback proxy addresses and does not change proxy, VPN, TUN, or routing settings.

## Deleting local data

1. Use **Log out** in MOKU.
   Logging out does not erase download history. Use **Clear records** in the history view to delete its metadata; downloaded files and pending download recovery state are not deleted.
2. Remove `MOKU.Pixiv.PHPSESSID`, `MOKU.Pixiv.AppOAuth`, and any older `MOKU.Pixiv.AppOAuth.Development` from Windows Credential Manager if they remain.
3. Delete the app's `logs` and `downloads` folders as desired.
4. Delete `%LOCALAPPDATA%\MOKU` after MOKU is closed to remove runtime descriptors and any stale temporary profiles.

Downloaded artwork may be subject to Pixiv's terms and creator rights. Do not redistribute it without authorization.
