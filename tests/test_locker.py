"""Safety and recovery tests. Kernel enforcement is covered by locker self-test."""

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "locker.py"
spec = importlib.util.spec_from_file_location("folder_locker", SOURCE)
locker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = locker
spec.loader.exec_module(locker)


class FakeFlags(locker.Flags):
    """Inject flag failures without needing admin privileges or changing files."""

    def __init__(self):
        self.values = {}
        self.calls = 0
        self.fail_calls = set()

    def identity(self, fd):
        info = os.fstat(fd)
        return info.st_dev, info.st_ino

    def read(self, fd):
        return self.values.get(self.identity(fd), 0x80000)

    def write(self, fd, value):
        self.calls += 1
        if self.calls in self.fail_calls:
            raise PermissionError("injected flag failure")
        self.values[self.identity(fd)] = value

    def at(self, path):
        with locker.open_path(path) as fd:
            return self.read(fd)


class LockerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.scratch = Path(self.temporary.name)
        self.root = self.scratch / "workspace"
        self.project = self.root / "project"
        self.project.mkdir(parents=True)
        self.reference = self.root / "reference.txt"
        self.reference.write_text("reference")
        self.paper = self.project / "paper.txt"
        self.paper.write_text("draft")
        self.scope = locker.Scope.create(self.root)
        self.flags = FakeFlags()
        self.journal_path = self.scratch / "state.json"
        self.engine = locker.Locker(self.scope, locker.Journal(self.journal_path), self.flags)

    def reload(self):
        self.engine = locker.Locker(self.scope, locker.Journal(self.journal_path), self.flags)

    def locked(self, path):
        return bool(self.flags.at(path) & locker.IMMUTABLE)

    def test_nested_unlock_keeps_parent_and_siblings_locked(self):
        self.engine.lock([self.root])
        self.reload()
        self.engine.unlock([self.project])
        self.assertFalse(self.locked(self.project))
        self.assertFalse(self.locked(self.paper))
        self.assertTrue(self.locked(self.root))
        self.assertTrue(self.locked(self.reference))

    def test_relock_includes_new_files_and_all_original_flags_survive(self):
        self.engine.lock([self.root])
        self.engine.unlock([self.project])
        new = self.project / "new.txt"
        new.write_text("new")
        self.engine.lock([self.project])
        self.assertTrue(self.locked(new))
        self.engine.unlock([self.root])
        for node, value in locker.scan(self.scope, [self.root], self.flags):
            self.assertEqual(value, 0x80000, node.path)

    def test_preexisting_immutable_flags_are_not_removed(self):
        node = locker.Node.from_stat(self.reference, self.reference.stat())
        locker.set_immutable(node, True, self.flags)
        self.engine.lock([self.root])
        self.assertNotIn(str(self.reference), self.engine.journal.records)
        self.engine.unlock([self.root])
        self.assertTrue(self.locked(self.reference))
        self.assertFalse(self.locked(self.root))

    def test_repeated_lock_does_not_lose_recovery_records(self):
        self.engine.lock([self.root])
        records = self.engine.journal.records.copy()
        self.assertEqual(self.engine.lock([self.root]), 0)
        self.assertEqual(self.engine.journal.records, records)
        self.assertEqual(self.engine.unlock([self.root]), len(records))

    def test_lock_failure_rolls_back_and_preserves_other_attributes(self):
        self.flags.fail_calls = {3}
        with self.assertRaisesRegex(locker.LockerError, "rolled back"):
            self.engine.lock([self.root])
        for node, flags in locker.scan(self.scope, [self.root], self.flags):
            self.assertEqual(flags, 0x80000, node.path)
        self.assertEqual(locker.Journal(self.journal_path).records, {})

    def test_rollback_failure_keeps_recoverable_journal(self):
        self.flags.fail_calls = {3, 4}
        with self.assertRaisesRegex(locker.LockerError, "rollback was incomplete"):
            self.engine.lock([self.root])
        self.assertTrue(locker.Journal(self.journal_path).records)
        self.flags.fail_calls.clear()
        self.reload()
        self.engine.unlock([self.root])
        self.assertFalse(any(value & locker.IMMUTABLE for _, value in locker.scan(self.scope, [self.root], self.flags)))

    def test_journal_is_persisted_before_first_flag_write(self):
        original_write = self.flags.write

        def checked(fd, value):
            saved = locker.Journal(self.journal_path)
            self.assertEqual(len(saved.records), 4)
            self.assertEqual(saved.pending, "lock")
            original_write(fd, value)

        self.flags.write = checked
        self.engine.lock([self.root])

    def test_interrupted_lock_can_be_recovered_by_new_process(self):
        nodes = [node for node, _ in locker.scan(self.scope, [self.root], self.flags)]
        self.engine.journal.records = {node.path: node for node in nodes}
        self.engine.journal.save("lock")
        locker.set_immutable(nodes[0], True, self.flags)
        self.reload()
        self.engine.unlock([self.root])
        self.assertFalse(self.locked(self.root))
        self.assertFalse(self.engine.journal.records)

    def test_interrupted_unlock_can_be_retried(self):
        self.engine.lock([self.root])
        self.flags.fail_calls = {self.flags.calls + 2}
        with self.assertRaisesRegex(locker.LockerError, "Unlock incomplete"):
            self.engine.unlock([self.root])
        self.flags.fail_calls.clear()
        self.reload()
        self.engine.unlock([self.root])
        self.assertFalse(any(value & locker.IMMUTABLE for _, value in locker.scan(self.scope, [self.root], self.flags)))

    def test_replaced_file_prevents_any_unlock(self):
        self.engine.lock([self.root])
        self.paper.rename(self.project / "old-paper.txt")
        self.paper.write_text("replacement inode")
        self.reload()
        before = self.flags.calls
        with self.assertRaisesRegex(locker.LockerError, "replaced"):
            self.engine.unlock([self.root])
        self.assertEqual(self.flags.calls, before)
        self.assertFalse(self.locked(self.paper))

    def test_symlink_target_is_never_locked_or_added_to_journal(self):
        external = self.scratch / "outside.txt"
        external.write_text("outside")
        link = self.project / "shortcut"
        link.symlink_to(external)
        self.engine.lock([self.root])
        self.assertTrue(self.locked(self.project))
        self.assertFalse(self.locked(external))
        self.assertNotIn(str(external), self.engine.journal.records)
        self.assertNotIn(str(link), self.engine.journal.records)
        inspected = locker.scan(self.scope, [self.root], self.flags)
        self.assertEqual(len(inspected.symlinks), 1)
        self.assertEqual(inspected.protected_symlinks(), 1)
        self.engine.unlock([self.project])
        self.assertEqual(locker.scan(self.scope, [self.root], self.flags).protected_symlinks(), 0)

    def test_directory_links_broken_links_and_cycles_are_not_traversed(self):
        external = self.scratch / "external-dir"
        external.mkdir()
        (external / "data.txt").write_text("outside")
        (self.project / "directory").symlink_to(external, target_is_directory=True)
        (self.project / "cycle").symlink_to(self.root, target_is_directory=True)
        (self.project / "broken").symlink_to(self.scratch / "missing")
        (self.project / "relative").symlink_to("paper.txt")
        (self.project / "chain").symlink_to("relative")
        self.engine.lock([self.root])
        inspected = locker.scan(self.scope, [self.root], self.flags)
        self.assertEqual(len(inspected), 4)
        self.assertEqual(len(inspected.symlinks), 5)
        self.assertEqual(inspected.protected_symlinks(), 5)
        self.assertFalse(self.locked(external / "data.txt"))
        self.assertTrue(self.locked(self.paper))
        self.assertEqual(len(self.engine.journal.records), 4)

    def test_hard_link_is_refused_before_any_mutation(self):
        os.link(self.paper, self.scratch / "external-link")
        with self.assertRaisesRegex(locker.LockerError, "Hard-linked"):
            self.engine.lock([self.root])
        self.assertEqual(self.flags.calls, 0)

    def test_hard_links_contained_in_selection_lock_and_unlock_together(self):
        alias = self.root / "alias.txt"
        os.link(self.paper, alias)
        self.assertEqual(self.engine.lock([self.root]), 4)
        self.assertTrue(self.locked(self.paper))
        self.assertTrue(self.locked(alias))
        self.assertEqual(len(self.engine.journal.records), 5)
        self.reload()
        self.assertEqual(self.engine.unlock([self.root]), 4)
        self.assertFalse(self.locked(self.paper))
        self.assertFalse(self.locked(alias))

    def test_partial_unlock_cannot_make_shared_file_outside_selection_writable(self):
        cache = self.root / "cache"
        cache.mkdir()
        alias = cache / "alias.txt"
        os.link(self.paper, alias)
        self.engine.lock([self.root])
        before = self.flags.calls
        with self.assertRaisesRegex(locker.LockerError, "has 2 hard links"):
            self.engine.unlock([self.project])
        self.assertEqual(self.flags.calls, before)
        self.assertTrue(self.locked(alias))
        self.engine.unlock([self.project, cache])
        self.assertFalse(self.locked(self.paper))
        self.assertFalse(self.locked(alias))
        self.assertTrue(self.locked(self.reference))
        self.assertTrue(self.locked(self.root))

    def test_multiple_selections_can_include_all_hard_link_aliases(self):
        cache = self.root / "cache"
        cache.mkdir()
        os.link(self.paper, cache / "alias.txt")
        self.engine.lock([self.project, cache])
        self.assertTrue(self.locked(self.paper))
        self.assertFalse(self.locked(self.root))
        self.engine.unlock([self.project, cache])
        self.assertFalse(self.locked(self.paper))

    def test_rollback_failure_retains_unvisited_aliases_of_attempted_inodes(self):
        alias = self.root / "alias.txt"
        os.link(self.reference, alias)
        self.flags.fail_calls = {3, 4}
        with self.assertRaisesRegex(locker.LockerError, "rollback was incomplete"):
            self.engine.lock([self.root])
        self.reload()
        self.assertIn(str(self.reference), self.engine.journal.records)
        self.assertIn(str(alias), self.engine.journal.records)
        self.flags.fail_calls.clear()
        self.engine.unlock([self.root])
        self.assertFalse(self.locked(self.reference))

    def test_fifo_is_refused_without_blocking(self):
        os.mkfifo(self.project / "pipe")
        with self.assertRaisesRegex(locker.LockerError, "Unsupported file type"):
            self.engine.lock([self.root])
        self.assertEqual(self.flags.calls, 0)

    def test_all_roots_are_preflighted_before_any_mutation(self):
        separate = self.root / "separate"
        separate.mkdir()
        os.mkfifo(separate / "pipe")
        with self.assertRaisesRegex(locker.LockerError, "Unsupported file type"):
            self.engine.lock([self.project, separate])
        self.assertEqual(self.flags.calls, 0)

    def test_same_device_bind_mount_is_refused(self):
        with patch.object(locker, "mounted_paths", return_value={self.project}):
            with self.assertRaisesRegex(locker.LockerError, "Mounted directories"):
                self.engine.lock([self.root])
            with self.assertRaisesRegex(locker.LockerError, "crosses a mount"):
                self.engine.lock([self.project])
        self.assertEqual(self.flags.calls, 0)

    def test_outside_paths_and_parent_traversal_are_rejected(self):
        for path in (self.scratch, self.root / "..", self.root.parent / "workspace-other"):
            with self.assertRaisesRegex(locker.LockerError, "Outside configured root"):
                self.scope.select([str(path)])

    def test_symlink_in_ancestor_is_rejected(self):
        link = self.root / "alias"
        link.symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(OSError):
            self.scope.select([str(link)])

    def test_overlapping_selections_are_collapsed(self):
        self.assertEqual(self.scope.select([str(self.project), str(self.root), str(self.project)]), [self.root])

    def test_changed_workspace_identity_is_rejected(self):
        self.root.rename(self.scratch / "old-workspace")
        self.root.mkdir()
        with self.assertRaisesRegex(locker.LockerError, "replaced"):
            self.scope.select([str(self.root)])

    def test_status_reports_unverified_when_scan_fails(self):
        os.mkfifo(self.project / "pipe")
        with patch("builtins.print") as output, patch.object(locker, "Flags", return_value=self.flags):
            code = locker.show_status(self.scope, [self.project], as_json=True)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.call_args.args[0])[0]["state"], "UNVERIFIED")

    def test_status_distinguishes_symlink_entries_from_targets(self):
        (self.project / "shortcut").symlink_to(self.scratch / "missing")
        for expected in ("EDITABLE", "LOCKED"):
            with patch("builtins.print") as output, patch.object(locker, "Flags", return_value=self.flags):
                code = locker.show_status(self.scope, [self.project], as_json=True)
            self.assertEqual(code, 0)
            result = json.loads(output.call_args.args[0])[0]
            self.assertEqual(result["state"], expected)
            self.assertEqual(result["symlinks"], 1)
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["protected_symlinks"], int(expected == "LOCKED"))
            self.engine.lock([self.root])

    def test_status_can_inspect_file_with_alias_outside_selection(self):
        os.link(self.paper, self.scratch / "outside-alias")
        with patch("builtins.print") as output, patch.object(locker, "Flags", return_value=self.flags):
            code = locker.show_status(self.scope, [self.project], as_json=True)
        self.assertEqual(code, 0)
        result = json.loads(output.call_args.args[0])[0]
        self.assertEqual(result["state"], "EDITABLE")
        self.assertEqual(result["hardlinks_outside_selection"], 1)

    def status_result(self, **options):
        with patch("builtins.print") as output, patch.object(locker, "Flags", return_value=self.flags):
            code = locker.show_status(self.scope, [self.root], as_json=True, **options)
        self.assertEqual(code, 0)
        return json.loads(output.call_args.args[0])[0]

    def test_status_names_the_unlocked_subtree_without_listing_every_descendant(self):
        self.engine.lock([self.root])
        self.engine.unlock([self.project])
        writes_before = self.flags.calls
        result = self.status_result()
        self.assertEqual(result["state"], "MIXED")
        self.assertEqual(result["locked"], 2)
        self.assertEqual(result["editable"], 2)
        self.assertEqual(len(result["locations"]), 1)
        location = result["locations"][0]
        self.assertEqual(location["path"], str(self.project))
        self.assertTrue(location["recursive"])
        self.assertEqual(location["total"], 2)
        self.assertEqual(self.flags.calls, writes_before)

    def test_status_calls_out_editable_files_using_actual_flags(self):
        self.engine.lock([self.root])
        node = locker.Node.from_stat(self.reference, self.reference.stat())
        locker.set_immutable(node, False, self.flags)
        result = self.status_result()
        self.assertIn(str(self.reference), self.engine.journal.records)
        self.assertEqual([row["path"] for row in result["locations"]], [str(self.reference)])
        self.assertEqual(result["locations"][0]["kind"], "file")
        self.assertFalse(result["locations"][0]["recursive"])

    def test_editable_directory_with_locked_contents_is_never_called_fully_editable(self):
        node = locker.Node.from_stat(self.paper, self.paper.stat())
        locker.set_immutable(node, True, self.flags)
        result = self.status_result(view="editable")
        locations = {row["path"]: row for row in result["locations"]}
        for directory in (self.root, self.project):
            row = locations[str(directory)]
            self.assertFalse(row["recursive"])
            self.assertTrue(row["mixed_contents"])
            self.assertEqual(row["total"], 1)
        self.assertNotIn(str(self.paper), locations)
        self.assertEqual(sum(row["editable"] for row in locations.values()), result["editable"])

    def test_tree_expands_mixed_branches_and_collapses_uniform_folders(self):
        self.engine.lock([self.root])
        self.engine.unlock([self.project])
        result = self.status_result(view="tree")
        locations = {row["path"]: row for row in result["locations"]}
        self.assertEqual(locations[str(self.root)]["state"], "MIXED")
        self.assertEqual(locations[str(self.root)]["self_state"], "LOCKED")
        self.assertEqual(locations[str(self.project)]["state"], "EDITABLE")
        self.assertEqual(locations[str(self.project)]["depth"], 1)
        self.assertEqual(locations[str(self.reference)]["state"], "LOCKED")
        self.assertNotIn(str(self.paper), locations)

    def test_locked_view_accounts_for_every_locked_entry_without_double_counting(self):
        self.engine.lock([self.root])
        self.engine.unlock([self.project])
        result = self.status_result(view="locked")
        self.assertEqual({row["path"] for row in result["locations"]},
                         {str(self.root), str(self.reference)})
        self.assertEqual(sum(row["locked"] for row in result["locations"]), result["locked"])

    def test_uniform_selection_collapses_to_one_location(self):
        result = self.status_result(view="editable")
        self.assertEqual(len(result["locations"]), 1)
        self.assertEqual(result["locations"][0]["path"], str(self.root))
        self.assertEqual(result["locations"][0]["total"], 4)
        self.engine.lock([self.root])
        result = self.status_result(view="locked")
        self.assertEqual(len(result["locations"]), 1)
        self.assertEqual(self.status_result(view="editable")["locations"], [])

    def test_human_status_shows_editable_exception_and_locked_remainder(self):
        self.engine.lock([self.root])
        self.engine.unlock([self.project])
        with patch("builtins.print") as output, patch.object(locker, "Flags", return_value=self.flags):
            locker.show_status(self.scope, [self.root])
        messages = "\n".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn(str(self.project), messages)
        self.assertIn("entire folder; 2 entries", messages)
        self.assertIn("Everything else in this selection is locked.", messages)
        self.assertNotIn(str(self.paper), messages)

    def test_truncation_is_explicit_and_json_still_contains_all_locations(self):
        other = self.root / "other"
        other.mkdir()
        self.engine.lock([self.root])
        self.engine.unlock([self.project, other])
        with patch("builtins.print") as output, patch.object(locker, "Flags", return_value=self.flags):
            locker.show_status(self.scope, [self.root], limit=1)
        messages = "\n".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("1 more locations not shown.", messages)
        self.assertNotIn("Everything else in this selection is locked.", messages)
        self.assertEqual(len(self.status_result(limit=1)["locations"]), 2)

    def test_status_rejects_negative_row_limit_before_accessing_files(self):
        with patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                locker.main(["status", "--limit", "-1"])
        self.assertEqual(error.exception.code, 2)

    def test_symlink_retargeted_during_lock_triggers_rollback(self):
        link = self.project / "shortcut"
        link.symlink_to("paper.txt")
        original_write = self.flags.write
        changed = False

        def change_link(fd, value):
            nonlocal changed
            original_write(fd, value)
            if not changed:
                changed = True
                link.unlink()
                link.symlink_to(self.scratch / "outside")

        self.flags.write = change_link
        with self.assertRaisesRegex(locker.LockerError, "Symlinks changed"):
            self.engine.lock([self.root])
        self.assertFalse(self.engine.journal.records)
        self.assertFalse(self.locked(self.root))

    def test_symlink_replaced_with_regular_file_while_scanning_is_refused(self):
        link = self.project / "shortcut"
        link.symlink_to("paper.txt")
        original_readlink = os.readlink

        def replace_link(path, **kwargs):
            target = original_readlink(path, **kwargs)
            link.unlink()
            link.write_text("different inode")
            return target

        with patch.object(locker.os, "readlink", side_effect=replace_link):
            with self.assertRaisesRegex(locker.LockerError, "Symlink changed"):
                self.engine.lock([self.root])
        self.assertEqual(self.flags.calls, 0)

    def test_failed_journal_write_does_not_change_flags(self):
        with patch.object(self.engine.journal, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.engine.lock([self.root])
        self.assertEqual(self.flags.calls, 0)
        self.assertEqual(self.engine.journal.records, {})

    def test_new_file_during_lock_is_detected_and_changes_rolled_back(self):
        original_write = self.flags.write
        created = False

        def add_during_write(fd, value):
            nonlocal created
            original_write(fd, value)
            if not created:
                created = True
                (self.project / "appeared.txt").write_text("concurrent writer")

        self.flags.write = add_during_write
        with self.assertRaisesRegex(locker.LockerError, "appeared or disappeared"):
            self.engine.lock([self.root])
        self.assertFalse(self.engine.journal.records)

    def test_file_removed_before_its_lock_does_not_leave_unrecoverable_records(self):
        original_write = self.flags.write
        removed = False

        def remove_before_child_is_locked(fd, value):
            nonlocal removed
            original_write(fd, value)
            if not removed:
                removed = True
                self.paper.unlink()

        self.flags.write = remove_before_child_is_locked
        with self.assertRaisesRegex(locker.LockerError, "rolled back"):
            self.engine.lock([self.root])
        self.assertFalse(self.engine.journal.records)
        self.assertFalse(self.locked(self.root))
        self.assertFalse(self.locked(self.project))

    def test_unlock_does_not_touch_an_unselected_directory(self):
        self.engine.lock([self.root])
        outside_selection = self.root / "other"
        outside_selection.mkdir()
        self.assertEqual(self.engine.unlock([outside_selection]), 0)
        self.assertTrue(self.locked(self.root))
        self.assertTrue(self.locked(self.paper))


if __name__ == "__main__":
    unittest.main()
