# VLC, Android and TikTok delivery

## Why the old default was risky

The previous desktop/CLI default, **Motion graphics master**, encoded lossless
RGB H.264 (`libx264rgb`, High 4:4:4 Predictive) inside an MP4. The extension does
not make that bitstream an ordinary mobile H.264 video. A software decoder may
play it correctly while a hardware decoder or an upload/transcoding service
cannot handle it. The default also doubled both dimensions, potentially making
a 1080×1920 composition a 2160×3840 / 60 fps master.

This is a confirmed compatibility problem in the previous defaults and is
consistent with reports of VLC artifacts, Android colour distortion and TikTok
rejection. Without the original failing movie and device, it is not proof of
the exact cause of a particular failure or of damaged encoded bytes.

## Reproduced mid-video colour conversion problem

The new regression exposed a second problem on the Linux CI runner's FFmpeg
6.1: after an RGB-to-RGBA PNG transition, a magenta swatch decoded as
`(255, 37, 255)` instead of `(255, 0, 255)`. The same test passed on FFmpeg 7.
This is encoded colour drift, not just a player-display difference.

FFmpeg 6.1 can insert an implicit output scaler after the filter graph is
reinitialized, once output dimensions are known. That scaler can take over
RGB-to-YUV conversion using its default matrix instead of the explicit BT.709
conversion. All export profiles now disable output autoscaling with
`-noautoscale`. The renderer already validates fixed capture dimensions; there
is no legitimate need to silently resize later frames. Graph reinitialization
remains enabled and frames are not dropped to conceal format changes.

The 240-frame decoded-colour regression keeps the RGB-to-RGBA transition and
checks every frame. A separate command-builder test guards the output option
for every profile, including archival and alpha-capable outputs.

## New default: Social delivery

New desktop, CLI and Python API jobs use **Social delivery — Sharp compatible
MP4 (default)**: native 1× capture, 60 fps, 8-bit H.264 Main / `yuv420p`, `avc1`
sample entries, and CRF 8 / slow encoding. Closed GOPs, at most 120 frames
between keyframes, three reference frames, two B-frames and a 20 Mb/s VBV ceiling
with a 40 Mb buffer bound delivery complexity. The existing fast-start MP4
layout remains enabled. An external soundtrack is encoded as AAC-LC stereo at
48 kHz; silent jobs do not acquire an unsolicited audio track.

**Social Compensation now applies CAS 0.28 explicitly**, rather than relying on
auto analysis that may disable sharpening. It improves edge definition, not
missing source detail. It does not increase contrast or saturation. Choose
No processing to disable it, or Exact source master for unprocessed lossless RGB.
No implicit geometric resizing, clipping or padding was introduced.

An optional **High Efficiency — H.265/HEVC 4:2:0 MP4** profile uses
`libx265`, CRF 10 / slow, Main 8-bit 4:2:0, `hvc1`, the same explicit colour
metadata and AAC-LC 48 kHz stereo audio defaults. It is intended for controlled
modern-device workflows where HEVC support has been verified. It is **not** the
Social delivery default because browser/player/upload acceptance is less
universal than H.264.

RGB-to-YUV conversion remains explicit: BT.709 primaries/matrix, limited YUV
range and the screenshot's sRGB transfer. Relabelling sRGB pixels as BT.709
transfer without a real transfer-function conversion would misdescribe the
samples. The tests check both metadata and decoded colour patches.

## Upgrading and re-rendering

An old saved new-job preference of `motion_graphics_master` migrates to
`social_delivery` once. Other recipe preferences and all explicit job/project
settings are preserved. After upgrading, choosing Motion graphics master again
and saving preferences is respected on subsequent launches. This one-time
migration cannot distinguish the former factory preference from a user who
selected that same recipe before upgrading.

For an existing queued/project job, choose **Delivery — H.264 4:2:0 MP4**, scale
**1×**, and **Social delivery — Compression compensation** in Job Settings to
preserve its source/timing configuration. Requeue and render to a new file.
Changing defaults does not repair movies already exported.

For a new CLI export:

```bash
python cli.py source.html --recipe social_delivery --output delivery.mp4
```

Keep any source-specific selector, timing-adapter and duration arguments needed
by that source. For an archival reference, explicitly choose
`--recipe exact_source_master`; keep a separate delivery copy for uploading.

## Limits and verification

This is a broadly compatible encoding preset, not a guarantee for every device
or upload. Output dimensions must still be even. Huge sources, custom scales
and frame rates can exceed device capabilities; the encoder selects the actual
H.264 level rather than falsely labelling arbitrary geometry as a fixed level.

TikTok's Content Posting API guide currently recommends MP4/H.264, with 23–60
fps, 360–4096 pixels on each axis, and at most 4 GB. Account duration limits also
apply. These are API requirements, not certification of every TikTok app path.
The studio does not silently resize an out-of-range composition to satisfy them.

Run the regressions with FFmpeg, ffprobe and the application dependencies:

```bash
python -m unittest discover -s tests -p test_delivery_compatibility.py -v
python -m unittest discover -s tests -p test_output_autoscale.py -v
```

They encode and fully decode 240 frames across changing colours, an RGB-to-RGBA
PNG transition and GOP boundaries; check timestamps, MP4 box order, AAC output,
edge definition and exact-master RGB identity. Fixtures do not require a
browser. They do not substitute for live VLC/Android playback or a TikTok upload.

Primary references:

- [Android supported media formats](https://developer.android.com/media/platform/supported-formats)
- [TikTok media transfer requirements](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide)
- [FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html)
- [FFmpeg output autoscaling](https://ffmpeg.org/ffmpeg.html)
- [FFmpeg 6.1 output filter configuration](https://github.com/FFmpeg/FFmpeg/blob/n6.1.1/fftools/ffmpeg_filter.c)
