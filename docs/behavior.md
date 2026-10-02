# Behavior, recovery and removal

## What a lock protects

The immutable flag is an inode attribute enforced by Linux. Folder Locker uses
`FS_IOC_GETFLAGS` and `FS_IOC_SETFLAGS` directly; it does not run `chattr` as a
subprocess. Files and directories keep their ordinary permissions and other
inode flags. No daemon, timer or editor integration is involved.

A locked regular file rejects new writes, deletion and renaming. A locked
directory protects its entries against creation, deletion and renaming. The
tool visits every regular file and directory in a selection to protect existing
contents as well. Unlocking a child leaves its parent and siblings locked.

An unlocked child's contents can change, but moving or deleting the child itself
still requires an editable parent. Unlocking does not grant permissions or
bypass ACLs. Other programs see the same immutable flags.

Recursive lock/unlock is not atomic. Stop concurrent writers first. In
particular, the kernel documents limitations for already-open writable handles;
this tool cannot revoke them. The administrator can explicitly clear immutable
flags. Keep versioned backups for work performed while folders are editable.

## Selection boundaries

The original workspace keeps its configuration and device/inode identity. An
explicit [two-root upgrade](two-root-upgrade.md) can reserve exactly one additional,
disjoint root, with its own identity and journal. Neither registration authorizes
access to their common parent. Commands can select one registered root, a nested
folder, or several disjoint folders within that same root.
Overlapping selections collapse into their common selected ancestor. Paths
outside the configured workspace are refused.

The selected path and its ancestors must not be symlinks. The scanner refuses
special files and nested mounts, including bind mounts on the same device.
Use a narrower selection or a workspace on the intended filesystem. There is
no command for silently replacing a root or adding a third one. An additional
root remains PENDING until the owner provisions it and explicitly activates its
identity. Missing/replaced roots, changed parent identity and mounted aliases are
refused. Problems with the additional root do not prevent commands on the original
root; an active additional root can also operate independently of the original tree.

The running tool, its configuration and its journal cannot be inside a selection
being changed. Use `/usr/local/bin/locker` when managing a folder that contains
the source checkout. Avoid moving the workspace or ancestors of managed folders
while any locks remain.

## Status across roots

Default status retains the original root and stdout contract, with a stderr scope
notice when another registration exists. Explicit `status --all` inspects ACTIVE
roots separately; PENDING is listed as UNVERIFIED without traversal (exit 1).
`roots` lists registration/identity, not actual immutable protection. Read the
[status contract](status.md) before parsing output, especially combined streams.

## Symlinks

Links remain in place, including directory links, broken links and cycles. Their
targets are never traversed. A locked parent protects the link's name against
unlinking, replacement and renaming. An external target retains its own
permissions, including when another program accesses it through the link.

A target physically inside the selected tree is protected via its own real
path. Status lists protected symlink entries separately so it does not imply
that every external target has been locked.

## Hard links

Hard links share a file's inode and flags. A mutating operation must include
every alias of a shared file. For example, select both a package cache and its
environment together, or their common parent:

```bash
sudo locker lock "$locker_workspace/cache" "$locker_workspace/environment"
sudo locker unlock "$locker_workspace/cache" "$locker_workspace/environment"
```

Selecting just one alias is refused before mutation because it would also
change the other aliases. A link outside the configured workspace cannot be
included. Read-only status can inspect partial selections and reports the
shared files separately.

## Interrupted operations

Recovery records are root-owned and stored under `/var/lib/folder-locker`.
They are saved before applying flags. Concurrent locker mutations are serialized
with the same process lock, including administrative upgrade/activation. The
additional root uses `additional/state.json`; its operations never rewrite the
original `state.json`. Files are checked by identity before changing their flags.
Hard-linked aliases spanning roots cannot be mutated through a one-root command.

- A normal locking failure attempts to roll back newly acquired locks.
- If rollback fails, or the process is killed, recovery records remain.
  Run `sudo locker unlock PATH ...` on the same selection, then inspect status.
- If an unlock is interrupted, repeat the same unlock command.
- If a file was replaced or moved, the tool refuses to change a different inode
  at its old path. Investigate the reported path before retrying.
- Immutable flags that existed before Folder Locker acquired an inode are
  preserved. Consequently a successful unlock can still leave pre-existing
  locks visible in status.

Keep the recovery journal while managed locks remain. If it is lost, the tool
cannot distinguish its former locks from pre-existing flags. An administrator
can inspect flags with `lsattr` and use `chattr -i` on individually reviewed
paths. Avoid recursively clearing flags without knowing their origin.

## Compatibility and troubleshooting

`locker doctor` checks that the inode flag interface can be read. Only a
successful `sudo locker self-test --parent PATH` verifies immutable flag changes
and kernel enforcement on that parent's filesystem.

The self-test creates and removes its own scratch tree. It exercises rejected
writes, creation, deletion and renaming; successful reads and copies; symlink
boundaries; hard-link containment; selective unlocking; relocking new files;
and preservation of pre-existing locks. Choose a writable parent on the actual
filesystem of interest. `/tmp` may be a different filesystem from the workspace.

| Message or symptom | What to check |
| --- | --- |
| Missing `CAP_LINUX_IMMUTABLE` | Run with sudo on the host. Containers and sandboxes may remove the capability. |
| `Operation not supported` / inappropriate ioctl | The filesystem may not implement the required flags; run the self-test on the intended filesystem. |
| Symlink ancestor refused | Use a physical path with no symlinks in its ancestors. |
| Hard links outside the selection | Include all aliases in the same command. |
| Nested mount refused | Select a folder that does not cross that mount. |
| `EDITABLE`, but writing fails | Check the parent directory's status, normal permissions, ACLs and mount options. |
| Self-test cannot create files | Use a writable parent on the intended filesystem. |
| Upgrade reports a different scope | Use the workspace shown by `locker doctor`; upgrades cannot switch it. |

## Uninstall

For a two-root installation, do not follow the single-workspace removal commands
below until both roots are separately recovered and their journals inspected. Never
discard an active additional-root registration or journal to downgrade. The bounded
upgrade rollback is only for the pre-provisioning/PENDING checkpoint; see
[two-root upgrade](two-root-upgrade.md).

For a single-workspace installation, first stop other locker operations and release the tool's locks across the
**whole configured workspace**, including any previously selected subfolders.
Set this variable to the scope shown by `locker doctor`:

```bash
locker doctor
locker_workspace="$HOME/projects"
sudo locker unlock "$locker_workspace"
locker status "$locker_workspace"
```

Continue only after the whole-workspace unlock succeeds. If it fails, resolve
the error using the recovery steps above and retain the command, configuration
and journal. A successful unlock leaves pre-existing immutable flags intact.

Then remove the installed command and its metadata:

```bash
sudo rm -- /usr/local/bin/locker
sudo rm -- /etc/folder-locker/config.json
sudo rmdir -- /etc/folder-locker
sudo rm -f -- /var/lib/folder-locker/state.json /var/lib/folder-locker/operation.lock
sudo rmdir -- /var/lib/folder-locker
```

These commands remove installation files only. Your workspace and this source
checkout remain. If a metadata directory contains unexpected files, `rmdir`
refuses to remove it; inspect those files separately.

## Linux references

- [Immutable flags and documented limitations](https://man7.org/linux/man-pages/man1/chattr.1.html)
- [The inode flag ioctl interface](https://man7.org/linux/man-pages/man2/FS_IOC_SETFLAGS.2const.html)
- [Symlink behavior](https://man7.org/linux/man-pages/man7/symlink.7.html)
