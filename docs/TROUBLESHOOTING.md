# Troubleshooting

**App will not start:** Use the new full folder, not old modules mixed with new ones. Run the installer, then launch from a terminal to retain errors. Confirm Python includes Tkinter (`python -m tkinter`).

**No browser:** Run `python -m playwright install chromium`. On Linux, `python -m playwright install --with-deps chromium` may be needed. An explicit browser override is HTML_VIDEO_BROWSER.

**Missing codec/filter:** The app preflights the actual selected FFmpeg. A system build can be selected with HTML_VIDEO_FFMPEG. Do not assume every platform wheel has the same codec list.

**Wrong/blurred size:** Analyze the target, use intrinsic locking for authored SVG, and check backing image/canvas/video resolution. At 2x, intrinsic CSS dimensions double. No processing + RGB lossless is the reference path. Test in a player at 100% pixel scale before judging a downscaled preview.

**Local assets blocked:** Embedded mode cannot grant local-file permissions. Export stops rather than silently omitting required relative assets. Use allowed File URL/Loopback loading or a self-contained bundle; administrator policy may block both navigation modes.

**No duration:** Supply a duration/trim end in job settings or repair source duration metadata. Infinite/unknown timing is not assigned a fallback length.

**Source does not move:** Select the adapter matching its animation system and confirm the source implements the advertised seek hook. Static is intentionally frozen. CSS and video do not become seekable through the JavaScript virtual clock.

**Cancel waits:** Browser calls finish or time out before cancellation is handled. The FFmpeg watchdog also handles cancellation during pipe writes/finalization. Infinite synchronous JavaScript can still wedge a renderer; close the app/process if necessary.

**Update fails:** Upgrade from 1.5.0 using a fresh-folder installer. See GITHUB_SETUP.md for log/backup handling, exact asset naming and changed-requirements restrictions. Never use the updater against an untrusted release.
