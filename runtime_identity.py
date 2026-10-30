"""Build/runtime identity, independent of server startup and local storage."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


CODE_GENERATION_FILES = (
    'server.py',
    'runtime_identity.py',
    'auth_store.py',
    'folder_picker.py',
    'pixiv_login.py',
    'moku_app.py',
    'desktop_client.py',
    'network_config.py',
    'pixiv_adapter.py',
    'search_aliases.py',
    'search_service.py',
    'download_requests.py',
    'download_history.py',
    'local_catalog.py',
    'workspace_store.py',
    'preview_resources.py',
    'ugoira_export.py',
    'version.py',
    'web/index.html',
    'web/app.js',
    'web/text-context-menu.js',
    'web/download-history.js',
    'web/workspace-sync.js',
    'web/ugoira-preview.js',
    'web/theme.js',
    'web/selection-store.js',
    'web/artwork-detail-view.js',
    'web/style.css',
    'edition_profile.py',
)


def compute_code_generation(
    *, root: Path | None = None, files: tuple[str, ...] = CODE_GENERATION_FILES,
    executable: Path | None = None, frozen: bool | None = None,
) -> str:
    """Include loose web resources when deciding whether a backend is reusable."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    resource_root = Path(root or getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    inputs = [(name, resource_root / name) for name in files
              if not is_frozen or name.startswith("web/")]
    if is_frozen:
        inputs.insert(0, ("executable", Path(executable or sys.executable).resolve()))
    digest = hashlib.sha256()
    for name, path in inputs:
        digest.update(name.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    prefix = "bundle" if is_frozen else "source"
    return f"{prefix}-sha256:{digest.hexdigest()}"
