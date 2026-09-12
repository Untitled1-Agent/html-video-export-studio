from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from models import (
    DEFAULT_SHARPEN_STRENGTH,
    AudioConfig,
    CaptureConfig,
    CaptureMode,
    GeometryMode,
    JobConfig,
    LoadStrategy,
    ProcessingConfig,
    RenderConfig,
    SourceKind,
    SourceSpec,
    TimelineConfig,
    TimelineMode,
)


@dataclass(frozen=True)
class OutputProfile:
    key: str
    label: str
    description: str
    extension: str
    video_encoder: str
    video_args: tuple[str, ...]
    audio_codec: str
    audio_args: tuple[str, ...]
    requires_even_dimensions: bool = False
    supports_alpha: bool = False
    archival: bool = False


# Capture geometry is validated by the renderer. Disable FFmpeg's implicit
# output scaler: on FFmpeg 6.1 an RGB/RGBA PNG transition can insert it during
# graph reinitialization and move RGB-to-YUV conversion to its default matrix.
# Only media_pipeline.color_pipeline should control that conversion.
OUTPUT_PROFILES: dict[str, OutputProfile] = {
    "lossless_rgb_mp4": OutputProfile(
        key="lossless_rgb_mp4",
        label="Master — Lossless RGB MP4",
        description=(
            "Pixel-lossless RGB H.264 master (libx264rgb, CRF 0, 4:4:4). "
            "Best fidelity and compact compared with image sequences, but limited "
            "hardware-player compatibility. Not for TikTok uploads or reliable mobile "
            "playback; use Social delivery for those destinations."
        ),
        extension="mp4",
        video_encoder="libx264rgb",
        video_args=(
            "-noautoscale",
            "-c:v", "libx264rgb",
            "-x264-params", "colorprim=bt709:transfer=iec61966-2-1:colormatrix=gbr:fullrange=on",
            "-crf", "0",
            "-preset", "medium",
            "-pix_fmt", "rgb24",
            "-color_range", "pc",
            "-colorspace", "rgb",
            "-color_primaries", "bt709",
            "-color_trc", "iec61966-2-1",
        ),
        audio_codec="aac",
        audio_args=("-c:a", "aac", "-b:a", "320k"),
        archival=True,
    ),
    "prores_4444_mov": OutputProfile(
        key="prores_4444_mov",
        label="Editing Master — ProRes 4444 MOV",
        description=(
            "10-bit 4:4:4 ProRes editing master. Excellent interchange with NLEs; "
            "much larger than H.264. The current screenshot path is opaque RGB, so "
            "the alpha channel is filled unless transparent capture is used."
        ),
        extension="mov",
        video_encoder="prores_ks",
        video_args=(
            "-noautoscale",
            "-c:v", "prores_ks",
            "-profile:v", "4",
            "-pix_fmt", "yuva444p10le",
            "-vendor", "apl0",
            "-color_primaries", "bt709",
            "-color_trc", "iec61966-2-1",
            "-colorspace", "bt709",
        ),
        audio_codec="pcm_s24le",
        audio_args=("-c:a", "pcm_s24le"),
        archival=True,
        supports_alpha=True,
    ),
    "prores_hq_mov": OutputProfile(
        key="prores_hq_mov",
        label="Editing Master — ProRes 422 HQ MOV",
        description=(
            "10-bit ProRes 422 HQ for editing workflows. Broad NLE support and "
            "high quality, with chroma subsampling by design."
        ),
        extension="mov",
        video_encoder="prores_ks",
        video_args=(
            "-noautoscale",
            "-c:v", "prores_ks",
            "-profile:v", "3",
            "-pix_fmt", "yuv422p10le",
            "-vendor", "apl0",
            "-color_primaries", "bt709",
            "-color_trc", "iec61966-2-1",
            "-colorspace", "bt709",
        ),
        audio_codec="pcm_s24le",
        audio_args=("-c:a", "pcm_s24le"),
        requires_even_dimensions=True,
        archival=True,
    ),
    "h264_444_mp4": OutputProfile(
        key="h264_444_mp4",
        label="High Quality — H.264 4:4:4 MP4",
        description=(
            "Very high-quality H.264 with full chroma resolution. Smaller than the "
            "lossless RGB master, but not mathematically lossless and less compatible "
            "than ordinary 4:2:0 H.264."
        ),
        extension="mp4",
        video_encoder="libx264",
        video_args=(
            "-noautoscale",
            "-c:v", "libx264",
            "-x264-params", "colorprim=bt709:transfer=iec61966-2-1:colormatrix=bt709:fullrange=off",
            "-crf", "8",
            "-preset", "slow",
            "-pix_fmt", "yuv444p",
            "-profile:v", "high444",
            "-color_range", "tv",
            "-color_primaries", "bt709",
            "-color_trc", "iec61966-2-1",
            "-colorspace", "bt709",
        ),
        audio_codec="aac",
        audio_args=("-c:a", "aac", "-b:a", "256k"),
    ),
    "h264_420_mp4": OutputProfile(
        key="h264_420_mp4",
        label="Delivery — H.264 4:2:0 MP4",
        description=(
            "8-bit H.264 Main at high-quality CRF 8 for phones, VLC, browsers, and social uploads. "
            "4:2:0 chroma subsampling may soften saturated text and fine UI edges."
        ),
        extension="mp4",
        video_encoder="libx264",
        video_args=(
            "-noautoscale",
            "-c:v", "libx264",
            "-x264-params", "colorprim=bt709:transfer=iec61966-2-1:colormatrix=bt709:fullrange=off:open-gop=0",
            "-crf", "8",
            "-preset", "slow",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-tag:v", "avc1",
            "-refs", "3",
            "-bf", "2",
            "-g", "120",
            "-maxrate", "20M",
            "-bufsize", "40M",
            "-color_range", "tv",
            "-color_primaries", "bt709",
            "-color_trc", "iec61966-2-1",
            "-colorspace", "bt709",
        ),
        audio_codec="aac",
        audio_args=("-c:a", "aac", "-profile:a", "aac_low", "-b:a", "256k", "-ar", "48000", "-ac", "2"),
        requires_even_dimensions=True,
    ),
    "h265_420_mp4": OutputProfile(
        key="h265_420_mp4",
        label="High Efficiency — H.265/HEVC 4:2:0 MP4",
        description=(
            "High-quality HEVC/H.265 Main 8-bit delivery using CRF 10 and hvc1 sample entries. "
            "Usually more efficient than H.264 at comparable fidelity, but playback and social-upload "
            "support is less universal; Social delivery intentionally remains H.264 by default."
        ),
        extension="mp4",
        video_encoder="libx265",
        video_args=(
            "-noautoscale",
            "-c:v", "libx265",
            "-x265-params", "colorprim=bt709:transfer=iec61966-2-1:colormatrix=bt709:range=limited:open-gop=0:log-level=error",
            "-crf", "10",
            "-preset", "slow",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-tag:v", "hvc1",
            "-g", "120",
            "-maxrate", "20M",
            "-bufsize", "40M",
            "-color_range", "tv",
            "-color_primaries", "bt709",
            "-color_trc", "iec61966-2-1",
            "-colorspace", "bt709",
        ),
        audio_codec="aac",
        audio_args=("-c:a", "aac", "-profile:a", "aac_low", "-b:a", "256k", "-ar", "48000", "-ac", "2"),
        requires_even_dimensions=True,
    ),
    "vp9_webm": OutputProfile(
        key="vp9_webm",
        label="Web — VP9 4:4:4 WebM",
        description=(
            "High-quality open WebM output with full chroma. Useful for web delivery; "
            "encoding is slower and editing support varies."
        ),
        extension="webm",
        video_encoder="libvpx-vp9",
        video_args=(
            "-noautoscale",
            "-c:v", "libvpx-vp9",
            "-crf", "12",
            "-b:v", "0",
            "-row-mt", "1",
            "-pix_fmt", "yuv444p",
            "-color_range", "tv",
            "-color_primaries", "bt709",
            "-color_trc", "iec61966-2-1",
            "-colorspace", "bt709",
        ),
        audio_codec="libopus",
        audio_args=("-c:a", "libopus", "-b:a", "192k"),
    ),
}

OUTPUT_PROFILE_LABEL_TO_KEY = {profile.label: key for key, profile in OUTPUT_PROFILES.items()}


@dataclass(frozen=True)
class ProcessingPreset:
    key: str
    label: str
    description: str
    sharpen_method: str = "none"
    sharpen_strength: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    brightness: float = 0.0
    deband: bool = False
    auto: bool = False

    def to_config(self) -> ProcessingConfig:
        return ProcessingConfig(
            preset_key=self.key,
            sharpen_method=self.sharpen_method,
            sharpen_strength=self.sharpen_strength,
            contrast=self.contrast,
            saturation=self.saturation,
            brightness=self.brightness,
            deband=self.deband,
        )


PROCESSING_PRESETS: dict[str, ProcessingPreset] = {
    "auto_content_aware": ProcessingPreset(
        key="auto_content_aware",
        label="Auto — Content-aware clarity",
        description=(
            "Samples the composition and applies conservative edge recovery to UI/vector "
            "art, no processing for mixed/uncertain or photographic content."
        ),
        auto=True,
    ),
    "no_processing": ProcessingPreset(
        key="no_processing",
        label="No processing — Exact captured pixels",
        description="No enhancement filters. RGB masters retain captured pixels; YUV outputs still require color conversion.",
    ),
    "ui_subtle": ProcessingPreset(
        key="ui_subtle",
        label="UI / Text — Subtle clarity",
        description="Conservative CAS edge recovery for type, icons, and motion graphics.",
        sharpen_method="cas",
        sharpen_strength=0.18,
    ),
    "ui_balanced": ProcessingPreset(
        key="ui_balanced",
        label="UI / Text — Balanced clarity",
        description="More visible CAS edge recovery while remaining below the halo-prone range.",
        sharpen_method="cas",
        sharpen_strength=0.28,
    ),
    "photo_gentle": ProcessingPreset(
        key="photo_gentle",
        label="Photo / Video — Gentle detail",
        description="Very light CAS intended for photographic or mixed raster content.",
        sharpen_method="cas",
        sharpen_strength=0.07,
    ),
    "social_compensation": ProcessingPreset(
        key="social_compensation",
        label="Social delivery — Compression compensation",
        description=(
            "CAS at 0.28 for sharper type and edges before a platform transcode. Use with a delivery copy, "
            "not as an exact-source reference."
        ),
        sharpen_method="cas",
        sharpen_strength=DEFAULT_SHARPEN_STRENGTH,
    ),
    "custom": ProcessingPreset(
        key="custom",
        label="Custom…",
        description="Use the custom processing controls.",
    ),
}

PROCESSING_LABEL_TO_KEY = {preset.label: key for key, preset in PROCESSING_PRESETS.items()}


@dataclass(frozen=True)
class Recipe:
    key: str
    label: str
    description: str
    scale: float
    fps: int
    output_profile_key: str
    processing_key: str
    capture_mode: CaptureMode = CaptureMode.AUTO
    timeline_mode: TimelineMode = TimelineMode.AUTO
    geometry_mode: GeometryMode = GeometryMode.AUTO
    viewport_width: int = 1080
    viewport_height: int = 1920
    manual_duration: Optional[float] = None
    strip_outer_shadow: bool = False

    def create_job(self, source_value: str, source_kind: SourceKind = SourceKind.FILE) -> JobConfig:
        processing = PROCESSING_PRESETS[self.processing_key].to_config()
        return JobConfig(
            source=SourceSpec(
                value=source_value,
                kind=source_kind,
                load_strategy=(
                    LoadStrategy.REMOTE_URL if source_kind == SourceKind.URL else LoadStrategy.AUTO
                ),
            ),
            capture=CaptureConfig(
                mode=self.capture_mode,
                viewport_width=self.viewport_width,
                viewport_height=self.viewport_height,
                geometry_mode=self.geometry_mode,
                strip_outer_shadow=self.strip_outer_shadow,
            ),
            timeline=TimelineConfig(
                mode=self.timeline_mode,
                manual_duration=self.manual_duration,
            ),
            render=RenderConfig(
                scale=self.scale,
                fps=self.fps,
                output_profile_key=self.output_profile_key,
                processing=processing,
                audio=AudioConfig(),
            ),
            recipe_key=self.key,
        )


RECIPES: dict[str, Recipe] = {
    "motion_graphics_master": Recipe(
        key="motion_graphics_master",
        label="Motion graphics master",
        description=(
            "2× / 60 fps lossless RGB with content-aware clarity for archival work. "
            "Use Social delivery for phones, VLC hardware decoding, and uploads."
        ),
        scale=2.0,
        fps=60,
        output_profile_key="lossless_rgb_mp4",
        processing_key="auto_content_aware",
        geometry_mode=GeometryMode.AUTO,
        strip_outer_shadow=True,
    ),
    "exact_source_master": Recipe(
        key="exact_source_master",
        label="Exact source master",
        description="2× / 60 fps lossless RGB with no visual processing.",
        scale=2.0,
        fps=60,
        output_profile_key="lossless_rgb_mp4",
        processing_key="no_processing",
    ),
    "editing_master": Recipe(
        key="editing_master",
        label="Editing master",
        description="2× / 60 fps ProRes 4444 MOV for NLE interchange.",
        scale=2.0,
        fps=60,
        output_profile_key="prores_4444_mov",
        processing_key="no_processing",
    ),
    "social_delivery": Recipe(
        key="social_delivery",
        label="Social delivery — Sharp compatible MP4 (default)",
        description="1× / 60 fps H.264 Main 4:2:0 at CRF 8 with CAS 0.28 clarity. Default for mobile playback and social uploads; no automatic 4K upscaling.",
        scale=1.0,
        fps=60,
        output_profile_key="h264_420_mp4",
        processing_key="social_compensation",
        strip_outer_shadow=True,
    ),
    "web_animation": Recipe(
        key="web_animation",
        label="CSS / Web animation",
        description="1× / 60 fps viewport capture using Web Animations seeking when available.",
        scale=1.0,
        fps=60,
        output_profile_key="h264_444_mp4",
        processing_key="no_processing",
        capture_mode=CaptureMode.VIEWPORT,
        timeline_mode=TimelineMode.WEB_ANIMATIONS,
        geometry_mode=GeometryMode.PRESERVE_LAYOUT,
        viewport_width=1920,
        viewport_height=1080,
    ),
    "video_element": Recipe(
        key="video_element",
        label="HTML video element",
        description="1× / 60 fps video-element capture driven by media currentTime.",
        scale=1.0,
        fps=60,
        output_profile_key="h264_420_mp4",
        processing_key="no_processing",
        capture_mode=CaptureMode.AUTO,
        timeline_mode=TimelineMode.MEDIA,
        geometry_mode=GeometryMode.PRESERVE_LAYOUT,
        viewport_width=1920,
        viewport_height=1080,
    ),
    "static_page_hold": Recipe(
        key="static_page_hold",
        label="Static page hold",
        description="1× / 30 fps full viewport held for 5 seconds. Duration remains editable per job.",
        scale=1.0,
        fps=30,
        output_profile_key="h264_420_mp4",
        processing_key="no_processing",
        capture_mode=CaptureMode.VIEWPORT,
        timeline_mode=TimelineMode.STATIC,
        geometry_mode=GeometryMode.PRESERVE_LAYOUT,
        viewport_width=1920,
        viewport_height=1080,
        manual_duration=5.0,
    ),
    "website_capture": Recipe(
        key="website_capture",
        label="Website / timer animation",
        description=(
            "1× / 30 fps full viewport using Chromium's virtual clock when supported. "
            "Supply duration manually when the page does not expose one."
        ),
        scale=1.0,
        fps=30,
        output_profile_key="h264_420_mp4",
        processing_key="no_processing",
        capture_mode=CaptureMode.VIEWPORT,
        timeline_mode=TimelineMode.BROWSER_CLOCK,
        geometry_mode=GeometryMode.PRESERVE_LAYOUT,
        viewport_width=1920,
        viewport_height=1080,
    ),
}

RECIPE_LABEL_TO_KEY = {recipe.label: key for key, recipe in RECIPES.items()}


def apply_recipe(job: JobConfig, recipe_key: str) -> JobConfig:
    recipe = RECIPES[recipe_key]
    fresh = recipe.create_job(job.source.value, job.source.kind)
    fresh.source.load_strategy = job.source.load_strategy
    fresh.render.output_directory = job.render.output_directory
    fresh.render.save_next_to_source = job.render.save_next_to_source
    fresh.render.overwrite = job.render.overwrite
    fresh.render.filename_template = job.render.filename_template
    fresh.render.cpu_threads = job.render.cpu_threads
    fresh.render.capture_workers = job.render.capture_workers
    fresh.render.frame_buffer_mb = job.render.frame_buffer_mb
    fresh.render.fast_capture = job.render.fast_capture
    import copy
    fresh.render.audio = copy.deepcopy(job.render.audio)
    return fresh
