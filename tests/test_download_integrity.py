from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import tempfile
import unittest
import urllib.error
import urllib.request
import ctypes
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

import server


PNG = b"\x89PNG\r\n\x1a\nMOKU-TEST"


def create_junction(junction: Path, target: Path) -> bool:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def attempt_directory_rename(source: Path, destination: Path) -> subprocess.CompletedProcess:
    command = (
        "import os,sys\n"
        "try:\n"
        " os.rename(sys.argv[1],sys.argv[2])\n"
        " print('renamed')\n"
        "except OSError as exc:\n"
        " print(f'blocked:{exc.winerror}')\n"
        " raise SystemExit(exc.winerror or 1)\n"
    )
    return subprocess.run(
        [
            os.fspath(Path(sys.executable)),
            "-B",
            "-c",
            command,
            os.fspath(source),
            os.fspath(destination),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )


def attempt_file_write(path: Path, payload: bytes) -> subprocess.CompletedProcess:
    command = (
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_bytes(sys.argv[2].encode())\n"
    )
    return subprocess.run(
        [
            os.fspath(Path(sys.executable)), "-B", "-c", command,
            os.fspath(path), payload.decode(),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )


@unittest.skipUnless(
    server.WINDOWS_SECURE_PUBLICATION,
    "download publication requires Windows secure filesystem primitives",
)
class BatchDownloadIntegrityTests(unittest.TestCase):
    def test_file_rename_info_uses_full_windows_abi_buffer(self):
        captured: list[tuple[int, int, int]] = []

        class FakeSetFileInformation:
            argtypes = None
            restype = None

            def __call__(self, handle, info_class, _buffer, size):
                captured.append((handle, info_class, size))
                return 1

        destination = Path(r"C:\temp\目标.png")
        encoded_size = len(str(destination).encode("utf-16-le"))
        with patch.object(
            server.ctypes.windll.kernel32,
            "SetFileInformationByHandle",
            FakeSetFileInformation(),
        ):
            server._rename_file_by_handle(123, destination)

        replace_if_exists_type = server.FILE_RENAME_INFO._fields_[0][1]
        self.assertEqual(
            ctypes.sizeof(replace_if_exists_type),
            ctypes.sizeof(wintypes.BOOL),
        )
        self.assertEqual(
            captured,
            [(123, server.FILE_RENAME_INFO_CLASS,
              ctypes.sizeof(server.FILE_RENAME_INFO) + encoded_size)],
        )

    def test_staged_file_is_write_locked_before_snapshot_until_publish(self):
        with tempfile.TemporaryDirectory(prefix="moku-staged-owner-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.part"
            destination = root / "payload.png"
            temporary.write_bytes(b"verified-bytes")
            real_publish = server._publish_owned_staged_file

            def assert_source_is_owned(ownership, *args, **kwargs):
                attack = attempt_file_write(temporary, b"tampered")
                self.assertNotEqual(attack.returncode, 0, attack.stdout + attack.stderr)
                self.assertEqual(
                    server._snapshot_windows_file_handle(ownership.handle),
                    ownership.snapshot,
                )
                return real_publish(ownership, *args, **kwargs)

            with patch.object(
                server, "_publish_owned_staged_file", side_effect=assert_source_is_owned,
            ):
                saved = server.publish_staged_files(
                    staging, [(temporary, destination)], save_root=root,
                )

            self.assertEqual(saved, [destination])
            self.assertEqual(destination.read_bytes(), b"verified-bytes")

    def test_publish_failure_owns_and_closes_the_entire_batch(self):
        with tempfile.TemporaryDirectory(prefix="moku-batch-owner-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            first = server._create_owned_staged_file(staging, root / "first.png", b"first")
            second = server._create_owned_staged_file(staging, root / "second.png", b"second")

            with patch.object(
                server, "_publish_owned_staged_file", side_effect=OSError("injected publish failure"),
            ):
                with self.assertRaises(OSError):
                    server.publish_staged_files(
                        staging, [first, second], save_root=root,
                    )

            self.assertIsNone(first.handle)
            self.assertIsNone(first.staged_handle)
            self.assertIsNone(second.handle)
            self.assertIsNone(second.staged_handle)
            self.assertEqual(list(staging.iterdir()), [])

    def test_http_publish_failure_has_one_cleanup_owner(self):
        with tempfile.TemporaryDirectory(prefix="moku-http-single-owner-") as raw_root:
            root = Path(raw_root).resolve()

            with patch.object(
                server, "_publish_owned_staged_file", side_effect=OSError("injected publish failure"),
            ), patch.object(
                server, "_discard_owned_staging", side_effect=AssertionError("HTTP must not double-clean after publish takes ownership"),
            ):
                status, body = self.post_single(root)

            self.assertEqual(status, 502, body)
            self.assertIn("下载失败", body["error"])
            self.assertEqual(list(root.glob(".moku-single-*")), [])

    def test_post_publish_failure_rolls_back_current_owned_entry(self):
        with tempfile.TemporaryDirectory(prefix="moku-owned-post-publish-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            first_staged = staging / "first.part"
            second_staged = staging / "second.part"
            first_staged.write_bytes(b"first-new")
            second_staged.write_bytes(b"second-new")
            first = root / "first.png"
            second = root / "second.png"
            first.write_bytes(b"first-old")
            real_publish = server._publish_owned_staged_file

            def fail_after_second_publish(ownership, final, *args, **kwargs):
                result = real_publish(ownership, final, *args, **kwargs)
                if Path(final) == second:
                    raise OSError("injected post-publish failure")
                return result

            with patch.object(
                server, "_publish_owned_staged_file", side_effect=fail_after_second_publish,
            ):
                with self.assertRaises(OSError):
                    server.publish_staged_files(
                        staging,
                        [(first_staged, first), (second_staged, second)],
                        save_root=root,
                    )

            self.assertEqual(first.read_bytes(), b"first-old")
            self.assertFalse(second.exists())

    def setUp(self):
        self.httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.token = "batch-test-token"
        self.artwork_id = "990001"
        self.remote_urls = [
            f"https://i.pximg.net/test/{self.artwork_id}_p0.png",
            f"https://i.pximg.net/test/{self.artwork_id}_p1.png",
        ]
        self._old_cache = dict(server.PIXIV_CACHE)
        self._old_tokens = dict(server.IMAGE_TOKENS)
        server.PIXIV_CACHE.clear()
        server.IMAGE_TOKENS.clear()
        server.PIXIV_CACHE[self.artwork_id] = {
            "id": self.artwork_id,
            "restriction": "safe",
            "source": "pixiv",
            "title": "批量完整性测试",
            "pageImages": [
                {
                    "regular": f"/api/pixiv/image?token={self.token}-{index}",
                    "original": f"/api/pixiv/image?token={self.token}-{index}",
                }
                for index in range(2)
            ],
        }
        for index, remote in enumerate(self.remote_urls):
            server.IMAGE_TOKENS[f"{self.token}-{index}"] = (
                9_999_999_999.0,
                self.artwork_id,
                remote,
                "safe",
            )

    def tearDown(self):
        server.PIXIV_CACHE.clear()
        server.PIXIV_CACHE.update(self._old_cache)
        server.IMAGE_TOKENS.clear()
        server.IMAGE_TOKENS.update(self._old_tokens)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def _request(self, request: urllib.request.Request, *, pixiv_side_effect=None) -> tuple[int, dict]:
        """Install the network seam before the threaded handler begins."""
        if pixiv_side_effect is None:
            pixiv_side_effect = lambda *args, **kwargs: (PNG, "image/png")
        with patch.object(server, "ensure_network_opener_current", return_value=server.PIXIV_PROXY), \
             patch.object(server, "pixiv_request", side_effect=pixiv_side_effect):
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read())
                    return response.status, payload
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

    def post(self, payload: dict, root: Path, *, pixiv_side_effect=None) -> tuple[int, dict]:
        body = json.dumps({**payload, "saveRoot": str(root)}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.httpd.server_port}/api/pixiv/batch-download",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Host": f"127.0.0.1:{self.httpd.server_port}",
                "Origin": f"http://127.0.0.1:{self.httpd.server_port}",
                "X-MOKU-Request-Token": server.REQUEST_TOKEN,
            },
            method="POST",
        )
        return self._request(request, pixiv_side_effect=pixiv_side_effect)

    def post_single(
        self, root: Path, create_folder: bool = False, context: dict | None = None,
        *, pixiv_side_effect=None,
    ) -> tuple[int, dict]:
        body = json.dumps({
            "id": self.artwork_id,
            "quality": "regular",
            "saveRoot": str(root),
            "createFolder": create_folder,
            **({"context": context} if context is not None else {}),
        }).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.httpd.server_port}/api/pixiv/download",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.httpd.server_port}",
                "X-MOKU-Request-Token": server.REQUEST_TOKEN,
            },
            method="POST",
        )
        return self._request(request, pixiv_side_effect=pixiv_side_effect)

    def test_batch_download_honors_create_folder_false(self):
        with tempfile.TemporaryDirectory(prefix="moku-batch-folder-test-") as raw_root:
            root = Path(raw_root)
            status, body = self.post(
                {
                    "groups": [{"id": self.artwork_id, "pages": [0]}],
                    "quality": "regular",
                    "createFolder": False,
                },
                root,
            )
            self.assertEqual(status, 200, body)
            saved = root / Path(body["saved"][0])
            self.assertEqual(saved.parent, root)
            self.assertTrue(saved.exists())

    def test_single_response_paths_are_built_while_nested_parent_is_locked(self):
        with tempfile.TemporaryDirectory(prefix="moku-response-lock-parent-") as raw_parent:
            parent = Path(raw_parent).resolve()
            root = parent / "selected"
            root.mkdir()
            moved = root / "retargeted"
            attacks: list[subprocess.CompletedProcess] = []
            real_public_saved_files = server.public_saved_files

            def probe_nested_parent(save_root, saved):
                nested_parent = Path(saved[0]).parent
                attacks.append(attempt_directory_rename(nested_parent, moved))
                return real_public_saved_files(save_root, saved)

            with patch.object(
                server, "public_saved_files", side_effect=probe_nested_parent,
            ):
                status, body = self.post_single(
                    root,
                    create_folder=True,
                    context={"kind": "tags", "value": "response-lock"},
                )

            self.assertEqual(status, 200, body)
            self.assertEqual(len(attacks), 1)
            self.assertEqual(
                (attacks[0].returncode, attacks[0].stdout.strip()),
                (32, "blocked:32"),
                (attacks[0].stdout, attacks[0].stderr),
            )
            self.assertTrue((root / Path(body["saved"][0])).is_file())
            self.assertFalse(moved.exists())

    def test_http_staging_is_write_locked_from_creation_until_publish(self):
        with tempfile.TemporaryDirectory(prefix="moku-http-staging-owner-") as raw_root:
            root = Path(raw_root).resolve()
            real_publish = server.publish_staged_files
            attacks: list[subprocess.CompletedProcess] = []

            def attack_before_publish(staging_root, staged, **kwargs):
                self.assertTrue(staged)
                self.assertTrue(all(
                    isinstance(entry, server.PublishedFileOwnership)
                    for entry in staged
                ))
                temporary = next(Path(staging_root).glob(".page-*.part"))
                attacks.append(attempt_file_write(temporary, b"tampered"))
                return real_publish(staging_root, staged, **kwargs)

            with patch.object(
                server, "publish_staged_files", side_effect=attack_before_publish,
            ):
                status, body = self.post_single(root)

            self.assertEqual(status, 200, body)
            self.assertEqual(len(attacks), 1)
            self.assertNotEqual(
                attacks[0].returncode, 0,
                attacks[0].stdout + attacks[0].stderr,
            )
            saved = root / Path(body["saved"][0])
            self.assertEqual(saved.read_bytes(), PNG)

    def test_staging_write_failure_preserves_file_when_handle_delete_fails(self):
        with tempfile.TemporaryDirectory(prefix="moku-stage-write-fail-") as raw_root:
            root = Path(raw_root).resolve()
            real_write = server._write_windows_file_handle
            real_delete = server._delete_empty_directory_on_close
            created_handles: set[int] = set()

            def fail_write(handle, raw):
                created_handles.add(handle)
                raise OSError("injected staging write failure")

            def fail_created_handle_delete(handle):
                if handle in created_handles:
                    raise PermissionError("injected owned staging delete failure")
                return real_delete(handle)

            with patch.object(
                server, "_write_windows_file_handle", side_effect=fail_write,
            ), patch.object(
                server, "_delete_empty_directory_on_close", side_effect=fail_created_handle_delete,
            ):
                status, body = self.post_single(root)

            self.assertEqual(status, 502, body)
            self.assertIn("下载失败", body["error"])
            staging_dirs = list(root.glob(".moku-single-*"))
            self.assertEqual(len(staging_dirs), 1)
            self.assertEqual(len(list(staging_dirs[0].glob(".page-*.part"))), 1)
            # The failure path must close the handle even though it preserves
            # the recovery file. A child process can remove it after response.
            shutil.rmtree(staging_dirs[0])

    def test_committed_download_reports_cleanup_warning_without_false_failure(self):
        with tempfile.TemporaryDirectory(prefix="moku-cleanup-warning-test-") as raw_root:
            root = Path(raw_root).resolve()
            real_rmdir = Path.rmdir

            def fail_staging_rmdir(path):
                if Path(path).name.startswith(".moku-single-"):
                    raise PermissionError("injected committed cleanup failure")
                return real_rmdir(path)

            with patch.object(
                Path, "rmdir", autospec=True, side_effect=fail_staging_rmdir,
            ), self.assertLogs("moku.http", level="WARNING") as captured:
                status, body = self.post_single(root)

            self.assertEqual(status, 200, body)
            self.assertTrue(body["ok"])
            self.assertTrue(body["cleanupPending"])
            self.assertTrue((root / Path(body["saved"][0])).is_file())
            self.assertIn("临时目录清理待处理", "\n".join(captured.output))
            for staging in root.glob(".moku-single-*"):
                real_rmdir(staging)

    def test_batch_download_uses_one_context_folder_for_all_artworks(self):
        with tempfile.TemporaryDirectory(prefix="moku-batch-context-test-") as raw_root:
            root = Path(raw_root)
            status, body = self.post(
                {
                    "groups": [{"id": self.artwork_id, "pages": [0, 1]}],
                    "quality": "regular",
                    "createFolder": True,
                    "groupArtworks": False,
                    "context": {"kind": "tags", "value": "猫；夜景"},
                },
                root,
            )
            self.assertEqual(status, 200, body)
            saved = [root / Path(relative) for relative in body["saved"]]
            self.assertTrue(all(path.parent == root / "tag_猫；夜景" for path in saved))
            self.assertTrue(all(path.exists() for path in saved))

    def test_batch_download_can_group_each_artwork_inside_context_folder(self):
        with tempfile.TemporaryDirectory(prefix="moku-batch-group-test-") as raw_root:
            root = Path(raw_root)
            status, body = self.post(
                {
                    "groups": [{"id": self.artwork_id, "pages": [0]}],
                    "quality": "regular",
                    "createFolder": True,
                    "groupArtworks": True,
                    "context": {"kind": "tags", "value": "猫；夜景"},
                },
                root,
            )
            self.assertEqual(status, 200, body)
            saved = root / Path(body["saved"][0])
            self.assertEqual(saved.parent.parent, root / "tag_猫；夜景")
            self.assertTrue(saved.parent.name.endswith(f"_{self.artwork_id}"))
            self.assertTrue(saved.exists())

    def test_publish_rejects_reparse_parent_before_publication(self):
        with tempfile.TemporaryDirectory(prefix="moku-publish-reparse-test-") as raw_root:
            root = Path(raw_root)
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.png"
            temporary.write_bytes(PNG)
            final = root / "tag_test" / "payload.png"
            real_open = server._open_directory_handle

            def reject_test_parent(path, **kwargs):
                if Path(path).name == "tag_test":
                    raise server.PixivPolicyError("injected reparse directory")
                return real_open(path, **kwargs)

            with patch.object(
                server,
                "_open_directory_handle",
                side_effect=reject_test_parent,
            ):
                with self.assertRaises(server.PixivPolicyError):
                    server.publish_staged_files(
                        staging, [(temporary, final)], save_root=root,
                    )
            self.assertFalse(final.exists())

    def test_existing_different_target_is_preserved_and_publishes_sibling(self):
        with tempfile.TemporaryDirectory(prefix="moku-atomic-replace-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.part"
            temporary.write_bytes(b"new-bytes")
            destination = root / "payload.png"
            destination.write_bytes(b"old-bytes")

            with patch.object(
                server.os,
                "replace",
                side_effect=AssertionError("existing targets must use ReplaceFileW"),
            ):
                saved = server.publish_staged_files(
                    staging, [(temporary, destination)], save_root=root,
                )

            sibling = root / "payload (1).png"
            self.assertEqual(saved, [sibling])
            self.assertEqual(destination.read_bytes(), b"old-bytes")
            self.assertEqual(sibling.read_bytes(), b"new-bytes")
            self.assertFalse((staging / ".backup-0.bak").exists())

    def test_target_created_at_no_replace_seam_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="moku-target-race-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.part"
            temporary.write_bytes(b"downloaded")
            destination = root / "payload.png"
            real_publish = server._publish_owned_staged_file

            def create_competitor_then_publish(ownership, final, **kwargs):
                Path(final).write_bytes(b"competitor")
                return real_publish(ownership, final, **kwargs)

            with patch.object(
                server,
                "_publish_owned_staged_file",
                side_effect=create_competitor_then_publish,
            ):
                server.publish_staged_files(
                    staging, [(temporary, destination)], save_root=root,
                )

            self.assertEqual(destination.read_bytes(), b"competitor")
            self.assertEqual((root / "payload (1).png").read_bytes(), b"downloaded")
            self.assertFalse((staging / ".backup-0.bak").exists())

    def test_locked_staging_preserves_existing_target_and_publishes_sibling(self):
        with tempfile.TemporaryDirectory(prefix="moku-locked-atomic-test-") as raw_root:
            root = Path(raw_root).resolve()
            destination = root / "payload.png"
            destination.write_bytes(b"old-bytes")

            with server.secure_staging_directory(
                root, prefix=".moku-locked-",
            ) as staging:
                temporary = staging / "payload.part"
                temporary.write_bytes(b"new-bytes")
                saved = server.publish_staged_files(
                    staging,
                    [(temporary, destination)],
                    save_root=root,
                    staging_locked=True,
                )
                sibling = root / "payload (1).png"
                self.assertEqual(saved, [sibling])
                self.assertEqual(destination.read_bytes(), b"old-bytes")
                self.assertEqual(sibling.read_bytes(), b"new-bytes")
                self.assertFalse((staging / ".backup-0.bak").exists())

            self.assertFalse(any(root.glob(".moku-locked-*")))

    def test_failed_publish_removes_the_parent_it_created_before_validation_failed(self):
        with tempfile.TemporaryDirectory(prefix="moku-publish-cleanup-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.png"
            temporary.write_bytes(PNG)
            destination = root / "new-parent" / "nested" / "payload.png"
            validate = server._validated_publish_parent

            def fail_after_creating_parent(save_root, final, **kwargs):
                validate(save_root, final, **kwargs)
                raise server.PixivPolicyError("injected publish failure")

            with patch.object(
                server,
                "_validated_publish_parent",
                side_effect=fail_after_creating_parent,
            ):
                with self.assertRaisesRegex(server.PixivPolicyError, "injected"):
                    server.publish_staged_files(
                        staging, [(temporary, destination)], save_root=root,
                    )

            self.assertFalse(destination.parent.exists())
            self.assertFalse((root / "new-parent").exists())

    def test_created_parent_is_recorded_before_handle_validation(self):
        with tempfile.TemporaryDirectory(prefix="moku-created-parent-window-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.part"
            temporary.write_bytes(PNG)
            destination = root / "new-parent" / "payload.png"
            real_open = server._open_directory_handle
            injected = False

            def fail_new_parent_open(path, **kwargs):
                nonlocal injected
                if Path(path) == destination.parent and Path(path).exists() and not injected:
                    injected = True
                    raise PermissionError("injected new-parent open failure")
                return real_open(path, **kwargs)

            with patch.object(
                server, "_open_directory_handle", side_effect=fail_new_parent_open,
            ):
                with self.assertRaisesRegex(PermissionError, "injected"):
                    server.publish_staged_files(
                        staging, [(temporary, destination)], save_root=root,
                    )

            self.assertFalse(destination.parent.exists())

    @unittest.skipUnless(os.name == "nt", "Windows delete-sharing directory locks")
    def test_publish_locks_each_destination_ancestor_against_retargeting(self):
        for attack_level in ("parent", "ancestor"):
            with self.subTest(attack_level=attack_level), tempfile.TemporaryDirectory(
                prefix="moku-publish-lock-test-",
            ) as raw_root:
                root = Path(raw_root).resolve()
                staging = root / ".staging"
                staging.mkdir()
                temporary = staging / "payload.png"
                temporary.write_bytes(PNG)
                destination = root / "branch" / "target" / "payload.png"
                destination.parent.mkdir(parents=True)
                attacked = destination.parent if attack_level == "parent" else root / "branch"
                moved = root / f"retargeted-{attack_level}"
                real_replace = server.os.replace

                def attack_before_replace(source, target):
                    result = attempt_directory_rename(attacked, moved)
                    if result.returncode == 0:
                        moved.rename(attacked)
                        raise AssertionError(
                            f"{attack_level} retargeting was not blocked",
                        )
                    if result.returncode != 32 or result.stdout.strip() != "blocked:32":
                        raise AssertionError(
                            f"unexpected retarget failure: {result.returncode} "
                            f"{result.stdout!r} {result.stderr!r}",
                        )
                    return real_replace(source, target)

                with patch.object(server.os, "replace", side_effect=attack_before_replace):
                    saved = server.publish_staged_files(
                        staging, [(temporary, destination)], save_root=root,
                    )

                self.assertEqual(saved, [destination])
                self.assertEqual(destination.read_bytes(), PNG)
                self.assertFalse(moved.exists())
                # Every native handle must be closed after publication.
                attacked.rename(moved)
                moved.rename(attacked)

    @unittest.skipUnless(os.name == "nt", "Windows secure staging directory locks")
    def test_staging_locks_the_selected_root_before_mkdtemp(self):
        with tempfile.TemporaryDirectory(prefix="moku-staging-lock-test-") as raw_parent:
            parent = Path(raw_parent).resolve()
            root = parent / "selected"
            root.mkdir()
            moved = parent / "retargeted"
            real_mkdtemp = server.tempfile.mkdtemp

            def attack_at_mkdtemp(*args, **kwargs):
                result = attempt_directory_rename(root, moved)
                self.assertEqual(
                    (result.returncode, result.stdout.strip()),
                    (32, "blocked:32"),
                    (result.stdout, result.stderr),
                )
                return real_mkdtemp(*args, **kwargs)

            with patch.object(server.tempfile, "mkdtemp", side_effect=attack_at_mkdtemp):
                with server.secure_staging_directory(root, prefix=".moku-test-") as staging:
                    (staging / "payload.part").write_bytes(PNG)
                    self.assertEqual((staging / "payload.part").read_bytes(), PNG)

            self.assertTrue(root.is_dir())
            self.assertFalse(moved.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_staging_initialization_failure_removes_created_empty_directory(self):
        with tempfile.TemporaryDirectory(prefix="moku-staging-init-test-") as raw_root:
            root = Path(raw_root).resolve()
            real_open = server._open_directory_handle

            def fail_staging_open(path, **kwargs):
                if Path(path).name.startswith(".moku-init-"):
                    raise PermissionError("injected staging handle failure")
                return real_open(path, **kwargs)

            with patch.object(
                server, "_open_directory_handle", side_effect=fail_staging_open,
            ):
                with self.assertRaisesRegex(PermissionError, "injected"):
                    with server.secure_staging_directory(
                        root, prefix=".moku-init-",
                    ):
                        self.fail("staging context unexpectedly opened")

            self.assertEqual(list(root.iterdir()), [])

    def test_staging_identity_failure_closes_handle_before_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="moku-staging-identity-test-") as raw_root:
            root = Path(raw_root).resolve()
            real_open = server._open_directory_handle
            real_identity = server._handle_identity
            staging_handles: set[int] = set()

            def open_and_mark_staging(path, **kwargs):
                handle = real_open(path, **kwargs)
                if Path(path).name.startswith(".moku-identity-"):
                    staging_handles.add(handle)
                return handle

            def fail_staging_identity(handle):
                if handle in staging_handles:
                    raise server.PixivPolicyError("injected identity failure")
                return real_identity(handle)

            with patch.object(
                server, "_open_directory_handle", side_effect=open_and_mark_staging,
            ), patch.object(server, "_handle_identity", side_effect=fail_staging_identity):
                with self.assertRaisesRegex(server.PixivPolicyError, "identity failure"):
                    with server.secure_staging_directory(
                        root, prefix=".moku-identity-",
                    ):
                        self.fail("staging context unexpectedly opened")

            self.assertEqual(list(root.iterdir()), [])

    def test_target_swapped_before_publish_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="moku-target-swap-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.part"
            temporary.write_bytes(b"downloaded")
            destination = root / "payload.png"
            destination.write_bytes(b"original")
            moved_original = root / "original-moved.png"
            real_publish = server._publish_owned_staged_file

            def swap_then_publish(ownership, final, **kwargs):
                Path(final).replace(moved_original)
                Path(final).write_bytes(b"competitor")
                return real_publish(ownership, final, **kwargs)

            with patch.object(
                server,
                "_publish_owned_staged_file",
                side_effect=swap_then_publish,
            ):
                server.publish_staged_files(
                    staging, [(temporary, destination)], save_root=root,
                )

            self.assertEqual(moved_original.read_bytes(), b"original")
            self.assertEqual(destination.read_bytes(), b"competitor")
            self.assertEqual((root / "payload (1).png").read_bytes(), b"downloaded")
            self.assertFalse((staging / ".backup-0.bak").exists())

    def test_publish_rejects_a_posix_symlink_inside_the_save_root(self):
        with tempfile.TemporaryDirectory(prefix="moku-publish-symlink-test-") as raw_root:
            root = Path(raw_root).resolve()
            target = root / "target"
            target.mkdir()
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlink creation is unavailable")
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.png"
            temporary.write_bytes(PNG)

            with self.assertRaisesRegex(server.PixivPolicyError, "重解析点"):
                server.publish_staged_files(
                    staging,
                    [(temporary, link / "payload.png")],
                    save_root=root,
                )

            self.assertFalse((target / "payload.png").exists())

    def test_concurrent_publish_transactions_are_serialized(self):
        with tempfile.TemporaryDirectory(prefix="moku-publish-serial-test-") as raw_root:
            root = Path(raw_root).resolve()
            first_staging = root / ".first-staging"
            second_staging = root / ".second-staging"
            first_staging.mkdir()
            second_staging.mkdir()
            first_temporary = first_staging / "payload.part"
            second_temporary = second_staging / "payload.part"
            first_temporary.write_bytes(b"first")
            second_temporary.write_bytes(b"second")
            destination = root / "payload.png"
            first_entered = threading.Event()
            release_first = threading.Event()
            second_entered = threading.Event()
            failures: list[BaseException] = []
            failures_lock = threading.Lock()
            real_publish_owned = server._publish_owned_staged_file

            def gated_publish(ownership, final, **kwargs):
                if threading.current_thread().name == "first-publisher":
                    first_entered.set()
                    if not release_first.wait(timeout=3):
                        raise AssertionError("first publication was not released")
                elif threading.current_thread().name == "second-publisher":
                    second_entered.set()
                return real_publish_owned(ownership, final, **kwargs)

            def publish(staging, temporary):
                try:
                    server.publish_staged_files(
                        staging, [(temporary, destination)], save_root=root,
                    )
                except BaseException as exc:
                    with failures_lock:
                        failures.append(exc)

            with patch.object(
                server,
                "_publish_owned_staged_file",
                side_effect=gated_publish,
            ):
                first = threading.Thread(
                    target=publish, args=(first_staging, first_temporary),
                    name="first-publisher", daemon=True,
                )
                second = threading.Thread(
                    target=publish, args=(second_staging, second_temporary),
                    name="second-publisher", daemon=True,
                )
                first.start()
                self.assertTrue(first_entered.wait(timeout=2))
                second.start()
                try:
                    second_entered_early = second_entered.wait(timeout=0.5)
                finally:
                    release_first.set()
                first.join(timeout=3)
                second.join(timeout=3)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertFalse(
                second_entered_early,
                "a second publication entered the active transaction",
            )
            self.assertEqual(failures, [])
            self.assertEqual(destination.read_bytes(), b"first")
            self.assertEqual((root / "payload (1).png").read_bytes(), b"second")

    def test_published_target_blocks_external_writer_until_rollback(self):
        for had_original in (False, True):
            with self.subTest(had_original=had_original), tempfile.TemporaryDirectory(
                prefix="moku-external-writer-test-",
            ) as raw_root:
                root = Path(raw_root).resolve()
                staging = root / ".staging"
                staging.mkdir()
                first = root / "first.png"
                second = root / "second.png"
                if had_original:
                    first.write_bytes(b"first-old")
                first_staged = staging / "first.part"
                second_staged = staging / "second.part"
                first_staged.write_bytes(b"first-new")
                second_staged.write_bytes(b"second-new")
                real_publish = server._publish_owned_staged_file

                def fail_second_publish(ownership, destination, **kwargs):
                    if Path(destination) == second:
                        owned_first = (
                            root / "first (1).png" if had_original else first
                        )
                        attack = attempt_file_write(
                            owned_first, b"concurrent-owner-data",
                        )
                        self.assertNotEqual(
                            attack.returncode, 0, attack.stdout + attack.stderr,
                        )
                        raise OSError("injected second publish failure")
                    return real_publish(ownership, destination, **kwargs)

                with patch.object(
                    server,
                    "_publish_owned_staged_file",
                    side_effect=fail_second_publish,
                ):
                    with self.assertRaises(OSError):
                        server.publish_staged_files(
                            staging,
                            [(first_staged, first), (second_staged, second)],
                            save_root=root,
                        )

                if had_original:
                    self.assertEqual(first.read_bytes(), b"first-old")
                    self.assertFalse((root / "first (1).png").exists())
                else:
                    self.assertFalse(first.exists())


    def test_sibling_rollback_avoids_touching_existing_target(self):
        with tempfile.TemporaryDirectory(prefix="moku-no-delete-pending-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            first = root / "first.png"
            second = root / "second.png"
            first.write_bytes(b"first-old")
            first_staged = staging / "first.part"
            second_staged = staging / "second.part"
            first_staged.write_bytes(b"first-new")
            second_staged.write_bytes(b"second-new")
            real_publish = server._publish_owned_staged_file

            def fail_second_without_delete_pending(ownership, destination, **kwargs):
                if Path(destination) == second:
                    attack = attempt_file_write(
                        root / "first (1).png", b"concurrent",
                    )
                    self.assertNotEqual(attack.returncode, 0, attack.stdout + attack.stderr)
                    raise OSError("injected second publish failure")
                return real_publish(ownership, destination, **kwargs)

            with patch.object(
                server,
                "_publish_owned_staged_file",
                side_effect=fail_second_without_delete_pending,
            ):
                with self.assertRaises(OSError):
                    server.publish_staged_files(
                        staging,
                        [(first_staged, first), (second_staged, second)],
                        save_root=root,
                    )

            self.assertEqual(first.read_bytes(), b"first-old")
            self.assertFalse((root / "first (1).png").exists())

    def test_post_publish_failure_removes_owned_siblings_and_preserves_originals(self):
        with tempfile.TemporaryDirectory(prefix="moku-publish-rollback-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            first = root / "first.png"
            second = root / "second.png"
            first.write_bytes(b"first-old")
            second.write_bytes(b"second-old")
            first_staged = staging / "first.png"
            second_staged = staging / "second.png"
            first_staged.write_bytes(b"first-new")
            second_staged.write_bytes(b"second-new")
            real_publish = server._publish_owned_staged_file

            def fail_after_second_publish(ownership, destination, **kwargs):
                result = real_publish(ownership, destination, **kwargs)
                if Path(destination) == second:
                    raise OSError("injected post-publish failure")
                return result

            with patch.object(
                server,
                "_publish_owned_staged_file",
                side_effect=fail_after_second_publish,
            ):
                with self.assertRaises(OSError):
                    server.publish_staged_files(
                        staging,
                        [(first_staged, first), (second_staged, second)],
                        save_root=root,
                    )

            self.assertEqual(first.read_bytes(), b"first-old")
            self.assertEqual(second.read_bytes(), b"second-old")
            self.assertFalse((root / "first (1).png").exists())
            self.assertFalse((root / "second (1).png").exists())

    def test_post_publish_failure_keeps_owned_sibling_locked_until_rollback(self):
        with tempfile.TemporaryDirectory(prefix="moku-owned-backup-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            first = root / "first.png"
            second = root / "second.png"
            first.write_bytes(b"first-old")
            second.write_bytes(b"second-old")
            first_staged = staging / "first.part"
            second_staged = staging / "second.part"
            first_staged.write_bytes(b"first-new")
            second_staged.write_bytes(b"second-new")
            real_publish = server._publish_owned_staged_file

            def fail_after_owned_publish(ownership, destination, **kwargs):
                result = real_publish(ownership, destination, **kwargs)
                if Path(destination) == second:
                    attack = attempt_file_write(
                        root / "second (1).png", b"tampered-owned-file",
                    )
                    self.assertNotEqual(
                        attack.returncode, 0, attack.stdout + attack.stderr,
                    )
                    raise OSError("injected owned post-publish failure")
                return result

            with patch.object(
                server,
                "_publish_owned_staged_file",
                side_effect=fail_after_owned_publish,
            ):
                with self.assertRaises(OSError):
                    server.publish_staged_files(
                        staging,
                        [(first_staged, first), (second_staged, second)],
                        save_root=root,
                    )

            self.assertEqual(first.read_bytes(), b"first-old")
            self.assertEqual(second.read_bytes(), b"second-old")
            self.assertFalse((root / "first (1).png").exists())
            self.assertFalse((root / "second (1).png").exists())

    def test_incomplete_rollback_preserves_empty_staging_without_open_file_handles(self):
        with tempfile.TemporaryDirectory(prefix="moku-preserve-staging-test-") as raw_root:
            root = Path(raw_root)
            created: list[server.PublishedFileOwnership] = []
            real_create = server._create_owned_staged_file
            real_delete = server._delete_empty_directory_on_close

            def capture_ownership(*args, **kwargs):
                ownership = real_create(*args, **kwargs)
                created.append(ownership)
                return ownership

            def inject_publish_failure(ownership, final, **kwargs):
                # The real publish function takes ownership first, then this seam
                # fails before response construction. Rollback is now solely owned
                # by publish_staged_files, not by the HTTP handler.
                result = real_publish(ownership, final, **kwargs)
                raise OSError("injected publish failure")

            def fail_owned_delete(handle):
                if any(ownership.handle == handle for ownership in created):
                    raise PermissionError("injected rollback delete failure")
                return real_delete(handle)

            real_publish = server._publish_owned_staged_file
            with patch.object(
                server, "_create_owned_staged_file", side_effect=capture_ownership,
            ), patch.object(
                server, "_publish_owned_staged_file", side_effect=inject_publish_failure,
            ), patch.object(
                server, "_delete_empty_directory_on_close", side_effect=fail_owned_delete,
            ):
                status, body = self.post_single(root)

            self.assertEqual(status, 502, body)
            self.assertIn("未能安全恢复", body["error"])
            self.assertTrue(created)
            self.assertTrue(all(
                ownership.handle is None
                and ownership.staged_handle is None
                for ownership in created
            ))
            staging = list(root.glob(".moku-single-*"))
            self.assertEqual(len(staging), 1)
            recovery_files = list(staging[0].glob(".page-*.part"))
            self.assertEqual(len(recovery_files), 1)
            # The recovery material is preserved because rollback deletion failed,
            # but all Win32 handles must already be closed.
            shutil.rmtree(staging[0])

    def test_network_exceptions_remain_covered_by_oserror(self):
        self.assertTrue(issubclass(urllib.error.URLError, OSError))
        self.assertTrue(issubclass(TimeoutError, OSError))
        self.assertTrue(issubclass(ConnectionError, OSError))

    @unittest.skipUnless(
        os.name == "nt" and hasattr(Path, "is_junction"),
        "Windows junction detection requires pathlib.Path.is_junction",
    )
    def test_publish_rejects_a_junction_inside_the_save_root(self):
        with tempfile.TemporaryDirectory(prefix="moku-publish-junction-test-") as raw_root:
            root = Path(raw_root).resolve()
            target = root / "target"
            target.mkdir()
            junction = root / "junction"
            if not create_junction(junction, target):
                self.skipTest("Windows junction creation is unavailable")
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.png"
            temporary.write_bytes(PNG)
            try:
                with self.assertRaisesRegex(server.PixivPolicyError, "重解析点"):
                    server.publish_staged_files(
                        staging,
                        [(temporary, junction / "payload.png")],
                        save_root=root,
                    )
                self.assertFalse((target / "payload.png").exists())
            finally:
                junction.rmdir()

    @unittest.skipUnless(
        os.name == "nt" and hasattr(Path, "is_junction"),
        "Windows junction detection requires pathlib.Path.is_junction",
    )
    def test_save_root_rejects_a_junction_instead_of_resolving_through_it(self):
        with tempfile.TemporaryDirectory(prefix="moku-save-root-junction-test-") as raw_root:
            root = Path(raw_root).resolve()
            target = root / "target"
            target.mkdir()
            junction = root / "junction"
            if not create_junction(junction, target):
                self.skipTest("Windows junction creation is unavailable")
            try:
                with self.assertRaisesRegex(server.RequestInputError, "重解析点"):
                    server.Handler._save_root({"saveRoot": str(junction)})
            finally:
                junction.rmdir()

    def test_save_root_canonicalizes_an_absolute_alias(self):
        with tempfile.TemporaryDirectory(prefix="moku-save-root-alias-test-") as raw_root:
            root = Path(raw_root).resolve()
            alias_root = root / "alias-parent" / ".."
            self.assertEqual(
                server.Handler._save_root({"saveRoot": str(alias_root)}),
                root,
            )

    def test_save_root_rejects_a_missing_directory_during_input_validation(self):
        with tempfile.TemporaryDirectory(prefix="moku-save-root-missing-test-") as raw_parent:
            missing = Path(raw_parent) / "not-created"
            with self.assertRaisesRegex(server.RequestInputError, "不存在"):
                server.Handler._save_root({"saveRoot": str(missing)})
            self.assertFalse(missing.exists())

    def test_publish_accepts_an_unresolved_alias_of_the_same_save_root(self):
        with tempfile.TemporaryDirectory(prefix="moku-publish-alias-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.png"
            temporary.write_bytes(PNG)
            alias_root = root / "alias-parent" / ".."
            final = alias_root / "payload.png"

            saved = server.publish_staged_files(
                staging, [(temporary, final)], save_root=alias_root,
            )

            self.assertEqual(saved, [root / "payload.png"])
            self.assertEqual((root / "payload.png").read_bytes(), PNG)

    def test_single_download_uses_current_search_context_folder(self):
        with tempfile.TemporaryDirectory(prefix="moku-single-context-test-") as raw_root:
            root = Path(raw_root)
            status, body = self.post_single(
                root, create_folder=True, context={"kind": "author", "value": "测试画师"},
            )
            self.assertEqual(status, 200, body)
            saved = [root / Path(relative) for relative in body["saved"]]
            self.assertTrue(all(path.parent == root / "author_测试画师" for path in saved))
            self.assertTrue(all(path.exists() for path in saved))

    def test_batch_failure_publishes_no_partial_files(self):
        calls = 0

        def fail_second(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise urllib.error.URLError("injected failure")
            return PNG, "image/png"

        with tempfile.TemporaryDirectory(prefix="moku-batch-partial-test-") as raw_root:
            root = Path(raw_root)
            status, body = self.post(
                {
                    "groups": [{"id": self.artwork_id, "pages": [0, 1]}],
                    "quality": "regular",
                    "createFolder": True,
                    "context": {"kind": "tags", "value": "测试"},
                },
                root,
                pixiv_side_effect=fail_second,
            )
            self.assertEqual(status, 502)
            self.assertIn("失败", body["error"])
            self.assertEqual([path for path in root.rglob("*") if path.is_file()], [])

    def test_single_download_publishes_all_pages_atomically(self):
        calls = 0

        def fail_second(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise urllib.error.URLError("injected failure")
            return PNG, "image/png"

        with tempfile.TemporaryDirectory(prefix="moku-single-partial-test-") as raw_root:
            root = Path(raw_root)
            status, body = self.post_single(
                root,
                pixiv_side_effect=fail_second,
            )
            self.assertEqual(status, 502)
            self.assertIn("失败", body["error"])
            self.assertEqual([path for path in root.rglob("*") if path.is_file()], [])


class CrossPlatformPublicationContractTests(unittest.TestCase):
    def test_publish_fails_closed_without_windows_publication_primitives(self):
        with tempfile.TemporaryDirectory(prefix="moku-platform-gate-test-") as raw_root:
            root = Path(raw_root).resolve()
            staging = root / ".staging"
            staging.mkdir()
            temporary = staging / "payload.part"
            temporary.write_bytes(PNG)
            destination = root / "payload.png"

            with patch.object(server, "WINDOWS_SECURE_PUBLICATION", False):
                with self.assertRaisesRegex(server.PixivPolicyError, "Windows"):
                    server.publish_staged_files(
                        staging, [(temporary, destination)], save_root=root,
                    )

            self.assertEqual(temporary.read_bytes(), PNG)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
