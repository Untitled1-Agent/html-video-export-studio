"""Real codecs, decoded samples, browser renders, and cancellation for audio."""
from __future__ import annotations

import array
import io
import json
import math
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from cli import main
from media_pipeline import validate_audio_stream
from models import AudioConfig, AudioMode, LoadStrategy, TimelineMode
from presets import OUTPUT_PROFILES, RECIPES
from renderer import ExportCancelled, ExportError, HtmlVideoRenderer


def write_wave(path: Path, duration=1.6, lead=0.0, frequency=440, amplitude=0.2):
    rate = 48000
    samples = array.array('h')
    for index in range(round(rate * duration)):
        sample = 0 if index < round(lead * rate) else round(amplitude * 32767 * math.sin(2 * math.pi * frequency * index / rate))
        samples.extend((sample, sample))
    if __import__('sys').byteorder != 'little':
        samples.byteswap()
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.tobytes())


class AudioMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = shutil.which('ffmpeg')
        cls.ffprobe = shutil.which('ffprobe')
        if not cls.ffmpeg or not cls.ffprobe:
            raise RuntimeError('Audio integration tests require FFmpeg and FFprobe.')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.html = self.root / 'input.html'
        self.html.write_text('<svg width="64" height="96" data-video-export><rect width="64" height="96" fill="#e24535"/></svg>', encoding='utf-8')
        self.wav = self.root / 'sound.wav'
        write_wave(self.wav)
        buffer = io.BytesIO()
        Image.new('RGB', (64, 96), '#e24535').save(buffer, format='PNG')
        self.png = buffer.getvalue()
        buffer.close()
        self.counter = 0

    def job(self, path=None, **audio_options):
        job = RECIPES['social_delivery'].create_job(str(self.html))
        job.render.scale = 1
        job.render.fps = 30
        job.render.cpu_threads = 2
        job.render.processing.preset_key = 'no_processing'
        job.source.load_strategy = LoadStrategy.EMBEDDED
        job.timeline.mode = TimelineMode.STATIC
        job.timeline.manual_duration = 1.0
        job.render.audio = AudioConfig(path=str(path or self.wav), mode=AudioMode.TRIM, **audio_options)
        return job

    def encode(self, job=None, duration=1.0):
        job = job or self.job()
        frames = math.ceil(duration * job.render.fps - 1e-9)
        self.counter += 1
        output = self.root / f'encoded-{self.counter}.{OUTPUT_PROFILES[job.render.output_profile_key].extension}'
        renderer = HtmlVideoRenderer.__new__(HtmlVideoRenderer)
        renderer._ffmpeg_exe = self.ffmpeg
        renderer.log_callback = None
        command = renderer._build_ffmpeg_command(job, output, frames / job.render.fps, '')
        result = subprocess.run(command, input=self.png * frames, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        return output

    def probe(self, path):
        result = subprocess.run([self.ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                                capture_output=True, text=True, check=True, timeout=15)
        return json.loads(result.stdout)

    def samples(self, path, channels=1):
        result = subprocess.run([self.ffmpeg, '-v', 'error', '-i', str(path), '-map', '0:a:0',
                                 '-ac', str(channels), '-ar', '48000', '-f', 'f32le', 'pipe:1'],
                                capture_output=True, check=True, timeout=15)
        samples = array.array('f')
        samples.frombytes(result.stdout)
        if __import__('sys').byteorder != 'little':
            samples.byteswap()
        return samples

    @staticmethod
    def rms(samples, start, end):
        window = samples[round(start * 48000):round(end * 48000)]
        return math.sqrt(sum(value * value for value in window) / max(1, len(window)))

    def assert_av(self, path, duration=1.0, codec='aac', rate='48000', channels=2):
        info = self.probe(path)
        videos = [s for s in info['streams'] if s['codec_type'] == 'video']
        audios = [s for s in info['streams'] if s['codec_type'] == 'audio']
        self.assertEqual((len(videos), len(audios)), (1, 1))
        self.assertEqual(audios[0]['codec_name'], codec)
        self.assertEqual(audios[0]['sample_rate'], rate)
        self.assertEqual(audios[0]['channels'], channels)
        self.assertAlmostEqual(float(info['format']['duration']), duration, delta=0.05)
        for stream in (videos[0], audios[0]):
            if 'duration' in stream:
                self.assertAlmostEqual(float(stream['duration']), duration, delta=0.05)
        return videos[0], audios[0]

    def formats(self):
        tracks = [self.wav]
        for suffix, codec in (('.mp3', 'libmp3lame'), ('.m4a', 'aac')):
            path = self.root / ('sound' + suffix)
            subprocess.run([self.ffmpeg, '-v', 'error', '-y', '-i', str(self.wav), '-c:a', codec, str(path)],
                           capture_output=True, check=True, timeout=15)
            tracks.append(path)
        return tracks

    def test_wav_mp3_m4a_reencode_to_social_aac_lc(self):
        for track in self.formats():
            with self.subTest(track=track.name):
                validate_audio_stream(self.ffmpeg, str(track))
                output = self.encode(self.job(track))
                video, audio = self.assert_av(output)
                self.assertEqual(video['codec_name'], 'h264')
                self.assertEqual(video['pix_fmt'], 'yuv420p')
                self.assertEqual(video['codec_tag_string'], 'avc1')
                self.assertEqual(audio['profile'], 'LC')
                self.assertGreater(self.rms(self.samples(output), 0.2, 0.5), 0.05)
                data = output.read_bytes()
                atoms = []
                offset = 0
                while offset + 8 <= len(data):
                    size, kind = struct.unpack('>I4s', data[offset:offset + 8])
                    atoms.append(kind)
                    if size < 8:
                        break
                    offset += size
                self.assertLess(atoms.index(b'moov'), atoms.index(b'mdat'))

    def test_signed_offsets_move_both_channels_by_sample_content(self):
        lead = self.root / 'leading.wav'
        write_wave(lead, lead=0.3)
        for offset, expected in ((-0.2, 0.1), (0, 0.3), (0.2, 0.5)):
            with self.subTest(offset=offset):
                output = self.encode(self.job(lead, offset_seconds=offset))
                self.assert_av(output)
                stereo = self.samples(output, channels=2)
                for channel in (0, 1):
                    samples = stereo[channel::2]
                    onset = next(i / 48000 for i in range(0, 45000, 960)
                                 if self.rms(samples, i / 48000, (i + 960) / 48000) > 0.02)
                    self.assertAlmostEqual(onset, expected, delta=0.04)

    def test_offsets_beyond_either_end_produce_full_length_silence(self):
        for offset in (-3.0, 2.0):
            with self.subTest(offset=offset):
                output = self.encode(self.job(offset_seconds=offset))
                self.assert_av(output)
                self.assertLess(self.rms(self.samples(output), 0.1, 0.9), 0.0001)

    def test_short_track_pads_or_loops_without_shortening_video(self):
        short = self.root / 'short.wav'
        write_wave(short, duration=0.2)
        once = self.encode(self.job(short))
        job = self.job(short)
        job.render.audio.mode = AudioMode.LOOP
        looped = self.encode(job)
        self.assert_av(once)
        self.assert_av(looped)
        self.assertLess(self.rms(self.samples(once), 0.7, 0.9), 0.001)
        self.assertGreater(self.rms(self.samples(looped), 0.7, 0.9), 0.08)

    def test_looping_negative_offset_still_has_audio(self):
        short = self.root / 'loop.wav'
        write_wave(short, duration=0.2)
        job = self.job(short, offset_seconds=-0.55)
        job.render.audio.mode = AudioMode.LOOP
        output = self.encode(job)
        self.assert_av(output)
        self.assertGreater(self.rms(self.samples(output), 0.05, 0.2), 0.08)
        self.assertGreater(self.rms(self.samples(output), 0.7, 0.9), 0.08)

    def test_compressed_tracks_loop_after_negative_seek(self):
        for track in self.formats():
            with self.subTest(track=track.name):
                job = self.job(track, offset_seconds=-1.8)
                job.render.audio.mode = AudioMode.LOOP
                output = self.encode(job)
                self.assert_av(output)
                self.assertGreater(self.rms(self.samples(output), 0.1, 0.9), 0.05)

    def test_gain_and_mute_affect_decoded_samples(self):
        original = self.samples(self.encode())
        half = self.samples(self.encode(self.job(volume=0.5)))
        muted = self.samples(self.encode(self.job(volume=0.0)))
        ratio = self.rms(half, 0.2, 0.8) / self.rms(original, 0.2, 0.8)
        self.assertAlmostEqual(ratio, 0.5, delta=0.05)
        self.assertLess(self.rms(muted, 0.1, 0.9), 0.0001)

    def test_fades_and_delayed_fade_in_are_audible_not_hidden_in_silence(self):
        samples = self.samples(self.encode(self.job(fade_in_seconds=0.3, fade_out_seconds=0.3)))
        middle = self.rms(samples, 0.4, 0.6)
        self.assertLess(self.rms(samples, 0.01, 0.05), middle * 0.3)
        self.assertLess(self.rms(samples, 0.95, 0.99), middle * 0.3)
        delayed = self.samples(self.encode(self.job(offset_seconds=0.4, fade_in_seconds=0.3)))
        self.assertLess(self.rms(delayed, 0.1, 0.3), 0.001)
        self.assertLess(self.rms(delayed, 0.41, 0.45), self.rms(delayed, 0.8, 0.9) * 0.3)

    def test_normalization_and_encoding_overrides(self):
        quiet = self.root / 'quiet.wav'
        write_wave(quiet, amplitude=0.01)
        output = self.encode(self.job(quiet, normalize_loudness=True, bitrate_kbps=128,
                                     sample_rate_hz=44100, channels=1))
        self.assert_av(output, rate='44100', channels=1)
        self.assertGreater(self.rms(self.samples(output), 0.2, 0.8), 0.02)

    def test_all_video_profiles_keep_container_appropriate_audio(self):
        for key, profile in OUTPUT_PROFILES.items():
            with self.subTest(profile=key):
                job = self.job()
                job.render.output_profile_key = key
                output = self.encode(job, duration=0.3)
                expected = 'opus' if profile.audio_codec == 'libopus' else profile.audio_codec
                self.assert_av(output, duration=0.3, codec=expected)

    def test_disabled_audio_stays_video_only(self):
        job = self.job()
        job.render.audio.mode = AudioMode.NONE
        output = self.encode(job)
        self.assertEqual([s['codec_type'] for s in self.probe(output)['streams']], ['video'])

    def test_preflight_rejects_corrupt_and_audio_less_inputs(self):
        corrupt = self.root / 'broken.mp3'
        corrupt.write_bytes(b'not audio')
        with self.assertRaisesRegex(ValueError, 'decodable audio'):
            validate_audio_stream(self.ffmpeg, str(corrupt))
        silent = self.job()
        silent.render.audio.mode = AudioMode.NONE
        video = self.encode(silent)
        audio_less = self.root / 'video-only.m4a'
        audio_less.write_bytes(video.read_bytes())
        with self.assertRaisesRegex(ValueError, 'decodable audio'):
            validate_audio_stream(self.ffmpeg, str(audio_less))

    def test_real_browser_renders_all_input_formats_with_frame_rounded_audio(self):
        with HtmlVideoRenderer() as renderer:
            for track in self.formats():
                with self.subTest(track=track.name):
                    job = self.job(track, offset_seconds=-0.02)
                    job.timeline.manual_duration = 0.23
                    output = self.root / (track.suffix[1:] + '-browser.mp4')
                    result = renderer.render(job, output_path=output)
                    self.assertEqual(result.frame_count, 7)
                    video, audio = self.assert_av(result.output_path, duration=7 / 30)
                    self.assertEqual(video['nb_frames'], '7')
                    self.assertGreater(self.rms(self.samples(output), 0.05, 0.2), 0.05)

    def test_real_cli_batch_maps_tracks_and_renders_audio(self):
        other = self.root / 'other.html'
        other.write_text(self.html.read_text(encoding='utf-8'), encoding='utf-8')
        other_track = self.root / 'different.wav'
        write_wave(other_track, frequency=880)
        output = self.root / 'cli-output'
        with patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO()) as errors:
            code = main([str(self.html), str(other), '--audio-map', str(self.html), str(self.wav),
                         '--audio-map', str(other), str(other_track), '--timeline', 'static',
                         '--duration', '0.2', '--fps', '30', '--scale', '1', '--processing', 'no_processing',
                         '--output', str(output), '--audio-offset', '-0.02'])
        self.assertEqual(code, 0, errors.getvalue())
        paths = sorted(output.glob('*.mp4'))
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assert_av(path, duration=0.2)

    def test_failed_or_cancelled_render_does_not_publish_or_replace_output(self):
        output = self.root / 'protected.mp4'
        output.write_bytes(b'previous completed video')
        corrupt = self.root / 'broken.m4a'
        corrupt.write_bytes(b'broken')
        job = self.job(corrupt)
        job.render.overwrite = True
        with HtmlVideoRenderer() as renderer:
            with self.assertRaises(ExportError):
                renderer.render(job, output_path=output)
            event = threading.Event()
            job = self.job()
            job.render.audio.mode = AudioMode.LOOP
            job.render.overwrite = True
            def progress(value):
                if value['frame'] >= 2:
                    event.set()
            with self.assertRaises(ExportCancelled):
                renderer.render(job, output_path=output, cancel_event=event, progress_callback=progress)
        self.assertEqual(output.read_bytes(), b'previous completed video')
        self.assertFalse(list(self.root.glob('.hves-partial-*')))


if __name__ == '__main__':
    unittest.main()
