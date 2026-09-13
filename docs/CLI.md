# Command-line reference

[Back to README](../README.md)

Run `python cli.py --help` from the checkout or install it with `python -m pip install .` and run `html-video-export`. Source arguments are one or more local `.html`/`.htm` paths or HTTP/HTTPS URLs. Quote paths containing spaces. The CLI processes sources **sequentially**, using an isolated context for each; use the desktop queue or independent Python workers for parallel jobs.

## Export examples

```bash
python cli.py "animation.html" --recipe exact_source_master --scale 2 --fps 60 --output exports
python cli.py first.html second.html --recipe exact_source_master --output exports
python cli.py examples/canvas_function.html --timeline javascript_function --seek-function window.demo.renderAt --load embedded --output demo.mp4
python cli.py examples/deterministic_svg_event.html --timeline custom_event --event-name video-export-seek --load embedded --output demo.mp4
python cli.py examples/css_animation.html --recipe web_animation --duration 2 --output css.mp4
python cli.py examples/static_hold.html --recipe static_page_hold --duration 3 --output still.mp4
python cli.py animation.html --recipe exact_source_master --trim-start 1 --trim-end 4 --hold-end .5 --output excerpt.mp4
python cli.py animation.html --profile av1_420_mp4 --crf 18 --output av1.mp4
```

A single source accepts a new `.mp4`, `.mov` or `.webm` file destination. The extension must match the selected profile. An existing directory is a directory even when its name contains dots; a new path without a recognized movie extension is treated as a directory. Multiple sources require a directory. Existing outputs are preserved through suffixing unless `--overwrite` is explicit. A returned output path can therefore differ from the requested basename.

## Inspect as JSON

```bash
python cli.py animation.html --probe
python cli.py first.html second.html --probe --deep-analysis
```

One source emits a JSON object; several emit one JSON array. Per-source errors include `source` and `error`. Browser startup errors now produce the same structure instead of empty stdout. Diagnostics with severity `error` also result in failure status. Logs go to stderr. On success, export mode prints the actual output paths, one per line.

Exit codes: **0** success; **1** source/export/environment failure; **2** command usage or conflicting output arguments; **130** keyboard interruption. Argparse usage errors and interruption are not guaranteed to produce probe JSON, so automation must check exit status before interpreting stdout. Batch errors do not stop subsequent sources unless the renderer cannot start.

## Options

`--recipe` chooses initial settings; explicit flags override them. `--scale`, `--fps`, `--profile`, `--crf`, `--processing` affect the output. `--crf 1..51` is available for H.264/H.265/AV1 profiles; omit it to keep the selected profile default. Lower CRF means higher quality and usually larger files. `--capture`, `--selector`, `--viewport WIDTH HEIGHT`, `--geometry`, and `--load` configure capture/loading. Supplying `--selector` selects selector mode. `--timeline`, `--duration`, `--trim-start`, `--trim-end`, `--hold-start`, and `--hold-end` configure timing. `--seek-function` is a JavaScript property path, not arbitrary Python code; `--event-name` selects a custom DOM event.

[Generated configuration and complete CLI help](CONFIGURATION.md) lists the accepted enum/preset keys. There is **no** `--project` or `--workers` flag in this CLI release. Projects, parallel queueing, selector index, manual intrinsic dimensions, and custom processing details are accessible through the desktop/Python API; external soundtracks are available through the audio flags documented below. This distinction prevents copying a command for a feature that exists only in the GUI.

## External soundtracks

Match WAV/M4A/MP3 files to individual HTML jobs, use signed audio sync, and adjust gain, looping, fades, encoding quality, or optional normalization. See the [soundtrack guide](AUDIO.md) for desktop controls, CLI batch mapping, timing semantics, and social-delivery defaults.
