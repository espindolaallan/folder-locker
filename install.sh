#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo 'Run: sudo ./install.sh [--upgrade] /absolute/workspace/path' >&2
    exit 1
fi
locker_upgrade=false
if [ "${1-}" = '--upgrade' ]; then
    locker_upgrade=true
    shift
fi
if [ "$#" -ne 1 ]; then
    echo 'Usage: sudo ./install.sh [--upgrade] /absolute/workspace/path' >&2
    exit 1
fi
case "$1" in
    /*) ;;
    *) echo 'The workspace must be an absolute path. Quote paths containing spaces.' >&2; exit 1 ;;
esac
if [ ! -x /usr/bin/python3 ]; then
    echo 'Install Python 3.10 or newer at /usr/bin/python3 first.' >&2
    exit 1
fi
/usr/bin/python3 -I - <<'PY'
import sys

if sys.platform != 'linux':
    raise SystemExit('Folder Locker requires Linux.')
if sys.version_info < (3, 10):
    raise SystemExit('Folder Locker requires Python 3.10 or newer at /usr/bin/python3.')
PY

locker_source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
locker_destination=/usr/local/bin/locker
if [ -L "$locker_destination" ]; then
    echo 'Refusing to replace a symlink at /usr/local/bin/locker.' >&2
    exit 1
fi
if [ -e "$locker_destination" ] && ! cmp -s "$locker_source_dir/locker.py" "$locker_destination"; then
    if [ "$locker_upgrade" != true ]; then
        echo 'A different version is installed. To update: sudo ./install.sh --upgrade /absolute/workspace/path' >&2
        exit 1
    fi
    # Identify the installed program from its source without executing it.
    # Never overwrite another program just because it is also named locker.
    /usr/bin/python3 -I - "$locker_destination" <<'PY'
import os
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
info = path.lstat()
expected = b'#!/usr/bin/python3 -I\n"""Folder Locker: explicit, recursive Linux immutable locks within one workspace."""\n'
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
    raise SystemExit('Refusing to upgrade an installation that is not a trusted root-owned regular file.')
with path.open('rb') as stream:
    if stream.read(len(expected)) != expected:
        raise SystemExit('Refusing to overwrite an unrelated /usr/local/bin/locker.')
PY
fi

locker_staged=$(mktemp /usr/local/bin/.folder-locker.XXXXXX)
trap 'rm -f -- "$locker_staged"' EXIT HUP INT TERM
install -o root -g root -m 0755 "$locker_source_dir/locker.py" "$locker_staged"
# Validate/initialize the scope before replacing a working installation.
"$locker_staged" --root "$1" init
mv -fT -- "$locker_staged" "$locker_destination"
echo 'Installed /usr/local/bin/locker. Existing settings and locks were preserved.'
echo 'Next: locker status'
