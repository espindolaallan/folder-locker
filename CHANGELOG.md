# Changelog

Entries describe source versions; publication is represented by Git tags and GitHub
Releases. See [the release procedure](docs/releasing.md).

## 1.4.0

- Add one explicitly reserved additional root, without rewriting primary config
  or journal; separate root identities and recovery records, one root per command.
- Keep an absent root PENDING until separate owner provisioning and activation.
- Reject third/common-parent scopes, mixed selections, root/parent replacement,
  mounted activation aliases and wrong-root journal records.
- Add a bounded 1.3.0 upgrade with exact-state backups, protection comparison,
  automatic failure rollback and an explicit pre-provisioning rollback command.
- Keep default status stdout/JSON and root selection unchanged. A stderr notice
  makes additional ACTIVE/PENDING registrations visible without scanning them.
- Add explicit `status --all [--json]`, with per-root registration/default labels,
  PENDING/UNVERIFIED reporting and nonzero exit for incomplete global inspection.
- Isolate legacy CLI tests from installed additional-root metadata. Test text/JSON
  compatibility, explicit paths, single/multiple roots and ordinary upgrade identity.
- Unit tests cover routing, independent histories, failures and recovery without
  administrator access or real immutable flag writes. Kernel self-test remains
  a separate administrator validation.

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
