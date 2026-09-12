# Changelog

## Unreleased

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