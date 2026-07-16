import unittest
from pathlib import Path

import server
from pixiv_adapter import PixivPolicyError, resolve_web_path, validate_public_policy


class PixivSecurityRegressionTests(unittest.TestCase):
    def test_http_responses_install_strict_content_security_policy(self):
        source = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy", source)
        self.assertIn("default-src 'self'", source)
        self.assertIn("object-src 'none'", source)
        self.assertIn("frame-ancestors 'none'", source)

    def test_static_path_cannot_escape_web_root(self):
        root = Path("C:/app/web")
        self.assertEqual(resolve_web_path(root, "/style.css"), root / "style.css")
        with self.assertRaises(PixivPolicyError):
            resolve_web_path(root, "/../server.py")
        with self.assertRaises(PixivPolicyError):
            resolve_web_path(root, "/%2e%2e/server.py")

    def test_public_policy_is_fail_closed_and_shared(self):
        valid = {"xRestrict": 0, "isMasked": False, "isUnlisted": False, "visibilityScope": 0, "isLoginOnly": False}
        validate_public_policy(valid, detail=True)
        for field, value in [("xRestrict", 1), ("isMasked", True), ("isUnlisted", True), ("visibilityScope", 1), ("isLoginOnly", True)]:
            bad = dict(valid); bad[field] = value
            with self.assertRaises(PixivPolicyError, msg=field):
                validate_public_policy(bad, detail=True)
        missing = dict(valid); del missing["xRestrict"]
        with self.assertRaises(PixivPolicyError):
            validate_public_policy(missing, detail=True)


if __name__ == "__main__": unittest.main()
