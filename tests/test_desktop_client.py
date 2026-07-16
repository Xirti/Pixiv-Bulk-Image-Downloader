from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from webview.util import create_cookie

import desktop_client


class DesktopClientTests(unittest.TestCase):
    def test_desktop_api_keeps_native_windows_and_factories_private(self):
        api = desktop_client.DesktopApi(window_factory=Mock())

        self.assertFalse(hasattr(api, "window"))
        self.assertFalse(hasattr(api, "window_factory"))
        self.assertTrue(hasattr(api, "_window"))
        self.assertTrue(hasattr(api, "_window_factory"))

    def test_webview_cookie_container_is_converted_without_losing_security_fields(self):
        cookie = create_cookie({
            "name": "PHPSESSID",
            "value": "abcdefgh",
            "domain": ".pixiv.net",
            "path": "/",
            "expires": "Wed, 01 Jan 2031 00:00:00 GMT",
            "secure": True,
            "httponly": True,
            "samesite": "lax",
        })
        rows = desktop_client.desktop_cookie_rows([cookie])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "PHPSESSID")
        self.assertEqual(rows[0]["value"], "abcdefgh")
        self.assertEqual(rows[0]["domain"], ".pixiv.net")
        self.assertEqual(rows[0]["path"], "/")
        self.assertTrue(rows[0]["secure"])
        self.assertTrue(rows[0]["httpOnly"])
        self.assertGreater(rows[0]["expires"], 1_900_000_000)

    def test_webview_session_cookie_minimum_date_is_normalized_to_session_expiry(self):
        cookie = create_cookie({
            "name": "PHPSESSID",
            "value": "abcdefgh",
            "domain": ".pixiv.net",
            "path": "/",
            "expires": "Mon, 01 Jan 0001 00:00:00 GMT",
            "secure": True,
            "httponly": True,
            "samesite": "lax",
        })

        rows = desktop_client.desktop_cookie_rows([cookie])

        self.assertEqual(rows[0]["expires"], 0)
        self.assertEqual(
            desktop_client.select_session_cookie(rows, now=1_800_000_000),
            "abcdefgh",
        )

    def test_desktop_login_uses_second_webview_window_and_stores_eligible_home_session(self):
        cookie = create_cookie({
            "name": "PHPSESSID",
            "value": "abcdefgh",
            "domain": ".pixiv.net",
            "path": "/",
            "expires": "Wed, 01 Jan 2031 00:00:00 GMT",
            "secure": True,
            "httponly": True,
            "samesite": "lax",
        })
        login = Mock()
        login.events.closed.is_set.return_value = False
        login.events.loaded.is_set.return_value = True
        login.get_current_url.return_value = "https://www.pixiv.net/"
        login.get_cookies.return_value = [cookie]
        window_factory = Mock(return_value=login)
        api = desktop_client.DesktopApi(window_factory=window_factory, poll_interval=0)

        with patch.object(desktop_client, "store_session") as store, patch.object(
            desktop_client, "mark_authorized_session"
        ) as mark:
            result = api.pixiv_login(remember=True)

        self.assertTrue(result["ok"])
        window_factory.assert_called_once()
        self.assertTrue(window_factory.call_args.kwargs["url"].startswith("https://www.pixiv.net/login.php"))
        store.assert_called_once_with("abcdefgh", remember=True)
        mark.assert_called_once_with()
        login.destroy.assert_called_once_with()

    def test_desktop_accepts_pixiv_home_and_eligible_cookie_without_self_endpoint(self):
        cookie = create_cookie({
            "name": "PHPSESSID", "value": "abcdefgh", "domain": ".pixiv.net", "path": "/",
            "expires": "Wed, 01 Jan 2031 00:00:00 GMT", "secure": True, "httponly": True,
            "samesite": "lax",
        })
        login = Mock()
        login.events.closed.is_set.return_value = False
        login.events.loaded.is_set.return_value = True
        login.get_current_url.return_value = "https://www.pixiv.net/"
        login.get_cookies.return_value = [cookie]
        api = desktop_client.DesktopApi(window_factory=Mock(return_value=login), poll_interval=0)

        with patch.object(desktop_client, "store_session") as store, patch.object(
            desktop_client, "mark_authorized_session"
        ) as mark:
            result = api.pixiv_login(remember=True)

        self.assertTrue(result["ok"])
        store.assert_called_once_with("abcdefgh", remember=True)
        mark.assert_called_once_with()

    def test_desktop_does_not_accept_cookie_while_still_on_pixiv_login_page(self):
        cookie = create_cookie({
            "name": "PHPSESSID", "value": "abcdefgh", "domain": ".pixiv.net", "path": "/",
            "expires": "Wed, 01 Jan 2031 00:00:00 GMT", "secure": True, "httponly": True,
            "samesite": "lax",
        })
        login = Mock()
        login.events.closed.is_set.side_effect = [False, True, True]
        login.events.loaded.is_set.return_value = True
        login.get_current_url.return_value = "https://accounts.pixiv.net/login"
        login.get_cookies.return_value = [cookie]
        api = desktop_client.DesktopApi(window_factory=Mock(return_value=login), poll_interval=0)

        with patch.object(desktop_client, "store_session") as store:
            result = api.pixiv_login(remember=False)

        self.assertFalse(result["ok"])
        store.assert_not_called()

    def test_start_desktop_can_run_internal_probe_callback_without_enabling_it_by_default(self):
        main = Mock()
        callback = Mock()
        with patch.object(desktop_client.webview, "create_window", return_value=main), patch.object(
            desktop_client.webview, "start"
        ) as start:
            desktop_client.start_desktop("http://127.0.0.1:45678/", Path("C:/tmp/moku-profile"), startup=callback)
        start.assert_called_once()
        self.assertIs(start.call_args.args[0], callback)
        self.assertEqual(start.call_args.kwargs["args"], [main])

    def test_desktop_does_not_let_second_webview_controller_clear_shared_login_cookies(self):
        main = Mock()
        with patch.object(desktop_client.webview, "create_window", return_value=main), patch.object(
            desktop_client.webview, "start"
        ) as start:
            desktop_client.start_desktop(
                "http://127.0.0.1:45678/", Path("C:/tmp/moku-profile")
            )

        self.assertFalse(start.call_args.kwargs["private_mode"])

    def test_desktop_login_clears_shared_webview_cookies_before_and_after_closed_child(self):
        main = Mock()
        login = Mock()
        login.events.closed.is_set.return_value = True
        api = desktop_client.DesktopApi(window_factory=Mock(return_value=login), poll_interval=0)
        api._window = main

        result = api.pixiv_login(remember=False)

        self.assertFalse(result["ok"])
        self.assertIn("取消", result["error"])
        self.assertEqual(main.clear_cookies.call_count, 2)
        login.clear_cookies.assert_not_called()

    def test_desktop_login_cancel_does_not_touch_closed_webview(self):
        login = Mock()
        login.events.closed.is_set.return_value = True
        api = desktop_client.DesktopApi(window_factory=Mock(return_value=login), poll_interval=0)

        result = api.pixiv_login(remember=False)

        self.assertFalse(result["ok"])
        self.assertIn("取消", result["error"])
        login.get_cookies.assert_not_called()
        login.clear_cookies.assert_not_called()
        login.destroy.assert_not_called()

    def test_desktop_login_does_not_read_cookies_before_page_is_loaded(self):
        login = Mock()
        login.events.closed.is_set.side_effect = [False, True, True]
        login.events.loaded.is_set.return_value = False
        login.get_cookies.return_value = []
        api = desktop_client.DesktopApi(
            window_factory=Mock(return_value=login), poll_interval=0
        )

        result = api.pixiv_login(remember=False)

        self.assertFalse(result["ok"])
        self.assertIn("取消", result["error"])
        login.get_cookies.assert_not_called()

    def test_desktop_login_does_not_accept_eligible_cookie_on_non_pixiv_page(self):
        cookie = create_cookie({
            "name": "PHPSESSID", "value": "abcdefgh", "domain": ".pixiv.net", "path": "/",
            "expires": "", "secure": True, "httponly": True, "samesite": "lax",
        })
        login = Mock()
        login.events.closed.is_set.side_effect = [False, True, True]
        login.events.loaded.is_set.return_value = True
        login.get_current_url.return_value = "https://example.com/"
        login.get_cookies.return_value = [cookie]
        api = desktop_client.DesktopApi(window_factory=Mock(return_value=login), poll_interval=0)

        with patch.object(desktop_client, "store_session") as store:
            result = api.pixiv_login(remember=False)

        self.assertFalse(result["ok"])
        store.assert_not_called()

    def test_desktop_login_rejects_malformed_cookie_even_on_pixiv_home(self):
        cookie = create_cookie({
            "name": "PHPSESSID", "value": "bad value", "domain": ".pixiv.net", "path": "/",
            "expires": "Wed, 01 Jan 2031 00:00:00 GMT", "secure": True, "httponly": True,
            "samesite": "lax",
        })
        login = Mock()
        login.events.closed.is_set.side_effect = [False, True, True]
        login.events.loaded.is_set.return_value = True
        login.get_current_url.return_value = "https://www.pixiv.net/"
        login.get_cookies.return_value = [cookie]
        api = desktop_client.DesktopApi(window_factory=Mock(return_value=login), poll_interval=0)
        with patch.object(desktop_client, "store_session") as store:
            result = api.pixiv_login(remember=False)
        self.assertFalse(result["ok"])
        store.assert_not_called()
        login.destroy.assert_not_called()


if __name__ == "__main__":
    unittest.main()