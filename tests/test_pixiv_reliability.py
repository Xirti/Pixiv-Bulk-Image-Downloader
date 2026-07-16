import unittest

from pixiv_adapter import should_retry_status


class PixivReliabilityTests(unittest.TestCase):
    def test_only_transient_server_statuses_are_retryable(self):
        self.assertTrue(should_retry_status(500))
        self.assertTrue(should_retry_status(503))
        self.assertFalse(should_retry_status(401))
        self.assertFalse(should_retry_status(403))
        self.assertFalse(should_retry_status(429))


    def test_single_attempt_anonymous_probe_never_sleeps_after_failure(self):
        import urllib.error
        import server
        from unittest.mock import Mock, patch
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError("offline")
        with patch.object(server, "PIXIV_OPENER", opener), patch.object(server.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.URLError):
                server.pixiv_request(
                    "https://www.pixiv.net/", anonymous=True, timeout=1, attempts=1,
                )
        sleep.assert_not_called()



if __name__ == "__main__": unittest.main()
