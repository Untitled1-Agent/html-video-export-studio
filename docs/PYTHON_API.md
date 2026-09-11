# Python API and project files

[Back to README](../README.md)

The API uses `JobConfig` dataclasses from `models.py`, recipes from `presets.py`, and `HtmlVideoRenderer` from `renderer.py`. Use explicit source paths. Calls are synchronous. Create a renderer in the thread that will use and close it; do not share browser objects across threads.

## Probe and exact-source export

```python
from pathlib import Path
from presets import RECIPES
from renderer import HtmlVideoRenderer

job = RECIPES['exact_source_master'].create_job(str(Path('animation.html').resolve()))
job.render.scale = 2
job.render.fps = 60

with HtmlVideoRenderer() as renderer:
    probe = renderer.probe(job)
    if probe.has_errors:
        raise RuntimeError('; '.join(d.message for d in probe.diagnostics if d.severity == 'error'))
    result = renderer.render(job, output_path=Path('exports/master.mp4'))
    print(result.output_path, result.frame_count, result.output_duration_seconds)
```

Explicit output paths still obey the profile's container extension and the overwrite setting. `ExportResult.output_path` is the actual path after any collision suffixing. Probe results describe the source; output duration after trims/holds is in the export result.

## External audio and transparency

```python
from models import AudioMode

job.render.audio.mode = AudioMode.TRIM
job.render.audio.path = str(Path('soundtrack.wav').resolve())
job.render.audio.volume = 0.8
job.render.audio.offset_seconds = 0.25
job.render.audio.fade_out_seconds = 0.5
```

For transparency set `job.capture.transparent_background = True`, use profile `prores_4444_mov`, choose No processing, and write a `.mov` file. This retains captured alpha; it does not remove a page's authored opaque background. No alpha-preserving enhancement pipeline is exposed in 1.5.2.

## Cancellation and progress

```python
from threading import Event

cancel = Event()
run = Event()
run.set()  # clear() to pause between frames; set() to resume

def progress(payload):
    print(payload['frame'], payload['total_frames'])
    # Another thread can call cancel.set(). Never manipulate Playwright there.

with HtmlVideoRenderer() as renderer:
    result = renderer.render(job, Path('exports/master.mp4'),
                             progress_callback=progress,
                             cancel_event=cancel, run_event=run)
```

Catch `ExportCancelled` separately from `ExportError` as needed. A callback exception fails the export and triggers cleanup; callbacks should be fast and should not directly touch Tk widgets. Progress callbacks run on the rendering thread. For parallelism create the job and renderer inside each ThreadPoolExecutor task; use a bounded pool, not one thread per frame.

## Native PNG preview

```python
with HtmlVideoRenderer() as renderer:
    path, probe = renderer.capture_test_frame(job, Path('exports/check.png'),
                                             at_seconds=1.0, apply_processing=False)
```

`at_seconds` is a source timestamp and is clamped to the source bounds. The PNG has native capture resolution; comparison images are fit-to-panel. Writes are transactional and require the PNG extension. A cancelled or failed processed preview does not replace an existing good file.

## Project persistence

```python
from models import QueueJob
from project_io import ProjectDocument, save_project, load_project

save_project(ProjectDocument([QueueJob('job-1', job)], name='Demo'), Path('demo.hves.json'))
loaded = load_project(Path('demo.hves.json'))
job_again = loaded.jobs[0].config
```

Projects are versioned JSON with format `html-video-export-studio-project`, an integer `format_version`, name/notes, and job configurations. Save is via temporary file and replacement. Load enforces a 10 MB file limit and validates job settings. It creates new queued IDs rather than restoring worker state. Relative file/audio/output paths resolve against the project location. Assets are not embedded. Keep sources available and never put secrets in URL/query fields, audio paths, notes, or diagnostic attachments.
