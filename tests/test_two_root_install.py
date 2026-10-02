"""Installer failure/rollback fixtures: no root, real flags, or system installation writes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_locker import FakeFlags

SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('two_root_upgrade',SOURCE/'install_two_roots.py')
upgrade=importlib.util.module_from_spec(spec);sys.modules[spec.name]=upgrade;spec.loader.exec_module(upgrade)


class TwoRootInstallerTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='locker-upgrade-fixture-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.legacy=self.root/'code';self.legacy.mkdir()
        (self.legacy/'file.txt').write_text('original content')
        self.canonical=self.root/'library'
        control=self.root/'control';control.mkdir()
        state=control/'state';state.mkdir()
        paths=upgrade.Paths(control/'locker',control/'config.json',control/'additional-root.json',state)
        self.u=upgrade.Upgrade(SOURCE/'locker.py',paths);self.p=paths
        self.flags=FakeFlags()
        self.u.api.require_admin=lambda **kw:None
        self.u.api.check_trusted=lambda p:None
        self.u.api.Flags=lambda:self.flags
        self.original=b'old executable fixture; never executed\n';self.p.executable.write_bytes(self.original)
        self.p.executable.chmod(0o755)
        mock=patch.object(upgrade,'PREVIOUS_SHA256',hashlib.sha256(self.original).hexdigest());mock.start();self.addCleanup(mock.stop)
        scope=self.u.api.Scope.create(self.legacy)
        self.p.config.write_text(json.dumps({'version':1,'root':str(self.legacy),'identity':self.u.api.asdict(scope.identity)},indent=3)+'\n')
        node=self.u.api.Node.from_stat(self.legacy/'file.txt',(self.legacy/'file.txt').stat())
        (state/'state.json').write_text(json.dumps({'version':1,'pending':None,'entries':[self.u.api.asdict(node)]},indent=4))
        self.before_config=self.p.config.read_bytes();self.before_journal=(state/'state.json').read_bytes()

    def unchanged(self):
        self.assertEqual(self.p.config.read_bytes(),self.before_config)
        self.assertEqual((self.p.state/'state.json').read_bytes(),self.before_journal)
        self.assertFalse(self.canonical.exists())
        self.assertEqual(self.flags.calls,0)

    def test_success_preserves_exact_config_journal_and_all_protection(self):
        self.u.install(self.legacy,self.canonical);self.u.validate();self.unchanged()
        data=json.loads(self.p.secondary.read_text());self.assertIsNone(data['identity'])
        self.assertEqual(data['root'],str(self.canonical))
        self.assertEqual(self.p.executable.read_bytes(),(SOURCE/'locker.py').read_bytes())
        self.assertEqual(self.p.executable.stat().st_mode&0o777,0o755)
        self.assertEqual(self.p.backup.stat().st_mode&0o777,0o700)
        self.assertEqual((self.p.backup/'config-before.json').read_bytes(),self.before_config)
        self.assertEqual((self.p.backup/'journal-before.json').read_bytes(),self.before_journal)

    def test_rollback_restores_binary_and_only_removes_new_controls(self):
        original_stat=self.u.signature(self.p.executable)
        self.u.install(self.legacy,self.canonical);self.u.rollback();self.unchanged()
        self.assertEqual(self.p.executable.read_bytes(),self.original)
        self.assertEqual(self.u.signature(self.p.executable),original_stat)
        self.assertFalse(self.p.secondary.exists());self.assertFalse((self.p.state/'additional').exists())
        self.u.rollback();self.unchanged()  # Idempotent recovery.

    def test_validation_failure_automatically_rolls_back(self):
        with patch.object(self.u,'_validate',side_effect=ValueError('injected validation failure')):
            with self.assertRaisesRegex(ValueError,'injected'):self.u.install(self.legacy,self.canonical)
        self.assertEqual(self.p.executable.read_bytes(),self.original)
        self.assertFalse(self.p.secondary.exists());self.unchanged()

    def test_binary_install_failure_leaves_original_working(self):
        real=self.u.write
        def fail(path,data,mode=0o600):
            if path==self.p.executable:raise OSError('injected atomic replacement failure')
            return real(path,data,mode)
        with patch.object(self.u,'write',side_effect=fail),self.assertRaisesRegex(OSError,'injected'):
            self.u.install(self.legacy,self.canonical)
        self.assertEqual(self.p.executable.read_bytes(),self.original);self.assertFalse(self.p.secondary.exists());self.unchanged()

    def test_interrupted_upgrade_can_be_rolled_back_from_saved_checkpoint(self):
        with patch.object(self.u,'_validate',side_effect=KeyboardInterrupt),patch.object(self.u,'_rollback'):
            with self.assertRaises(KeyboardInterrupt):self.u.install(self.legacy,self.canonical)
        self.assertTrue(self.p.secondary.exists())
        self.u.rollback();self.assertEqual(self.p.executable.read_bytes(),self.original);self.unchanged()

    def test_wrong_binary_or_root_rejected_before_backup_or_change(self):
        self.p.executable.write_bytes(b'foreign tool')
        with self.assertRaisesRegex(self.u.api.LockerError,'reviewed 1.3.0'):self.u.install(self.legacy,self.canonical)
        self.assertFalse(self.p.backup.exists());self.assertFalse(self.p.secondary.exists());self.unchanged()
        self.p.executable.write_bytes(self.original)
        with self.assertRaisesRegex(self.u.api.LockerError,'configured root'):self.u.install(self.root,self.canonical)
        self.assertFalse(self.p.backup.exists())

    def test_pending_recovery_refused_without_resetting_journal(self):
        record=json.loads(self.before_journal);record['pending']='unlock'
        (self.p.state/'state.json').write_text(json.dumps(record));before=(self.p.state/'state.json').read_bytes()
        with self.assertRaisesRegex(self.u.api.LockerError,'pending primary'):self.u.install(self.legacy,self.canonical)
        self.assertEqual((self.p.state/'state.json').read_bytes(),before)
        self.assertFalse(self.p.backup.exists());self.assertFalse(self.p.secondary.exists())

    def test_existing_canonical_root_third_root_and_shared_parent_are_not_adopted(self):
        self.canonical.mkdir()
        with self.assertRaisesRegex(self.u.api.LockerError,'absent root'):self.u.install(self.legacy,self.canonical)
        self.canonical.rmdir()
        with self.assertRaisesRegex(self.u.api.LockerError,'disjoint'):self.u.install(self.legacy,self.root)
        self.p.secondary.write_text('{}')
        with self.assertRaisesRegex(self.u.api.LockerError,'already exists'):self.u.install(self.legacy,self.canonical)
        self.assertFalse(self.p.backup.exists());self.unchanged()

    def test_journal_changes_are_never_overwritten_by_rollback(self):
        self.u.install(self.legacy,self.canonical)
        journal=self.p.state/'state.json';journal.write_bytes(self.before_journal+b'\n')
        with self.assertRaisesRegex(self.u.api.LockerError,'journal changed'):self.u.rollback()
        self.assertEqual(journal.read_bytes(),self.before_journal+b'\n')
        self.assertEqual(self.p.executable.read_bytes(),(SOURCE/'locker.py').read_bytes())

    def test_identical_bytes_do_not_hide_primary_config_or_journal_replacement(self):
        self.u.install(self.legacy,self.canonical)
        for target in (self.p.config,self.p.state/'state.json'):
            with self.subTest(path=target.name):
                original=target.with_suffix('.original');target.rename(original)
                info=original.stat();target.write_bytes(original.read_bytes())
                target.chmod(info.st_mode&0o777)
                os.utime(target,ns=(info.st_atime_ns,info.st_mtime_ns))
                with self.assertRaisesRegex(self.u.api.LockerError,'changed; stop and inspect'):
                    self.u.validate()
                os.replace(original,target)
                self.u.validate()
        self.unchanged()

    def test_post_activation_or_provisioning_rollback_is_refused(self):
        self.u.install(self.legacy,self.canonical);self.canonical.mkdir()
        with self.assertRaisesRegex(self.u.api.LockerError,'pre-provisioning'):self.u.rollback()
        self.assertTrue(self.canonical.exists());self.assertTrue(self.p.secondary.exists())

    def test_changed_legacy_protection_is_detected_without_repair_attempt(self):
        self.u.install(self.legacy,self.canonical)
        path=self.legacy/'file.txt';info=path.stat();self.flags.values[info.st_dev,info.st_ino]=0x80000|0x10
        with self.assertRaisesRegex(self.u.api.LockerError,'Legacy inode'):self.u.validate()
        self.assertEqual(self.flags.calls,0)

    def test_validation_detects_non_executable_installed_mode(self):
        self.u.install(self.legacy,self.canonical);self.p.executable.chmod(0o644)
        with self.assertRaisesRegex(self.u.api.LockerError,'mode is not 0755'):self.u.validate()
        self.unchanged()

    def test_second_install_cannot_replace_the_reviewed_checkpoint(self):
        self.u.install(self.legacy,self.canonical)
        manifest=(self.p.backup/'manifest.json').read_bytes()
        with self.assertRaisesRegex(self.u.api.LockerError,'Checkpoint already exists'):self.u.install(self.legacy,self.canonical)
        self.assertEqual((self.p.backup/'manifest.json').read_bytes(),manifest);self.unchanged()


if __name__=='__main__':unittest.main()
