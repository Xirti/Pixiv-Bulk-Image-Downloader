
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import server


class FolderPickerEndpointTests(unittest.TestCase):
    def make_handler(self, origin="http://127.0.0.1:48721", host="127.0.0.1:48721", client="127.0.0.1"):
        return SimpleNamespace(client_address=(client, 12345), headers={"Origin": origin, "Host": host})

    def test_same_origin_loopback_is_trusted(self):
        self.assertTrue(server.trusted_local_request(self.make_handler()))

    def test_cross_site_origin_is_rejected(self):
        self.assertFalse(server.trusted_local_request(self.make_handler(origin="https://evil.example")))

    def test_non_loopback_client_is_rejected(self):
        self.assertFalse(server.trusted_local_request(self.make_handler(client="192.168.6.88")))

    def test_picker_uses_sta_helper_and_stdin_for_path(self):
        process = SimpleNamespace()
        process.communicate = unittest.mock.Mock(return_value=('{"selected":"D:\\\\Pictures","cancelled":false}', ""))
        process.kill = unittest.mock.Mock()
        with patch("folder_picker.subprocess.Popen", return_value=process) as launch:
            from folder_picker import select_folder
            self.assertEqual(select_folder("D:\\"), {"selected": r"D:\Pictures", "cancelled": False})
        args = launch.call_args.args[0]
        self.assertIn("-Sta", args)
        self.assertNotIn("D:\\", args)
        self.assertIn('"initial": "D:\\\\"', process.communicate.call_args.kwargs["input"])

    def test_picker_timeout_is_truthful(self):
        process = SimpleNamespace()
        process.communicate = unittest.mock.Mock(side_effect=[subprocess.TimeoutExpired("helper", 0.01), ("", "")])
        process.kill = unittest.mock.Mock()
        with patch("folder_picker.subprocess.Popen", return_value=process):
            from folder_picker import select_folder
            result = select_folder("", timeout=0.01)
            self.assertTrue(result["cancelled"])
            self.assertIn("超时", result["error"])
            process.kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
