from __future__ import annotations

import hashlib
import re
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHEEL = ROOT / "third_party" / "wheels" / "proxy_tools-0.1.0-py3-none-any.whl"

EXPECTED_HASHES = {
    "bottle==0.13.4": "045684fbd2764eac9cdeb824861d1551d113e8b683d8d26e296898d3dd99a12e",
    "cffi==2.1.0": "c97f080ea627e2863524c5af3836e2270b5f5dfff1f104392b959f8df0c5d384",
    "clr-loader==0.3.1": "cbad189de20d202a7d621956b0fc38049e13c9bf7ca2923441eff725cd121aa1",
    "proxy-tools==0.1.0": "e9c3763d867f00a88c203686480d67950c04f210d8be71c861800bc7e9b53b40",
    "pycparser==3.0": "b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992",
    "pythonnet==3.1.0": "7bdd4de03df3547a48122a3989265c8b31d5be0d19dadffa009eec7df8085e0b",
    "pywebview==6.2.1": "9d07275f53894ab4d5e2e0e996227193e7187dec276d9b624dccbce029216b46",
    "typing-extensions==4.16.0": "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
    "altgraph==0.17.5": "f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597",
    "packaging==26.2": "5fc45236b9446107ff2415ce77c807cee2862cb6fac22b8a73826d0693b0980e",
    "pefile==2024.8.26": "76f8b485dcd3b1bb8166f1128d395fa3d87af26360c2358fb75b80019b957c6f",
    "pyinstaller==6.21.0": "7fae06c494ce0ebfe6bd3055c0e409def884f63af2e3705d06bd431ad9237fc7",
    "pyinstaller-hooks-contrib==2026.6": "fd13b8ac126b35361175edacd41a0d97080b75dd5f4b594ecefefff969509dd3",
    "pywin32-ctypes==0.2.3": "8a1513379d709975552d202d942d9837758905c8d01eb82b8bcc30918929e7b8",
    "setuptools==83.0.0": "29b23c360f22f414dc7336bb39178cc7bcbf6021ed2733cde173f09dba19abb3",
    "websocket-client==1.9.0": "af248a825037ef591efbf6ed20cc5faa03d3b47b9e5a2230a529eeee1c1fc3ef",
}


class DependencyLockTests(unittest.TestCase):
    @staticmethod
    def _locked_rows(text: str) -> dict[str, str]:
        pattern = re.compile(
            r"(?m)^([A-Za-z0-9_.-]+==[^\s\\]+) \\\n"
            r"    --hash=sha256:([0-9a-f]{64})$"
        )
        return dict(pattern.findall(text))

    def test_lockfiles_require_exact_binary_artifact_hashes(self):
        runtime = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        development = (ROOT / "requirements-dev.lock").read_text(encoding="utf-8")
        combined = runtime + "\n" + development

        self.assertIn("--require-hashes", runtime)
        self.assertIn("--only-binary=:all:", runtime)
        self.assertIn("--find-links ./third_party/wheels", runtime)
        self.assertEqual(self._locked_rows(combined), EXPECTED_HASHES)

    def test_vendored_proxy_wheel_matches_audited_reproducible_artifact(self):
        self.assertTrue(WHEEL.is_file())
        raw = WHEEL.read_bytes()
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            EXPECTED_HASHES["proxy-tools==0.1.0"],
        )
        with zipfile.ZipFile(WHEEL) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(
                set(archive.namelist()),
                {
                    "proxy_tools/__init__.py",
                    "proxy_tools-0.1.0.dist-info/METADATA",
                    "proxy_tools-0.1.0.dist-info/WHEEL",
                    "proxy_tools-0.1.0.dist-info/top_level.txt",
                    "proxy_tools-0.1.0.dist-info/RECORD",
                },
            )
            self.assertEqual(
                hashlib.sha256(archive.read("proxy_tools/__init__.py")).hexdigest(),
                "d1539d95e1a713c068ca81d42e047b2c76568964cf277596d4e19efb22f476be",
            )

    def test_license_generator_covers_every_locked_distribution(self):
        source = (ROOT / "generate-third-party-licenses.py").read_text(encoding="utf-8")
        for requirement in EXPECTED_HASHES:
            project = requirement.split("==", 1)[0]
            self.assertIn(f'"{project}"', source)


if __name__ == "__main__":
    unittest.main()
