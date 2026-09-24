"""Run the installer in a disposable prefix with simulated admin commands.

The staged program simulates scope initialization. No system files, inode flags
or privileges are changed. Kernel enforcement is a separate CI self-test.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="locker-installer-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.source = self.root / "source checkout with spaces"
        self.source.mkdir()
        self.installed = self.bin / "locker"
        self.log = self.root / "init.log"
        self.script = self.source / "install.sh"
        self.script.write_text(INSTALLER.read_text().replace("/usr/local/bin", str(self.bin)))
        self.payload = b'#!/bin/sh\nprintf "%s\\n" "$@" > "$LOCKER_TEST_LOG"\nexit "$LOCKER_TEST_INIT_EXIT"\n'
        (self.source / "locker.py").write_bytes(self.payload)
        self.helpers = self.root / "helpers"
        self.helpers.mkdir()
        self.helper("id", '#!/bin/sh\nprintf "0\\n"\n')
        self.helper("install", '#!/bin/sh\nexec /usr/bin/install -m 0755 "$7" "$8"\n')
        self.environment = dict(os.environ, PATH=f"{self.helpers}:/usr/bin:/bin",
                                LOCKER_TEST_LOG=str(self.log), LOCKER_TEST_INIT_EXIT="0")

    def helper(self, name, contents):
        path = self.helpers / name
        path.write_text(contents)
        path.chmod(0o755)

    def run_install(self, workspace, *, fail=False):
        environment = dict(self.environment, LOCKER_TEST_INIT_EXIT="23" if fail else "0")
        return subprocess.run(["/bin/sh", str(self.script), workspace], env=environment,
                              text=True, capture_output=True, check=False)

    def test_failed_initialization_preserves_existing_executable(self):
        # The same payload allows a reinstall without an upgrade ownership check.
        self.installed.write_bytes(self.payload)
        before = self.installed.stat().st_ino
        result = self.run_install(str(self.root / "missing workspace"), fail=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self.installed.stat().st_ino, before)
        self.assertEqual(self.installed.read_bytes(), self.payload)
        self.assertEqual(list(self.bin.glob(".folder-locker.*")), [])

    def test_failed_first_install_does_not_leave_an_executable(self):
        result = self.run_install(str(self.root / "missing workspace"), fail=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertFalse(self.installed.exists())
        self.assertEqual(list(self.bin.iterdir()), [])

    def test_successful_install_passes_workspace_as_one_argument(self):
        workspace = str(self.root / "shared projects")
        result = self.run_install(workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed.read_bytes(), self.payload)
        self.assertEqual(self.installed.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.log.read_text().splitlines(), ["--root", workspace, "init"])
        self.assertEqual(list(self.bin.glob(".folder-locker.*")), [])

    def test_relative_workspace_is_rejected_before_installation(self):
        result = self.run_install("relative/workspace")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute path", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(list(self.bin.iterdir()), [])

    def test_symlink_destination_is_not_replaced(self):
        unrelated = self.root / "unrelated"
        unrelated.write_text("keep this\n")
        self.installed.symlink_to(unrelated)
        result = self.run_install(str(self.root / "workspace"))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.installed.is_symlink())
        self.assertEqual(unrelated.read_text(), "keep this\n")
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
