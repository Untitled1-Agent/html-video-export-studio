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
- Share automatic budgets across effective queue workers without changing saved jobs.
- Add decoded-media/queue regressions and a reproducible encoding benchmark.
- Retain existing delivery compatibility, color conversion, and exact RGB behavior.

## 1.5.2 — repository preparation


See [release notes](RELEASE_NOTES_1.5.2.md) and [test report](TEST_REPORT.md).