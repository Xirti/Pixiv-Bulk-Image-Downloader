import tempfile
import unittest
from pathlib import Path


def task():
    return {"id": "task-000000000001", "kind": "batch", "taskOptions": {
        "quality": "original", "ugoiraFormat": "source", "saveRoot": "C:/art",
        "createFolder": False, "groupArtworks": False, "skipExisting": True,
    }, "remainingChunks": [{"requestId": "request-000000000001", "groups": [{"id": "123", "pages": [0, 2]}],
                            "pageCount": 2, "context": {"kind": "tags", "value": "cat"}}],
        "savedCount": 2, "fileCount": 2, "skippedCount": 0, "firstSaved": "cat.png",
        "completedBatches": 1, "totalBatches": 2}


class WorkspaceStoreTests(unittest.TestCase):
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
