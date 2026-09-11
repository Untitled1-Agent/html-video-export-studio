from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from models import (
    CaptureMode,
    GeometryMode,
    LoadStrategy,
    SourceKind,
    TimelineMode,
    dataclass_to_dict,
)
from presets import OUTPUT_PROFILES, PROCESSING_PRESETS, RECIPES
from renderer import HtmlVideoRenderer, choose_output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="html-video-export",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, width=88),
        description="Analyze and export HTML animations/pages to video.",
    )
    parser.add_argument("sources", nargs="+", help="HTML file path(s) or http(s) URL(s).")
    parser.add_argument("--recipe", choices=RECIPES, default="motion_graphics_master")
    parser.add_argument("--probe", action="store_true", help="Analyze only; write JSON to stdout.")
    parser.add_argument("--deep-analysis", action="store_true", help="Sample frames for content classification.")
    parser.add_argument("--output", type=Path, help="Output file for one source, or directory for several sources.")
    parser.add_argument("--scale", type=float)
    parser.add_argument("--fps", type=int)
    parser.add_argument("--profile", choices=OUTPUT_PROFILES)
    parser.add_argument("--processing", choices=PROCESSING_PRESETS)
    parser.add_argument("--capture", choices=[item.value for item in CaptureMode])
    parser.add_argument("--selector", default="")
    parser.add_argument("--timeline", choices=[item.value for item in TimelineMode])
    parser.add_argument("--duration", type=float)
    parser.add_argument("--trim-start", type=float)
    parser.add_argument("--trim-end", type=float)
    parser.add_argument("--hold-start", type=float)
    parser.add_argument("--hold-end", type=float)
    parser.add_argument("--viewport", metavar=("WIDTH", "HEIGHT"), nargs=2, type=int)
    parser.add_argument("--geometry", choices=[item.value for item in GeometryMode])
    parser.add_argument("--load", choices=[m.value for m in LoadStrategy])
    parser.add_argument("--seek-function", default="", help="JavaScript method path for function adapter.")
    parser.add_argument("--event-name", default="", help="Custom seek event name.")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def make_job(source: str, args: argparse.Namespace):
    kind = SourceKind.URL if source.startswith(("http://", "https://")) else SourceKind.FILE
    value = source if kind == SourceKind.URL else str(Path(source).expanduser().resolve())
    job = RECIPES[args.recipe].create_job(value, kind)
    if args.scale is not None:
        job.render.scale = args.scale
    if args.fps is not None:
        job.render.fps = args.fps
    if args.profile:
        job.render.output_profile_key = args.profile
    if args.processing:
        job.render.processing = PROCESSING_PRESETS[args.processing].to_config()
    if args.capture:
        job.capture.mode = CaptureMode(args.capture)
    if args.selector:
        job.capture.mode = CaptureMode.SELECTOR
        job.capture.selector = args.selector
    if args.timeline:
        job.timeline.mode = TimelineMode(args.timeline)
    if args.duration is not None:
        job.timeline.manual_duration = args.duration
    if args.trim_start is not None:
        job.timeline.trim_start = args.trim_start
    if args.trim_end is not None:
        job.timeline.trim_end = args.trim_end
    if args.hold_start is not None:
        job.timeline.hold_start = args.hold_start
    if args.hold_end is not None:
        job.timeline.hold_end = args.hold_end
    if args.viewport:
        job.capture.viewport_width, job.capture.viewport_height = args.viewport
    if args.geometry:
        job.capture.geometry_mode = GeometryMode(args.geometry)
    if args.load: job.source.load_strategy = LoadStrategy(args.load)
    if args.seek_function: job.timeline.javascript_function = args.seek_function
    if args.event_name: job.timeline.custom_event_name = args.event_name
    job.render.overwrite = args.overwrite
    return job


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output is not None:
        args.output = args.output.expanduser()
    known_extensions = {'.'+p.extension for p in OUTPUT_PROFILES.values()}
    is_output_file = bool(args.output and not args.output.is_dir() and
                          args.output.suffix.lower() in known_extensions)
    if len(args.sources) > 1 and is_output_file:
        print("--output must be a directory for multiple sources.", file=sys.stderr)
        return 2
    exit_code = 0
    probes = []
    try:
        with HtmlVideoRenderer(log_callback=lambda m: print(m, file=sys.stderr)) as renderer:
            for source in args.sources:
                try:
                    job = make_job(source, args)
                    if args.probe:
                        result = renderer.probe(job, deep_analysis=args.deep_analysis)
                        probes.append(dataclass_to_dict(result))
                        if result.has_errors: exit_code = 1
                        continue
                    output = None
                    if args.output:
                        if is_output_file:
                            output = args.output.resolve()
                        else:
                            job.render.save_next_to_source = False
                            job.render.output_directory = str(args.output.resolve())
                            output = choose_output_path(job)
                    result = renderer.render(job, output_path=output)
                    print(result.output_path)
                except Exception as exc:
                    exit_code = 1
                    print(f"ERROR {source}: {exc}", file=sys.stderr)
                    if args.probe: probes.append({"source": source, "error": str(exc)})
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Exporter could not start: {exc}", file=sys.stderr)
        if args.probe:
            remaining = args.sources[len(probes):]
            probes.extend({'source': source, 'error': f'Exporter could not start: {exc}'}
                          for source in remaining)
            print(json.dumps(probes[0] if len(args.sources) == 1 else probes, indent=2))
        return 1
    if args.probe:
        # A single valid JSON document even for a batch containing failed jobs.
        print(json.dumps(probes[0] if len(args.sources) == 1 and probes else probes, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
