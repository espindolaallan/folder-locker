"""Status contracts use disposable registrations and GET-only simulated flags."""
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

import test_two_roots as fixtures
from test_locker import locker


class StatusRootTests(unittest.TestCase):
    setUp = fixtures.TwoRootTests.setUp
    activate = fixtures.TwoRootTests.activate

    def cli(self, *args):
        before = {p: p.read_bytes() for p in (self.config, self.extra, self.state / 'state.json',
                                            self.state / 'additional/state.json') if p.exists()}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch.object(locker, 'require_admin', side_effect=AssertionError('status requested admin')), \
                patch.object(locker, 'operation_journal', side_effect=AssertionError('status opened journal')):
            try:
                code = locker.main(list(args))
            except SystemExit as error:
                code = error.code
        self.assertEqual(self.flags.calls, 0)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertFalse((self.state / 'state.json').exists())
        self.assertFalse((self.state / 'additional/state.json').exists())
        return code, stdout.getvalue(), stderr.getvalue()

    def mark_primary_locked(self):
        for path in (self.primary, self.primary / 'file.txt'):
            info = path.stat()
            self.flags.values[info.st_dev, info.st_ino] = 0x80000 | locker.IMMUTABLE

    def test_single_root_default_stdout_and_stderr_unchanged(self):
        self.extra.unlink()
        for options in [(), ('--json',), ('--tree',), ('--editable',), ('--locked',)]:
            with self.subTest(options=options):
                implicit = self.cli('status', *options)
                explicit = self.cli('status', str(self.primary), *options)
                self.assertEqual(implicit, explicit)
                self.assertEqual(implicit[0], 0)
                self.assertEqual(implicit[2], '')

    def test_default_multi_root_preserves_stdout_and_announces_scope_on_stderr(self):
        self.activate()
        self.mark_primary_locked()
        for options in [(), ('--json',), ('--tree', '--limit', '0')]:
            with self.subTest(options=options):
                code, output, notice = self.cli('status', *options)
                explicit = self.cli('status', str(self.primary), *options)
                self.assertEqual((code, output), explicit[:2])
                self.assertEqual(explicit[2], '')
                self.assertIn('Scope: default root only.', notice)
                self.assertIn(str(self.secondary), notice)
                self.assertIn('ACTIVE; not inspected.', notice)
                self.assertIn('locker status --all', notice)
                self.assertIn('locker roots', notice)
        report, = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(report['path'], str(self.primary))
        self.assertNotIn('registration', report)  # Legacy JSON schema unchanged.
        self.assertNotIn('default', report)

    def test_default_pending_notice_does_not_inspect_reserved_path(self):
        with patch.object(locker, 'open_path', wraps=locker.open_path) as opened:
            code, output, notice = self.cli('status', '--json')
        self.assertEqual(code, 0)
        self.assertIn('PENDING; not inspected.', notice)
        self.assertFalse(self.secondary.exists())
        self.assertTrue(all(not locker.inside(call.args[0], self.secondary) for call in opened.call_args_list))
        self.assertEqual(len(json.loads(output)), 1)

    def test_explicit_secondary_path_and_selector_remain_quiet(self):
        self.activate()
        selected = self.cli('--root', str(self.secondary), 'status', '--json')
        path = self.cli('status', str(self.secondary), '--json')
        self.assertEqual(selected, path)
        self.assertEqual(path[0], 0)
        self.assertEqual(path[2], '')
        row, = json.loads(path[1])
        self.assertEqual(row['path'], str(self.secondary))
        self.assertNotIn('registration', row)
        self.assertEqual(self.cli('--root', str(self.primary), 'status')[2], '')

    def test_all_active_roots_json_and_text(self):
        self.activate()
        self.mark_primary_locked()
        code, output, error = self.cli('status', '--all', '--json')
        self.assertEqual((code, error), (0, ''))
        rows = json.loads(output)
        self.assertEqual([r['path'] for r in rows], [str(self.primary), str(self.secondary)])
        self.assertEqual([r['default'] for r in rows], [True, False])
        self.assertEqual([r['registration'] for r in rows], ['ACTIVE', 'ACTIVE'])
        self.assertEqual([r['state'] for r in rows], ['LOCKED', 'EDITABLE'])
        self.assertEqual([r['total'] for r in rows], [2, 1])
        for row in rows:
            explicit, = json.loads(self.cli('status', row['path'], '--json')[1])
            self.assertEqual({k:v for k,v in row.items() if k not in ('default','registration')}, explicit)
        code, output, error = self.cli('status', '--all')
        self.assertEqual((code, error), (0, ''))
        self.assertIn(f'Root: {locker.display(self.primary)} [ACTIVE] (default)', output)
        self.assertIn(f'Root: {locker.display(self.secondary)} [ACTIVE]', output)
        self.assertIn('2 locked, 0 editable', output)
        self.assertIn('0 locked, 1 editable', output)

    def test_all_single_root_still_has_explicit_registration(self):
        self.extra.unlink()
        code, output, error = self.cli('status', '--all', '--json')
        self.assertEqual((code, error), (0, ''))
        row, = json.loads(output)
        self.assertEqual(row['registration'], 'ACTIVE')
        self.assertTrue(row['default'])
        self.assertEqual(row['path'], str(self.primary))

    def test_all_reports_pending_without_inspection_and_exits_nonzero(self):
        self.secondary.mkdir()  # Still not activated; must not scan even an existing directory.
        (self.secondary / 'not-authorized.txt').write_text('synthetic reserved content')
        with patch.object(locker, 'scan', wraps=locker.scan) as scan:
            code, output, error = self.cli('status', '--all', '--json')
        self.assertEqual((code, error), (1, ''))
        rows = json.loads(output)
        self.assertEqual(rows[0]['state'], 'EDITABLE')
        self.assertEqual(rows[1]['registration'], 'PENDING')
        self.assertEqual(rows[1]['state'], 'UNVERIFIED')
        self.assertIn('not inspected', rows[1]['error'])
        self.assertNotIn('total', rows[1])  # Unknown is never zero or LOCKED.
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(scan.call_args.args[1], [self.primary])
        code, output, _ = self.cli('status', '--all')
        self.assertEqual(code, 1)
        self.assertIn('[PENDING]', output)
        self.assertIn('UNVERIFIED', output)
        self.assertEqual(self.cli('status', str(self.secondary))[0], 1)

    def test_broken_additional_metadata_preserves_default_but_refuses_global_claim(self):
        self.extra.write_text('invalid json')
        code, output, error = self.cli('status', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(output)), 1)
        self.assertIn('Additional registration is UNVERIFIED; global status is unknown.', error)
        code, output, error = self.cli('status', '--all', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(output, '')
        self.assertTrue(error)

    def test_replaced_active_root_fails_before_global_output(self):
        self.activate()
        self.secondary.rename(self.parent / 'original-library')
        self.secondary.mkdir()
        code, output, error = self.cli('status', '--all', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(output, '')
        self.assertIn('replaced', error)
        self.assertEqual(self.cli('status', '--json')[0], 0)

    def test_scan_failure_is_reported_per_root_with_nonzero_exit(self):
        self.activate()
        actual_read = self.flags.read
        secondary_inode = self.secondary.stat().st_ino
        def fail_secondary(fd):
            if os.fstat(fd).st_ino == secondary_inode:
                raise PermissionError('synthetic read failure')
            return actual_read(fd)
        with patch.object(self.flags, 'read', side_effect=fail_secondary):
            code, output, error = self.cli('status', '--all', '--json')
        self.assertEqual((code, error), (1, ''))
        rows = json.loads(output)
        self.assertEqual(rows[0]['state'], 'EDITABLE')
        self.assertEqual(rows[1]['state'], 'UNVERIFIED')
        self.assertEqual(rows[1]['registration'], 'ACTIVE')
        self.assertIn('synthetic read failure', rows[1]['error'])

    def test_all_cannot_be_combined_with_paths_or_root(self):
        for args in [('status', '--all', str(self.primary)),
                     ('--root', str(self.primary), 'status', '--all')]:
            with self.subTest(args=args):
                code, output, error = self.cli(*args)
                self.assertEqual(code, 2)
                self.assertEqual(output, '')
                self.assertIn('cannot be combined', error)

    def test_all_requires_installed_configuration(self):
        self.config.unlink()
        code, output, error = self.cli('status', '--all', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(output, '')
        self.assertTrue(error)

    def test_roots_registry_output_remains_separate_from_lock_status(self):
        for active in (False, True):
            if active:
                self.activate()
            code, output, error = self.cli('roots', '--json')
            self.assertEqual((code, error), (0, ''))
            self.assertEqual(json.loads(output), [
                {'root':str(self.primary),'registration':'ACTIVE','default':True},
                {'root':str(self.secondary),'registration':'ACTIVE' if active else 'PENDING','default':False}])
            code, output, error = self.cli('roots')
            self.assertEqual((code, error), (0, ''))
            self.assertIn('(default)', output)
            self.assertIn('ACTIVE' if active else 'PENDING', output)

    def test_all_filters_and_limits_apply_per_root_without_truncating_json(self):
        self.activate()
        self.mark_primary_locked()
        for name in ('a', 'b'):
            (self.secondary / name).write_text('synthetic')
        info = self.secondary.stat()
        self.flags.values[info.st_dev, info.st_ino] = locker.IMMUTABLE
        for view in ('--tree', '--editable', '--locked'):
            with self.subTest(view=view):
                code, output, error = self.cli('status', '--all', view, '--limit', '1', '--json')
                self.assertEqual((code, error), (0, ''))
                for row in json.loads(output):
                    expected, = json.loads(self.cli('status', row['path'], view, '--json')[1])
                    self.assertEqual(row['locations'], expected['locations'])
        code, text, error = self.cli('status', '--all', '--editable', '--limit', '1')
        self.assertEqual((code, error), (0, ''))
        self.assertIn('1 more locations not shown', text)

    def test_reinitializing_same_primary_preserves_active_secondary_and_histories(self):
        # Ordinary binary upgrade calls init with the original root, not reserve/activate.
        self.activate()
        primary_journal = self.state / 'state.json'
        secondary_journal = self.state / 'additional/state.json'
        primary_journal.write_text('legacy journal fixture')
        secondary_journal.write_text('independent journal fixture')
        before = {p:p.read_bytes() for p in (self.config, self.extra, primary_journal, secondary_journal)}
        with contextlib.redirect_stdout(io.StringIO()):
            locker.initialize(self.primary)
        self.assertEqual({p:p.read_bytes() for p in before}, before)
        self.assertEqual(self.flags.calls, 0)


if __name__ == '__main__':
    unittest.main()
