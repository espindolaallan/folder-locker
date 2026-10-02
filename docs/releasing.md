# Publishing a reviewed version

Source, tests, public documentation and version history belong in this repository.
Host-specific deployment evidence, recovery journals and private library paths do
not. VERSION identifies source behavior; publication is represented by Git tags
and GitHub Releases, not by the presence of a changelog heading alone.

For 1.4.0 the multi-root status UX is part of the first public version. A previously
installed, untagged 1.4.0 build may lack `status --all` and the stderr scope notice;
compare the executable hash/commit as well as `--version`. Its protection controls
and config/journal formats remain compatible. An ordinary same-root upgrade is
sufficient to adopt the final executable; do not rerun reservation/activation.

Before publishing:

1. Review the diff against the actual remote branch, public filenames/content,
   CHANGELOG, release notes and VERSION. Confirm the intended branch and remote;
   unrelated automation branches are not part of a release.
2. Run the standard-library suite, `sh -n install.sh` and CLI help/version checks.
   Locking/traversal/recovery changes require the privileged disposable kernel
   self-test described in CONTRIBUTING.md. Presentation-only follow-ups need not
   repeat kernel mutations when those mechanics are unchanged.
3. Commit the coherent reviewed source locally. Record the exact commit to publish.
   Stop for owner approval before any push, remote tag or GitHub Release.

After approval, use the exact reviewed commit, not an unchecked moving HEAD:

```bash
# Replace REVIEWED_COMMIT with the full reviewed SHA.
git push origin REVIEWED_COMMIT:refs/heads/main
```

Wait for the existing GitHub Actions Python matrix and kernel job on that exact
commit to pass. Do not force a push if main advanced; inspect and reconcile first.
Then tag the same reviewed commit and publish only that tag:

```bash
git tag -a v1.4.0 REVIEWED_COMMIT -m 'Folder Locker 1.4.0'
git push origin refs/tags/v1.4.0
gh release create v1.4.0 --repo espindolaallan/folder-locker --verify-tag --title 'Folder Locker 1.4.0' --notes-file docs/releases/1.4.0.md
```

The optional final command requires GitHub CLI and the owner's authenticated
account; the same reviewed notes can be published through GitHub's release UI.
Do not overwrite an existing tag/release or publish generated host state as assets.
No release automation or package registry is necessary for this project.
