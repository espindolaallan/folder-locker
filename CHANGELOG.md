# Changelog

## 1.3.0 (unreleased)

- Document workspace configuration with examples that apply to any installation.
- Require an explicit `--root` for initialization and unconfigured previews.
  Existing installations continue using their recorded workspace.
- Require an explicit `--parent` for the disposable kernel self-test.
- Report unsupported platforms and Python versions with clear startup errors.
- Validate the workspace before replacing an existing installed command, and
  reject relative installer paths.
- Add installation, upgrade, recovery, removal and contribution documentation,
  plus an MIT license.
- Add GitHub Actions unit tests for Python 3.10–3.14 and a separate privileged
  kernel self-test on Ubuntu. Add regression tests for workspace selection and
  installer failure handling.

The configuration and recovery journal remain at schema version 1. Locks and
selective unlocks remain compatible with 1.2.0. Run the existing upgrade command
with the same workspace to install this version.

## 1.2.0

- Name editable exceptions when status is mixed.
- Add filtered status, a tree view, output limits and detailed JSON locations.

## 1.1.0

- Preserve symlinks without traversing their targets.
- Support hard links when all aliases are included in an operation.
- Add explicit upgrades that preserve configuration and recovery records.

## 1.0.0

- Introduce recursive locking, selective unlocking, scope boundaries, preflight,
  recovery records, rollback, status and a disposable kernel self-test.
