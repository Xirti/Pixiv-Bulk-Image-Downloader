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
    'experiment_profile.py',
    'web/glass-lab.css',
    'web/glass-lab.js',
    'web/vendor/lens/card-list.js',
    'web/vendor/lens/component-catalog.js',
    'web/vendor/lens/component-patterns.js',
    'web/vendor/lens/components.css',
    'web/vendor/lens/components.js',
    'web/vendor/lens/focus.js',
    'web/vendor/lens/index.js',
    'web/vendor/lens/internal/context-budget.js',
    'web/vendor/lens/internal/jelly-physics.js',
    'web/vendor/lens/internal/jelly.js',
    'web/vendor/lens/internal/lens-surface.js',
    'web/vendor/lens/internal/optical-motion.js',
    'web/vendor/lens/internal/optics.js',
    'web/vendor/lens/internal/renderer.js',
    'web/vendor/lens/LICENSE',
    'web/vendor/lens/optical-components.js',
    'web/vendor/lens/README.md',
    'web/vendor/lens/runtime.js',
    'web/vendor/lens/sound.js',
    'web/vendor/lens/tokens.css',
    'web/vendor/lens/tokens.js',
    'web/vendor/lens/UPSTREAM.md',
    'web/glass-select.js',
    'web/dot-title.js',
    'web/check-feedback.js',
    'web/lattice-backdrop.js',
    'web/vendor/lattice/point-sets.js',
    'web/vendor/lattice/dots.js',
    'web/vendor/lattice/LICENSE',
    'web/vendor/lattice/UPSTREAM.md',
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
