from __future__ import annotations

import io
import math
from dataclasses import dataclass
from statistics import mean
from typing import Iterable

from PIL import Image, ImageChops, ImageFilter, ImageStat

from models import ProcessingConfig
from presets import PROCESSING_PRESETS


@dataclass(frozen=True)
class ContentAnalysis:
    classification: str
    recommended_preset_key: str
    edge_energy: float
    flat_fraction: float
    saturation: float
    texture_energy: float
    confidence: float
    explanation: str
    usable_samples: int = 0
    ignored_near_uniform_samples: int = 0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _frame_metrics(png_bytes: bytes) -> tuple[float, float, float, float, float]:
    with Image.open(io.BytesIO(png_bytes)) as source:
        image = source.convert("RGB")
        image.thumbnail((384, 384), Image.Resampling.LANCZOS)

        gray = image.convert("L")
        edge = gray.filter(ImageFilter.FIND_EDGES)
        if edge.width > 2 and edge.height > 2:
            edge = edge.crop((1, 1, edge.width - 1, edge.height - 1))
        edge_stat = ImageStat.Stat(edge)
        edge_energy = edge_stat.mean[0] / 255.0
        contrast_std = ImageStat.Stat(gray).stddev[0] / 255.0

        # Flat-region estimate: compare each pixel with a lightly blurred version.
        blurred = image.filter(ImageFilter.GaussianBlur(radius=1.2))
        residual = ImageChops.difference(image, blurred).convert("L")
        hist = residual.histogram()
        total = max(1, sum(hist))
        flat_fraction = sum(hist[:5]) / total
        texture_energy = ImageStat.Stat(residual).mean[0] / 255.0

        hsv = image.convert("HSV")
        saturation = ImageStat.Stat(hsv.getchannel("S")).mean[0] / 255.0

    return edge_energy, flat_fraction, saturation, texture_energy, contrast_std


def analyze_frames(png_frames: Iterable[bytes]) -> ContentAnalysis:
    metrics = [_frame_metrics(frame) for frame in png_frames]
    if not metrics:
        return ContentAnalysis(
            classification="unknown",
            recommended_preset_key="no_processing",
            edge_energy=0.0,
            flat_fraction=0.0,
            saturation=0.0,
            texture_energy=0.0,
            confidence=0.0,
            explanation="No sample frames were available; processing was disabled.",
            usable_samples=0,
            ignored_near_uniform_samples=0,
        )

    usable = [item for item in metrics if item[4] >= 0.025]
    ignored = len(metrics) - len(usable)
    if not usable:
        return ContentAnalysis(
            classification="near_uniform",
            recommended_preset_key="no_processing",
            edge_energy=mean(item[0] for item in metrics),
            flat_fraction=mean(item[1] for item in metrics),
            saturation=mean(item[2] for item in metrics),
            texture_energy=mean(item[3] for item in metrics),
            confidence=1.0,
            explanation=(
                "All sampled frames were near-uniform (for example a fade, blank intro, or "
                "solid field), so sharpening was disabled rather than guessing."
            ),
            usable_samples=0,
            ignored_near_uniform_samples=ignored,
        )

    edge_energy = mean(item[0] for item in usable)
    flat_fraction = mean(item[1] for item in usable)
    saturation = mean(item[2] for item in usable)
    texture_energy = mean(item[3] for item in usable)

    # These deliberately conservative heuristics distinguish flat-colour UI/vector
    # work from continuous-tone imagery. They do not attempt to infer perceived
    # quality or invent detail.
    ui_score = (
        _clamp((flat_fraction - 0.52) / 0.32, 0.0, 1.0) * 0.55
        + _clamp((edge_energy - 0.025) / 0.09, 0.0, 1.0) * 0.30
        + _clamp((0.055 - texture_energy) / 0.045, 0.0, 1.0) * 0.15
    )
    photo_score = (
        _clamp((texture_energy - 0.035) / 0.08, 0.0, 1.0) * 0.65
        + _clamp((0.68 - flat_fraction) / 0.35, 0.0, 1.0) * 0.35
    )

    if ui_score >= 0.58 and ui_score > photo_score + 0.08:
        classification = "ui_vector"
        preset = "ui_subtle"
        confidence = ui_score
        explanation = (
            "Flat regions and concentrated edges indicate UI/vector or typography-heavy "
            "content; subtle CAS is recommended."
        )
    elif photo_score >= 0.62 and photo_score > ui_score + 0.08:
        classification = "photo_video"
        preset = "no_processing"
        confidence = photo_score
        explanation = (
            "Continuous-tone texture suggests photographic/video content; sharpening is "
            "disabled to avoid halos and amplified noise."
        )
    else:
        classification = "mixed"
        preset = "no_processing"
        confidence = min(0.5, abs(ui_score-photo_score))
        explanation = (
            "The simple metrics are inconclusive for this mixed content. Automatic processing "
            "is disabled; compare an explicit subtle preset before choosing it."
        )

    return ContentAnalysis(
        classification=classification,
        recommended_preset_key=preset,
        edge_energy=edge_energy,
        flat_fraction=flat_fraction,
        saturation=saturation,
        texture_energy=texture_energy,
        confidence=_clamp(confidence, 0.0, 1.0),
        explanation=explanation,
        usable_samples=len(usable),
        ignored_near_uniform_samples=ignored,
    )


def resolve_processing_config(
    configured: ProcessingConfig,
    analysis: ContentAnalysis | None,
) -> ProcessingConfig:
    if configured.preset_key != "auto_content_aware":
        if configured.preset_key == "custom":
            return configured
        preset = PROCESSING_PRESETS.get(configured.preset_key)
        return preset.to_config() if preset else configured

    if analysis is None:
        return PROCESSING_PRESETS["no_processing"].to_config()

    return PROCESSING_PRESETS[analysis.recommended_preset_key].to_config()


def build_filter_chain(config: ProcessingConfig) -> str:
    filters: list[str] = []

    if config.sharpen_method == "cas" and config.sharpen_strength > 0:
        filters.append(f"cas=strength={config.sharpen_strength:.4f}")
    elif config.sharpen_method == "unsharp" and config.sharpen_strength > 0:
        # Keep custom unsharp conservative. The luma amount range here is 0..1.5.
        amount = config.sharpen_strength * 1.5
        filters.append(f"unsharp=5:5:{amount:.4f}:5:5:0")

    eq_changed = not (
        math.isclose(config.contrast, 1.0, abs_tol=1e-9)
        and math.isclose(config.saturation, 1.0, abs_tol=1e-9)
        and math.isclose(config.brightness, 0.0, abs_tol=1e-9)
    )
    if eq_changed:
        filters.append(
            "eq="
            f"contrast={config.contrast:.4f}:"
            f"saturation={config.saturation:.4f}:"
            f"brightness={config.brightness:.4f}"
        )

    if config.deband:
        # Mild, deterministic debanding. Not enabled by any default preset.
        filters.append("deband=1thr=0.008:2thr=0.008:3thr=0.008:range=12:blur=0")

    return ",".join(filters)
