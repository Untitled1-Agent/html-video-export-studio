# Installation, upgrade, and environment

[Back to README](../README.md)

## Requirements

The declared baseline is Python 3.10 or newer, Tkinter for the desktop, Playwright, imageio-ffmpeg, and Pillow. Runtime ranges are kept in `requirements.txt` and `pyproject.toml`; development adds the Python build frontend and PyYAML. Browser packages and native FFmpeg builds are separate from the Python interpreter. See the current [test report](../TEST_REPORT.md) for the exact versions exercised, not a claim that every version combination was tested.

The standard Windows Python installer can include Tcl/Tk and the launcher. On macOS, use a Python distribution with Tk; Homebrew Python may need its matching `python-tk` package. On Debian/Ubuntu install `python3-tk` and `python3-venv` before setup when absent. `python -m tkinter` should open a small test window in a desktop session.

Installation needs network access to download dependencies and Chromium. Runtime needs network access only when the source references network resources or an update check is requested. Fonts, images and scripts must load before reliable export. Keep related local assets with the source.

## Windows

Extract into a normal user-writable directory. Avoid Program Files and a read-only install location when using the standalone updater. Double-click `install_windows.bat`, then `run_windows.bat`.

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe app.py
```

Using the virtual environment's Python directly avoids PowerShell activation-policy problems. If the installer fails, retain the displayed error. Do not run the application as administrator to work around a Chromium organizational policy.

## macOS and Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python app.py
```

Linux native browser libraries can be installed with:

```bash
.venv/bin/python -m playwright install --with-deps chromium
```

That dependency installation can ask for elevated privileges. The app itself should run as an ordinary user. Automated GUI testing on headless Linux uses `xvfb-run`; normal use requires a display server/desktop.

## Optional command-line entry points

From the checkout, `python -m pip install .` installs `html-video-export` and `html-video-export-studio`. `python -m pip install -e .` installs editable entry points for development. Bundled examples/docs are most conveniently used from the source checkout. The distribution is not a one-file native executable.

An editable checkout is still an active development tree. Update it with Git/pip, not the in-app file replacement mechanism. A regular site-packages install is explicitly rejected by the standalone updater.

## Browser and FFmpeg selection

Playwright-managed Chromium is the preferred browser. The renderer can try installed Chromium, Google Chrome, and Edge when managed Chromium is unavailable. To select a known executable for this process:

```powershell
$env:HTML_VIDEO_BROWSER = 'C:\Path\To\chrome.exe'
$env:HTML_VIDEO_FFMPEG = 'C:\Tools\ffmpeg.exe'
.venv\Scripts\python.exe app.py
```

```bash
HTML_VIDEO_BROWSER=/usr/bin/chromium HTML_VIDEO_FFMPEG=/usr/bin/ffmpeg .venv/bin/python app.py
```

`FFMPEG_BINARY` is also recognized when `HTML_VIDEO_FFMPEG` is unset. An explicit executable override is authoritative; an invalid path is an error rather than a silent switch. Without an override, system and imageio-ffmpeg candidates are examined for the selected job's encoders and filters. In 1.5.2 the check includes color-conversion and audio filters, not just visible sharpening filters. PNG previews require the PNG encoder plus their effective filter chain.

Use Settings & Updates → dependency health to launch a test browser and check codec availability. A successful “FFmpeg exists” test alone does not prove a particular build contains CAS, ProRes, VP9, or an audio encoder.

## Upgrading

Close all copies of the app, extract a new release into a fresh folder, and run its installer. Keep the old folder and verified outputs until the new version works. Projects store configurations and can be opened in the new app; they do not bundle their source/audio assets.

Per-user preferences are outside the app folder: `%APPDATA%/HTMLVideoExportStudio` on Windows, `~/Library/Application Support/HTMLVideoExportStudio` on macOS, and `$XDG_CONFIG_HOME/HTMLVideoExportStudio` or `~/.config/HTMLVideoExportStudio` on Linux. Do not delete these just to upgrade. Inspect `settings_store.py` for exact resolution.

For versions 1.5.0 and earlier, prefer a fresh-folder upgrade because their application/updater implementations had defects. The standalone updater refuses changes to `requirements.txt`; use a fresh installation for dependency changes. Updates preserve user trees but are not atomic against power loss across the entire directory.

## Uninstalling

Close the app and remove its source/virtual-environment folder. Exported videos and projects are ordinary files and remain wherever you saved them. Remove per-user settings separately only when intentionally resetting preferences. Playwright's shared browser cache can be used by other projects; do not remove it blindly.
