import unittest
from unittest.mock import patch

import auth_store


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        auth_store.clear_memory_session()
        auth_store._PERSISTENT_SESSION_CACHE = auth_store._SESSION_CACHE_UNSET

    def tearDown(self):
        auth_store.clear_memory_session()
        auth_store._PERSISTENT_SESSION_CACHE = auth_store._SESSION_CACHE_UNSET

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

    def test_persistent_write_failure_does_not_commit_new_memory_session(self):
        with patch.object(
            auth_store, "write_persistent_session", side_effect=OSError("write failed")
        ):
            with self.assertRaises(OSError):
                auth_store.store_session("new-session-value", remember=True)
        with patch.object(auth_store, "read_persistent_session", return_value=""):
            self.assertEqual(auth_store.read_session(), "")

    def test_delete_failure_cannot_resurrect_old_persistent_session_in_process(self):
        with patch.object(auth_store, "write_persistent_session"):
            auth_store.store_session("old-session-value", remember=True)
        with patch.object(
            auth_store, "delete_persistent_session", side_effect=OSError("delete failed")
        ):
            with self.assertRaises(OSError):
                auth_store.delete_session()
        with patch.object(
            auth_store, "read_persistent_session", return_value="old-session-value"
        ) as persistent:
            self.assertEqual(auth_store.read_session(), "")
        persistent.assert_not_called()

    def test_read_session_caches_the_first_credential_manager_read(self):
        auth_store._PERSISTENT_SESSION_CACHE = auth_store._SESSION_CACHE_UNSET
        with patch.object(
            auth_store, "read_persistent_session", return_value="cached-session"
        ) as persistent:
            self.assertEqual(auth_store.read_session(), "cached-session")
            self.assertEqual(auth_store.read_session(), "cached-session")
        persistent.assert_called_once_with()

    def test_credential_delete_ignores_only_not_found(self):
        with patch.object(auth_store, "persistent_session_disabled", return_value=False), patch.object(
            auth_store.advapi32, "CredDeleteW", return_value=False,
        ), patch.object(auth_store.ctypes, "get_last_error", return_value=1168):
            auth_store.delete_persistent_session()
        with patch.object(auth_store, "persistent_session_disabled", return_value=False), patch.object(
            auth_store.advapi32, "CredDeleteW", return_value=False,
        ), patch.object(auth_store.ctypes, "get_last_error", return_value=5):
            with self.assertRaises(OSError):
                auth_store.delete_persistent_session()


if __name__ == "__main__":
    unittest.main()
