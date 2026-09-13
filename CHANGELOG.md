# Changelog

## Unreleased — browser capture performance

- Add ordered, bounded single-export browser capture workers with independent Playwright ownership.
- Auto parallelism requires an author declaration; explicit 2+ workers require independently seekable sources. Progressive timelines remain sequential.
- Use viewport screenshots for geometry-guarded roots only when their live bounds match the viewport; retain the legacy fallback and an opt-out.
- Add GUI/CLI controls, per-stage render timing, actual browser-render regressions and an end-to-end benchmark.

## Unreleased

### Encoding quality

- Raise the default Social delivery H.264 quality from CRF 12 to CRF 8 while retaining CRF rate control and the existing compatibility constraints. Raise the Social/HEVC VBV guardrail from 20M/40M to 30M/60M after sustained-detail stress renders showed the old ceiling could override CRF quality.
- Add an opt-in H.265/HEVC Main 4:2:0 MP4 profile using libx265 CRF 10, hvc1 sample entries, fast-start MP4, explicit BT.709/sRGB metadata, and AAC-LC audio. Social delivery remains H.264 by default for broader playback/upload compatibility.

### Added

- MIT License (LICENSE); project license declared in pyproject metadata.

### Changed

- README, NOTICE, GITHUB_SETUP, TEST_REPORT and KNOWN_ISSUES K-09 updated from "license decision open" to MIT.
- Repository made public.

### Performance

- Replace fixed FFmpeg video thread counts with CPU-aware, bounded automatic pools.
- Add per-job CPU thread controls in the desktop, CLI, and project/Python model.
- Add dynamic queue CPU allocation so simultaneous auto-thread jobs divide the logical CPU budget; explicit per-job overrides remain unchanged.
- Add an opt-in/automatic ordered parallel browser-capture path for independently seekable timelines; progressive timelines remain sequential and ordered output stays deterministic.
- Add capture-worker and frame-buffer controls in desktop, CLI and project settings, plus stage timing logs and an end-to-end capture benchmark.

### Audio

- Add per-HTML external soundtrack attachment and deterministic filename matching for WAV, M4A, MP3 and other supported FFmpeg audio inputs.
- Add signed audio sync offsets, loop/trim behavior, gain, fades, optional loudness normalization, bitrate/sample-rate/channel controls, and AAC-LC social delivery audio.
- Add desktop queue/editor controls plus CLI `--audio`, `--audio-map`, and `--audio-dir` workflows.

### Fixed

- Preserve video duration when external audio is shorter or longer; non-looped tracks pad with silence and long tracks trim to the output timeline.
- Reject corrupt or audio-less soundtrack files before video frame encoding begins.
- Preserve soundtrack settings while switching video recipes and while saving/loading projects.
