"""CPU allocation and real decoded-media regressions; no timing thresholds."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from cli import build_parser, make_job
from media_pipeline import (available_cpu_count, ffmpeg_thread_plan, resolve_cpu_threads,
                            select_ffmpeg, snapshot_for_export)
from models import (AudioMode, JobConfig, SourceSpec, RenderConfig, dataclass_to_dict,
                    job_config_from_dict)
from presets import OUTPUT_PROFILES, PROCESSING_PRESETS, RECIPES, apply_recipe
from renderer import HtmlVideoRenderer


class CPUAllocationTests(unittest.TestCase):
    def test_auto_uses_more_than_two_encoder_threads(self):
        with patch('media_pipeline.available_cpu_count', return_value=16):
            plan = ffmpeg_thread_plan()
        self.assertEqual((plan.encoder, plan.filters, plan.decoder), (15, 8, 4))

    def test_auto_divides_budget_between_jobs(self):
        with patch('media_pipeline.available_cpu_count', return_value=16):
            self.assertEqual(resolve_cpu_threads(0, 2), 7)
            self.assertEqual(resolve_cpu_threads(0, 4), 3)

    def test_auto_is_bounded_on_large_hosts(self):
        with patch('media_pipeline.available_cpu_count', return_value=1024):
            self.assertEqual(resolve_cpu_threads(), 32)

    def test_small_cpu_counts_never_yield_zero(self):
        for cpus in (1, 2):
            with self.subTest(cpus=cpus), patch('media_pipeline.available_cpu_count', return_value=cpus):
                self.assertEqual(resolve_cpu_threads(0, 8), 1)

    def test_explicit_request_is_not_silently_divided(self):
        with patch('media_pipeline.available_cpu_count', side_effect=AssertionError('not needed')):
            self.assertEqual(resolve_cpu_threads(24, 4), 24)

    def test_one_thread_bounds_all_three_pools(self):
        plan = ffmpeg_thread_plan(1)
        self.assertEqual((plan.encoder, plan.filters, plan.decoder), (1, 1, 1))

    def test_invalid_thread_requests_are_rejected(self):
        for value in (-1, 257, True, 2.5, float('inf'), float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_cpu_threads(value)

    def test_invalid_concurrency_is_rejected(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_cpu_threads(4, value)

    def test_cpu_count_honors_affinity(self):
        with patch('media_pipeline.os.process_cpu_count', return_value=16, create=True), \
             patch('media_pipeline.os.sched_getaffinity', return_value={2, 3, 4, 5}, create=True):
            self.assertEqual(available_cpu_count(), 4)

    def test_cpu_count_honors_smaller_process_override(self):
        with patch('media_pipeline.os.process_cpu_count', return_value=2, create=True), \
             patch('media_pipeline.os.sched_getaffinity', return_value=set(range(8)), create=True):
            self.assertEqual(available_cpu_count(), 2)

    def test_cpu_count_fallback_for_older_python_and_windows(self):
        with patch('media_pipeline.os.process_cpu_count', None, create=True), \
             patch('media_pipeline.os.sched_getaffinity', None, create=True), \
             patch('media_pipeline.os.cpu_count', return_value=8):
            self.assertEqual(available_cpu_count(), 8)

    def test_failed_or_unknown_cpu_probes_have_safe_fallback(self):
        with patch('media_pipeline.os.process_cpu_count', side_effect=OSError, create=True), \
             patch('media_pipeline.os.sched_getaffinity', side_effect=OSError, create=True), \
             patch('media_pipeline.os.cpu_count', return_value=None):
            self.assertEqual(available_cpu_count(), 1)

    def test_cpu_count_is_not_cached(self):
        with patch('media_pipeline.os.process_cpu_count', None, create=True), \
             patch('media_pipeline.os.sched_getaffinity', side_effect=[{0}, {0, 1, 2}], create=True):
            self.assertEqual(available_cpu_count(), 1)
            self.assertEqual(available_cpu_count(), 3)


class CPUConfigurationTests(unittest.TestCase):
    def test_old_projects_default_to_auto(self):
        job = job_config_from_dict({'source': {'value': 'source.html'}})
        self.assertEqual(job.render.cpu_threads, 0)
        job.validate()

    def test_thread_setting_round_trips(self):
        job = JobConfig(SourceSpec('source.html'))
        job.render.cpu_threads = 12
        loaded = job_config_from_dict(json.loads(json.dumps(dataclass_to_dict(job))))
        self.assertEqual(loaded, job)

    def test_model_validation_rejects_invalid_thread_values(self):
        for value in (True, -1, 257, 1.5, float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RenderConfig(cpu_threads=value).validate()

    def test_project_does_not_truncate_fractional_or_boolean_threads(self):
        for value in (True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                job_config_from_dict({'render': {'cpu_threads': value}})

    def test_cli_default_and_explicit_threads(self):
        for options, expected in (([], 0), (['--cpu-threads', '16'], 16)):
            args = build_parser().parse_args(['source.html', *options])
            self.assertEqual(make_job('source.html', args).render.cpu_threads, expected)

    def test_cli_rejects_invalid_threads_at_parse_time(self):
        for value in ('-1', '257', '2.5', 'invalid'):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                build_parser().parse_args(['source.html', '--cpu-threads', value])

    def test_recipe_switch_preserves_cpu_setting(self):
        original = RECIPES['social_delivery'].create_job('source.html')
        original.render.cpu_threads = 16
        changed = apply_recipe(original, 'exact_source_master')
        self.assertEqual(changed.render.cpu_threads, 16)
        self.assertEqual(changed.render.processing.preset_key, 'no_processing')

    def test_queue_snapshot_does_not_mutate_saved_configuration(self):
        original = RECIPES['social_delivery'].create_job('source.html')
        before = copy.deepcopy(original)
        with patch('media_pipeline.available_cpu_count', return_value=16):
            snapshot = snapshot_for_export(original, 2)
        self.assertEqual(snapshot.render.cpu_threads, 7)
        snapshot.render.processing.sharpen_strength = 0.1
        self.assertEqual(original, before)

    def test_queue_snapshot_preserves_manual_override(self):
        original = JobConfig(SourceSpec('source.html'))
        original.render.cpu_threads = 12
        self.assertEqual(snapshot_for_export(original, 4).render.cpu_threads, 12)


class CPUCommandTests(unittest.TestCase):
    def test_all_profiles_scope_input_and_output_thread_options(self):
        renderer = object.__new__(HtmlVideoRenderer)
        renderer._ffmpeg_exe = 'ffmpeg'
        for key, profile in OUTPUT_PROFILES.items():
            with self.subTest(profile=key):
                job = JobConfig(SourceSpec('source.html'))
                job.render.output_profile_key = key
                job.render.cpu_threads = 12
                command = renderer._build_ffmpeg_command(job, Path('out.' + profile.extension), 1, '')
                first_input = command.index('-i')
                positions = [i for i, option in enumerate(command) if option == '-threads:v']
                self.assertEqual(len(positions), 2)
                self.assertLess(positions[0], first_input)
                self.assertGreater(positions[1], first_input)
                self.assertEqual([command[i + 1] for i in positions], ['4', '12'])
                self.assertEqual(command[command.index('-filter_threads') + 1], '8')
                self.assertIn('-noautoscale', command)
                self.assertNotIn('-reinit_filter', command)
                self.assertNotIn('-threads', command)

    def test_exact_master_has_no_enhancement_or_conversion_filter(self):
        renderer = object.__new__(HtmlVideoRenderer)
        renderer._ffmpeg_exe = 'ffmpeg'
        job = RECIPES['exact_source_master'].create_job('source.html')
        job.render.cpu_threads = 8
        command = renderer._build_ffmpeg_command(job, Path('master.mp4'), 1, '')
        self.assertNotIn('-vf', command)
        self.assertEqual(command[command.index('-crf') + 1], '0')

    def test_effective_allocation_is_logged(self):
        renderer = object.__new__(HtmlVideoRenderer)
        renderer._ffmpeg_exe = 'ffmpeg'
        messages = []
        renderer.log_callback = messages.append
        job = JobConfig(SourceSpec('source.html'))
        job.render.cpu_threads = 8
        renderer._build_ffmpeg_command(job, Path('out.mp4'), 1, '')
        self.assertTrue(any('encoder=8, filters=8, decoder=4' in message for message in messages))


class CPUMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = select_ffmpeg({'libx264', 'libx264rgb', 'aac'}, {'scale', 'format', 'cas'})
        cls.ffprobe = shutil.which('ffprobe')
        if not cls.ffprobe:
            raise RuntimeError('ffprobe is required for CPU media regressions.')

    def run_checked(self, command, **kwargs):
        result = subprocess.run(command, capture_output=True, timeout=90, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        return result.stdout

    def encode(self, job, path, frames, chain=''):
        renderer = object.__new__(HtmlVideoRenderer)
        renderer._ffmpeg_exe = self.executable
        command = renderer._build_ffmpeg_command(job, path, len(frames) / job.render.fps, chain)
        self.run_checked(command, input=b''.join(frames))

    def decode(self, path, pixel_format='rgb24'):
        return self.run_checked([self.executable, '-v', 'error', '-xerror', '-err_detect', 'explode',
                                 '-threads', '2', '-i', str(path), '-map', '0:v:0',
                                 '-threads:v', '1', '-f', 'rawvideo', '-pix_fmt', pixel_format, '-'])

    def probe(self, path):
        return json.loads(self.run_checked([self.ffprobe, '-v', 'error', '-count_frames',
                         '-show_streams', '-show_frames', '-of', 'json', str(path)]))

    def frames(self, count=132):
        encoded, raw = [], []
        for index in range(count):
            # Repeated RGB/RGBA switches plus motion; straddles the 120-frame GOP.
            image = Image.new('RGB', (192, 96), (255, 0, 255))
            draw = ImageDraw.Draw(image)
            draw.rectangle((96, 0, 191, 95), fill=(0, 255, 0))
            draw.rectangle((index % 120, 72, index % 120 + 20, 94), fill=(32, 64, 128))
            raw.append(image.tobytes())
            if (index // 11) % 2:
                image = image.convert('RGBA')
            stream = io.BytesIO()
            image.save(stream, 'PNG')
            encoded.append(stream.getvalue())
        return encoded, raw

    def test_multithreaded_delivery_decodes_all_frames_with_stable_color(self):
        frames, _ = self.frames()
        with tempfile.TemporaryDirectory() as directory:
            for threads in (1, 4, 8):
                with self.subTest(threads=threads):
                    job = RECIPES['social_delivery'].create_job('source.html')
                    job.render.cpu_threads = threads
                    path = Path(directory) / f'delivery-{threads}.mp4'
                    self.encode(job, path, frames, 'cas=strength=0.2800')
                    decoded = self.decode(path)
                    stride = 192 * 96 * 3
                    self.assertEqual(len(decoded), len(frames) * stride)
                    for frame in range(len(frames)):
                        for x, expected in ((24, (255, 0, 255)), (144, (0, 255, 0))):
                            offset = frame * stride + (24 * 192 + x) * 3
                            actual = decoded[offset:offset + 3]
                            self.assertLessEqual(max(abs(a - b) for a, b in zip(actual, expected)), 5,
                                                 (threads, frame, tuple(actual), expected))
                    metadata = self.probe(path)
                    video = metadata['streams'][0]
                    self.assertEqual((video['codec_name'], video['profile'], video['pix_fmt']),
                                     ('h264', 'Main', 'yuv420p'))
                    self.assertEqual(int(video['nb_read_frames']), len(frames))
                    self.assertEqual(video['r_frame_rate'], '60/1')
                    timestamps = [float(f['best_effort_timestamp_time']) for f in metadata['frames']
                                  if f['media_type'] == 'video']
                    for index, timestamp in enumerate(timestamps):
                        self.assertAlmostEqual(timestamp, index / 60, places=5)

    def test_multithreaded_exact_rgb_is_byte_exact(self):
        frames, raw = self.frames(33)
        with tempfile.TemporaryDirectory() as directory:
            job = RECIPES['exact_source_master'].create_job('source.html')
            job.render.cpu_threads = 8
            path = Path(directory) / 'exact.mp4'
            self.encode(job, path, frames)
            self.assertEqual(self.decode(path), b''.join(raw))

    def test_multithreaded_delivery_retains_audio_contract(self):
        frames, _ = self.frames(60)
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'sound.wav'
            with wave.open(str(audio), 'wb') as wav:
                wav.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\0\0' * 44100)
            job = RECIPES['social_delivery'].create_job('source.html')
            job.render.cpu_threads = 8
            job.render.audio.mode = AudioMode.TRIM
            job.render.audio.path = str(audio)
            path = Path(directory) / 'audio.mp4'
            self.encode(job, path, frames, 'cas=strength=0.2800')
            self.run_checked([self.executable, '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'])
            streams = self.probe(path)['streams']
            sound = next(s for s in streams if s['codec_type'] == 'audio')
            self.assertEqual((sound['codec_name'], sound['profile'], sound['sample_rate'], sound['channels']),
                             ('aac', 'LC', '48000', 2))


if __name__ == '__main__':
    unittest.main()
