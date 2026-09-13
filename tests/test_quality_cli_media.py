"""Fresh end-to-end CLI/browser/media regression for Social H.264, HEVC, and AV1."""
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
from unittest.mock import patch

from cli import main


@unittest.skipUnless(shutil.which("ffprobe") and shutil.which("ffmpeg"), "FFmpeg/ffprobe required")
class QualityCLIMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.html = self.root / "quality.html"
        self.html.write_text(
            """<!doctype html><html><body style='margin:0;background:#151515'>
<svg width='96' height='160' data-video-export xmlns='http://www.w3.org/2000/svg'>
  <rect width='96' height='160' fill='#151515'/>
  <rect x='8' y='18' width='80' height='44' rx='8' fill='#ff4b2b'/>
  <text x='12' y='48' font-size='16' font-family='sans-serif' fill='white'>Syncnema</text>
  <circle cx='48' cy='108' r='25' fill='#34d399'/>
</svg></body></html>""",
            encoding="utf-8",
        )
        self.wav = self.root / "cue.wav"
        rate = 48000
        with wave.open(str(self.wav), "wb") as output:
            output.setparams((2, 2, rate, 0, "NONE", "not compressed"))
            frames = bytearray()
            for i in range(round(rate * 0.4)):
                value = int(6000 * math.sin(2 * math.pi * 330 * i / rate))
                frames += struct.pack("<hh", value, value)
            output.writeframes(frames)

    def probe(self, path: Path):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-count_frames", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    def assert_media(self, path: Path, codec: str, tag: str):
        info = self.probe(path)
        video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
        audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
        self.assertEqual(video["codec_name"], codec)
        self.assertEqual(video["codec_tag_string"], tag)
        self.assertEqual(video["pix_fmt"], "yuv420p")
        self.assertEqual(video["avg_frame_rate"], "30/1")
        self.assertEqual(video["nb_read_frames"], "8")
        self.assertEqual((video["color_range"], video["color_space"], video["color_primaries"]), ("tv", "bt709", "bt709"))
        self.assertEqual(video["color_transfer"], "iec61966-2-1")
        self.assertEqual((audio["codec_name"], audio["profile"], audio["sample_rate"], audio["channels"]), ("aac", "LC", "48000", 2))
        self.assertAlmostEqual(float(info["format"]["duration"]), 8 / 30, delta=0.03)
        decoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0", "-f", "null", "-"],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors="replace"))
        self.assertFalse(decoded.stderr)
        data = path.read_bytes()
        boxes = []
        offset = 0
        while offset + 8 <= len(data):
            size, kind = struct.unpack_from(">I4s", data, offset)
            if size == 1:
                size = struct.unpack_from(">Q", data, offset + 8)[0]
            elif size == 0:
                size = len(data) - offset
            if size < 8 or offset + size > len(data):
                break
            boxes.append(kind)
            offset += size
        self.assertLess(boxes.index(b"moov"), boxes.index(b"mdat"))

    def render(self, profile: str, name: str):
        output = self.root / name
        stderr = io.StringIO()
        with patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=stderr):
            code = main([
                str(self.html),
                "--timeline", "static",
                "--duration", "0.25",
                "--fps", "30",
                "--scale", "1",
                "--processing", "no_processing",
                "--profile", profile,
                "--audio", str(self.wav),
                "--audio-fade-out", "0.05",
                "--output", str(output),
                "--overwrite",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertTrue(output.is_file())
        return output

    def test_real_cli_browser_renders_h264_hevc_and_av1_with_audio(self):
        h264 = self.render("h264_420_mp4", "social-h264.mp4")
        hevc = self.render("h265_420_mp4", "hevc.mp4")
        av1 = self.render("av1_420_mp4", "av1.mp4")
        self.assert_media(h264, "h264", "avc1")
        self.assert_media(hevc, "hevc", "hvc1")
        self.assert_media(av1, "av1", "av01")


if __name__ == "__main__":
    unittest.main()
