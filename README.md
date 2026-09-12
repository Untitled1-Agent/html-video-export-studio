# HTML Video Export Studio

**A desktop and command-line workspace for exporting compatible HTML animations to video.**

Version **1.5.2** · Python **3.10+** · Chromium + FFmpeg · Windows / macOS / Linux source distribution

Capture a marked animation, SVG, canvas, video element, selected DOM element, browser viewport, or full page. Inspect the source, choose its timing adapter, preview a frame, then export one job or a parallel queue. The application is local; it does not require an export service or a paid rendering API.

> **Sharp, compatible exports by default:** “Social delivery” uses 1× / 60 fps, 8-bit H.264 Main 4:2:0 MP4 and CAS 0.28 clarity. Use it for mobile playback and social uploads. “Exact source master” remains an explicit lossless RGB / No processing option for archival fidelity, not a mobile-delivery format. [VLC / Android / TikTok compatibility](docs/DELIVERY_COMPATIBILITY.md).

[Documentation index](docs/README.md) · [Installation](docs/INSTALLATION.md) · [User guide](docs/USER_GUIDE.md) · [CLI](docs/CLI.md) · [Source adapters](docs/UNIVERSAL_CAPTURE_GUIDE.md) · [Configuration](docs/CONFIGURATION.md) · [Testing](TEST_REPORT.md)

## What this tool is—and is not

An HTML file is executable page code, not a video timeline. For predictable output, the page needs a seekable animation or a supported timeline adapter. The studio does not magically infer a correct timeline for every website.

| Task | Support |
| --- | --- |
| Frame-accurate authored UI / motion graphics | Preferred path: OM event, custom event, or JavaScript seek function |
| CSS / Web Animations API animation | Pause and set animation time; explicit duration required for infinite loops |
| JavaScript timers and `requestAnimationFrame` | Chromium Browser Clock, advanced progressively from the ready-state baseline |
| Static page | Capture one frame and repeat it identically for a visible, chosen duration |
| HTML media | Seek a selected/first matching media element; not DRM or live streams |
| Arbitrary live webpage | Real-time mode is best-effort, not guaranteed frame-accurate screen recording |
| Browser audio | Not recorded; an external soundtrack can be added through the UI/Python API |

See [compatibility and known limits](docs/KNOWN_ISSUES.md) before choosing an adapter.

## Quick start

### Windows

Install Python 3.10+ with Tcl/Tk and the Python launcher, extract the source ZIP, and double-click **`install_windows.bat`**, then **`run_windows.bat`**. Setup downloads Python packages and Playwright Chromium; it does not install Python itself.

Equivalent PowerShell commands, from the extracted application directory:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe app.py
```

### macOS / Linux

Use a Python installation with Tkinter, then run:

```bash
bash install_macos_linux.sh
bash run_macos_linux.sh
```

On Debian/Ubuntu, missing prerequisites can be installed with `sudo apt-get install python3-venv python3-tk`. Browser library errors are addressed by `.venv/bin/python -m playwright install --with-deps chromium`. See [installation and upgrade details](docs/INSTALLATION.md) for manual setup, dependency overrides, and troubleshooting.

### Your first export

Open **Add HTML**, select a local file, and run **Analyze**. Check the capture target, source dimensions, timing adapter, duration source, and diagnostics. Keep **Social delivery — Sharp compatible MP4 (default)** with **1× / 60 fps / Social Compensation**, then use **Test frame** to inspect the result. Start the queue only after the preview and duration are correct.

Try a bundled example without customer assets:

```bash
python cli.py examples/canvas_function.html --recipe social_delivery --timeline javascript_function --seek-function window.demo.renderAt --load embedded --output exports
```

The examples are self-contained demonstrations; no customer HTML, poster assets, font files, access tokens, or rendered movies are included.

## Output quality

**2× means twice the width and twice the height.** A 1080×1920 capture surface is rasterized directly by Chromium at **2160×3840**. The renderer does not enlarge a previous video. This improves vector/text sampling but cannot restore detail missing from a small bitmap or a low-resolution canvas backing store.

| Profile | Container | Use | Main tradeoff |
| --- | --- | --- | --- |
| Lossless RGB | MP4 | Exact captured-pixel reference | Limited hardware/player compatibility; large files |
| ProRes 4444 | MOV | Editing and transparent compositions | Lossy codec, large files, not new 10-bit source detail |
| ProRes 422 HQ | MOV | Editing interchange | Chroma subsampling; even dimensions enforced |
| H.264 4:4:4 | MP4 | Smaller high-quality UI video | Lossy, less broadly compatible than 4:2:0 |
| H.264 Main 4:2:0 (default) | MP4 | Mobile playback and social uploads | Chroma subsampling can soften saturated text |
| VP9 4:4:4 | WebM | Web-oriented delivery | Encoding speed and player/editor support vary |

**No processing + Lossless RGB** omits FFmpeg visual filters and preserves the captured RGB samples through encoding. With another profile, “No processing” means no enhancement; color conversion still occurs. With a filter selected, a lossless encoder preserves the *processed* pixels, not the unmodified source.

The PNG capture path is 8-bit browser output. It is not an HDR/wide-gamut archival pipeline. Frame comparisons are fit-to-window inspection images; inspect an exported PNG at 100% to judge native edges. [Presets and algorithms](docs/PRESETS_AND_ALGORITHMS.md) explains the exact settings.

## Timelines without hidden fallback durations

Duration comes from explicit job settings, capture attributes, JavaScript metadata, scene durations, media metadata, finite Web Animations timing, or an explicit trim bound. Missing duration is an error, not an assumed clip length. A finite sibling no longer gives an infinite looping animation an accidental duration.

The **Static page hold** recipe visibly sets five seconds. That is editable recipe data, not a renderer fallback. Trim end is exclusive for sampling; output duration is rounded up to an integral number of frames. Opening and closing holds repeat boundary samples. Details and examples are in the [source adapter guide](docs/UNIVERSAL_CAPTURE_GUIDE.md).

## Workflow features

The desktop includes a multi-file queue, folders/URLs, per-job configuration, analysis, test-frame/comparison previews, search/filtering, duplicate/requeue, projects, progress, pause/resume, cancellation, and configurable worker count. Workers own separate Playwright/Chromium resources and FFmpeg processes. Start with one or two workers for 2× or 4K output.

Jobs are snapshotted at run start. Pending/running jobs cannot be edited during that run. Cancel Selected also handles pending jobs. Stop Queue cancels active exports while leaving unstarted work queued. Pause acts between frames; it does not freeze an ordinary real-time webpage.

Completed movies and previews are published from temporary files. Existing outputs are preserved by default with numbered alternatives. Overwrite is explicit. Export destinations may not replace the input HTML or external soundtrack. See [user guide](docs/USER_GUIDE.md) for output naming and filesystem limits.

## CPU performance

Video exports use CPU-aware FFmpeg thread budgets. Choose **Job Settings → Output → CPU threads per export** or `--cpu-threads N` on the CLI; **0** is automatic. The desktop shares automatic budgets across queue workers while a single queued video can use more cores. Browser capture remains ordered. See [CPU parallelism and benchmarking](docs/PERFORMANCE.md) for limits and measurement commands.

## Automation

```bash
# One JSON result, with a nonzero exit status on errors
python cli.py examples/deterministic_svg_event.html --probe --timeline custom_event --load embedded

# A batch: sequential on the CLI, parallel in the desktop queue
python cli.py first.html second.html --recipe exact_source_master --output exports

# An explicitly bounded static page
python cli.py examples/static_hold.html --recipe static_page_hold --duration 3 --output still.mp4
```

Use `python cli.py --help` or [the CLI reference](docs/CLI.md). The [Python API guide](docs/PYTHON_API.md) covers external audio, cancellation, project files, and thread ownership. Not every desktop setting is a CLI flag.

## GitHub, releases, and updates

The source distribution includes a safe initial-publication helper:

```bash
python tools/publish_github.py --repo html-video-export-studio --dry-run
```

The actual publication requires your locally authenticated GitHub CLI. It creates a **new private** repository by default, stages an allowlisted set of project files, commits, and pushes without force. It refuses existing repository names/remotes rather than overwriting them. [GitHub setup](GITHUB_SETUP.md) has the exact authentication and publication commands.

Application updates use a configured trusted GitHub repository, a versioned release ZIP, and its matching SHA-256 file. Checksums establish integrity, not publisher identity. Dependency-changing releases require a fresh-folder installation. Pip installations must be updated with pip. [Release and updater guide](docs/RELEASING.md) describes recovery and trust boundaries.

## Development and verification

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python tools/check_docs.py
# Desktop session:
python -W error::ResourceWarning tools/validate.py
# Headless Linux: xvfb-run -a python -W error::ResourceWarning tools/validate.py
python tests/profile_matrix.py
python -m build
python tools/package_release.py dist
```

The release gate rejects required-test skips and records unhandled thread/finalizer exceptions. The [test report](TEST_REPORT.md) records runs actually completed for this version and distinguishes local tests from proposed CI. [Contributing](CONTRIBUTING.md), [architecture](docs/ARCHITECTURE.md), and [known issues](docs/KNOWN_ISSUES.md) describe the maintenance constraints.

## Security and distribution

Only render HTML/URLs you trust. Pages execute JavaScript and can access the network; this is not an isolation environment for hostile HTML. The publishing helper is not a secret scanner: keep personal inputs outside repository source folders and review staged changes. Never put tokens in projects or reports.

**A project license has not been selected.** This package does not silently assign an open-source license on the owner's behalf. Review [NOTICE.md](NOTICE.md) before public distribution, and [SECURITY.md](SECURITY.md) for handling sensitive reports.

## External soundtracks

Match WAV/M4A/MP3 files to individual HTML jobs, use signed audio sync, and adjust gain, looping, fades, encoding quality, or optional normalization. See the [soundtrack guide](docs/AUDIO.md) for desktop controls, CLI batch mapping, timing semantics, and social-delivery defaults.
**Licensed under the [MIT License](LICENSE).** Review [NOTICE.md](NOTICE.md) for third-party component notes, and [SECURITY.md](SECURITY.md) for handling sensitive reports.
