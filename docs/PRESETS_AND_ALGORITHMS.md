# Presets and algorithms — 1.5.2

## Capture fidelity

The browser produces PNG frames at the requested device density. There is no FFmpeg geometric resize step. Native SVG/text can benefit from increased raster density; a low-resolution canvas/image/video cannot gain source detail. Samples are the browser's rendered 8-bit output, not an HDR archival pipeline.

## Exact / processed / delivery

No processing with libx264rgb CRF 0 is tested for pixel-exact RGB round-trip. No enhancement chain is inserted. Explicit H.264 VUI identifies full-range RGB, BT.709 primaries and sRGB transfer.

CAS runs in planar RGB. Unsharp and luma/color controls use explicit full-chroma BT.709 conversions where required. Conversion back to RGB is explicit; that conversion can change samples even if the final encoder is lossless. YUV profiles always require RGB-to-YUV conversion; metadata alone is insufficient to choose its matrix. The `scale` filter in a YUV color-conversion chain has no dimension arguments and does not enlarge/shrink the image.

ProRes 4444/HQ, H.264 delivery and VP9 are not lossless source references. H.264 4:2:0 subsamples colored edges. The master profile is not supported by every hardware player. Final display scaling and platform transcoding can change perceived sharpness.

## Clarity

Presets: UI Subtle CAS 0.18; UI Balanced CAS 0.28; Photo Gentle 0.07; Social Compensation 0.22; No processing; Custom. Values are artistic preferences, not objectively optimal quality guarantees.

Automatic analysis excludes the edge detector's artificial image border and near-uniform bright/dark frames. It samples multiple positions within the trimmed source window for seekable sources. Ambiguous/photographic results stay unprocessed. It does not advance real-time/virtual-clock sources to gather samples, and alpha captures bypass automatic processing. Test-frame and comparison previews use the same selection logic as export.

Transparency plus explicit processing is rejected in this release. Use transparent ProRes 4444 with No processing; decoded alpha is covered by a regression test.

## Timing

Clock targets use `round(source_seconds * 1000)` and the difference from the previous absolute target. They do not repeatedly add a rounded 17ms frame interval, which drifts at 60fps. Clock milliseconds are not fractional-frame temporal supersampling. CSS animations need the WAAPI adapter; HTML media needs Media mode. Static duplicates exactly one PNG. Realtime sampling remains best-effort.

## Evidence

The included tests verify dimensions, frame counts, RGB byte identity, YUV swatches, H.264 bitstream color headers, alpha, static frame equality, clock advancement and cancellation. They do not prove arbitrary website correctness or subjective superiority of a sharpening preset.

Primary references checked during the review:
- Playwright Clock: https://playwright.dev/python/docs/clock
- Clock API: https://playwright.dev/python/docs/api/class-clock
- Locator screenshots: https://playwright.dev/python/docs/api/class-locator
- Python process signaling: https://docs.python.org/3/library/os.html#os.kill
- GitHub release assets: https://docs.github.com/en/rest/releases/assets
