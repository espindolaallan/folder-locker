#!/usr/bin/python3 -IB
"""Bounded 1.3.0 -> 1.4.0 upgrade: preserve primary state, reserve one absent root.

No immutable flag writes, workspace creation, journal migration, sudo or subprocess.
Administrative execution is deliberately left to the owner.
"""
import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

sys.dont_write_bytecode = True
PREVIOUS_SHA256 = 'af8472848f339b887ccf5069d3011ad0faf278eb3b6cc03f19e2096e36ef2328'


@dataclass
class Paths:
    executable: Path = Path('/usr/local/bin/locker')
    config: Path = Path('/etc/folder-locker/config.json')
    secondary: Path = Path('/etc/folder-locker/additional-root.json')
    state: Path = Path('/var/lib/folder-locker')

    @property
    def backup(self):
        return self.state / 'upgrades' / 'two-roots-1.4.0'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_tool(source):
    spec = importlib.util.spec_from_file_location('locker_upgrade_api', source)
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
    return api


class Upgrade:
    def __init__(self, source, paths=None):
        self.source = Path(source)
        self.paths = paths or Paths()  # Injection for disposable tests only; no CLI prefix option.
        self.api = load_tool(self.source)
        self.api.CONFIG = self.paths.config
        self.api.SECONDARY_CONFIG = self.paths.secondary
        self.api.STATE_DIR = self.paths.state

    def require(self, ok, message):
        if not ok:
            raise self.api.LockerError(message)

    def read(self, path):
        self.api.check_trusted(path)
        with self.api.open_path(path) as fd:
            info = os.fstat(fd)
            self.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Expected a single trusted regular file: ' + str(path))
            with os.fdopen(os.dup(fd), 'rb') as stream:
                return stream.read()

    def signature(self, path, *, identity=False):
        if not os.path.lexists(path):
            return None
        data = self.read(path)
        info = path.stat()
        result = {'sha256': sha(data), 'mode': stat.S_IMODE(info.st_mode),
                  'uid': info.st_uid, 'gid': info.st_gid, 'mtime_ns': info.st_mtime_ns}
        if identity:
            result.update(device=info.st_dev, inode=info.st_ino)
        return result

    def write(self, path, data, mode=0o600):
        self.api.check_trusted(path.parent)
        if os.path.lexists(path):
            self.read(path)  # Refuse links/untrusted existing targets.
        fd, temporary = tempfile.mkstemp(prefix='.two-roots-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                os.fchmod(f.fileno(), mode)
                f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(temporary, path)
            with self.api.open_path(path.parent, directory=True) as directory:
                os.fsync(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def json_write(self, path, payload, mode=0o600):
        self.write(path, (json.dumps(payload, sort_keys=True, indent=2) + '\n').encode(), mode)

    def snapshot(self, scope):
        """Filesystem protection metadata only; no original file-content reads."""
        api = self.api
        underlying = api.Flags()
        attributes = {}
        class Capture:
            def read(self, fd):
                info = os.fstat(fd)
                attributes[info.st_dev, info.st_ino] = {
                    'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}
                return underlying.read(fd)
        scope.select([str(scope.root)], protect_tool=False)
        rows = api.scan(scope, [scope.root], Capture(), require_containment=False)
        return {'entries': [{**api.asdict(node), **attributes[node.device, node.inode], 'flags': flags}
                            for node, flags in rows], 'symlinks': [api.asdict(s) for s in rows.symlinks]}

    def manifest(self):
        return json.loads(self.read(self.paths.backup / 'manifest.json'))

    def invariant(self, manifest):
        self.require(self.signature(self.paths.config, identity=True) == manifest['primary_config'], 'Primary configuration changed; stop and inspect.')
        self.require(self.signature(self.paths.state / 'state.json', identity=True) == manifest['primary_journal'], 'Primary journal changed; stop and inspect.')

    def install(self, legacy, canonical):
        self.api.require_admin()
        p = self.paths
        self.require(self.api.VERSION == '1.4.0', 'Unexpected candidate version.')
        candidate = self.source.read_bytes()
        with self.api.operation_lock():
            self.require(not os.path.lexists(p.backup), 'Checkpoint already exists; validate or rollback it before retrying.')
            original = self.read(p.executable)
            self.require(sha(original) == PREVIOUS_SHA256, 'Installed executable is not the reviewed 1.3.0; refusing upgrade.')
            primary = self.api.primary_scope()
            self.require(primary.root == legacy, 'Legacy argument must equal the unchanged configured root.')
            registration = self.api.root_reservation(primary, canonical)
            self.require(not os.path.lexists(p.secondary), 'An additional-root registration already exists.')
            self.require(not os.path.lexists(p.state / 'additional'), 'Additional-root state already exists; review separately.')
            journal = p.state / 'state.json'
            journal_signature = self.signature(journal, identity=True)
            if journal_signature is not None:
                self.require(self.api.Journal(journal).pending is None, 'Resolve the pending primary recovery operation first.')
            config_signature = self.signature(p.config, identity=True)
            original_signature = self.signature(p.executable)
            before = self.snapshot(primary)
            # Capture everything before either installed control file is changed.
            upgrades = p.backup.parent
            if not upgrades.exists():
                upgrades.mkdir(mode=0o700)
            self.api.check_trusted(upgrades)
            p.backup.mkdir(mode=0o700)
            self.write(p.backup / 'locker-before', original, original_signature['mode'])
            self.write(p.backup / 'config-before.json', self.read(p.config))
            if journal_signature is not None:
                self.write(p.backup / 'journal-before.json', self.read(journal))
            self.write(p.backup / 'locker.py', candidate, 0o755)
            self.write(p.backup / 'upgrade.py', Path(__file__).read_bytes(), 0o700)
            self.json_write(p.backup / 'protection-before.json', before)
            encoded_registration = (json.dumps(registration, sort_keys=True, indent=2) + '\n').encode()
            manifest = {'version': 1, 'legacy_root': str(legacy), 'canonical_root': str(canonical),
                        'primary_config': config_signature, 'primary_journal': journal_signature,
                        'original_executable': original_signature, 'candidate_sha256': sha(candidate),
                        'registration_sha256': sha(encoded_registration), 'protection_sha256': sha(json.dumps(before, sort_keys=True).encode())}
            self.json_write(p.backup / 'manifest.json', manifest)
            self.invariant(manifest)
            self.require(self.source.read_bytes() == candidate, 'Candidate changed during staging.')
            try:
                (p.state / 'additional').mkdir(mode=0o700)
                self.write(p.secondary, encoded_registration, 0o644)
                self.write(p.executable, candidate, 0o755)
                self._validate(manifest)
                self.json_write(p.backup / 'result.json', {'installed': True, 'canonical_registration': 'PENDING',
                                                         'root_created': False, 'flags_changed': False})
            except BaseException:
                # Same rules are used for operator rollback after interruption.
                self._rollback(manifest)
                raise
        return p.backup

    def _validate(self, manifest):
        p = self.paths
        self.invariant(manifest)
        self.require(sha(self.read(p.executable)) == manifest['candidate_sha256'], 'Installed executable differs from candidate.')
        self.require(stat.S_IMODE(p.executable.stat().st_mode) == 0o755, 'Installed executable mode is not 0755.')
        self.require(sha(self.read(p.secondary)) == manifest['registration_sha256'], 'Additional registration changed; this checkpoint expects PENDING.')
        self.require(stat.S_IMODE(p.secondary.stat().st_mode) == 0o644, 'Additional registration mode is not 0644.')
        self.api.check_trusted(p.state / 'additional')
        self.require(not any((p.state / 'additional').iterdir()), 'Additional journal must still be absent.')
        self.require(not os.path.lexists(manifest['canonical_root']), 'Canonical root was provisioned; use post-activation validation, not this checkpoint.')
        primary = self.api.primary_scope()
        current = self.snapshot(primary)
        self.require(sha(json.dumps(current, sort_keys=True).encode()) == manifest['protection_sha256'],
                     'Legacy inode identities, permissions, flags or symlinks changed; compare the private baseline.')
        roots = self.api.registered_roots()
        self.require(roots == [{'root': manifest['legacy_root'], 'registration': 'ACTIVE', 'default': True},
                               {'root': manifest['canonical_root'], 'registration': 'PENDING', 'default': False}],
                     'Registered roots differ from the two approved roots.')
        # Ensure selection remains refused while pending, including an explicit --root.
        try:
            self.api.load_scope(manifest['canonical_root'])
        except self.api.LockerError as error:
            self.require('PENDING' in str(error), 'Unexpected canonical-root refusal: ' + str(error))
        else:
            raise self.api.LockerError('Pending root unexpectedly grants authority.')

    def validate(self):
        self.api.require_admin()
        with self.api.operation_lock():
            self._validate(self.manifest())

    def _rollback(self, manifest):
        p = self.paths
        self.invariant(manifest)
        self.require(not os.path.lexists(manifest['canonical_root']), 'Rollback is limited to this pre-provisioning checkpoint; do not orphan an activated root.')
        actual = sha(self.read(p.executable))
        self.require(actual in (manifest['candidate_sha256'], manifest['original_executable']['sha256']),
                     'Installed executable changed independently; refusing overwrite.')
        if os.path.lexists(p.secondary):
            self.require(sha(self.read(p.secondary)) == manifest['registration_sha256'], 'Registration changed/activated; rollback refused.')
        secondary_state = p.state / 'additional'
        if os.path.lexists(secondary_state):
            self.api.check_trusted(secondary_state)
            self.require(not any(secondary_state.iterdir()), 'Additional journal exists; rollback refused.')
        original = self.read(p.backup / 'locker-before')
        self.require(sha(original) == manifest['original_executable']['sha256'], 'Executable backup changed.')
        # No journal, primary configuration, workspace file or inode flag is restored/rewritten.
        if actual != manifest['original_executable']['sha256']:
            self.write(p.executable, original, manifest['original_executable']['mode'])
            os.utime(p.executable, ns=(manifest['original_executable']['mtime_ns'], manifest['original_executable']['mtime_ns']), follow_symlinks=False)
        if os.path.lexists(p.secondary):
            p.secondary.unlink()
        if secondary_state.exists():
            secondary_state.rmdir()
        self.invariant(manifest)
        self.json_write(p.backup / 'result.json', {'installed': False, 'rolled_back': True, 'flags_changed': False})

    def rollback(self):
        self.api.require_admin()
        with self.api.operation_lock():
            self._rollback(self.manifest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    install = commands.add_parser('install')
    install.add_argument('--legacy', required=True)
    install.add_argument('--canonical', required=True)
    commands.add_parser('validate')
    commands.add_parser('rollback')
    args = parser.parse_args()
    upgrade = Upgrade(Path(__file__).with_name('locker.py'))
    try:
        if args.command == 'install':
            for value in (args.legacy, args.canonical):
                upgrade.require(Path(value).is_absolute(), 'Absolute root arguments required.')
            checkpoint = upgrade.install(Path(args.legacy), Path(args.canonical))
            print(f'Installed 1.4.0; legacy configuration/journal/protection verified unchanged. Checkpoint: {checkpoint}')
            print('Additional root is PENDING and was not created. No inode flags changed.')
        elif args.command == 'validate':
            upgrade.validate()
            print('PASS: executable, two exact registrations, unchanged legacy protection/configuration/journal; canonical root absent/PENDING.')
        else:
            upgrade.rollback()
            print('Restored original executable; removed only pending registration/empty additional state. Primary config/journal and workspace flags untouched.')
        return 0
    except (OSError, ValueError, KeyError, TypeError, upgrade.api.LockerError) as error:
        print('folder-locker upgrade: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
