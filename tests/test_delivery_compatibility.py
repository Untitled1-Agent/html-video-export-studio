"""Observable regressions for default playback/upload compatibility and clarity.

Codec tests use the real renderer command builder and FFmpeg/ffprobe, not a
mock encoder. They do not certify a particular Android device or TikTok account.
"""
from __future__ import annotations

import io
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from cli import build_parser, make_job
from media_pipeline import color_pipeline, select_ffmpeg
from models import (AudioMode, DEFAULT_RECIPE_KEY, JobConfig, ProcessingConfig,
                    RenderConfig, SourceSpec, dataclass_to_dict, job_config_from_dict)
from presets import OUTPUT_PROFILES, PROCESSING_PRESETS, RECIPES
from processing import build_filter_chain, resolve_processing_config
from renderer import HtmlVideoRenderer
from settings_store import AppSettings, load_settings, save_settings


class DeliveryDefaultsTests(unittest.TestCase):
    def test_cli_desktop_and_api_defaults_agree(self):
        args = build_parser().parse_args(['source.html'])
        cli_job = make_job('source.html', args)
        desktop_job = RECIPES[AppSettings().default_recipe_key].create_job('source.html')
        api_job = JobConfig(SourceSpec('source.html'))
        for job in (cli_job, desktop_job, api_job):
            with self.subTest(entrypoint=job.source.value):
                self.assertEqual(job.recipe_key, DEFAULT_RECIPE_KEY)
                self.assertEqual(job.render.output_profile_key, 'h264_420_mp4')
                self.assertEqual(job.render.scale, 1.0)
                self.assertEqual(job.render.fps, 60)
                self.assertEqual(job.render.processing, ProcessingConfig())
        self.assertEqual(RenderConfig().processing, desktop_job.render.processing)

    def test_default_clarity_does_not_depend_on_auto_analysis(self):
        processing = resolve_processing_config(ProcessingConfig(), None)
        self.assertEqual(processing.preset_key, 'social_compensation')
        self.assertEqual(build_filter_chain(processing), 'cas=strength=0.2800')
        self.assertGreater(processing.sharpen_strength, PROCESSING_PRESETS['ui_subtle'].sharpen_strength)
        self.assertEqual((processing.contrast, processing.saturation, processing.brightness), (1, 1, 0))
        self.assertFalse(processing.deband)

    def test_explicit_master_and_no_processing_are_preserved(self):
        args = build_parser().parse_args(['source.html', '--recipe', 'exact_source_master'])
        job = make_job('source.html', args)
        self.assertEqual(job.render.output_profile_key, 'lossless_rgb_mp4')
        self.assertEqual(job.render.scale, 2.0)
        self.assertEqual(build_filter_chain(job.render.processing), '')
        self.assertEqual(color_pipeline('', rgb_output=True), '')
        self.assertEqual(job_config_from_dict(dataclass_to_dict(job)), job)

    def test_explicit_cli_overrides_are_preserved(self):
        args = build_parser().parse_args(['source.html', '--profile', 'h264_444_mp4',
                                         '--processing', 'no_processing', '--scale', '2', '--fps', '30'])
        job = make_job('source.html', args)
        self.assertEqual((job.render.output_profile_key, job.render.scale, job.render.fps),
                         ('h264_444_mp4', 2.0, 30))
        self.assertEqual(build_filter_chain(job.render.processing), '')

    def test_legacy_factory_preference_is_migrated_without_losing_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'format_version': 2, 'default_recipe_key': 'motion_graphics_master',
                                        'workers': 3, 'recent_sources': ['source.html'], 'overwrite': True}))
            settings = load_settings(path)
            self.assertEqual(settings.default_recipe_key, DEFAULT_RECIPE_KEY)
            self.assertEqual(settings.workers, 3)
            self.assertEqual(settings.recent_sources, ['source.html'])
            self.assertTrue(settings.overwrite)
            save_settings(settings, path)
            self.assertEqual(json.loads(path.read_text())['delivery_defaults_version'], 1)
            self.assertEqual(load_settings(path), settings)

    def test_new_explicit_master_preference_survives_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = AppSettings(default_recipe_key='motion_graphics_master')
            save_settings(settings, path)
            self.assertEqual(load_settings(path).default_recipe_key, 'motion_graphics_master')

    def test_other_legacy_preferences_are_not_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            for recipe in ('exact_source_master', 'editing_master', 'website_capture'):
                with self.subTest(recipe=recipe):
                    path.write_text(json.dumps({'default_recipe_key': recipe}))
                    self.assertEqual(load_settings(path).default_recipe_key, recipe)

    def test_missing_or_invalid_preference_uses_safe_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            self.assertEqual(load_settings(path).default_recipe_key, DEFAULT_RECIPE_KEY)
            for recipe in (None, [], 'deleted_recipe'):
                with self.subTest(recipe=recipe):
                    path.write_text(json.dumps({'default_recipe_key': recipe}))
                    self.assertEqual(load_settings(path).default_recipe_key, DEFAULT_RECIPE_KEY)


@unittest.skipUnless(shutil.which('ffprobe'), 'ffprobe is required for decoded media regressions')
class DeliveryMediaTests(unittest.TestCase):
    WIDTH, HEIGHT, FPS, FRAMES = 256, 144, 60, 240
    COLORS = ((0, 0, 0), (255, 255, 255), (128, 128, 128), (255, 0, 0),
              (0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 0, 255))

    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = select_ffmpeg({'libx264', 'libx264rgb', 'aac'}, {'cas', 'scale', 'format'})
        cls.ffprobe = shutil.which('ffprobe')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.renderer = object.__new__(HtmlVideoRenderer)
        cls.renderer._ffmpeg_exe = cls.ffmpeg
        cls.default_job = RECIPES[DEFAULT_RECIPE_KEY].create_job('source.html')
        cls.video = cls.directory / 'default.mp4'
        cls.encode(cls.default_job, cls.video, [cls.color_frame(i) for i in range(cls.FRAMES)])

    @classmethod
    def color_frame(cls, index):
        image = Image.new('RGB', (cls.WIDTH, cls.HEIGHT))
        draw = ImageDraw.Draw(image)
        colors = cls.COLORS if index < cls.FRAMES // 2 else cls.COLORS[::-1]
        for i, color in enumerate(colors):
            draw.rectangle((i * 32, 0, (i + 1) * 32 - 1, 95), fill=color)
        # Changing detail exercises inter-frame prediction, not just an I-frame.
        draw.rectangle((index % 224, 104, index % 224 + 31, 135), fill=(180, 100, 60))
        # Chromium can return RGB or RGBA PNGs. Keep the size constant while
        # changing PNG format at the midpoint to catch reconfiguration regressions.
        if index >= cls.FRAMES // 2:
            image = image.convert('RGBA')
        return image

    @classmethod
    def encode(cls, job, path, images):
        processing = resolve_processing_config(job.render.processing, None)
        command = cls.renderer._build_ffmpeg_command(job, path, len(images) / job.render.fps,
                                                     build_filter_chain(processing))
        data = io.BytesIO()
        for image in images:
            image.save(data, format='PNG')
        result = subprocess.run(command, input=data.getvalue(), capture_output=True, timeout=45)
        if result.returncode or result.stderr:
            raise AssertionError(f'Encoder failed: {result.stderr.decode(errors="replace")}')

    @classmethod
    def probe(cls, path, *args):
        result = subprocess.run([cls.ffprobe, '-v', 'error', *args, '-of', 'json', str(path)],
                                capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout)

    @classmethod
    def decode(cls, path):
        result = subprocess.run([cls.ffmpeg, '-v', 'error', '-xerror', '-i', str(path),
                                 '-map', '0:v:0', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                                capture_output=True, check=True, timeout=30)
        if result.stderr:
            raise AssertionError(result.stderr.decode(errors='replace'))
        size = cls.WIDTH * cls.HEIGHT * 3
        if len(result.stdout) % size:
            raise AssertionError('Decoded an incomplete frame')
        return [Image.frombytes('RGB', (cls.WIDTH, cls.HEIGHT), result.stdout[i:i + size])
                for i in range(0, len(result.stdout), size)]

    def test_default_bitstream_is_8bit_main_avc_not_high444_rgb(self):
        streams = self.probe(self.video, '-show_streams', '-count_frames')['streams']
        self.assertEqual(len(streams), 1)  # No unsolicited silent audio track.
        video = streams[0]
        self.assertEqual(video['codec_name'], 'h264')
        self.assertEqual(video['codec_tag_string'], 'avc1')
        self.assertEqual(video['profile'], 'Main')
        self.assertEqual(video['pix_fmt'], 'yuv420p')
        self.assertEqual(int(video['nb_read_frames']), self.FRAMES)
        self.assertEqual(video['avg_frame_rate'], '60/1')
        self.assertEqual(video['color_range'], 'tv')
        self.assertEqual(video['color_space'], 'bt709')
        self.assertEqual(video['color_primaries'], 'bt709')
        # Screenshot samples are sRGB. Do not relabel their transfer as BT.709
        # without actually transforming the pixels.
        self.assertEqual(video['color_transfer'], 'iec61966-2-1')
        self.assertAlmostEqual(float(video['duration']), self.FRAMES / self.FPS, places=5)

    def test_all_frames_decode_with_stable_colors_across_midpoint_and_gop(self):
        frames = self.decode(self.video)
        self.assertEqual(len(frames), self.FRAMES)
        for index, image in enumerate(frames):
            colors = self.COLORS if index < self.FRAMES // 2 else self.COLORS[::-1]
            for i, color in enumerate(colors):
                actual = image.getpixel((i * 32 + 16, 48))
                self.assertLessEqual(max(abs(a - b) for a, b in zip(actual, color)), 5,
                                     (index, i, actual, color))

    def test_timestamps_are_constant_and_mp4_header_precedes_media(self):
        frames = self.probe(self.video, '-select_streams', 'v:0', '-show_frames',
                            '-show_entries', 'frame=best_effort_timestamp_time,key_frame')['frames']
        self.assertEqual(len(frames), self.FRAMES)
        for index, frame in enumerate(frames):
            self.assertAlmostEqual(float(frame['best_effort_timestamp_time']), index / self.FPS, places=5)
        keys = [index for index, frame in enumerate(frames) if frame['key_frame']]
        self.assertEqual(keys[0], 0)
        self.assertGreaterEqual(len(keys), 2)
        self.assertLessEqual(max(b - a for a, b in zip(keys, keys[1:])), 120)
        data = self.video.read_bytes()
        boxes = []
        offset = 0
        while offset < len(data):
            size, kind = struct.unpack_from('>I4s', data, offset)
            if size == 1:
                size = struct.unpack_from('>Q', data, offset + 8)[0]
            elif size == 0:
                size = len(data) - offset
            self.assertGreaterEqual(size, 8)
            boxes.append(kind)
            offset += size
        self.assertEqual(offset, len(data))
        self.assertLess(boxes.index(b'moov'), boxes.index(b'mdat'))

    def test_soundtrack_is_aac_lc_stereo_48khz(self):
        audio_path = self.directory / 'soundtrack.wav'
        with wave.open(str(audio_path), 'wb') as audio:
            audio.setparams((1, 2, 96000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b''.join(struct.pack('<h', int(4000 * math.sin(2 * math.pi * 440 * i / 96000)))
                                       for i in range(48000)))
        job = RECIPES[DEFAULT_RECIPE_KEY].create_job('source.html')
        job.render.audio.mode = AudioMode.TRIM
        job.render.audio.path = str(audio_path)
        output = self.directory / 'with-audio.mp4'
        self.encode(job, output, [self.color_frame(0)] * 30)
        streams = self.probe(output, '-show_streams')['streams']
        audio = next(s for s in streams if s['codec_type'] == 'audio')
        self.assertEqual((audio['codec_name'], audio['profile']), ('aac', 'LC'))
        self.assertEqual((int(audio['sample_rate']), audio['channels']), (48000, 2))
        result = subprocess.run([self.ffmpeg, '-v', 'error', '-xerror', '-i', str(output),
                                 '-map', '0', '-f', 'null', '-'], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stderr)

    def test_default_sharpening_increases_edge_definition(self):
        image = Image.new('RGB', (self.WIDTH, self.HEIGHT), (64, 64, 64))
        draw = ImageDraw.Draw(image)
        for x in range(16, self.WIDTH - 16, 32):
            draw.rectangle((x, 16, x + 15, self.HEIGHT - 17), fill=(192, 192, 192))
        image = image.filter(ImageFilter.GaussianBlur(0.8))
        gradients = []
        for preset in ('no_processing', 'social_compensation'):
            job = RECIPES[DEFAULT_RECIPE_KEY].create_job('source.html')
            job.render.processing = PROCESSING_PRESETS[preset].to_config()
            output = self.directory / f'{preset}.mp4'
            self.encode(job, output, [image] * 4)
            decoded = self.decode(output)[0]
            row = [decoded.getpixel((x, 72))[0] for x in range(self.WIDTH)]
            gradients.append(max(abs(b - a) for a, b in zip(row, row[1:])))
        self.assertGreater(gradients[1], gradients[0], gradients)

    def test_exact_rgb_master_still_round_trips_pixels(self):
        job = RECIPES['exact_source_master'].create_job('source.html')
        image = self.color_frame(0)
        output = self.directory / 'exact.mp4'
        self.encode(job, output, [image] * 4)
        self.assertEqual(self.decode(output)[0].tobytes(), image.tobytes())
        video = self.probe(output, '-show_streams')['streams'][0]
        self.assertEqual(video['profile'], 'High 4:4:4 Predictive')
        self.assertEqual(video['pix_fmt'], 'gbrp')


if __name__ == '__main__':
    unittest.main()
