# Single-video capture performance

## Why more encoder threads were not sufficient

The encoder and the browser are different pipeline stages. A single browser
seeks, waits for its page to repaint, captures and losslessly compresses a PNG,
then supplies it to FFmpeg. More FFmpeg threads cannot make a waiting browser
produce frames faster. CPU percentage alone is not a throughput measure.

## Controls

In **Job Settings → Performance**, set **Capture workers per export** to 4 for a
known deterministic, independently seekable animation. This uses separate
browser instances to produce different frames of ONE movie concurrently. Keep
queue workers at 1 while tuning a single movie. Encoder threads remain on the
Output tab; automatic encoding is a reasonable starting point.

Equivalent CLI:

```bash
python cli.py source.html --capture-workers 4 --cpu-threads 0 --frame-buffer-mb 256
```

`capture_workers` is 0 by default. **0 is safe auto, not unconditional parallel
rendering:** it uses multiple browsers only when the source declares the
parallel-safe contract below. **1 always selects sequential capture.** Explicit
2–16 is a user assertion that the source meets that contract, not a proof by the
application. Counts are capped by frame-buffer budget and frame count. Auto
also accounts for available CPUs, concurrent desktop jobs, and short clips.

`fast_capture` defaults to true. In locked intrinsic geometry, the renderer
still reapplies root/iframe guards, waits for repaint and checks dimensions. It
uses a viewport screenshot only when the live element bounding box exactly
covers the expected viewport. This avoids redundant element-stability/scroll
waits. Other layouts fall back to the original element capture. Disable the
checkbox or pass `--no-fast-capture` for comparison or troubleshooting. No lossy
JPEG intermediate, resolution reduction or reduced quality preset is used.

## Parallel-safe source contract

Each freshly loaded independent browser must produce **the same entire captured
frame for a given absolute timestamp**, regardless of previous seeks, seek order
or wall-clock time. Fonts, assets, duration, geometry and loading outcome must be
consistent. The application does not rewrite random seeds, network state or
page clocks to invent this guarantee.

An author can opt into automatic worker selection on the capture root:

```html
<div data-video-export data-video-export-parallel-safe="true">...</div>
```

Alternatively set `window.HTML_VIDEO_EXPORT.parallelSafe = true` on the existing
export object. The mere presence of a seek hook or `data-om-sync-seek` is NOT
sufficient. Supported seek adapters are OM events, custom events, JavaScript
functions, Web Animations and media seeking. Independent sessions validate
resolved target, dimensions, duration, loading strategy and timeline metadata
against the initial prepared source; mismatches fail the export.

Browser Clock and Realtime cannot parallelize safely and reject explicit 2+
workers. Static holds stay sequential and reuse the captured frame. Cumulative
canvas simulations, random initialization, network-driven content and hooks
that depend on every preceding sample should use 1. A few matching test frames
do not prove arbitrary JavaScript stateless; compare a complete representative
export before using parallel mode for a new source.

## Memory, ordering and lifecycle

`frame_buffer_mb` (16–4096, default 256 MiB) bounds planned PNG payloads, **not
total process memory**. Auto caps at eight capture workers. A lane reserves one
result slot before it captures; it retains at most a cached PNG and one new
result, plus the consumer's current frame. Planning uses a conservative
five-bytes-per-pixel allowance plus 64 KiB per PNG, and oversized frames fail.
If the budget cannot hold parallel lanes, rendering stays sequential; one PNG
still has to fit in memory. Browser heaps, decoded surfaces and FFmpeg pools
are additional and may be much larger. Reduce workers for complex pages/4K.

Each lane creates, uses and closes its own Playwright/browser resources on one
owner thread. The consumer feeds frames in original index order to **one**
FFmpeg process. It does not concatenate separately encoded clips or duplicate
audio per lane. Producer queues cannot grow with clip duration. Pause blocks
new capture between operations; already-started operations may finish. Worker
failures cancel the pool. Cleanup joins all owners before output publication.
Cancellation leaves an existing good output intact. In-flight browser calls
are bounded by the configured page timeout, not instantaneously interrupted.

The previous explicit colour conversion, `-noautoscale`, delivery format,
sharpening, frame-count rules and lossless RGB path remain unchanged.

## Measuring actual improvement

Render logs include effective workers and `Render timings` with seek,
screenshot, frame-wait, pipe-write, finalization and total time. For parallel
capture, seek/screenshot values are sums across lanes and overlap; do NOT add
them to derive wall time. Frame-wait is time the encoder-feeding consumer waits
for ordered results, including independent-browser startup. `last_render_stats`
on the renderer contains the same summary after success; it is cleared before
a new attempt. Its total ends at output publication; the benchmark also includes
renderer startup and final browser shutdown.

```bash
python tools/benchmark_capture.py --width 1280 --height 720 --frames 120 --trials 2 --workers 2 4
```

This benchmarks complete browser-driven exports with identical encoder thread
counts, compares legacy serial/fast serial/parallel capture, rotates trial order
and fully decodes every output. It generates an asset-free animated SVG source
and temporary movies; neither private inputs nor media assets are committed.
There is no flaky CI speed threshold. Small clips, expensive encoders, limited
RAM, thermal throttling and different CPUs may produce different results.
More CPU occupancy is useful only when wall-clock export time decreases.

A local two-trial, 1280×720 / 90-frame result (five available logical CPUs,
Chromium 144, FFmpeg 7.1.5, fixed four encoder threads) measured these median
wall-clock export times, including browser startup and shutdown:

| Path | Median seconds |
| --- | ---: |
| Legacy sequential element screenshots | 15.79 |
| Fast sequential guarded viewport screenshots | 10.50 |
| Four independent capture workers + fast screenshots | 7.21 |

The ratio of median elapsed times is **2.19× throughput**, or about **54% less
elapsed time**, for parallel versus legacy in this fixture. Two samples are
limited evidence, not a universal performance claim or a Ryzen measurement.
All six movies fully decoded to the expected frame count. The separate
regressions compare complete decoded RGB pixels, colour swatches, transparency,
trims/holds, cancellation and thread cleanup. Raw timing samples are in
[the validation record](../validation/parallel_capture_benchmark.json).

Primary references:
- Playwright Python thread ownership: https://playwright.dev/python/docs/library#threading
- Locator screenshot behavior: https://playwright.dev/python/docs/api/class-locator#locator-screenshot
- Page screenshots: https://playwright.dev/python/docs/api/class-page#page-screenshot
