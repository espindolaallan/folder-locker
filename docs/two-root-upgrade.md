# Bounded two-root upgrade

This upgrade reserves one additional, disjoint root while preserving the original
configuration, inode identity, recovery history and protection. It is specifically
for the reviewed 1.3.0 executable; another installed build is refused. Normal
single-root installations and the original default selection remain supported.

## Boundary and states

There are at most two registered roots. Each has its own identity and recovery
journal. The original `config.json` and `state.json` stay in place, byte for byte,
with their inode, mode, owner and modification time checked. A sidecar binds the
additional root to the exact original configuration and its physical parent.
The common parent is not a managed scope. No third-root command exists.

- `PENDING`: the path is reserved, initially absent. Status and mutations for that
  root fail closed. Creating a directory there does not activate it automatically.
- `ACTIVE`: a separately provisioned empty directory has been explicitly bound by
  device/inode identity. This is registration status, **not lock status**. Activation
  performs no flag writes; use `status` to inspect actual protection.

An explicit path chooses its own registered root. `--root` can select only an exact
registration. With no selection, commands retain the original default. Mixed-root
commands are refused; operations on either root use a separate journal under the
same process lock. Invalid or pending additional registration does not disable the
original root. Hard links spanning roots cannot be mutated through a partial
selection. Symlink, nested-mount, replacement and recovery checks remain in force.

## Install before creating the additional root

Use the actual absolute paths approved for your host. In this example the existing
root is `/home/alex/code`, and `/home/alex/library` must still be absent. Do not
create it for the upgrade. Keep writers and other Locker operations stopped during
installation and validation so the protection baseline remains comparable.

From the reviewed source checkout:

```bash
sudo ./install.sh --upgrade --reserve-root /home/alex/library /home/alex/code
```

The installer refuses a different legacy root, unreviewed installed executable,
existing additional registration/state, pending primary recovery, existing new
root, symlink parent, overlapping roots or a previous upgrade checkpoint. It reads
protection metadata across the original scope; it does not read workspace file
contents. Unsupported entries or an unreadable baseline stop installation.

The administrative changes are limited to:

| Path | Change |
| --- | --- |
| `/usr/local/bin/locker` | Atomically replace the reviewed 1.3.0 with 1.4.0, mode 0755. |
| `/etc/folder-locker/additional-root.json` | Reserve the one exact additional path, mode 0644, identity null. |
| `/var/lib/folder-locker/additional/` | Empty, private journal directory, mode 0700. |
| `/var/lib/folder-locker/upgrades/two-roots-1.4.0/` | Private checkpoint with old executable, original config/journal copies, protection baseline, manifest, validation/rollback code and result. |

The existing global operation lock serializes the upgrade. There is no workspace
creation, chmod or immutable-flag mutation, service installation, journal migration,
original-config rewrite, or widening to the common parent. The private baseline
includes path/inode/type, permissions, ownership, flags and symlink metadata. It is
kept outside Git, like the journals; do not publish it as a generic test report.

## Validate this checkpoint

Run immediately after installation, before any new lock/unlock, provisioning or
other changes to the original workspace:

```bash
sudo /usr/bin/python3 -IB /var/lib/folder-locker/upgrades/two-roots-1.4.0/upgrade.py validate
locker --version
locker roots --json
locker status /home/alex/library
```

Validation must report PASS; the version must be 1.4.0. The two registrations must
be exactly `code` ACTIVE/default and `library` PENDING. The final `status` command
must fail with PENDING and exit 1, because that root has no authority yet.
`validate` also proves the new root is absent, the new journal empty, the original
configuration/journal unchanged, and every observed legacy identity, permission,
owner, flag and symlink unchanged. Existing editable exceptions stay editable.
The installer runs this same validation before reporting success.

Unit tests use simulated flags and administrative files in temporary directories.
They do not replace the real kernel self-test required by CONTRIBUTING.md. After
installation, the owner should run the installed self-test on an already editable
parent on the original filesystem, for example an unlocked source checkout:

```bash
sudo locker self-test --parent /home/alex/code/folder-locker
```

It changes flags only on its own disposable scratch tree and cleans that tree.
It does not add a registered root. If the chosen parent is locked, do not broaden
an unlock for this recipe; choose an already editable parent or review the smallest
needed exception. A later canonical-root activation needs its own status/identity
check and, if on a different filesystem, kernel-support validation there.

## Rollback before provisioning

Ordinary validation/install errors attempt this rollback automatically. After an
interruption, or to revert a completed PENDING upgrade, run the preserved code:

```bash
sudo /usr/bin/python3 -IB /var/lib/folder-locker/upgrades/two-roots-1.4.0/upgrade.py rollback
locker --version
```

Expect version 1.3.0. Rollback verifies that the original config/journal are still
identical, that the additional root remains absent, its state directory empty and
its registration unactivated. It restores the exact original executable and its
mode/mtime, removes only the pending sidecar and empty additional journal directory,
and retains the private checkpoint/result. It never rewrites the primary journal,
configuration, workspace data or flags. Unknown executable/control changes cause
refusal, not an attempted repair.

This rollback is deliberately unavailable after provisioning/activation or changes
to the primary config/journal. Stop and inspect rather than discarding recovery
history. If installation failed before `manifest.json` was saved, installed control
files were not changed; retain any partial checkpoint for inspection. A previous
checkpoint is never overwritten, including after rollback: review/archive it
separately before a retry. Do not downgrade an active two-root installation with
the ordinary single-root installer.

## Later provisioning is a separate owner action

After accepting the installed checkpoint, the owner may separately provision the
reserved path as an empty real directory, then run `sudo locker activate-root PATH`.
Activation requires the exact reserved path, unchanged physical parent, no mounted
alias and an empty directory on its parent's filesystem. It records the new inode;
it does not create the directory, change permissions or lock it. Inspect `roots`,
`--root PATH doctor` and `status PATH`, then establish protection explicitly before
using it for library migrations. That later step is not part of this installation.
