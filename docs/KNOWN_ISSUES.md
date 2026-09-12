# Known limitations and open follow-up work

[Back to README](../README.md)

This is a local backlog/compatibility record, **not a claim that GitHub issues were created or closed**. Resolved regressions are recorded in the [changelog](../CHANGELOG.md) with tests. The report describes verified coverage; absence of a known bug is not proof of universal correctness.

| ID | Status / scope | Current behavior or workaround |
| --- | --- | --- |
| K-01 | Open: native-platform certification | Windows/macOS runtime, installer, media-player and live updater tests have not been executed in this review environment. CI definitions are not evidence of completed runs. |
| K-02 | Design limit: arbitrary HTML | A marker or duration alone does not guarantee a seek implementation. Prefer an authored event/function hook and visually inspect frames. |
| K-03 | Open: hard browser watchdog | An infinite synchronous JS loop can block cooperative timeout/cancellation. Render trusted pages only; restart externally for a hung page. No process-isolated hard timeout is promised. |
| K-04 | Design limit: real-time fidelity | Expensive captures can miss states. Pausing the queue does not stop the page's independent clock. Use deterministic hooks, WAAPI, or Browser Clock where appropriate. |
| K-05 | Design limit: combined timelines | Browser Clock does not seek CSS compositor animations or media. Media adapter seeks the selected/first matching media element, not an arbitrary synchronized multi-track composition. Use one custom authoritative seek function for mixed content. |
| K-06 | Open: signed-update trust | SHA-256 detects corruption but does not authenticate a compromised publisher. Updates trust the configured GitHub repo. Signatures/attestations are not yet verified by the app. |
| K-07 | Design limit: file replacement | Updating multiple files is not power-loss atomic; backups provide manual recovery. POSIX no-clobber output publication requires hard-link support. Use a local filesystem or a carefully chosen unique overwrite destination. |
| K-08 | Design limit: audio / alpha / color | No browser audio recording, no DRM/live media, no processed-alpha pipeline, no HDR/wide-gamut archival capture. Use external audio and No processing + ProRes 4444 for alpha. |
| K-09 | ~~Open: repository license decision~~ **Resolved 2026-09-12: MIT.** See [LICENSE](../LICENSE). Third-party obligations: Playwright/Chromium/FFmpeg/imageio-ffmpeg/Pillow obtained separately and carry their own licenses — reviewed, no bundled binaries in this repo. |
| K-10 | Test-environment restriction | The review host blocks direct file/localhost Chromium navigation. Standalone embedded HTML is tested; loopback server safety is tested separately. Do not bypass administrative browser policy. |
| K-11 | Design limit: resources and assets | Very large pages or many parallel browsers can exhaust RAM. Warnings are not a universal memory estimator. External resources can still fail after readiness; test representative frames. |
| K-12 | Design limit: frame-rate model | Integer fps only. No rational 29.97/59.94 rates, AI super-resolution, or frame interpolation. Higher output resolution/fps cannot invent missing source detail. |

Live GitHub release installation and initial repository publication need real authenticated user environments. Mock tests cover failure handling and safety decisions, not actual permission grants. See [GitHub setup](../GITHUB_SETUP.md) and [release operations](RELEASING.md).
