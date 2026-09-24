#!/usr/bin/python3 -I
"""Folder Locker: explicit, recursive Linux immutable locks within one workspace."""

from __future__ import annotations

import sys

if sys.platform != "linux":
    raise SystemExit("locker: Linux is required; this tool uses Linux filesystem immutable flags.")
if sys.version_info < (3, 10):
    raise SystemExit("locker: Python 3.10 or newer is required.")

import argparse
import array
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import errno
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile


VERSION = "1.3.0"
CONFIG = Path("/etc/folder-locker/config.json")
STATE_DIR = Path("/var/lib/folder-locker")
IMMUTABLE = 0x10
CAP_LINUX_IMMUTABLE = 9
GETFLAGS = (2 << 30) | (struct.calcsize("l") << 16) | (ord("f") << 8) | 1
SETFLAGS = (1 << 30) | (struct.calcsize("l") << 16) | (ord("f") << 8) | 2
OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


class LockerError(Exception):
    pass


def absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def display(path: Path | str) -> str:
    # Escape control characters in filenames before displaying them in a terminal.
    return json.dumps(str(path), ensure_ascii=True)


@contextmanager
def open_path(path: Path, directory: bool = False):
    """Open every component without following symlinks, including ancestors."""
    fd = os.open("/", OPEN_FLAGS | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            flags = OPEN_FLAGS
            if directory or index < len(path.parts) - 2:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def effective_caps() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("CapEff:"):
            return int(line.split()[1], 16)
    return 0


def require_admin(*, immutable: bool = False) -> None:
    if os.geteuid() != 0:
        raise LockerError("Run this operation with sudo in your own terminal.")
    if immutable and not effective_caps() & (1 << CAP_LINUX_IMMUTABLE):
        raise LockerError(
            "This process lacks CAP_LINUX_IMMUTABLE. Run from the host terminal; "
            "an agent sandbox or container may remove this capability even for root."
        )


class Flags:
    def read(self, fd: int) -> int:
        buffer = array.array("L", [0])
        fcntl.ioctl(fd, GETFLAGS, buffer, True)
        return buffer[0]

    def write(self, fd: int, value: int) -> None:
        buffer = array.array("L", [value])
        fcntl.ioctl(fd, SETFLAGS, buffer, True)


@dataclass(frozen=True)
class Node:
    path: str
    device: int
    inode: int
    directory: bool

    @classmethod
    def from_stat(cls, path: Path, info: os.stat_result) -> Node:
        return cls(str(path), info.st_dev, info.st_ino, stat.S_ISDIR(info.st_mode))


@dataclass(frozen=True)
class Symlink:
    path: str
    device: int
    inode: int
    target: str


class TreeScan(list[tuple[Node, int]]):
    """Flag-bearing entries plus link identities, whose targets are never opened."""

    def __init__(self):
        super().__init__()
        self.symlinks: list[Symlink] = []
        self.hardlink_counts: dict[tuple[int, int], int] = {}
        self.hardlink_paths: dict[tuple[int, int], set[str]] = {}

    def protected_symlinks(self) -> int:
        locked_directories = {node.path for node, flags in self if node.directory and flags & IMMUTABLE}
        return sum(str(Path(link.path).parent) in locked_directories for link in self.symlinks)

    def links_for(self, node: Node) -> int:
        return self.hardlink_counts.get((node.device, node.inode), 1)

    def incomplete_hardlinks(self) -> int:
        return sum(len(self.hardlink_paths[key]) != count for key, count in self.hardlink_counts.items())


@contextmanager
def open_node(node: Node, *, expected_links: int = 1):
    with open_path(Path(node.path), node.directory) as fd:
        current = os.fstat(fd)
        if Node.from_stat(Path(node.path), current) != node:
            raise LockerError(f"Path was replaced; refusing to touch {display(node.path)}.")
        if not node.directory:
            if not stat.S_ISREG(current.st_mode):
                raise LockerError(f"File type changed: {display(node.path)}.")
            if current.st_nlink != expected_links:
                raise LockerError(f"{display(node.path)} has {current.st_nlink} hard links; this selection covers "
                                  f"{expected_links}. Include every linked location in the operation.")
        yield fd


def set_immutable(node: Node, enabled: bool, backend: Flags, *, before_write=None, expected_links: int = 1) -> None:
    with open_node(node, expected_links=expected_links) as fd:
        current = backend.read(fd)
        wanted = current | IMMUTABLE if enabled else current & ~IMMUTABLE
        if current != wanted:
            if before_write is not None:
                before_write(node)
            backend.write(fd, wanted)
        if bool(backend.read(fd) & IMMUTABLE) != enabled:
            raise LockerError(f"Could not verify immutable flag for {display(node.path)}.")


def mounted_paths() -> set[Path]:
    def unescape(value: str) -> str:
        for encoded, decoded in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
            value = value.replace(encoded, decoded)
        return value

    return {Path(unescape(line.split()[4])) for line in Path("/proc/self/mountinfo").read_text().splitlines()}


@dataclass
class Scope:
    root: Path
    identity: Node

    @classmethod
    def create(cls, root: Path) -> Scope:
        with open_path(root, directory=True) as fd:
            return cls(root, Node.from_stat(root, os.fstat(fd)))

    def select(self, paths: list[str], *, protect_tool: bool = True) -> list[Path]:
        with open_node(self.identity):
            pass
        selected: list[Path] = []
        for path in sorted({absolute(value) for value in paths}, key=lambda p: (len(p.parts), str(p))):
            if not inside(path, self.root):
                raise LockerError(f"Outside configured root {display(self.root)}: {display(path)}.")
            if any(inside(path, parent) for parent in selected):
                continue
            for protected in (CONFIG, STATE_DIR, Path(__file__).resolve()) if protect_tool else ():
                if inside(protected, path):
                    raise LockerError(
                        f"{display(path)} contains the running tool or its settings. "
                        "Use the installed /usr/local/bin/locker to manage that folder."
                    )
            with open_path(path, directory=True):
                pass
            selected.append(path)
        return selected


def scan(scope: Scope, roots: list[Path], backend: Flags, *, require_containment: bool = True) -> TreeScan:
    """Preflight the whole selection before any write. Return parents first."""
    mounts = mounted_paths()
    result = TreeScan()

    def walk(path: Path, fd: int) -> None:
        info = os.fstat(fd)
        if info.st_dev != scope.identity.device or (path != scope.root and path in mounts):
            raise LockerError(f"Mounted directories require a separate scope: {display(path)}.")
        if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
            raise LockerError(f"Only regular files and directories are supported: {display(path)}.")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            key = info.st_dev, info.st_ino
            if info.st_nlink < 1 or result.hardlink_counts.get(key, info.st_nlink) != info.st_nlink:
                raise LockerError(f"Hard link count changed during inspection: {display(path)}.")
            result.hardlink_counts[key] = info.st_nlink
            result.hardlink_paths.setdefault(key, set()).add(str(path))
        result.append((Node.from_stat(path, info), backend.read(fd)))
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(fd) as entries:
                names = sorted(entry.name for entry in entries)
            for name in names:
                child_path = path / name
                child_info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(child_info.st_mode):
                    # Linux does not support immutable flags on symlinks. An
                    # immutable parent protects the name against deletion,
                    # replacement and renaming. Never open or traverse the target.
                    target = os.readlink(name, dir_fd=fd)
                    after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if (after.st_dev, after.st_ino, after.st_mode, after.st_ctime_ns) != (
                        child_info.st_dev, child_info.st_ino, child_info.st_mode, child_info.st_ctime_ns
                    ):
                        raise LockerError(f"Symlink changed during inspection: {display(child_path)}.")
                    result.symlinks.append(Symlink(str(child_path), after.st_dev, after.st_ino, target))
                    continue
                if not stat.S_ISREG(child_info.st_mode) and not stat.S_ISDIR(child_info.st_mode):
                    raise LockerError(f"Unsupported file type: {display(child_path)}.")
                flags = OPEN_FLAGS | (os.O_DIRECTORY if stat.S_ISDIR(child_info.st_mode) else 0)
                child = os.open(name, flags, dir_fd=fd)
                try:
                    if (os.fstat(child).st_dev, os.fstat(child).st_ino) != (child_info.st_dev, child_info.st_ino):
                        raise LockerError(f"Folder changed during inspection: {display(child_path)}.")
                    walk(child_path, child)
                finally:
                    os.close(child)

    for root in roots:
        # Also reject a root reached through a bind mount below the configured scope.
        if any(inside(root, mount) and inside(mount, scope.root) and mount != scope.root for mount in mounts):
            raise LockerError(f"Selection crosses a mount below the configured root: {display(root)}.")
        with open_path(root, directory=True) as fd:
            walk(root, fd)
    if require_containment:
        for key, expected in result.hardlink_counts.items():
            paths = result.hardlink_paths[key]
            if len(paths) != expected:
                raise LockerError(f"Hard-linked file has aliases outside this selection: {display(min(paths))} "
                                  f"({len(paths)} of {expected} links included). Select all linked locations together.")
    return result


def atomic_json(path: Path, data: dict, mode: int = 0o600) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        with open_path(path.parent, directory=True) as directory:
            os.fsync(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_trusted(path: Path) -> None:
    for component in [*reversed(path.parents), path]:
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise LockerError(f"Settings must be root-owned and not writable by other users: {display(component)}.")


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.records: dict[str, Node] = {}
        self.pending: str | None = None
        if path.exists():
            data = json.loads(path.read_text())
            if data.get("version") != 1:
                raise LockerError("Unsupported recovery journal version.")
            self.records = {entry["path"]: Node(**entry) for entry in data["entries"]}
            self.pending = data.get("pending")

    def save(self, pending: str | None = None) -> None:
        atomic_json(self.path, {"version": 1, "pending": pending,
                               "entries": [asdict(node) for node in self.records.values()]})
        self.pending = pending


class Locker:
    def __init__(self, scope: Scope, journal: Journal, backend: Flags | None = None):
        self.scope = scope
        self.journal = journal
        self.backend = backend if backend is not None else Flags()

    def lock(self, roots: list[Path]) -> int:
        inspected = scan(self.scope, roots, self.backend)
        previous = self.journal.records.copy()
        previous_pending = self.journal.pending
        changes = []
        for node, flags in inspected:
            recorded = previous.get(node.path)
            if recorded is not None and recorded != node:
                raise LockerError(f"Recovery record points to a replaced file: {display(node.path)}.")
            if not flags & IMMUTABLE:
                changes.append(node)
        # Persist the intended changes BEFORE setting any flags so SIGKILL/power
        # loss cannot leave locks without a recovery record.
        self.journal.records.update({node.path: node for node in changes})
        try:
            self.journal.save("lock")
        except BaseException:
            self.journal.records = previous
            raise
        attempted = []
        try:
            for node in changes:
                set_immutable(node, True, self.backend, before_write=attempted.append,
                              expected_links=inspected.links_for(node))
            verified = scan(self.scope, roots, self.backend)
            if {node for node, _ in verified} != {node for node, _ in inspected}:
                raise LockerError("Files appeared or disappeared while locking. Stop writers and retry.")
            if set(verified.symlinks) != set(inspected.symlinks):
                raise LockerError("Symlinks changed while locking. Stop writers and retry.")
            if verified.hardlink_counts != inspected.hardlink_counts:
                raise LockerError("Hard links changed while locking. Stop writers and retry.")
            if any(not flags & IMMUTABLE for _, flags in verified):
                raise LockerError("Some files remained editable after locking.")
            self.journal.save(previous_pending if previous_pending else None)
        except BaseException as problem:
            failures = []
            for node in reversed(attempted):
                try:
                    set_immutable(node, False, self.backend, expected_links=inspected.links_for(node))
                except (OSError, LockerError) as error:
                    failures.append(f"{display(node.path)}: {error}")
            if failures:
                attempted_inodes = {(node.device, node.inode) for node in attempted}
                self.journal.records = previous | {
                    node.path: node for node in changes if (node.device, node.inode) in attempted_inodes
                }
                self.journal.save("lock failed; unlock the affected selection to recover")
                raise LockerError("Lock failed and rollback was incomplete. Recovery records retained. "
                                  + "; ".join(failures[:3])) from problem
            self.journal.records = previous
            self.journal.save(previous_pending)
            raise LockerError(f"Lock failed; new locks rolled back: {problem}") from problem
        return len(attempted)

    def unlock(self, roots: list[Path]) -> int:
        selected = [node for node in self.journal.records.values()
                    if any(inside(Path(node.path), root) for root in roots)]
        selected.sort(key=lambda node: (len(Path(node.path).parts), node.path), reverse=True)
        link_counts = Counter((node.device, node.inode) for node in selected if not node.directory)
        # Verify every recorded identity before clearing any flags. Existing locks
        # not recorded by this tool are deliberately preserved.
        for node in selected:
            with open_node(node, expected_links=link_counts.get((node.device, node.inode), 1)):
                pass
        if not selected:
            return 0
        self.journal.save("unlock")
        try:
            for node in selected:
                set_immutable(node, False, self.backend, expected_links=link_counts.get((node.device, node.inode), 1))
            for node in selected:
                del self.journal.records[node.path]
            self.journal.save()
        except (OSError, LockerError, KeyboardInterrupt) as problem:
            # Durable records remain in place if interrupted: repeating unlock is safe.
            raise LockerError(f"Unlock incomplete; repeat the same unlock command to recover: {problem}") from problem
        return len({(node.device, node.inode) for node in selected})


def load_scope(override: str | None, *, write: bool = False) -> Scope:
    if CONFIG.exists():
        check_trusted(CONFIG)
        data = json.loads(CONFIG.read_text())
        if data.get("version") != 1:
            raise LockerError("Unsupported configuration version.")
        root = Path(data["root"])
        if override and absolute(override) != root:
            raise LockerError("--root cannot override the installed scope.")
        return Scope(root, Node(**data["identity"]))
    if write:
        raise LockerError("Install and initialize the tool first; see README.md.")
    if not override:
        raise LockerError("No workspace configured. Use --root /absolute/workspace/path for previews, "
                          "or install with: sudo ./install.sh /absolute/workspace/path")
    return Scope.create(absolute(override))


@contextmanager
def operation_journal():
    require_admin()
    check_trusted(STATE_DIR)
    fd = os.open(STATE_DIR / "operation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LockerError("Another locker operation is running.") from error
        state_path = STATE_DIR / "state.json"
        if state_path.exists() or state_path.is_symlink():
            check_trusted(state_path)
        yield Journal(state_path)
    finally:
        os.close(fd)


def initialize(root: Path) -> None:
    require_admin()
    if root == Path("/") or len(root.parts) < 3:
        raise LockerError("Choose a dedicated workspace, such as /srv/projects or a projects folder in your home.")
    for protected in (CONFIG, STATE_DIR, Path(sys.executable), Path(__file__).resolve()):
        if inside(protected, root):
            raise LockerError("The scope must not contain the installed tool, Python, or system settings.")
    scope = Scope.create(root)
    if CONFIG.exists() or CONFIG.is_symlink():
        existing = load_scope(str(root), write=True)
        if existing.identity != scope.identity:
            raise LockerError("Configured workspace identity changed. Inspect it before reconfiguring.")
        print(f"Already configured for {display(root)}.")
        return
    for directory, mode in ((CONFIG.parent, 0o755), (STATE_DIR, 0o700)):
        check_trusted(directory.parent)
        directory.mkdir(mode=mode, exist_ok=True)
        check_trusted(directory)
    atomic_json(CONFIG, {"version": 1, "root": str(root), "identity": asdict(scope.identity)}, 0o644)
    print(f"Configured scope: {display(root)}. No folders have been locked.")


@dataclass(slots=True)
class StatusBranch:
    node: Node
    self_locked: bool
    locked: int
    total: int = 1
    children: list[str] | None = None

    @property
    def state(self) -> str:
        return "LOCKED" if self.locked == self.total else "EDITABLE" if self.locked == 0 else "MIXED"


def status_locations(inspected: TreeScan, root: Path, view: str) -> list[dict]:
    """Collapse uniform subtrees and expand only branches whose states differ."""
    branches = {
        node.path: StatusBranch(node, bool(flags & IMMUTABLE), int(bool(flags & IMMUTABLE)),
                                children=[] if node.directory else None)
        for node, flags in inspected
    }
    for branch in branches.values():
        parent = branches.get(branch.node.path.rpartition("/")[0] or "/")
        if parent is not None:
            parent.children.append(branch.node.path)
    for branch in reversed(list(branches.values())):
        parent = branches.get(branch.node.path.rpartition("/")[0] or "/")
        if parent is not None:
            parent.locked += branch.locked
            parent.total += branch.total

    wanted = "LOCKED" if view == "locked" else "EDITABLE"
    locations = []
    pending = [(str(root), 0)]
    while pending:
        path, depth = pending.pop()
        branch = branches[path]
        state = branch.state
        own_state = "LOCKED" if branch.self_locked else "EDITABLE"
        if view == "tree" or state == wanted or (state == "MIXED" and own_state == wanted):
            only_self = view != "tree" and state == "MIXED"
            locked = int(branch.self_locked) if only_self else branch.locked
            total = 1 if only_self else branch.total
            locations.append({
                "path": path,
                "kind": "directory" if branch.node.directory else "file",
                "state": own_state if only_self else state,
                "self_state": own_state,
                "recursive": branch.node.directory and state != "MIXED",
                "mixed_contents": state == "MIXED",
                "locked": locked,
                "editable": total - locked,
                "total": total,
                "depth": depth,
            })
        if state == "MIXED":
            pending.extend((child, depth + 1) for child in reversed(branch.children or []))
    return locations


def print_status_locations(result: dict, view: str, limit: int) -> None:
    if view == "default" and result["state"] != "MIXED":
        description = "locked" if result["state"] == "LOCKED" else "editable"
        print(f"  All files and folders in this selection are {description}.")
        return
    rows = result["locations"]
    if view == "tree":
        print("  Folder tree (uniform subfolders are collapsed):")
        rows = rows[1:]  # The selected root is already printed as the heading.
    else:
        label = "Locked" if view == "locked" else "Editable"
        print(f"  {label} locations:")
    if not rows:
        print("    None." if view != "tree" else "    The entire selection has the state shown above.")
    visible = rows if limit == 0 else rows[:limit]
    for row in visible:
        if view == "tree":
            label = Path(row["path"]).name + ("/" if row["kind"] == "directory" else "")
            indent = "  " * row["depth"]
        else:
            label = row["path"]
            indent = "    "
        if row["mixed_contents"]:
            note = (f"folder itself {row['self_state']}; contents vary" if view == "tree"
                    else "folder itself only; some contents have a different status")
        elif row["recursive"]:
            note = f"entire folder; {row['total']} entries"
        else:
            note = "file"
        print(f"{indent}{row['state']:<10} {display(label)} ({note})")
    omitted = len(rows) - len(visible)
    if omitted:
        print(f"    {omitted} more locations not shown. Use --limit 0 to show all.")
    elif view == "default":
        print("  Everything else in this selection is locked.")


def show_status(scope: Scope, roots: list[Path], *, as_json: bool = False,
                view: str = "default", limit: int = 50) -> int:
    results = []
    failed = False
    for root in roots:
        try:
            inspected = scan(scope, [root], Flags(), require_containment=False)
            locked = sum(bool(flags & IMMUTABLE) for _, flags in inspected)
            total = len(inspected)
            state = "LOCKED" if locked == total else "EDITABLE" if locked == 0 else "MIXED"
            results.append({"path": str(root), "state": state, "locked": locked, "total": total,
                            "editable": total - locked,
                            "view": "editable" if view == "default" else view,
                            "locations": status_locations(inspected, root, view),
                            "symlinks": len(inspected.symlinks),
                            "protected_symlinks": inspected.protected_symlinks(),
                            "hardlinked_files": len(inspected.hardlink_counts),
                            "hardlinks_outside_selection": inspected.incomplete_hardlinks()})
        except (LockerError, OSError) as error:
            failed = True
            results.append({"path": str(root), "state": "UNVERIFIED", "error": str(error)})
    if as_json:
        print(json.dumps(results, indent=2, ensure_ascii=True))
    else:
        for result in results:
            if "error" in result:
                print(f"UNVERIFIED {display(result['path'])}: {result['error']}")
            else:
                print(f"{result['state']:<10} {display(result['path'])} "
                      f"({result['locked']} locked, {result['editable']} editable files/directories)")
                print_status_locations(result, view, limit)
                if result["symlinks"]:
                    print(f"  Symlinks: {result['protected_symlinks']}/{result['symlinks']} entries protected "
                          "by their parents; targets not followed or changed.")
                if result["hardlinks_outside_selection"]:
                    print(f"  {result['hardlinks_outside_selection']} shared files have hard links outside this "
                          "selection. Lock/unlock must include every linked location.")
    return 1 if failed else 0


def self_test(parent: Path) -> None:
    """Exercise the actual kernel on disposable files, including a writable subtree."""
    require_admin(immutable=True)
    with tempfile.TemporaryDirectory(prefix=".locker-selftest-", dir=parent) as temporary:
        scratch = Path(temporary)
        root = scratch / "workspace"
        work = root / "project"
        work.mkdir(parents=True)
        reference = root / "reference.txt"
        reference.write_text("reference\n")
        paper = work / "paper.txt"
        paper.write_text("draft\n")
        preexisting = root / "already-locked.txt"
        preexisting.write_text("preserve my original lock\n")
        external = scratch / "external.txt"
        external.write_text("external target\n")
        link = work / "external-link"
        link.symlink_to(external)
        cache = root / "cache"
        environment = root / "environment"
        cache.mkdir()
        environment.mkdir()
        shared = cache / "package.txt"
        shared.write_text("shared package\n")
        os.link(shared, environment / "package.txt")
        scope = Scope.create(root)
        backend = Flags()
        journal = Journal(scratch / "journal.json")
        locker = Locker(scope, journal, backend)
        original = Node.from_stat(preexisting, preexisting.stat())

        def denied(action, label: str) -> None:
            try:
                action()
            except OSError as error:
                if error.errno not in (errno.EPERM, errno.EACCES, errno.EROFS):
                    raise
            else:
                raise LockerError(f"Kernel test failed: {label} was allowed.")

        try:
            set_immutable(original, True, backend)
            locker.lock([root])
            denied(lambda: reference.write_text("changed"), "write")
            denied(lambda: reference.unlink(), "delete")
            denied(lambda: reference.rename(root / "moved.txt"), "rename")
            denied(lambda: (root / "new.txt").write_text("new"), "creation")
            denied(lambda: link.unlink(), "symlink deletion")
            denied(lambda: link.rename(work / "renamed-link"), "symlink rename")
            replacement = scratch / "replacement-link"
            replacement.symlink_to(reference)
            denied(lambda: replacement.replace(link), "symlink replacement")
            external.write_text("external target remains editable\n")
            if link.read_text() != "external target remains editable\n":
                raise LockerError("Reading through a protected symlink failed.")
            if reference.read_text() != "reference\n":
                raise LockerError("Reading locked files failed.")
            shutil.copytree(root, scratch / "backup", symlinks=True)
            (scratch / "backup" / "reference.txt").write_text("writable copy\n")
            print("PASS: locked files reject writes, deletion, renaming and creation; reading and copying work.")
            print("PASS: symlink names are protected while external targets remain editable.")
            try:
                locker.unlock([cache])
            except LockerError as error:
                if "hard links" not in str(error):
                    raise
            else:
                raise LockerError("A partial unlock of a shared file was incorrectly allowed.")
            denied(lambda: shared.write_text("changed"), "write after refused partial unlock")
            locker.unlock([cache, environment])
            shared.write_text("shared package is editable\n")
            locker.lock([cache, environment])
            print("PASS: hard links are supported together and partial unlocks cannot weaken another folder.")
            # Reload from disk to exercise recovery records across invocations.
            locker = Locker(scope, Journal(scratch / "journal.json"), backend)
            locker.unlock([work])
            paper.write_text("revised\n")
            link.unlink()
            link.symlink_to(external)
            (work / "new.txt").write_text("new\n")
            denied(lambda: reference.write_text("changed"), "write outside unlocked subtree")
            print("PASS: a nested folder becomes editable while the surrounding tree remains locked.")
            locker.lock([work])
            denied(lambda: (work / "new.txt").unlink(), "deletion of newly locked file")
            locker.unlock([root])
            reference.write_text("editable again\n")
            denied(lambda: preexisting.write_text("changed"), "write to pre-existing immutable file")
            print("PASS: relocking includes new files; unlocking preserves pre-existing locks.")
        finally:
            # Only our disposable tree is touched, including the test's original lock.
            inspected = scan(scope, [root], backend)
            for node, _ in reversed(inspected):
                set_immutable(node, False, backend, expected_links=inspected.links_for(node))
    print("Kernel self-test passed; disposable files removed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"Folder Locker {VERSION}")
    parser.add_argument("--root", help="Workspace for previews/initialization; cannot override installed scope")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize the root-owned configuration (installer uses this)")
    commands.add_parser("doctor", help="Check scope, filesystem flag support and process privileges")
    for action in ("lock", "unlock"):
        command = commands.add_parser(action, help=f"Recursively {action} one or more directories")
        command.add_argument("paths", nargs="+")
        command.add_argument("--dry-run", action="store_true", help="Inspect without changing attributes")
    command = commands.add_parser("status", help="Verify actual immutable flags recursively")
    command.add_argument("paths", nargs="*")
    command.add_argument("--json", action="store_true")
    views = command.add_mutually_exclusive_group()
    views.add_argument("--editable", "--unlocked", dest="view", action="store_const", const="editable",
                       help="List editable folders/files, collapsing fully editable subfolders")
    views.add_argument("--locked", dest="view", action="store_const", const="locked",
                       help="List locked folders/files, collapsing fully locked subfolders")
    views.add_argument("--tree", dest="view", action="store_const", const="tree",
                       help="Show a directory tree, expanding mixed folders")
    command.add_argument("--limit", type=int, default=50,
                         help="Maximum detail rows per selection; 0 lists all (default: 50; JSON is unlimited)")
    command.set_defaults(view="default")
    command = commands.add_parser("self-test", help="Test kernel enforcement on disposable files (sudo required)")
    command.add_argument("--parent", required=True, help="Writable directory for disposable test files")
    args = parser.parse_args(argv)
    if args.command == "status" and args.limit < 0:
        parser.error("--limit must be zero or positive")
    if args.command == "init" and not args.root:
        parser.error("--root PATH is required for init")
    try:
        if args.command == "init":
            initialize(absolute(args.root))
            return 0
        if args.command == "self-test":
            self_test(absolute(args.parent))
            return 0
        write = args.command in ("lock", "unlock") and not args.dry_run
        if write:
            require_admin(immutable=True)
        scope = load_scope(args.root, write=write)
        if args.command == "doctor":
            with open_path(scope.root, directory=True) as fd:
                flags = Flags().read(fd)
            print(f"Scope: {display(scope.root)}")
            print(f"Installed configuration: {'yes' if CONFIG.exists() else 'no (preview only)'}")
            print(f"Filesystem inode flags: supported (current value {flags:#x})")
            print(f"Can change immutable flags in this process: "
                  f"{'yes' if effective_caps() & (1 << CAP_LINUX_IMMUTABLE) else 'no; host sudo required'}")
            return 0
        roots = scope.select(args.paths or [str(scope.root)], protect_tool=write)
        if args.command == "status":
            return show_status(scope, roots, as_json=args.json, view=args.view, limit=args.limit)
        if args.command == "lock" and args.dry_run:
            inspected = scan(scope, roots, Flags())
            count = sum(not flags & IMMUTABLE for _, flags in inspected)
            print(f"Would lock {count} of {len(inspected)} entries recursively. No attributes changed.")
            if inspected.symlinks:
                print(f"Would protect {len(inspected.symlinks)} symlink entries through their parents; "
                      "targets will not be followed or changed.")
            if inspected.hardlink_counts:
                print(f"All aliases of {len(inspected.hardlink_counts)} hard-linked files are inside the selection.")
            for root in roots:
                print(f"  {display(root)}")
            return 0
        with operation_journal() as journal:
            if args.command == "unlock" and args.dry_run:
                count = sum(any(inside(Path(path), root) for root in roots) for path in journal.records)
                print(f"Would release {count} recorded locks. Pre-existing locks stay in place.")
                return 0
            locker = Locker(scope, journal)
            if args.command == "lock":
                count = locker.lock(roots)
                print(f"Verified LOCKED. Added {count} immutable flags.")
                print("Symlink entries are protected by their parents; link targets retain their own permissions.")
            else:
                count = locker.unlock(roots)
                print(f"Released {count} locks created by locker. Pre-existing locks stay in place.")
        return 0
    except (LockerError, OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        print(f"locker: {str(error)!a}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("locker: interrupted. Check status; repeat unlock if recovery is needed.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
