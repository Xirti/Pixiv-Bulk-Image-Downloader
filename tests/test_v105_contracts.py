from __future__ import annotations

import unittest
from pathlib import Path

from pixiv_adapter import (
    build_download_context,
    matches_tag_groups,
    resolve_download_target,
    safe_context_folder_name,
)
from search_service import (
    build_search_tag_groups,
    parse_search_tags,
    plan_download_chunks,
)

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
STYLE = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


class V105SearchContractTests(unittest.TestCase):
    def test_semicolon_separators_create_strict_tag_groups_and_spaces_stay_inside_tag(self):
        self.assertEqual(parse_search_tags("猫；夜景; 星 夜"), ("猫", "夜景", "星 夜"))
        self.assertEqual(build_search_tag_groups("猫；夜景"), (("猫",), ("夜景",)))
        self.assertEqual(build_search_tag_groups("猫 夜景"), (("猫 夜景",),))

    def test_alias_expansion_is_bounded_and_prefers_fandom_aliases(self):
        groups = build_search_tag_groups("miku；原神", fuzzy=True)
        self.assertIn("初音未来", groups[0])
        self.assertIn("初音ミク", groups[0])
        self.assertEqual(groups[1], ("原神",))
        self.assertLessEqual(max(len(group) for group in groups), 8)

    def test_strict_tag_predicate_requires_each_group(self):
        self.assertTrue(matches_tag_groups(["猫", "夜景"], (("猫",), ("夜景",))))
        self.assertFalse(matches_tag_groups(["猫"], (("猫",), ("夜景",))))
        self.assertTrue(matches_tag_groups(["初音未来"], (("miku", "初音未来"),)))

    def test_search_source_metadata_normalizes_pixiv_tag_objects(self):
        from pixiv_adapter import normalize_search_item
        raw = {
            "id": "123", "xRestrict": 0, "isUnlisted": False,
            "url": "https://i.pximg.net/example.jpg",
            "tags": {"tags": [{"tag": "猫"}, {"name": "夜景"}]},
            "pageCount": 1,
        }
        self.assertEqual(normalize_search_item(raw)["tags"], ["猫", "夜景"])

    def test_fuzzy_and_exact_sessions_use_distinct_namespaced_cache_keys(self):
        import server
        exact = ("tags", (("miku",),), "safe", "all", True, False)
        fuzzy = (
            "tags", (("miku", "初音未来", "初音ミク", "hatsune miku"),),
            "safe", "all", True, True,
        )
        self.assertNotEqual(exact, fuzzy)
        self.assertEqual(server.search_session_scope(exact), "safe")
        self.assertEqual(server.search_session_scope(fuzzy), "safe")


class V105DownloadContractTests(unittest.TestCase):
    def test_server_enforces_per_request_chunk_limits_not_whole_basket_limits(self):
        self.assertNotIn("MAX_SELECTED_ARTWORKS = 100", SERVER)
        self.assertNotIn("MAX_SELECTED_PAGES = 1000", SERVER)
        self.assertIn("len(groups) <= DOWNLOAD_CHUNK_ARTWORKS", SERVER)
        self.assertIn("if total_pages > DOWNLOAD_CHUNK_PAGES", SERVER)

    def test_download_context_names_one_shared_folder_for_tag_author_and_pid(self):
        self.assertEqual(safe_context_folder_name("tag", "猫；夜景"), "tag_猫；夜景")
        self.assertEqual(safe_context_folder_name("author", "画师/甲"), "author_画师_甲")
        self.assertEqual(safe_context_folder_name("pid", "123456"), "pid_123456")
        self.assertEqual(build_download_context("tags", "猫；夜景")["kind"], "tags")

    def test_create_folder_resolves_shared_context_not_artwork_folder(self):
        root = Path("C:/Pictures")
        context = build_download_context("tags", "猫；夜景")
        self.assertEqual(
            resolve_download_target(root, "标题", "123", True, context=context),
            root / "tag_猫；夜景",
        )

    def test_download_target_rejects_untrusted_context_folder_components(self):
        root = Path("C:/Pictures")
        with self.assertRaisesRegex(ValueError, "invalid download context folder"):
            resolve_download_target(
                root, "标题", "123", True,
                context={"folder": "../outside", "kind": "tags", "value": "猫"},
            )

    def test_chunk_plan_is_image_first_and_bounds_artworks(self):
        groups = [
            {"id": "1", "pages": list(range(1))},
            {"id": "2", "pages": list(range(1))},
            {"id": "3", "pages": list(range(80))},
        ]
        chunks = plan_download_chunks(groups, max_artworks=20, max_pages=200)
        self.assertEqual(sum(chunk["pageCount"] for chunk in chunks), 82)
        self.assertEqual(len(chunks), 1)
        self.assertEqual([row["id"] for row in chunks[0]["groups"]], ["1", "2", "3"])

        many_single_page = [{"id": str(index), "pages": [0]} for index in range(35)]
        chunks = plan_download_chunks(many_single_page, max_artworks=20, max_pages=200)
        self.assertEqual([len(chunk["groups"]) for chunk in chunks], [20, 15])


class V105VisualContractTests(unittest.TestCase):
    def test_deep_art_direction_uses_static_layers_not_runtime_particles(self):
        self.assertIn(".art-depth", STYLE)
        self.assertIn(".moon-ring", STYLE)
        self.assertIn(".art-constellation", STYLE)
        self.assertIn('class="art-depth"', HTML)
        self.assertIn('class="moon-ring"', HTML)
        self.assertIn('class="art-constellation constellation-one"', HTML)
        self.assertIn("aria-hidden=\"true\"", HTML)
        self.assertNotIn("requestAnimationFrame", APP)
        self.assertNotIn("setInterval", APP)

    def test_art_layers_are_bounded_and_disabled_in_conservative_mode(self):
        self.assertIn(".art-depth{", STYLE)
        self.assertIn("max-width:560px", STYLE)
        self.assertIn("max-height:560px", STYLE)
        self.assertIn("box-shadow:inset 22px 15px 28px", STYLE)
        self.assertIn("html.conservative .moon-ring", STYLE)
        self.assertIn("html.conservative .art-constellation", STYLE)
        self.assertIn("prefers-reduced-motion:reduce", STYLE)

    def test_folder_picker_button_has_explicit_dark_text_on_light_surface(self):
        self.assertIn('id="browseFolder"', HTML)
        self.assertIn("#browseFolder{", STYLE)
        self.assertIn("color:#0a111a", STYLE)

    def test_sparse_kimi_style_light_ribbons_are_decorative_and_low_cost(self):
        self.assertIn('class="light-ribbons"', HTML)
        self.assertIn('aria-hidden="true"', HTML)
        self.assertIn(".light-ribbon", STYLE)
        self.assertIn("light-ribbon-drift", STYLE)
        self.assertIn("html.conservative .light-ribbon{animation:none", STYLE)
        self.assertNotIn("requestAnimationFrame", APP)
        self.assertNotIn("backdrop-filter", STYLE)
        self.assertNotIn("filter:", STYLE)

    def test_moon_theme_and_search_controls_are_present_without_removing_existing_actions(self):
        self.assertIn("moon", STYLE)
        self.assertIn("lunar", STYLE)
        self.assertIn("模糊", HTML)
        self.assertIn("采集篮", HTML + APP)
        self.assertIn("旧页缓存即将清理", HTML + APP)
        self.assertIn("createFolder", APP)
        self.assertIn("batchDownload", HTML)

    def test_collection_basket_capacity_decision_only_runs_when_navigation_evicts_selected_pages(self):
        self.assertIn("MAX_SELECTED_ARTWORKS = 100", APP)
        self.assertIn("MAX_SELECTED_PAGES = 1000", APP)
        self.assertIn('id="capacityDialog"', HTML)
        self.assertIn('id="archiveAndContinue"', HTML)
        self.assertIn('id="clearAndContinue"', HTML)
        self.assertIn("pendingNavigationPage", APP)
        self.assertIn("openCapacityDialog", APP)
        self.assertIn("SEARCH_KEEP_BEHIND = 6", APP)
        self.assertIn("const selectedResultPageByArtwork = new Map()", APP)
        self.assertIn("function selectionWouldBeEvicted(targetPage)", APP)
        navigate = APP[
            APP.index("function navigateToPage"):APP.index("function archiveAndContinue")
        ]
        self.assertIn("selectionWouldBeEvicted(page)", navigate)
        self.assertNotIn("currentPageSelectionIds().size > 0", navigate)
        archive = APP[
            APP.index("function archiveAndContinue"):APP.index("function clearAndContinue")
        ]
        clear = APP[
            APP.index("function clearAndContinue"):APP.index("function cancelCapacityDecision")
        ]
        self.assertIn("unarchivedSelectionIds()", archive)
        self.assertIn("unarchivedSelectionIds()", clear)
        self.assertIn("采集篮", APP + HTML)

    def test_opening_detail_does_not_create_ghost_collection_pages(self):
        detail = APP[
            APP.index("function renderDetail"):APP.index("function renderCollectionPageWindow")
        ]
        self.assertNotIn("selectedPagesByArtwork.set", detail)
        count = APP[
            APP.index("function selectedPageCount"):APP.index("function unarchivedSelectionIds")
        ]
        self.assertIn("selectedArtworkIds", count)

    def test_collection_basket_detaches_unarchived_selection_before_cache_eviction(self):
        self.assertIn("const archivedArtworkIds = new Set()", APP)
        self.assertIn("function detachSelection(ids)", APP)
        self.assertIn("function clearSelection(ids)", APP)
        self.assertIn("archivedArtworkIds.add", APP)

    def test_collection_workspace_has_one_download_command_and_separate_picker_surface(self):
        workspace = HTML[HTML.index('id="batchWorkspace"'):HTML.index('id="deck"')]
        self.assertEqual(workspace.count('id="batchDownload"'), 1)
        self.assertIn('id="openBatchPicker"', workspace)
        self.assertIn('id="batchPicker"', HTML)
        self.assertIn('id="closeBatchPicker"', HTML)
        self.assertIn('class="batch-picker-grid"', HTML)
        self.assertIn("renderBatchPicker", APP)
        self.assertIn("openBatchCollection", APP)

    def test_collection_workspace_renders_one_cover_per_artwork_without_a_preview_cap(self):
        batch = APP[
            APP.index("function renderBatchPicker"):APP.index("function selectedGroups")
        ]
        self.assertIn("chosen.map", batch)
        self.assertNotIn("chosen.slice", batch)
        self.assertNotIn("basket-overflow", batch)

    def test_result_page_has_one_click_select_all_controls(self):
        self.assertIn('id="selectAllPage"', HTML)
        self.assertIn('id="clearPageSelection"', HTML)
        self.assertIn("function selectAllCurrentPage()", APP)
        select_all = APP[
            APP.index("function selectAllCurrentPage"):APP.index("function clearAllCurrentPage")
        ]
        self.assertIn("toggleArtworkSelection(item, true)", select_all)
        self.assertIn("additionalPages", select_all)
        self.assertIn("selectedPagesByArtwork.set(item.id, allPages)", select_all)
        self.assertIn("无法全选", select_all)
        self.assertIn("render()", select_all)

    def test_result_pagination_stays_visible_at_viewport_bottom(self):
        self.assertIn('class="pagination-dock"', HTML)
        self.assertIn(".pagination-dock{position:fixed;left:0;right:0;bottom:0", STYLE)
        self.assertIn(".pagination-dock.is-visible{display:flex}", STYLE)
        self.assertIn("function updatePaginationDock()", APP)
        self.assertIn('Boolean($("#pagination").children.length)', APP)
        self.assertIn('window.addEventListener("scroll", updatePaginationDock, { passive: true })', APP)
        self.assertIn("z-index:11", STYLE)
        self.assertIn(".results{padding:55px 5vw 96px", STYLE)

    def test_collection_detail_uses_vertical_page_picker_and_hides_single_download(self):
        self.assertIn('class="detail scene collection-mode"', HTML)
        self.assertIn("body.collection-basket-open #download{display:none}", STYLE)
        self.assertIn("body.collection-basket-open .collection-pages", STYLE)
        self.assertIn("grid-template-columns:repeat(auto-fill,minmax(150px,1fr))", STYLE)
        self.assertIn("aspect-ratio:2/3", STYLE)
        self.assertIn("object-position:center top", STYLE)

    def test_capacity_dialog_buttons_use_restrained_monochrome_styles(self):
        for selector in ("#archiveAndContinue", "#clearAndContinue", "#cancelCapacity"):
            self.assertIn(f"{selector}{{", STYLE)
        capacity_styles = STYLE[STYLE.index("#archiveAndContinue{"):]
        self.assertNotIn("#9dd6ff", capacity_styles)
        self.assertNotIn("#9a3542", capacity_styles)
        self.assertIn("color:#eef3f8", capacity_styles)

    def test_home_uses_one_clean_monochrome_moon_ring_without_search_panel_arcs(self):
        self.assertNotIn(".search-panel::before", STYLE)
        self.assertIn('class="moon-ring"', HTML)
        self.assertIn(".moon-ring::before", STYLE)
        self.assertIn(".moon-ring::after", STYLE)
        self.assertIn("conic-gradient", STYLE)
        self.assertNotIn("rgba(255,141,78", STYLE)

    def test_collection_downloads_are_grouped_by_their_original_search_context(self):
        self.assertIn("const selectedContextByArtwork = new Map()", APP)
        self.assertIn("function planContextDownloadChunks", APP)
        self.assertIn("context: chunk.context", APP)
        self.assertIn("selectedContextByArtwork.delete", APP)

    def test_large_artwork_page_picker_is_windowed(self):
        self.assertIn("DETAIL_PAGE_WINDOW = 48", APP)
        self.assertIn("function renderCollectionPageWindow", APP)
        self.assertIn('id="collectionPageMore"', HTML)

    def test_single_pixiv_download_uses_current_context_folder(self):
        single = SERVER[SERVER.index("def _post_pixiv_download"):SERVER.index("def _post_fixture_download")]
        self.assertIn("download_context", single)
        self.assertIn("download_context=download_context", single)

    def test_download_chunking_is_image_first_and_artwork_grouping_is_optional(self):
        self.assertIn("DOWNLOAD_CHUNK_PAGES = 200", APP)
        self.assertIn("DOWNLOAD_CHUNK_ARTWORKS = 20", APP)
        self.assertIn('id="groupArtworks"', HTML)
        self.assertIn("planDownloadChunks", APP)
        self.assertIn("|| current.length >= DOWNLOAD_CHUNK_ARTWORKS", APP)
        self.assertIn("groupArtworks", APP)
        self.assertIn("context", APP)


if __name__ == "__main__":
    unittest.main()
