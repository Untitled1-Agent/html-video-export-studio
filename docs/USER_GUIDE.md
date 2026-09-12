# Desktop user guide

[Back to README](../README.md)

## Choose the right path

For authored motion graphics, use **Exact source master** as the baseline: 2× browser rasterization, 60 fps, lossless RGB, No processing. For a browser/player-friendly copy choose Social delivery, then inspect its default sharpening and chroma tradeoff. For transparency choose ProRes 4444, transparent capture, and No processing. For a webpage with no natural endpoint explicitly choose a duration and a suitable timing adapter.

Recipes are starting configurations, not automatic fixes for incompatible HTML. The [generated configuration reference](CONFIGURATION.md) lists their exact keys and defaults. The new-job default is **Social delivery — Sharp compatible MP4 (default)**: 1× / 60 fps H.264 Main 4:2:0 with explicit CAS 0.28 sharpening. Exact source master remains an opt-in unfiltered reference recipe. See [delivery compatibility](DELIVERY_COMPATIBILITY.md) for VLC/Android/TikTok guidance, the one-time legacy-default migration, and how to re-render existing jobs without resetting their timing settings.

## Sources and inspection

Use Add HTML for one or multiple files, Add Folder for HTML inputs in a directory, or Add URL for HTTP/HTTPS sources. Local URL credentials are not required; credential-bearing source URLs are rejected. URL jobs must have an output directory because they have no local source folder.

Analyze a selected job before exporting. The inspector reports load strategy, target kind/selector/frame, timing mode, duration and its provenance, intrinsic and output dimensions, geometry policy, and errors/warnings. Deep analysis samples suitable seekable sources to suggest processing. It does not make an incompatible animation seekable.

Errors block export. Warnings deserve inspection: low raster density means assets contain too few pixels for the requested size; very large frames warn of memory use; real-time timing warns about missed states; browser console warnings can reveal missing network assets. The app is not a complete asset-health checker for every loading pattern. A visually wrong preview must not be treated as a passing export merely because no exception occurred.

## Job settings

Double-click/edit a job to choose the capture target, selector index, viewport, geometry, scale, frame rate, timeline, trim/holds, processing, output profile/folder, and external audio. A selector can choose a specific surface; selector index is zero-based across the discovered frames/matches. Automatic marker selection prefers a marked/larger surface, so use a selector when a page contains several candidates.

**Preserve layout** keeps responsive page geometry. **Lock intrinsic** pins a marked composition to authored dimensions and neutralizes outer preview transforms/clipping. It can change the way a generic webpage is laid out; use it for a dedicated export surface, not arbitrarily for every page. Manual element dimensions require intrinsic locking. Geometry is checked during capture; a mismatch is an error rather than an implicit FFmpeg resize.

Scale supports 0.25×–4×. Fractional scale uses Chromium-style pixel rounding. Frame rate is an integer 1–240; 29.97/59.94 rational rates are not currently represented by the model. Higher capture fps does not synthesize motion in a video/raster asset authored at a lower rate.

## Test frame versus Compare

Test frame creates a native-size PNG. The default position is halfway through the detected source duration; the Python API can choose a specific source timestamp. It is a source-time preview, not a proof of the trimmed movie's entire timeline. Compare creates a labelled, fit-to-panel source/processed inspection image. It can resize panels for display, so use the native PNG at 100% for fine-edge judgement.

In 1.5.2 preview destinations must end in `.png`. Processing writes to a temporary sibling file, checks cancellation and success, then publishes the result. Existing preview files follow the job's overwrite setting; otherwise a numbered path is returned. The returned path is authoritative.

No processing is always available. Subtle CAS changes captured pixels; it does not recover genuinely missing detail. Transparent previews/exports require No processing in this release. Inspect both source and processed examples before spending time on a long movie.

## Queue operation

Workers process jobs independently; each owns a renderer/browser and an FFmpeg encoder. The desktop permits 1–8 workers and defaults to 2. Analysis/preview uses a separate two-worker pool. The CLI processes its list sequentially. Workers accelerate independent jobs, not a single export through frame-parallel encoding.

At queue start configurations are deep-copied. Busy jobs cannot be edited, removed, or requeued mid-run; change settings after the run finishes. A new queued job added during a run is for a later run. Progress shows completed frames and estimated throughput, not a promised finish time.

Pause is checked between frames. Cancel Selected sets a per-job signal, including for a pending job. Stop Queue cancels active work and leaves unstarted jobs queued. Closing requests cancellation and waits for workers. A CPU-blocking JavaScript loop can prevent cooperative browser operations from returning; terminate the application/browser process externally when a trusted-but-broken input hangs indefinitely.

## Files and projects

Default naming includes stem, scale, fps, profile, and resolved processing preset. Template fields are `{stem}`, `{scale}`, `{fps}`, `{profile}`, `{processing}`, and `{ext}`. Stem and extension are mandatory; nested fields, format specifications, path characters, Windows reserved device names, and overlong filenames are rejected. A default delivery example is `animation_1x_60fps_h264_420_social_compensation.mp4`.

Outputs are encoded into same-folder temporary files and committed only after success. No-overwrite publication uses atomic non-replacing operations; POSIX needs a filesystem that supports hard links. Use a local filesystem if a network/FAT volume cannot provide that guarantee. Overwrite is explicit, not a workaround to enable on valuable destinations. An export may not use its external soundtrack as the output path.

Projects save job configuration, not movies, assets, inspection results, or live progress. Loading makes fresh queued jobs. Relative source/audio/output paths in a project resolve against the project file directory, not the current working directory. Missing input files need relocating/relinking. Keep customer assets under `user_inputs/` or outside the checkout, and videos under `exports/`; never put them under repository examples/tests/docs to publish them accidentally.

## External audio

Browser/system audio is not captured. In Job Settings choose an external file and Trim or Loop. Short audio is padded, long audio is trimmed, and the video duration controls the final length. Offset adds initial silence. Volume and fades apply on the output timeline. A fade-out longer than the movie follows the configured fade function from time zero; choose meaningful durations. Source trim and visual holds do not automatically retime an unrelated soundtrack.

## Checking results

Inspect the first and last frames and each scene transition. Compare size/rate/frame count with the inspector. A limited player may refuse or poorly scale lossless RGB MP4; export H.264 4:2:0 for compatibility without treating that copy as the pixel reference. Social platforms may transcode uploads. Preserve the original HTML, exact-source master, job project, app version, browser version and FFmpeg version for reproducible work.

## External soundtracks

Match WAV/M4A/MP3 files to individual HTML jobs, use signed audio sync, and adjust gain, looping, fades, encoding quality, or optional normalization. See the [soundtrack guide](AUDIO.md) for desktop controls, CLI batch mapping, timing semantics, and social-delivery defaults.
