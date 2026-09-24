"""Workspace selection and startup behavior, without system configuration writes."""

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_locker import FakeFlags, locker


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scratch = Path(temporary.name)
        self.workspace = self.scratch / "Shared research (drafts)"
        self.workspace.mkdir()
        (self.workspace / "notes.txt").write_text("notes\n")
        self.config = self.scratch / "config.json"
        config_patch = patch.object(locker, "CONFIG", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def install_config(self):
        scope = locker.Scope.create(self.workspace)
        self.config.write_text(json.dumps({
            "version": 1, "root": str(self.workspace), "identity": locker.asdict(scope.identity),
        }))
        return scope

    def test_unconfigured_preview_requires_explicit_workspace(self):
        with self.assertRaisesRegex(locker.LockerError, "No workspace configured"):
            locker.load_scope(None)
        self.assertFalse(self.config.exists())

    def test_unconfigured_write_still_requires_installation(self):
        with self.assertRaisesRegex(locker.LockerError, "Install and initialize"):
            locker.load_scope(str(self.workspace), write=True)

    def test_explicit_preview_handles_arbitrary_workspace_with_spaces(self):
        flags = FakeFlags()
        output = io.StringIO()
        with patch.object(locker, "Flags", return_value=flags), patch("sys.stdout", output):
            result = locker.main(["--root", str(self.workspace), "status", "--json"])
        self.assertEqual(result, 0)
        report, = json.loads(output.getvalue())
        self.assertEqual(report["path"], str(self.workspace))
        self.assertEqual(report["state"], "EDITABLE")
        self.assertEqual(report["total"], 2)
        self.assertEqual(flags.calls, 0)
        self.assertFalse(self.config.exists())

    def test_existing_version_one_config_remains_the_default(self):
        expected = self.install_config()
        before = self.config.read_bytes()
        with patch.object(locker, "check_trusted") as trusted:
            self.assertEqual(locker.load_scope(None), expected)
            self.assertEqual(locker.load_scope(str(self.workspace)), expected)
        self.assertEqual(trusted.call_count, 2)
        self.assertEqual(self.config.read_bytes(), before)

    def test_override_cannot_replace_installed_workspace(self):
        self.install_config()
        with patch.object(locker, "check_trusted"):
            with self.assertRaisesRegex(locker.LockerError, "cannot override"):
                locker.load_scope(str(self.scratch))

    def test_init_without_root_does_not_create_configuration(self):
        with patch.object(locker, "initialize") as initialize, patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                locker.main(["init"])
        self.assertEqual(error.exception.code, 2)
        initialize.assert_not_called()
        self.assertFalse(self.config.exists())

    def test_self_test_requires_explicit_parent(self):
        with patch.object(locker, "self_test") as self_test, patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                locker.main(["self-test"])
        self.assertEqual(error.exception.code, 2)
        self_test.assert_not_called()

    def test_help_and_version_do_not_need_a_workspace(self):
        for option in ("--help", "--version"):
            with self.subTest(option=option), patch("sys.stdout", io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    locker.main([option])
                self.assertEqual(error.exception.code, 0)
        self.assertFalse(self.config.exists())


class StartupTests(unittest.TestCase):
    def run_with_runtime(self, assignment):
        return subprocess.run(
            [sys.executable, "-I", "-c",
             f"import sys, runpy; {assignment}; runpy.run_path(sys.argv[1], run_name='__main__')",
             str(Path(locker.__file__)), "--version"],
            text=True, capture_output=True, check=False,
        )

    def test_unsupported_platform_has_a_clear_error(self):
        result = self.run_with_runtime("sys.platform = 'darwin'")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Linux is required", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_old_python_has_a_clear_error(self):
        result = self.run_with_runtime("sys.version_info = (3, 9, 0)")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.10 or newer", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
