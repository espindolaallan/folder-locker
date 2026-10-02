# Contributing

Keep changes focused on predictable filesystem protection and understandable
commands. Describe the problem, the resulting behavior and how you verified it.
Use disposable examples in bug reports; replace private directory names and
file contents before sharing logs or JSON status output.

## Local checks

Development requires Linux and Python 3.10 or newer. No third-party Python
packages are needed for the tests:

```bash
python3 -B -m unittest discover -s tests -v
sh -n install.sh
./locker.py --help
```

Unit tests use temporary files and a simulated flag backend. Installer tests run
in a temporary prefix with simulated administrator commands. Neither changes
system configuration or real immutable flags.

Changes to locking, traversal or recovery also need the kernel self-test on a
writable parent on the target filesystem:

```bash
sudo ./locker.py self-test --parent /absolute/writable/test-parent
```

This operates on its own disposable files and requires `CAP_LINUX_IMMUTABLE`.
Do not substitute a test that skips a failure of kernel enforcement. Report the
Python version, architecture, kernel and filesystem used for verification.

## Behavior to preserve

- Preflight the complete selection before applying flags.
- Never follow symlink targets or silently cross mount boundaries.
- Include every alias when changing a hard-linked inode.
- Persist recovery records before mutations and retain them on incomplete recovery.
- Preserve pre-existing immutable flags and other inode attributes.
- Verify identities before mutations; do not silently act on replaced files.
- Base status on observed filesystem flags and explain mixed results.
- Require an explicit selection for every lock and unlock.

Add regression tests when changing these behaviors. Keep documentation and the
changelog aligned with the final implementation.

GitHub Actions uses a read-only repository token and pinned action revisions.
Unit tests cover the declared Python versions; a separate Ubuntu job runs the
kernel self-test. Successful unit tests alone do not establish filesystem support.

Contributions are made under the project's [MIT license](LICENSE).

## Releases

Review the exact commit and public artifacts before publishing. Follow
[the release procedure](docs/releasing.md); tests do not authorize pushing,
creating remote tags or publishing a GitHub Release.
