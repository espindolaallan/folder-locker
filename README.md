# Folder Locker

Protect Linux folders from accidental edits and deletion, with selective
unlocking and clear status reporting.

Folder Locker provides the `locker` command. It uses Linux's immutable inode
flag, the same protection exposed by `chattr +i`. Your editor, terminal, coding
agents and other programs all encounter the same filesystem protection. Files
remain readable and can be copied to a writable destination.

- Lock a workspace and unlock just the project you are working on.
- Inspect actual locks with summaries, a tree view or JSON.
- Preview a lock before applying it.
- Keep recovery records and preserve immutable flags that existed beforehand.
- Run with the Python standard library; no service or Python packages required.

## Requirements

- **Linux.** Windows and macOS are not supported.
- **Python 3.10 or newer at `/usr/bin/python3`.** The installed command uses this
  interpreter in isolated mode, independently of virtual environments.
- **A filesystem that supports immutable flags.** Kernel enforcement has been
  exercised on Linux x86-64 with ext4. Other filesystems and architectures need
  verification with the self-test on the intended filesystem.
- **Administrator access** for installation, locking, unlocking and the kernel
  self-test. The process needs `CAP_LINUX_IMMUTABLE`; containers and sandboxes can
  withhold it even from root.

One installation manages **one configured workspace**, with any number of
selected folders inside it. Selected paths and their ancestors must be real
directories, rather than symlinks. Nested mounts and special files are refused.
Read [behavior and recovery](docs/behavior.md) for the details of these boundaries.

## Install

Clone this repository and choose an existing folder to manage;
`$HOME/projects` below is only an example.

```bash
git clone https://github.com/espindolaallan/folder-locker.git
cd folder-locker
locker_workspace="$HOME/projects"

# Read-only checks before installation.
./locker.py --root "$locker_workspace" doctor
./locker.py --root "$locker_workspace" lock "$locker_workspace" --dry-run

# Install the command and record the chosen workspace. This applies no locks.
sudo ./install.sh "$locker_workspace"

# Test the filesystem using disposable files, before locking the workspace.
sudo locker self-test --parent "$locker_workspace"
locker status
```

If the workspace does not exist yet, create it first. Quote paths containing
spaces. Use a dedicated folder, rather than a system directory.

The installer stores the command at `/usr/local/bin/locker`, configuration at
`/etc/folder-locker/config.json`, and private recovery records under
`/var/lib/folder-locker`. It does not change sudo policy or start a background
service. Installation requires common Linux utilities such as `install`,
`mktemp` and GNU `mv`.

If `locker` is not found after installation, add `/usr/local/bin` to your shell's
`PATH`, or run `/usr/local/bin/locker` directly.

## Lock a workspace; unlock one project

Assume `project-a` already exists within your configured workspace. Close
programs writing there before applying locks.

```bash
locker_workspace="$HOME/projects"

locker lock "$locker_workspace" --dry-run
sudo locker lock "$locker_workspace"

sudo locker unlock "$locker_workspace/project-a"
locker status

# When finished, include any newly created files in the lock.
sudo locker lock "$locker_workspace/project-a"
```

An illustrative status after unlocking one project:

```text
MIXED "/home/alex/projects" (12 locked, 3 editable files/directories)
  Editable locations:
    EDITABLE "/home/alex/projects/project-a" (entire folder; 3 entries)
  Everything else in this selection is locked.
```

Unlocking a folder makes its contents editable by programs that already have
write permission. If its parent stays locked, the folder itself cannot be
removed, moved or renamed. Locking a parent again also relocks its previously
unlocked descendants.

You can manage several selected folders together:

```bash
sudo locker lock "$locker_workspace/project-a" "$locker_workspace/project-b"
sudo locker unlock "$locker_workspace/project-a" "$locker_workspace/project-b"
```

## Inspect locks

```bash
locker status                    # Name editable exceptions in the workspace
locker status --editable         # Editable locations; --unlocked is an alias
locker status --locked           # Locked locations
locker status --tree             # Expand mixed folders in a compact tree
locker status --tree --limit 0   # Show all detail rows
locker status "$locker_workspace/project-a" --json
```

Uniform subfolders collapse into one row covering their contents. A mixed folder
can have a different flag from its children; filtered lists label such an entry
as "folder itself only". Detail output defaults to 50 rows per selection and
explicitly reports omitted locations. JSON always contains all matching locations.

| State | Meaning |
| --- | --- |
| `LOCKED` | Every inspected regular file and directory has its immutable flag set. |
| `EDITABLE` | None has its immutable flag set; normal permissions or mounts can still restrict access. |
| `MIXED` | Both locked and editable entries exist; the output identifies their locations. |
| `UNVERIFIED` | Inspection failed; the command exits unsuccessfully. |

Symlinks are counted separately. Their names are protected by locked parents;
targets are never followed or changed through the link. A large workspace takes
longer to inspect because status reads the filesystem rather than trusting the
recovery journal.

## Commands

| Command | Purpose |
| --- | --- |
| `locker doctor` | Inspect the scope, inode-flag interface and current capability. |
| `locker status [PATH ...]` | Inspect the selected directories, or the configured workspace. |
| `locker lock PATH ... --dry-run` | Preflight the selection without changing flags. |
| `sudo locker lock PATH ...` | Record, apply and verify recursive locks. |
| `sudo locker unlock PATH ... --dry-run` | Count selected locks in the private recovery journal. |
| `sudo locker unlock PATH ...` | Release locks acquired by this tool. |
| `sudo locker self-test --parent PATH` | Exercise real kernel protection on disposable files. |
| `locker --version` | Print the installed version. |

Before installation, previews require `./locker.py --root PATH ...`. Once
configured, `--root` cannot redirect the installation to a different workspace.
The self-test always requires an explicit, writable parent on the filesystem
you want to test. It cannot create its scratch directory inside a locked parent.

## Upgrade and remove

From the new version's checkout, pass the **existing** workspace:

```bash
sudo ./install.sh --upgrade "$locker_workspace"
locker --version
locker status
```

Upgrades preserve the workspace configuration, recovery journal and locks.
Workspace validation happens before replacing the installed command. If your
source checkout is locked, extract the new version into a writable location.

For removal, first release every lock acquired by the tool. Follow the complete
[uninstall instructions](docs/behavior.md#uninstall); keep recovery records until
unlocking succeeds.

## Limits and recovery

This tool protects against accidental filesystem changes. An administrator with
the required capability can deliberately remove immutable flags. Protection
persists without a running Python process, but recursive operations are not
atomic and already-open writable handles have kernel-specific caveats. Stop
writers before locking and keep versioned backups.

Reads and one-way copying out of a locked tree work. Operations that delete or
change source files or metadata, and syncs that write back into the source, can
fail. Ordinary `rsync` copying does not carry the immutable flag to the copy.

See [behavior and recovery](docs/behavior.md) for symlinks, hard links, interrupted
operations, journal handling and troubleshooting.

## Development

```bash
python3 -B -m unittest discover -s tests -v
sh -n install.sh
```

Unit tests use disposable directories and simulated flag operations. The
separate privileged self-test verifies kernel enforcement. GitHub Actions is
configured to run unit tests on Python 3.10 through 3.14 and the kernel self-test
on Ubuntu. See [contributing](CONTRIBUTING.md) and the [changelog](CHANGELOG.md).

## License

[MIT](LICENSE).
