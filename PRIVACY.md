# Privacy

MOKU is a local Windows application. It has no analytics, advertising SDK, crash-upload service, or application-operated cloud backend.

## Data stored locally

- Downloads are written to the folder selected by the user.
- Successful downloads are recorded in `%LOCALAPPDATA%\MOKU\library\downloads.sqlite3` for the current Windows user. Up to 5,000 artwork records contain titles, artist names, artwork IDs, selected pages, quality/format, completion times and saved file paths. No cookies, tokens or image copies are stored in this database. It is machine-local history, shared across Pixiv accounts used by that Windows user. History is loaded only on entry; it does not scan old downloads or contact Pixiv.
- Animation previews use bounded in-memory caches, cleared on account changes. GIF conversion runs locally. Optional MP4 conversion uses a local FFmpeg registered in PATH and an owned temporary folder, which is removed after conversion; no artwork is sent to a conversion service.
- Runtime logs are written beside the application. HTTP logs omit query parameters, cookies, request bodies, and image authorization tokens.
- Temporary WebView2 profiles are removed when the desktop window closes; stale `session-*` profiles older than 24 hours are cleaned on startup.
- When **Keep me signed in** is selected, only Pixiv's `PHPSESSID` is stored for the current Windows user in Windows Credential Manager under `MOKU.Pixiv.PHPSESSID`.
- Without that option, the session remains in process memory only and is removed on exit.

## Network requests

MOKU contacts only the following service hosts during normal use:

- `https://www.pixiv.net` for search, metadata, and the official login page;
- `https://i.pximg.net` for artwork images.

The Pixiv session is sent only to `www.pixiv.net`. It is never sent to the image CDN. The optional network diagnosis runs only after a user click and uses anonymous requests without the Pixiv session.

MOKU may use an enabled loopback HTTP proxy from the current Windows proxy settings or `HTTPS_PROXY`. It rejects non-loopback proxy addresses and does not change proxy, VPN, TUN, or routing settings.

## Deleting local data

1. Use **Log out** in MOKU.
   Logging out does not erase download history. Use **Clear records** in the history view to delete its metadata; downloaded files and pending download recovery state are not deleted.
2. Remove `MOKU.Pixiv.PHPSESSID` from Windows Credential Manager if it remains.
3. Delete the app's `logs` and `downloads` folders as desired.
4. Delete `%LOCALAPPDATA%\MOKU` after MOKU is closed to remove runtime descriptors and any stale temporary profiles.

Downloaded artwork may be subject to Pixiv's terms and creator rights. Do not redistribute it without authorization.
