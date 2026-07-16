import unittest
from pathlib import Path

from pixiv_adapter import normalize_search_item

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "launch-moku.ps1").read_text(encoding="utf-8-sig")
DESKTOP = (ROOT / "desktop_client.py").read_text(encoding="utf-8")


def row(illust_type=0, ai_type=1):
    return {"id":"123", "title":"t", "userName":"u", "userId":"1", "url":"https://i.pximg.net/a.jpg", "tags":[], "pageCount":1, "width":10, "height":10, "xRestrict":0, "isUnlisted":False, "isMasked":False, "visibilityScope":0, "illustType":illust_type, "aiType":ai_type}


class WorkflowV2Tests(unittest.TestCase):
    def test_manual_release_probes_follow_protected_get_handshake(self):
        root = Path(__file__).resolve().parent
        probes = (
            "final_packaged_search_probe.py",
            "final_packaged_tag_cache_probe.py",
            "final_source_search_probe.py",
            "live_history_probe.py",
            "packaged_native_click_login_probe.py",
        )
        for name in probes:
            source = (root / name).read_text(encoding="utf-8-sig")
            self.assertIn("api/health", source, name)
            self.assertIn("X-MOKU-Request-Token", source, name)

    def test_frontend_sends_request_token_on_protected_gets(self):
        self.assertIn('headers.set("X-MOKU-Request-Token", await getRequestToken())', APP)
        self.assertNotIn('if (method !== "GET" && method !== "HEAD")', APP)

    def test_normalized_items_expose_type_and_ai_generation(self):
        self.assertEqual(normalize_search_item(row(1, 2))["workType"], "manga")
        self.assertTrue(normalize_search_item(row(0, 2))["aiGenerated"])
        self.assertFalse(normalize_search_item(row(2, 1))["aiGenerated"])

    def test_search_ui_has_type_and_ai_controls(self):
        self.assertIn('id="workType"', HTML)
        self.assertIn('id="includeAi"', HTML)
        self.assertIn("workType", APP)
        self.assertIn("includeAi", APP)
        self.assertIn("aiGenerated", SERVER)

    def test_desktop_webview_is_the_only_login_entrypoint(self):
        self.assertNotIn(chr(34)+"/api/auth/login"+chr(34), SERVER)
        self.assertNotIn("/api/auth/tasks/", SERVER)
        self.assertNotIn("run_login", SERVER)
        self.assertNotIn("/api/auth/login", APP)

    def test_launcher_offers_desktop_and_browser_after_readiness(self):
        self.assertIn("Desktop", LAUNCHER)
        self.assertIn("Browser", LAUNCHER)
        prompt_position = LAUNCHER.index("MessageBox]::Show")
        self.assertLess(LAUNCHER.index("/api/health"), prompt_position)
        self.assertLess(LAUNCHER.index("app.js"), prompt_position)

    def test_desktop_mode_uses_webview2_main_and_pixiv_login_windows(self):
        self.assertIn("webview.create_window", DESKTOP)
        self.assertIn("MOKU \u2014 Pixiv \u6807\u7b7e\u91c7\u96c6\u518c", DESKTOP)
        self.assertIn("MOKU \u2014 Pixiv \u5b98\u65b9\u767b\u5f55", DESKTOP)
        self.assertIn("login.get_cookies()", DESKTOP)
        self.assertIn("is_completed_pixiv_login_url", DESKTOP)
        self.assertIn("select_session_cookie", DESKTOP)
        self.assertIn("store_session", DESKTOP)
        self.assertNotIn("verify_session_status", DESKTOP)

    def test_desktop_launcher_starts_webview_host_not_edge_app(self):
        self.assertIn("moku_app.py", LAUNCHER)
        desktop_block = LAUNCHER[LAUNCHER.index("if($desktopChosen)"):LAUNCHER.index("elseif($browserChosen)")]
        self.assertNotIn("--app=$url", desktop_block)
        self.assertNotIn("EdgeAppProfile", desktop_block)
        self.assertIn("Start-Process -FilePath $python", desktop_block)
        self.assertIn("$env:MOKU_CODE_GENERATION=$codeGeneration", desktop_block)
    def test_launcher_supports_noninteractive_verification_mode(self):
        self.assertIn("ValidateSet('Prompt','Desktop','Browser','Cancel')", LAUNCHER)
        self.assertIn("$Mode = 'Prompt'", LAUNCHER)

    def test_launcher_rejects_backends_without_current_request_token_protocol(self):
        self.assertIn("$protocolVersion = 5", LAUNCHER)
        self.assertIn("requestToken", LAUNCHER)
        self.assertIn("codeGeneration", LAUNCHER)
        self.assertIn("MOKU_CODE_GENERATION", LAUNCHER)
        self.assertIn("Get-FileHash", LAUNCHER)
        self.assertNotIn("protocolVersion -eq 1", LAUNCHER)

    def test_browser_mode_never_attempts_pixiv_login(self):
        self.assertIn("账户授权只在 MOKU 桌面版提供", APP)
        self.assertIn("window.pywebview?.api?.pixiv_login", APP)
        desktop_branch = APP[APP.index('$("#authAction").onclick'):]
        self.assertNotIn('fetchJson("/api/auth/login"', desktop_branch)

    def test_login_ui_describes_live_monitoring_and_global_rounded_controls(self):
        self.assertIn("实时监控", HTML + APP)
        self.assertNotIn("完成后请关闭", HTML + APP)
        self.assertNotIn("完成后关闭", HTML + APP)
        self.assertIn("--radius", STYLE)
        self.assertIn("button,input:not([type=checkbox]):not([type=radio]),select", STYLE)
        self.assertIn(".path-picker{gap:8px}", STYLE)
        self.assertIn(".path-picker button{border-radius:var(--radius)}", STYLE)
        self.assertIn("focus-visible", STYLE)


    def test_header_embeds_offline_usage_guide_and_user_triggered_network_diagnosis(self):
        self.assertIn('id="helpBtn"', HTML)
        self.assertIn('id="helpDialog"', HTML)
        self.assertIn('id="networkCheck"', HTML)
        for section in ("快速开始", "网络连接", "登录与隐私", "搜索与分页", "下载与文件", "SHA-256", "常见问题"):
            self.assertIn(section, HTML)
        self.assertIn('fetchJson("/api/network/diagnose"', APP)
        network_click = APP[APP.index('$("#networkCheck").onclick'):]
        self.assertIn('fetchJson("/api/network/diagnose"', network_click)
        startup = APP[APP.index('$("#count").textContent = "等待搜索"'):]
        self.assertNotIn('fetchJson("/api/network/diagnose"', startup)
        self.assertIn('querySelectorAll(".dialog-close")', APP)
        self.assertNotIn('$(".dialog-close").onclick', APP)
        self.assertIn("min-height:44px", STYLE)
        self.assertIn("help-button", STYLE)

    def test_network_guide_states_it_never_changes_windows_or_starts_vpn(self):
        self.assertIn("不会修改 Windows 系统代理", HTML)
        self.assertIn("不会自动启动 VPN", HTML)
        self.assertIn("不会扫描本机端口", HTML)


    def test_revoked_preview_tokens_fall_back_without_broken_image_icons(self):
        self.assertIn("function installImageFallbacks", APP)
        self.assertIn('img.addEventListener("error"', APP)
        self.assertIn('classList.add("image-unavailable")', APP)
        self.assertGreaterEqual(APP.count("installImageFallbacks("), 4)
        self.assertIn(".image-unavailable", STYLE)


    def test_network_errors_are_rendered_as_chinese_and_image_fallbacks_do_not_block_clicks(self):
        for label in ("连接超时", "连接被拒绝", "证书或 TLS 错误", "HTTP 响应异常", "无法连接"):
            self.assertIn(label, APP)
        self.assertIn("errorLabels[row?.errorKind]", APP)
        self.assertIn("pointer-events:none", STYLE)
        self.assertIn(".batch-collection.image-unavailable::after", STYLE)
        self.assertIn("width:62px", STYLE)

    def test_detail_selection_uses_artwork_identity_not_search_index(self):
        self.assertIn("let activeArtworkId", APP)
        self.assertNotIn("let selected = -1", APP)
        self.assertNotIn("items[selected]", APP)

    def test_detail_layout_and_collection_navigation_exist(self):
        self.assertIn('class="detail-info"', HTML)
        self.assertIn('class="detail-content"', HTML)
        self.assertIn('id="collectionPages"', HTML)
        self.assertIn('id="batchCollections"', HTML)
        self.assertLess(HTML.index('class="detail-content"'), HTML.index('id="batchWorkspace"'))
        self.assertIn("openBatchCollection", APP)
        self.assertIn("selectedPagesByArtwork", APP)
        self.assertIn("returnToBatch", APP)
        self.assertIn("page-select", STYLE)

    def test_batch_summary_is_immediate_and_detail_is_loaded_on_navigation(self):
        batch_block = APP[APP.index("async function renderBatchWorkspace"):APP.index("async function openBatchCollection")]
        detail_start = APP.index("async function openBatchCollection")
        detail_end = APP.index('$("#returnToBatch").onclick', detail_start)
        detail_block = APP[detail_start:detail_end]
        self.assertNotIn("await fetchJson", batch_block)
        self.assertIn("await fetchJson", detail_block)

    def test_page_selection_enrolls_collection_in_cross_collection_batch(self):
        self.assertIn("selectedArtworks.set(item.id, item)", APP)
        self.assertIn("selectedArtworkIds.add(item.id)", APP)


if __name__ == "__main__": unittest.main()
