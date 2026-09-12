from __future__ import annotations

import dataclasses
import enum
import math
import re
import string
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# Shared defaults for the Python API, CLI and desktop new-job recipe.
DEFAULT_RECIPE_KEY = "social_delivery"
DEFAULT_SHARPEN_STRENGTH = 0.28
MAX_CPU_THREADS = 256
MAX_CAPTURE_WORKERS = 16


def strict_int(value: Any) -> int:
    if isinstance(value, bool): raise ValueError('Boolean is not an integer setting.')
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError('Expected a finite integer.')
    return int(number)


def strict_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError('Boolean is not a numeric setting.')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Expected a finite number.')
    return number


def strict_bool(value: Any) -> bool:
    if not isinstance(value, bool): raise ValueError('Expected true or false, not a string/number.')
    return value


class SourceKind(str, enum.Enum):
    FILE = "file"
    URL = "url"


class LoadStrategy(str, enum.Enum):
    AUTO = "auto"
    LOOPBACK_HTTP = "loopback_http"
    FILE_URL = "file_url"
    EMBEDDED = "embedded"
    REMOTE_URL = "remote_url"


class CaptureMode(str, enum.Enum):
    AUTO = "auto"
    MARKER = "marker"
    SELECTOR = "selector"
    VIEWPORT = "viewport"
    FULL_PAGE = "full_page"


class TimelineMode(str, enum.Enum):
    AUTO = "auto"
    OM_EVENT = "om_event"
    CUSTOM_EVENT = "custom_event"
    JAVASCRIPT_FUNCTION = "javascript_function"
    WEB_ANIMATIONS = "web_animations"
    MEDIA = "media"
    BROWSER_CLOCK = "browser_clock"
    REALTIME = "realtime"
    STATIC = "static"


class GeometryMode(str, enum.Enum):
    AUTO = "auto"
    LOCK_INTRINSIC = "lock_intrinsic"
    PRESERVE_LAYOUT = "preserve_layout"


class AudioMode(str, enum.Enum):
    NONE = "none"
    TRIM = "trim"
    LOOP = "loop"


class JobStatus(str, enum.Enum):
    QUEUED = "Queued"
    ANALYZING = "Analyzing"
    READY = "Ready"
    RENDERING = "Rendering"
    PAUSED = "Paused"
    DONE = "Done"
    ERROR = "Error"
    CANCELLED = "Cancelled"
    SKIPPED = "Skipped"


@dataclass
class SourceSpec:
    value: str
    kind: SourceKind = SourceKind.FILE
    load_strategy: LoadStrategy = LoadStrategy.AUTO

    @property
    def display_name(self) -> str:
        if self.kind == SourceKind.FILE:
            return Path(self.value).name
        return self.value

    def validate(self) -> None:
        if not self.value.strip():
            raise ValueError("Source cannot be empty.")
        if not isinstance(self.kind, SourceKind) or not isinstance(self.load_strategy, LoadStrategy):
            raise ValueError('Invalid source kind/loading strategy.')
        if self.kind == SourceKind.URL:
            parsed = urllib.parse.urlsplit(self.value)
            if parsed.scheme not in {'http','https'} or not parsed.hostname:
                raise ValueError('URL must have an http(s) scheme and a hostname.')
            if parsed.username or parsed.password:
                raise ValueError('Credential-bearing URLs are not supported.')
        if self.kind == SourceKind.FILE:
            path = Path(self.value).expanduser()
            if path.suffix.lower() not in {".html", ".htm"}:
                raise ValueError("Local sources must be .html or .htm files.")


@dataclass
class CaptureConfig:
    mode: CaptureMode = CaptureMode.AUTO
    selector: str = ""
    selector_index: int = 0
    viewport_width: int = 1080
    viewport_height: int = 1920
    manual_width: Optional[int] = None
    manual_height: Optional[int] = None
    geometry_mode: GeometryMode = GeometryMode.AUTO
    transparent_background: bool = False
    strip_outer_shadow: bool = False

    def validate(self) -> None:
        if self.mode == CaptureMode.SELECTOR and not self.selector.strip():
            raise ValueError("A CSS selector is required for Selector capture mode.")
        for value in (self.viewport_width, self.viewport_height, self.selector_index):
            strict_int(value)
        for value in (self.manual_width, self.manual_height):
            if value is not None: strict_int(value)
        if self.viewport_width > 32768 or self.viewport_height > 32768:
            raise ValueError('Viewport dimension exceeds the 32768-pixel safety limit.')
        if self.selector_index < 0:
            raise ValueError("Selector index cannot be negative.")
        if self.viewport_width < 1 or self.viewport_height < 1:
            raise ValueError("Viewport dimensions must be positive.")
        manual = (self.manual_width is not None, self.manual_height is not None)
        if manual[0] != manual[1]:
            raise ValueError("Manual width and height must be set together.")
        if self.manual_width is not None and self.manual_width < 1:
            raise ValueError("Manual width must be positive.")
        if self.manual_height is not None and self.manual_height < 1:
            raise ValueError("Manual height must be positive.")


@dataclass
class TimelineConfig:
    mode: TimelineMode = TimelineMode.AUTO
    manual_duration: Optional[float] = None
    trim_start: float = 0.0
    trim_end: Optional[float] = None
    hold_start: float = 0.0
    hold_end: float = 0.0
    custom_event_name: str = "video-export-seek"
    javascript_function: str = "window.seekTo"
    realtime_start_delay: float = 0.0

    def validate(self) -> None:
        for label, value in (
            ("manual duration", self.manual_duration),
            ("trim start", self.trim_start),
            ("trim end", self.trim_end),
            ("hold start", self.hold_start),
            ("hold end", self.hold_end),
            ("realtime start delay", self.realtime_start_delay),
        ):
            if value is not None and (not math.isfinite(float(value)) or float(value) < 0):
                raise ValueError(f"{label.capitalize()} must be a finite non-negative number.")
        if self.manual_duration is not None and self.manual_duration <= 0:
            raise ValueError("Manual duration must be greater than zero.")
        if self.trim_end is not None and self.trim_end <= self.trim_start:
            raise ValueError("Trim end must be greater than trim start.")
        if self.mode == TimelineMode.CUSTOM_EVENT and not self.custom_event_name.strip():
            raise ValueError("A custom event name is required.")
        if self.mode == TimelineMode.JAVASCRIPT_FUNCTION and not self.javascript_function.strip():
            raise ValueError("A JavaScript function path is required.")


@dataclass
class ProcessingConfig:
    preset_key: str = "social_compensation"
    sharpen_method: str = "cas"
    sharpen_strength: float = DEFAULT_SHARPEN_STRENGTH
    contrast: float = 1.0
    saturation: float = 1.0
    brightness: float = 0.0
    deband: bool = False

    def validate(self) -> None:
        if self.sharpen_method not in {"none", "cas", "unsharp"}:
            raise ValueError("Sharpen method must be none, cas, or unsharp.")
        if not 0 <= self.sharpen_strength <= 1:
            raise ValueError("Sharpen strength must be between 0 and 1.")
        if not 0.5 <= self.contrast <= 2.0:
            raise ValueError("Contrast must be between 0.5 and 2.0.")
        if not 0.0 <= self.saturation <= 3.0:
            raise ValueError("Saturation must be between 0 and 3.0.")
        if not -1.0 <= self.brightness <= 1.0:
            raise ValueError("Brightness must be between -1 and 1.")


@dataclass
class AudioConfig:
    path: str = ""
    mode: AudioMode = AudioMode.NONE
    volume: float = 1.0
    offset_seconds: float = 0.0
    fade_in_seconds: float = 0.0
    fade_out_seconds: float = 0.0

    def validate(self) -> None:
        if self.mode != AudioMode.NONE and not self.path.strip():
            raise ValueError("Choose an audio file or disable audio.")
        if self.volume < 0 or not math.isfinite(self.volume):
            raise ValueError("Audio volume must be a finite non-negative number.")
        for label, value in (
            ("Audio offset", self.offset_seconds),
            ("Audio fade-in", self.fade_in_seconds),
            ("Audio fade-out", self.fade_out_seconds),
        ):
            if value < 0 or not math.isfinite(value):
                raise ValueError(f"{label} must be a finite non-negative number.")


@dataclass
class RenderConfig:
    scale: float = 1.0
    fps: int = 60
    output_profile_key: str = "h264_420_mp4"
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    overwrite: bool = False
    output_directory: str = ""
    save_next_to_source: bool = True
    filename_template: str = "{stem}_{scale}x_{fps}fps_{profile}_{processing}.{ext}"
    cpu_threads: int = 0  # 0: automatic; otherwise encoder threads per export.
    capture_workers: int = 0  # Auto requires an author-declared parallel-safe source.
    frame_buffer_mb: int = 256  # Buffered PNG payload budget, not total browser RAM.
    fast_capture: bool = True

    def validate(self, for_export: bool = True) -> None:
        from presets import OUTPUT_PROFILES, PROCESSING_PRESETS
        if self.output_profile_key not in OUTPUT_PROFILES:
            raise ValueError('Unknown output profile: ' + self.output_profile_key)
        if self.processing.preset_key not in PROCESSING_PRESETS:
            raise ValueError('Unknown processing preset: ' + self.processing.preset_key)
        strict_int(self.fps)
        if not 0 <= strict_int(self.capture_workers) <= MAX_CAPTURE_WORKERS:
            raise ValueError(f"Capture workers must be between 0 (automatic) and {MAX_CAPTURE_WORKERS}.")
        if not 16 <= strict_int(self.frame_buffer_mb) <= 4096:
            raise ValueError("Frame buffer must be between 16 and 4096 MiB.")
        strict_bool(self.fast_capture)
        if not 0 <= strict_int(self.cpu_threads) <= MAX_CPU_THREADS:
            raise ValueError(f"CPU threads must be between 0 (automatic) and {MAX_CPU_THREADS}.")
        if isinstance(self.scale, bool): raise ValueError('Scale must be a number, not a boolean.')
        if not math.isfinite(float(self.scale)) or not 0.25 <= float(self.scale) <= 4.0:
            raise ValueError("Scale must be between 0.25× and 4×.")
        if not 1 <= int(self.fps) <= 240:
            raise ValueError("FPS must be between 1 and 240.")
        self.processing.validate()
        self.audio.validate()
        if for_export and not self.save_next_to_source and not self.output_directory.strip():
            raise ValueError("Choose an output directory.")
        fields = {'stem','ext','scale','fps','profile','processing'}
        for _, name, spec, conversion in string.Formatter().parse(self.filename_template):
            if name is not None and (name not in fields or spec or conversion):
                raise ValueError('Filename template contains an unsupported field or format.')
        if re.search(r'[\\/:*?"<>|\x00-\x1f]', self.filename_template):
            raise ValueError('Filename template must be a filename, with no path or reserved characters.')
        required = {"{stem}", "{ext}"}
        missing = [token for token in required if token not in self.filename_template]
        if missing:
            raise ValueError("Filename template must contain {stem} and {ext}.")


@dataclass
class JobConfig:
    source: SourceSpec
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    timeline: TimelineConfig = field(default_factory=TimelineConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    recipe_key: str = DEFAULT_RECIPE_KEY

    def validate(self, for_export: bool = True) -> None:
        self.source.validate()
        self.capture.validate()
        self.timeline.validate()
        self.render.validate(for_export=for_export)


@dataclass
class Diagnostic:
    severity: str
    code: str
    message: str


@dataclass
class ProbeResult:
    source: SourceSpec
    load_strategy: str
    target_description: str
    target_kind: str
    target_selector: str
    target_frame_url: str
    timeline_mode: TimelineMode
    duration_seconds: Optional[float]
    duration_source: str
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    geometry_mode: GeometryMode
    sync_seek: bool = False
    recommended_processing_key: str = "no_processing"
    content_classification: str = "unknown"
    diagnostics: list[Diagnostic] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)


@dataclass
class ExportResult:
    output_path: Path
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    source_duration_seconds: float
    output_duration_seconds: float
    frame_count: int
    fps: int
    scale: float
    output_profile_key: str
    processing_key: str
    load_strategy: str
    timeline_mode: TimelineMode
    duration_source: str
    content_classification: str


@dataclass
class QueueJob:
    job_id: str
    config: JobConfig
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    phase: str = "Queued"
    output_path: str = ""
    error: str = ""
    elapsed_seconds: float = 0.0
    eta_seconds: Optional[float] = None
    probe: Optional[ProbeResult] = None


def dataclass_to_dict(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: dataclass_to_dict(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {str(key): dataclass_to_dict(item) for key, item in value.items()}
    return value


def _enum(enum_type: type[enum.Enum], value: Any, default: enum.Enum) -> enum.Enum:
    if value is None:
        return default
    try:
        return enum_type(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'Invalid {enum_type.__name__}: {value!r}') from exc


def processing_from_dict(data: dict[str, Any]) -> ProcessingConfig:
    base = ProcessingConfig()
    return ProcessingConfig(
        preset_key=str(data.get("preset_key", base.preset_key)),
        sharpen_method=str(data.get("sharpen_method", base.sharpen_method)),
        sharpen_strength=strict_float(data.get("sharpen_strength", base.sharpen_strength)),
        contrast=strict_float(data.get("contrast", base.contrast)),
        saturation=strict_float(data.get("saturation", base.saturation)),
        brightness=strict_float(data.get("brightness", base.brightness)),
        deband=strict_bool(data.get("deband", base.deband)),
    )


def audio_from_dict(data: dict[str, Any]) -> AudioConfig:
    base = AudioConfig()
    return AudioConfig(
        path=str(data.get("path", base.path)),
        mode=_enum(AudioMode, data.get("mode"), base.mode),  # type: ignore[arg-type]
        volume=strict_float(data.get("volume", base.volume)),
        offset_seconds=strict_float(data.get("offset_seconds", base.offset_seconds)),
        fade_in_seconds=strict_float(data.get("fade_in_seconds", base.fade_in_seconds)),
        fade_out_seconds=strict_float(data.get("fade_out_seconds", base.fade_out_seconds)),
    )


def job_config_from_dict(data: dict[str, Any]) -> JobConfig:
    source_data = data.get("source") or {}
    capture_data = data.get("capture") or {}
    timeline_data = data.get("timeline") or {}
    render_data = data.get("render") or {}

    source_base = SourceSpec(value="")
    capture_base = CaptureConfig()
    timeline_base = TimelineConfig()
    render_base = RenderConfig()

    source = SourceSpec(
        value=str(source_data.get("value", "")),
        kind=_enum(SourceKind, source_data.get("kind"), source_base.kind),  # type: ignore[arg-type]
        load_strategy=_enum(
            LoadStrategy, source_data.get("load_strategy"), source_base.load_strategy
        ),  # type: ignore[arg-type]
    )
    capture = CaptureConfig(
        mode=_enum(CaptureMode, capture_data.get("mode"), capture_base.mode),  # type: ignore[arg-type]
        selector=str(capture_data.get("selector", capture_base.selector)),
        selector_index=strict_int(capture_data.get("selector_index", capture_base.selector_index)),
        viewport_width=strict_int(capture_data.get("viewport_width", capture_base.viewport_width)),
        viewport_height=strict_int(capture_data.get("viewport_height", capture_base.viewport_height)),
        manual_width=(
            strict_int(capture_data["manual_width"])
            if capture_data.get("manual_width") not in (None, "")
            else None
        ),
        manual_height=(
            strict_int(capture_data["manual_height"])
            if capture_data.get("manual_height") not in (None, "")
            else None
        ),
        geometry_mode=_enum(
            GeometryMode, capture_data.get("geometry_mode"), capture_base.geometry_mode
        ),  # type: ignore[arg-type]
        transparent_background=strict_bool(
            capture_data.get("transparent_background", capture_base.transparent_background)
        ),
        strip_outer_shadow=strict_bool(
            capture_data.get("strip_outer_shadow", capture_base.strip_outer_shadow)
        ),
    )
    timeline = TimelineConfig(
        mode=_enum(TimelineMode, timeline_data.get("mode"), timeline_base.mode),  # type: ignore[arg-type]
        manual_duration=(
            strict_float(timeline_data["manual_duration"])
            if timeline_data.get("manual_duration") not in (None, "")
            else None
        ),
        trim_start=strict_float(timeline_data.get("trim_start", timeline_base.trim_start)),
        trim_end=(
            strict_float(timeline_data["trim_end"])
            if timeline_data.get("trim_end") not in (None, "")
            else None
        ),
        hold_start=strict_float(timeline_data.get("hold_start", timeline_base.hold_start)),
        hold_end=strict_float(timeline_data.get("hold_end", timeline_base.hold_end)),
        custom_event_name=str(
            timeline_data.get("custom_event_name", timeline_base.custom_event_name)
        ),
        javascript_function=str(
            timeline_data.get("javascript_function", timeline_base.javascript_function)
        ),
        realtime_start_delay=strict_float(
            timeline_data.get("realtime_start_delay", timeline_base.realtime_start_delay)
        ),
    )
    render = RenderConfig(
        scale=strict_float(render_data.get("scale", render_base.scale)),
        fps=strict_int(render_data.get("fps", render_base.fps)),
        cpu_threads=strict_int(render_data.get("cpu_threads", render_base.cpu_threads)),
        capture_workers=strict_int(render_data.get("capture_workers", render_base.capture_workers)),
        frame_buffer_mb=strict_int(render_data.get("frame_buffer_mb", render_base.frame_buffer_mb)),
        fast_capture=strict_bool(render_data.get("fast_capture", render_base.fast_capture)),
        output_profile_key=str(
            render_data.get("output_profile_key", render_base.output_profile_key)
        ),
        processing=processing_from_dict(render_data.get("processing") or {}),
        audio=audio_from_dict(render_data.get("audio") or {}),
        overwrite=strict_bool(render_data.get("overwrite", render_base.overwrite)),
        output_directory=str(
            render_data.get("output_directory", render_base.output_directory)
        ),
        save_next_to_source=strict_bool(
            render_data.get("save_next_to_source", render_base.save_next_to_source)
        ),
        filename_template=str(
            render_data.get("filename_template", render_base.filename_template)
        ),
    )
    return JobConfig(
        source=source,
        capture=capture,
        timeline=timeline,
        render=render,
        recipe_key=str(data.get("recipe_key", DEFAULT_RECIPE_KEY)),
    )
