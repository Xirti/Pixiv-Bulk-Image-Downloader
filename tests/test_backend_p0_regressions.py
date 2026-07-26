from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import unittest
from http.cookies import SimpleCookie
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor

import auth_store
import desktop_client
import server


class DesktopAuthIpcTests(unittest.TestCase):
    def setUp(self):
        auth_store.clear_memory_session()
        server.clear_authorized_state()
        self.previous_capability = getattr(server, "DESKTOP_AUTH_TOKEN", "")
        server.DESKTOP_AUTH_TOKEN = "desktop-capability-0123456789abcdef"
        self.httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}/"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        server.DESKTOP_AUTH_TOKEN = self.previous_capability
        auth_store.clear_memory_session()
        server.clear_authorized_state()

    def post(self, path: str, payload: dict, capability: str):
        request = urllib.request.Request(
            self.base + path.lstrip("/"),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-MOKU-Desktop-Capability": capability,
            },
            method="POST",
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=3,
            ) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def browser_request(self, method: str, path: str, payload: dict | None = None):
        request = urllib.request.Request(
            self.base + path.lstrip("/"),
            data=(json.dumps(payload).encode("utf-8") if payload is not None else None),
            headers={
                "Content-Type": "application/json",
                "X-MOKU-Request-Token": server.REQUEST_TOKEN,
            },
            method=method,
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=3,
            ) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_desktop_session_rejects_web_request_token_as_capability(self):
        status, body = self.post(
            "/api/desktop/auth/session",
            {"session": "desktop-session-123", "remember": False},
            server.REQUEST_TOKEN,
        )
        self.assertEqual(status, 403)
        self.assertIn("桌面", body["error"])

    def test_desktop_api_updates_the_actual_http_backend_without_echoing_cookie(self):
        cookie = SimpleCookie()
        cookie.load(
            "PHPSESSID=desktop-session-123; Domain=.pixiv.net; Path=/; "
            "Secure; HttpOnly"
        )
        login = Mock()
        login.events.closed.is_set.return_value = False
        login.events.loaded.is_set.return_value = True
        login.get_current_url.return_value = "https://www.pixiv.net/"
        login.get_cookies.return_value = [cookie]
        api = desktop_client.DesktopApi(
            window_factory=Mock(return_value=login),
            poll_interval=0,
            backend_url=self.base,
            desktop_auth_token=server.DESKTOP_AUTH_TOKEN,
        )

        with patch.dict(
            auth_store.os.environ,
            {"MOKU_DISABLE_PERSISTENT_SESSION": "1"},
            clear=False,
        ):
            result = api.pixiv_login(remember=False)
            self.assertEqual(result, {"ok": True, "remembered": False})
            self.assertNotIn("desktop-session-123", json.dumps(result))
            self.assertTrue(server.auth_status_snapshot()["loggedIn"])

            logged_out = api.pixiv_logout()
            self.assertTrue(logged_out["ok"])
            self.assertFalse(server.auth_status_snapshot()["loggedIn"])

    def test_health_never_discloses_desktop_capability(self):
        request = urllib.request.Request(
            self.base + "api/health",
            headers={"Sec-Fetch-Site": "same-origin"},
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            request, timeout=3,
        ) as response:
            body = json.loads(response.read())
        self.assertNotIn("desktopAuthToken", body)
        self.assertNotIn(server.DESKTOP_AUTH_TOKEN, json.dumps(body))

    def test_r18_image_validates_epoch_outside_pixiv_state_lock(self):
        token = "restricted-image-token"
        server.mark_authorized_session()
        epoch = server.authorization_generation()
        server.IMAGE_TOKENS[token] = (
            9999999999.0, "77", "https://i.pximg.net/r.jpg", "r18", epoch,
        )

        def validate_without_lock_inversion(_epoch):
            self.assertFalse(server.PIXIV_STATE_LOCK._is_owned())
            raise server.AuthorizationRevokedError("revoked")

        request = urllib.request.Request(
            self.base + f"api/pixiv/image?token={token}",
        )
        with patch.object(
            server,
            "assert_authorization_generation",
            side_effect=validate_without_lock_inversion,
        ):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    request, timeout=3,
                )
        self.assertEqual(raised.exception.code, 403)

    def test_download_endpoints_fail_fast_when_the_shared_task_limit_is_full(self):
        limiter = threading.BoundedSemaphore(1)
        self.assertTrue(limiter.acquire(blocking=False))
        try:
            with patch.object(server, "DOWNLOAD_TASK_SLOTS", limiter), patch.object(
                server,
                "ensure_network_opener_current",
                side_effect=AssertionError("a saturated request must not start network work"),
            ):
                for path in ("/api/pixiv/download", "/api/pixiv/batch-download"):
                    status, body = self.browser_request("POST", path, {})
                    self.assertEqual(status, 429, (path, body))
                    self.assertTrue(body["retryable"])
        finally:
            limiter.release()

    def test_download_task_slot_is_released_when_the_handler_raises(self):
        limiter = threading.BoundedSemaphore(1)
        handler = object.__new__(server.Handler)
        handler.path = "/api/pixiv/download"
        with patch.object(server, "DOWNLOAD_TASK_SLOTS", limiter), patch.object(
            server, "validate_mutating_request", return_value=None
        ), patch.object(
            server.Handler, "read_json_object", return_value={}
        ), patch.object(
            server, "ensure_network_opener_current"
        ), patch.object(
            server.Handler,
            "_post_pixiv_download",
            side_effect=RuntimeError("injected handler failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected handler failure"):
                server.Handler.do_POST(handler)

        self.assertTrue(limiter.acquire(blocking=False))
        limiter.release()

    def test_single_download_rejects_an_unbounded_artwork_before_staging(self):
        oversized = {
            "id": "77", "restriction": "safe",
            "pageImages": [
                {"regular": "/unused", "original": "/unused"}
                for _ in range(server.DOWNLOAD_CHUNK_PAGES + 1)
            ],
        }
        with tempfile.TemporaryDirectory() as raw_root, patch.object(
            server, "ensure_network_opener_current",
        ), patch.object(
            server, "validated_authorization", return_value=(False, None),
        ), patch.object(
            server, "pixiv_item_for_download", return_value=oversized,
        ), patch.object(
            server, "_stage_and_publish_download",
        ) as stage_and_publish:
            status, body = self.browser_request("POST", "/api/pixiv/download", {
                "id": "77", "saveRoot": raw_root,
                "quality": "regular", "createFolder": False,
            })

        self.assertEqual(status, 502, body)
        self.assertIn(
            f"最多处理 {server.DOWNLOAD_CHUNK_PAGES} 张图片",
            body["error"],
        )
        stage_and_publish.assert_not_called()

    def test_revoked_search_and_downloads_return_forbidden_not_gateway_error(self):
        revoked = server.AuthorizationRevokedError("authorization revoked")
        with patch.object(server, "ensure_network_opener_current"), patch.object(
            server, "validated_authorization", return_value=(True, 7)
        ), patch.object(
            server, "search_pixiv_results", side_effect=revoked
        ):
            status, body = self.browser_request(
                "GET", "/api/pixiv/search?tag=test&mode=all"
            )
        self.assertEqual(status, 403, body)

        def invoke_stage(
            _save_root, *, prefix, stage, authorization_epochs=None,
        ):
            stage(None, [])
            return [], False

        with tempfile.TemporaryDirectory() as raw_root, patch.object(
            server, "ensure_network_opener_current"
        ), patch.object(
            server, "validated_authorization", return_value=(True, 7)
        ), patch.object(
            server, "pixiv_item_for_download", side_effect=revoked
        ), patch.object(
            server, "_stage_and_publish_download", side_effect=invoke_stage
        ):
            single_status, single_body = self.browser_request("POST", "/api/pixiv/download", {
                "id": "77", "saveRoot": raw_root,
                "quality": "regular", "createFolder": False,
            })
            batch_status, batch_body = self.browser_request("POST", "/api/pixiv/batch-download", {
                "groups": [{"id": "77", "pages": [0]}],
                "saveRoot": raw_root, "quality": "regular",
                "createFolder": False,
            })
        self.assertEqual(single_status, 403, single_body)
        self.assertEqual(batch_status, 403, batch_body)


class RestrictedDetailRevocationTests(unittest.TestCase):
    def setUp(self):
        server.reset_search_caches()
        server.PIXIV_CACHE.clear()
        server.IMAGE_TOKENS.clear()

    def tearDown(self):
        server.reset_search_caches()
        server.PIXIV_CACHE.clear()
        server.IMAGE_TOKENS.clear()

    @staticmethod
    def detail() -> dict:
        return {
            "id": "77", "illustId": "77", "title": "restricted",
            "userName": "artist", "userId": "9", "tags": {"tags": []},
            "xRestrict": 1, "isUnlisted": False, "isLoginOnly": False,
            "isMasked": False, "visibilityScope": 0, "pageCount": 1,
        }

    @staticmethod
    def pages() -> list[dict]:
        return [{
            "width": 1, "height": 1,
            "urls": {
                "regular": "https://i.pximg.net/r.jpg",
                "original": "https://i.pximg.net/o.jpg",
            },
        }]

    def test_every_r18_image_response_is_no_store(self):
        self.assertEqual(
            server.image_token_cache_control(
                (9999999999.0, "77", "https://i.pximg.net/r.jpg", "r18")
            ),
            "no-store",
        )

    def test_logout_during_detail_prevents_token_cache_and_response_commit(self):
        epoch = server.authorization_generation()
        calls = 0

        def detail_then_logout(_url):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"body": self.detail()}
            server.clear_authorized_state()
            return {"body": self.pages()}

        with patch.object(server, "pixiv_json", side_effect=detail_then_logout):
            with self.assertRaises(server.AuthorizationRevokedError):
                server.pixiv_detail(
                    "77", allow_r18=True, authorization_epoch=epoch,
                )

        self.assertNotIn("77", server.PIXIV_CACHE)
        self.assertFalse(any(row[3] == "r18" for row in server.IMAGE_TOKENS.values()))

    def test_restricted_image_token_is_bound_to_the_original_account_epoch(self):
        server.mark_authorized_session()
        epoch = server.authorization_generation()
        proxy = server.authorize_image_proxy(
            "/api/pixiv/image?url=https%3A%2F%2Fi.pximg.net%2Fr.jpg",
            "77",
            "r18",
            authorization_epoch=epoch,
        )
        token = urllib.parse.parse_qs(
            urllib.parse.urlsplit(proxy).query
        )["token"][0]
        approved = tuple(server.IMAGE_TOKENS[token])

        server.clear_authorized_state()
        server.mark_authorized_session()

        with self.assertRaises(server.AuthorizationRevokedError):
            server.assert_image_token_authorization(approved)

    def test_revocation_after_staging_cleans_files_before_publish(self):
        class FakeStaging:
            cleanup_pending = False

            def __enter__(self):
                return None

            def __exit__(self, *_args):
                return False

        server.mark_authorized_session()
        epoch = server.authorization_generation()
        server.clear_authorized_state()
        server.mark_authorized_session()
        staged_entry = object()

        def stage(_root, staged):
            staged.append(staged_entry)

        with patch.object(
            server, "secure_staging_directory", return_value=FakeStaging()
        ), patch.object(
            server, "_discard_owned_staging", return_value=0
        ) as discard, patch.object(
            server,
            "publish_staged_files",
            side_effect=AssertionError("revoked bytes must not publish"),
        ) as publish:
            with self.assertRaises(server.AuthorizationRevokedError):
                server._stage_and_publish_download(
                    server.DOWNLOADS,
                    prefix=".test-",
                    stage=stage,
                    authorization_epochs={epoch},
                )

        discard.assert_called_once_with([staged_entry])
        publish.assert_not_called()


class AuthorCursorCommitTests(unittest.TestCase):
    def setUp(self):
        server.reset_search_caches()

    def tearDown(self):
        server.reset_search_caches()

    def test_failed_profile_batch_does_not_advance_cursor(self):
        ids = [str(5000 - index) for index in range(100)]
        with patch.object(server, "load_user_profile_ids", return_value=ids), patch.object(
            server, "load_user_profile_works", side_effect=OSError("transient")
        ):
            with self.assertRaises(OSError):
                server.search_pixiv_results(
                    "uid:42", "safe", 1, "all", True, authorized=False,
                )

        key = ("user", "uid", "42", "42", "safe", "all", True, False)
        self.assertEqual(server.SEARCH_SESSIONS[key]["profileOffset"], 0)

    def test_malformed_profile_row_does_not_advance_cursor(self):
        ids = [str(5000 - index) for index in range(100)]
        with patch.object(server, "load_user_profile_ids", return_value=ids), patch.object(
            server, "load_user_profile_works", return_value=[{
                "userId": "42", "xRestrict": "not-a-number",
            }]
        ):
            with self.assertRaises(ValueError):
                server.search_pixiv_results(
                    "uid:42", "safe", 1, "all", True, authorized=False,
                )

        key = ("user", "uid", "42", "42", "safe", "all", True, False)
        self.assertEqual(server.SEARCH_SESSIONS[key]["profileOffset"], 0)

    def test_author_resolution_is_reused_across_pages_and_cache_is_bounded(self):
        def response(user_id: str, name: str) -> dict:
            return {"body": {
                "page": {"userIds": [user_id]},
                "users": [{"userId": user_id, "name": name}],
            }}

        with patch.object(
            server, "pixiv_json", return_value=response("42", "目标画师")
        ) as request:
            self.assertEqual(server.resolve_author_user("目标画师"), ("42", "目标画师"))
            self.assertEqual(server.resolve_author_user("  目标画师  "), ("42", "目标画师"))
        request.assert_called_once()

        old_limit = server.MAX_AUTHOR_RESOLUTION_CACHE
        try:
            server.MAX_AUTHOR_RESOLUTION_CACHE = 2
            server.reset_search_caches()
            with patch.object(server, "pixiv_json", side_effect=[
                response("1", "甲"), response("2", "乙"), response("3", "丙"),
            ]):
                for user_id, name in (("1", "甲"), ("2", "乙"), ("3", "丙")):
                    self.assertEqual(server.resolve_author_user(name), (user_id, name))
            self.assertEqual(len(server.AUTHOR_RESOLUTION_CACHE), 2)
            self.assertNotIn("甲".casefold(), server.AUTHOR_RESOLUTION_CACHE)
        finally:
            server.MAX_AUTHOR_RESOLUTION_CACHE = old_limit
            server.reset_search_caches()


class BoundedAuthorizationStateTests(unittest.TestCase):
    def setUp(self):
        server.clear_authorized_state()
        server.IMAGE_TOKENS.clear()

    def tearDown(self):
        server.IMAGE_TOKENS.clear()
        server.clear_authorized_state()

    def test_repeated_unauthenticated_checks_revoke_only_once(self):
        server.mark_authorized_session()
        generation = server.authorization_generation()
        with patch.object(server, "session_cookie_header", return_value={}):
            self.assertFalse(server.validated_session())
            for _ in range(9):
                self.assertFalse(server.validated_session())
        self.assertEqual(server.authorization_generation(), generation + 1)

    def test_session_and_epoch_snapshot_cannot_straddle_logout(self):
        cookie_read = threading.Event()
        release_cookie = threading.Event()
        logout_started = threading.Event()
        result = []

        def blocking_cookie():
            cookie_read.set()
            release_cookie.wait(timeout=3)
            return {"Cookie": "PHPSESSID=valid-session"}

        def capture_snapshot():
            result.append(server.validated_authorization())

        def logout():
            logout_started.set()
            server.clear_authorized_state(force=True)

        with patch.object(server, "session_cookie_header", side_effect=blocking_cookie):
            snapshot_thread = threading.Thread(target=capture_snapshot)
            snapshot_thread.start()
            self.assertTrue(cookie_read.wait(timeout=2))
            logout_thread = threading.Thread(target=logout)
            logout_thread.start()
            self.assertTrue(logout_started.wait(timeout=2))
            logout_thread.join(timeout=0.05)
            self.assertTrue(logout_thread.is_alive())
            release_cookie.set()
            snapshot_thread.join(timeout=2)
            logout_thread.join(timeout=2)

        self.assertEqual(len(result), 1)
        authorized, epoch = result[0]
        self.assertTrue(authorized)
        with self.assertRaises(server.AuthorizationRevokedError):
            server.assert_authorization_generation(epoch)

    def test_failed_account_switch_cannot_reactivate_the_previous_session(self):
        with patch.dict(
            auth_store.os.environ,
            {"MOKU_DISABLE_PERSISTENT_SESSION": "1"},
            clear=False,
        ):
            auth_store.store_session("previous-session", remember=False)
            server.mark_authorized_session()
            with patch.object(server, "store_session", side_effect=OSError("write failed")):
                with self.assertRaises(OSError):
                    server.connect_authorized_session(
                        "replacement-session", remember=True,
                    )
            self.assertFalse(auth_store.session_cookie_header())
            self.assertFalse(server.validated_authorization()[0])

    def test_login_cannot_commit_between_logout_delete_and_revocation(self):
        deleted = threading.Event()
        release_logout = threading.Event()
        completions = []
        real_delete = server.delete_session
        logout_thread = None

        def controlled_delete():
            real_delete()
            if threading.current_thread() is logout_thread:
                deleted.set()
                release_logout.wait(timeout=3)

        def logout():
            server.disconnect_authorized_session()
            completions.append("logout")

        def login():
            server.connect_authorized_session(
                "account-b-session", remember=False,
            )
            completions.append("login")

        with patch.dict(
            auth_store.os.environ,
            {"MOKU_DISABLE_PERSISTENT_SESSION": "1"},
            clear=False,
        ), patch.object(server, "delete_session", side_effect=controlled_delete):
            auth_store.store_session("account-a-session", remember=False)
            server.mark_authorized_session()
            logout_thread = threading.Thread(target=logout)
            login_thread = threading.Thread(target=login)
            logout_thread.start()
            self.assertTrue(deleted.wait(timeout=2))
            login_thread.start()
            login_thread.join(timeout=0.05)
            self.assertTrue(
                login_thread.is_alive(),
                "login committed inside logout's delete/revoke transaction",
            )
            release_logout.set()
            logout_thread.join(timeout=2)
            login_thread.join(timeout=2)

            self.assertFalse(logout_thread.is_alive())
            self.assertFalse(login_thread.is_alive())
            self.assertEqual(completions, ["logout", "login"])
            self.assertEqual(auth_store.read_session(), "account-b-session")
            self.assertTrue(server.validated_authorization()[0])
            auth_store.delete_session()

    def test_parallel_token_insertions_never_exceed_limit(self):
        old_limit = server.MAX_IMAGE_TOKENS
        barrier = threading.Barrier(8)
        counter = 0
        counter_lock = threading.Lock()

        def synchronized_token(_bytes):
            nonlocal counter
            with counter_lock:
                counter += 1
                value = counter
            barrier.wait(timeout=3)
            return f"token-{value}"

        proxy = "/api/pixiv/image?url=https%3A%2F%2Fi.pximg.net%2Fa.jpg"
        try:
            server.MAX_IMAGE_TOKENS = 1
            with patch.object(
                server.secrets, "token_urlsafe", side_effect=synchronized_token,
            ):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(
                        lambda value: server.authorize_image_proxy(
                            proxy, str(value), "safe",
                        ),
                        range(8),
                    ))
            self.assertLessEqual(len(server.IMAGE_TOKENS), 1)
        finally:
            server.MAX_IMAGE_TOKENS = old_limit


if __name__ == "__main__":
    unittest.main()
