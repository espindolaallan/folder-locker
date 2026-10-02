"""Two-root routing/identity/recovery tests; no real flag writes or administrator access."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_locker import FakeFlags, locker


class TwoRootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='locker-two-roots-')
        self.addCleanup(temp.cleanup)
        self.scratch = Path(temp.name)
        self.parent = self.scratch / 'workspaces'; self.parent.mkdir()
        self.primary = self.parent / 'code'; self.primary.mkdir()
        (self.primary / 'file.txt').write_text('legacy')
        self.secondary = self.parent / 'library'
        self.config = self.scratch / 'config.json'
        self.extra = self.scratch / 'additional-root.json'
        self.state = self.scratch / 'state'; self.state.mkdir()
        (self.state / 'additional').mkdir()
        self.flags = FakeFlags()
        for name, value in [('CONFIG',self.config),('SECONDARY_CONFIG',self.extra),('STATE_DIR',self.state),
                            ('Flags',lambda:self.flags),('check_trusted',lambda p:None),('require_admin',lambda **kw:None)]:
            mocked=patch.object(locker,name,value);mocked.start();self.addCleanup(mocked.stop)
        scope=locker.Scope.create(self.primary)
        self.config.write_text(json.dumps({'version':1,'root':str(self.primary),'identity':locker.asdict(scope.identity)}))
        self.extra.write_text(json.dumps(locker.root_reservation(scope,self.secondary)))

    def activate(self):
        self.secondary.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):locker.activate_root(self.secondary)
        return locker.load_scope(str(self.secondary))

    def test_pending_is_not_created_or_adopted(self):
        before=self.config.read_bytes()
        rows=locker.registered_roots()
        self.assertEqual([r['registration'] for r in rows],['ACTIVE','PENDING'])
        self.assertFalse(self.secondary.exists())
        for override,paths in [(str(self.secondary),None),(None,[str(self.secondary)])]:
            with self.assertRaisesRegex(locker.LockerError,'PENDING'):locker.load_scope(override,paths=paths)
        self.secondary.mkdir()
        with self.assertRaisesRegex(locker.LockerError,'PENDING'):locker.load_scope(str(self.secondary))
        self.assertEqual(self.config.read_bytes(),before)

    def test_activation_binds_identity_without_changing_flags_or_primary_files(self):
        before=self.config.read_bytes();scope=self.activate()
        self.assertEqual(scope.root,self.secondary)
        self.assertEqual(locker.load_scope(None).root,self.primary)
        self.assertEqual(locker.load_scope(None,paths=[str(self.secondary)]).root,self.secondary)
        self.assertEqual(self.flags.calls,0)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertFalse((self.state/'state.json').exists())
        self.assertFalse((self.state/'additional/state.json').exists())
        saved=self.extra.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):locker.activate_root(self.secondary)
        self.assertEqual(self.extra.read_bytes(),saved)

    def test_nonempty_root_is_not_implicitly_adopted(self):
        self.secondary.mkdir();(self.secondary/'unexpected.txt').write_text('do not adopt')
        with self.assertRaisesRegex(locker.LockerError,'empty'):locker.activate_root(self.secondary)
        self.assertIsNone(json.loads(self.extra.read_text())['identity'])
        self.assertEqual(self.flags.calls,0)

    def test_third_root_shared_parent_and_mixed_selections_refused(self):
        self.activate()
        for override,paths in [(str(self.parent),None),(str(self.parent/'third'),None),
                               (None,[str(self.parent)]),(None,[str(self.primary),str(self.secondary)])]:
            with self.subTest(override=override,paths=paths),self.assertRaises(locker.LockerError):
                locker.load_scope(override,paths=paths)
        for root in [self.parent,self.primary,self.primary/'sub']:
            with self.assertRaises(locker.LockerError):locker.root_reservation(locker.primary_scope(),root)
        self.assertEqual(self.flags.calls,0)

    def test_bad_additional_registration_does_not_disable_primary(self):
        self.extra.write_text('broken')
        self.assertEqual(locker.load_scope(None).root,self.primary)
        with self.assertRaises(ValueError):locker.load_scope(str(self.secondary))

    def test_changed_primary_configuration_invalidates_only_additional_binding(self):
        self.activate();self.config.write_text(self.config.read_text()+'\n')
        self.assertEqual(locker.load_scope(None).root,self.primary)
        with self.assertRaisesRegex(locker.LockerError,'Primary configuration changed'):locker.load_scope(str(self.secondary))

    def test_parent_and_root_replacement_are_refused(self):
        self.activate()
        self.secondary.rename(self.parent/'old-library');self.secondary.mkdir()
        scope=locker.load_scope(str(self.secondary))
        with self.assertRaisesRegex(locker.LockerError,'replaced'):scope.select([str(self.secondary)])
        self.parent.rename(self.scratch/'old-parent');self.parent.mkdir()
        with self.assertRaisesRegex(locker.LockerError,'replaced'):locker.load_scope(str(self.secondary))

    def test_symlink_root_and_other_root_targets_are_not_followed(self):
        self.secondary.symlink_to(self.primary,target_is_directory=True)
        with self.assertRaises(OSError):locker.activate_root(self.secondary)
        self.secondary.unlink();scope=self.activate()
        (self.secondary/'legacy-link').symlink_to(self.primary,target_is_directory=True)
        with self.assertRaises(OSError):scope.select([str(self.secondary/'legacy-link')])
        scan=locker.scan(scope,[self.secondary],self.flags)
        self.assertEqual(len(scan),1);self.assertEqual(len(scan.symlinks),1)

    def test_activation_refuses_a_mounted_alias(self):
        self.secondary.mkdir()
        with patch.object(locker,'mounted_paths',return_value={self.secondary}):
            with self.assertRaisesRegex(locker.LockerError,'mounted/aliased'):locker.activate_root(self.secondary)
        self.assertIsNone(json.loads(self.extra.read_text())['identity'])
        self.assertEqual(self.flags.calls,0)

    def test_primary_path_identity_mismatch_is_refused(self):
        data=json.loads(self.config.read_text());data['root']=str(self.parent)
        self.config.write_text(json.dumps(data))
        with self.assertRaisesRegex(locker.LockerError,'Invalid primary'):locker.load_scope(None)

    def test_read_only_cli_roots_and_pending_status(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):self.assertEqual(locker.main(['roots','--json']),0)
        rows=json.loads(output.getvalue());self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['registration'],'PENDING')
        error=io.StringIO()
        with contextlib.redirect_stderr(error):self.assertEqual(locker.main(['status',str(self.secondary),'--json']),1)
        self.assertIn('PENDING',error.getvalue());self.assertFalse(self.secondary.exists())
        self.assertEqual(self.flags.calls,0)

    def test_independent_journals_leave_primary_history_bytes_untouched(self):
        primary=locker.load_scope(None)
        with locker.operation_journal(primary) as journal:
            locker.Locker(primary,journal,self.flags).lock([self.primary])
        history=(self.state/'state.json').read_bytes()
        config=self.config.read_bytes();scope=self.activate()
        (self.secondary/'new.txt').write_text('canonical')
        with locker.operation_journal(scope) as journal:
            locker.Locker(scope,journal,self.flags).lock([self.secondary])
        with locker.operation_journal(scope) as journal:
            locker.Locker(scope,journal,self.flags).unlock([self.secondary])
        self.assertEqual((self.state/'state.json').read_bytes(),history)
        self.assertEqual(self.config.read_bytes(),config)
        self.assertTrue(self.flags.at(self.primary)&locker.IMMUTABLE)
        self.assertFalse(self.flags.at(self.secondary)&locker.IMMUTABLE)

    def test_cross_root_hardlink_is_refused_before_any_flag_write(self):
        scope=self.activate();os.link(self.primary/'file.txt',self.secondary/'alias.txt')
        with locker.operation_journal(scope) as journal:
            with self.assertRaisesRegex(locker.LockerError,'outside this selection'):
                locker.Locker(scope,journal,self.flags).lock([self.secondary])
        self.assertEqual(self.flags.calls,0)

    def test_wrong_root_journal_records_fail_closed(self):
        scope=self.activate()
        node=locker.Node.from_stat(self.primary/'file.txt',(self.primary/'file.txt').stat())
        (self.state/'additional/state.json').write_text(json.dumps({'version':1,'pending':None,'entries':[locker.asdict(node)]}))
        with self.assertRaisesRegex(locker.LockerError,'outside its own root'):
            with locker.operation_journal(scope):pass
        self.assertEqual(self.flags.calls,0)

    def test_additional_root_works_when_primary_directory_is_unavailable(self):
        scope=self.activate();self.primary.rename(self.parent/'code-moved')
        with locker.operation_journal(scope) as journal:
            self.assertEqual(journal.path,self.state/'additional/state.json')
        self.assertEqual(self.flags.calls,0)


if __name__=='__main__':unittest.main()
