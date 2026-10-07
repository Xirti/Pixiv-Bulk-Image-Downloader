import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch


def task():
    return {"id": "task-000000000001", "kind": "batch", "taskOptions": {
        "quality": "original", "ugoiraFormat": "source", "saveRoot": "C:/art",
        "createFolder": False, "groupArtworks": False, "skipExisting": True,
    }, "remainingChunks": [{"requestId": "request-000000000001", "groups": [{"id": "123", "pages": [0, 2]}],
                            "pageCount": 2, "context": {"kind": "tags", "value": "cat"}}],
        "savedCount": 2, "fileCount": 2, "skippedCount": 0, "firstSaved": "cat.png",
        "completedBatches": 1, "totalBatches": 2}


class WorkspaceStoreTests(unittest.TestCase):
    def test_mixed_basket_keeps_saved_artwork_order(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkspaceStore(Path(temporary) / "workspace.db")
            rows = [{"id": id, "pages": [0], "item": {"pages": 1, "restriction": restriction}}
                    for id, restriction in (("123", "r18"), ("456", "safe"), ("789", "r18"))]
            store.save_basket(rows, "account-A", revision=0)
            self.assertEqual([row["id"] for row in store.load("account-A")["basket"]], ["123", "456", "789"])

    def test_saved_workspace_can_be_read_without_database_writes(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkspaceStore(Path(temporary) / "workspace.db")
            basket = [{"id": "123", "pages": [0, 2], "item": {"pages": 3}}]
            store.save_basket(basket, "public", revision=0)
            store.save_task(task(), "public")
            connect = sqlite3.connect
            def read_only(*args, **kwargs):
                connection = connect(*args, **kwargs)
                connection.execute("PRAGMA query_only=ON")
                return connection
            with patch("workspace_store.sqlite3.connect", side_effect=read_only):
                restored = store.load("public")
                self.assertEqual(restored["basket"][0]["pages"], [0, 2])
                self.assertEqual(len(restored["tasks"]), 1)
                self.assertTrue(store.has_task(task()["id"], "public"))

    def test_capacity_counts_merged_public_and_private_choices_once(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkspaceStore(Path(temporary) / "workspace.db")
            private = {"id": "123", "pages": list(range(600)), "item": {"pages": 1000, "restriction": "r18"}}
            public = {"id": "123", "pages": list(range(400, 1000)), "item": {"pages": 1000, "restriction": "safe"}}
            store.save_basket([private], "account-A", revision=0)
            store.save_basket([public], "public", revision=1)
            restored = store.load("account-A")
            self.assertEqual(len(restored["basket"]), 1)
            self.assertEqual(restored["basket"][0]["pages"], list(range(1000)))
            additional = {"id": "456", "pages": [0], "item": {"pages": 1}}
            with self.assertRaisesRegex(ValueError, "容量"):
                store.save_basket([public, additional], "public", revision=2)
            self.assertEqual(store.load("account-A")["revision"], 2)

    def test_work_changing_to_public_does_not_duplicate_restored_basket_entry(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkspaceStore(Path(temporary) / "workspace.db")
            private = {"id": "123", "pages": [0, 2], "item": {"pages": 3, "restriction": "r18"},
                       "context": {"kind": "author", "value": "artist"}}
            public = {"id": "123", "pages": [1], "item": {"pages": 3, "restriction": "safe"},
                      "context": {"kind": "tags", "value": "cat"}}
            store.save_basket([private], "account-A", revision=0)
            store.save_basket([public], "public", revision=1)
            restored = WorkspaceStore(store.path).load("account-A")["basket"]
            self.assertEqual(len(restored), 1, "one work appeared twice and cannot be restored by the UI")
            self.assertEqual(restored[0]["pages"], [0, 1, 2])
            self.assertEqual(restored[0]["item"]["restriction"], "safe")
            self.assertEqual(restored[0]["context"], private["context"])
            self.assertEqual(store.load("public")["basket"][0]["pages"], [1])

    def test_shared_basket_update_does_not_overfill_another_accounts_private_selection(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkspaceStore(Path(temporary) / "workspace.db")
            private = {"id":"123", "pages":list(range(600)), "item":{"id":"123", "pages":600, "restriction":"r18"}}
            public = {"id":"456", "pages":list(range(400)), "item":{"id":"456", "pages":900, "restriction":"safe"}}
            store.save_basket([private, public], "account-A", revision=0)
            enlarged = {**public, "pages":list(range(900))}
            with self.assertRaisesRegex(ValueError, "容量"):
                store.save_basket([enlarged], "account-B", revision=1)
            restored = store.load("account-A")
            self.assertEqual(restored["revision"], 1)
            self.assertEqual(sum(len(row["pages"]) for row in restored["basket"]), 1000)

    def test_capacity_counts_each_accounts_private_selection_separately(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkspaceStore(Path(temporary) / "workspace.db")
            public = {"id": "456", "pages": list(range(300)), "item": {"pages": 300}}
            for revision, account, artwork in ((0, "account-A", "123"), (1, "account-B", "789")):
                private = {"id": artwork, "pages": list(range(700)), "item": {"pages": 700, "restriction": "r18"}}
                store.save_basket([private, public], account, revision=revision)
            for account in ("account-A", "account-B"):
                self.assertEqual(sum(len(row["pages"]) for row in store.load(account)["basket"]), 1000)

    def test_recent_search_survives_temporary_browser_profile_removal(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workspace.db"
            row = {"tag":"cat", "filters":{"mode":"safe", "workType":"ugoira", "includeAi":False,
                   "fuzzy":True, "startDate":"2026-01-01", "endDate":"2026-01-03"}}
            WorkspaceStore(path).remember_search(row, "public")
            self.assertEqual(WorkspaceStore(path).load("public")["recent"], [row])

    def test_restart_restores_exact_pages_and_paused_tasks_without_tokens(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "workspace.db"
            store = WorkspaceStore(file)
            basket = [{"id": "123", "pages": [0, 2], "item": {"id": "123", "title": "Cat", "pages": 3,
                       "thumb": "/api/pixiv/image?token=secret", "restriction": "safe"},
                       "context": {"kind": "tags", "value": "cat"}, "resultPage": 8, "archived": True}]
            store.save_basket(basket, "public", revision=0)
            store.save_task(task(), "public")
            loaded = WorkspaceStore(file).load("public")
            self.assertEqual(loaded["basket"][0]["pages"], [0, 2])
            self.assertTrue(loaded["basket"][0]["archived"])
            self.assertNotIn("thumb", loaded["basket"][0]["item"])
            self.assertEqual(loaded["tasks"][0]["status"], "paused")
            self.assertEqual(loaded["tasks"][0]["remainingChunks"][0]["requestId"], "request-000000000001")
            self.assertNotIn(b"secret", file.read_bytes())

    def test_account_binding_limits_and_stale_basket_writes(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkspaceStore(Path(temporary) / "workspace.db")
            basket = [{"id": "123", "pages": [0], "item": {"id": "123", "pages": 1, "restriction": "r18"}}]
            store.save_basket(basket, "account-A", revision=0)
            store.save_task(task(), "account-A")
            self.assertEqual(store.load("account-B")["basket"], [])
            self.assertEqual(store.load("account-B")["tasks"], [])
            store.save_basket([], "account-B", revision=1)
            self.assertEqual(len(store.load("account-A")["basket"]), 1)
            with self.assertRaisesRegex(ValueError, "stale"):
                store.save_basket([], "account-A", revision=0)
            invalid = task()
            invalid["taskOptions"]["Cookie"] = "secret"
            with self.assertRaises(ValueError):
                store.save_task(invalid, "account-A")
            with self.assertRaises(ValueError):
                store.save_basket([{"id":"123","pages":list(range(1001)),"item":{"id":"123","pages":1001}}], "public", revision=1)

    def test_completed_task_is_removed_without_touching_basket(self):
        from workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "workspace.db"
            store = WorkspaceStore(file)
            store.save_task(task(), "public")
            self.assertTrue(store.delete_task(task()["id"], "public"))
            self.assertEqual(WorkspaceStore(file).load("public")["tasks"], [])
