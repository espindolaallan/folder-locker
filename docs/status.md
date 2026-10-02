# Status scope and automation

`locker status` inspects actual filesystem flags. `locker roots` lists registered
boundaries and their activation state. ACTIVE is not a synonym for LOCKED.

## Selection contract

| Command | Scope | stdout | stderr on success |
| --- | --- | --- | --- |
| `locker status` | Original default root only | Existing text format | Scope notice if an additional registration exists |
| `locker status --json` | Original default root only | Existing array and fields, unchanged | Same scope notice |
| `locker status PATH [PATH ...]` | Explicit paths in one registered root | Existing text/JSON format | No implicit-scope notice |
| `locker --root ROOT status` | One exact registered root | Existing text/JSON format | No implicit-scope notice |
| `locker status --all` | Every registered root; scan ACTIVE roots only | Text with registration/default headings | None unless registry validation fails |
| `locker status --all --json` | Same global view | Array of status rows, adding `registration` and `default` | Same failure behavior |
| `locker roots [--json]` | Registration inventory only | Existing registry format, unchanged | Errors only |

For a single-root installation, default status is unchanged, including stderr.
In a multi-root installation, default status still scans only the original root.
The additional registration is read to explain the scope, without scanning that
root or claiming anything about its filesystem protection.

An illustrative default terminal view (the first line is stderr):

```text
Scope: default root only. Additional root "/home/alex/library" is ACTIVE; not inspected. Use 'locker status --all' or 'locker roots'.
LOCKED     "/home/alex/code" (2 locked, 0 editable files/directories)
  All files and folders in this selection are locked.
```

PENDING is named in the notice when appropriate. If the additional metadata is
invalid, the notice instead says it is UNVERIFIED and global status is unknown;
this does not disable inspection of the default root.

For the same invented fixture with both roots locked, `status --all` prints:

```text
Root: "/home/alex/code" [ACTIVE] (default)
LOCKED     "/home/alex/code" (2 locked, 0 editable files/directories)
  All files and folders in this selection are locked.
Root: "/home/alex/library" [ACTIVE]
LOCKED     "/home/alex/library" (1 locked, 0 editable files/directories)
  All files and folders in this selection are locked.
```

The full `status --all --json` report for that fixture is:

```json
[
  {
    "path": "/home/alex/code",
    "state": "LOCKED",
    "locked": 2,
    "total": 2,
    "editable": 0,
    "view": "editable",
    "locations": [],
    "symlinks": 0,
    "protected_symlinks": 0,
    "hardlinked_files": 0,
    "hardlinks_outside_selection": 0,
    "registration": "ACTIVE",
    "default": true
  },
  {
    "path": "/home/alex/library",
    "state": "LOCKED",
    "locked": 1,
    "total": 1,
    "editable": 0,
    "view": "editable",
    "locations": [],
    "symlinks": 0,
    "protected_symlinks": 0,
    "hardlinked_files": 0,
    "hardlinks_outside_selection": 0,
    "registration": "ACTIVE",
    "default": false
  }
]
```

`--all` works with the existing views and detail limits. Limits apply per root;
JSON still contains all matching locations. It cannot be combined with paths or
`--root` (usage error, exit 2). On an unconfigured installation it fails, rather
than inferring roots from the filesystem. It never scans the common parent.

## PENDING and failures

A PENDING reservation is never scanned, even if a directory has appeared at its
path. Global status includes this explicit row, alongside the inspected ACTIVE root:

```json
{
  "path": "/home/alex/library",
  "state": "UNVERIFIED",
  "error": "Root is PENDING; not inspected and no filesystem authority granted.",
  "registration": "PENDING",
  "default": false
}
```

`--all` exits 1 if any root is PENDING or has an inspection failure. Unknown counts
are omitted, not presented as zero. A scan failure in an ACTIVE root produces an
UNVERIFIED row with its error; other roots still have their observations. Invalid
registration metadata or identity checks fail before global output: stdout is
empty, an error goes to stderr, exit 1. A partial report is never a successful
global result. An exit of 0 means inspection succeeded, not that every root is
LOCKED; inspect each row's `state`.

Global status is a sequential observation, not an atomic snapshot or an authorization
to mutate. It reuses the existing read-only traversal and identity checks, including
symlink and mount boundaries. It does not read/write journals, activate registrations
or acquire/clear immutable flags. `roots` retains its existing identity validation.

## Compatibility and rationale

The default does not switch automatically to all roots: that would silently widen
filesystem reads, change the number of JSON rows and affect scripts parsing the
old text. Only adding an explicit option would leave the original ambiguity in
normal human use. A stderr scope notice plus explicit `--all` addresses both cases.
Changing the JSON envelope or adding fields to legacy rows was unnecessary.

Consumers should parse stdout, keep stderr separate, and check the exit code.
Scripts that merge `2>&1` will now see the notice in implicit multi-root mode; use an
explicit path/`--root` when the intended scope is one root. Agents asking for global
protection should use `status --all --json`; agents performing a bounded operation
should continue selecting its exact path. Do not infer global protection from
plain `status --json`, or immutable protection from `roots --json`.
