"""Keep implicit FFmpeg output conversion out of fixed-geometry exports."""
from __future__ import annotations

import unittest
from pathlib import Path

from models import JobConfig, SourceSpec
from presets import OUTPUT_PROFILES
from renderer import HtmlVideoRenderer


class OutputAutoscaleTests(unittest.TestCase):
    def test_all_profiles_disable_implicit_output_scaling(self):
        renderer = object.__new__(HtmlVideoRenderer)
        renderer._ffmpeg_exe = 'ffmpeg'
        for key, profile in OUTPUT_PROFILES.items():
            with self.subTest(profile=key):
                job = JobConfig(SourceSpec('source.html'))
                job.render.output_profile_key = key
                command = renderer._build_ffmpeg_command(
                    job, Path('output.' + profile.extension), 1.0, '')
                self.assertEqual(command.count('-noautoscale'), 1)
                self.assertGreater(command.index('-noautoscale'), command.index('pipe:0'))
                self.assertNotIn('-autoscale', command)
                # Reinitialization must remain enabled; do not drop or misread
                # RGBA frames to hide the RGB-to-RGBA color regression.
                self.assertNotIn('-reinit_filter', command)
                self.assertNotIn('-drop_changed', command)


if __name__ == '__main__':
    unittest.main()
