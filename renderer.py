from __future__ import annotations

import functools
import hashlib
import html as html_lib
import io
import json
import math
import os
import posixpath
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable, Optional, Sequence

import imageio_ffmpeg
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Frame,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from models import (
    AudioMode,
    CaptureMode,
    Diagnostic,
    ExportResult,
    GeometryMode,
    JobConfig,
    LoadStrategy,
    ProbeResult,
    SourceKind,
    TimelineMode,
)
from presets import OUTPUT_PROFILES, PROCESSING_PRESETS
from processing import (
    ContentAnalysis,
    analyze_frames,
    build_filter_chain,
    resolve_processing_config,
)
from version import APP_NAME, APP_VERSION


OM_EXPORT_SELECTOR = "[data-om-exportable-video-with-duration-secs]"
GENERIC_MARKER_SELECTORS = (
    "[data-video-export]",
    "[data-export-video]",
    "[data-export-root]",
    OM_EXPORT_SELECTOR,
)
AUTO_ELEMENT_SELECTORS = GENERIC_MARKER_SELECTORS + ("video", "canvas", "svg")
OM_SEEK_EVENT = "data-om-seek-to-time-frame"


class ExportError(RuntimeError):
    pass


class ExportCancelled(RuntimeError):
    pass


class EnvironmentError(ExportError):
    pass


ProgressCallback = Callable[[dict[str, Any]], None]
LogCallback = Callable[[str], None]


def _log(callback: Optional[LogCallback], message: str) -> None:
    if callback:
        callback(message)


def positive_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def parse_scene_duration(value: Any) -> Optional[float]:
    if value is None:
        return None
    parsed = value
    for _ in range(2):
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                return None
        else:
            break
    if not isinstance(parsed, list) or not parsed:
        return None
    total = 0.0
    for scene in parsed:
        if not isinstance(scene, dict):
            return None
        duration = positive_float(scene.get("dur"))
        if duration is None:
            return None
        total += duration
    return total if math.isfinite(total) and total > 0 else None


def inject_base_href(source_html: str, source_path: Path) -> str:
    if re.search(r"<base\b", source_html, flags=re.IGNORECASE):
        return source_html
    base_uri = source_path.resolve().parent.as_uri().rstrip("/") + "/"
    tag = f'<base href="{html_lib.escape(base_uri, quote=True)}">'
    head = re.search(r"<head\b[^>]*>", source_html, flags=re.IGNORECASE)
    if head:
        index = head.end()
        return source_html[:index] + tag + source_html[index:]
    return tag + source_html


def png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ExportError("Chromium did not return a valid PNG frame.")
    return struct.unpack(">II", data[16:24])


def get_ffmpeg_executable() -> str:
    from media_pipeline import select_ffmpeg
    return select_ffmpeg(set(), set())


def _output_dimensions(source_width: int, source_height: int, scale: float) -> tuple[int, int]:
    return max(1, int(math.floor(source_width * scale + 0.5))), max(1, int(math.floor(source_height * scale + 0.5)))


def _safe_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return value or "export"


def _format_scale(scale: float) -> str:
    return str(int(scale)) if float(scale).is_integer() else f"{scale:g}"


def render_output_name(job: JobConfig, profile_key: Optional[str] = None, processing_key: Optional[str] = None) -> str:
    profile_key = profile_key or job.render.output_profile_key
    profile = OUTPUT_PROFILES[profile_key]
    source = job.source
    if source.kind == SourceKind.FILE:
        stem = Path(source.value).stem
    else:
        parsed = urllib.parse.urlparse(source.value)
        stem = Path(parsed.path).stem or parsed.netloc or "web-capture"
    stem = _safe_slug(stem)
    processing_key = processing_key or job.render.processing.preset_key
    values = {
        "stem": stem,
        "scale": _format_scale(job.render.scale),
        "fps": str(job.render.fps),
        "profile": profile_key.replace("_mp4", "").replace("_mov", "").replace("_webm", ""),
        "processing": processing_key,
        "ext": profile.extension,
    }
    try:
        name = job.render.filename_template.format(**values)
    except KeyError as exc:
        raise ExportError(f"Unknown filename template field: {exc}") from exc
    if Path(name).name != name or any(char in name for char in '\\/:*?"<>|') or any(ord(char)<32 for char in name):
        raise ExportError('Filename template must create a safe filename, not a path.')
    if not name.lower().endswith('.'+profile.extension):
        raise ExportError('Filename extension must match the output profile.')
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
        raise ExportError('Output filename is a reserved Windows device name; use another template or source name.')
    if len(name.encode('utf-8')) > 240:
        raise ExportError('Output filename is too long; use a shorter source name or template.')
    return name


def choose_output_path(job: JobConfig, processing_key: Optional[str] = None) -> Path:
    profile = OUTPUT_PROFILES[job.render.output_profile_key]
    if job.render.save_next_to_source:
        if job.source.kind != SourceKind.FILE:
            raise ExportError("URL jobs require an explicit output directory.")
        folder = Path(job.source.value).expanduser().resolve().parent
    else:
        folder = Path(job.render.output_directory).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / render_output_name(job, processing_key=processing_key)
    if job.render.overwrite or not candidate.exists():
        return candidate
    for index in range(2, 10_000):
        alternate = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
        if not alternate.exists():
            return alternate
    raise ExportError("Could not find a free output filename.")


class _SecureHandler(SimpleHTTPRequestHandler):
    server_version = "HTMLVideoExportStudio/1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def translate_path(self, path: str) -> str:
        # SimpleHTTPRequestHandler already normalizes traversal; this adds an
        # explicit resolved-path containment check and blocks symlink escapes.
        root = Path(self.directory).resolve()  # type: ignore[attr-defined]
        parsed = urllib.parse.urlsplit(path)
        decoded = urllib.parse.unquote(parsed.path)
        normalized = posixpath.normpath(decoded).lstrip("/")
        candidate = (root / normalized).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return str(root / "__blocked__")
        return str(candidate)


class LoopbackServer:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        handler = functools.partial(_SecureHandler, directory=str(self.directory))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "LoopbackServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def url_for(self, path: Path) -> str:
        relative = path.resolve().relative_to(self.directory)
        quoted = "/".join(urllib.parse.quote(part) for part in relative.parts)
        return f"http://127.0.0.1:{self.server.server_port}/{quoted}"


@dataclass
class CaptureTarget:
    frame: Frame
    locator: Optional[Locator]
    mode: CaptureMode
    selector: str
    description: str
    kind: str
    geometry_mode: GeometryMode
    clip: Optional[dict[str, float]] = None
    intrinsic_width: int = 0
    intrinsic_height: int = 0
    guard_token: str = ""
    strip_outer_shadow: bool = False


@dataclass
class LoadedSource:
    context: BrowserContext
    page: Page
    strategy: str
    server: Optional[LoopbackServer] = None
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)

    def close(self) -> None:
        try:
            self.context.close()
        finally:
            if self.server is not None:
                self.server.__exit__(None, None, None)
                self.server = None


@dataclass
class PreparedSource:
    loaded: LoadedSource
    target: CaptureTarget
    duration: Optional[float]
    duration_source: str
    timeline_mode: TimelineMode
    sync_seek: bool
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    diagnostics: list[Diagnostic]
    content_analysis: Optional[ContentAnalysis] = None
    clock_elapsed_ms: int = 0


class _StderrCollector:
    def __init__(self, stream: Any):
        self.stream = stream
        self.parts: list[bytes] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            while True:
                data = self.stream.read(64 * 1024)
                if not data:
                    break
                self.parts.append(data)
                if sum(map(len, self.parts)) > 2_000_000:
                    self.parts = [b"".join(self.parts)[-1_000_000:]]
        except Exception:
            pass

    def start(self) -> None:
        self.thread.start()

    def finish(self) -> str:
        self.thread.join(timeout=5)
        return b"".join(self.parts).decode("utf-8", errors="replace").strip()


class _CaptureCancel:
    def __init__(self, stop, user):
        self.stop, self.user = stop, user

    def is_set(self):
        return self.stop.is_set() or (self.user is not None and self.user.is_set())


def _capture_signature(prepared):
    # Plain immutable metadata only: never share Page/Locator objects.
    return (prepared.source_width, prepared.source_height,
            prepared.output_width, prepared.output_height,
            prepared.timeline_mode, prepared.target.kind,
            prepared.target.selector, prepared.target.geometry_mode,
            prepared.loaded.strategy, prepared.duration)


@contextmanager
def _capture_session(job, signature, browser_executable, page_timeout_ms,
                     start, end, stop, cancel_event):
    import copy
    worker_job = copy.deepcopy(job)
    renderer = HtmlVideoRenderer(browser_executable=browser_executable,
                                 page_timeout_ms=page_timeout_ms)
    renderer._cancel_event = _CaptureCancel(stop, cancel_event)
    prepared = None
    try:
        renderer._check_cancel()
        prepared = renderer._prepare(worker_job, deep_analysis=False)
        errors = [item.message for item in prepared.diagnostics if item.severity == 'error']
        if errors or _capture_signature(prepared) != signature:
            raise ExportError('Independent capture source differs from the original: ' +
                              '; '.join(errors or ['target, geometry, duration or timeline changed']))
        previous = None
        cached = None

        def capture(index):
            nonlocal previous, cached
            renderer._check_cancel()
            source_time = renderer._frame_source_time(
                index, worker_job.render.fps, start, end,
                worker_job.timeline.hold_start, worker_job.timeline.hold_end)
            if cached is not None and source_time == previous:
                return cached, 0.0, 0.0
            then = time.perf_counter()
            renderer._seek(prepared, worker_job, source_time, previous, None)
            sought = time.perf_counter()
            data = renderer._capture_frame(prepared, worker_job)
            elapsed = time.perf_counter() - sought
            previous, cached = source_time, data
            return data, sought - then, elapsed

        yield capture
    finally:
        try:
            if prepared is not None:
                prepared.loaded.close()
        finally:
            renderer.close()


class HtmlVideoRenderer:
    def __init__(
        self,
        browser_executable: Optional[str] = None,
        page_timeout_ms: int = 120_000,
        log_callback: Optional[LogCallback] = None,
    ):
        self.browser_executable = browser_executable
        self.page_timeout_ms = page_timeout_ms
        self.log_callback = log_callback
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    def __enter__(self) -> "HtmlVideoRenderer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self._browser is not None:
            return

        self._playwright = sync_playwright().start()
        try:
            self._browser, browser_label = launch_chromium_with_fallback(
                self._playwright,
                self.browser_executable,
                headless=True,
                args=[
                    '--allow-file-access-from-files',
                    '--disable-background-timer-throttling',
                    '--disable-renderer-backgrounding',
                    '--disable-backgrounding-occluded-windows',
                ],
            )
            _log(self.log_callback, f'Browser: {browser_label}')
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        browser, playwright = self._browser, self._playwright
        self._browser = self._playwright = None
        try:
            if browser is not None: browser.close()
        finally:
            if playwright is not None: playwright.stop()

    def _new_context(self, job: JobConfig) -> BrowserContext:
        self.start()
        assert self._browser is not None
        context = self._browser.new_context(
            viewport={
                "width": job.capture.viewport_width,
                "height": job.capture.viewport_height,
            },
            device_scale_factor=job.render.scale,
            reduced_motion="no-preference",
            color_scheme="light",
        )
        context.set_default_timeout(self.page_timeout_ms)
        context.set_default_navigation_timeout(self.page_timeout_ms)
        # Native watchdog remains usable when the page's virtual clock is paused.
        context.add_init_script("window.__hvesNativeTimeout = window.setTimeout.bind(window);"
                                "window.__hvesNativeClearTimeout = window.clearTimeout.bind(window);")
        return context

    def _attach_logging(self, page: Page, loaded: LoadedSource) -> None:
        page.on(
            "console",
            lambda msg: loaded.console_errors.append(msg.text)
            if msg.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: loaded.page_errors.append(str(error)))

    def _install_browser_clock(self, page: Page) -> None:
        if not hasattr(page, "clock"):
            raise ExportError("Browser Clock requires Playwright 1.45 or newer.")
        # Install and pause BEFORE any user script runs. The numeric arguments
        # are epoch seconds in Playwright Python; run_for() takes milliseconds.
        from datetime import datetime, timezone, timedelta
        epoch = datetime.now(timezone.utc)
        page.clock.install(time=epoch)
        page.clock.pause_at(epoch + timedelta(seconds=1))

    def _try_load_embedded(self, job: JobConfig) -> LoadedSource:
        source_path = Path(job.source.value).expanduser().resolve()
        try:
            raw = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = source_path.read_text(encoding="utf-8", errors="replace")
        context = self._new_context(job)
        page = context.new_page()
        loaded = LoadedSource(context=context, page=page, strategy=LoadStrategy.EMBEDDED.value)
        self._attach_logging(page, loaded)
        try:
            if job.timeline.mode == TimelineMode.BROWSER_CLOCK:
                self._install_browser_clock(page)
            page.set_content(
                inject_base_href(raw, source_path),
                wait_until="domcontentloaded",
                timeout=self.page_timeout_ms,
            )
            return loaded
        except Exception:
            loaded.close()
            raise

    def _try_load_file_url(self, job: JobConfig) -> LoadedSource:
        source_path = Path(job.source.value).expanduser().resolve()
        context = self._new_context(job)
        page = context.new_page()
        loaded = LoadedSource(context=context, page=page, strategy=LoadStrategy.FILE_URL.value)
        self._attach_logging(page, loaded)
        try:
            if job.timeline.mode == TimelineMode.BROWSER_CLOCK:
                self._install_browser_clock(page)
            page.goto(source_path.as_uri(), wait_until="domcontentloaded", timeout=self.page_timeout_ms)
            return loaded
        except Exception:
            loaded.close()
            raise

    def _try_load_loopback(self, job: JobConfig) -> LoadedSource:
        source_path = Path(job.source.value).expanduser().resolve()
        server = LoopbackServer(source_path.parent)
        server.__enter__()
        try:
            context = self._new_context(job)
            page = context.new_page()
        except Exception:
            server.__exit__(None, None, None)
            raise
        loaded = LoadedSource(
            context=context,
            page=page,
            strategy=LoadStrategy.LOOPBACK_HTTP.value,
            server=server,
        )
        self._attach_logging(page, loaded)
        try:
            if job.timeline.mode == TimelineMode.BROWSER_CLOCK:
                self._install_browser_clock(page)
            page.goto(server.url_for(source_path), wait_until="domcontentloaded", timeout=self.page_timeout_ms)
        except Exception:
            loaded.close()
            raise
        return loaded

    def _try_load_remote(self, job: JobConfig) -> LoadedSource:
        context = self._new_context(job)
        page = context.new_page()
        loaded = LoadedSource(context=context, page=page, strategy=LoadStrategy.REMOTE_URL.value)
        self._attach_logging(page, loaded)
        try:
            if job.timeline.mode == TimelineMode.BROWSER_CLOCK:
                self._install_browser_clock(page)
            page.goto(job.source.value, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
            return loaded
        except Exception:
            loaded.close()
            raise

    def _load_source(self, job: JobConfig) -> LoadedSource:
        job.validate(for_export=False)
        if job.source.kind == SourceKind.URL:
            return self._try_load_remote(job)

        source_path = Path(job.source.value).expanduser().resolve()
        if not source_path.exists():
            raise ExportError(f"HTML source does not exist: {source_path}")

        requested = job.source.load_strategy
        if requested == LoadStrategy.EMBEDDED:
            strategies = [self._try_load_embedded]
        elif requested == LoadStrategy.FILE_URL:
            strategies = [self._try_load_file_url]
        elif requested == LoadStrategy.LOOPBACK_HTTP:
            strategies = [self._try_load_loopback]
        else:
            # Loopback best matches normal browser loading. Embedded is the most
            # portable fallback for bundled standalone exports and locked-down hosts.
            strategies = [
                self._try_load_loopback,
                self._try_load_file_url,
                self._try_load_embedded,
            ]

        errors: list[str] = []
        for strategy in strategies:
            try:
                loaded = strategy(job)
                _log(self.log_callback, f"Loaded using {loaded.strategy}.")
                return loaded
            except Exception as exc:
                errors.append(f"{strategy.__name__.replace('_try_load_', '')}: {exc}")

        raise ExportError(
            "Could not load the HTML source with any enabled strategy.\n\n"
            + "\n".join(f"- {item}" for item in errors)
        )

    def _wait_for_settle(self, page: Page | Frame) -> None:
        frame = page.main_frame if isinstance(page, Page) else page
        self._bounded_evaluate(frame, """
            async () => {
                if (document.fonts) await document.fonts.ready;
                await Promise.all(Array.from(document.images || []).map(async image => {
                    try { await image.decode(); } catch (_) {}
                }));
            }
        """)

    def _bounded_evaluate(self, frame: Frame, script: str, arg: Any = None) -> Any:
        # Reject never-settling seek/asset promises instead of hanging indefinitely.
        return frame.evaluate("""async payload => {
            const timer = window.__hvesNativeTimeout || window.setTimeout.bind(window);
            const clear = window.__hvesNativeClearTimeout || window.clearTimeout.bind(window);
            let id;
            const watchdog = new Promise((_, reject) => {
                id = timer(() => reject(new Error('Export operation timed out')), payload.timeout);
            });
            try {
                const fn = (0, eval)('(' + payload.script + ')');
                return await Promise.race([Promise.resolve().then(() => fn(payload.arg)), watchdog]);
            } finally { clear(id); }
        }""", {"script": script, "arg": arg, "timeout": self.page_timeout_ms})

    def _wait_for_target(self, loaded: LoadedSource, job: JobConfig) -> CaptureTarget:
        deadline = time.monotonic() + self.page_timeout_ms / 1000
        last_error = None
        while True:
            self._check_cancel()
            bundling = loaded.page.locator('#__bundler_loading').count() > 0
            if not bundling and job.capture.mode in {CaptureMode.AUTO,CaptureMode.MARKER}:
                expected_om = loaded.page.evaluate('() => window.OM_SCENES != null')
                if expected_om:
                    bundling = not any(f.locator(OM_EXPORT_SELECTOR).count() for f in loaded.page.frames)
            try:
                if not bundling:
                    target = self._choose_target(loaded, job)
                    if target.kind in {'video','audio'} and target.locator is not None:
                        ready = target.locator.evaluate('el => el.readyState >= 1 || Boolean(el.error)')
                        if ready: return target
                    else:
                        return target
            except ExportError as exc:
                last_error = exc
            if time.monotonic() >= deadline:
                raise ExportError(str(last_error or 'Timed out waiting for HTML to finish loading.'))
            if job.timeline.mode == TimelineMode.BROWSER_CLOCK:
                loaded.page.clock.run_for(16)
            loaded.page.wait_for_timeout(40) if job.timeline.mode != TimelineMode.BROWSER_CLOCK else time.sleep(0.01)

    def _check_cancel(self) -> None:
        event = getattr(self, '_cancel_event', None)
        if event is not None and event.is_set():
            raise ExportCancelled('Export cancelled.')

    def _visible_area(self, locator: Locator) -> tuple[bool, float]:
        try:
            data = locator.evaluate(
                """
                el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    const visible = r.width > 0 && r.height > 0 &&
                        s.display !== 'none' && s.visibility !== 'hidden' &&
                        Number(s.opacity || '1') > 0;
                    return {visible, area: Math.max(0, r.width * r.height)};
                }
                """
            )
            return bool(data.get("visible")), float(data.get("area", 0))
        except Exception:
            return False, 0.0

    def _choose_target(self, loaded: LoadedSource, job: JobConfig) -> CaptureTarget:
        page = loaded.page
        mode = job.capture.mode

        if mode in {CaptureMode.VIEWPORT, CaptureMode.FULL_PAGE}:
            return CaptureTarget(
                frame=page.main_frame,
                locator=None,
                mode=mode,
                selector="",
                description="Full viewport" if mode == CaptureMode.VIEWPORT else "Full scrollable page",
                kind="viewport" if mode == CaptureMode.VIEWPORT else "full_page",
                geometry_mode=GeometryMode.PRESERVE_LAYOUT,
                strip_outer_shadow=job.capture.strip_outer_shadow,
            )

        frames = page.frames

        if mode == CaptureMode.SELECTOR:
            matches: list[tuple[Frame, Locator]] = []
            for frame in frames:
                locator = frame.locator(job.capture.selector)
                try:
                    count = locator.count()
                except Exception:
                    continue
                for index in range(count):
                    matches.append((frame, locator.nth(index)))
            if not matches:
                raise ExportError(f"CSS selector matched no elements: {job.capture.selector}")
            if job.capture.selector_index >= len(matches):
                raise ExportError(
                    f"Selector index {job.capture.selector_index} is out of range; "
                    f"the selector matched {len(matches)} element(s)."
                )
            frame, locator = matches[job.capture.selector_index]
            kind = locator.evaluate("el => el.tagName.toLowerCase()")
            geometry = job.capture.geometry_mode
            if geometry == GeometryMode.AUTO:
                geometry = GeometryMode.PRESERVE_LAYOUT
            return CaptureTarget(
                frame=frame,
                locator=locator,
                mode=mode,
                selector=job.capture.selector,
                description=f"Selector: {job.capture.selector}",
                kind=str(kind),
                geometry_mode=geometry,
                strip_outer_shadow=job.capture.strip_outer_shadow,
            )

        selectors = GENERIC_MARKER_SELECTORS if mode == CaptureMode.MARKER else AUTO_ELEMENT_SELECTORS
        candidates: list[tuple[int, float, Frame, Locator, str]] = []
        for priority, selector in enumerate(selectors):
            for frame in frames:
                locator = frame.locator(selector)
                try:
                    count = min(locator.count(), 100)
                except Exception:
                    continue
                for index in range(count):
                    item = locator.nth(index)
                    visible, area = self._visible_area(item)
                    if visible or (selector in GENERIC_MARKER_SELECTORS and area > 0):
                        candidates.append((priority, -area, frame, item, selector))

        if not candidates:
            if mode == CaptureMode.MARKER:
                raise ExportError(
                    "No export marker was found. Add data-video-export to the capture root, "
                    "choose a CSS selector, or use Viewport capture."
                )
            return CaptureTarget(
                frame=page.main_frame,
                locator=None,
                mode=CaptureMode.VIEWPORT,
                selector="",
                description="Full viewport (auto fallback)",
                kind="viewport",
                geometry_mode=GeometryMode.PRESERVE_LAYOUT,
                strip_outer_shadow=job.capture.strip_outer_shadow,
            )

        candidates.sort(key=lambda item: (item[0], item[1]))
        _priority, _area, frame, locator, selector = candidates[0]
        kind = str(locator.evaluate("el => el.tagName.toLowerCase()"))
        has_strong_marker = selector in GENERIC_MARKER_SELECTORS
        geometry = job.capture.geometry_mode
        if geometry == GeometryMode.AUTO:
            geometry = GeometryMode.LOCK_INTRINSIC if has_strong_marker else GeometryMode.PRESERVE_LAYOUT
        return CaptureTarget(
            frame=frame,
            locator=locator,
            mode=mode,
            selector=selector,
            description=f"Auto: {selector}",
            kind=kind,
            geometry_mode=geometry,
            strip_outer_shadow=job.capture.strip_outer_shadow,
        )

    def _element_dimensions(self, target: CaptureTarget, job: JobConfig) -> tuple[int, int]:
        if job.capture.manual_width and job.capture.manual_height:
            return job.capture.manual_width, job.capture.manual_height

        if target.locator is None:
            if target.mode == CaptureMode.VIEWPORT:
                return job.capture.viewport_width, job.capture.viewport_height
            data = target.frame.evaluate(
                """
                () => ({
                    width: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
                    height: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
                })
                """
            )
            return max(1, int(math.ceil(data["width"]))), max(1, int(math.ceil(data["height"])))

        values = target.locator.evaluate(
            """
            el => {
                const positive = value => {
                    const n = Number.parseFloat(value);
                    return Number.isFinite(n) && n > 0 ? n : null;
                };
                const dataW = positive(el.dataset.omExportWidth || el.dataset.exportWidth || el.dataset.videoWidth);
                const dataH = positive(el.dataset.omExportHeight || el.dataset.exportHeight || el.dataset.videoHeight);
                const tag = el.tagName.toLowerCase();
                let intrinsicW = null;
                let intrinsicH = null;
                if (tag === 'canvas') {
                    intrinsicW = positive(el.width);
                    intrinsicH = positive(el.height);
                } else if (tag === 'video') {
                    intrinsicW = positive(el.videoWidth);
                    intrinsicH = positive(el.videoHeight);
                } else if (tag === 'svg') {
                    const widthAttr = el.getAttribute('width');
                    const heightAttr = el.getAttribute('height');
                    if (widthAttr && !widthAttr.includes('%')) intrinsicW = positive(widthAttr);
                    if (heightAttr && !heightAttr.includes('%')) intrinsicH = positive(heightAttr);
                    try {
                        if ((!intrinsicW || !intrinsicH) && el.viewBox && el.viewBox.baseVal) {
                            intrinsicW = intrinsicW || positive(el.viewBox.baseVal.width);
                            intrinsicH = intrinsicH || positive(el.viewBox.baseVal.height);
                        }
                    } catch (_) {}
                }
                const rect = el.getBoundingClientRect();
                return {
                    dataW, dataH, intrinsicW, intrinsicH,
                    rectW: positive(rect.width), rectH: positive(rect.height),
                    attrW: positive(el.getAttribute('width')),
                    attrH: positive(el.getAttribute('height')),
                };
            }
            """
        )

        if target.geometry_mode == GeometryMode.PRESERVE_LAYOUT:
            width = positive_float(values.get("rectW"))
            height = positive_float(values.get("rectH"))
        else:
            width = (
                positive_float(values.get("dataW"))
                or positive_float(values.get("intrinsicW"))
                or positive_float(values.get("attrW"))
                or positive_float(values.get("rectW"))
            )
            height = (
                positive_float(values.get("dataH"))
                or positive_float(values.get("intrinsicH"))
                or positive_float(values.get("attrH"))
                or positive_float(values.get("rectH"))
            )

        if width is None or height is None:
            raise ExportError(
                "Could not determine capture dimensions. Set manual width and height in Job Settings."
            )
        return max(1, int(round(width))), max(1, int(round(height)))

    def _prepare_capture_geometry(
        self,
        loaded: LoadedSource,
        target: CaptureTarget,
        width: int,
        height: int,
    ) -> None:
        page = loaded.page
        if target.locator is None:
            if target.mode == CaptureMode.FULL_PAGE:
                actual = target.frame.evaluate('() => [Math.max(document.documentElement.scrollWidth,document.body?.scrollWidth||0),Math.max(document.documentElement.scrollHeight,document.body?.scrollHeight||0)]')
                if (width,height) != tuple(actual):
                    raise ExportError('Full-page manual dimensions are not a resize control. Use Viewport capture for a fixed canvas.')
            if target.mode == CaptureMode.VIEWPORT:
                page.set_viewport_size({"width": width, "height": height})
            return

        if target.intrinsic_width == 0:
            target.intrinsic_width, target.intrinsic_height = width, height
        if target.geometry_mode == GeometryMode.PRESERVE_LAYOUT:
            box = target.locator.bounding_box()
            if not box:
                raise ExportError("Capture element has no visible bounding box.")
            target.clip = {
                "x": float(box["x"]),
                "y": float(box["y"]),
                "width": float(box["width"]),
                "height": float(box["height"]),
            }
            return

        token = uuid.uuid4().hex
        target.guard_token = token
        target.intrinsic_width = width
        target.intrinsic_height = height
        target.locator.evaluate(
            "(el, token) => el.setAttribute('data-hves-capture-root', token)", token
        )
        # A stylesheet-level !important guard wins over preview scripts that keep
        # rewriting inline transforms. The per-frame reapply below also restores
        # marker attributes if a framework replaces the capture node.
        escaped = token.replace('\\', '\\\\').replace('"', '\\"')
        target.frame.add_style_tag(content=f"""
            [data-hves-geometry-guard=\"{escaped}\"] {{
                transform: none !important;
                zoom: 1 !important;
                perspective: none !important;
                transform-origin: 0 0 !important;
                overflow: visible !important;
                clip: auto !important;
                clip-path: none !important;
                contain: none !important;
            }}
            [data-hves-capture-root=\"{escaped}\"] {{
                position: fixed !important;
                left: 0 !important;
                top: 0 !important;
                width: {width}px !important;
                height: {height}px !important;
                min-width: {width}px !important;
                min-height: {height}px !important;
                max-width: none !important;
                max-height: none !important;
                margin: 0 !important;
                transform: none !important;
                transform-origin: 0 0 !important;
                zoom: 1 !important;
                {"box-shadow: none !important;" if target.strip_outer_shadow else ""}
                z-index: 2147483647 !important;
                display: block !important;
            }}
        """)
        page.set_viewport_size({"width": width, "height": height})
        self._reapply_geometry_guard(target)
        self._guard_frame_chain(target)

    def _reapply_geometry_guard(self, target: CaptureTarget) -> None:
        if target.locator is None or target.geometry_mode != GeometryMode.LOCK_INTRINSIC:
            return
        target.locator.evaluate(
            """
            (el, payload) => {
                const important = (node, name, value) => node.style.setProperty(name, value, 'important');
                important(el, 'position', 'fixed');
                important(el, 'left', '0px');
                important(el, 'top', '0px');
                important(el, 'width', payload.width + 'px');
                important(el, 'height', payload.height + 'px');
                important(el, 'min-width', payload.width + 'px');
                important(el, 'min-height', payload.height + 'px');
                important(el, 'max-width', 'none');
                important(el, 'max-height', 'none');
                important(el, 'margin', '0');
                important(el, 'transform', 'none');
                important(el, 'transform-origin', '0 0');
                important(el, 'zoom', '1');
                if (payload.stripShadow) important(el, 'box-shadow', 'none');
                important(el, 'z-index', '2147483647');
                important(el, 'display', 'block');

                el.setAttribute('data-hves-capture-root', payload.token);
                let branch = el;
                let parent = el.parentElement;
                while (parent) {
                    for (const sibling of parent.children) {
                        if (sibling !== branch && sibling.tagName !== 'STYLE' && sibling.tagName !== 'SCRIPT') {
                            important(sibling, 'opacity', '0');
                            important(sibling, 'pointer-events', 'none');
                        }
                    }
                    important(parent, 'transform', 'none');
                    important(parent, 'zoom', '1');
                    important(parent, 'perspective', 'none');
                    important(parent, 'overflow', 'visible');
                    important(parent, 'clip', 'auto');
                    important(parent, 'clip-path', 'none');
                    important(parent, 'contain', 'none');
                    parent.setAttribute('data-hves-geometry-guard', payload.token);
                    if (parent === document.body || parent === document.documentElement) {
                        important(parent, 'margin', '0');
                        important(parent, 'padding', '0');
                        important(parent, 'overflow', 'hidden');
                        important(parent, 'width', payload.width + 'px');
                        important(parent, 'height', payload.height + 'px');
                    }
                    branch = parent;
                    parent = parent.parentElement;
                }
            }
            """,
            {
                "width": target.intrinsic_width,
                "height": target.intrinsic_height,
                "token": target.guard_token,
                "stripShadow": target.strip_outer_shadow,
            },
        )

    def _guard_frame_chain(self, target: CaptureTarget) -> None:
        frame = target.frame
        while frame.parent_frame is not None:
            element = frame.frame_element()
            try:
                element.evaluate("""(el, size) => {
                    const put=(n,k,v)=>n.style.setProperty(k,v,'important');
                    for (const [k,v] of Object.entries({position:'fixed', left:'0px', top:'0px',
                        width:size[0]+'px', height:size[1]+'px', border:'0', margin:'0',
                        transform:'none', zoom:'1', maxWidth:'none', maxHeight:'none',
                        minWidth:size[0]+'px',minHeight:size[1]+'px',zIndex:'2147483647'})) {
                        put(el, k.replace(/[A-Z]/g, m=>'-'+m.toLowerCase()),v);
                    }
                    let branch=el;
                    for(let p=el.parentElement;p;p=p.parentElement) {
                        for(const n of p.children) if(n!==branch && !['STYLE','SCRIPT'].includes(n.tagName))
                            put(n,'opacity','0');
                        for(const [k,v] of Object.entries({transform:'none',zoom:'1',perspective:'none',
                            overflow:'visible',contain:'none',clip:'auto','clip-path':'none'})) put(p,k,v);
                        branch=p;
                    }
                }""", [target.intrinsic_width, target.intrinsic_height])
            finally:
                element.dispose()
            frame = frame.parent_frame

    @staticmethod
    def _evaluate_target(target: CaptureTarget, script: str) -> Any:
        """Evaluate with the root without retaining a remote ElementHandle."""
        if target.locator is not None:
            return target.locator.evaluate(script)
        return target.frame.evaluate(script, None)

    def _resolve_duration(
        self,
        loaded: LoadedSource,
        target: CaptureTarget,
        job: JobConfig,
    ) -> tuple[Optional[float], str]:
        if job.timeline.manual_duration is not None:
            return float(job.timeline.manual_duration), "manual duration"

        if target.locator is not None:
            for attribute in (
                "data-om-exportable-video-with-duration-secs",
                "data-video-duration-secs",
                "data-video-duration",
                "data-duration-seconds",
                "data-duration",
            ):
                value = positive_float(target.locator.get_attribute(attribute))
                if value is not None:
                    return value, f"capture root {attribute}"

        global_data = target.frame.evaluate(
            """
            () => ({
                duration: window.__HTML_VIDEO_EXPORT_DURATION__ ??
                    window.HTML_VIDEO_EXPORT?.duration ?? null,
                scenes: window.OM_SCENES ?? null,
            })
            """
        )
        global_duration = positive_float(global_data.get("duration"))
        if global_duration is not None:
            return global_duration, "JavaScript export duration"
        scene_duration = parse_scene_duration(global_data.get("scenes"))
        if scene_duration is not None:
            return scene_duration, "window.OM_SCENES"

        media_duration = self._evaluate_target(target,
            """
            el => {
                const media = el && ['video','audio'].includes(el.tagName?.toLowerCase())
                    ? el : (el || document).querySelector('video, audio');
                if (!media) return null;
                const d = Number(media.duration);
                return Number.isFinite(d) && d > 0 ? d : null;
            }
            """,
        )
        value = positive_float(media_duration)
        if value is not None:
            return value, "HTML media duration"

        animation_duration = self._evaluate_target(target,
            """
            el => {
                const root = el || document.documentElement;
                const animations = root.getAnimations ? root.getAnimations({subtree:true}) : [];
                let maxEnd = 0;
                for (const animation of animations) {
                    try {
                        const timing = animation.effect?.getComputedTiming?.();
                        const end = Number(timing?.endTime);
                        // A finite sibling does not bound a looping animation.
                        if (end === Infinity) return null;
                        if (Number.isFinite(end) && end > maxEnd) maxEnd = end;
                    } catch (_) {}
                }
                return maxEnd > 0 ? maxEnd / 1000 : null;
            }
            """,
        )
        value = positive_float(animation_duration)
        if value is not None:
            return value, "Web Animations timing"

        if job.timeline.trim_end is not None:
            return float(job.timeline.trim_end), "explicit trim end (capture bound)"
        return None, "not detected"

    def _resolve_timeline_mode(
        self,
        loaded: LoadedSource,
        target: CaptureTarget,
        job: JobConfig,
        duration: Optional[float],
    ) -> TimelineMode:
        if job.timeline.mode != TimelineMode.AUTO:
            return job.timeline.mode

        if target.locator is not None:
            has_om = target.locator.get_attribute(
                "data-om-exportable-video-with-duration-secs"
            ) is not None
            if has_om:
                return TimelineMode.OM_EVENT

        functions = target.frame.evaluate(
            """
            () => ({
                htmlVideoExport: typeof window.__HTML_VIDEO_EXPORT_SEEK__ === 'function',
                seekTo: typeof window.seekTo === 'function',
                objectSeek: typeof window.HTML_VIDEO_EXPORT?.seek === 'function',
            })
            """
        )
        if any(bool(value) for value in functions.values()):
            return TimelineMode.JAVASCRIPT_FUNCTION

        if target.kind in {"video", "audio"}:
            return TimelineMode.MEDIA
        media_exists = (target.locator or target.frame).locator("video, audio").count() > 0
        if media_exists:
            return TimelineMode.MEDIA

        animation_count = self._evaluate_target(target,
            """
            el => {
                const root = el || document.documentElement;
                return root.getAnimations ? root.getAnimations({subtree:true}).length : 0;
            }
            """,
        )
        if int(animation_count or 0) > 0:
            return TimelineMode.WEB_ANIMATIONS

        if duration is not None:
            return TimelineMode.REALTIME
        return TimelineMode.STATIC

    def _validate_timeline(
        self,
        loaded: LoadedSource,
        target: CaptureTarget,
        job: JobConfig,
        mode: TimelineMode,
        duration: Optional[float],
        diagnostics: list[Diagnostic],
    ) -> bool:
        sync_seek = False
        if target.locator is not None:
            sync_seek = target.locator.get_attribute("data-om-sync-seek") == "true"

        if duration is None:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "duration_missing",
                    "Duration was not detected. Set a manual duration in Job Settings.",
                )
            )

        if mode in {TimelineMode.OM_EVENT,TimelineMode.CUSTOM_EVENT} and target.locator is None:
            diagnostics.append(Diagnostic('error','element_required','Event timing requires an element capture target.'))
        if mode == TimelineMode.MEDIA:
            media = target.locator if target.locator is not None and target.kind in {'video','audio'} else (target.locator or target.frame).locator('video, audio').first
            if media.count() == 0:
                diagnostics.append(Diagnostic('error','media_missing','No media element exists in the selected capture target.'))
        if mode == TimelineMode.REALTIME:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "realtime_nondeterministic",
                    "Realtime capture is best-effort: expensive frames can be late. Prefer a seek adapter or Browser Clock when possible.",
                )
            )
        if mode == TimelineMode.BROWSER_CLOCK and not hasattr(loaded.page, "clock"):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "clock_unavailable",
                    "This Playwright build does not provide Chromium Browser Clock support.",
                )
            )
        if mode == TimelineMode.JAVASCRIPT_FUNCTION:
            path = job.timeline.javascript_function.strip()
            if path == "window.seekTo":
                detected = target.frame.evaluate(
                    """
                    () => typeof window.__HTML_VIDEO_EXPORT_SEEK__ === 'function' ||
                          typeof window.HTML_VIDEO_EXPORT?.seek === 'function' ||
                          typeof window.seekTo === 'function'
                    """
                )
                if not detected:
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "seek_function_missing",
                            "No default JavaScript seek function was detected. Enter its function path in Job Settings.",
                        )
                    )
        return sync_seek

    def _prepare(self, job: JobConfig, deep_analysis: bool = False) -> PreparedSource:
        self._clock_active = job.timeline.mode == TimelineMode.BROWSER_CLOCK
        loaded = self._load_source(job)
        try:
            self._wait_for_settle(loaded.page)
            target = self._wait_for_target(loaded, job)
            self._wait_for_settle(target.frame)
            if job.capture.manual_width and target.locator is not None:
                if job.capture.geometry_mode == GeometryMode.PRESERVE_LAYOUT:
                    raise ExportError('Manual element dimensions require Lock intrinsic geometry.')
                target.geometry_mode = GeometryMode.LOCK_INTRINSIC
            source_width, source_height = self._element_dimensions(target, job)
            output_width, output_height = _output_dimensions(
                source_width, source_height, job.render.scale
            )
            self._prepare_capture_geometry(
                loaded, target, source_width, source_height
            )
            duration, duration_source = self._resolve_duration(loaded, target, job)
            if duration is None and target.locator is not None and target.locator.get_attribute('data-om-exportable-video-with-duration-secs') is not None:
                deadline = time.monotonic() + self.page_timeout_ms / 1000
                while duration is None and time.monotonic() < deadline:
                    self._check_cancel()
                    if self._clock_active:
                        loaded.page.clock.run_for(16)
                        time.sleep(0.01)
                    else:
                        loaded.page.wait_for_timeout(40)
                    duration, duration_source = self._resolve_duration(loaded, target, job)
            timeline_mode = self._resolve_timeline_mode(
                loaded, target, job, duration
            )
            diagnostics: list[Diagnostic] = []
            if timeline_mode == TimelineMode.BROWSER_CLOCK:
                diagnostics.append(Diagnostic('info', 'virtual_clock_scope',
                    'Virtual time controls JavaScript timers from the ready-state baseline, not CSS animations or media playback. Use Web Animations or Media adapters for those sources.'))
            sync_seek = self._validate_timeline(
                loaded,
                target,
                job,
                timeline_mode,
                duration,
                diagnostics,
            )

            profile = OUTPUT_PROFILES.get(job.render.output_profile_key)
            if profile is None:
                diagnostics.append(
                    Diagnostic("error", "profile_unknown", "The selected output profile no longer exists.")
                )
            elif profile.requires_even_dimensions and (
                output_width % 2 or output_height % 2
            ):
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "odd_dimensions",
                        f"{profile.label} requires even dimensions; current output is {output_width}×{output_height}.",
                    )
                )

            if profile and job.capture.transparent_background and not profile.supports_alpha:
                diagnostics.append(Diagnostic('error', 'alpha_unsupported',
                    'Transparent capture requires ProRes 4444; the selected codec cannot retain alpha.'))
            pixel_count = output_width * output_height
            if pixel_count > 33_177_600:
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "very_large_frame",
                        f"Each frame is {pixel_count / 1_000_000:.1f} megapixels. Reduce scale or worker count if memory pressure occurs.",
                    )
                )
            elif pixel_count > 8_294_400:
                diagnostics.append(
                    Diagnostic(
                        "info",
                        "large_frame",
                        f"Output is {pixel_count / 1_000_000:.1f} megapixels per frame; parallel exports can use substantial RAM.",
                    )
                )

            if target.mode == CaptureMode.FULL_PAGE and timeline_mode != TimelineMode.STATIC:
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "full_page_dynamic",
                        "Full-page dimensions may change during animation. Viewport capture is safer for dynamic pages.",
                    )
                )

            if target.locator is not None:
                densities = target.locator.evaluate("""(el, scale) => {
                    const nodes=[el,...el.querySelectorAll('img,canvas,video')];
                    let low=0;
                    for(const n of nodes) {
                        const tag=n.tagName.toLowerCase();
                        let w,h;
                        if(tag==='img') { if(window.__resourceBlobs?.[n.currentSrc||n.src]?.type==='image/svg+xml') continue; if((n.currentSrc||n.src||'').match(/(?:[.]svg(?:[?#]|$)|^data:image[/]svg)/i)) continue; w=n.naturalWidth;h=n.naturalHeight; }
                        else if(tag==='canvas') {w=n.width;h=n.height;}
                        else if(tag==='video') {w=n.videoWidth;h=n.videoHeight;}
                        else continue;
                        const b=n.getBoundingClientRect();
                        if(w>0 && h>0 && b.width>0 && b.height>0 && (w+1<b.width*scale || h+1<b.height*scale)) low++;
                    }
                    return low;
                }""", job.render.scale)
                if densities:
                    diagnostics.append(Diagnostic('warning','raster_density',f'{densities} image/canvas/video surface(s) have fewer source pixels than the requested output density; increasing scale cannot restore missing detail.'))
            blocked_local = any('Not allowed to load local resource' in message
                                for message in loaded.console_errors + loaded.page_errors)
            if blocked_local:
                diagnostics.append(Diagnostic('error', 'local_assets_blocked',
                    'The embedded fallback cannot load required local assets. Use File URL or Loopback HTTP in an environment allowing local navigation, or provide a self-contained HTML bundle. Export stopped rather than silently omitting assets.'))
            if loaded.console_errors or loaded.page_errors:
                messages = (loaded.page_errors + loaded.console_errors)[-5:]
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "browser_errors",
                        "The source logged browser errors: " + " | ".join(messages),
                    )
                )

            prepared = PreparedSource(
                loaded=loaded,
                target=target,
                duration=duration,
                duration_source=duration_source,
                timeline_mode=timeline_mode,
                sync_seek=sync_seek,
                source_width=source_width,
                source_height=source_height,
                output_width=output_width,
                output_height=output_height,
                diagnostics=diagnostics,
            )

            if deep_analysis and duration is not None and not any(
                item.severity == "error" for item in diagnostics
            ):
                prepared.content_analysis = self._analyze_prepared(prepared, job)
            return prepared
        except Exception:
            loaded.close()
            raise

    def probe(self, job: JobConfig, deep_analysis: bool = False, cancel_event: Optional[Event] = None) -> ProbeResult:
        self._cancel_event = cancel_event
        self._check_cancel()
        prepared = self._prepare(job, deep_analysis=deep_analysis)
        try:
            analysis = prepared.content_analysis
            return ProbeResult(
                source=job.source,
                load_strategy=prepared.loaded.strategy,
                target_description=prepared.target.description,
                target_kind=prepared.target.kind,
                target_selector=prepared.target.selector,
                target_frame_url=prepared.target.frame.url,
                timeline_mode=prepared.timeline_mode,
                duration_seconds=prepared.duration,
                duration_source=prepared.duration_source,
                source_width=prepared.source_width,
                source_height=prepared.source_height,
                output_width=prepared.output_width,
                output_height=prepared.output_height,
                geometry_mode=prepared.target.geometry_mode,
                sync_seek=prepared.sync_seek,
                recommended_processing_key=(
                    analysis.recommended_preset_key if analysis else "no_processing"
                ),
                content_classification=(analysis.classification if analysis else "not sampled"),
                diagnostics=prepared.diagnostics,
                extra=(
                    {
                        "edge_energy": analysis.edge_energy,
                        "flat_fraction": analysis.flat_fraction,
                        "saturation": analysis.saturation,
                        "texture_energy": analysis.texture_energy,
                        "confidence": analysis.confidence,
                        "analysis_explanation": analysis.explanation,
                        "usable_samples": analysis.usable_samples,
                        "ignored_near_uniform_samples": analysis.ignored_near_uniform_samples,
                    }
                    if analysis
                    else {}
                ),
            )
        finally:
            prepared.loaded.close()

    def _wait_raf(self, frame: Frame, count: int = 1) -> None:
        if getattr(self, '_clock_active', False):
            return
        self._bounded_evaluate(frame, """count => new Promise(resolve => {
            let left = Math.max(1, count);
            const tick = () => { if (--left <= 0) resolve(); else requestAnimationFrame(tick); };
            requestAnimationFrame(tick);
        })""", count)

    def _seek(
        self,
        prepared: PreparedSource,
        job: JobConfig,
        source_time: float,
        previous_source_time: Optional[float],
        realtime_origin: Optional[float],
    ) -> Optional[float]:
        frame = prepared.target.frame
        locator = prepared.target.locator
        mode = prepared.timeline_mode

        if mode == TimelineMode.STATIC:
            self._wait_raf(frame, 1)
            return realtime_origin

        if mode == TimelineMode.OM_EVENT:
            if locator is None:
                raise ExportError("OM event timing requires an element capture target.")
            locator.evaluate(
                """
                (el, t) => el.dispatchEvent(new CustomEvent('data-om-seek-to-time-frame', {
                    detail: {time: t, sync: true, playing: false}
                }))
                """,
                source_time,
            )
            self._wait_raf(frame, 1 if prepared.sync_seek else 2)
            return realtime_origin

        if mode == TimelineMode.CUSTOM_EVENT:
            if locator is None:
                raise ExportError("Custom event timing requires an element capture target.")
            locator.evaluate(
                """
                (el, payload) => el.dispatchEvent(new CustomEvent(payload.name, {
                    detail: {time: payload.time, sync: true, playing: false}
                }))
                """,
                {"name": job.timeline.custom_event_name, "time": source_time},
            )
            self._wait_raf(frame, 2)
            return realtime_origin

        if mode == TimelineMode.JAVASCRIPT_FUNCTION:
            function_path = job.timeline.javascript_function.strip()
            self._bounded_evaluate(frame,
                """
                async payload => {
                    let fn = null;
                    if (payload.path === 'window.seekTo') {
                        if (typeof window.__HTML_VIDEO_EXPORT_SEEK__ === 'function')
                            fn = window.__HTML_VIDEO_EXPORT_SEEK__.bind(window);
                        else if (typeof window.HTML_VIDEO_EXPORT?.seek === 'function')
                            fn = window.HTML_VIDEO_EXPORT.seek.bind(window.HTML_VIDEO_EXPORT);
                        else if (typeof window.seekTo === 'function') fn = window.seekTo.bind(window);
                    } else {
                        const parts = payload.path.replace(/^window[.]/, '').split('.');
                        let owner = window;
                        for (let i = 0; i < parts.length - 1; i++) owner = owner?.[parts[i]];
                        fn = owner?.[parts[parts.length - 1]];
                        if (typeof fn === 'function') fn = fn.bind(owner);
                    }
                    if (typeof fn !== 'function') {
                        throw new Error('JavaScript seek function was not found: ' + payload.path);
                    }
                    await fn(payload.time, {sync: true, playing: false});
                }
                """,
                {"path": function_path, "time": source_time},
            )
            self._wait_raf(frame, 2)
            return realtime_origin

        if mode == TimelineMode.WEB_ANIMATIONS:
            if locator is not None:
                locator.evaluate(
                    """
                    (el, milliseconds) => {
                        const animations = el.getAnimations ? el.getAnimations({subtree:true}) : [];
                        for (const animation of animations) {
                            try {
                                animation.pause();
                                animation.currentTime = milliseconds;
                            } catch (_) {}
                        }
                    }
                    """,
                    source_time * 1000,
                )
            else:
                frame.evaluate(
                    """
                    milliseconds => {
                        const animations = document.getAnimations ? document.getAnimations() : [];
                        for (const animation of animations) {
                            try {
                                animation.pause();
                                animation.currentTime = milliseconds;
                            } catch (_) {}
                        }
                    }
                    """,
                    source_time * 1000,
                )
            self._wait_raf(frame, 2)
            return realtime_origin

        if mode == TimelineMode.MEDIA:
            media = locator if locator is not None and prepared.target.kind in {'video','audio'} else (locator or frame).locator('video, audio').first
            media.evaluate("el => el.pause()")
            handle = media.element_handle()
            try:
                self._bounded_evaluate(frame, """async payload => {
                    const media=payload.el;
                    if(!media) throw new Error('No media element found.');
                    if(media.error) throw new Error('Media error: '+media.error.message);
                    if(media.readyState < 1) await new Promise((resolve,reject)=>{
                        media.addEventListener('loadedmetadata',resolve,{once:true});
                        media.addEventListener('error',()=>reject(new Error('Media metadata failed')),{once:true});
                    });
                    const t=Math.max(0,Math.min(payload.time,Number.isFinite(media.duration) ? Math.max(0,media.duration-1e-6):payload.time));
                    if(Math.abs(media.currentTime-t)>1e-6 || media.seeking) await new Promise((resolve,reject)=>{
                        const done=()=>{media.removeEventListener('error',err);resolve();};
                        const err=()=>{media.removeEventListener('seeked',done);reject(new Error('Media seek failed'));};
                        media.addEventListener('seeked',done,{once:true});
                        media.addEventListener('error',err,{once:true});
                        media.currentTime=t;
                    });
                    if(media.readyState < 2) await new Promise((resolve,reject)=>{
                        media.addEventListener('loadeddata',resolve,{once:true});
                        media.addEventListener('error',()=>reject(new Error('Media frame failed')),{once:true});
                    });
                }""", {"el": handle, "time": source_time})
            finally:
                if handle is not None:
                    handle.dispose()
            self._wait_raf(frame, 2)
            return realtime_origin

        if mode == TimelineMode.BROWSER_CLOCK:
            page = prepared.loaded.page
            if not hasattr(page, "clock"):
                raise ExportError("Browser Clock is not supported by this Playwright build.")
            target_ms = int(round(source_time * 1000))
            delta_ms = target_ms - prepared.clock_elapsed_ms
            if delta_ms < 0:
                raise ExportError('Browser Clock cannot seek backwards. Reload the source first.')
            if delta_ms:
                page.clock.run_for(delta_ms)
            prepared.clock_elapsed_ms = target_ms
            return realtime_origin

        if mode == TimelineMode.REALTIME:
            if realtime_origin is None:
                realtime_origin = time.perf_counter()
                if job.timeline.realtime_start_delay:
                    prepared.loaded.page.wait_for_timeout(
                        int(round(job.timeline.realtime_start_delay * 1000))
                    )
                    realtime_origin = time.perf_counter()
            target_clock = realtime_origin + source_time
            remaining = target_clock - time.perf_counter()
            if remaining > 0:
                prepared.loaded.page.wait_for_timeout(int(round(remaining * 1000)))
            return realtime_origin

        raise ExportError(f"Unsupported timeline mode: {mode.value}")

    def _capture_frame(self, prepared: PreparedSource, job: JobConfig) -> bytes:
        target = prepared.target
        page = prepared.loaded.page

        if target.locator is not None and target.geometry_mode == GeometryMode.LOCK_INTRINSIC:
            expected = (prepared.output_width, prepared.output_height)
            last_actual = (0, 0)
            for _attempt in range(3):
                self._reapply_geometry_guard(target)
                self._guard_frame_chain(target)
                self._wait_raf(target.frame, 1)
                screenshot = target.locator.screenshot
                if job.render.fast_capture:
                    box = target.locator.bounding_box()
                    viewport = page.viewport_size
                    covers_viewport = (
                        box is not None and viewport is not None
                        and viewport['width'] == prepared.source_width
                        and viewport['height'] == prepared.source_height
                        and all(math.isclose(box[key], value, abs_tol=1e-6, rel_tol=0)
                                for key, value in [('x', 0), ('y', 0),
                                    ('width', prepared.source_width),
                                    ('height', prepared.source_height)])
                    )
                    if covers_viewport:
                        # Guards put the capture root at 0,0 and hide siblings.
                        # Page capture avoids redundant element stability/scroll waits.
                        screenshot = page.screenshot
                data = screenshot(
                    type="png",
                    scale="device",
                    caret="hide",
                    omit_background=job.capture.transparent_background,
                    timeout=self.page_timeout_ms,
                )
                last_actual = png_dimensions(data)
                if last_actual == expected:
                    return data
            raise ExportError(
                "Browser raster size did not match the requested render size after "
                "three geometry-lock attempts. Expected "
                f"{expected[0]}×{expected[1]}, got {last_actual[0]}×{last_actual[1]}. "
                "Try Preserve layout geometry or set manual capture dimensions."
            )

        if target.locator is not None and target.geometry_mode == GeometryMode.PRESERVE_LAYOUT:
            data = target.locator.screenshot(
                type="png",
                scale="device",
                caret="hide",
                omit_background=job.capture.transparent_background,
                timeout=self.page_timeout_ms,
            )
        elif target.mode == CaptureMode.FULL_PAGE:
            data = page.screenshot(
                type="png",
                full_page=True,
                scale="device",
                caret="hide",
                omit_background=job.capture.transparent_background,
                timeout=self.page_timeout_ms,
            )
        else:
            data = page.screenshot(
                type="png",
                full_page=False,
                scale="device",
                caret="hide",
                omit_background=job.capture.transparent_background,
                timeout=self.page_timeout_ms,
            )

        actual = png_dimensions(data)
        expected = (prepared.output_width, prepared.output_height)
        if actual != expected:
            raise ExportError(
                f"Captured frame size changed. Expected {expected[0]}×{expected[1]}, "
                f"got {actual[0]}×{actual[1]}. Use Lock intrinsic geometry, a fixed "
                "viewport, or explicit dimensions."
            )
        return data

    def _sample_times(self, duration: float, timeline_mode: TimelineMode) -> list[float]:
        if timeline_mode in {TimelineMode.REALTIME, TimelineMode.BROWSER_CLOCK, TimelineMode.STATIC}:
            return [0.0]
        if duration <= 0.3:
            return [max(0.0, duration * 0.5)]
        return [duration * 0.20, duration * 0.50, duration * 0.80]

    def _analyze_prepared(self, prepared: PreparedSource, job: JobConfig) -> ContentAnalysis:
        assert prepared.duration is not None
        frames: list[bytes] = []
        previous: Optional[float] = None
        realtime_origin: Optional[float] = None
        start, end, _ = self._timeline_bounds(prepared, job)
        for sample_time in [start + t for t in self._sample_times(end-start, prepared.timeline_mode)]:
            self._check_cancel()
            realtime_origin = self._seek(
                prepared,
                job,
                sample_time,
                previous,
                realtime_origin,
            )
            frames.append(self._capture_frame(prepared, job))
            previous = sample_time
        analysis = analyze_frames(frames)
        _log(
            self.log_callback,
            f"Content analysis: {analysis.classification}; {analysis.explanation}",
        )
        return analysis

    def _timeline_bounds(self, prepared: PreparedSource, job: JobConfig) -> tuple[float, float, float]:
        if prepared.duration is None:
            raise ExportError(
                "Duration is required. Set a manual duration or expose it in the source."
            )
        source_duration = prepared.duration
        start = job.timeline.trim_start
        end = job.timeline.trim_end if job.timeline.trim_end is not None else source_duration
        if start >= source_duration:
            raise ExportError(
                f"Trim start {start:.3f}s is not before source duration {source_duration:.3f}s."
            )
        if end > source_duration + 1e-6:
            raise ExportError(
                f"Trim end {end:.3f}s exceeds source duration {source_duration:.3f}s."
            )
        if end <= start:
            raise ExportError("Trim end must be greater than trim start.")
        active = end - start
        output_duration = job.timeline.hold_start + active + job.timeline.hold_end
        if output_duration <= 0:
            raise ExportError("Output duration must be greater than zero.")
        return start, end, output_duration

    def _frame_source_time(
        self,
        frame_index: int,
        fps: int,
        start: float,
        end: float,
        hold_start: float,
        hold_end: float,
    ) -> float:
        output_time = frame_index / fps
        active_duration = end - start
        if output_time < hold_start:
            return start
        if output_time < hold_start + active_duration:
            return max(start, min(math.nextafter(end, start), start + output_time - hold_start))
        # Hold the last frame that was actually sampled, not an arbitrary later time.
        last_index = max(0, math.ceil((hold_start + active_duration) * fps - 1e-9) - 1)
        return max(start, min(math.nextafter(end, start), start + last_index / fps - hold_start))

    def _validate_profile_and_filters(self, job: JobConfig, width: int, height: int, filter_chain: str) -> None:
        profile = OUTPUT_PROFILES.get(job.render.output_profile_key)
        if profile is None:
            raise ExportError(f'Unknown output profile: {job.render.output_profile_key}')
        if profile.requires_even_dimensions and (width % 2 or height % 2):
            raise ExportError(f'{profile.label} requires even output dimensions; got {width}×{height}.')
        if job.capture.transparent_background and not profile.supports_alpha:
            raise ExportError('This output profile cannot preserve transparency.')
        if job.capture.transparent_background and filter_chain:
            raise ExportError('Transparent masters currently require No processing to preserve alpha exactly.')
        from media_pipeline import (select_ffmpeg, filter_names, color_pipeline,
                                    audio_source_path, build_audio_filter_chain,
                                    build_audio_output_args, validate_audio_stream)
        effective_chain = color_pipeline(filter_chain, profile.video_encoder == 'libx264rgb')
        required_filters = filter_names(effective_chain)
        required_encoders = {profile.video_encoder}
        audio = job.render.audio
        if audio.mode != AudioMode.NONE:
            try:
                audio_source_path(audio.path)
                build_audio_output_args(audio, profile)
                required_filters.update(filter_names(build_audio_filter_chain(audio, 1.0)))
            except ValueError as exc:
                raise ExportError(str(exc)) from exc
            required_encoders.add(profile.audio_codec)
            required_filters.add('aresample')
            if audio.fade_in_seconds > 0 or audio.fade_out_seconds > 0:
                required_filters.add('afade')
        self._ffmpeg_exe = select_ffmpeg(required_encoders, required_filters)
        if audio.mode != AudioMode.NONE:
            try:
                validate_audio_stream(self._ffmpeg_exe, audio.path)
            except ValueError as exc:
                raise ExportError(str(exc)) from exc

    def _build_ffmpeg_command(
        self,
        job: JobConfig,
        output_path: Path,
        output_duration: float,
        filter_chain: str,
    ) -> list[str]:
        ffmpeg = getattr(self, '_ffmpeg_exe', None) or get_ffmpeg_executable()
        profile = OUTPUT_PROFILES[job.render.output_profile_key]
        from media_pipeline import color_pipeline, ffmpeg_thread_plan
        threads = ffmpeg_thread_plan(job.render.cpu_threads)
        _log(getattr(self, 'log_callback', None),
             f'FFmpeg CPU threads: encoder={threads.encoder}, filters={threads.filters}, decoder={threads.decoder}.')
        filter_chain = color_pipeline(filter_chain, profile.video_encoder == 'libx264rgb')
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-filter_threads", str(threads.filters),
            "-threads:v", str(threads.decoder),
            "-f", "image2pipe",
            "-framerate", str(job.render.fps),
            "-vcodec", "png",
            "-i", "pipe:0",
        ]

        audio = job.render.audio
        has_audio = audio.mode != AudioMode.NONE and bool(audio.path.strip())
        if has_audio:
            if audio.mode == AudioMode.LOOP:
                command.extend(["-stream_loop", "-1"])
            command.extend(["-i", str(Path(audio.path).expanduser().resolve())])

        command.extend(["-map", "0:v:0"])
        if has_audio:
            command.extend(["-map", "1:a:0"])
        if filter_chain:
            command.extend(["-vf", filter_chain])
        command.extend(profile.video_args)
        command.extend(['-threads:v', str(threads.encoder)])

        if has_audio:
            from media_pipeline import build_audio_filter_chain, build_audio_output_args
            command.extend(["-af", build_audio_filter_chain(audio, output_duration)])
            command.extend(build_audio_output_args(audio, profile))
        else:
            command.append("-an")

        command.extend(["-t", f"{output_duration:.9f}"])
        if profile.extension in {"mp4", "mov"}:
            command.extend(["-movflags", "+faststart"])
        command.extend(["-metadata", f"encoder={APP_NAME} {APP_VERSION}"])
        command.append(str(output_path))
        return command

    def render(
        self,
        job: JobConfig,
        output_path: Optional[Path] = None,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_event: Optional[Event] = None,
        run_event: Optional[Event] = None,
    ) -> ExportResult:
        wall_started = time.perf_counter()
        self.last_render_stats = {}
        import copy
        job = copy.deepcopy(job)
        if output_path is not None:
            job.render.save_next_to_source = False
            job.render.output_directory = str(Path(output_path).expanduser().resolve().parent)
        job.validate()
        self._cancel_event = cancel_event
        self._check_cancel()
        prepared = self._prepare(job, deep_analysis=False)
        process: Optional[subprocess.Popen[bytes]] = None
        collector: Optional[_StderrCollector] = None
        temp_output: Optional[Path] = None
        watchdog_stop = threading.Event()
        watchdog = None
        capture_pool = None
        metrics = dict(seek_seconds=0.0, screenshot_seconds=0.0,
                       frame_wait_seconds=0.0, pipe_write_seconds=0.0,
                       finalize_seconds=0.0)

        try:
            errors = [item.message for item in prepared.diagnostics if item.severity == "error"]
            if errors:
                raise ExportError("Source validation failed:\n- " + "\n- ".join(errors))

            start, end, output_duration = self._timeline_bounds(prepared, job)
            total_frames = max(1, int(math.ceil(output_duration * job.render.fps - 1e-9)))

            analysis: Optional[ContentAnalysis] = None
            if job.render.processing.preset_key == "auto_content_aware":
                if prepared.timeline_mode in {TimelineMode.REALTIME, TimelineMode.BROWSER_CLOCK, TimelineMode.STATIC} or job.capture.transparent_background:
                    _log(
                        self.log_callback,
                        "Automatic processing is disabled for progressive/static/transparent capture. "
                        "No frames are consumed before frame zero; no processing is used.",
                    )
                else:
                    analysis = self._analyze_prepared(prepared, job)
            resolved_processing = resolve_processing_config(job.render.processing, analysis)
            filter_chain = build_filter_chain(resolved_processing)
            processing_key = resolved_processing.preset_key

            from media_pipeline import capture_plan, OrderedCapturePool
            declared_safe = False
            if job.render.capture_workers == 0 and prepared.timeline_mode not in {
                    TimelineMode.STATIC, TimelineMode.REALTIME, TimelineMode.BROWSER_CLOCK}:
                declared_safe = bool(self._evaluate_target(prepared.target, """el =>
                    el?.getAttribute('data-video-export-parallel-safe') === 'true' ||
                    window.HTML_VIDEO_EXPORT?.parallelSafe === true
                """))
            plan = capture_plan(job, prepared.timeline_mode, prepared.output_width,
                                prepared.output_height, total_frames, declared_safe)
            metrics['capture_workers'] = plan.workers
            metrics['fast_capture'] = job.render.fast_capture
            _log(self.log_callback, f'Browser capture: {plan.workers} worker(s); {plan.reason}. '
                 f'PNG buffer budget {job.render.frame_buffer_mb} MiB (browser/encoder RAM additional).')

            final_output = output_path.expanduser().resolve() if output_path else choose_output_path(
                job, processing_key=processing_key
            )
            profile_ext = '.' + OUTPUT_PROFILES[job.render.output_profile_key].extension
            if final_output.suffix.lower() != profile_ext:
                raise ExportError(f'Output extension must be {profile_ext} for the selected profile.')
            if job.source.kind == SourceKind.FILE and final_output == Path(job.source.value).resolve():
                raise ExportError('Output cannot replace the source HTML.')
            if (job.render.audio.mode != AudioMode.NONE and job.render.audio.path.strip()
                    and final_output == Path(job.render.audio.path).expanduser().resolve()):
                raise ExportError('Output cannot replace the external audio input.')
            final_output.parent.mkdir(parents=True, exist_ok=True)
            temp_output = final_output.with_name(
                f".hves-partial-{uuid.uuid4().hex}{final_output.suffix}"
            )

            self._validate_profile_and_filters(
                job, prepared.output_width, prepared.output_height, filter_chain
            )
            command = self._build_ffmpeg_command(
                job=job,
                output_path=temp_output,
                output_duration=total_frames / job.render.fps,
                filter_chain=filter_chain,
            )
            _log(
                self.log_callback,
                (
                    f"Rendering {prepared.source_width}×{prepared.source_height} → "
                    f"{prepared.output_width}×{prepared.output_height}, "
                    f"{job.render.fps} fps, {total_frames} frames, "
                    f"{OUTPUT_PROFILES[job.render.output_profile_key].label}, "
                    f"{PROCESSING_PRESETS.get(processing_key, PROCESSING_PRESETS['custom']).label}."
                ),
            )

            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if process.stdin is None or process.stderr is None:
                raise ExportError("Could not create FFmpeg pipes.")
            collector = _StderrCollector(process.stderr)
            collector.start()
            # Process operations, unlike Playwright objects, can be cancelled safely
            # from this watchdog even while stdin.write()/wait() blocks.
            def monitor_encoder():
                while not watchdog_stop.wait(0.1):
                    if cancel_event is not None and cancel_event.is_set():
                        try:
                            process.terminate()
                            process.wait(timeout=2)
                        except Exception:
                            try: process.kill()
                            except OSError: pass
                        return
            watchdog = threading.Thread(target=monitor_encoder, daemon=True)
            watchdog.start()

            if plan.workers > 1:
                signature = _capture_signature(prepared)
                browser_path, timeout = self.browser_executable, self.page_timeout_ms
                capture_pool = OrderedCapturePool(
                    lambda _lane, stop: _capture_session(job, signature, browser_path,
                        timeout, start, end, stop, cancel_event),
                    plan.workers, total_frames, plan.max_frame_bytes,
                    self._check_cancel, run_event)
                capture_pool.__enter__()

            render_started = time.perf_counter()
            previous_source_time: Optional[float] = None
            realtime_origin: Optional[float] = None
            cached_static_frame: Optional[bytes] = None
            cached_source_time: Optional[float] = None

            for frame_index in range(total_frames):
                if cancel_event is not None and cancel_event.is_set():
                    raise ExportCancelled("Export cancelled.")
                if run_event is not None:
                    pause_started = time.perf_counter()
                    while not run_event.wait(timeout=0.1):
                        if cancel_event is not None and cancel_event.is_set():
                            raise ExportCancelled("Export cancelled while paused.")
                    if realtime_origin is not None:
                        realtime_origin += time.perf_counter() - pause_started

                source_time = self._frame_source_time(
                    frame_index=frame_index,
                    fps=job.render.fps,
                    start=start,
                    end=end,
                    hold_start=job.timeline.hold_start,
                    hold_end=job.timeline.hold_end,
                )

                can_reuse = (
                    cached_static_frame is not None
                    and (prepared.timeline_mode == TimelineMode.STATIC or
                        (cached_source_time is not None and math.isclose(source_time, cached_source_time, abs_tol=1e-12)))
                )

                if capture_pool is not None:
                    capture_started = time.perf_counter()
                    png = capture_pool.frame(frame_index)
                    metrics['frame_wait_seconds'] += time.perf_counter() - capture_started
                elif can_reuse:
                    png = cached_static_frame
                else:
                    seek_started = time.perf_counter()
                    realtime_origin = self._seek(
                        prepared,
                        job,
                        source_time,
                        previous_source_time,
                        realtime_origin,
                    )
                    screenshot_started = time.perf_counter()
                    metrics['seek_seconds'] += screenshot_started - seek_started
                    png = self._capture_frame(prepared, job)
                    metrics['screenshot_seconds'] += time.perf_counter() - screenshot_started
                    cached_static_frame = png
                    cached_source_time = source_time

                try:
                    write_started = time.perf_counter()
                    process.stdin.write(png)
                    metrics['pipe_write_seconds'] += time.perf_counter() - write_started
                except (BrokenPipeError, OSError) as exc:
                    self._check_cancel()
                    detail = collector.finish() if collector else ""
                    raise ExportError(
                        "FFmpeg stopped while receiving frames."
                        + (f"\n\n{detail}" if detail else "")
                    ) from exc

                previous_source_time = source_time
                done = frame_index + 1
                elapsed = time.perf_counter() - render_started
                rate = done / elapsed if elapsed > 0 else 0.0
                eta = (total_frames - done) / rate if rate > 0 else None
                if progress_callback:
                    progress_callback(
                        {
                            "phase": "Rendering",
                            "fraction": done / total_frames,
                            "frame": done,
                            "total_frames": total_frames,
                            "source_time": source_time,
                            "output_time": frame_index / job.render.fps,
                            "elapsed": elapsed,
                            "eta": eta,
                            "render_fps": rate,
                        }
                    )

            if capture_pool is not None:
                metrics['seek_seconds'] = capture_pool.seek_seconds
                metrics['screenshot_seconds'] = capture_pool.capture_seconds
                capture_pool.close()
                capture_pool = None

            # A cancel arriving after the last frame but before commit must still win.
            if cancel_event is not None and cancel_event.is_set():
                raise ExportCancelled("Export cancelled before finalization.")

            finalize_started = time.perf_counter()
            process.stdin.close()
            return_code = process.wait(timeout=max(120, total_frames / 2))
            metrics['finalize_seconds'] = time.perf_counter() - finalize_started
            self._check_cancel()
            detail = collector.finish() if collector else ""
            if return_code != 0:
                raise ExportError(
                    f"FFmpeg exited with code {return_code}."
                    + (f"\n\n{detail}" if detail else "")
                )
            if not temp_output.exists() or temp_output.stat().st_size == 0:
                raise ExportError("FFmpeg reported success but produced no output file.")

            if cancel_event is not None and cancel_event.is_set():
                raise ExportCancelled("Export cancelled before output commit.")

            from media_pipeline import commit_output
            final_output = commit_output(temp_output, final_output, overwrite=job.render.overwrite)
            temp_output = None

            metrics['total_seconds'] = time.perf_counter() - wall_started
            metrics['frames'] = total_frames
            metrics['end_to_end_fps'] = total_frames / max(metrics['total_seconds'], 1e-9)
            self.last_render_stats = dict(metrics)
            _log(self.log_callback, 'Render timings: ' + json.dumps(metrics, sort_keys=True))
            return ExportResult(
                output_path=final_output,
                source_width=prepared.source_width,
                source_height=prepared.source_height,
                output_width=prepared.output_width,
                output_height=prepared.output_height,
                source_duration_seconds=prepared.duration or 0.0,
                output_duration_seconds=total_frames / job.render.fps,
                frame_count=total_frames,
                fps=job.render.fps,
                scale=job.render.scale,
                output_profile_key=job.render.output_profile_key,
                processing_key=processing_key,
                load_strategy=prepared.loaded.strategy,
                timeline_mode=prepared.timeline_mode,
                duration_source=prepared.duration_source,
                content_classification=(analysis.classification if analysis else "not analyzed"),
            )

        except ExportCancelled:
            if process is not None and process.poll() is None:
                try:
                    if process.stdin:
                        process.stdin.close()
                except Exception:
                    pass
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            raise
        finally:
            if capture_pool is not None:
                # A render failure/cancellation already takes precedence. Stop
                # producers before disposing the process/parent source below.
                try:
                    capture_pool.close()
                except Exception:
                    pass
            watchdog_stop.set()
            if watchdog is not None: watchdog.join(timeout=3)
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if collector is not None: collector.finish()
                for stream in (process.stdin, process.stderr):
                    if stream is not None:
                        try: stream.close()
                        except OSError: pass
            if temp_output is not None:
                temp_output.unlink(missing_ok=True)
            prepared.loaded.close()
            self._cancel_event = None

    def _write_preview_png(self, png: bytes, output_path: Path, *,
                           filter_chain: str = '', overwrite: bool = False) -> Path:
        """Encode into a sibling temporary file, then commit only after cancellation check."""
        from media_pipeline import select_ffmpeg, filter_names, commit_output
        output_path = Path(output_path).expanduser().resolve()
        if output_path.suffix.lower() != '.png':
            raise ExportError('Preview output must use the .png extension.')
        self._check_cancel()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.preview-', suffix='.png', dir=output_path.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            if filter_chain:
                executable = select_ffmpeg({'png'}, filter_names(filter_chain))
                proc = subprocess.run([
                    executable, '-hide_banner', '-loglevel', 'error', '-y',
                    '-filter_threads', '1', '-f', 'image2pipe', '-vcodec', 'png',
                    '-i', 'pipe:0', '-vf', filter_chain, '-frames:v', '1', str(temporary)
                ], input=png, capture_output=True, timeout=60)
                if proc.returncode:
                    raise ExportError('Could not apply preview processing:\n' +
                                      proc.stderr.decode('utf-8', errors='replace'))
            else:
                temporary.write_bytes(png)
            png_dimensions(temporary.read_bytes())
            self._check_cancel()
            return commit_output(temporary, output_path, overwrite=overwrite)
        finally:
            temporary.unlink(missing_ok=True)

    def capture_test_frame(
        self,
        job: JobConfig,
        output_path: Path,
        at_seconds: Optional[float] = None,
        apply_processing: bool = True,
        cancel_event: Optional[Event] = None,
    ) -> tuple[Path, ProbeResult]:
        if Path(output_path).suffix.lower() != '.png':
            raise ExportError('Preview output must use the .png extension.')
        self._cancel_event = cancel_event
        self._check_cancel()
        prepared = self._prepare(job, deep_analysis=False)
        temp_png: Optional[Path] = None
        try:
            errors = [item.message for item in prepared.diagnostics if item.severity == "error"]
            if errors:
                raise ExportError("Source validation failed:\n- " + "\n- ".join(errors))
            duration = prepared.duration
            if duration is None:
                raise ExportError("Duration is required to capture a timed test frame.")
            source_time = at_seconds if at_seconds is not None else duration * 0.5
            source_time = min(max(0.0, source_time), max(0.0, duration - 1e-6))
            self._seek(prepared, job, source_time, None, None)
            png = self._capture_frame(prepared, job)

            analysis: Optional[ContentAnalysis] = None
            if job.render.processing.preset_key == "auto_content_aware" and prepared.timeline_mode not in {TimelineMode.REALTIME, TimelineMode.BROWSER_CLOCK, TimelineMode.STATIC} and not job.capture.transparent_background:
                analysis = self._analyze_prepared(prepared, job)
            resolved = resolve_processing_config(job.render.processing, analysis)
            filter_chain = build_filter_chain(resolved) if apply_processing else ""

            if filter_chain and job.capture.transparent_background:
                raise ExportError('Processed alpha previews are unsupported. Select No processing.')
            self._check_cancel()
            from media_pipeline import color_pipeline
            filter_chain = color_pipeline(filter_chain, True)
            output_path = self._write_preview_png(
                png, output_path, filter_chain=filter_chain, overwrite=job.render.overwrite)

            probe = ProbeResult(
                source=job.source,
                load_strategy=prepared.loaded.strategy,
                target_description=prepared.target.description,
                target_kind=prepared.target.kind,
                target_selector=prepared.target.selector,
                target_frame_url=prepared.target.frame.url,
                timeline_mode=prepared.timeline_mode,
                duration_seconds=prepared.duration,
                duration_source=prepared.duration_source,
                source_width=prepared.source_width,
                source_height=prepared.source_height,
                output_width=prepared.output_width,
                output_height=prepared.output_height,
                geometry_mode=prepared.target.geometry_mode,
                sync_seek=prepared.sync_seek,
                recommended_processing_key=(
                    analysis.recommended_preset_key if analysis else "no_processing"
                ),
                content_classification=(analysis.classification if analysis else "not sampled"),
                diagnostics=prepared.diagnostics,
                extra={},
            )
            self._check_cancel()
            return output_path, probe
        finally:
            if temp_png is not None:
                temp_png.unlink(missing_ok=True)
            prepared.loaded.close()


    def capture_processing_comparison(
        self,
        job: JobConfig,
        output_path: Path,
        at_seconds: Optional[float] = None,
        cancel_event: Optional[Event] = None,
    ) -> tuple[Path, ProbeResult]:
        """Create a labelled source-vs-processing preview without changing export settings."""
        from PIL import Image, ImageDraw

        if Path(output_path).suffix.lower() != '.png':
            raise ExportError('Preview output must use the .png extension.')
        self._cancel_event = cancel_event
        self._check_cancel()
        prepared = self._prepare(job, deep_analysis=False)
        temporary_files: list[Path] = []
        try:
            errors = [item.message for item in prepared.diagnostics if item.severity == "error"]
            if errors:
                raise ExportError("Source validation failed:\n- " + "\n- ".join(errors))
            if prepared.duration is None:
                raise ExportError("Duration is required to capture a comparison frame.")
            source_time = at_seconds if at_seconds is not None else prepared.duration * 0.5
            source_time = min(max(0.0, source_time), max(0.0, prepared.duration - 1e-6))
            self._seek(prepared, job, source_time, None, None)
            raw_png = self._capture_frame(prepared, job)

            analysis = None
            if job.render.processing.preset_key == "auto_content_aware" and prepared.timeline_mode not in {TimelineMode.REALTIME, TimelineMode.BROWSER_CLOCK, TimelineMode.STATIC} and not job.capture.transparent_background:
                analysis = self._analyze_prepared(prepared, job)
            resolved = resolve_processing_config(job.render.processing, analysis)
            filter_chain = build_filter_chain(resolved)

            if filter_chain and job.capture.transparent_background:
                raise ExportError('Processed alpha previews are unsupported. Select No processing.')
            self._check_cancel()
            from media_pipeline import color_pipeline, select_ffmpeg, filter_names
            filter_chain = color_pipeline(filter_chain, True)
            processed_png = raw_png
            if filter_chain:
                fd_in, name_in = tempfile.mkstemp(suffix=".png")
                os.close(fd_in)
                fd_out, name_out = tempfile.mkstemp(suffix=".png")
                os.close(fd_out)
                input_path = Path(name_in)
                processed_path = Path(name_out)
                temporary_files.extend([input_path, processed_path])
                input_path.write_bytes(raw_png)
                proc = subprocess.run(
                    [
                        select_ffmpeg({'png'}, filter_names(filter_chain)),
                        "-hide_banner", "-loglevel", "error", "-y", "-filter_threads", "1",
                        "-i", str(input_path),
                        "-vf", filter_chain,
                        "-frames:v", "1",
                        str(processed_path),
                    ],
                    capture_output=True,
                    timeout=60,
                )
                if proc.returncode != 0:
                    raise ExportError(
                        "Could not render processing comparison:\n"
                        + proc.stderr.decode("utf-8", errors="replace")
                    )
                processed_png = processed_path.read_bytes()

            with Image.open(io.BytesIO(raw_png)) as raw_image_source:
                raw_image = raw_image_source.convert("RGB")
            with Image.open(io.BytesIO(processed_png)) as processed_image_source:
                processed_image = processed_image_source.convert("RGB")

            max_panel_width = 900
            max_panel_height = 850
            scale = min(
                1.0,
                max_panel_width / raw_image.width,
                max_panel_height / raw_image.height,
            )
            panel_size = (
                max(1, int(round(raw_image.width * scale))),
                max(1, int(round(raw_image.height * scale))),
            )
            raw_image = raw_image.resize(panel_size, Image.Resampling.LANCZOS)
            processed_image = processed_image.resize(panel_size, Image.Resampling.LANCZOS)

            gap = 24
            label_height = 52
            canvas = Image.new(
                "RGB",
                (panel_size[0] * 2 + gap, panel_size[1] + label_height),
                "#202020",
            )
            canvas.paste(raw_image, (0, label_height))
            canvas.paste(processed_image, (panel_size[0] + gap, label_height))
            draw = ImageDraw.Draw(canvas)
            draw.text((14, 16), "Captured source · no processing", fill="white")
            processing_label = PROCESSING_PRESETS.get(
                resolved.preset_key, PROCESSING_PRESETS["custom"]
            ).label
            draw.text(
                (panel_size[0] + gap + 14, 16),
                processing_label,
                fill="white",
            )

            buffer = io.BytesIO()
            canvas.save(buffer, 'PNG')
            output_path = self._write_preview_png(
                buffer.getvalue(), output_path, overwrite=job.render.overwrite)

            probe = ProbeResult(
                source=job.source,
                load_strategy=prepared.loaded.strategy,
                target_description=prepared.target.description,
                target_kind=prepared.target.kind,
                target_selector=prepared.target.selector,
                target_frame_url=prepared.target.frame.url,
                timeline_mode=prepared.timeline_mode,
                duration_seconds=prepared.duration,
                duration_source=prepared.duration_source,
                source_width=prepared.source_width,
                source_height=prepared.source_height,
                output_width=prepared.output_width,
                output_height=prepared.output_height,
                geometry_mode=prepared.target.geometry_mode,
                sync_seek=prepared.sync_seek,
                recommended_processing_key=(
                    analysis.recommended_preset_key if analysis else resolved.preset_key
                ),
                content_classification=(analysis.classification if analysis else "not sampled"),
                diagnostics=prepared.diagnostics,
                extra={
                    "comparison_processing": resolved.preset_key,
                    "analysis_explanation": analysis.explanation if analysis else "",
                },
            )
            self._check_cancel()
            return output_path, probe
        finally:
            for path in temporary_files:
                path.unlink(missing_ok=True)
            prepared.loaded.close()


_CAPABILITY_CACHE: Optional[dict[str, set[str]]] = None
_CAPABILITY_LOCK = threading.Lock()


def check_ffmpeg_capabilities(force: bool = False) -> dict[str, set[str]]:
    from media_pipeline import capabilities
    return capabilities(get_ffmpeg_executable(), force=force)


def browser_executable_candidates(
    playwright: Playwright,
    explicit: Optional[str] = None,
) -> list[Optional[str]]:
    """Return launch candidates in preference order, without duplicates.

    ``None`` means Playwright's managed Chromium.  System browser fallbacks make
    the desktop app more portable when a managed browser was cleaned up or the
    user deliberately points the app at Chrome/Edge/Chromium.
    """
    candidates: list[Optional[str]] = []

    def add(value: Optional[str], *, require_exists: bool = True) -> None:
        if value:
            expanded = os.path.expandvars(os.path.expanduser(str(value)))
            resolved = shutil.which(expanded) or expanded
            if require_exists and not Path(resolved).exists():
                return
            value_out: Optional[str] = str(Path(resolved))
        else:
            value_out = None
        if value_out not in candidates:
            candidates.append(value_out)

    add(explicit)
    add(os.environ.get('HTML_VIDEO_BROWSER'))

    # Let Playwright use its managed build first when no explicit override was
    # supplied.  It knows the exact browser revision expected by the package.
    if not explicit and not os.environ.get('HTML_VIDEO_BROWSER'):
        add(None, require_exists=False)

    try:
        add(playwright.chromium.executable_path)
    except Exception:
        pass

    for name in (
        'chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable',
        'chrome', 'msedge', 'microsoft-edge', 'microsoft-edge-stable',
    ):
        add(shutil.which(name))

    common = [
        # Windows
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        # macOS
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    ]
    for candidate in common:
        if candidate and candidate not in {'.', ''}:
            add(candidate)

    # If an explicit path was invalid, retain a final managed-browser attempt.
    if None not in candidates:
        add(None, require_exists=False)

    return candidates


def launch_chromium_with_fallback(
    playwright: Playwright,
    explicit: Optional[str] = None,
    *,
    headless: bool = True,
    args: Optional[list[str]] = None,
) -> tuple[Browser, str]:
    errors: list[str] = []
    for candidate in browser_executable_candidates(playwright, explicit):
        kwargs: dict[str, Any] = {
            'headless': headless,
            'args': list(args or []),
        }
        label = 'Playwright managed Chromium'
        if candidate:
            kwargs['executable_path'] = candidate
            label = candidate
        try:
            browser = playwright.chromium.launch(**kwargs)
            return browser, label
        except Exception as exc:
            errors.append(f'{label}: {exc}')

    detail = '\n\n'.join(errors[-5:])
    raise ExportError(
        'No compatible Chromium-based browser could be launched. Install the '
        'managed browser with:\npython -m playwright install chromium\n\n'
        'Alternatively set HTML_VIDEO_BROWSER to Chrome, Edge, or Chromium.'
        + (f'\n\nLaunch attempts:\n{detail}' if detail else '')
    )

def check_environment(browser_executable: Optional[str] = None) -> dict[str, Any]:
    """Validate FFmpeg and launch a real Chromium instance.

    Returning structured data keeps the GUI responsive and gives useful setup
    guidance rather than merely checking whether a path happens to exist.
    """
    result: dict[str, Any] = {
        'ffmpeg_ok': False,
        'ffmpeg_path': None,
        'chromium_ok': False,
        'chromium_path': None,
        'messages': [],
    }

    try:
        ffmpeg = get_ffmpeg_executable()
        proc = subprocess.run(
            [ffmpeg, '-hide_banner', '-version'],
            capture_output=True,
            text=True,
            timeout=15,
        )
        result['ffmpeg_ok'] = proc.returncode == 0
        result['ffmpeg_path'] = ffmpeg
        from media_pipeline import select_ffmpeg
        result['profiles'] = {}
        for key, profile in OUTPUT_PROFILES.items():
            try:
                select_ffmpeg({profile.video_encoder}, set())
                result['profiles'][key] = True
            except Exception:
                result['profiles'][key] = False
        if proc.returncode != 0:
            result['messages'].append('FFmpeg did not start correctly.')
    except Exception as exc:
        result['messages'].append(f'FFmpeg check failed: {exc}')

    try:
        pw = sync_playwright().start()
        try:
            browser, label = launch_chromium_with_fallback(
                pw,
                browser_executable,
                headless=True,
                args=['--disable-background-timer-throttling'],
            )
            try:
                page = browser.new_page(viewport={'width': 64, 'height': 64})
                page.set_content('<!doctype html><title>ok</title>', wait_until='domcontentloaded')
                result['chromium_ok'] = page.title() == 'ok'
                result['chromium_path'] = label
            finally:
                browser.close()
        finally:
            pw.stop()
    except Exception as exc:
        result['messages'].append(f'Chromium check failed: {exc}')

    return result
