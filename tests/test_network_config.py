import unittest
from network_config import normalize_loopback_proxy


class NetworkConfigTests(unittest.TestCase):
    def test_normalizes_registry_proxy_shapes(self):
        self.assertEqual(normalize_loopback_proxy("127.0.0.1:7890"), "http://127.0.0.1:7890")
        self.assertEqual(normalize_loopback_proxy("http=127.0.0.1:7890;https=127.0.0.1:7890"), "http://127.0.0.1:7890")

    def test_rejects_remote_or_invalid_proxy(self):
        for value in ["example.com:7890", "127.0.0.1", "127.0.0.1:99999", "file://127.0.0.1:1"]:
            self.assertEqual(normalize_loopback_proxy(value), "")


if __name__ == "__main__":
    unittest.main()
