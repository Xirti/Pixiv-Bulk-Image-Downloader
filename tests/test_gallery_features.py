import unittest
from pathlib import Path

from pixiv_adapter import safe_artwork_stem, resolve_download_target


class GalleryFeatureTests(unittest.TestCase):
    def test_image_token_pruning_removes_expired_rows_and_bounds_growth(self):
        import server
        server.IMAGE_TOKENS.clear()
        now = 1000.0
        server.IMAGE_TOKENS["expired"] = (999.0, "1", "https://i.pximg.net/expired.jpg", "safe")
        for index in range(server.MAX_IMAGE_TOKENS + 20):
            server.IMAGE_TOKENS[f"old-{index}"] = (now + index + 1, str(index), f"https://i.pximg.net/{index}.jpg", "safe")
        server.prune_image_tokens(now=now)
        self.assertNotIn("expired", server.IMAGE_TOKENS)
        self.assertLessEqual(len(server.IMAGE_TOKENS), server.MAX_IMAGE_TOKENS)
        server.authorize_image_proxy("/api/pixiv/image?url=https%3A%2F%2Fi.pximg.net%2Fnew.jpg", "999", "safe")
        self.assertLessEqual(len(server.IMAGE_TOKENS), server.MAX_IMAGE_TOKENS)

    def test_artwork_stem_removes_windows_path_characters(self):
        self.assertEqual(safe_artwork_stem('猫:夜/星*?"<>|', '123'), '猫_夜_星_123')
        self.assertEqual(safe_artwork_stem('...', '123'), 'pixiv_123')

    def test_download_target_can_optionally_create_artwork_folder(self):
        root = Path('C:/Pictures')
        self.assertEqual(resolve_download_target(root, '猫', '123', True), root / '猫_123')
        self.assertEqual(resolve_download_target(root, '猫', '123', False), root)


if __name__ == '__main__': unittest.main()
