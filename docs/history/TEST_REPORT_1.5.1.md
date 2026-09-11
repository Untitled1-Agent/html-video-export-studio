# Validation report — HTML Video Export Studio 1.5.1

Review date: 2026-09-05. Source reviewed: the conversation's actual `HTML-Video-Export-Studio-v1.5.0-GitHub-ready.zip`, not the previous response's claims. Source files were extracted into a separate working folder, repaired, and tested. Tests were executed locally; this is not independent certification.

## Baseline correction

The shipped archive did not match its prior description. `app.py` had an unterminated f-string; renderer startup referenced missing attributes; the dependency check and several supplied smoke scripts called nonexistent APIs. The original report itself contained FAIL/MISSING rows while claiming success. Fresh compilation reproduced the UI failure; the baseline suite reproduced the renderer startup failure. The baseline logs are included. Earlier all-checks-passed statements should not be relied on.

## Executed results

| Check | Actual result |
| --- | --- |
| Python compile + release validation gate | PASS; **118 tests, zero skips**; 26.747 seconds on the recorded working-tree run |
| Hidden failure checks | No ignored finalizer exceptions, unhandled worker exceptions, Tk callback errors or failed tests in the final gate. The gate records unraisable/thread exceptions instead of silently accepting them. |
| Real Chromium + FFmpeg | Actual renders inside the suite; not mocked browser/codec tests |
| Output matrix | PASS: six profiles × no processing / subtle CAS / custom unsharp+contrast = **18 encode/decode combinations** |
| RGB fidelity | Browser PNG RGB samples equal decoded lossless RGB MP4 samples byte-for-byte for tested fixtures |
| Color conversion | Tested YUV color swatches and parsed H.264 SPS color primaries, transfer and matrix fields |
| Transparency | ProRes 4444 decoded alpha checked; incompatible profiles/processed alpha rejected |
| Timing | OM/custom hooks, method receiver, CSS/WAAPI, real media seeks, absolute virtual-clock steps, sub-frame clips, trim/closing holds and identical static frame hashes |
| Desktop | Actual Tk UI under Xvfb, real two-worker queue, editor/analysis, busy protection, pending cancellation, stop behavior, bounded analysis threads, stale result rejection and minimum-window controls |
| UI lifecycle | Reproduced then repaired ignored Tcl-variable finalizers and `Tcl_AsyncDelete` crash under worker garbage collection; regression included |
| File publication | Simultaneous same-name commits preserve both outputs; cancellation before commit preserves the old file; process-finalization cancellation exercised |
| Project / CLI | Strict settings, relative project paths, batch JSON, failure exit codes, custom hooks and dotted output directories |
| Updater | Checksum/name pairing, semantic versions, HTTPS/redirect/token rules, ZIP safety, staging ownership, exit-wait abort and rollback of existing/new files. Network/process boundaries are mocked where indicated in tests. |
| Dependency health | Actual Chromium launch and FFmpeg codec checks; six supported profiles in this environment |
| Shell installers | `bash -n` syntax check PASS; not a ShellCheck result or a live dependency installation |
| GitHub workflows | YAML parse PASS; not run on GitHub |
| Source distribution / wheel | Local setuptools build backend completed both builds. No dependencies downloaded during this check. |

Raw evidence is under `validation/`. The supplied real-source regression accepts an explicit HTML path and can render the full file; it no longer silently claims success when the input is missing.

## Real supplied animation

The actual self-contained Syncnema Cut J HTML was analyzed and rendered with **Exact Source Master / No processing**. It reported its own **1080×1920** size and **14.4-second** duration. That duration was read from its runtime attribute; it is not a renderer fallback.

Five positions (0%, 20%, 50%, 80%, 95%) were captured and checked for exact **2160×3840** dimensions. A full **864-frame, 60-fps** lossless RGB MP4 was then rendered. FFprobe verified dimensions, count, rate, 14.4-second duration, full-range RGB, BT.709 primaries and sRGB transfer; FFmpeg decoded the complete video without errors. One representative decoded frame was visually inspected. The generated movie is 65,149,160 bytes; it is not bundled in the source release.

The source HTML, its bundled font files and poster assets are not redistributed. Cut A/B/C were not available as test inputs. No claim is made that those exact files or arbitrary websites were executed.

## Environment and limits

Tests ran on Linux with Python 3.13.5, Playwright 1.57.0, Pillow 12.3.0, imageio-ffmpeg 0.6.0, Chromium and system FFmpeg. Tk tests used Xvfb. Windows/macOS native startup, Explorer/Finder dialogs, native encoders/players and installers were **not** run. Windows updater process-liveness logic was unit-tested using mocks.

This browser environment blocks top-level `file://` and loopback HTTP navigation with `ERR_BLOCKED_BY_ADMINISTRATOR`. Those load modes could not be exercised end-to-end here. Automatic fallback loaded an embedded fixture, but its relative script was blocked; the app now reports that as a hard source-validation error instead of exporting the wrong image. Local-server/path safety is tested separately. Standalone embedded HTML and same-origin embedded iframe capture were exercised end-to-end.

Live GitHub release downloads, OAuth/account connection, a GitHub Actions run and a user-installed automatic restart update were not performed. Updater tests validate local transactions, malicious payloads and mocked network/process behavior. Update checksum validation is not publisher-signature verification. Automatic updates are not power-failure-atomic across the whole folder.

Package download/installation in a brand-new networked virtual environment was unavailable here. Ruff, Mypy, Bandit and ShellCheck were not installed and **were not run**; there is no claim of passing those tools. Python compilation, actual runtime tests and shell syntax checks are the stated evidence.

Real-time capture remains best-effort; Browser Clock does not seek CSS/media or arbitrary async data. Infinite synchronous JavaScript can prevent cooperative timeout handling. HDR/wide-gamut preservation, DRM/live media, every cross-origin iframe and every possible website are outside verified coverage.

## Reproduce

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
# Linux, with Tk + Xvfb installed:
xvfb-run -a python -W error::ResourceWarning tools/validate.py
python tests/profile_matrix.py
python tests/real_source_smoke.py /path/to/source.html --output full-master.mp4
```

Use a normal desktop display instead of xvfb-run on Windows/macOS. The validation gate treats required skips and unhandled thread/finalizer errors as failures. Reports state completed runs, not guarantees that no bugs remain.

## Distribution verification

The release archives are compiled, extracted into a clean directory and retested before delivery. `validation/clean_archive.txt` records that independent archive run; `validation/package_integrity.txt` records archive/source payload and checksum checks. Documentation/log inclusion is the only change made after those payload tests. The archive SHA-256 files identify the delivered bytes.
