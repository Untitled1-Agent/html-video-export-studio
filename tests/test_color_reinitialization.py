"""Exercise color conversion across repeated PNG pixel-format changes.

Run against both the system and bundled FFmpeg when available. A passing test
on one FFmpeg version does not establish correctness on the other.
"""
from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from media_pipeline import capabilities, color_pipeline, ffmpeg_candidates, filter_names


class ColorReinitializationTests(unittest.TestCase):
    WIDTH, HEIGHT = 256, 64
    COLORS = ((0, 0, 0), (255, 255, 255), (128, 128, 128), (255, 0, 0),
              (0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 0, 255))
    MODES = ('RGB', 'RGB', 'RGBA', 'RGBA', 'RGB', 'RGBA', 'RGB', 'RGBA')

    @classmethod
    def setUpClass(cls):
        cls.executables = ffmpeg_candidates()
        if not cls.executables:
            raise RuntimeError('FFmpeg is required for color regressions.')
        image = Image.new('RGB', (cls.WIDTH, cls.HEIGHT))
        draw = ImageDraw.Draw(image)
        for index, color in enumerate(cls.COLORS):
            draw.rectangle((index * 32, 0, (index + 1) * 32 - 1, cls.HEIGHT - 1), fill=color)
        cls.reference = image.tobytes()
        stream = io.BytesIO()
        for mode in cls.MODES:
            image.convert(mode).save(stream, format='PNG')
        cls.pngs = stream.getvalue()

    def check_sequence(self, processing, rgb_output=False, exact=False):
        chain = color_pipeline(processing, rgb_output)
        codec = 'libx264rgb' if rgb_output else 'libx264'
        size = self.WIDTH * self.HEIGHT * 3
        for executable in self.executables:
            with self.subTest(ffmpeg=executable, processing=processing, rgb_output=rgb_output):
                available = capabilities(executable)
                self.assertIn(codec, available['encoders'])
                self.assertFalse(filter_names(chain) - available['filters'])
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'sequence.mp4'
                    command = [executable, '-hide_banner', '-loglevel', 'error', '-y',
                               '-filter_threads', '1', '-threads', '2', '-f', 'image2pipe',
                               '-framerate', '30', '-c:v', 'png', '-i', 'pipe:0']
                    if chain:
                        command += ['-vf', chain]
                    # Match the fixed-geometry export contract, which is also
                    # guarded in test_output_autoscale for the real command builder.
                    command += ['-noautoscale', '-c:v', codec, '-threads:v', '2', '-crf', '0' if exact else '12',
                                '-pix_fmt', 'rgb24' if rgb_output else 'yuv420p',
                                '-colorspace', 'rgb' if rgb_output else 'bt709',
                                '-color_range', 'pc' if rgb_output else 'tv',
                                '-color_primaries', 'bt709', '-color_trc', 'iec61966-2-1',
                                '-an', str(path)]
                    encoded = subprocess.run(command, input=self.pngs, capture_output=True, timeout=30)
                    self.assertEqual(encoded.returncode, 0, encoded.stderr)
                    self.assertFalse(encoded.stderr)
                    decoded = subprocess.run([executable, '-v', 'error', '-xerror', '-i', str(path),
                                              '-threads:v', '2', '-pix_fmt', 'rgb24',
                                              '-f', 'rawvideo', '-'], capture_output=True, timeout=30)
                    self.assertEqual(decoded.returncode, 0, decoded.stderr)
                    self.assertFalse(decoded.stderr)
                    self.assertEqual(len(decoded.stdout), len(self.MODES) * size)
                    for index in range(len(self.MODES)):
                        frame = decoded.stdout[index * size:(index + 1) * size]
                        if exact:
                            self.assertEqual(frame, self.reference, (executable, index))
                        else:
                            image = Image.frombytes('RGB', (self.WIDTH, self.HEIGHT), frame)
                            for swatch, color in enumerate(self.COLORS):
                                actual = image.getpixel((swatch * 32 + 16, 32))
                                error = max(abs(a - b) for a, b in zip(actual, color))
                                if error > 5:
                                    # Show negotiation details only on failure; do not relax
                                    # pixel tolerances or treat decode success as color proof.
                                    debug_command = command.copy()
                                    debug_command[debug_command.index('-loglevel') + 1] = 'verbose'
                                    debug = subprocess.run(debug_command, input=self.pngs,
                                                           capture_output=True, timeout=30)
                                    details = '\n'.join(line for line in debug.stderr.decode(errors='replace').splitlines()
                                                        if any(word in line for word in ('scale', 'fmt:', 'reinit')))
                                    self.fail(f'frame={index} mode={self.MODES[index]} swatch={swatch} '
                                              f'actual={actual} expected={color}\n{details}')

    def test_yuv_color_conversion_survives_repeated_format_changes(self):
        for processing in ('', 'cas=strength=0.2800', 'unsharp=5:5:0.3:5:5:0',
                           'cas=strength=0.2800,eq=contrast=1:saturation=1:brightness=0'):
            self.check_sequence(processing)

    def test_rgb_cas_survives_repeated_format_changes(self):
        self.check_sequence('cas=strength=0.2800', rgb_output=True)

    def test_unprocessed_rgb_stays_pixel_exact_across_format_changes(self):
        self.assertEqual(color_pipeline('', True), '')
        self.check_sequence('', rgb_output=True, exact=True)


if __name__ == '__main__':
    unittest.main()
