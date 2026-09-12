from __future__ import annotations

import concurrent.futures
import copy
import os
import platform
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from tkinter import (
    Variable,
    TclError,
    BooleanVar,
    DoubleVar,
    IntVar,
    Menu,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    simpledialog,
)
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable, Optional

from models import (
    AudioMode,
    MAX_CAPTURE_WORKERS,
    CaptureMode,
    GeometryMode,
    JobConfig,
    JobStatus,
    LoadStrategy,
    ProcessingConfig,
    QueueJob,
    SourceKind,
    TimelineMode,
)
from presets import (
    OUTPUT_PROFILES,
    OUTPUT_PROFILE_LABEL_TO_KEY,
    PROCESSING_PRESETS,
    PROCESSING_LABEL_TO_KEY,
    RECIPES,
    RECIPE_LABEL_TO_KEY,
    apply_recipe,
)
from project_io import ProjectDocument, ProjectError, load_project, save_project
from renderer import (
    ExportCancelled,
    ExportError,
    HtmlVideoRenderer,
    check_environment,
)
from settings_store import AppSettings, add_recent, load_settings, save_settings
from updater import (
    UpdateError,
    detect_git_origin,
    fetch_latest_release,
    is_newer_version,
    launch_update_helper,
    normalize_repo,
    prepare_release_update,
)
from version import APP_NAME, APP_VERSION


CAPTURE_LABELS = {
    "Automatic": CaptureMode.AUTO,
    "Export marker": CaptureMode.MARKER,
    "CSS selector": CaptureMode.SELECTOR,
    "Viewport": CaptureMode.VIEWPORT,
    "Full scrollable page": CaptureMode.FULL_PAGE,
}
TIMELINE_LABELS = {
    "Automatic": TimelineMode.AUTO,
    "OM export event": TimelineMode.OM_EVENT,
    "Custom DOM event": TimelineMode.CUSTOM_EVENT,
    "JavaScript seek function": TimelineMode.JAVASCRIPT_FUNCTION,
    "CSS / Web Animations": TimelineMode.WEB_ANIMATIONS,
    "HTML media currentTime": TimelineMode.MEDIA,
    "Chromium Browser Clock": TimelineMode.BROWSER_CLOCK,
    "Realtime (best effort)": TimelineMode.REALTIME,
    "Static hold": TimelineMode.STATIC,
}
GEOMETRY_LABELS = {
    "Automatic": GeometryMode.AUTO,
    "Lock intrinsic dimensions": GeometryMode.LOCK_INTRINSIC,
    "Preserve page layout": GeometryMode.PRESERVE_LAYOUT,
}
LOAD_LABELS = {
    "Automatic": LoadStrategy.AUTO,
    "Loopback HTTP": LoadStrategy.LOOPBACK_HTTP,
    "file:// URL": LoadStrategy.FILE_URL,
    "Embedded document": LoadStrategy.EMBEDDED,
}
AUDIO_LABELS = {
    "No audio": AudioMode.NONE,
    "Use once / trim": AudioMode.TRIM,
    "Loop to video length": AudioMode.LOOP,
}


def reverse_lookup(mapping: dict[str, Any], value: Any, default: str) -> str:
    return next((label for label, candidate in mapping.items() if candidate == value), default)


def format_seconds(value: Optional[float]) -> str:
    if value is None:
        return "—"
    seconds = max(0, int(round(value)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def open_path(path: Path) -> None:
    system = platform.system()
    if system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def release_tk_variables(owner: Any) -> None:
    """Release Tcl-backed variables on the UI thread before an owner is destroyed.

    A worker can trigger cyclic GC after a closed dialog loses its last reference.
    Leaving Tcl handles on those variables can otherwise call Tcl from that worker.
    """
    for value in list(vars(owner).values()):
        if isinstance(value, Variable) and value._tk is not None:
            try:
                value.__del__()
            except TclError:
                pass
            finally:
                value._tk = None
                value._root = None


def widget_tree(owner: Any) -> list[Any]:
    # Capture Python widget references before Tk destroys its children mapping.
    result = [owner]
    for child in list(owner.children.values()):
        result.extend(widget_tree(child))
    return result


class JobEditor(Toplevel):
    def __init__(self, parent: "StudioApp", job: QueueJob):
        super().__init__(parent)
        self.parent = parent
        self.job = job
        self.result: Optional[JobConfig] = None
        self.title(f"Job Settings — {job.config.source.display_name}")
        self.geometry("820x740")
        self.minsize(740, 680)
        self.transient(parent)
        self.grab_set()

        config = copy.deepcopy(job.config)

        self.recipe_var = StringVar(
            value=RECIPES.get(config.recipe_key, RECIPES["motion_graphics_master"]).label
        )
        self.load_var = StringVar(
            value=reverse_lookup(LOAD_LABELS, config.source.load_strategy, "Automatic")
        )
        self.capture_var = StringVar(
            value=reverse_lookup(CAPTURE_LABELS, config.capture.mode, "Automatic")
        )
        self.selector_var = StringVar(value=config.capture.selector)
        self.selector_index_var = IntVar(value=config.capture.selector_index)
        self.viewport_w_var = IntVar(value=config.capture.viewport_width)
        self.viewport_h_var = IntVar(value=config.capture.viewport_height)
        self.manual_w_var = StringVar(
            value="" if config.capture.manual_width is None else str(config.capture.manual_width)
        )
        self.manual_h_var = StringVar(
            value="" if config.capture.manual_height is None else str(config.capture.manual_height)
        )
        self.geometry_var = StringVar(
            value=reverse_lookup(GEOMETRY_LABELS, config.capture.geometry_mode, "Automatic")
        )
        self.transparent_var = BooleanVar(value=config.capture.transparent_background)
        self.strip_shadow_var = BooleanVar(value=config.capture.strip_outer_shadow)

        self.timeline_var = StringVar(
            value=reverse_lookup(TIMELINE_LABELS, config.timeline.mode, "Automatic")
        )
        self.duration_var = StringVar(
            value="" if config.timeline.manual_duration is None else str(config.timeline.manual_duration)
        )
        self.trim_start_var = DoubleVar(value=config.timeline.trim_start)
        self.trim_end_var = StringVar(
            value="" if config.timeline.trim_end is None else str(config.timeline.trim_end)
        )
        self.hold_start_var = DoubleVar(value=config.timeline.hold_start)
        self.hold_end_var = DoubleVar(value=config.timeline.hold_end)
        self.event_var = StringVar(value=config.timeline.custom_event_name)
        self.function_var = StringVar(value=config.timeline.javascript_function)
        self.realtime_delay_var = DoubleVar(value=config.timeline.realtime_start_delay)

        self.scale_var = DoubleVar(value=config.render.scale)
        self.fps_var = IntVar(value=config.render.fps)
        self.cpu_threads_var = StringVar(value=str(config.render.cpu_threads))
        self.capture_workers_var = StringVar(value=str(config.render.capture_workers))
        self.frame_buffer_var = StringVar(value=str(config.render.frame_buffer_mb))
        self.fast_capture_var = BooleanVar(value=config.render.fast_capture)
        self.profile_var = StringVar(value=OUTPUT_PROFILES[config.render.output_profile_key].label)
        self.next_to_source_var = BooleanVar(value=config.render.save_next_to_source)
        self.output_dir_var = StringVar(value=config.render.output_directory)
        self.overwrite_var = BooleanVar(value=config.render.overwrite)
        self.filename_var = StringVar(value=config.render.filename_template)

        processing_label = PROCESSING_PRESETS.get(
            config.render.processing.preset_key, PROCESSING_PRESETS["custom"]
        ).label
        self.processing_var = StringVar(value=processing_label)
        self.sharpen_method_var = StringVar(value=config.render.processing.sharpen_method)
        self.sharpen_strength_var = DoubleVar(value=config.render.processing.sharpen_strength)
        self.contrast_var = DoubleVar(value=config.render.processing.contrast)
        self.saturation_var = DoubleVar(value=config.render.processing.saturation)
        self.brightness_var = DoubleVar(value=config.render.processing.brightness)
        self.deband_var = BooleanVar(value=config.render.processing.deband)

        self.audio_mode_var = StringVar(
            value=reverse_lookup(AUDIO_LABELS, config.render.audio.mode, "No audio")
        )
        self.audio_path_var = StringVar(value=config.render.audio.path)
        self.audio_volume_var = DoubleVar(value=config.render.audio.volume)
        self.audio_offset_var = DoubleVar(value=config.render.audio.offset_seconds)
        self.audio_fade_in_var = DoubleVar(value=config.render.audio.fade_in_seconds)
        self.audio_fade_out_var = DoubleVar(value=config.render.audio.fade_out_seconds)
        self.audio_bitrate_var = IntVar(value=config.render.audio.bitrate_kbps)
        self.audio_sample_rate_var = IntVar(value=config.render.audio.sample_rate_hz)
        self.audio_channels_var = IntVar(value=config.render.audio.channels)
        self.audio_normalize_var = BooleanVar(value=config.render.audio.normalize_loudness)
        self.audio_encoding_var = StringVar(value="")
        self.profile_description_var = StringVar(value="")
        self.processing_description_var = StringVar(value="")
        self.custom_processing_widgets: list[Any] = []

        self._build_ui()
        self._update_profile_description()
        self._on_processing_change()
        self._toggle_output()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        recipe_bar = ttk.Frame(root)
        recipe_bar.pack(fill="x", pady=(0, 10))
        ttk.Label(recipe_bar, text="Start from recipe:").pack(side="left")
        ttk.Combobox(
            recipe_bar,
            state="readonly",
            values=tuple(recipe.label for recipe in RECIPES.values()),
            textvariable=self.recipe_var,
            width=30,
        ).pack(side="left", padx=8)
        ttk.Button(recipe_bar, text="Apply Recipe", command=self._apply_recipe).pack(side="left")
        ttk.Label(
            recipe_bar,
            text="Applying a recipe resets capture, timing, codec, and processing for this job.",
        ).pack(side="left", padx=12)

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)

        source_tab = ttk.Frame(notebook, padding=14)
        timing_tab = ttk.Frame(notebook, padding=14)
        output_tab = ttk.Frame(notebook, padding=14)
        process_tab = ttk.Frame(notebook, padding=14)
        audio_tab = ttk.Frame(notebook, padding=14)
        performance_tab = ttk.Frame(notebook, padding=14)
        notebook.add(source_tab, text="Source & Capture")
        notebook.add(timing_tab, text="Timeline")
        notebook.add(output_tab, text="Output")
        notebook.add(process_tab, text="Processing")
        notebook.add(audio_tab, text="Audio")
        notebook.add(performance_tab, text="Performance")

        self._build_source_tab(source_tab)
        self._build_timing_tab(timing_tab)
        self._build_output_tab(output_tab)
        self._build_processing_tab(process_tab)
        self._build_audio_tab(audio_tab)
        self._build_performance_tab(performance_tab)

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right", padx=(0, 8))

    def _row(self, parent: ttk.Frame, row: int, label: str, widget: Any, help_text: str = "") -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        widget.grid(row=row, column=1, sticky="ew", padx=(12, 8), pady=5)
        if help_text:
            ttk.Label(parent, text=help_text, wraplength=300).grid(
                row=row, column=2, sticky="w", pady=5
            )
        parent.columnconfigure(1, weight=1)

    def _build_source_tab(self, tab: ttk.Frame) -> None:
        self._row(
            tab, 0, "Loading strategy",
            ttk.Combobox(tab, state="readonly", values=tuple(LOAD_LABELS), textvariable=self.load_var),
            "Automatic tries normal local serving, file URL, then embedded loading.",
        )
        self._row(
            tab, 1, "Capture target",
            ttk.Combobox(tab, state="readonly", values=tuple(CAPTURE_LABELS), textvariable=self.capture_var),
            "Automatic prefers export markers, then video/canvas/SVG, then the viewport.",
        )
        self._row(tab, 2, "CSS selector", ttk.Entry(tab, textvariable=self.selector_var), "Used only in CSS selector mode.")
        self._row(tab, 3, "Selector index", ttk.Spinbox(tab, from_=0, to=999, textvariable=self.selector_index_var), "Zero-based when a selector matches several elements.")
        self._row(tab, 4, "Viewport width", ttk.Spinbox(tab, from_=1, to=32768, textvariable=self.viewport_w_var))
        self._row(tab, 5, "Viewport height", ttk.Spinbox(tab, from_=1, to=32768, textvariable=self.viewport_h_var))
        self._row(tab, 6, "Manual source width", ttk.Entry(tab, textvariable=self.manual_w_var), "Leave both manual dimensions blank for automatic detection.")
        self._row(tab, 7, "Manual source height", ttk.Entry(tab, textvariable=self.manual_h_var))
        self._row(
            tab, 8, "Geometry",
            ttk.Combobox(tab, state="readonly", values=tuple(GEOMETRY_LABELS), textvariable=self.geometry_var),
            "Lock intrinsic neutralizes preview scaling; Preserve layout captures the displayed box.",
        )
        ttk.Checkbutton(tab, text="Transparent page background when supported", variable=self.transparent_var).grid(
            row=9, column=1, sticky="w", padx=(12, 8), pady=5
        )
        ttk.Checkbutton(
            tab,
            text="Remove outer preview shadow (only when it is not part of the artwork)",
            variable=self.strip_shadow_var,
        ).grid(row=10, column=1, columnspan=2, sticky="w", padx=(12, 8), pady=5)

    def _build_timing_tab(self, tab: ttk.Frame) -> None:
        self._row(
            tab, 0, "Timeline adapter",
            ttk.Combobox(tab, state="readonly", values=tuple(TIMELINE_LABELS), textvariable=self.timeline_var),
            "Automatic detects OM events, JS hooks, media, and Web Animations.",
        )
        self._row(tab, 1, "Manual duration (sec)", ttk.Entry(tab, textvariable=self.duration_var), "Blank means detect it from the source. There is no fixed fallback.")
        self._row(tab, 2, "Trim start (sec)", ttk.Entry(tab, textvariable=self.trim_start_var))
        self._row(tab, 3, "Trim end (sec)", ttk.Entry(tab, textvariable=self.trim_end_var), "Blank uses the detected/manual source duration.")
        self._row(tab, 4, "Hold first frame (sec)", ttk.Entry(tab, textvariable=self.hold_start_var))
        self._row(tab, 5, "Hold last frame (sec)", ttk.Entry(tab, textvariable=self.hold_end_var))
        self._row(tab, 6, "Custom event name", ttk.Entry(tab, textvariable=self.event_var))
        self._row(tab, 7, "JavaScript function", ttk.Entry(tab, textvariable=self.function_var), "Examples: window.seekTo or window.myExporter.seek")
        self._row(tab, 8, "Realtime start delay (sec)", ttk.Entry(tab, textvariable=self.realtime_delay_var))

    def _build_output_tab(self, tab: ttk.Frame) -> None:
        self._row(tab, 0, "Browser render scale", ttk.Combobox(tab, state="readonly", values=(0.5, 1, 1.5, 2, 3, 4), textvariable=self.scale_var), "2× rasterizes a 1080×1920 source directly at 2160×3840.")
        self._row(tab, 1, "Frames per second", ttk.Spinbox(tab, from_=1, to=240, textvariable=self.fps_var))
        profile_combo = ttk.Combobox(
            tab, state="readonly",
            values=tuple(profile.label for profile in OUTPUT_PROFILES.values()),
            textvariable=self.profile_var,
        )
        profile_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_profile_description())
        self._row(
            tab, 2, "Output profile", profile_combo,
            "Use a lossless/editing master for archive and a compatible delivery profile for upload.",
        )
        ttk.Label(tab, textvariable=self.profile_description_var, wraplength=620).grid(
            row=3, column=1, columnspan=2, sticky="w", padx=(12, 8), pady=(0, 8)
        )
        self.next_to_source_check = ttk.Checkbutton(
            tab, text="Save beside source HTML",
            variable=self.next_to_source_var, command=self._toggle_output
        )
        self.next_to_source_check.grid(row=4, column=1, sticky="w", padx=(12, 8), pady=5)
        if self.job.config.source.kind == SourceKind.URL:
            self.next_to_source_var.set(False)
            self.next_to_source_check.configure(state="disabled")
        output_row = ttk.Frame(tab)
        output_row.columnconfigure(0, weight=1)
        self.output_entry_widget = ttk.Entry(output_row, textvariable=self.output_dir_var)
        self.output_entry_widget.grid(row=0, column=0, sticky="ew")
        self.output_browse_widget = ttk.Button(output_row, text="Browse…", command=self._browse_output)
        self.output_browse_widget.grid(row=0, column=1, padx=(6, 0))
        self._row(tab, 5, "Output folder", output_row)
        self._row(tab, 6, "Filename template", ttk.Entry(tab, textvariable=self.filename_var), "Fields: {stem}, {scale}, {fps}, {profile}, {processing}, {ext}")
        ttk.Checkbutton(tab, text="Overwrite existing output", variable=self.overwrite_var).grid(row=7, column=1, sticky="w", padx=(12, 8), pady=5)
        from models import MAX_CPU_THREADS
        self._row(tab, 8, "CPU threads per export",
                  ttk.Spinbox(tab, from_=0, to=MAX_CPU_THREADS, textvariable=self.cpu_threads_var),
                  "0 = automatic, shared across queue workers. Higher values use more CPU/RAM.")

    def _build_processing_tab(self, tab: ttk.Frame) -> None:
        processing_combo = ttk.Combobox(
            tab, state="readonly", values=tuple(p.label for p in PROCESSING_PRESETS.values()),
            textvariable=self.processing_var
        )
        processing_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_processing_change())
        self._row(
            tab, 0, "Processing preset", processing_combo,
            "Auto samples the composition. No processing emits no visual FFmpeg filter chain.",
        )
        ttk.Label(tab, textvariable=self.processing_description_var, wraplength=620).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=(12, 8), pady=(0, 8)
        )
        sharpening = ttk.Combobox(tab, state="readonly", values=("none", "cas", "unsharp"), textvariable=self.sharpen_method_var)
        strength = ttk.Scale(tab, from_=0.0, to=1.0, variable=self.sharpen_strength_var, orient="horizontal")
        contrast = ttk.Spinbox(tab, from_=0.5, to=2.0, increment=0.01, textvariable=self.contrast_var)
        saturation = ttk.Spinbox(tab, from_=0.0, to=3.0, increment=0.01, textvariable=self.saturation_var)
        brightness = ttk.Spinbox(tab, from_=-1.0, to=1.0, increment=0.01, textvariable=self.brightness_var)
        deband = ttk.Checkbutton(tab, text="Enable mild debanding", variable=self.deband_var)
        self.custom_processing_widgets = [sharpening, strength, contrast, saturation, brightness, deband]
        self._row(tab, 2, "Custom sharpening", sharpening)
        self._row(tab, 3, "Sharpen strength", strength, "Only used by Custom.")
        self._row(tab, 4, "Contrast", contrast)
        self._row(tab, 5, "Saturation", saturation)
        self._row(tab, 6, "Brightness", brightness)
        deband.grid(row=7, column=1, sticky="w", padx=(12, 8), pady=5)
        ttk.Label(
            tab,
            text=(
                "Processing intentionally changes the captured pixels. For a source-faithful "
                "reference, choose No processing. Lossless encoding remains lossless relative "
                "to the processed frame."
            ),
            wraplength=650,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(16, 0))

    def _build_performance_tab(self, tab: ttk.Frame) -> None:
        self._row(tab, 0, "Capture workers per export",
                  ttk.Spinbox(tab, from_=0, to=MAX_CAPTURE_WORKERS, textvariable=self.capture_workers_var),
                  "0: auto for declared-safe sources; 1: sequential. 2+ asserts independent seeking.")
        self._row(tab, 1, "PNG frame buffer (MiB)",
                  ttk.Spinbox(tab, from_=16, to=4096, textvariable=self.frame_buffer_var),
                  "Bounds buffered PNG payloads. Browser and encoder RAM are additional.")
        ttk.Checkbutton(tab, text="Faster guarded viewport screenshots",
                        variable=self.fast_capture_var).grid(row=2, column=1, columnspan=2,
                                                             sticky="w", padx=12, pady=8)
        ttk.Label(tab, text=(
            "Capture workers accelerate a SINGLE movie with separate browser instances. "
            "Start with 4 for deterministic, independently seekable animations. "
            "Each instance must produce the same frame for a timestamp after a fresh load. "
            "Random content, external state, simulations and cumulative seek hooks are not safe. "
            "Browser Clock and Realtime remain sequential. Static holds reuse one frame. "
            "Queue workers run separate movies; CPU threads on Output control FFmpeg. "
            "Use the render log to inspect effective worker counts and stage timings."
        ), wraplength=650).grid(row=3, column=0, columnspan=3, sticky="w", pady=16)

    def _build_audio_tab(self, tab: ttk.Frame) -> None:
        from models import AUDIO_BITRATES, AUDIO_SAMPLE_RATES, AUDIO_CHANNELS
        self._row(tab, 0, "Audio mode", ttk.Combobox(tab, state="readonly", values=tuple(AUDIO_LABELS), textvariable=self.audio_mode_var))
        audio_row = ttk.Frame(tab)
        audio_row.columnconfigure(0, weight=1)
        ttk.Entry(audio_row, textvariable=self.audio_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(audio_row, text="Browse…", command=self._browse_audio).grid(row=0, column=1, padx=(6, 0))
        self._row(tab, 1, "Matched soundtrack", audio_row, "WAV / M4A / MP3 and other audio. This track belongs only to this HTML job; browser audio is not recorded.")
        self._row(tab, 2, "Volume (gain)", ttk.Spinbox(tab, from_=0.0, to=4.0, increment=0.05, textvariable=self.audio_volume_var), "0 = mute; 1 = original; 0.5 = half amplitude.")
        self._row(tab, 3, "Sync offset (sec)", ttk.Spinbox(tab, from_=-86400, to=86400, increment=0.01, textvariable=self.audio_offset_var), "+ delays audio; − skips its beginning. Relative to the output video, including holds.")
        self._row(tab, 4, "Fade in (sec)", ttk.Entry(tab, textvariable=self.audio_fade_in_var), "Starts with the audible track, after any positive delay.")
        self._row(tab, 5, "Fade out (sec)", ttk.Entry(tab, textvariable=self.audio_fade_out_var), "Ends with the video. Short tracks are padded with silence.")
        self.audio_bitrate_widget = ttk.Combobox(tab, state="readonly", values=AUDIO_BITRATES, textvariable=self.audio_bitrate_var)
        self._row(tab, 6, "Bitrate (kbps)", self.audio_bitrate_widget, "0 = profile default; 96 = voice; 192 = balanced; 256 = high quality. PCM ignores bitrate.")
        self._row(tab, 7, "Sample rate (Hz)", ttk.Combobox(tab, state="readonly", values=AUDIO_SAMPLE_RATES, textvariable=self.audio_sample_rate_var), "0 = profile default (48000). Opus requires 48000.")
        self._row(tab, 8, "Channels", ttk.Combobox(tab, state="readonly", values=AUDIO_CHANNELS, textvariable=self.audio_channels_var), "0 = profile default; 1 = mono; 2 = stereo.")
        ttk.Checkbutton(tab, text="Normalize loudness to −16 LUFS before gain/fades (optional)", variable=self.audio_normalize_var).grid(row=9, column=1, columnspan=2, sticky="w", padx=(12, 8), pady=5)
        ttk.Label(tab, textvariable=self.audio_encoding_var, wraplength=620).grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _toggle_output(self) -> None:
        if not hasattr(self, "output_entry_widget"):
            return
        state = "disabled" if self.next_to_source_var.get() else "normal"
        self.output_entry_widget.configure(state=state)
        self.output_browse_widget.configure(state=state)

    def _update_profile_description(self) -> None:
        key = OUTPUT_PROFILE_LABEL_TO_KEY.get(self.profile_var.get())
        self.profile_description_var.set(OUTPUT_PROFILES[key].description if key else "")
        if key and hasattr(self, "audio_encoding_var"):
            profile = OUTPUT_PROFILES[key]
            codec = {"aac": "AAC-LC", "libopus": "Opus", "pcm_s24le": "24-bit PCM"}.get(profile.audio_codec, profile.audio_codec)
            self.audio_encoding_var.set(
                f"Audio codec follows the video profile: {codec}. "
                "Social delivery defaults to AAC-LC, 256 kbps, 48 kHz stereo in fast-start MP4. "
                "Use Social delivery for phone/social uploads; master profiles remain specialist formats.")
            self.audio_bitrate_widget.configure(state="disabled" if profile.audio_codec.startswith("pcm_") else "readonly")

    def _on_processing_change(self) -> None:
        key = PROCESSING_LABEL_TO_KEY.get(self.processing_var.get(), "custom")
        preset = PROCESSING_PRESETS[key]
        self.processing_description_var.set(preset.description)
        custom = key == "custom"
        if not custom:
            config = preset.to_config()
            self.sharpen_method_var.set(config.sharpen_method)
            self.sharpen_strength_var.set(config.sharpen_strength)
            self.contrast_var.set(config.contrast)
            self.saturation_var.set(config.saturation)
            self.brightness_var.set(config.brightness)
            self.deband_var.set(config.deband)
        for widget in self.custom_processing_widgets:
            try:
                widget.configure(state="normal" if custom else "disabled")
            except Exception:
                pass

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Choose output folder")
        if folder:
            self.output_dir_var.set(folder)
            self.next_to_source_var.set(False)

    def _browse_audio(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Choose audio",
            filetypes=[
                ("Audio files", "*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.audio_path_var.set(path)
            if self.audio_mode_var.get() == "No audio":
                self.audio_mode_var.set("Use once / trim")

    def _apply_recipe(self) -> None:
        key = RECIPE_LABEL_TO_KEY[self.recipe_var.get()]
        fresh = RECIPES[key].create_job(self.job.config.source.value, self.job.config.source.kind)
        self.capture_var.set(reverse_lookup(CAPTURE_LABELS, fresh.capture.mode, "Automatic"))
        self.viewport_w_var.set(fresh.capture.viewport_width)
        self.viewport_h_var.set(fresh.capture.viewport_height)
        self.geometry_var.set(reverse_lookup(GEOMETRY_LABELS, fresh.capture.geometry_mode, "Automatic"))
        self.timeline_var.set(reverse_lookup(TIMELINE_LABELS, fresh.timeline.mode, "Automatic"))
        self.duration_var.set("" if fresh.timeline.manual_duration is None else str(fresh.timeline.manual_duration))
        self.scale_var.set(fresh.render.scale)
        self.fps_var.set(fresh.render.fps)
        self.profile_var.set(OUTPUT_PROFILES[fresh.render.output_profile_key].label)
        self.processing_var.set(PROCESSING_PRESETS[fresh.render.processing.preset_key].label)
        self._update_profile_description()

    @staticmethod
    def _optional_float(value: str) -> Optional[float]:
        value = value.strip()
        return None if not value else float(value)

    @staticmethod
    def _optional_int(value: str) -> Optional[int]:
        value = value.strip()
        return None if not value else int(value)

    def _save(self) -> None:
        try:
            config = copy.deepcopy(self.job.config)
            config.recipe_key = RECIPE_LABEL_TO_KEY[self.recipe_var.get()]
            config.source.load_strategy = LOAD_LABELS[self.load_var.get()]
            config.capture.mode = CAPTURE_LABELS[self.capture_var.get()]
            config.capture.selector = self.selector_var.get().strip()
            config.capture.selector_index = int(self.selector_index_var.get())
            config.capture.viewport_width = int(self.viewport_w_var.get())
            config.capture.viewport_height = int(self.viewport_h_var.get())
            config.capture.manual_width = self._optional_int(self.manual_w_var.get())
            config.capture.manual_height = self._optional_int(self.manual_h_var.get())
            config.capture.geometry_mode = GEOMETRY_LABELS[self.geometry_var.get()]
            config.capture.transparent_background = bool(self.transparent_var.get())
            config.capture.strip_outer_shadow = bool(self.strip_shadow_var.get())

            config.timeline.mode = TIMELINE_LABELS[self.timeline_var.get()]
            config.timeline.manual_duration = self._optional_float(self.duration_var.get())
            config.timeline.trim_start = float(self.trim_start_var.get())
            config.timeline.trim_end = self._optional_float(self.trim_end_var.get())
            config.timeline.hold_start = float(self.hold_start_var.get())
            config.timeline.hold_end = float(self.hold_end_var.get())
            config.timeline.custom_event_name = self.event_var.get().strip()
            config.timeline.javascript_function = self.function_var.get().strip()
            config.timeline.realtime_start_delay = float(self.realtime_delay_var.get())

            config.render.scale = float(self.scale_var.get())
            config.render.fps = int(self.fps_var.get())
            config.render.cpu_threads = int(self.cpu_threads_var.get())
            config.render.capture_workers = int(self.capture_workers_var.get())
            config.render.frame_buffer_mb = int(self.frame_buffer_var.get())
            config.render.fast_capture = bool(self.fast_capture_var.get())
            config.render.output_profile_key = OUTPUT_PROFILE_LABEL_TO_KEY[self.profile_var.get()]
            config.render.save_next_to_source = bool(self.next_to_source_var.get())
            config.render.output_directory = self.output_dir_var.get().strip()
            config.render.overwrite = bool(self.overwrite_var.get())
            config.render.filename_template = self.filename_var.get().strip()

            processing_key = PROCESSING_LABEL_TO_KEY[self.processing_var.get()]
            if processing_key == "custom":
                config.render.processing = ProcessingConfig(
                    preset_key="custom",
                    sharpen_method=self.sharpen_method_var.get(),
                    sharpen_strength=float(self.sharpen_strength_var.get()),
                    contrast=float(self.contrast_var.get()),
                    saturation=float(self.saturation_var.get()),
                    brightness=float(self.brightness_var.get()),
                    deband=bool(self.deband_var.get()),
                )
            else:
                config.render.processing = PROCESSING_PRESETS[processing_key].to_config()

            config.render.audio.mode = AUDIO_LABELS[self.audio_mode_var.get()]
            config.render.audio.path = self.audio_path_var.get().strip()
            config.render.audio.volume = float(self.audio_volume_var.get())
            config.render.audio.offset_seconds = float(self.audio_offset_var.get())
            config.render.audio.fade_in_seconds = float(self.audio_fade_in_var.get())
            config.render.audio.fade_out_seconds = float(self.audio_fade_out_var.get())
            config.render.audio.bitrate_kbps = int(self.audio_bitrate_var.get())
            config.render.audio.sample_rate_hz = int(self.audio_sample_rate_var.get())
            config.render.audio.channels = int(self.audio_channels_var.get())
            config.render.audio.normalize_loudness = bool(self.audio_normalize_var.get())
            if config.render.audio.mode != AudioMode.NONE:
                from media_pipeline import audio_source_path
                config.render.audio.path = str(audio_source_path(config.render.audio.path))

            config.validate()
            self.result = config
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self)
            return
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def destroy(self) -> None:
        widgets = widget_tree(self)
        release_tk_variables(self)
        super().destroy()
        for widget in widgets:
            widget.tk = None



class StudioApp(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.jobs: list[QueueJob] = []
        self.jobs_lock = threading.RLock()
        self.project_path: Optional[Path] = None
        self.project_dirty = False
        self.environment: dict[str, Any] = {}

        self.ui_events = queue.SimpleQueue()
        self.closed_event = threading.Event()
        self.stop_event = threading.Event()
        self.analysis_cancels: dict[str, threading.Event] = {}
        self.analysis_restore: dict[str, JobStatus] = {}
        self.background_threads: list[threading.Thread] = []
        self.update_in_progress = False
        self.checking_updates = False
        self.preview_directory = tempfile.TemporaryDirectory(prefix='hves-previews-')
        self.running = False
        self.paused = False
        self.stop_requested = False
        self.work_queue: queue.Queue = queue.Queue()
        self.worker_threads: list[threading.Thread] = []
        self.active_cancels: dict[str, threading.Event] = {}
        self.run_event = threading.Event()
        self.run_event.set()
        self.analysis_semaphore = threading.Semaphore(2)
        self.analysis_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="hves-analyze")
        self.analysis_futures: list[concurrent.futures.Future] = []
        self.run_job_ids: set[str] = set()

        self.default_recipe_var = StringVar(
            value=RECIPES.get(
                self.settings.default_recipe_key, RECIPES["motion_graphics_master"]
            ).label
        )
        self.workers_var = IntVar(value=self.settings.workers)
        self.auto_analyze_var = BooleanVar(value=self.settings.auto_analyze_on_add)
        self.default_output_dir_var = StringVar(value=self.settings.default_output_directory)
        self.default_next_to_source_var = BooleanVar(value=self.settings.save_next_to_source)
        self.default_overwrite_var = BooleanVar(value=self.settings.overwrite)
        self.update_repo_var = StringVar(value=self.settings.update_repo)
        self.update_startup_var = BooleanVar(value=self.settings.check_updates_at_startup)
        self.environment_var = StringVar(value="Checking dependencies…")
        self.queue_summary_var = StringVar(value="No jobs")
        self.inspector_title_var = StringVar(value="Select a job to inspect")
        self.processing_help_var = StringVar(value="")
        self.recipe_help_var = StringVar(value="")
        self.filter_queue_var = StringVar(value="")

        self.title(f"{APP_NAME} {APP_VERSION}")
        try:
            self.geometry(self.settings.window_geometry)
        except Exception:
            self.geometry("1180x800")
        self.minsize(1020, 680)
        self._apply_platform_tweaks()
        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(40, self._drain_ui)
        self.after(200, self._check_environment_async)

    def _apply_platform_tweaks(self) -> None:
        if platform.system() == "Windows":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

    def _build_menu(self) -> None:
        menubar = Menu(self)
        file_menu = Menu(menubar, tearoff=False)
        file_menu.add_command(label="Add HTML Files…", accelerator="Ctrl+O", command=self._add_files)
        file_menu.add_command(label="Add URL…", accelerator="Ctrl+L", command=self._add_url)
        file_menu.add_command(label="Add Folder…", command=self._add_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Open Project…", accelerator="Ctrl+Shift+O", command=self._open_project)
        file_menu.add_command(label="Save Project", accelerator="Ctrl+S", command=self._save_project)
        file_menu.add_command(label="Save Project As…", accelerator="Ctrl+Shift+S", command=self._save_project_as)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        queue_menu = Menu(menubar, tearoff=False)
        queue_menu.add_command(label="Start Queue", accelerator="F5", command=self._start_queue)
        queue_menu.add_command(label="Pause / Resume", accelerator="F6", command=self._toggle_pause)
        queue_menu.add_command(label="Cancel Selected", accelerator="Delete", command=self._cancel_selected)
        queue_menu.add_command(label="Stop Queue", accelerator="Shift+F5", command=self._stop_queue)
        queue_menu.add_separator()
        queue_menu.add_command(label="Analyze Selected", command=self._analyze_selected)
        queue_menu.add_command(label="Capture Test Frame", command=self._capture_test_frame)
        queue_menu.add_command(label="Compare Processing", command=self._compare_processing)
        queue_menu.add_command(label="Job Settings…", accelerator="Enter", command=self._edit_selected)
        menubar.add_cascade(label="Queue", menu=queue_menu)

        help_menu = Menu(menubar, tearoff=False)
        help_menu.add_command(label="Check for Updates…", command=lambda: self._check_updates_async(False))
        help_menu.add_command(label="Open Documentation", command=self._open_documentation)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text=APP_NAME, font=("TkDefaultFont", 17, "bold")).pack(side="left")
        ttk.Label(header, text=f"v{APP_VERSION}").pack(side="left", padx=(8, 0), pady=(5, 0))
        ttk.Label(header, textvariable=self.environment_var).pack(side="right")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)

        queue_tab = ttk.Frame(self.notebook, padding=8)
        inspector_tab = ttk.Frame(self.notebook, padding=8)
        presets_tab = ttk.Frame(self.notebook, padding=8)
        settings_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(queue_tab, text="Export Queue")
        self.notebook.add(inspector_tab, text="Source Inspector")
        self.notebook.add(presets_tab, text="Recipes & Presets")
        self.notebook.add(settings_tab, text="Settings & Updates")

        self._build_queue_tab(queue_tab)
        self._build_inspector_tab(inspector_tab)
        self._build_presets_tab(presets_tab)
        self._build_settings_tab(settings_tab)

        status = ttk.Frame(root)
        status.pack(side="bottom", fill="x", pady=(8, 0), before=self.notebook)
        self.overall_progress = ttk.Progressbar(status, maximum=100, mode="determinate")
        self.overall_progress.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ttk.Label(status, textvariable=self.queue_summary_var, width=38).pack(side="left")

    def _build_queue_tab(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Button(top, text="＋ HTML Files", command=self._add_files).pack(side="left", padx=(0, 5))
        ttk.Button(top, text="＋ URL", command=self._add_url).pack(side="left", padx=(0, 5))
        ttk.Button(top, text="＋ Folder", command=self._add_folder).pack(side="left", padx=(0, 12))
        ttk.Button(top, text="Job Settings", command=self._edit_selected).pack(side="left", padx=(0, 5))
        ttk.Button(top, text="Analyze", command=self._analyze_selected).pack(side="left", padx=(0, 5))
        ttk.Button(top, text="Test Frame", command=self._capture_test_frame).pack(side="left", padx=(0, 5))
        ttk.Button(top, text="Compare", command=self._compare_processing).pack(side="left", padx=(0, 12))
        selector_bar = ttk.Frame(tab)
        selector_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(selector_bar, text="New-job recipe:").pack(side="left")
        ttk.Combobox(
            selector_bar,
            state="readonly",
            values=tuple(recipe.label for recipe in RECIPES.values()),
            textvariable=self.default_recipe_var,
            width=27,
        ).pack(side="left", padx=(6, 12))
        ttk.Label(selector_bar, text="Filter queue:").pack(side="left")
        filter_entry = ttk.Entry(selector_bar, textvariable=self.filter_queue_var, width=20)
        self.filter_entry = filter_entry
        filter_entry.pack(side="left", padx=(6, 0))
        self.filter_queue_var.trace_add("write", lambda *_: self._refresh_tree())

        audio_bar = ttk.Frame(tab)
        audio_bar.pack(fill="x", pady=(0, 8))
        ttk.Button(audio_bar, text="Attach Audio…", command=self._attach_audio).pack(side="left", padx=(0, 5))
        ttk.Button(audio_bar, text="Match by Filename…", command=self._match_audio).pack(side="left", padx=(0, 5))
        ttk.Button(audio_bar, text="Remove Audio", command=self._remove_audio).pack(side="left")

        paned = ttk.Panedwindow(tab, orient="vertical")
        paned.pack(fill="both", expand=True)

        queue_frame = ttk.Frame(paned)
        log_frame = ttk.LabelFrame(paned, text="Activity", padding=6)
        paned.add(queue_frame, weight=4)
        paned.add(log_frame, weight=1)

        columns = (
            "source", "audio", "recipe", "dimensions", "duration", "timeline",
            "status", "progress", "eta", "output"
        )
        self.tree = ttk.Treeview(queue_frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "source": "Source",
            "audio": "Soundtrack",
            "recipe": "Recipe",
            "dimensions": "Output",
            "duration": "Duration",
            "timeline": "Timeline",
            "status": "Status",
            "progress": "Progress",
            "eta": "ETA",
            "output": "Output file",
        }
        widths = {
            "source": 260, "audio": 230, "recipe": 145, "dimensions": 115, "duration": 72,
            "timeline": 120, "status": 90, "progress": 76, "eta": 62, "output": 290,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w", stretch=column in {"source", "output"})
        yscroll = ttk.Scrollbar(queue_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(queue_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.tag_configure("done", foreground="#297a3a")
        self.tree.tag_configure("error", foreground="#a12622")
        self.tree.tag_configure("active", foreground="#145a9c")
        self.tree.tag_configure("cancelled", foreground="#777777")
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        queue_frame.rowconfigure(0, weight=1)
        queue_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_inspector())
        self.tree.bind("<Double-1>", lambda _e: self._edit_selected())

        action = ttk.Frame(queue_frame)
        action.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(action, text="Remove", command=self._remove_selected).pack(side="left", padx=(0, 5))
        ttk.Button(action, text="Duplicate", command=self._duplicate_selected).pack(side="left", padx=(0, 5))
        ttk.Button(action, text="Requeue", command=self._requeue_selected).pack(side="left", padx=(0, 5))
        ttk.Button(action, text="Clear Finished", command=self._clear_finished).pack(side="left", padx=(0, 12))
        ttk.Button(action, text="▲", width=3, command=lambda: self._move_selected(-1)).pack(side="left", padx=(0, 3))
        ttk.Button(action, text="▼", width=3, command=lambda: self._move_selected(1)).pack(side="left", padx=(0, 12))
        ttk.Button(action, text="Open Output", command=self._open_selected_output).pack(side="left", padx=(0, 5))
        ttk.Button(action, text="Reveal Folder", command=self._reveal_selected_output).pack(side="left")

        controls = ttk.Frame(queue_frame)
        controls.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(controls, text="Export workers:").pack(side="left")
        ttk.Spinbox(controls, from_=1, to=8, textvariable=self.workers_var, width=4).pack(side="left", padx=(6, 12))
        self.start_button = ttk.Button(controls, text="Start Queue", command=self._start_queue)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(controls, text="Stop", command=self._stop_queue, state="disabled")
        self.stop_button.pack(side="right", padx=(0, 5))
        self.cancel_button = ttk.Button(controls, text="Cancel Selected", command=self._cancel_selected, state="disabled")
        self.cancel_button.pack(side="right", padx=(0, 5))
        self.pause_button = ttk.Button(controls, text="Pause", command=self._toggle_pause, state="disabled")
        self.pause_button.pack(side="right", padx=(0, 5))

        self.log = ScrolledText(log_frame, height=7, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)

    def _build_inspector_tab(self, tab: ttk.Frame) -> None:
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, textvariable=self.inspector_title_var, font=("TkDefaultFont", 13, "bold")).pack(side="left")
        ttk.Button(top, text="Analyze Selected", command=self._analyze_selected).pack(side="right")
        ttk.Button(top, text="Capture Test Frame", command=self._capture_test_frame).pack(side="right", padx=(0, 6))
        ttk.Button(top, text="Compare Processing", command=self._compare_processing).pack(side="right", padx=(0, 6))
        ttk.Button(top, text="Edit Job", command=self._edit_selected).pack(side="right", padx=(0, 6))

        self.inspector = ScrolledText(tab, wrap="word", state="disabled", font=("TkFixedFont", 10))
        self.inspector.pack(fill="both", expand=True)

    def _build_presets_tab(self, tab: ttk.Frame) -> None:
        paned = ttk.Panedwindow(tab, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned)
        right = ttk.Frame(paned, padding=(14, 0, 0, 0))
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        ttk.Label(left, text="Workflow recipes", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        self.recipe_list = ttk.Treeview(left, columns=("name",), show="tree", selectmode="browse")
        for key, recipe in RECIPES.items():
            self.recipe_list.insert("", "end", iid=key, text=recipe.label)
        self.recipe_list.pack(fill="both", expand=True, pady=(8, 8))
        self.recipe_list.bind("<<TreeviewSelect>>", lambda _e: self._show_recipe())
        ttk.Button(left, text="Apply to Selected Jobs", command=self._apply_recipe_to_selected).pack(fill="x")
        ttk.Button(left, text="Set as New-job Default", command=self._set_default_recipe).pack(fill="x", pady=(6, 0))

        ttk.Label(right, text="Recipe details", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(right, textvariable=self.recipe_help_var, wraplength=720, justify="left").pack(anchor="w", pady=(8, 14))
        ttk.Separator(right).pack(fill="x", pady=8)
        ttk.Label(right, text="Processing presets", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        self.processing_list = ttk.Treeview(right, columns=("name",), show="tree", height=8, selectmode="browse")
        for key, preset in PROCESSING_PRESETS.items():
            self.processing_list.insert("", "end", iid=key, text=preset.label)
        self.processing_list.pack(fill="x", pady=(8, 8))
        self.processing_list.bind("<<TreeviewSelect>>", lambda _e: self._show_processing())
        ttk.Label(right, textvariable=self.processing_help_var, wraplength=720, justify="left").pack(anchor="w")
        ttk.Button(right, text="Apply Processing to Selected Jobs", command=self._apply_processing_to_selected).pack(anchor="w", pady=(10, 0))

        self.recipe_list.selection_set("motion_graphics_master")
        self.processing_list.selection_set("auto_content_aware")
        self._show_recipe()
        self._show_processing()

    def _build_settings_tab(self, tab: ttk.Frame) -> None:
        general = ttk.LabelFrame(tab, text="Performance and defaults", padding=12)
        general.pack(fill="x")
        ttk.Label(general, text="Parallel workers").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(general, from_=1, to=8, textvariable=self.workers_var, width=8).grid(row=0, column=1, sticky="w", padx=(10, 18))
        ttk.Label(
            general,
            text="Each worker owns a separate Chromium and FFmpeg process. At 2×/4K, 1–2 workers is usually safest.",
            wraplength=650,
        ).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(
            general,
            text="Analyze new jobs automatically",
            variable=self.auto_analyze_var,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(
            general,
            text="Save new file jobs beside their source",
            variable=self.default_next_to_source_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            general,
            text="Overwrite existing outputs by default",
            variable=self.default_overwrite_var,
        ).grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Label(general, text="Default output folder").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(general, textvariable=self.default_output_dir_var).grid(
            row=3, column=1, sticky="ew", padx=(10, 8), pady=(8, 0)
        )
        ttk.Button(general, text="Browse…", command=self._browse_default_output).grid(
            row=3, column=2, sticky="w", pady=(8, 0)
        )
        general.columnconfigure(1, weight=1)
        ttk.Button(general, text="Run Dependency Diagnostics", command=self._check_environment_async).grid(row=4, column=0, sticky="w", pady=(12, 0))

        updates = ttk.LabelFrame(tab, text="GitHub updates", padding=12)
        updates.pack(fill="x", pady=(12, 0))
        updates.columnconfigure(1, weight=1)
        ttk.Label(updates, text="Repository").grid(row=0, column=0, sticky="w")
        ttk.Entry(updates, textvariable=self.update_repo_var).grid(row=0, column=1, sticky="ew", padx=(10, 8))
        ttk.Button(updates, text="Detect origin", command=self._detect_origin).grid(row=0, column=2)
        ttk.Label(
            updates,
            text="Use owner/repository or a GitHub URL. Public repos need no token; private repos may use HTML_MP4_GITHUB_TOKEN.",
            wraplength=850,
        ).grid(row=1, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(5, 0))
        ttk.Checkbutton(
            updates,
            text="Check for updates at startup",
            variable=self.update_startup_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(updates, text="Check Now…", command=lambda: self._check_updates_async(False)).grid(row=2, column=2, sticky="e", pady=(10, 0))

        limits = ttk.LabelFrame(tab, text="Universal capture guidance", padding=12)
        limits.pack(fill="both", expand=True, pady=(12, 0))
        ttk.Label(
            limits,
            justify="left",
            wraplength=950,
            text=(
                "Deterministic adapters (export event, JavaScript function, Web Animations, media currentTime) are preferred. "
                "Browser Clock covers many timer/rAF-driven pages but cannot control every worker, WebGL, streaming, or network-driven animation. "
                "Realtime mode is a compatibility fallback and can miss timing under load. Browser audio is not recorded; attach an external audio track per job. "
                "For exact pixels choose No processing + Lossless RGB. For normal playback/upload, create a separate H.264 4:2:0 delivery copy."
            ),
        ).pack(anchor="w")

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-o>", lambda _e: self._add_files())
        self.bind_all("<Control-l>", lambda _e: self._add_url())
        self.bind_all("<Control-s>", lambda _e: self._save_project())
        self.bind_all("<Control-Shift-S>", lambda _e: self._save_project_as())
        self.bind_all("<Control-Shift-O>", lambda _e: self._open_project())
        self.bind_all("<F5>", lambda _e: self._start_queue())
        self.bind_all("<F6>", lambda _e: self._toggle_pause())
        self.tree.bind("<Delete>", lambda _e: self._cancel_or_remove_selected())
        self.tree.bind("<Return>", lambda _e: self._edit_selected())

    def _default_recipe_key(self) -> str:
        return RECIPE_LABEL_TO_KEY.get(self.default_recipe_var.get(), "motion_graphics_master")

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose HTML files",
            filetypes=[("HTML files", "*.html *.htm"), ("All files", "*.*")],
        )
        self._add_sources([(str(Path(path).resolve()), SourceKind.FILE) for path in paths])

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose a folder containing HTML files")
        if not folder:
            return
        base = Path(folder)
        paths = sorted([*base.rglob("*.html"), *base.rglob("*.htm")], key=lambda p: str(p).lower())
        if len(paths) > 500 and not messagebox.askyesno(
            "Large folder",
            f"This folder contains {len(paths)} HTML files. Add all of them?",
        ):
            return
        self._add_sources([(str(path.resolve()), SourceKind.FILE) for path in paths])

    def _add_url(self) -> None:
        value = simpledialog.askstring("Add URL", "Web page URL:", parent=self)
        if not value:
            return
        value = value.strip()
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            messagebox.showerror("Invalid URL", "Use an http:// or https:// URL.")
            return
        self._add_sources([(value, SourceKind.URL)])

    def _add_sources(self, sources: list[tuple[str, SourceKind]]) -> None:
        recipe_key = self._default_recipe_key()
        existing = {(job.config.source.kind, job.config.source.value) for job in self.jobs}
        added: list[QueueJob] = []
        for value, kind in sources:
            key = (kind, value)
            if key in existing:
                continue
            config = RECIPES[recipe_key].create_job(value, kind)
            config.render.save_next_to_source = (
                bool(self.default_next_to_source_var.get()) if kind == SourceKind.FILE else False
            )
            config.render.output_directory = self.default_output_dir_var.get().strip()
            config.render.overwrite = bool(self.default_overwrite_var.get())
            job = QueueJob(job_id=uuid.uuid4().hex, config=config)
            self.jobs.append(job)
            existing.add(key)
            added.append(job)
            self.settings.recent_sources = add_recent(self.settings.recent_sources, value)
        if added:
            self.project_dirty = True
            self._append_log(f"Added {len(added)} source(s).")
            self._refresh_tree()
            if self.auto_analyze_var.get():
                for job in added:
                    self._analyze_job_async(job)

    def _selected_jobs(self) -> list[QueueJob]:
        selected = set(self.tree.selection())
        return [job for job in self.jobs if job.job_id in selected]

    def _refresh_tree(self) -> None:
        selected = set(self.tree.selection())
        query_text = self.filter_queue_var.get().strip().lower()
        visible_ids: set[str] = set()
        for index, job in enumerate(self.jobs):
            source_text = job.config.source.display_name
            haystack = " ".join(
                [source_text, job.config.render.audio.path, job.status.value, job.phase, job.config.recipe_key, job.output_path]
            ).lower()
            if query_text and query_text not in haystack:
                if self.tree.exists(job.job_id):
                    self.tree.delete(job.job_id)
                continue
            visible_ids.add(job.job_id)
            probe = job.probe
            dimensions = (
                f"{probe.output_width}×{probe.output_height}" if probe else "—"
            )
            duration = (
                f"{probe.duration_seconds:.3f}s" if probe and probe.duration_seconds is not None else "—"
            )
            timeline = probe.timeline_mode.value if probe else job.config.timeline.mode.value
            recipe = RECIPES.get(job.config.recipe_key)
            audio = job.config.render.audio
            soundtrack = (f"{Path(audio.path).name} · {audio.mode.value} · {audio.offset_seconds:+.3f}s"
                          if audio.mode != AudioMode.NONE else "No audio")
            values = (
                source_text,
                soundtrack,
                recipe.label if recipe else job.config.recipe_key,
                dimensions,
                duration,
                timeline,
                job.status.value,
                f"{job.progress * 100:.1f}%",
                format_seconds(job.eta_seconds),
                job.output_path,
            )
            tag = (
                "done" if job.status == JobStatus.DONE else
                "error" if job.status == JobStatus.ERROR else
                "active" if job.status in {JobStatus.RENDERING, JobStatus.ANALYZING} else
                "cancelled" if job.status == JobStatus.CANCELLED else ""
            )
            tags = (tag,) if tag else ()
            if self.tree.exists(job.job_id):
                self.tree.item(job.job_id, values=values, tags=tags)
                self.tree.move(job.job_id, "", index)
            else:
                self.tree.insert("", "end", iid=job.job_id, values=values, tags=tags)
        for iid in self.tree.get_children(""):
            if iid not in visible_ids:
                self.tree.delete(iid)
        for iid in selected:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)
        self._update_queue_summary()

    def _editable_audio_jobs(self, *, single: bool = False) -> list[QueueJob]:
        jobs = self._selected_jobs()
        if not jobs or (single and len(jobs) != 1):
            messagebox.showinfo("Select jobs", "Select exactly one HTML job to attach a soundtrack." if single else "Select one or more HTML jobs.", parent=self)
            return []
        if self.update_in_progress or any(self._is_busy(job) for job in jobs):
            messagebox.showinfo("Jobs are busy", "Finish or stop the queue/analysis before changing soundtracks.", parent=self)
            return []
        return jobs

    def _set_job_audio(self, job: QueueJob, path: Optional[Path]) -> None:
        job.config.render.audio.path = str(path) if path else ""
        if path and job.config.render.audio.mode == AudioMode.NONE:
            job.config.render.audio.mode = AudioMode.TRIM
        if path is None:
            job.config.render.audio.mode = AudioMode.NONE
        job.status = JobStatus.QUEUED
        job.progress = 0.0
        job.phase = "Queued"
        job.probe = None
        job.error = ""
        job.output_path = ""
        job.elapsed_seconds = 0.0
        job.eta_seconds = None
        self.project_dirty = True

    def _attach_audio(self) -> None:
        jobs = self._editable_audio_jobs(single=True)
        if not jobs:
            return
        path = filedialog.askopenfilename(parent=self, title=f"Soundtrack for {jobs[0].config.source.display_name}",
            filetypes=[("Audio files", "*.wav *.m4a *.mp3 *.WAV *.M4A *.MP3 *.aac *.flac *.ogg *.opus"), ("All files", "*.*")])
        if not path or self._is_busy(jobs[0]) or self.update_in_progress:
            return
        from media_pipeline import audio_source_path
        try:
            resolved = audio_source_path(path)
        except ValueError as exc:
            messagebox.showerror("Invalid audio", str(exc), parent=self)
            return
        self._set_job_audio(jobs[0], resolved)
        self._refresh_tree()
        self._update_inspector()

    def _match_audio(self) -> None:
        jobs = self._editable_audio_jobs()
        if not jobs:
            return
        paths = filedialog.askopenfilenames(parent=self, title="Match soundtracks by HTML filename",
            filetypes=[("Audio files", "*.wav *.m4a *.mp3 *.WAV *.M4A *.MP3 *.aac *.flac *.ogg *.opus"), ("All files", "*.*")])
        if not paths or any(self._is_busy(job) for job in jobs) or self.update_in_progress:
            return
        from media_pipeline import find_matching_audio
        matched = 0
        notes = []
        for job in jobs:
            if job.config.render.audio.path.strip():
                notes.append(f"Kept existing soundtrack: {job.config.source.display_name}")
                continue
            try:
                path = find_matching_audio(job.config.source, [Path(p) for p in paths])
            except ValueError as exc:
                notes.append(str(exc))
                continue
            if path is None:
                notes.append(f"No filename match: {job.config.source.display_name}")
                continue
            self._set_job_audio(job, path)
            matched += 1
        self._refresh_tree()
        self._update_inspector()
        messagebox.showinfo("Audio matching", f"Matched {matched} soundtrack(s)." + ("\n\n" + "\n".join(notes) if notes else ""), parent=self)

    def _remove_audio(self) -> None:
        jobs = self._editable_audio_jobs()
        for job in jobs:
            self._set_job_audio(job, None)
        if jobs:
            self._refresh_tree()
            self._update_inspector()

    def _edit_selected(self) -> None:
        jobs = self._selected_jobs()
        if len(jobs) != 1:
            messagebox.showinfo("Select one job", "Select exactly one queue job to edit.")
            return
        job = jobs[0]
        if self._is_busy(job):
            messagebox.showinfo('Job is busy', 'Finish or stop this queue/analysis before editing its settings.')
            return
        dialog = JobEditor(self, job)
        self.wait_window(dialog)
        if dialog.result is not None:
            job.config = dialog.result
            job.status = JobStatus.QUEUED
            job.progress = 0.0
            job.probe = None
            job.error = ""
            job.output_path = ""
            self.project_dirty = True
            self._refresh_tree()
            self._update_inspector()

    def _remove_selected(self) -> None:
        selected = self._selected_jobs()
        if not selected:
            return
        active = [job for job in selected if self._is_busy(job)]
        if active:
            messagebox.showinfo("Jobs are rendering", "Cancel active jobs before removing them.")
            return
        ids = {job.job_id for job in selected}
        self.jobs = [job for job in self.jobs if job.job_id not in ids]
        self.project_dirty = True
        self._refresh_tree()
        self._update_inspector()

    def _duplicate_selected(self) -> None:
        selected = self._selected_jobs()
        if not selected:
            return
        for source in selected:
            duplicate = QueueJob(
                job_id=uuid.uuid4().hex,
                config=copy.deepcopy(source.config),
            )
            self.jobs.insert(self.jobs.index(source) + 1, duplicate)
        self.project_dirty = True
        self._refresh_tree()

    def _requeue_selected(self) -> None:
        for job in self._selected_jobs():
            if not self._is_busy(job):
                job.status = JobStatus.QUEUED
                job.progress = 0.0
                job.phase = "Queued"
                job.error = ""
                job.eta_seconds = None
        self._refresh_tree()

    def _clear_finished(self) -> None:
        if self.running:
            return
        finished = {JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED, JobStatus.SKIPPED}
        self.jobs = [job for job in self.jobs if job.status not in finished]
        self.project_dirty = True
        self._refresh_tree()

    def _move_selected(self, direction: int) -> None:
        if self.running:
            return
        selected = self._selected_jobs()
        if len(selected) != 1:
            return
        job = selected[0]
        index = self.jobs.index(job)
        new_index = max(0, min(len(self.jobs) - 1, index + direction))
        if new_index != index:
            self.jobs.pop(index)
            self.jobs.insert(new_index, job)
            self.project_dirty = True
            self._refresh_tree()
            self.tree.selection_set(job.job_id)

    def _cancel_or_remove_selected(self) -> None:
        if any(job.status == JobStatus.RENDERING for job in self._selected_jobs()):
            self._cancel_selected()
        else:
            self._remove_selected()

    def _analyze_selected(self) -> None:
        jobs = self._selected_jobs()
        if not jobs:
            messagebox.showinfo("Nothing selected", "Select one or more jobs to analyze.")
            return
        for job in jobs:
            if not self._is_busy(job):
                self._analyze_job_async(job)

    def _analyze_job_async(self, job: QueueJob) -> None:
        if self._is_busy(job) or self.update_in_progress: return
        event=threading.Event()
        self.analysis_cancels[job.job_id]=event
        self.analysis_restore[job.job_id]=job.status
        job.status=JobStatus.ANALYZING; job.phase='Analyzing'
        self._refresh_tree()
        config=copy.deepcopy(job.config)
        job_id=job.job_id
        def worker():
            with self.analysis_semaphore:
                try:
                    if event.is_set(): raise ExportCancelled('Analysis cancelled.')
                    with HtmlVideoRenderer(log_callback=lambda m:self._ui(self._append_log,m)) as renderer:
                        probe=renderer.probe(config,deep_analysis=True,cancel_event=event)
                    self._ui(self._analysis_finished,job_id,probe,'')
                except Exception as exc:
                    self._ui(self._analysis_finished,job_id,None,str(exc))
        self.analysis_futures = [f for f in self.analysis_futures if not f.done()]
        self.analysis_futures.append(self.analysis_pool.submit(worker))

    def _analysis_finished(self, job_id: str, probe: Any, error: str) -> None:
        event=self.analysis_cancels.pop(job_id,None)
        self.analysis_restore.pop(job_id,None)
        if event is not None and event.is_set(): return
        job = next((item for item in self.jobs if item.job_id == job_id), None)
        if not job:
            return
        if error:
            job.status = JobStatus.ERROR
            job.error = error
            job.phase = "Analysis failed"
            self._append_log(f"Analysis failed for {job.config.source.display_name}: {error}")
        else:
            job.probe = probe
            job.status = JobStatus.ERROR if probe.has_errors else JobStatus.READY
            job.error = "\n".join(
                item.message for item in probe.diagnostics if item.severity == "error"
            )
            job.phase = "Analyzed"
            self._append_log(
                f"Analyzed {job.config.source.display_name}: "
                f"{probe.source_width}×{probe.source_height} → "
                f"{probe.output_width}×{probe.output_height}, "
                f"{probe.duration_seconds if probe.duration_seconds is not None else 'duration required'}, "
                f"{probe.timeline_mode.value}."
            )
        self._refresh_tree()
        self._update_inspector()

    def _capture_test_frame(self) -> None:
        self._start_preview(False)

    def _compare_processing(self) -> None:
        self._start_preview(True)

    def _start_preview(self, comparison: bool) -> None:
        jobs=self._selected_jobs()
        if len(jobs)!=1:
            messagebox.showinfo('Select one job','Select exactly one job for a preview.'); return
        job=jobs[0]
        if self._is_busy(job) or self.update_in_progress: return
        event=threading.Event(); job_id=job.job_id
        self.analysis_cancels[job_id]=event
        self.analysis_restore[job_id]=job.status
        job.status=JobStatus.ANALYZING; job.phase='Comparing' if comparison else 'Test frame'
        self._refresh_tree()
        output=Path(self.preview_directory.name)/(job_id+('-compare' if comparison else '-frame')+'.png')
        config=copy.deepcopy(job.config)
        def worker():
            with self.analysis_semaphore:
                try:
                    if event.is_set(): raise ExportCancelled('Preview cancelled.')
                    with HtmlVideoRenderer(log_callback=lambda m:self._ui(self._append_log,m)) as renderer:
                        if comparison:
                            path,probe=renderer.capture_processing_comparison(config,output,cancel_event=event)
                        else:
                            path,probe=renderer.capture_test_frame(config,output,cancel_event=event)
                    self._ui(self._test_frame_finished,job_id,path,probe,'')
                except Exception as exc:
                    self._ui(self._test_frame_finished,job_id,None,None,str(exc))
        self.analysis_futures = [f for f in self.analysis_futures if not f.done()]
        self.analysis_futures.append(self.analysis_pool.submit(worker))

    def _test_frame_finished(self, job_id: str, path: Optional[Path], probe: Any, error: str) -> None:
        event=self.analysis_cancels.pop(job_id,None)
        restore=self.analysis_restore.pop(job_id,JobStatus.QUEUED)
        if event is not None and event.is_set(): return
        job = next((item for item in self.jobs if item.job_id == job_id), None)
        if job:
            job.status=restore
            job.phase = "Ready" if not error else "Test frame failed"
            if probe:
                job.probe = probe
            if error:
                job.error = error
        self._refresh_tree()
        self._update_inspector()
        if error:
            messagebox.showerror("Test frame failed", error)
        elif path:
            try:
                open_path(path)
            except Exception as exc:
                messagebox.showerror("Could not open preview", str(exc))

    def _start_queue(self) -> None:
        if self.running or self.update_in_progress:
            return
        queued = [job for job in self.jobs if job.status in {JobStatus.QUEUED, JobStatus.READY}]
        if not queued:
            messagebox.showinfo("Nothing queued", "Add or requeue at least one job.")
            return
        try:
            workers = max(1, min(8, int(self.workers_var.get())))
        except Exception:
            messagebox.showerror("Invalid worker count", "Workers must be between 1 and 8.")
            return
        workers = min(workers, len(queued))
        if workers > 2 and any(job.config.render.scale >= 2 for job in queued):
            if not messagebox.askyesno(
                "High memory use",
                "More than two concurrent 2× exports can use substantial RAM. Continue?",
            ):
                return

        from media_pipeline import snapshot_for_export
        try:
            snapshots = {job.job_id: snapshot_for_export(job.config, workers) for job in queued}
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Invalid CPU settings", str(exc))
            return

        self.running = True
        self.run_job_ids = {job.job_id for job in queued}
        self.paused = False
        self.stop_requested = False
        self.stop_event.clear()
        self.run_event.set()
        self.active_cancels.clear()
        self.work_queue = queue.Queue()
        for job in queued:
            cancel=threading.Event()
            self.active_cancels[job.job_id]=cancel
            job.phase='Waiting for worker'
            # Snapshot on the main thread. Worker never reads GUI job state.
            self.work_queue.put((job.job_id,snapshots[job.job_id],cancel))
        for _ in range(workers):
            self.work_queue.put(None)

        self.start_button.configure(state="disabled")
        self.pause_button.configure(state="normal", text="Pause")
        self.cancel_button.configure(state="normal")
        self.stop_button.configure(state="normal")
        self.worker_threads = []
        for index in range(workers):
            thread = threading.Thread(
                target=self._worker_loop,
                args=(index + 1,),
                daemon=True,
            )
            self.worker_threads.append(thread)
            thread.start()
        threading.Thread(target=self._watch_workers, daemon=True).start()
        self._append_log(f"Queue started with {workers} worker(s).")

    def _worker_loop(self, worker_number: int) -> None:
        renderer=HtmlVideoRenderer(log_callback=lambda m:self._ui(self._append_log,f'Worker {worker_number}: {m}'))
        try:
            while True:
                item=self.work_queue.get()
                try:
                    if item is None: break
                    job_id, config, cancel_event=item
                    if self.stop_event.is_set() or cancel_event.is_set(): continue
                    while not self.run_event.wait(0.1):
                        if self.stop_event.is_set() or cancel_event.is_set(): break
                    if self.stop_event.is_set() or cancel_event.is_set(): continue
                    self._ui(self._mark_rendering,job_id,worker_number)
                    last_update=0.0
                    def progress(payload):
                        nonlocal last_update
                        now=time.monotonic()
                        if now-last_update>=0.08 or payload.get('fraction')==1.0:
                            last_update=now
                            self._ui(self._apply_progress,job_id,dict(payload))
                    try:
                        result=renderer.render(config,progress_callback=progress,
                                               cancel_event=cancel_event,run_event=self.run_event)
                        self._ui(self._job_done,job_id,result)
                    except ExportCancelled: self._ui(self._job_cancelled,job_id)
                    except Exception as exc: self._ui(self._job_failed,job_id,str(exc))
                finally: self.work_queue.task_done()
        except Exception as exc:
            self._ui(self._append_log,f'Worker {worker_number} stopped: {exc}')
        finally:
            renderer.close()

    def _watch_workers(self) -> None:
        for thread in self.worker_threads:
            thread.join()
        self._ui(self._queue_finished)

    def _mark_rendering(self, job_id: str, worker_number: int) -> None:
        job = next((item for item in self.jobs if item.job_id == job_id), None)
        if job:
            event=self.active_cancels.get(job_id)
            if job.status==JobStatus.CANCELLED or (event is not None and event.is_set()): return
            job.status = JobStatus.RENDERING
            job.phase = f"Worker {worker_number}"
            job.progress = 0.0
            job.error = ""
            job.eta_seconds = None
            job.elapsed_seconds = 0.0
            self._refresh_tree()

    def _apply_progress(self, job_id: str, payload: dict[str, Any]) -> None:
        job = next((item for item in self.jobs if item.job_id == job_id), None)
        if job:
            job.progress = float(payload.get("fraction", 0.0))
            job.phase = str(payload.get("phase", "Rendering"))
            job.elapsed_seconds = float(payload.get("elapsed", 0.0))
            eta = payload.get("eta")
            job.eta_seconds = float(eta) if eta is not None else None
            self._refresh_tree()

    def _job_done(self, job_id: str, result: Any) -> None:
        job = next((item for item in self.jobs if item.job_id == job_id), None)
        if job:
            job.status = JobStatus.DONE
            job.phase = "Done"
            job.progress = 1.0
            job.eta_seconds = 0.0
            job.output_path = str(result.output_path)
            self._append_log(
                f"Finished {job.config.source.display_name} → {result.output_path.name} "
                f"({result.output_width}×{result.output_height}, {result.frame_count} frames)."
            )
            self._refresh_tree()
            self._update_inspector()

    def _job_cancelled(self, job_id: str) -> None:
        job = next((item for item in self.jobs if item.job_id == job_id), None)
        if job:
            job.status = JobStatus.CANCELLED
            job.phase = "Cancelled"
            job.eta_seconds = None
            self._append_log(f"Cancelled {job.config.source.display_name}.")
            self._refresh_tree()

    def _job_failed(self, job_id: str, error: str) -> None:
        job = next((item for item in self.jobs if item.job_id == job_id), None)
        if job:
            job.status = JobStatus.ERROR
            job.phase = "Failed"
            job.error = error
            job.eta_seconds = None
            self._append_log(f"ERROR {job.config.source.display_name}: {error}")
            self._refresh_tree()
            self._update_inspector()

    def _toggle_pause(self) -> None:
        if not self.running:
            return
        self.paused = not self.paused
        if self.paused:
            self.run_event.clear()
            self.pause_button.configure(text="Resume")
            self._append_log("Queue paused. Active workers will pause between frames.")
        else:
            self.run_event.set()
            self.pause_button.configure(text="Pause")
            self._append_log("Queue resumed.")

    def _cancel_selected(self) -> None:
        for job in self._selected_jobs():
            event=self.active_cancels.get(job.job_id) if self.running else None
            analysis=self.analysis_cancels.get(job.job_id)
            if event is not None: event.set()
            if analysis is not None: analysis.set()
            if job.status in {JobStatus.QUEUED,JobStatus.READY,JobStatus.ANALYZING}:
                job.status=JobStatus.CANCELLED; job.phase='Cancelled'
        self._refresh_tree()

    def _stop_queue(self) -> None:
        if not self.running:
            return
        self.stop_requested = True
        self.stop_event.set()
        self.run_event.set()
        for event in list(self.active_cancels.values()):
            event.set()
        self._append_log("Stop requested. Active jobs are being cancelled; pending jobs remain queued.")
        for job in self.jobs:
            if job.status == JobStatus.READY:
                job.status = JobStatus.QUEUED
        self._refresh_tree()

    def _queue_finished(self) -> None:
        for job in self.jobs:
            if job.job_id in self.run_job_ids and job.status in {JobStatus.QUEUED,JobStatus.READY}:
                job.status=JobStatus.QUEUED; job.phase='Queued'
        self.active_cancels.clear()
        self.running = False
        self.paused = False
        self.run_event.set()
        self.start_button.configure(state="normal")
        self.pause_button.configure(state="disabled", text="Pause")
        self.cancel_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self._append_log("Queue finished.")
        self._refresh_tree()

    def _update_queue_summary(self) -> None:
        visible_jobs = (
            [job for job in self.jobs if job.job_id in self.run_job_ids]
            if self.running and self.run_job_ids else self.jobs
        )
        total = len(visible_jobs)
        if not total:
            self.overall_progress["value"] = 0
            self.queue_summary_var.set("No jobs")
            return
        completed = 0.0
        done_count = 0
        active_count = 0
        for job in visible_jobs:
            if job.status in {JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED, JobStatus.SKIPPED}:
                completed += 1.0
            elif job.status == JobStatus.RENDERING:
                completed += job.progress
                active_count += 1
            if job.status == JobStatus.DONE:
                done_count += 1
        percent = completed / total * 100
        self.overall_progress["value"] = percent
        self.queue_summary_var.set(
            f"{done_count}/{total} complete · {active_count} active · {percent:.1f}%"
        )

    def _update_inspector(self) -> None:
        jobs = self._selected_jobs()
        self.inspector.configure(state="normal")
        self.inspector.delete("1.0", "end")
        if len(jobs) != 1:
            self.inspector_title_var.set("Select one job to inspect")
            self.inspector.insert(
                "end",
                "Analyze a source to see its resolved capture target, dimensions, duration, "
                "timeline adapter, content recommendation, warnings, and output settings.\n",
            )
        else:
            job = jobs[0]
            self.inspector_title_var.set(job.config.source.display_name)
            config = job.config
            self.inspector.insert("end", f"SOURCE\n  {config.source.value}\n  Kind: {config.source.kind.value}\n\n")
            self.inspector.insert(
                "end",
                f"JOB SETTINGS\n"
                f"  Recipe: {RECIPES.get(config.recipe_key).label if config.recipe_key in RECIPES else config.recipe_key}\n"
                f"  Capture: {config.capture.mode.value}"
                + (f" ({config.capture.selector})" if config.capture.selector else "")
                + "\n"
                f"  Timeline: {config.timeline.mode.value}\n"
                f"  Manual duration: {config.timeline.manual_duration or 'automatic'}\n"
                f"  Trim: {config.timeline.trim_start}s → {config.timeline.trim_end or 'source end'}\n"
                f"  Holds: {config.timeline.hold_start}s / {config.timeline.hold_end}s\n"
                f"  Scale/FPS: {config.render.scale:g}× / {config.render.fps}\n"
                f"  CPU threads: {config.render.cpu_threads or 'automatic'}\n"
                f"  Codec: {OUTPUT_PROFILES[config.render.output_profile_key].label}\n"
                f"  Processing: {PROCESSING_PRESETS.get(config.render.processing.preset_key, PROCESSING_PRESETS['custom']).label}\n"
                f"  Audio: {config.render.audio.mode.value}\n\n",
            )
            if job.probe:
                probe = job.probe
                self.inspector.insert(
                    "end",
                    f"ANALYSIS\n"
                    f"  Loading: {probe.load_strategy}\n"
                    f"  Target: {probe.target_description} ({probe.target_kind})\n"
                    f"  Frame: {probe.target_frame_url or 'main document'}\n"
                    f"  Geometry: {probe.geometry_mode.value}\n"
                    f"  Source size: {probe.source_width}×{probe.source_height}\n"
                    f"  Output size: {probe.output_width}×{probe.output_height}\n"
                    f"  Duration: {probe.duration_seconds if probe.duration_seconds is not None else 'missing'}\n"
                    f"  Duration source: {probe.duration_source}\n"
                    f"  Timeline adapter: {probe.timeline_mode.value}\n"
                    f"  Synchronous seek: {probe.sync_seek}\n"
                    f"  Content: {probe.content_classification}\n"
                    f"  Recommended processing: {PROCESSING_PRESETS.get(probe.recommended_processing_key, PROCESSING_PRESETS['no_processing']).label}\n",
                )
                explanation = probe.extra.get("analysis_explanation")
                if explanation:
                    self.inspector.insert("end", f"  Why: {explanation}\n")
                if probe.diagnostics:
                    self.inspector.insert("end", "\nDIAGNOSTICS\n")
                    for item in probe.diagnostics:
                        self.inspector.insert("end", f"  [{item.severity.upper()}] {item.message}\n")
            else:
                self.inspector.insert("end", "ANALYSIS\n  Not analyzed yet.\n")
            if job.error:
                self.inspector.insert("end", f"\nLAST ERROR\n  {job.error}\n")
            if job.output_path:
                self.inspector.insert("end", f"\nOUTPUT\n  {job.output_path}\n")
        self.inspector.configure(state="disabled")

    def _show_recipe(self) -> None:
        selection = self.recipe_list.selection()
        if not selection:
            return
        recipe = RECIPES[selection[0]]
        profile = OUTPUT_PROFILES[recipe.output_profile_key]
        processing = PROCESSING_PRESETS[recipe.processing_key]
        self.recipe_help_var.set(
            f"{recipe.description}\n\n"
            f"Capture: {recipe.capture_mode.value}\n"
            f"Timeline: {recipe.timeline_mode.value}\n"
            f"Scale / FPS: {recipe.scale:g}× / {recipe.fps}\n"
            f"Output: {profile.label}\n"
            f"Processing: {processing.label}"
        )

    def _show_processing(self) -> None:
        selection = self.processing_list.selection()
        if not selection:
            return
        preset = PROCESSING_PRESETS[selection[0]]
        self.processing_help_var.set(preset.description)

    def _apply_recipe_to_selected(self) -> None:
        selection = self.recipe_list.selection()
        jobs = self._selected_jobs()
        if not selection or not jobs:
            messagebox.showinfo("Choose jobs", "Select a recipe and one or more queue jobs.")
            return
        key = selection[0]
        for job in jobs:
            if not self._is_busy(job):
                job.config = apply_recipe(job.config, key)
                job.status = JobStatus.QUEUED
                job.probe = None
                job.progress = 0.0
        self.project_dirty = True
        self._refresh_tree()
        self._update_inspector()

    def _set_default_recipe(self) -> None:
        selection = self.recipe_list.selection()
        if selection:
            key = selection[0]
            self.default_recipe_var.set(RECIPES[key].label)
            self.settings.default_recipe_key = key
            self._save_preferences()

    def _apply_processing_to_selected(self) -> None:
        selection = self.processing_list.selection()
        jobs = self._selected_jobs()
        if not selection or not jobs:
            messagebox.showinfo("Choose jobs", "Select a processing preset and one or more queue jobs.")
            return
        key = selection[0]
        for job in jobs:
            if not self._is_busy(job):
                job.config.render.processing = PROCESSING_PRESETS[key].to_config()
                job.status = JobStatus.QUEUED
                job.probe = None
        self.project_dirty = True
        self._refresh_tree()

    def _open_project(self) -> None:
        if not self._confirm_discard_changes():
            return
        path = filedialog.askopenfilename(
            title="Open project",
            filetypes=[("Studio projects", "*.hves.json *.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            document = load_project(Path(path))
        except ProjectError as exc:
            messagebox.showerror("Could not open project", str(exc))
            return
        self.jobs = document.jobs
        self.project_path = Path(path)
        self.project_dirty = False
        self.settings.recent_projects = add_recent(self.settings.recent_projects, path)
        self._refresh_tree()
        self._update_inspector()
        self._append_log(f"Opened project: {path}")

    def _save_project(self) -> bool:
        if self.project_path is None:
            return self._save_project_as()
        try:
            save_project(ProjectDocument(self.jobs, name=self.project_path.stem), self.project_path)
            self.project_dirty = False
            self._append_log(f"Saved project: {self.project_path}")
            return True
        except Exception as exc:
            messagebox.showerror("Could not save project", str(exc))
            return False

    def _save_project_as(self) -> bool:
        path = filedialog.asksaveasfilename(
            title="Save project",
            defaultextension=".hves.json",
            filetypes=[("Studio projects", "*.hves.json"), ("JSON", "*.json")],
        )
        if not path:
            return False
        self.project_path = Path(path)
        self.settings.recent_projects = add_recent(self.settings.recent_projects, path)
        return self._save_project()

    def _confirm_discard_changes(self) -> bool:
        if not self.project_dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved project",
            "Save changes to the current project?",
        )
        if answer is None:
            return False
        if answer:
            return self._save_project()
        return True

    def _check_environment_async(self) -> None:
        self.environment_var.set("Checking dependencies…")

        def worker() -> None:
            result = check_environment()
            self._ui(self._environment_finished, result)

        self._start_background(worker)

    def _environment_finished(self, result: dict[str, Any]) -> None:
        self.environment = result
        if result.get("ffmpeg_ok") and result.get("chromium_ok"):
            unavailable = [
                OUTPUT_PROFILES[key].label
                for key, available in result.get("profiles", {}).items()
                if not available and key in OUTPUT_PROFILES
            ]
            self.environment_var.set(
                "Ready" if not unavailable else "Ready · some codecs unavailable"
            )
            self._append_log("Dependency diagnostics passed.")
            if unavailable:
                self._append_log("Unavailable codecs: " + ", ".join(unavailable))
        else:
            self.environment_var.set("Setup required")
            for message in result.get("messages", []):
                self._append_log(message)
        if self.update_startup_var.get():
            self.after(500, lambda: self._check_updates_async(True))

    def _browse_default_output(self) -> None:
        folder = filedialog.askdirectory(title="Choose default output folder")
        if folder:
            self.default_output_dir_var.set(folder)

    def _open_selected_output(self) -> None:
        jobs = self._selected_jobs()
        if len(jobs) != 1 or not jobs[0].output_path:
            messagebox.showinfo("No output selected", "Select one completed job with an output file.")
            return
        path = Path(jobs[0].output_path)
        if not path.exists():
            messagebox.showerror("Output missing", f"The output file no longer exists:\n{path}")
            return
        try:
            open_path(path)
        except Exception as exc:
            messagebox.showerror("Could not open output", str(exc))

    def _reveal_selected_output(self) -> None:
        jobs = self._selected_jobs()
        path = None
        if len(jobs) == 1 and jobs[0].output_path:
            path = Path(jobs[0].output_path).parent
        elif len(jobs) == 1 and jobs[0].config.source.kind == SourceKind.FILE:
            path = Path(jobs[0].config.source.value).parent
        if path is None or not path.exists():
            messagebox.showinfo("No folder", "Select a job with an available source or output folder.")
            return
        try:
            open_path(path)
        except Exception as exc:
            messagebox.showerror("Could not open folder", str(exc))

    def _detect_origin(self) -> None:
        repo = detect_git_origin(Path(__file__).resolve().parent)
        if repo:
            self.update_repo_var.set(repo)
        else:
            messagebox.showinfo("No Git origin", "No GitHub origin remote was detected.")

    def _check_updates_async(self, quiet: bool) -> None:
        if self.checking_updates or self.update_in_progress: return
        if self.running or self.analysis_cancels:
            if not quiet: messagebox.showinfo('App is busy','Finish export/analysis before checking for an update.')
            return
        raw = self.update_repo_var.get().strip()
        if not raw:
            if quiet:
                return
            messagebox.showinfo(
                "GitHub repository required",
                "Enter owner/repository in Settings & Updates first.",
            )
            return
        try:
            repo = normalize_repo(raw)
        except ValueError as exc:
            if not quiet:
                messagebox.showerror("Invalid repository", str(exc))
            return
        self.checking_updates=True
        token = os.environ.get("HTML_MP4_GITHUB_TOKEN", "")
        if not quiet:
            self._append_log(f"Checking GitHub releases for {repo}…")

        def worker() -> None:
            try:
                release = fetch_latest_release(repo, token)
                self._ui(self._update_check_finished, repo, release, quiet, token, "")
            except Exception as exc:
                self._ui(self._update_check_finished, repo, None, quiet, token, str(exc))

        self._start_background(worker)

    def _update_check_finished(self, repo: str, release: Any, quiet: bool, token: str, error: str) -> None:
        self.checking_updates=False
        if self.running or self.analysis_cancels:
            self._append_log('Update check completed while busy. Check again after the queue finishes.'); return
        if error:
            if not quiet:
                messagebox.showerror("Update check failed", error)
            return
        if not is_newer_version(release.version, APP_VERSION):
            if not quiet:
                messagebox.showinfo("Up to date", f"{APP_NAME} {APP_VERSION} is current.")
            return
        notes = release.notes.strip()
        prompt = f"Version {release.version} is available. Download and install it?"
        if notes:
            prompt += "\n\n" + notes[:1500]
        if not messagebox.askyesno("Update available", prompt):
            return
        if not self._confirm_discard_changes(): return
        self.update_in_progress=True
        self._append_log(f"Preparing update {release.version}…")

        def worker() -> None:
            try:
                source_root = prepare_release_update(repo, release, token)
                self._ui(self._update_ready_to_restart,source_root,'')
            except Exception as exc:
                self._ui(self._update_ready_to_restart,None,str(exc))

        self._start_background(worker)

    def _update_ready_to_restart(self, source_root: Optional[Path], error: str = '') -> None:
        from updater import cleanup_staging
        self.update_in_progress=False
        if error:
            messagebox.showerror('Update failed',error); return
        if source_root is None: return
        if self.running or self.analysis_cancels or not self._confirm_discard_changes():
            cleanup_staging(source_root); return
        try:
            self._save_preferences()
            launch_update_helper(source_root,Path(__file__).resolve().parent)
        except Exception as exc:
            cleanup_staging(source_root)
            messagebox.showerror('Update not installed',str(exc)); return
        self.destroy()

    def _open_documentation(self) -> None:
        path = Path(__file__).resolve().parent / "README.md"
        try:
            open_path(path)
        except Exception as exc:
            messagebox.showerror("Could not open documentation", str(exc))

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About",
            f"{APP_NAME} {APP_VERSION}\n\n"
            "Deterministic HTML animation, element, viewport, media, and web-page capture "
            "with queueing, source diagnostics, processing presets, editing/delivery codecs, "
            "external audio muxing, projects, CLI automation, and verified GitHub updates.",
        )

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _ui(self, callback: Callable[..., Any], *args: Any) -> None:
        # Workers must not call ANY Tk API, including after() or Variable.get().
        if not self.closed_event.is_set():
            self.ui_events.put((callback,args))

    def _drain_ui(self) -> None:
        if self.closed_event.is_set(): return
        for _ in range(200):
            if self.closed_event.is_set(): break
            try: callback,args=self.ui_events.get_nowait()
            except queue.Empty: break
            try: callback(*args)
            except Exception as exc:
                self._append_log(f'UI action failed: {type(exc).__name__}: {exc}')
        if not self.closed_event.is_set(): self.after(40,self._drain_ui)

    def _start_background(self, target: Callable[[], None]) -> None:
        self.background_threads=[t for t in self.background_threads if t.is_alive()]
        thread=threading.Thread(target=target,daemon=True)
        self.background_threads.append(thread)
        thread.start()

    def _is_busy(self, job: QueueJob) -> bool:
        return ((self.running and job.job_id in self.run_job_ids) or
                job.job_id in self.analysis_cancels)

    def destroy(self) -> None:
        widgets = widget_tree(self)
        self.closed_event.set()
        # Cancel Tcl callbacks before Tk removes their registered Python commands.
        for timer in self.tk.splitlist(self.tk.call('after', 'info')):
            try: self.after_cancel(timer)
            except Exception: pass
        self.analysis_pool.shutdown(wait=False, cancel_futures=True)
        self.stop_event.set()
        self.run_event.set()
        for event in list(self.active_cancels.values())+list(self.analysis_cancels.values()): event.set()
        release_tk_variables(self)
        try: self.preview_directory.cleanup()
        except OSError: pass
        super().destroy()
        # Closed widgets retained by Python cycles must not keep a Tcl interpreter
        # alive until a worker's cyclic-GC run. Release these handles here instead.
        for widget in widgets:
            widget.tk = None
        while True:
            try: self.ui_events.get_nowait()
            except queue.Empty: break

    def _save_preferences(self) -> None:
        try:
            self.settings.workers = max(1, min(8, int(self.workers_var.get())))
        except Exception:
            pass
        self.settings.default_recipe_key = self._default_recipe_key()
        self.settings.auto_analyze_on_add = bool(self.auto_analyze_var.get())
        self.settings.default_output_directory = self.default_output_dir_var.get().strip()
        self.settings.save_next_to_source = bool(self.default_next_to_source_var.get())
        self.settings.overwrite = bool(self.default_overwrite_var.get())
        self.settings.update_repo = self.update_repo_var.get().strip()
        self.settings.check_updates_at_startup = bool(self.update_startup_var.get())
        self.settings.window_geometry = self.geometry()
        try: save_settings(self.settings)
        except OSError as exc:
            self._append_log(f'Could not save preferences: {exc}')

    def _on_close(self) -> None:
        if self.update_in_progress:
            messagebox.showinfo('Update download active','Finish the update download before closing.'); return
        if not self._confirm_discard_changes(): return
        if self.running or self.analysis_cancels:
            if not messagebox.askyesno('Work in progress','Cancel current work and close?'): return
        if self.running: self._stop_queue()
        for event in self.analysis_cancels.values(): event.set()
        self._wait_close_after_workers()

    def _wait_close_after_workers(self) -> None:
        if (any(t.is_alive() for t in self.worker_threads + self.background_threads)
                or any(not f.done() for f in self.analysis_futures)):
            self.after(150,self._wait_close_after_workers); return
        self._save_preferences()
        self.destroy()


def main() -> None:
    app=StudioApp()
    app.mainloop()


if __name__ == '__main__':
    main()
