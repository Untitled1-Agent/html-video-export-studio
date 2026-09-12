"""Regressions reproduced during repository preparation (1.5.2)."""
from __future__ import annotations
import contextlib
import io
import json
import tempfile
import subprocess
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import cli
from models import AudioMode, LoadStrategy, TimelineMode
from presets import RECIPES
from renderer import HtmlVideoRenderer, ExportError, parse_scene_duration, render_output_name
from updater import validate_application

ROOT = Path(__file__).resolve().parents[1]

class RepositoryCoreTests(unittest.TestCase):
    def test_probe_startup_failure_is_json_for_single_source(self):
        out, err = io.StringIO(), io.StringIO()
        with patch('cli.HtmlVideoRenderer', side_effect=RuntimeError('no browser')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(['a.html', '--probe'])
        self.assertEqual(rc, 1)
        self.assertIn('error', json.loads(out.getvalue()))

    def test_probe_startup_failure_is_json_for_batch(self):
        out = io.StringIO()
        with patch('cli.HtmlVideoRenderer', side_effect=RuntimeError('no browser')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(['a.html', 'b.html', '--probe'])
        self.assertEqual(rc, 1)
        self.assertEqual([p['source'] for p in json.loads(out.getvalue())], ['a.html', 'b.html'])

    def test_scene_sum_overflow_rejected(self):
        self.assertIsNone(parse_scene_duration([{'dur': 1e308}, {'dur': 1e308}]))

    def test_windows_reserved_output_name_rejected(self):
        for name in ['CON.html', 'nul.html', 'LPT1.html', 'com3.html']:
            job = RECIPES['exact_source_master'].create_job(name)
            job.render.filename_template = '{stem}.{ext}'
            with self.subTest(name=name), self.assertRaises(ExportError):
                render_output_name(job)

    def test_yuv_preflight_checks_actual_color_filters(self):
        job = RECIPES['social_delivery'].create_job('a.html')
        with patch('media_pipeline.select_ffmpeg', return_value='ffmpeg') as select:
            HtmlVideoRenderer()._validate_profile_and_filters(job, 64, 48, '')
        self.assertIn('scale', select.call_args.args[1])

    def test_audio_preflight_checks_actual_audio_filters(self):
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / 'tone.wav'
            # Preflight now verifies decodability, not just the file's existence.
            with wave.open(str(audio), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48000)
                output.writeframes(b'\x00\x00' * 4800)
            job = RECIPES['exact_source_master'].create_job('a.html')
            job.render.audio.path = str(audio); job.render.audio.mode = AudioMode.TRIM
            job.render.audio.offset_seconds = 0.2; job.render.audio.fade_in_seconds = 0.1
            with patch('media_pipeline.select_ffmpeg', return_value='ffmpeg') as select:
                HtmlVideoRenderer()._validate_profile_and_filters(job, 64, 48, '')
            self.assertTrue({'asetpts','apad','atrim','adelay','afade'} <= select.call_args.args[1])

    def test_application_validation_ignores_local_venv(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td)
            for p in ROOT.glob('*.py'): (dst/p.name).write_bytes(p.read_bytes())
            (dst/'requirements.txt').write_bytes((ROOT/'requirements.txt').read_bytes())
            (dst/'.venv').mkdir(); (dst/'.venv/bad.py').write_text('not valid python!')
            self.assertTrue(validate_application(dst))

    def test_application_requires_cli(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td)
            for p in ROOT.glob('*.py'):
                if p.name != 'cli.py': (dst/p.name).write_bytes(p.read_bytes())
            (dst/'requirements.txt').write_bytes((ROOT/'requirements.txt').read_bytes())
            with self.assertRaises(Exception): validate_application(dst)

class RepositoryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        cls.renderer = HtmlVideoRenderer(page_timeout_ms=4000); cls.renderer.start()
    @classmethod
    def tearDownClass(cls):
        cls.renderer.close(); cls.temp.cleanup()
    def job(self, html, mode=TimelineMode.STATIC):
        src = self.root/(self._testMethodName+'.html'); src.write_text(html, encoding='utf-8')
        job = RECIPES['exact_source_master'].create_job(str(src))
        job.source.load_strategy = LoadStrategy.EMBEDDED
        job.timeline.mode = mode; job.render.scale = 1; job.render.fps = 10
        return job
    def mixed(self):
        return """<style>@keyframes move{to{transform:translateX(5px)}}
          #loop{animation:move 1s infinite}#once{animation:move .2s 1}</style>
          <div data-video-export style='width:64px;height:48px'><b id=loop>L</b><b id=once>O</b></div>"""
    def test_infinite_and_finite_animations_do_not_guess_duration(self):
        job = self.job(self.mixed(), TimelineMode.WEB_ANIMATIONS)
        result = self.renderer.probe(job)
        self.assertIsNone(result.duration_seconds)
        self.assertTrue(result.has_errors)
    def test_explicit_duration_allows_infinite_animation(self):
        job = self.job(self.mixed(), TimelineMode.WEB_ANIMATIONS)
        job.timeline.manual_duration = 0.15
        result = self.renderer.render(job, self.root/'explicit-loop.mp4')
        self.assertEqual(result.frame_count, 2)
    def test_trim_bound_allows_infinite_animation(self):
        job = self.job(self.mixed(), TimelineMode.WEB_ANIMATIONS)
        job.timeline.trim_end = 0.3
        result = self.renderer.probe(job)
        self.assertEqual(result.duration_seconds, 0.3)
    def test_output_must_not_overwrite_external_audio(self):
        job = self.job('<svg width=64 height=48 data-video-export data-duration=.1></svg>')
        from media_pipeline import select_ffmpeg
        audio = self.root/'soundtrack.mp4'
        subprocess.run([select_ffmpeg({'aac'}, set()), '-v', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=1000:duration=0.2', '-c:a', 'aac', str(audio)],
                       capture_output=True, check=True, timeout=30)
        original_audio = audio.read_bytes()
        job.render.audio.path = str(audio); job.render.audio.mode = AudioMode.TRIM
        job.render.overwrite = True
        with self.assertRaisesRegex(ExportError, 'audio'):
            self.renderer.render(job, audio)
        self.assertEqual(audio.read_bytes(), original_audio)
    def test_long_valid_output_name_does_not_overflow_temporary_filename(self):
        job = self.job('<svg width=64 height=48 data-video-export data-duration=.1></svg>')
        out = self.root / ('a' * 235 + '.mp4')
        result = self.renderer.render(job, out)
        self.assertEqual(result.output_path, out)
        self.assertTrue(out.stat().st_size > 0)
        self.assertFalse(list(self.root.glob('.hves-partial-*')))

    def test_preview_must_not_overwrite_html(self):
        job = self.job('<svg width=64 height=48 data-video-export data-duration=.1></svg>')
        src = Path(job.source.value); original = src.read_bytes()
        with self.assertRaisesRegex(ExportError, 'PNG|png'):
            self.renderer.capture_test_frame(job, src)
        self.assertEqual(src.read_bytes(), original)
    def test_preview_filter_failure_preserves_existing_file(self):
        job = self.job('<svg width=64 height=48 data-video-export data-duration=.1></svg>')
        job.render.processing.preset_key = 'ui_subtle'
        # Obtain a genuine known preset key without guessing legacy names.
        from presets import PROCESSING_PRESETS
        job.render.processing = next(p.to_config() for p in PROCESSING_PRESETS.values() if p.sharpen_method=='cas' and not p.auto)
        out = self.root/'preserved.png'; out.write_bytes(b'old-good-preview')
        job.render.overwrite = True
        def fail(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b'incomplete')
            return Mock(returncode=1, stderr=b'injected failure')
        with patch('subprocess.run', side_effect=fail), patch('media_pipeline.select_ffmpeg', return_value='ffmpeg'), patch('renderer.get_ffmpeg_executable', return_value='ffmpeg'):
            with self.assertRaises(ExportError): self.renderer.capture_test_frame(job, out)
        self.assertEqual(out.read_bytes(), b'old-good-preview')
        self.assertFalse(list(self.root.glob('*.partial-*')))

if __name__ == '__main__': unittest.main()
