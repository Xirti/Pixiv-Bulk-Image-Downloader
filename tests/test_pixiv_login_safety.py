import unittest

import pixiv_login


class PixivLoginSafetyTests(unittest.TestCase):
    def test_cookie_selection_is_strict_and_unique(self):
        good = {"name":"PHPSESSID", "value":"abcdefgh", "domain":".pixiv.net", "path":"/", "secure":True, "httpOnly":True, "expires":9999999999}
        self.assertEqual(pixiv_login.select_session_cookie([good], now=100), "abcdefgh")
        for mutation in [
            {"domain":"evil.pixiv.net"}, {"path":"/x"}, {"secure":False},
            {"httpOnly":False}, {"expires":50}, {"expires":-1},
            {"value":"x y"}, {"partitionKey": {"topLevelSite": "https://pixiv.net"}},
        ]:
            row = dict(good); row.update(mutation)
            with self.assertRaises(ValueError):
                pixiv_login.select_session_cookie([row], now=100)
        conflict = dict(good); conflict["value"] = "differentvalue"
        with self.assertRaises(ValueError):
            pixiv_login.select_session_cookie([good, conflict], now=100)

    def test_cookie_selection_accepts_secure_session_cookie_without_expiry(self):
        session_cookie = {
            "name": "PHPSESSID", "value": "abcdefgh", "domain": ".pixiv.net",
            "path": "/", "secure": True, "httpOnly": True, "expires": 0,
        }
        self.assertEqual(pixiv_login.select_session_cookie([session_cookie], now=100), "abcdefgh")

    def test_cookie_selection_deduplicates_identical_secure_candidates(self):
        first = {
            "name": "PHPSESSID", "value": "abcdefgh", "domain": ".pixiv.net",
            "path": "/", "secure": True, "httpOnly": True, "expires": 0,
        }
        second = dict(first); second["domain"] = "www.pixiv.net"
        self.assertEqual(pixiv_login.select_session_cookie([first, second], now=100), "abcdefgh")

    def test_cookie_metadata_summary_never_contains_cookie_values(self):
        rows = [
            {"name": "PHPSESSID", "value": "never-log-this", "domain": ".pixiv.net", "path": "/", "secure": True, "httpOnly": True, "expires": 0},
            {"name": "PHPSESSID", "value": "expired-secret", "domain": "www.pixiv.net", "path": "/", "secure": True, "httpOnly": True, "expires": 50},
            {"name": "other", "value": "other-secret", "domain": ".pixiv.net", "path": "/", "secure": True, "httpOnly": True, "expires": 999},
        ]
        summary = pixiv_login.session_cookie_metadata(rows, now=100)
        self.assertEqual(summary["phpRows"], 2)
        self.assertEqual(summary["eligibleRows"], 1)
        self.assertEqual(summary["sessionRows"], 1)
        self.assertEqual(summary["expiredRows"], 1)
        rendered = repr(summary)
        self.assertNotIn("never-log-this", rendered)
        self.assertNotIn("expired-secret", rendered)
        self.assertNotIn("other-secret", rendered)


if __name__ == "__main__":
    unittest.main()
