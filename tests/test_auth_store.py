import unittest
from unittest.mock import patch

import auth_store


class AuthStoreTests(unittest.TestCase):
    def test_probe_isolation_disables_all_persistent_credential_operations(self):
        with patch.dict("os.environ", {"MOKU_DISABLE_PERSISTENT_SESSION": "1"}, clear=False), \
                patch.object(auth_store.advapi32, "CredReadW") as read, \
                patch.object(auth_store.advapi32, "CredWriteW") as write, \
                patch.object(auth_store.advapi32, "CredDeleteW") as delete:
            self.assertEqual(auth_store.read_persistent_session(), "")
            auth_store.write_persistent_session("isolated-session")
            auth_store.delete_persistent_session()

        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()

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
