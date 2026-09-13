# Soundtracks and audio synchronization

Attach a local WAV, M4A, or MP3 file to each HTML job. AAC, FLAC, OGG, and Opus inputs are also supported when the selected FFmpeg build can decode them. Browser audio is **not recorded**: the selected external track is decoded, processed, and muxed with the captured video.

## Desktop workflow

Add HTML files to the Export Queue. Select one job and choose **Attach Audio…** to associate any audio filename with it. The Soundtrack column shows the match, playback mode, and signed offset. Open **Job Settings → Audio** to change the soundtrack or its processing/encoding options.

For batches, select the HTML jobs, choose **Match by Filename…**, then select the audio files. Matching compares the HTML and audio basenames case-insensitively: `scene.html` can match `SCENE.m4a`. It never pairs files by picker order. A unique audio file beside the HTML takes precedence over same-named tracks in other folders. Ambiguous and missing matches are reported rather than guessed. Existing assignments are retained, including disabled tracks; remove or manually replace them first.

**Remove Audio** clears the assignment and disables the track. Changing an assignment requeues the affected job and clears stale output/probe state. Rendering, queued-for-worker, and analyzing jobs cannot be rematched until they are stopped. Assignments and audio settings are preserved when saving projects, duplicating jobs, or applying video recipes; workers receive independent snapshots.

## Sound options

| Option | Behavior |
| --- | --- |
| No audio | Export video only, even if an audio path remains in the job. |
| Use once / trim | Use the track once; pad short tracks with silence and trim long tracks to video length. |
| Loop to video length | Repeat the track for the full video. Loop seams depend on the source recording and codec. |
| Volume | Linear gain: `0` mutes, `1` preserves amplitude, `0.5` halves amplitude. Gain above 1 can clip. |
| Sync offset | Signed seconds. Positive values delay the track; negative values skip its beginning. |
| Fade in | Fade from the audible start, after positive-offset silence. |
| Fade out | Fade ending at the output video's end, not the input audio's end. |
| Bitrate | `0` uses the video profile default; selectable AAC/Opus rates are 64, 96, 128, 160, 192, and 256 kbps. PCM ignores bitrate. |
| Sample rate | `0` uses the profile default (48000 Hz); explicit 44100 or 48000 Hz. WebM/Opus requires 48000 Hz. |
| Channels | `0` uses the profile default (stereo), `1` is mono, `2` is stereo. Multichannel inputs are downmixed. |
| Normalize loudness | Optional single-pass FFmpeg loudnorm processing targeting −16 LUFS, −1.5 dBTP and LRA 11, before user gain and fades. |

Normalization is off by default. It is not a guaranteed final measured loudness: source length, dynamics, subsequent gain, and fades affect the result. It adds processing work, and gain applied afterward can exceed the normalization peak target. These controls do not alter the video processing preset.

## Exact timing contract

Audio time zero refers to the **output video**, including any video hold-start/hold-end periods. Video trim settings do not implicitly trim the soundtrack. Set an audio offset explicitly to align the chosen track with the edited video.

For `+0.250`, insert 250 ms of silence before the first audio sample. For `-0.250`, discard the first 250 ms of the decoded source and start the remaining samples at output time zero. Negative offsets reset timestamps after trimming. In loop mode, negative offsets advance into the repeated audio timeline, including past one repetition.

The video duration is authoritative. Audio is padded and trimmed to the actual frame-rounded video duration; it never shortens the video via `-shortest`. For example, a 0.23-second render at 30 fps has seven frames, so its target audio duration is 7/30 seconds. Compressed audio can have codec-sized packet/decoder padding around this duration.

An offset after the video end, or an exhausted once-only track after a negative offset, produces full-length silence. Offsets are bounded to ±86400 seconds and fades to 0–86400 seconds; non-finite values and booleans are rejected. Large negative offsets can take additional decoding time, especially when looping. Fade-in is clamped to the remaining audible window; fade-out is clamped to the output duration. If a short once-only track ends early, the end-of-video fade may operate on silence—use looping or edit the track for a musical ending.

## Social delivery and other output profiles

Choose **Social delivery** / **Delivery — H.264 4:2:0 MP4** for the existing phone/social-oriented video settings. Its default soundtrack is AAC-LC, 256 kbps, 48 kHz stereo. MP4 retains `+faststart`, and streams are explicitly mapped to captured video plus the selected input's first audio stream. Cover art or extra input streams are not copied. WAV PCM and MP3 inputs are re-encoded to AAC rather than copied into a potentially incompatible delivery stream.

The video profile controls the audio codec; audio controls cannot override it with a container-incompatible choice:

| Video container/profile | Audio codec |
| --- | --- |
| H.264, H.265/HEVC, or AV1 MP4, including Social delivery | AAC-LC |
| ProRes MOV editing masters | 24-bit PCM |
| VP9 WebM | Opus |

AAC/Opus bitrate, mono/stereo, and supported sample rates remain configurable. PCM bitrate is determined by its sample format, sample rate, and channels. Selecting 44100 Hz for WebM/Opus is rejected with an actionable message. RGB/4:4:4 MP4, ProRes, and WebM remain specialist profiles; adding a soundtrack does not make their video settings social-upload compatible.

Automated tests inspect codecs, channels, rate, stream duration, fast-start placement, and decoded audio. They are **not live TikTok/Instagram upload certification**. Platforms may impose additional duration, resolution, bitrate, account, API, and music-rights restrictions and may recompress uploads.

## CLI

Attach a soundtrack and advance it by 120 ms:

```sh
python cli.py scene.html --audio music.m4a --audio-offset -0.120 --output scene.mp4
```

Loop, delay, fade, and set quality:

```sh
python cli.py scene.html --audio music.mp3 --audio-mode loop --audio-offset 0.25 --audio-fade-in 0.5 --audio-fade-out 1 --audio-volume 0.8 --audio-bitrate 192 --audio-sample-rate 48000 --audio-channels 2
```

Explicitly match independent tracks in a batch:

```sh
python cli.py one.html two.html --audio-map one.html narration.wav --audio-map two.html music.m4a --output exports
```

Or match unique basenames from one audio directory:

```sh
python cli.py one.html two.html --audio-dir soundtracks --output exports
```

`--audio`, `--audio-map`, and `--audio-dir` are mutually exclusive. `--audio` applies one track to every input. Repeat `--audio-map HTML AUDIO` for explicit assignments; unmapped HTML inputs remain silent. Duplicate mappings and mappings to an input not in the source list are rejected. `--audio-dir` reports missing or ambiguous matches for the affected input. Shared processing/encoding flags apply to each assigned track; use separate invocations or the desktop/Python API for different per-job processing settings.

`--audio-normalize` enables optional normalization. `--audio-mode none` disables muxing. Non-default audio settings without an input option are rejected rather than silently ignored. Paths with spaces should be quoted normally; they are passed to FFmpeg as argument-list entries, never interpolated into a shell.

## Projects and Python API

All settings live in `job.render.audio` (`AudioConfig`). Saved projects retain the audio path and options. Existing projects without the new encoding/normalization keys load with profile defaults. Relative imported paths resolve against the project file, not the working directory.

```python
from models import AudioConfig, AudioMode

job.render.audio = AudioConfig(
    path="soundtrack.wav",
    mode=AudioMode.LOOP,
    offset_seconds=-0.12,
    volume=0.8,
    fade_in_seconds=0.25,
    fade_out_seconds=0.5,
    bitrate_kbps=192,
    sample_rate_hz=48000,
    channels=2,
    normalize_loudness=False,
)
```

Before encoding video frames, the renderer validates the file and decodes a bounded sample of its first audio stream using FFmpeg. Missing, unsupported, corrupt, or audio-less inputs fail with a diagnostic. FFprobe is used in integration tests, not required for runtime preflight. Failed or cancelled renders retain the existing atomic-output protections.

## Regression coverage

```sh
python -m unittest discover -s tests -p test_audio_core.py -v
xvfb-run -a python -W error::ResourceWarning tools/validate.py
python tests/profile_matrix.py
```

The full validation gate includes real WAV/M4A/MP3 decoding, sample-based signed-offset checks, looping/padding/fades/gain/normalization, all output profiles, browser and CLI renders, desktop pairing/editor controls, parallel queue export, project round trips, invalid input handling, and cancellation. Required integration tools are Chromium, FFmpeg, FFprobe, and a desktop display (or Xvfb on Linux).

See the [configuration reference](CONFIGURATION.md) and [FFmpeg audio filter documentation](https://ffmpeg.org/ffmpeg-filters.html) for the underlying options.
