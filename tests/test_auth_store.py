import unittest
from unittest.mock import patch

import auth_store


class AuthStoreTests(unittest.TestCase):
    def test_cookie_header_is_never_built_without_valid_session(self):
        with patch.object(auth_store, "read_session", return_value=""):
            self.assertEqual(auth_store.session_cookie_header(), {})

    def test_cookie_header_contains_only_session_cookie(self):
        with patch.object(auth_store, "read_session", return_value="abc123"):
            self.assertEqual(auth_store.session_cookie_header(), {"Cookie": "PHPSESSID=abc123"})

    def test_rejects_invalid_cookie_shape_before_storage(self):
        for value in ["", "a b", "x;y", "\n", "a" * 300]:
            with self.assertRaises(ValueError):
                auth_store.validate_session_value(value)


if __name__ == "__main__":
    unittest.main()
