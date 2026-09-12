"""Temporary end-to-end workloads for PR #6 review; removed after verification."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path

from media_pipeline import snapshot_for_export
from models import AudioMode, LoadStrategy, TimelineMode, dataclass_to_dict, job_config_from_dict
from presets import PROCESSING_PRESETS, RECIPES, apply_recipe
from renderer import HtmlVideoRenderer


HTML = '''<!doctype html>
<meta charset="utf-8">
<style>
html,body{margin:0;background:#07111d}#stage{width:320px;height:180px;background:#11263d;overflow:hidden}
</style>
<div id="stage" data-video-export data-video-duration="1.5" data-video-export-parallel-safe="true">
  <svg width="320" height="180" viewBox="0 0 320 180">
    <rect width="106" height="180" fill="#ff2a8a"/>
    <rect x="106" width="107" height="180" fill="#20d070"/>
    <rect x="213" width="107" height="180" fill="#22bce8"/>
    <rect id="moving" x="8" y="70" width="44" height="40" rx="6" fill="white"/>
  </svg>
</div>
<script src="anim.js"></script>
'''

JS = '''
window.seekTo = (t) => {
  const x = 8 + Math.round((Math.max(0, Math.min(1.5, t)) / 1.5) * 260);
  document.querySelector('#moving').setAttribute('x', String(x));
};
'''


def write_fixture(root: Path) -> Path:
    source = root / 'scene.html'
    source.write_text(HTML, encoding='utf-8')
    (root / 'anim.js').write_text(JS, encoding='utf-8')
    return source


def write_tone(path: Path, seconds: float = 0.43, rate: int = 48000) -> None:
    frames = int(seconds * rate)
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        payload = bytearray()
        for i in range(frames):
            sample = int(0.35 * 32767 * math.sin(2 * math.pi * 523.25 * i / rate))
            payload.extend(struct.pack('<h', sample))
        wav.writeframes(payload)


def decode_rgb(ffmpeg: str, path: Path, width: int, height: int) -> tuple[bytes, int]:
    proc = subprocess.run(
        [ffmpeg, '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'rawvideo',
         '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1'],
        capture_output=True, timeout=120,
    )
    if proc.returncode:
        raise AssertionError(proc.stderr.decode('utf-8', errors='replace'))
    stride = width * height * 3
    if len(proc.stdout) % stride:
        raise AssertionError('Decoded RGB payload is not frame aligned.')
    return proc.stdout, len(proc.stdout) // stride


class ReviewWorkloads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = write_fixture(self.root)

    def test_workload_cli_parallel_capture_with_looped_audio(self):
        """CLI + loopback assets + 4 browser lanes + AAC loop/offset/fades."""
        if shutil.which('ffprobe') is None:
            self.fail('ffprobe is required by the Linux integration workload')
        tone = self.root / 'tone.wav'
        output = self.root / 'delivery.mp4'
        write_tone(tone)
        cmd = [
            sys.executable, 'cli.py', str(self.source), '--output', str(output),
            '--recipe', 'social_delivery', '--fps', '24', '--cpu-threads', '2',
            '--capture-workers', '4', '--frame-buffer-mb', '64',
            '--timeline', 'javascript_function', '--seek-function', 'seekTo',
            '--load', 'loopback_http', '--profile', 'h264_420_mp4',
            '--processing', 'no_processing', '--trim-start', '0.10', '--trim-end', '1.30',
            '--hold-start', '0.20', '--hold-end', '0.20',
            '--audio', str(tone), '--audio-mode', 'loop', '--audio-volume', '0.7',
            '--audio-offset', '0.10', '--audio-fade-in', '0.10', '--audio-fade-out', '0.15',
            '--audio-bitrate', '128', '--audio-sample-rate', '48000', '--audio-channels', '2',
            '--overwrite',
        ]
        proc = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-6000:])
        self.assertTrue(output.is_file() and output.stat().st_size > 1000)
        self.assertIn('Browser capture: 4 worker(s)', proc.stderr)
        self.assertIn('Render timings:', proc.stderr)

        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-show_format',
             '-of', 'json', str(output)], capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(probe.returncode, 0, msg=probe.stderr)
        info = json.loads(probe.stdout)
        videos = [s for s in info['streams'] if s.get('codec_type') == 'video']
        audios = [s for s in info['streams'] if s.get('codec_type') == 'audio']
        self.assertEqual(len(videos), 1)
        self.assertEqual(len(audios), 1)
        video, audio = videos[0], audios[0]
        self.assertEqual((video.get('width'), video.get('height')), (320, 180))
        self.assertEqual(audio.get('sample_rate'), '48000')
        self.assertEqual(audio.get('channels'), 2)
        self.assertGreater(int(video.get('nb_read_frames') or 0), 30)
        video_duration = float(video.get('duration') or info['format']['duration'])
        audio_duration = float(audio.get('duration') or info['format']['duration'])
        self.assertLess(abs(video_duration - audio_duration), 0.12)
        self.assertGreater(video_duration, 1.5)
        self.assertLess(video_duration, 1.75)

    def test_workload_loopback_parallel_matches_sequential_lossless(self):
        """Independent loopback/browser sessions must reproduce the sequential master byte-for-byte after decode."""
        job = RECIPES['exact_source_master'].create_job(str(self.source))
        job.source.load_strategy = LoadStrategy.LOOPBACK_HTTP
        job.render.scale = 1
        job.render.fps = 20
        job.render.cpu_threads = 2
        job.render.processing = PROCESSING_PRESETS['no_processing'].to_config()
        job.render.output_profile_key = 'lossless_rgb_mp4'
        job.timeline.mode = TimelineMode.JAVASCRIPT_FUNCTION
        job.timeline.javascript_function = 'seekTo'
        job.timeline.trim_start = 0.10
        job.timeline.trim_end = 1.10
        job.timeline.hold_start = 0.10
        job.timeline.hold_end = 0.15
        job.render.fast_capture = True
        job.render.frame_buffer_mb = 64

        decoded = []
        stats_seen = []
        for workers, name in [(1, 'serial.mp4'), (4, 'parallel.mp4')]:
            run = job_config_from_dict(dataclass_to_dict(job))
            run.render.capture_workers = workers
            with HtmlVideoRenderer(page_timeout_ms=20000) as renderer:
                result = renderer.render(run, self.root / name)
                raw, frames = decode_rgb(renderer._ffmpeg_exe, result.output_path, 320, 180)
                decoded.append(raw)
                stats_seen.append(dict(renderer.last_render_stats))
                self.assertEqual(frames, result.frame_count)
                self.assertEqual(renderer.last_render_stats['capture_workers'], workers)
                self.assertGreater(renderer.last_render_stats['end_to_end_fps'], 0)
        self.assertEqual(hashlib.sha256(decoded[0]).digest(), hashlib.sha256(decoded[1]).digest())
        self.assertEqual(decoded[0], decoded[1])
        self.assertFalse(any(t.name.startswith('hves-capture-') for t in threading.enumerate()))

        # Exercise the exact merge-sensitive recipe path: capture and audio settings
        # must survive a recipe change while runtime-only concurrency context does not persist.
        job.render.capture_workers = 4
        job.render.frame_buffer_mb = 96
        job.render.fast_capture = False
        job.render.audio.mode = AudioMode.NONE
        changed = apply_recipe(job, 'social_delivery')
        self.assertEqual((changed.render.capture_workers, changed.render.frame_buffer_mb,
                          changed.render.fast_capture), (4, 96, False))
        snapshot = snapshot_for_export(changed, 2)
        self.assertNotIn('_concurrent_exports', dataclass_to_dict(snapshot.render))


if __name__ == '__main__':
    unittest.main()
