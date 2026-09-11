# Contributing

[Architecture](docs/ARCHITECTURE.md) · [Configuration](docs/CONFIGURATION.md) · [Known issues](docs/KNOWN_ISSUES.md)

Work from a branch, keep a minimal reproduction, and add a regression that fails before the fix. Favor observable assertions—decoded pixels, frame timing/count, file contents, or thread lifecycle—over “the method returned.” Treat tests as evidence for their fixtures, not proof of universal webpage support.

## Development setup

```bash
python -m venv .venv
# Activate the environment or use its Python explicitly.
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python tools/check_docs.py
python -W error::ResourceWarning tools/validate.py
python tests/profile_matrix.py
```

On headless Linux run the validation command with `xvfb-run -a` and install Tk, Xvfb and required Chromium system libraries. CI runs the full gate on Linux; limited non-browser unit jobs on other OSes do not certify native GUI behavior. Missing browsers/display are setup failures for the full gate, not successful skips.

## Invariants to preserve

Keep Tk activity on the UI thread and one Playwright owner per worker. Copy configurations before dispatch. Always close contexts/process streams and dispose transient handles. Do not add any implicit clip duration. Preserve the No processing/Lossless RGB path without visual filters. Validate actual color/audio/filter capabilities before capture. Never overwrite an input HTML, soundtrack, or an existing good output after a failed/cancelled export.

Do not bypass Chromium host policies, weaken updater path validation, forward auth tokens through redirects, silently drop alpha, or hide required-test failures. Changes to API/presets must update the generated reference with `python tools/generate_reference.py`. Include source limitations in docs when behavior cannot be guaranteed.

## Repository hygiene

Put private work under `user_inputs/` / `exports/` or outside the checkout, not under `examples`, `tests`, `docs`, `tools` or `validation`. Do not commit tokens, fonts, media, `.venv`, or machine-specific credentials. The package allowlist is in `tools/package_release.py`; add new root application files deliberately. Review `git diff --cached` before pushing. Ignore rules and allowlists are not a secret scan, especially for old Git history.

Issue reports should include version, OS/Python/browser/FFmpeg, recipe/capture/timing/processing, expected vs actual behavior, sanitized diagnostics, and a minimal asset-free HTML reproduction when possible. Report vulnerabilities privately using the repository's security reporting feature when enabled; do not open a public issue with credentials or exploit targets. The supplied issue/PR templates guide these fields.

## Release discipline

Update `version.py` and `pyproject.toml` together, update the changelog, run the full gate/profile matrix/docs checks, build, package, extract cleanly and retest. Attach the versioned update ZIP and exact checksum pairing. Packaging performs syntax/payload checks; it does not substitute for tests. Record native/live checks that were not run. No CI badge or documentation statement should imply a job has run merely because a workflow exists.
