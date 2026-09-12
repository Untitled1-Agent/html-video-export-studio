from __future__ import annotations

import unittest

from presets import OUTPUT_PROFILES, RECIPES


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


if __name__ == "__main__":
    unittest.main()
