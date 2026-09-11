# Validation report — HTML Video Export Studio 1.5.2

Review date: **September 11, 2026**. Basis: the supplied **1.5.1 GitHub-ready ZIP**, extracted and reviewed as code. This is local execution evidence, not independent certification or a claim that no defects remain.

## Repository status

A local Git repository and source distribution are prepared for initial publication. **No remote repository, hosted issue, GitHub release, or Actions run is claimed by this report.** The connected account could be read, but the exposed connector actions could not create repositories or push commits. The publication helper uses the owner's local GitHub CLI authorization. Its network writes are mocked in tests; real local Git initialization/staging/commit are exercised.

## Executed checks

| Check | Observed result | Evidence |
| --- | --- | --- |
| Supplied 1.5.1 baseline | 118 tests passed, zero skips | [baseline log](validation/baseline_1.5.1.txt) |
| Newly written reproductions against the old code | Failed as expected: 13 failure records (including subtests), 3 error records | [before fixes](validation/new_regressions_before_fix.txt) |
| Initial repaired regressions | 14 tests passed | [initial after-fix log](validation/new_regressions_after_fix.txt) |
| Clean extracted source archive | **147 tests passed, zero skips**, 26.573 seconds; docs/reference check also passed | [clean extraction](validation/clean_archive.txt) |
| Final working-tree gate | **147 tests passed, zero skips**, 26.592 seconds | [full gate](validation/release_gate.txt) |
| Publication/package tests | 14 tests passed; real Git operations with mocked GitHub | [tool tests](validation/publication_tests.txt) |
| Codec/processing matrix | **18 combinations passed**: six profiles × no processing/subtle CAS/custom | [matrix log](validation/profile_matrix.txt) |
| Supplied real animation | Automatic runtime duration, 1080×1920 source; exact 2160×3840 PNG captures at five timestamps | [sanitized capture log](validation/real_source_previews.txt) |
| Documentation/reference | Generated reference matches dataclasses/presets/help; local file links and version checked | [docs/CI checks](validation/docs_ci_syntax.txt) |
| Workflow/installer syntax | All YAML parsed; external Actions pinned to commit SHAs; `bash -n` passed | [syntax checks](validation/docs_ci_syntax.txt) |
| Source distribution / wheel | Both built with the installed setuptools PEP 517 backend; key source files and README metadata checked | [build log](validation/package_builds.txt) |

The gate records unhandled worker/finalizer exceptions and rejects required skips. All test counts refer to unittest cases, not an invented count of browser executions. The matrix and real-source preview checks are additional commands outside the 147-test gate.

## Repairs covered by new regressions

1. Mixed infinite and finite Web Animations no longer infer duration from the finite sibling. An explicit duration or trim end is still supported.
2. Overflowing scene-duration totals are invalid, rather than infinite exports.
3. Movie exports cannot replace the external soundtrack. The regression generates an actual AAC-in-MP4 input and verifies it remains unchanged.
4. PNG previews reject non-PNG destinations. Filtered preview/comparison writes use same-directory temporaries and atomic publication; injected FFmpeg failure preserves the previous file.
5. FFmpeg preflight includes required color-conversion and audio filters, not just enhancement filters. PNG processing selects a build with PNG/filter support.
6. Batch and single `--probe` produce structured JSON failures when Chromium cannot start.
7. Reserved Windows device basenames are rejected; temporary movie names stay short even for a long valid final filename.
8. Application update validation requires `cli.py`, ignores virtual environments/build artifacts, and checks the restart destination before applying changes.
9. Release/source publication uses an explicit repository-file allowlist. Private HTML/Markdown at the app root, environment files, runtime folders, and symlinks are not published by that helper.
10. Initial publication defaults to private, refuses existing repos/remotes or unexpected staged files, and distinguishes a permissions/network failure from an available repo name. No force-push or deletion operation is used.

The existing suite also exercises geometry races, SVG/canvas/DOM/iframe targets, deterministic hooks, media seek, static-frame equality, virtual-clock timing, RGB byte identity, alpha, color metadata, output transactions, audio duration, UI thread ownership, bounded analysis, two-worker queues, cancellation, editor lifecycle, and updater archive/rollback defenses.

## Explicit limits

Tests ran on **Linux, Python 3.13.5, Playwright 1.57.0, Pillow 12.3.0, imageio-ffmpeg 0.6.0**, Chromium, FFmpeg, and Tk under Xvfb. Exact executable/package versions are in [environment.json](validation/environment.json).

- Native Windows/macOS installation, file pickers, hardware players, and a live automatic update were not executed. Platform-specific operations are mocked where stated in tests.
- The workflows are prepared but have **not run on GitHub**. YAML parsing is not an Actions execution result. The portable core jobs are not native desktop certification.
- Managed policies here block top-level local-file/loopback navigation. Self-contained embedded HTML is exercised; this does not prove all local/remote asset-loading modes. No policy bypass is introduced.
- The real animation was probed and captured at five positions in this pass. **Its entire long movie was not re-exported for 1.5.2.** The older full 1.5.1 render belongs to the historical report, not this result. The synthetic integration renders do produce/decode movies.
- A fresh networked dependency installation was unavailable: installing the `build` frontend failed because the package host could not resolve. Source/wheel builds instead used the already-installed setuptools backend. `python -m build` and new-venv downloads were not falsely marked as run.
- Ruff, Mypy, Bandit, ShellCheck, external URL/Markdown-anchor checking, and cryptographic publisher-signature verification were not performed. The stated shell check is `bash -n` only.
- Checksums are integrity checks, not release signatures. No blanket safety guarantee is made for hostile HTML, arbitrary asynchronous websites, power failure, network filesystems, every GPU/player, HDR, DRM, or mixed animation clocks.

Remaining product limits are tracked in [known issues](docs/KNOWN_ISSUES.md), not described as fixed hosted tickets. A project license is still an explicit owner decision.

## Reproduce

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python tools/check_docs.py
# Linux with Tk/Xvfb; on a normal desktop omit xvfb-run:
xvfb-run -a python -W error::ResourceWarning tools/validate.py
python tests/profile_matrix.py
# Use your own authorized source; it is not in the repository:
python tests/real_source_smoke.py path/to/source.html --load embedded
# Optional full movie, explicitly requested:
python tests/real_source_smoke.py path/to/source.html --output test-master.mp4
python -m build
python tools/package_release.py dist
```

Raw logs are lightly sanitized to remove local repository/private source paths; test results are unchanged. Logs are not proof that the current reader's OS or inputs behave identically.

## Distribution verification

The source ZIP was extracted into a separate clean directory and its entire 147-test gate and documentation checks rerun. After that run, only the report, logs, and changelog were updated. Application modules, tests, publishing/build tools, generated configuration, and workflows in the final archive match those clean-tested files byte-for-byte. The external `.sha256` files identify the final delivered ZIP bytes. The Git bundle contains the prepared local main-branch commit; it is not a remote GitHub repository.
