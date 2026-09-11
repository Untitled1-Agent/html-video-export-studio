# Compatibility scope

Tested fixtures include authored SVG event timelines, custom methods, canvas, CSS/WAAPI, JavaScript virtual clocks, real-time sampling, static holds, same-origin iframes, transparent ProRes, external audio and viewport/full-page targets. The supplied self-contained Syncnema Cut J bundle was also rendered end to end.

This is not a claim that all HTML pages work. Source-specific async data loading, lazy-loading, WebGL, cross-origin iframes, DRM/live media, HDR and multi-clock compositions need independent validation. Remote sources and relative local assets depend on browser/network permissions. This review environment blocks top-level localhost/file navigation; embedded standalone sources were tested instead. The loader does not bypass policy and now fails visibly when fallback cannot read local assets.

No native Windows/macOS export or installer execution was available in the review environment. The package includes cross-platform code/scripts, with Windows-specific updater behavior covered by mocks. See TEST_REPORT.md for the exact executed evidence.
