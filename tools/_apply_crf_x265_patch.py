from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Expected text not found in {path}: {old[:120]!r}")
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one match in {path}, found {text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


presets = ROOT / "presets.py"
text = presets.read_text(encoding="utf-8")

replace_once(
    presets,
    '            "-crf", "12",\n            "-preset", "slow",\n            "-pix_fmt", "yuv420p",\n            "-profile:v", "main",\n            "-tag:v", "avc1",',
    '            "-crf", "8",\n            "-preset", "slow",\n            "-pix_fmt", "yuv420p",\n            "-profile:v", "main",\n            "-tag:v", "avc1",',
)
replace_once(
    presets,
    '            "8-bit H.264 Main for phones, VLC, browsers, and social uploads. "\n            "4:2:0 chroma subsampling may soften saturated text and fine UI edges."',
    '            "8-bit H.264 Main at high-quality CRF 8 for phones, VLC, browsers, and social uploads. "\n            "4:2:0 chroma subsampling may soften saturated text and fine UI edges."',
)
replace_once(
    presets,
    '        description="1× / 60 fps H.264 Main 4:2:0 with CAS 0.28 clarity. Default for mobile playback and social uploads; no automatic 4K upscaling.",',
    '        description="1× / 60 fps H.264 Main 4:2:0 at CRF 8 with CAS 0.28 clarity. Default for mobile playback and social uploads; no automatic 4K upscaling.",',
)

text = presets.read_text(encoding="utf-8")
if '"h265_420_mp4": OutputProfile(' in text:
    raise SystemExit("h265_420_mp4 already exists")
marker = '    "vp9_webm": OutputProfile(\n'
if marker not in text:
    raise SystemExit("VP9 insertion marker not found")
hevc = '''    "h265_420_mp4": OutputProfile(
        key="h265_420_mp4",
        label="High Efficiency — H.265/HEVC 4:2:0 MP4",
        description=(
            "High-quality HEVC/H.265 Main 8-bit delivery using CRF 10 and hvc1 sample entries. "
            "Usually more efficient than H.264 at comparable fidelity, but playback and social-upload "
            "support is less universal; Social delivery intentionally remains H.264 by default."
        ),
        extension="mp4",
        video_encoder="libx265",
        video_args=(
            "-noautoscale",
            "-c:v", "libx265",
            "-x265-params", "colorprim=bt709:transfer=iec61966-2-1:colormatrix=bt709:range=limited:open-gop=0:log-level=error",
            "-crf", "10",
            "-preset", "slow",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-tag:v", "hvc1",
            "-g", "120",
            "-maxrate", "20M",
            "-bufsize", "40M",
            "-color_range", "tv",
            "-color_primaries", "bt709",
            "-color_trc", "iec61966-2-1",
            "-colorspace", "bt709",
        ),
        audio_codec="aac",
        audio_args=("-c:a", "aac", "-profile:a", "aac_low", "-b:a", "256k", "-ar", "48000", "-ac", "2"),
        requires_even_dimensions=True,
    ),
'''
presets.write_text(text.replace(marker, hevc + marker, 1), encoding="utf-8")

# User-facing compatibility docs: keep H.264 as the default social target and
# explain why HEVC is opt-in rather than silently replacing the safe baseline.
delivery = ROOT / "docs" / "DELIVERY_COMPATIBILITY.md"
replace_once(
    delivery,
    'sample entries, and CRF 12 / slow encoding. Closed GOPs, at most 120 frames\nbetween keyframes, three reference frames, two B-frames and a 20 Mb/s VBV ceiling',
    'sample entries, and CRF 8 / slow encoding. Closed GOPs, at most 120 frames\nbetween keyframes, three reference frames, two B-frames and a 20 Mb/s VBV ceiling',
)
text = delivery.read_text(encoding="utf-8")
needle = 'RGB-to-YUV conversion remains explicit: BT.709 primaries/matrix, limited YUV\nrange and the screenshot\'s sRGB transfer.'
addition = '''An optional **High Efficiency — H.265/HEVC 4:2:0 MP4** profile uses
`libx265`, CRF 10 / slow, Main 8-bit 4:2:0, `hvc1`, the same explicit colour
metadata and AAC-LC 48 kHz stereo audio defaults. It is intended for controlled
modern-device workflows where HEVC support has been verified. It is **not** the
Social delivery default because browser/player/upload acceptance is less
universal than H.264.

'''
if needle not in text:
    raise SystemExit("Delivery documentation insertion marker not found")
delivery.write_text(text.replace(needle, addition + needle, 1), encoding="utf-8")

audio_doc = ROOT / "docs" / "AUDIO.md"
replace_once(
    audio_doc,
    '| H.264 MP4, including Social delivery | AAC-LC |',
    '| H.264 or H.265/HEVC MP4, including Social delivery | AAC-LC |',
)

changelog = ROOT / "CHANGELOG.md"
text = changelog.read_text(encoding="utf-8")
anchor = '## Unreleased\n'
entry = '''## Unreleased

### Encoding quality

- Raise the default Social delivery H.264 quality from CRF 12 to CRF 8 while retaining CRF rate control, the existing compatibility constraints, and the 20 Mb/s VBV ceiling.
- Add an opt-in H.265/HEVC Main 4:2:0 MP4 profile using libx265 CRF 10, hvc1 sample entries, fast-start MP4, explicit BT.709/sRGB metadata, and AAC-LC audio. Social delivery remains H.264 by default for broader playback/upload compatibility.
'''
if not text.startswith(anchor) and anchor not in text:
    raise SystemExit("CHANGELOG Unreleased heading not found")
changelog.write_text(text.replace(anchor, entry, 1), encoding="utf-8")

# Focused regressions. Real codec/browser coverage is also inherited automatically:
# profile_matrix iterates every output profile, and test_audio_media iterates every
# profile with a soundtrack.
test = ROOT / "tests" / "test_quality_profiles.py"
test.write_text('''from __future__ import annotations

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
        self.assertEqual(option(profile.video_args, "-maxrate"), "20M")
        self.assertEqual(option(profile.video_args, "-bufsize"), "40M")
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
        self.assertEqual(option(profile.video_args, "-maxrate"), "20M")
        self.assertEqual(option(profile.video_args, "-bufsize"), "40M")
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
''', encoding="utf-8")

subprocess.run(["python", str(ROOT / "tools" / "generate_reference.py")], cwd=ROOT, check=True)
print("Applied CRF 8 Social quality update and optional x265 profile.")
