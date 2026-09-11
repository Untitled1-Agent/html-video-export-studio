from __future__ import annotations

import copy
import os
import subprocess
import tempfile
import threading
import unittest
import wave
import math
import struct
from pathlib import Path

from models import (
    AudioMode,
    CaptureMode,
    GeometryMode,
    LoadStrategy,
    TimelineMode,
)
from presets import PROCESSING_PRESETS, RECIPES
from renderer import ExportError, HtmlVideoRenderer, get_ffmpeg_executable, png_dimensions


OM_FIXTURE = r'''<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body{margin:0;background:#111;width:100%;height:100%;overflow:hidden}
.shell{transform:scale(.9770833333);transform-origin:0 0}
</style></head><body>
<div class="shell"><svg id="stage" width="320" height="180" viewBox="0 0 320 180"
 data-om-exportable-video-with-duration-secs="0.2" data-om-sync-seek="true">
<rect width="320" height="180" fill="#201e1d"/><rect id="box" x="0" y="50" width="40" height="40" fill="#ec3013"/>
<text id="clock" x="10" y="150" fill="white">0.000</text></svg></div>
<script>
const stage=document.getElementById('stage');
stage.addEventListener('data-om-seek-to-time-frame',e=>{
 const t=Number(e.detail.time||0); document.getElementById('box').setAttribute('x',Math.round(t*1000));
 document.getElementById('clock').textContent=t.toFixed(3);
});
setInterval(()=>{document.querySelector('.shell').style.transform='scale(.9770833333)'},10);
</script></body></html>'''

GENERIC_FIXTURE = r'''<!doctype html><html><body style="margin:0">
<canvas id="c" width="200" height="100" data-video-export data-video-duration="0.2"></canvas>
<script>
const c=document.getElementById('c'),x=c.getContext('2d');
function draw(t){x.fillStyle='#201e1d';x.fillRect(0,0,200,100);x.fillStyle='#ec3013';x.fillRect(t*100,20,30,30)}
c.addEventListener('my-seek',e=>draw(e.detail.time));draw(0);
</script></body></html>'''

JS_HOOK_FIXTURE = r'''<!doctype html><html><body style="margin:0">
<svg id="stage" width="240" height="120" data-video-export data-video-duration="0.2">
<rect width="240" height="120" fill="#111"/><circle id="dot" cy="60" cx="10" r="8" fill="lime"/>
</svg><script>window.myExporter={seek:async(t)=>{document.getElementById('dot').setAttribute('cx',10+t*500)}}</script>
</body></html>'''

CSS_FIXTURE = r'''<!doctype html><html><head><style>
html,body{margin:0;width:320px;height:180px;overflow:hidden;background:#111}
#box{width:40px;height:40px;background:#ec3013;animation:move .2s linear 1 forwards}
@keyframes move{from{transform:translateX(0)}to{transform:translateX(200px)}}
</style></head><body data-video-duration="0.2"><div id="box"></div></body></html>'''

STATIC_FIXTURE = r'''<!doctype html><html><body style="margin:0;background:#123;width:160px;height:90px"><h1 style="color:white">Static</h1></body></html>'''

SVG_PERCENT_FIXTURE = r'''<!doctype html><html><body style="margin:0">
<svg data-video-export data-video-duration="0.1" width="100%" height="100%" viewBox="0 0 640 360">
<rect width="640" height="360" fill="navy"/></svg>
</body></html>'''

MULTI_FIXTURE = r'''<!doctype html><html><body style="margin:0">
<svg width="100" height="50"><rect width="100" height="50" fill="red"/></svg>
<svg data-video-export data-video-duration="0.1" width="300" height="150"><rect width="300" height="150" fill="blue"/></svg>
</body></html>'''


def browser_path() -> str | None:
    for candidate in (
        os.environ.get("CHROMIUM_PATH"),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        candidate = pw.chromium.executable_path
        if Path(candidate).exists(): return candidate
    return None


def write_fixture(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def base_job(path: Path, recipe="exact_source_master"):
    job = RECIPES[recipe].create_job(str(path))
    job.source.load_strategy = LoadStrategy.EMBEDDED
    job.render.fps = 10
    job.render.scale = 2
    job.render.processing = PROCESSING_PRESETS["no_processing"].to_config()
    return job


def decode_first_frame_size(path: Path) -> tuple[int, int]:
    proc = subprocess.run(
        [get_ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        capture_output=True,
        check=True,
    )
    return png_dimensions(proc.stdout)


@unittest.skipUnless(browser_path(), "No Chromium available")
class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_obj = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls.tmp_obj.name)
        cls.renderer = HtmlVideoRenderer(browser_executable=browser_path(), page_timeout_ms=30_000)
        cls.renderer.start()

    @classmethod
    def tearDownClass(cls):
        cls.renderer.close()
        cls.tmp_obj.cleanup()

    def test_om_geometry_lock_exact_2x(self):
        path = write_fixture(self.tmp, "om.html", OM_FIXTURE)
        job = base_job(path)
        probe = self.renderer.probe(job, deep_analysis=True)
        self.assertEqual((probe.source_width, probe.source_height), (320, 180))
        self.assertEqual((probe.output_width, probe.output_height), (640, 360))
        self.assertEqual(probe.timeline_mode, TimelineMode.OM_EVENT)
        output = self.tmp / "om.mp4"
        result = self.renderer.render(job, output)
        self.assertEqual(result.frame_count, 2)
        self.assertEqual(decode_first_frame_size(output), (640, 360))

    def test_canvas_custom_event(self):
        path = write_fixture(self.tmp, "canvas.html", GENERIC_FIXTURE)
        job = base_job(path)
        job.timeline.mode = TimelineMode.CUSTOM_EVENT
        job.timeline.custom_event_name = "my-seek"
        probe = self.renderer.probe(job)
        self.assertEqual(probe.target_kind, "canvas")
        self.assertEqual((probe.source_width, probe.source_height), (200, 100))
        output = self.tmp / "canvas.mp4"
        self.renderer.render(job, output)
        self.assertEqual(decode_first_frame_size(output), (400, 200))

    def test_javascript_function_adapter(self):
        path = write_fixture(self.tmp, "hook.html", JS_HOOK_FIXTURE)
        job = base_job(path)
        job.timeline.mode = TimelineMode.JAVASCRIPT_FUNCTION
        job.timeline.javascript_function = "window.myExporter.seek"
        output = self.tmp / "hook.mp4"
        result = self.renderer.render(job, output)
        self.assertEqual(result.timeline_mode, TimelineMode.JAVASCRIPT_FUNCTION)

    def test_css_web_animations_viewport(self):
        path = write_fixture(self.tmp, "css.html", CSS_FIXTURE)
        job = base_job(path)
        job.capture.mode = CaptureMode.VIEWPORT
        job.capture.viewport_width = 320
        job.capture.viewport_height = 180
        job.timeline.mode = TimelineMode.WEB_ANIMATIONS
        job.timeline.manual_duration = 0.2
        output = self.tmp / "css.mp4"
        result = self.renderer.render(job, output)
        self.assertEqual(decode_first_frame_size(output), (640, 360))
        self.assertEqual(result.frame_count, 2)

    def test_static_viewport(self):
        path = write_fixture(self.tmp, "static.html", STATIC_FIXTURE)
        job = base_job(path)
        job.capture.mode = CaptureMode.VIEWPORT
        job.capture.viewport_width = 160
        job.capture.viewport_height = 90
        job.timeline.mode = TimelineMode.STATIC
        job.timeline.manual_duration = 0.2
        output = self.tmp / "static.mp4"
        self.renderer.render(job, output)
        self.assertEqual(decode_first_frame_size(output), (320, 180))

    def test_svg_percent_uses_viewbox(self):
        path = write_fixture(self.tmp, "percent.html", SVG_PERCENT_FIXTURE)
        job = base_job(path)
        probe = self.renderer.probe(job)
        self.assertEqual((probe.source_width, probe.source_height), (640, 360))

    def test_auto_prefers_marker(self):
        path = write_fixture(self.tmp, "multi.html", MULTI_FIXTURE)
        job = base_job(path)
        probe = self.renderer.probe(job)
        self.assertEqual((probe.source_width, probe.source_height), (300, 150))
        self.assertEqual(probe.target_selector, "[data-video-export]")

    def test_selector_index(self):
        path = write_fixture(self.tmp, "selector.html", MULTI_FIXTURE)
        job = base_job(path)
        job.capture.mode = CaptureMode.SELECTOR
        job.capture.selector = "svg"
        job.capture.selector_index = 0
        job.capture.geometry_mode = GeometryMode.PRESERVE_LAYOUT
        probe = self.renderer.probe(job)
        self.assertEqual((probe.source_width, probe.source_height), (100, 50))

    def test_missing_duration_is_diagnostic_error(self):
        path = write_fixture(self.tmp, "missing.html", STATIC_FIXTURE)
        job = base_job(path)
        job.capture.mode = CaptureMode.VIEWPORT
        job.capture.viewport_width = 160
        job.capture.viewport_height = 90
        job.timeline.manual_duration = None
        job.timeline.mode = TimelineMode.STATIC
        probe = self.renderer.probe(job)
        self.assertTrue(probe.has_errors)
        self.assertTrue(any(d.code == "duration_missing" for d in probe.diagnostics))

    def test_trim_and_holds_frame_count(self):
        path = write_fixture(self.tmp, "trim.html", OM_FIXTURE)
        job = base_job(path)
        job.timeline.trim_start = 0.05
        job.timeline.trim_end = 0.15
        job.timeline.hold_start = 0.1
        job.timeline.hold_end = 0.1
        output = self.tmp / "trim.mp4"
        result = self.renderer.render(job, output)
        self.assertEqual(result.frame_count, 3)
        self.assertAlmostEqual(result.output_duration_seconds, 0.3)

    def test_filter_pipeline_and_no_filter_both_render(self):
        path = write_fixture(self.tmp, "filters.html", OM_FIXTURE)
        no_filter = base_job(path)
        filtered = copy.deepcopy(no_filter)
        filtered.render.processing = PROCESSING_PRESETS["ui_subtle"].to_config()
        out1 = self.tmp / "nofilter.mp4"
        out2 = self.tmp / "filtered.mp4"
        self.renderer.render(no_filter, out1)
        self.renderer.render(filtered, out2)
        self.assertTrue(out1.stat().st_size > 0)
        self.assertTrue(out2.stat().st_size > 0)

    def test_capture_test_frame(self):
        path = write_fixture(self.tmp, "preview.html", OM_FIXTURE)
        job = base_job(path)
        out = self.tmp / "preview.png"
        result_path, probe = self.renderer.capture_test_frame(job, out, at_seconds=0.1)
        self.assertEqual(result_path, out.resolve())
        self.assertEqual(png_dimensions(out.read_bytes()), (640, 360))
        self.assertEqual(probe.timeline_mode, TimelineMode.OM_EVENT)

    def test_parallel_independent_renderers(self):
        path = write_fixture(self.tmp, "parallel.html", OM_FIXTURE)
        errors = []
        outputs = [self.tmp / "p1.mp4", self.tmp / "p2.mp4"]

        def worker(output):
            try:
                with HtmlVideoRenderer(browser_executable=browser_path(), page_timeout_ms=30_000) as renderer:
                    renderer.render(base_job(path), output)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(output,)) for output in outputs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertFalse(errors, errors)
        self.assertTrue(all(path.exists() for path in outputs))

    def test_processing_comparison(self):
        path = write_fixture(self.tmp, "comparison.html", OM_FIXTURE)
        job = base_job(path)
        job.render.processing = PROCESSING_PRESETS["ui_subtle"].to_config()
        out = self.tmp / "comparison.png"
        result_path, _probe = self.renderer.capture_processing_comparison(job, out)
        self.assertEqual(result_path, out.resolve())
        width, height = png_dimensions(out.read_bytes())
        self.assertGreater(width, 640)
        self.assertGreater(height, 300)

    def test_external_audio_mux(self):
        path = write_fixture(self.tmp, "audio.html", OM_FIXTURE)
        wav_path = self.tmp / "tone.wav"
        rate = 8000
        samples = []
        for index in range(int(rate * 0.2)):
            value = int(6000 * math.sin(2 * math.pi * 440 * index / rate))
            samples.append(struct.pack("<h", value))
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(b"".join(samples))
        job = base_job(path)
        job.render.audio.mode = AudioMode.TRIM
        job.render.audio.path = str(wav_path)
        output = self.tmp / "audio.mp4"
        self.renderer.render(job, output)
        proc = subprocess.run(
            [get_ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-i", str(output),
             "-map", "0:a:0", "-f", "null", "-"],
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace"))

    def test_full_page_static_dimensions(self):
        html = """<!doctype html><html><body style='margin:0;width:120px;height:250px;background:red'></body></html>"""
        path = write_fixture(self.tmp, "fullpage.html", html)
        job = base_job(path)
        job.capture.mode = CaptureMode.FULL_PAGE
        job.capture.viewport_width = 120
        job.capture.viewport_height = 100
        job.timeline.mode = TimelineMode.STATIC
        job.timeline.manual_duration = 0.1
        job.render.scale = 1
        probe = self.renderer.probe(job)
        self.assertEqual(probe.source_width, 120)
        self.assertGreaterEqual(probe.source_height, 250)

    def test_manual_dimensions_override(self):
        path = write_fixture(self.tmp, "manual.html", STATIC_FIXTURE)
        job = base_job(path)
        job.capture.mode = CaptureMode.VIEWPORT
        job.capture.manual_width = 100
        job.capture.manual_height = 80
        job.timeline.mode = TimelineMode.STATIC
        job.timeline.manual_duration = 0.1
        probe = self.renderer.probe(job)
        self.assertEqual((probe.source_width, probe.source_height), (100, 80))



if __name__ == "__main__":
    unittest.main()
