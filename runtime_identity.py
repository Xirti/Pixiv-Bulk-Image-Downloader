"""Build/runtime identity, independent of server startup and local storage."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


CODE_GENERATION_FILES = (
    'server.py',
    'app_updates.py',
    'update_package.py',
    'update_install.py',
    'release_security.py',
    'release_trust.py',
    'release_build_policy.py',
    'web/app-updates.js',
    'web/update-notice.js',
    'web/media-budget.js',
    'source_search.py',
    'source_results.py',
    'source_browser.py',
    'web/source-search.js',
    'web/source-search.css',
    'runtime_identity.py',
    'auth_store.py',
    'folder_picker.py',
    'pixiv_login.py',
    'login_context.py',
    'moku_app.py',
    'desktop_client.py',
    'native_window_frame.py',
    'network_config.py',
    'network_transport.py',
    'shared_request_budget.py',
    'request_coordinator.py',
    'request_rhythm.py',
    'browser_transport.py',
    'browser_profile.py',
    'pixiv_adapter.py',
    'artist_service.py',
    'pixiv_account.py',
    'auth_method.py',
    'pixiv_app_gallery.py',
    'pixiv_app_config.py',
    'account_pages.py',
    'discovery_service.py',
    'popularity_service.py',
    'account_authorization.py',
    'cloud_state.py',
    'cloud_actions.py',
    'web_cloud.py',
    'search_aliases.py',
    'search_service.py',
    'download_requests.py',
    'download_execution.py',
    'download_pipeline.py',
    'download_feedback.py',
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
    'web/download-statistics.js',
    'web/download-statistics.css',
    'web/workspace-sync.js',
    'web/ugoira-preview.js',
    'web/theme.js',
    'web/selection-store.js',
    'web/artwork-detail-view.js',
    'web/artist-page.js',
    'web/popularity.js',
    'web/browse-options.js',
    'web/viewport.js',
    'web/artist-page.css',
    'web/account-page.js',
    'web/page-reads.js',
    'web/account-page.css',
    'web/cloud-controls.js',
    'web/cloud-controls.css',
    'web/account-settings.js',
    'web/artwork-cards.js',
    'web/sensitive-image-reveals.js',
    'web/gallery-layout.js',
    'web/preview-image-cache.js',
    'web/preview-image-retry.js',
    'web/search-page-cache.js',
    'web/card-interaction.js',
    'web/gallery-interaction.js',
    'web/image-stage.js',
    'web/image-viewer.js',
    'web/detail-image-cache.js',
    'web/navigation-history.js',
    'web/history-input.js',
    'web/page-context-menu.js',
    'web/navigation.css',
    'web/interaction.css',
    'web/style.css',
    'web/desktop-style.css',
    'web/adaptive-layout.js',
    'web/preview-layout.js',
    'web/discovery-page.js',
    'web/discovery-page.css',
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
