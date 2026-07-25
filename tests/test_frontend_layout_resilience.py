import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
STYLE = (ROOT / "web" / "style.css").read_text(encoding="utf-8")


def declarations_for(selector: str) -> str:
    """Return the declarations from every simple rule containing selector."""
    declarations = []
    source = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.DOTALL)
    for selector_list, body in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
        selectors = [item.strip() for item in selector_list.split(",")]
        if selector in selectors:
            declarations.append(body.replace(" ", "").replace("\n", ""))
    return ";".join(declarations)


class FrontendLayoutResilienceTests(unittest.TestCase):
    def assert_declarations(self, selector: str, *expected: str) -> None:
        declarations = declarations_for(selector)
        self.assertTrue(declarations, f"missing CSS rule for {selector}")
        for declaration in expected:
            self.assertIn(declaration, declarations, f"{selector} lacks {declaration}")

    def test_viewer_failure_overlay_is_scoped_to_each_figure(self):
        self.assert_declarations(
            ".viewer-grid figure",
            "position:relative",
            "min-width:0",
            "overflow:hidden",
        )
        self.assert_declarations(
            ".viewer-grid figure.image-unavailable::after",
            "position:absolute",
            "inset:0",
        )

    def test_remote_titles_authors_and_tags_can_shrink_and_wrap(self):
        for selector in (
            ".section-head>div",
            ".gallery-tools",
            ".meta>div",
            ".detail-info",
            ".detail-content",
            ".viewer-bar>div",
            ".viewer-meta",
            ".batch-card-copy",
        ):
            self.assert_declarations(selector, "min-width:0")

        for selector in (
            "#tagTitle",
            "#dTitle",
            "#dArtist",
            "#viewerTitle",
            "#viewerArtist",
            ".meta h3",
            ".meta p",
            ".tags span",
            ".batch-card-copy b",
            ".batch-card-copy small",
        ):
            self.assert_declarations(selector, "overflow-wrap:anywhere")

    def test_narrow_layout_exposes_text_navigation_for_all_three_scenes(self):
        for target, label in (
            ("home", "搜索"),
            ("gallery", "画廊"),
            ("detail", "作品"),
        ):
            self.assertRegex(
                HTML,
                rf'<a href="#{target}"[^>]*>{label}</a>',
            )
        self.assertIn('@media(max-width:600px)', STYLE)
        self.assert_declarations(".page-rail", "display:flex")
        self.assert_declarations(".scene", "scroll-margin-top:52px")
        self.assertIn('@media(max-width:360px)', STYLE)
        self.assert_declarations(".search-row", "flex-direction:column")
        self.assertIn('id="returnToBatch"', HTML)


if __name__ == "__main__":
    unittest.main()
