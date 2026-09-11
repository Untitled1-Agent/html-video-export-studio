# Releases, packaging and update recovery

[Back to README](../README.md) · [GitHub setup](../GITHUB_SETUP.md)

## Version and testing

Stable release tags are `vMAJOR.MINOR.PATCH`, matching `APP_VERSION` and `pyproject.toml`. Do not reuse an existing tag to replace a bad release; issue a new version. The updater understands semantic version ordering and ignores draft/prerelease metadata from the stable-release endpoint.

Run `python tools/check_docs.py`, the full `tools/validate.py` under a desktop/Xvfb, `tests/profile_matrix.py`, a Python distribution build, and packaging. The gate fails on required-test skips and unhandled background/finalizer exceptions. Test a clean archive, not only the development directory. For a real customer source use `tests/real_source_smoke.py` locally; do not commit its input/media or disclose its content in public logs.

`python tools/package_release.py dist` creates deterministic ZIP entries and matching checksums. Its allowlist is explicit; new root modules must be registered. It validates application syntax without traversing a local virtual environment. Syntax/packaging success is not a test-suite pass. Artifacts exclude browsers, environments, customer inputs and rendered media.

## Asset contract

For version 1.5.2:

| Asset | Purpose |
| --- | --- |
| `HTML-Video-Export-Studio-v1.5.2-GitHub-ready.zip` | Full source, including GitHub workflows |
| `html-video-export-studio-v1.5.2.zip` | In-app standalone update payload; excludes `.github` |
| `<exact ZIP filename>.sha256` | SHA-256 paired with that ZIP |

GitHub-generated “Source code” archives are not the named updater asset. Do not rename the ZIP without also updating its checksum asset/name record. The SHA file contains one digest and exact archive name. The app also checks the version inside the ZIP.

The checked-in Release workflow is tag-triggered, runs validation, builds distributions, packages release ZIPs and opens a draft release. Review before publishing. Tests have read-only repository permissions; release creation has contents write only in that workflow. Third-party action references are pinned to commits and can be updated by Dependabot. No workflow has been run remotely merely by generating these files.

## Installation behavior

Updates are downloaded over approved HTTPS GitHub hosts, bounded in size and validated before extraction. Archive traversal, symlinks, protected directories, duplicate/case-colliding paths, and version/syntax failures are rejected. The helper waits for the old app to exit; it never kills it to obtain the directory. It validates the restart target before modifying files.

Automatic replacement is for standalone app directories, not regular pip installs. Changes to `requirements.txt` are refused because modifying dependencies cannot be made transactional with file replacement. Use a fresh-folder installer when dependencies change. Developers with editable checkouts should update with Git/pip even when their directory is technically writable.

The helper backs up only shipped paths, writes per-file temporary replacements, attempts rollback on handled failures, and retains backups. This is not an atomic directory swap against a crash or power loss. Removed modules from earlier versions are not automatically garbage-collected unless explicitly handled by a future migration; a fresh installation is the cleanest upgrade path.

## Recovery

Close all running copies before manual repair. Read `update.log` in the installation directory for failure details and the retained backup path. Restore affected files from that backup, or install the release in a fresh folder and reopen the project. Do not delete `.venv`, customer sources, projects or exports as a generic recovery step. A stale `.hves-update.lock` should only be removed after confirming that no updater instance is active.

The checksum proves matching bytes, not trustworthy authorship. Verify the configured repository and release author yourself. Signed-update/attestation validation and live native-platform updater certification remain listed in [known issues](KNOWN_ISSUES.md).
