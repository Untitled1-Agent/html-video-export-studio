from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

from models import (
    CaptureMode,
    JobStatus,
    QueueJob,
    SourceKind,
    TimelineMode,
    dataclass_to_dict,
    job_config_from_dict,
)
from presets import OUTPUT_PROFILES, PROCESSING_PRESETS, RECIPES
from processing import analyze_frames, build_filter_chain, resolve_processing_config
from project_io import ProjectDocument, load_project, save_project
from renderer import (
    LoopbackServer,
    choose_output_path,
    inject_base_href,
    parse_scene_duration,
    png_dimensions,
    render_output_name,
)
from settings_store import AppSettings, add_recent, load_settings, save_settings
from updater import (
    UpdateError,
    is_newer_version,
    normalize_repo,
    parse_checksum,
    parse_version,
    stage_update,
    validate_zip_members,
    verify_sha256,
)


def png_with_ui() -> bytes:
    image = Image.new("RGB", (320, 180), "#201e1d")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 300, 70), fill="#ec3013")
    draw.rectangle((20, 90, 160, 160), fill="#f3f2f2")
    draw.line((180, 90, 300, 160), fill="#9cff57", width=8)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def png_with_photo_like_texture() -> bytes:
    image = Image.new("RGB", (320, 180))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = (
                (x * 7 + y * 3) % 256,
                (x * 2 + y * 11) % 256,
                (x * 13 + y * 5) % 256,
            )
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


class CoreTests(unittest.TestCase):
    def test_scene_duration_json_string(self):
        self.assertAlmostEqual(
            parse_scene_duration('[{"dur":1.2},{"dur":2.3}]'), 3.5
        )

    def test_scene_duration_rejects_invalid(self):
        for value in (None, "", "garbage", "[]", '[{"dur":0}]', '[{"x":1}]'):
            self.assertIsNone(parse_scene_duration(value))

    def test_no_hardcoded_duration_in_default_recipe(self):
        job = RECIPES["motion_graphics_master"].create_job("source.html")
        self.assertIsNone(job.timeline.manual_duration)

    def test_static_recipe_duration_is_explicit_recipe_data(self):
        job = RECIPES["static_page_hold"].create_job("source.html")
        self.assertEqual(job.timeline.manual_duration, 5.0)
        self.assertEqual(job.timeline.mode, TimelineMode.STATIC)

    def test_dataclass_roundtrip(self):
        original = RECIPES["motion_graphics_master"].create_job("/tmp/a.html")
        original.capture.selector = "#stage"
        original.timeline.trim_start = 0.25
        data = dataclass_to_dict(original)
        restored = job_config_from_dict(data)
        self.assertEqual(restored.capture.selector, "#stage")
        self.assertEqual(restored.timeline.trim_start, 0.25)
        self.assertEqual(restored.render.scale, 2.0)

    def test_base_href_injected_once(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "a.html"
            result = inject_base_href("<html><head></head><body></body></html>", source)
            self.assertEqual(result.lower().count("<base"), 1)
            result2 = inject_base_href(result, source)
            self.assertEqual(result2.lower().count("<base"), 1)

    def test_png_dimensions(self):
        self.assertEqual(png_dimensions(png_with_ui()), (320, 180))
        with self.assertRaises(Exception):
            png_dimensions(b"not png")

    def test_processing_no_filter(self):
        config = PROCESSING_PRESETS["no_processing"].to_config()
        self.assertEqual(build_filter_chain(config), "")

    def test_processing_subtle_cas(self):
        chain = build_filter_chain(PROCESSING_PRESETS["ui_subtle"].to_config())
        self.assertIn("cas=strength=0.1800", chain)
        self.assertNotIn("unsharp", chain)

    def test_custom_processing_chain(self):
        config = PROCESSING_PRESETS["custom"].to_config()
        config.sharpen_method = "unsharp"
        config.sharpen_strength = 0.4
        config.contrast = 1.03
        config.deband = True
        chain = build_filter_chain(config)
        self.assertIn("unsharp", chain)
        self.assertIn("eq=", chain)
        self.assertIn("deband", chain)

    def test_content_analysis_ui(self):
        analysis = analyze_frames([png_with_ui()] * 3)
        self.assertIn(analysis.classification, {"ui_vector", "mixed"})
        self.assertIn(analysis.recommended_preset_key, {"ui_subtle", "photo_gentle"})


    def test_content_analysis_ignores_blank_samples(self):
        blank = Image.new("RGB", (320, 180), "#201e1d")
        stream = io.BytesIO()
        blank.save(stream, "PNG")
        analysis = analyze_frames([stream.getvalue()])
        self.assertEqual(analysis.classification, "near_uniform")
        self.assertEqual(analysis.recommended_preset_key, "no_processing")

    def test_content_analysis_photo_does_not_recommend_strong_ui(self):
        analysis = analyze_frames([png_with_photo_like_texture()] * 2)
        self.assertNotEqual(analysis.recommended_preset_key, "ui_balanced")

    def test_auto_processing_resolves(self):
        analysis = analyze_frames([png_with_ui()])
        resolved = resolve_processing_config(
            PROCESSING_PRESETS["auto_content_aware"].to_config(), analysis
        )
        self.assertNotEqual(resolved.preset_key, "auto_content_aware")

    def test_output_profiles_have_extensions_and_encoders(self):
        for profile in OUTPUT_PROFILES.values():
            self.assertTrue(profile.extension)
            self.assertTrue(profile.video_encoder)
            self.assertIn("-c:v", profile.video_args)

    def test_output_name_template(self):
        job = RECIPES["motion_graphics_master"].create_job("/tmp/My Ad.html")
        name = render_output_name(job, processing_key="ui_subtle")
        self.assertTrue(name.endswith(".mp4"))
        self.assertIn("2x", name)
        self.assertIn("60fps", name)

    def test_output_collision_gets_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "a.html"
            source.write_text("<html></html>")
            job = RECIPES["exact_source_master"].create_job(str(source))
            first = choose_output_path(job, "no_processing")
            first.write_bytes(b"existing")
            second = choose_output_path(job, "no_processing")
            self.assertNotEqual(first, second)
            self.assertIn("_2", second.stem)

    def test_project_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "project.hves.json"
            job = QueueJob(
                job_id="one",
                config=RECIPES["exact_source_master"].create_job("/tmp/a.html"),
                status=JobStatus.DONE,
            )
            save_project(ProjectDocument([job], name="Test"), path)
            loaded = load_project(path)
            self.assertEqual(loaded.name, "Test")
            self.assertEqual(len(loaded.jobs), 1)
            self.assertEqual(loaded.jobs[0].status, JobStatus.QUEUED)

    def test_settings_independent_migration(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({"workers":"bad", "update_repo":"owner/repo", "auto_analyze_on_add":True}))
            loaded = load_settings(path)
            self.assertEqual(loaded.workers, 2)
            self.assertEqual(loaded.update_repo, "owner/repo")
            self.assertTrue(loaded.auto_analyze_on_add)

    def test_settings_atomic_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            settings = AppSettings(workers=4, update_repo="owner/repo")
            save_settings(settings, path)
            loaded = load_settings(path)
            self.assertEqual(loaded.workers, 4)
            self.assertEqual(loaded.update_repo, "owner/repo")

    def test_add_recent_deduplicates(self):
        self.assertEqual(add_recent(["a", "b", "c"], "b", 3), ["b", "a", "c"])

    def test_semver(self):
        self.assertEqual(parse_version("v1.5.0")[:3], (1, 5, 0))
        self.assertTrue(is_newer_version("1.5.1", "1.5.0"))
        self.assertFalse(is_newer_version("1.5.0-beta", "1.5.0"))

    def test_repo_normalization(self):
        self.assertEqual(normalize_repo("https://github.com/a/b.git"), "a/b")
        self.assertEqual(normalize_repo("git@github.com:a/b.git"), "a/b")
        with self.assertRaises(ValueError):
            normalize_repo("not a repo")

    def test_checksum(self):
        data = b"hello"
        digest = __import__("hashlib").sha256(data).hexdigest()
        self.assertEqual(parse_checksum(f"{digest}  app.zip", "app.zip"), digest)
        verify_sha256(data, digest)
        with self.assertRaises(UpdateError):
            verify_sha256(b"wrong", digest)

    def test_zip_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../escape.txt", "bad")
            with zipfile.ZipFile(path) as archive:
                with self.assertRaises(UpdateError):
                    validate_zip_members(archive)

    def test_safe_update_staging(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("app/version.py", "APP_VERSION='9.9.9'")
            archive.writestr("app/app.py", "print('ok')")
        with tempfile.TemporaryDirectory() as td:
            root = stage_update(stream.getvalue(), Path(td))
            self.assertTrue((root / "version.py").exists())


if __name__ == "__main__":
    unittest.main()
