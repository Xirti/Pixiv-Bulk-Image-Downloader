from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build_manifest


class BuildManifestTests(unittest.TestCase):
    def _copy_build_inputs(self, destination: Path) -> None:
        for relative in build_manifest.BUILD_INPUT_FILES:
            source = ROOT / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    def test_round_trip_rejects_changed_executable_source_or_distribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            self._copy_build_inputs(source_root)
            distribution = root / "distribution"
            support = distribution / "_internal" / "support.dll"
            support.parent.mkdir(parents=True)
            executable = distribution / "MOKU.exe"
            executable.write_bytes(b"verified-executable")
            support.write_bytes(b"verified-support")
            manifest = distribution / "BUILD_MANIFEST.json"

            build_manifest.write_manifest(
                manifest,
                executable,
                source_root=source_root,
            )
            build_manifest.verify_manifest(
                manifest,
                executable,
                source_root=source_root,
            )

            text = manifest.read_text(encoding="utf-8")
            payload = json.loads(text)
            self.assertEqual(
                set(payload),
                {
                    "schemaVersion",
                    "version",
                    "sourceGeneration",
                    "exeSha256",
                    "distributionFiles",
                    "distributionDirectories",
                },
            )
            self.assertNotIn(str(root), text)
            self.assertRegex(payload["sourceGeneration"], r"^source-sha256:[0-9a-f]{64}$")
            self.assertRegex(payload["exeSha256"], r"^[0-9A-F]{64}$")
            self.assertEqual(
                set(payload["distributionFiles"]),
                {"MOKU.exe", "_internal/support.dll"},
            )
            self.assertEqual(payload["distributionDirectories"], ["_internal"])

            executable.write_bytes(b"changed-executable")
            with self.assertRaisesRegex(RuntimeError, "rebuild first"):
                build_manifest.verify_manifest(
                    manifest,
                    executable,
                    source_root=source_root,
                )

            executable.write_bytes(b"verified-executable")
            changed_source = source_root / build_manifest.BUILD_INPUT_FILES[0]
            original_source = changed_source.read_bytes()
            changed_source.write_bytes(original_source + b"\n# changed\n")
            with self.assertRaisesRegex(RuntimeError, "rebuild first"):
                build_manifest.verify_manifest(
                    manifest,
                    executable,
                    source_root=source_root,
                )

            changed_source.write_bytes(original_source)
            support.write_bytes(b"changed-support")
            with self.assertRaisesRegex(RuntimeError, "rebuild first"):
                build_manifest.verify_manifest(
                    manifest,
                    executable,
                    source_root=source_root,
                )

            support.write_bytes(b"verified-support")
            unexpected = distribution / "unexpected.bin"
            unexpected.write_bytes(b"unexpected")
            with self.assertRaisesRegex(RuntimeError, "rebuild first"):
                build_manifest.verify_manifest(
                    manifest,
                    executable,
                    source_root=source_root,
                )

            unexpected.unlink()
            support.unlink()
            with self.assertRaisesRegex(RuntimeError, "rebuild first"):
                build_manifest.verify_manifest(
                    manifest,
                    executable,
                    source_root=source_root,
                )

            support.write_bytes(b"verified-support")
            (distribution / "unexpected-empty").mkdir()
            with self.assertRaisesRegex(RuntimeError, "rebuild first"):
                build_manifest.verify_manifest(
                    manifest,
                    executable,
                    source_root=source_root,
                )

    def test_manifest_refuses_non_windows_x64_distribution_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            distribution = Path(temporary)
            forbidden = distribution / "webview" / "lib" / "pywebview-android.jar"
            forbidden.parent.mkdir(parents=True)
            forbidden.write_bytes(b"android")
            with self.assertRaisesRegex(RuntimeError, "non-Windows-x64"):
                build_manifest.distribution_snapshot(distribution)

    def test_manifest_requires_all_pywebview_loader_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            distribution = Path(temporary)
            for marker in build_manifest.PYWEBVIEW_REQUIRED_LOADER_MARKERS[:-1]:
                loader = distribution / "webview" / "lib" / marker
                loader.parent.mkdir(parents=True, exist_ok=True)
                loader.write_bytes(b"loader")
            with self.assertRaisesRegex(RuntimeError, "missing pywebview WebView2 loader"):
                build_manifest.distribution_snapshot(distribution)

    def test_manifest_rejects_unlocked_top_level_package_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            distribution = Path(temporary)
            metadata = (
                distribution
                / "_internal"
                / "cryptography-49.0.0.dist-info"
                / "METADATA"
            )
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                "Name: cryptography\nVersion: 49.0.0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeError, "unlocked top-level package metadata"
            ):
                build_manifest.distribution_snapshot(distribution)

    def test_write_rejects_changed_build_inputs_without_partial_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            self._copy_build_inputs(source_root)
            executable = root / "MOKU.exe"
            executable.write_bytes(b"executable")
            manifest = root / "BUILD_MANIFEST.json"

            with self.assertRaisesRegex(RuntimeError, "changed during build"):
                build_manifest.write_manifest(
                    manifest,
                    executable,
                    source_root=source_root,
                    expected_source_generation="source-sha256:" + "0" * 64,
                )

            self.assertFalse(manifest.exists())
            self.assertFalse(manifest.with_suffix(".json.tmp").exists())

    def test_missing_or_invalid_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "MOKU.exe"
            executable.write_bytes(b"exe")
            source_root = root / "source"
            source_root.mkdir()
            self._copy_build_inputs(source_root)
            manifest = root / "BUILD_MANIFEST.json"

            with self.assertRaisesRegex(RuntimeError, "missing or invalid"):
                build_manifest.verify_manifest(
                    manifest,
                    executable,
                    source_root=source_root,
                )

            manifest.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "rebuild first"):
                build_manifest.verify_manifest(
                    manifest,
                    executable,
                    source_root=source_root,
                )

    def test_build_writes_and_release_verifies_manifest_before_staging(self):
        build = (ROOT / "build-portable.ps1").read_text(encoding="utf-8-sig")
        release = (ROOT / "make-release.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("BUILD_MANIFEST.json", build)
        self.assertIn("build_manifest.py", build)
        self.assertIn("'source'", build)
        self.assertIn("'write'", build)
        self.assertIn("--expected-source-generation", build)

        self.assertIn("BUILD_MANIFEST.json", release)
        self.assertIn("build_manifest.py", release)
        self.assertIn("'verify'", release)
        self.assertIn("Expand-Archive", release)
        self.assertLess(
            release.index("Expand-Archive"),
            release.index("New-Item -ItemType Directory -Path $ReleaseRoot"),
        )
        self.assertLess(
            release.index("'verify'"),
            release.index("$ReleaseRoot ="),
        )

    def test_release_metadata_describes_v105_search_and_collection_contract(self):
        version = (ROOT / "version.py").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        portable = (ROOT / "build-portable.ps1").read_text(encoding="utf-8-sig")

        self.assertIn('__version__ = "1.0.5"', version)
        self.assertIn("Strict multi-tag AND search", readme)
        self.assertIn("100 artworks and 1,000 selected images", readme)
        self.assertIn("## [1.0.5]", changelog)
        self.assertIn("Separate multiple tags with ; or ；", portable)
        self.assertNotIn("Space-separated tags use OR semantics", portable)

    def test_real_batch_probe_does_not_mutate_tracked_evidence_by_default(self):
        probe = (ROOT / "tests" / "real_batch_download_probe.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("MOKU_WRITE_PROBE_RESULT"', probe)
        self.assertNotIn(
            '\n            OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")',
            probe,
        )

    def test_release_closure_includes_packaged_v105_probes(self):
        release = (ROOT / "make-release.ps1").read_text(encoding="utf-8-sig")
        for probe in (
            "packaged_visual_style_probe.py",
            "final_packaged_search_probe.py",
            "final_packaged_tag_cache_probe.py",
        ):
            self.assertIn(probe, release)

    def test_packaged_search_probes_disable_persistent_credentials_and_assert_logged_out(self):
        for name in ("final_packaged_search_probe.py", "final_packaged_tag_cache_probe.py"):
            probe = (ROOT / "tests" / name).read_text(encoding="utf-8")
            self.assertIn("MOKU_DISABLE_PERSISTENT_SESSION", probe)
            self.assertIn('account.get("loggedIn") is', probe)
            self.assertIn("all_status", probe)
            self.assertIn("403", probe)

    def _prepare_release_sandbox(self, root: Path) -> None:
        self._copy_build_inputs(root)
        shutil.copyfile(ROOT / "make-release.ps1", root / "make-release.ps1")
        shutil.copyfile(ROOT / "CHANGELOG.md", root / "CHANGELOG.md")
        tests = root / "tests"
        tests.mkdir(exist_ok=True)
        for probe in (
            "packaged_visual_style_probe.py",
            "final_packaged_search_probe.py",
            "final_packaged_tag_cache_probe.py",
        ):
            (tests / probe).write_text("raise SystemExit(0)\n", encoding="utf-8")
        (root / "LICENSE").write_text("TEST-ONLY LICENSE\n", encoding="utf-8")
        dist = root / "dist" / "MOKU"
        internal = dist / "_internal"
        internal.mkdir(parents=True)
        executable = dist / "MOKU.exe"
        executable.write_bytes(b"release-executable")
        (internal / "support.dll").write_bytes(b"release-support")
        shutil.copyfile(root / "LICENSE", dist / "LICENSE")
        (dist / "THIRD_PARTY_LICENSES.txt").write_text("notices\n", encoding="utf-8")
        (dist / "PRIVACY.md").write_text("privacy\n", encoding="utf-8")
        digest = build_manifest.file_sha256(executable)
        (dist / "SHA256.txt").write_text(
            f"{digest}  MOKU.exe\r\n",
            encoding="utf-8",
            newline="",
        )
        build_manifest.write_manifest(
            dist / "BUILD_MANIFEST.json",
            executable,
            source_root=root,
        )

    @staticmethod
    def _run_release(root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "make-release.ps1"),
                "-SkipBuild",
            ],
            cwd=root,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    @unittest.skipUnless(sys.platform == "win32", "PowerShell integration is Windows-only")
    def test_skip_build_release_accepts_verified_distribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_release_sandbox(root)

            result = self._run_release(root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            version = build_manifest.__version__
            release = root / "release" / f"v{version}"
            archive = release / f"MOKU-v{version}-windows-x64.zip"
            self.assertTrue(archive.is_file())
            self.assertTrue((release / "SHA256SUMS.txt").is_file())
            with zipfile.ZipFile(archive) as packaged:
                self.assertIsNone(packaged.testzip())
                names = {name.replace("\\", "/") for name in packaged.namelist()}
                self.assertIn("MOKU/BUILD_MANIFEST.json", names)
                self.assertIn("MOKU/LICENSE", names)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell integration is Windows-only")
    def test_skip_build_release_rejects_tampered_support_file_without_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_release_sandbox(root)
            (root / "dist" / "MOKU" / "_internal" / "support.dll").write_bytes(
                b"tampered-support"
            )

            result = self._run_release(root)

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((root / "release" / f"v{build_manifest.__version__}").exists())

    @unittest.skipUnless(sys.platform == "win32", "PowerShell integration is Windows-only")
    def test_skip_build_release_rejects_license_changed_after_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_release_sandbox(root)
            (root / "LICENSE").write_text("DIFFERENT LICENSE\n", encoding="utf-8")

            result = self._run_release(root)

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((root / "release" / f"v{build_manifest.__version__}").exists())

    def test_release_script_rehashes_moved_archive_and_cleans_partial_output(self):
        release = (ROOT / "make-release.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("$ProbeLogs", release)
        self.assertIn("Remove-Item -LiteralPath $ProbeLogs -Recurse -Force", release)
        self.assertIn("$ReleaseComplete = $false", release)
        self.assertIn("$ReleaseComplete = $true", release)
        self.assertIn("$FinalArchiveHash", release)
        self.assertLess(release.index("Move-Item"), release.index("$FinalArchiveHash"))
        self.assertIn("if (-not $ReleaseComplete -and (Test-Path -LiteralPath $ReleaseRoot))", release)
        self.assertIn("Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force", release)

    def test_build_and_release_use_verified_python_executable_and_shared_mutex(self):
        build = (ROOT / "build-portable.ps1").read_text(encoding="utf-8-sig")
        release = (ROOT / "make-release.ps1").read_text(encoding="utf-8-sig")
        for script in (build, release):
            self.assertIn("Get-Command python.exe -CommandType Application", script)
            self.assertIn("Microsoft.PowerShell.Security.psd1", script)
            self.assertIn("$env:PSModulePath", script)
            self.assertLess(script.index("Microsoft.PowerShell.Security.psd1"), script.index("Get-AuthenticodeSignature"))
            self.assertIn("sys.implementation.name", script)
            self.assertIn("CPython 3.12", script)
            self.assertIn("MOKU.PixivTagGallery.BuildRelease", script)
            self.assertIn("WaitOne", script)
        self.assertIn("run_tests.py", build)
        self.assertLess(build.index("$SourceBefore ="), build.index("run_tests.py"))
        self.assertLess(build.index("run_tests.py"), build.index("WaitOne"))
        self.assertLess(build.index("WaitOne"), build.index("$SourceLocked ="))
        self.assertLess(build.index("$SourceLocked ="), build.index("-m PyInstaller"))
        self.assertNotRegex(build, r"unittest discover")
        self.assertNotRegex(build, r"(?m)^python ")
        self.assertNotRegex(release, r"(?m)^python ")
        self.assertIn("$SourceFinal", release)
        self.assertLess(release.index("WaitOne"), release.index("$SourceInitial ="))
        self.assertLess(release.index("$SourceInitial ="), release.index("$VersionText ="))
        self.assertLess(release.index("$VersionText ="), release.index("$SourceAfterMetadata ="))
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("Build and verify portable Windows package", workflow)
        self.assertIn("build-portable.ps1", workflow)
        self.assertIn("schemaVersion -ne 3", workflow)
        self.assertRegex(workflow, r"actions/upload-artifact@[0-9a-f]{40}")


if __name__ == "__main__":
    unittest.main()
