# Architecture and invariants

[Back to README](../README.md)

## Data flow

```text
Desktop UI / CLI / Python API
        |
        v
Validated JobConfig + source specification
        |
        v
Isolated Playwright context -> load page -> await assets/target
        |
        v
Target + geometry + duration + timing adapter + diagnostics
        |
        v
Seek source timestamp -> browser PNG capture at device scale
        |
        v
Optional processing + explicit codec color conversion
        |
        v
FFmpeg temporary output -> exit/cancel checks -> file publication
```

## Module boundaries

| Module | Responsibility |
| --- | --- |
| `models.py` | Typed dataclasses/enums, input validation, serialization |
| `presets.py` | Recipes, output profiles, processing presets |
| `renderer.py` | Browser lifecycle/loading, target/geometry/timing, preview/export orchestration |
| `processing.py` | Conservative content classifier and enhancement filter construction |
| `media_pipeline.py` | FFmpeg candidates/capability cache, color pipeline, no-clobber commits |
| `app.py` | Tk widgets, job editing, queue scheduling and main-thread event delivery |
| `cli.py` | Sequential batch/export/probe command line |
| `project_io.py` | Versioned JSON projects and relative-path resolution |
| `settings_store.py` | Per-user preferences and independent field migration |
| `updater.py` | GitHub release metadata, HTTPS downloads, ZIP/checksum/version validation |
| `update_helper.py` | Exit waiting, selective backups, file replacement and handled-error rollback |
| `tools/` | Documentation generation, validation, release packaging, initial publication |

There is deliberately no opaque cloud renderer. The desktop queue lives in `app.py`; a separate `queue_runner.py` from older releases is not part of this architecture. The Python API is pragmatic and not a semantically versioned third-party plugin interface yet.

## Concurrency

The Tk main thread owns widgets and variables. Background workers send events through a queue; the UI drains them on its own thread. Configurations are deep-copied before worker dispatch. Each rendering worker creates/uses/closes its own Playwright instance and Chromium resources. Never pass a Locator/Page/Browser to another worker. Playwright documents its API as not thread-safe (see [external references](REFERENCES.md)).

One FFmpeg process belongs to one export. A stderr-draining thread prevents encoder pipe deadlock. A cancellation watchdog can terminate the encoder if writing its input or waiting for finalization blocks. Browser work remains cooperative; a never-yielding JavaScript loop is an acknowledged limitation. Analysis/preview is bounded separately from the rendering queue.

## Timing and pixels

Authored event/function hooks must render the requested timestamp. The application sends seconds, `sync: true`, and `playing: false`; it does not verify a source's creative intent or the presence of every event listener. Web Animations use milliseconds. Browser Clock advances monotonically using absolute rounded target milliseconds, not an accumulated rounded frame interval. Static/hold frames reuse PNG bytes.

Frame count is `ceil(output_seconds * fps - 1e-9)`, with at least one frame. `output_seconds = hold_start + (trim_end - trim_start) + hold_end`. The encoded duration is frame count divided by fps. Sampling at the exclusive source end is avoided to prevent loop wraparound. See the actual `_frame_source_time` implementation for boundary mapping.

Geometry locking is for authored export surfaces. It neutralizes outer preview scaling/overlays but preserves internal effects and intentionally hidden elements. Native device scale increases browser rasterization size. Canvas/video/image backing resolution remains an independent limit. Screenshots are PNG; the exact RGB/no-processing path has no visual filter graph. YUV profiles have explicit conversion even without enhancement.

## Failure and ownership boundaries

Source-loading strategies close failed contexts. A selected page can still fail due to its own script/assets. Duration errors and incompatible alpha/odd-dimension profiles block export. FFmpeg is preflighted against the *effective* filter graph, including color and audio processing.

Frames are streamed to a same-folder temporary output. Publication occurs only after the encoder exits successfully and cancellation has been checked. No-overwrite collision handling is atomic on supported filesystems. A completed file is not a claim that every browser animation behaved correctly; visual review remains necessary.

Preview paths use the same transactional publication concept and return the actual selected path. A `.png` destination is required. Transient ElementHandles used for media seeks are explicitly disposed; ordinary target inspections use Locator evaluation without retaining remote handles.

## Repository boundaries

Release/package contents are allowlisted. Root application files and designated code/docs/examples/tests/tools/validation folders are distributable; random HTML, media, environments, and exports at the root are not. Contributors must update the package manifest when adding a new root module. Source directories are still publishable content, not safe storage for secrets.

The updater validates an archive before writing it and refuses protected paths. It backs up touched files, not entire user directories. It cannot make a many-file installation power-loss atomic; signed release verification and stronger process isolation remain future work. The initial GitHub publishing helper is separate from application updates and never receives connector credentials.
