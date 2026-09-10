# HTML Video Export Studio 1.5.1

A Python desktop app and command-line exporter for **compatible** HTML animations and pages. Capture local HTML or URLs, inspect sources, queue jobs, and render video using Chromium and FFmpeg.

## Upgrade from 1.5.0

**Close the old app, extract this release into a fresh folder and run its installer. Do not use the old 1.5.0 updater for this upgrade.** Both the application and updater needed repairs. Do not replace only `renderer.py`. Existing projects can be opened in the new app; per-user preferences remain outside the application directory. Keep your old folder until your exports have been verified.

## Windows setup

Install Python 3.10 or newer with **Tcl/Tk** and the Python launcher, then extract the ZIP. Double-click `install_windows.bat`, followed by `run_windows.bat`. An internet connection is needed to download packages and Playwright Chromium. Installation errors are left visible in the console.

Manual equivalent in PowerShell, in the extracted app directory:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe app.py
```

## macOS / Linux setup

```bash
chmod +x install_macos_linux.sh run_macos_linux.sh
./install_macos_linux.sh
./run_macos_linux.sh
```

On Debian/Ubuntu, install Tk and virtual-environment support first when missing:

```bash
sudo apt-get update
sudo apt-get install python3-tk python3-venv
```

When Chromium reports missing Linux system libraries:

```bash
.venv/bin/python -m playwright install --with-deps chromium
```

Dependencies: Playwright, imageio-ffmpeg, Pillow and Python's Tkinter. A separately installed FFmpeg is optional; the app tests candidate encoders/filters and can use the imageio-ffmpeg binary. A system FFmpeg may be necessary for a codec unavailable in the bundled build. Override with `HTML_VIDEO_FFMPEG` or `HTML_VIDEO_BROWSER` only when needed.

## Desktop workflow

**Add source → Analyze → Edit job / choose recipe → Test frame or Compare → Start queue.**

The queue supports file/folder selection, URLs, duplicate/requeue, per-job settings, filtering, progress, parallel exports, pause/resume, cancellation, and projects. Each export worker owns its Playwright and Chromium resources. Analysis/preview work is separately limited to two threads. The default is two export workers; high-resolution captures can use considerable RAM.

Settings are snapshotted when a run starts. Active and pending jobs in that run cannot be edited. **Cancel Selected** also cancels a pending job. **Stop Queue** cancels active work and leaves unstarted work queued. Pause is checked between frames; it cannot freeze an ordinary real-time webpage's clock.

Outputs use temporary files until encoding finishes. Existing files are preserved by default; same-name parallel exports get distinct numbered filenames. Overwrite is explicit. On POSIX, no-overwrite publication requires a filesystem with hard links; Windows uses atomic non-replacing rename. Network filesystems may have different guarantees.

## Recommended settings

| Goal | Recipe / processing |
| --- | --- |
| Exact source reference | Exact Source Master; 2×; 60 fps; **No processing** |
| UI / vector animation | Motion Graphics Master; optional content-aware clarity |
| Common player compatibility | Social Delivery; H.264 4:2:0 |
| Transparent editing output | ProRes 4444; transparent capture; **No processing** |

2× means twice each capture dimension. A 1080×1920 export surface is rasterized by Chromium at 2160×3840. This is not an FFmpeg enlargement of a previously encoded video. Canvas/image/video backing resolutions still limit available detail. Fractional scaling uses the browser's pixel rounding, not Python's half-to-even rounding.

**No processing + lossless RGB** has no FFmpeg visual filter chain and preserves the browser-captured RGB samples through encode/decode. Filters intentionally change those samples. YUV profiles necessarily convert RGB color values and may subsample chroma; their no-processing option means no enhancement, not no color conversion. These conversions now explicitly use BT.709 matrices. H.264 headers explicitly carry primaries, transfer and range settings. ProRes is not mathematically lossless and does not create real 10-bit source detail from 8-bit screenshots.

## Duration and timeline

There is **no fixed-duration fallback**. Duration is read from the source's export attributes, scene declarations, media metadata, animation timing or an explicit job duration / trim bound. Missing or infinite timing needs a user choice. The Static Page Hold recipe has a visible, editable duration; it is not used as a fallback for other sources.

| Adapter | Intended source and limitations |
| --- | --- |
| OM / custom event / JavaScript function | Source implements deterministic seeking. Preferred for authored animation. |
| Web Animations | CSS / Web Animations API timelines that can be paused and sought. |
| Media | A seekable HTML video/audio element with loaded metadata. Not DRM or a live stream. |
| Browser Clock | JavaScript timers/rAF; captures from the ready-state baseline, at millisecond clock precision. Does not seek media or CSS compositor animations. |
| Real-time | Best-effort screenshot sequence; capture speed can cause missed states or timing error. Not frame-accurate screen recording. |
| Static | One captured frame repeated identically for the chosen duration. |

See `docs/UNIVERSAL_CAPTURE_GUIDE.md` for target selection and source contracts. Neither a duration attribute nor an export marker magically makes an arbitrary JavaScript animation seekable.

## Processing and alpha

Content-aware clarity is a heuristic, not restoration or super-resolution. It examines representative frames from seekable sources, avoids blank frames, and leaves photographic or uncertain content unprocessed. It does not pre-sample real-time, virtual-clock, static or transparent sources. Compare a representative test frame before a full export. Explicit CAS, unsharp and color controls remain available.

Transparency is restricted to the ProRes 4444 profile. In this release, processed alpha captures are rejected rather than silently dropping alpha. Use No processing. Comparison previews are inspection images, not lossless or full-size reference masters; they may be resized to fit the comparison layout.

## Audio

Browser audio is **not** recorded. External audio can be trimmed/padded or looped, offset and faded. The visual timeline determines output length, even when audio is shorter. Offset is silence inserted on the output timeline; fades are also relative to that output timeline. Changing trim/holds does not automatically align an unrelated soundtrack to source events.

## Command-line examples

```bash
python cli.py animation.html --probe
python cli.py a.html b.html --probe
python cli.py animation.html --recipe exact_source_master --scale 2 --fps 60 --output exports
python cli.py examples/canvas_function.html --timeline javascript_function --seek-function window.demo.renderAt --output demo.mp4
python cli.py page.html --timeline static --duration 5 --output still.mp4
```

Single-source probing prints one JSON object; batch probing prints one JSON array. Failures return a nonzero exit status. An existing folder, even one with dots in its name, remains a folder. A new output ending in .mp4, .mov or .webm is treated as a file for a single source. Relative file/audio/output references inside projects resolve against the project file location.

## Updates / GitHub

Updates require a trusted GitHub repository and the versioned release ZIP plus its matching SHA-256 asset. The checksum checks integrity, **not publisher identity**. Updates are downloaded and validated before installation, wait for the app to exit, back up touched application files and roll back handled failures. A power failure is not an atomic whole-directory transaction; backups are retained for recovery. Changed Python dependencies require a fresh-folder/manual install. Pip-installed copies must be updated with pip, not the standalone self-updater.

Public repositories need no token. For private releases use `HTML_MP4_GITHUB_TOKEN` in your local environment; it is not saved in preferences. Do not paste tokens into chat or commit them. See `GITHUB_SETUP.md`.

## Validation

The original 1.5.0 package had startup errors; its earlier all-passed report was not reliable. This release's `TEST_REPORT.md` records actual runs and their limitations. Reproduce the release gate:

```bash
# Windows/macOS with a desktop display:
python tools/validate.py
# Headless Linux:
xvfb-run -a python tools/validate.py
python tests/profile_matrix.py
# Optional real-source/full-export test, using YOUR own HTML:
python tests/real_source_smoke.py path/to/animation.html --output test-master.mp4
```

The validation gate fails if required tests are skipped. The original Syncnema HTML and its bundled assets/fonts are not distributed with this application.

## Important limits and safety

Only open HTML or URLs you trust. Chromium executes their JavaScript and may access the network. This application is not a sandbox for malicious pages. No DRM bypass, HDR/wide-gamut preservation, cross-browser pixel identity, or automatic correctness for arbitrary websites is promised.

Managed environments can block `file://` and localhost navigation. Embedded loading works for self-contained bundles but may block relative local scripts/images/styles. Such local-asset failures now stop export rather than silently producing an incomplete video. Remote assets, lazy loading and application-specific initialization can still require source changes. Generic delayed export roots should use explicit Marker or Selector capture. Cancellation can wait for an in-progress browser call to reach its timeout; a page stuck in an infinite JavaScript loop is outside the promise-based timeout guard.

See `TEST_REPORT.md` for what was run on Linux and what was not tested natively on Windows/macOS.
