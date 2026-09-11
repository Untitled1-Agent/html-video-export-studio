# Changelog

## 1.5.2 — repository preparation


See [release notes](RELEASE_NOTES_1.5.2.md) and [test report](TEST_REPORT.md).

### Fixed

- Long valid output names now use short temporary names, avoiding filesystem component-length overflow.
- CLI output paths expand the home-directory shorthand.
- Source-distribution manifests explicitly list root documentation rather than sweeping up unrelated Markdown.
- Mixed infinite and finite Web Animations no longer inherit a guessed finite duration; explicit duration/trim bounds remain supported.
- Non-finite scene-duration totals are rejected.
- Movie output cannot replace an external audio input; native PNG previews cannot overwrite HTML and now publish transactionally.
- Preview filter failure/cancellation preserves an existing good file; preview names follow overwrite/collision settings.
- FFmpeg preflight selects against effective color conversion and audio filters, and processed previews require a compatible PNG encoder/filter build.
- Media seek ElementHandles are disposed; target inspection no longer retains handles.
- Startup probe failures emit structured JSON; reserved Windows device names are rejected for output templates.
- Update application validation requires CLI and skips local environment/cache/build trees. Restart target validation occurs before file mutation.
- Release packaging no longer sweeps arbitrary HTML/text files from the application root.

### Added

- Private-by-default new GitHub repository publishing helper; dry run, remote/name conflict refusal, allowlisted staging, no force push.
- Detailed README, setup/user/CLI/Python/configuration/architecture/release/security/backlog guides and code-generated reference checks.
- Issue forms, PR template, pinned Actions, Dependabot and documented license-decision status.
- Regression cases reproduced against 1.5.1 and fresh integration/package validation.

## Earlier release history

## 1.5.1 — 2026-09-05

Repair and verification release for the actually shipped 1.5.0 archive. Prior all-passed claims were contradicted by that archive's startup errors and report. This release uses fresh executable tests and an explicit coverage report.

### Startup and desktop
- Fixed the unterminated UI f-string, renderer startup attribute errors and broken dependency check.
- Replaced Tk calls from worker threads with a main-thread event queue; bounded analysis/preview thread creation.
- Snapshotted queued settings; protected pending jobs against editing, cancellation and stale analysis results.
- Fixed stop/pending behavior, end-of-job cancellation, update/busy/unsaved-state races and scheduled callback cleanup on shutdown.
- Fixed minimum-window clipping that hid search, Pause and overall progress; split controls into rows.
- Awaited selected iframe fonts/assets instead of checking only the parent document.
- Repaired supplied GUI, codec and real-source scripts that referenced nonexistent APIs.

### Capture, timing and pixels
- Fixed undefined media-target access, awaited media seeking and bounded promise-based operations.
- Installed/paused JavaScript virtual time before source scripts; used absolute millisecond targets instead of accumulating rounded frame intervals; retained a current date epoch.
- Fixed true static frame reuse, closing holds and negative timestamps on sub-frame clips.
- Preserved function receiver context and waited for bundled/delayed marked roots and OM duration values.
- Fixed fractional pixel rounding, iframe preview geometry and overlay leakage while preserving hidden content and ancestor opacity.
- Added low-resolution raster diagnostics and stopped export when embedded fallback blocks required local assets.
- Used explicit BT.709 RGB/YUV matrices and explicit H.264 color headers. CAS uses planar RGB; No processing RGB has no visual filter chain.
- Removed border/uniform-frame bias in automatic clarity and used no processing for uncertainty; avoided sampling non-rewindable/transparent sources.
- Validated alpha/profile combinations and rejected processing that would drop alpha.

### Encoding, data and updating
- Capability-aware FFmpeg selection; bounded pipe draining, process cleanup and cancellation during encoder finalization.
- Atomic no-clobber publication and explicit-output-folder preservation across collisions.
- External audio padded/trimmed to the visual timeline; required audio-stream mapping and explicit offsets/fades.
- Strict project parsing for enums/booleans/integer FPS/templates; project-relative asset paths.
- CLI batch JSON and meaningful exit codes; dotted output directories and custom hook options.
- Safe Windows process-liveness checks, exit-wait timeout abort, selective backups and rollback of newly added files.
- Strict HTTPS/token redirect handling, exact release/checksum pairing and bounded cross-platform ZIP validation.
- Restricted cleanup to app-owned staging folders; changed dependencies and pip installs require manual updates.
- Added actual GitHub workflows, packaging metadata, validation/release tools and rewritten evidence-based documentation.

No guarantee of correctness for every HTML site, codec/player or platform is implied. See TEST_REPORT.md.
