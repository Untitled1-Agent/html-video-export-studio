from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import imageio_ffmpeg

from cli import build_parser, make_job
from media_pipeline import build_video_output_args
from models import dataclass_to_dict, job_config_from_dict
from presets import OUTPUT_PROFILES, RECIPES, apply_recipe
from renderer import HtmlVideoRenderer


def option(args: tuple[str, ...], flag: str) -> str:
    index = args.index(flag)
    return args[index + 1]


class QualityProfileTests(unittest.TestCase):
    def test_social_default_keeps_crf_rate_control_at_higher_quality(self):
        profile = OUTPUT_PROFILES["h264_420_mp4"]
        self.assertEqual(profile.video_encoder, "libx264")
        self.assertEqual(option(profile.video_args, "-crf"), "8")
        self.assertNotIn("-b:v", profile.video_args)
        self.assertEqual(option(profile.video_args, "-preset"), "slow")
        self.assertEqual(option(profile.video_args, "-pix_fmt"), "yuv420p")
        self.assertEqual(option(profile.video_args, "-profile:v"), "main")
        self.assertEqual(option(profile.video_args, "-tag:v"), "avc1")
        self.assertEqual(option(profile.video_args, "-maxrate"), "30M")
        self.assertEqual(option(profile.video_args, "-bufsize"), "60M")
        self.assertEqual(RECIPES["social_delivery"].output_profile_key, "h264_420_mp4")

    def test_hevc_is_opt_in_high_efficiency_profile(self):
        profile = OUTPUT_PROFILES["h265_420_mp4"]
        self.assertEqual(profile.video_encoder, "libx265")
        self.assertEqual(option(profile.video_args, "-crf"), "10")
        self.assertNotIn("-b:v", profile.video_args)
        self.assertEqual(option(profile.video_args, "-preset"), "slow")
        self.assertEqual(option(profile.video_args, "-pix_fmt"), "yuv420p")
        self.assertEqual(option(profile.video_args, "-profile:v"), "main")
        self.assertEqual(option(profile.video_args, "-tag:v"), "hvc1")
        self.assertEqual(option(profile.video_args, "-maxrate"), "30M")
        self.assertEqual(option(profile.video_args, "-bufsize"), "60M")
        self.assertEqual(profile.audio_codec, "aac")
        self.assertIn("aac_low", profile.audio_args)

    def test_hevc_and_social_profiles_keep_explicit_delivery_colour_metadata(self):
        for key in ("h264_420_mp4", "h265_420_mp4"):
            with self.subTest(profile=key):
                args = OUTPUT_PROFILES[key].video_args
                self.assertEqual(option(args, "-color_range"), "tv")
                self.assertEqual(option(args, "-colorspace"), "bt709")
                self.assertEqual(option(args, "-color_primaries"), "bt709")
                self.assertEqual(option(args, "-color_trc"), "iec61966-2-1")

    def test_packaged_ffmpeg_fallback_can_encode_and_decode_hevc(self):
        """The wheel's FFmpeg fallback must make the user-facing HEVC profile usable."""
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        width, height, frames = 32, 32, 4
        payload = bytearray()
        for frame in range(frames):
            for y in range(height):
                for x in range(width):
                    payload.extend(((x * 7 + frame * 17) % 256,
                                    (y * 9 + frame * 23) % 256,
                                    ((x + y) * 5 + frame * 29) % 256))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "hevc-fallback.mp4"
            encoded = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s:v", f"{width}x{height}", "-r", "10", "-i", "pipe:0",
                    "-frames:v", str(frames), "-c:v", "libx265", "-crf", "20",
                    "-preset", "ultrafast", "-x265-params", "pools=1:frame-threads=1:log-level=error",
                    "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-movflags", "+faststart",
                    str(output),
                ],
                input=bytes(payload), capture_output=True, timeout=45,
            )
            self.assertEqual(encoded.returncode, 0, encoded.stderr.decode(errors="replace"))
            self.assertTrue(output.is_file() and output.stat().st_size > 0)
            decoded = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror",
                 "-i", str(output), "-map", "0:v:0", "-f", "null", "-"],
                capture_output=True, timeout=30,
            )
            self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors="replace"))
            self.assertFalse(decoded.stderr)

    def test_manual_crf_override_replaces_only_profile_crf(self):
        social = OUTPUT_PROFILES["h264_420_mp4"]
        hevc = OUTPUT_PROFILES["h265_420_mp4"]
        self.assertEqual(option(tuple(build_video_output_args(social)), "-crf"), "8")
        self.assertEqual(option(tuple(build_video_output_args(social, 6.5)), "-crf"), "6.5")
        self.assertEqual(option(tuple(build_video_output_args(hevc, 7)), "-crf"), "7")
        self.assertEqual(option(social.video_args, "-crf"), "8")
        self.assertEqual(option(hevc.video_args, "-crf"), "10")

    def test_manual_crf_cli_project_roundtrip_and_recipe_reset(self):
        args = build_parser().parse_args(["clip.html", "--profile", "h265_420_mp4", "--crf", "7.5"])
        job = make_job("clip.html", args)
        self.assertEqual(job.render.video_crf, 7.5)
        payload = dataclass_to_dict(job)
        restored = job_config_from_dict(payload)
        self.assertEqual(restored.render.video_crf, 7.5)
        del payload["render"]["video_crf"]
        self.assertIsNone(job_config_from_dict(payload).render.video_crf)
        self.assertIsNone(apply_recipe(restored, "social_delivery").render.video_crf)

    def test_manual_crf_validation_rejects_bad_values_and_non_h26x_profiles(self):
        job = RECIPES["social_delivery"].create_job("clip.html")
        for value in (0, -1, 51.1, float("nan"), True):
            with self.subTest(value=value):
                job.render.video_crf = value
                with self.assertRaises(ValueError):
                    job.render.validate(for_export=False)
        job.render.video_crf = 8
        job.render.output_profile_key = "prores_hq_mov"
        with self.assertRaisesRegex(ValueError, "H.264/H.265"):
            job.render.validate(for_export=False)

    def test_renderer_command_uses_manual_crf_override(self):
        job = RECIPES["social_delivery"].create_job("clip.html")
        job.render.video_crf = 5.5
        renderer = HtmlVideoRenderer()
        renderer._ffmpeg_exe = "ffmpeg"
        command = renderer._build_ffmpeg_command(job, Path("out.mp4"), 1.0, "")
        self.assertEqual(command[command.index("-crf") + 1], "5.5")


if __name__ == "__main__":
    unittest.main()
