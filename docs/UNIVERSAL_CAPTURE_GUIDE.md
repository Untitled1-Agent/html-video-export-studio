# Capture guide

Choose an adapter matching the source; this is not automatic support for every page.

## OM / authored SVG or DOM

A source can expose one export surface with explicit dimensions and a positive duration:

```html
<svg width="1080" height="1920" viewBox="0 0 1080 1920"
     data-om-exportable-video-with-duration-secs="10.2"
     data-om-sync-seek="true"></svg>
```

Its listener must handle `data-om-seek-to-time-frame` with `{time, sync: true, playing: false}`. The duration above is an example belonging to the source, never a renderer fallback. The renderer uses the actual value. Mark synchronous seek only when the event returns after committing the new frame.

Generic examples in `examples/` use `data-video-export` and `data-export-duration`. Custom Event mode sends `{time, sync: true, playing: false}` to the selected element. Function mode calls the configured JavaScript method with time in seconds and preserves the object's `this`; returned promises are awaited. Avoid unrelated free-running timers in deterministic compositions.

## Geometry

Automatic capture selects a marked element where available; explicit Selector plus index removes ambiguity. SVG viewBox, intrinsic canvas/video dimensions or manual dimensions provide a capture size. Intrinsic locking removes preview geometry outside the composition while preserving intentional opacity/filter effects and hidden content. Use Preserve Layout for responsive generic DOM pages. Manual element dimensions require intrinsic locking.

Viewport and full-page modes preserve the browser layout. Dynamic full-page growth can change dimensions and fail an export; viewport capture is safer. Same-origin iframes are exercised in tests. Cross-origin/custom embedded applications need their own compatibility check.

## Timing

Web Animations is for CSS/WAAPI. Media is for seekable video. Browser Clock pauses supported JavaScript clocks before source scripts and advances to absolute millisecond targets; it does not control CSS animations/media decoding or make external fetches deterministic. Time zero is the loaded/ready capture baseline. Realtime is best-effort and should not be used for exact masters. Static captures one image once.

Duration must be finite and source-derived or explicitly supplied. Trim and holds are expressed in seconds. At a fixed FPS, the encoded duration is rounded up to a whole frame; error is less than one frame. Closing holds repeat the last sampled active frame without seeking backward. Very short positive clips still produce one frame.

For late-created elements use Marker or Selector mode. Loading indicators in the supplied bundle format and delayed OM duration values are awaited. Unknown application readiness protocols are not inferred universally.

## Asset loading

Auto loading tries the supported local strategies and embedded fallback. Embedded mode does not grant file/network privileges. A relative asset rejected by Chromium is a source-loading error, not permission to skip it. Use an allowed local server/navigation environment or a truly self-contained bundle. The browser is not modified to circumvent administrator policy.
