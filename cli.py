from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from models import (
    DEFAULT_RECIPE_KEY,
    MAX_CPU_THREADS,
    MAX_CAPTURE_WORKERS,
    CaptureMode,
    GeometryMode,
    LoadStrategy,
    SourceKind,
    TimelineMode,
    dataclass_to_dict,
)
from presets import OUTPUT_PROFILES, PROCESSING_PRESETS, RECIPES
from renderer import HtmlVideoRenderer, choose_output_path


def _cpu_threads(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("CPU threads must be an integer.") from exc
    if not 0 <= count <= MAX_CPU_THREADS:
        raise argparse.ArgumentTypeError(f"CPU threads must be between 0 and {MAX_CPU_THREADS}.")
    return count


def _capture_workers(value: str) -> int:
    count = _cpu_threads(value)
    if count > MAX_CAPTURE_WORKERS:
        raise argparse.ArgumentTypeError(f"Capture workers must be between 0 and {MAX_CAPTURE_WORKERS}.")
    return count


def _frame_buffer(value: str) -> int:
    try:
        size = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Frame buffer must be an integer MiB value.") from exc
    if not 16 <= size <= 4096:
        raise argparse.ArgumentTypeError("Frame buffer must be between 16 and 4096 MiB.")
    return size


def _crf(value: str) -> float:
    try:
        crf = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("CRF must be a number.") from exc
    if not math.isfinite(crf) or not 1 <= crf <= 51:
        raise argparse.ArgumentTypeError("CRF must be between 1 and 51.")
    return crf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="html-video-export",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, width=88),
        description="Analyze and export HTML animations/pages to video.",
    )
    parser.add_argument("sources", nargs="+", help="HTML file path(s) or http(s) URL(s).")
    parser.add_argument("--recipe", choices=RECIPES, default=DEFAULT_RECIPE_KEY,
                        help="Workflow recipe (default: social_delivery; sharp, compatible MP4).")
    parser.add_argument("--probe", action="store_true", help="Analyze only; write JSON to stdout.")
    parser.add_argument("--deep-analysis", action="store_true", help="Sample frames for content classification.")
    parser.add_argument("--output", type=Path, help="Output file for one source, or directory for several sources.")
    parser.add_argument("--scale", type=float)
    parser.add_argument("--fps", type=int)
    parser.add_argument("--cpu-threads", type=_cpu_threads, default=0, metavar="N",
                        help="Encoder threads per export; 0 selects a CPU-aware budget (default).")
    parser.add_argument("--capture-workers", type=_capture_workers, default=0, metavar="N",
                        help="Browser lanes: 0=auto for declared-safe sources; 1=sequential; 2+ asserts independent seeking.")
    parser.add_argument("--frame-buffer-mb", type=_frame_buffer, default=256, metavar="MIB",
                        help="PNG buffer budget; browser and encoder memory are additional (default: 256).")
    parser.add_argument("--no-fast-capture", action="store_true",
                        help="Use legacy element screenshot waits instead of the guarded viewport fast path.")
    parser.add_argument("--profile", choices=OUTPUT_PROFILES)
    parser.add_argument("--crf", type=_crf, metavar="CRF",
                        help="Override H.264/H.265/AV1 CRF (1-51); omit to use the profile default.")
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
    from models import AUDIO_BITRATES, AUDIO_SAMPLE_RATES, AUDIO_CHANNELS
    sound = parser.add_argument_group('External soundtrack')
    inputs = sound.add_mutually_exclusive_group()
    inputs.add_argument('--audio', type=Path, metavar='FILE',
                        help='Attach one audio file to every input (WAV/M4A/MP3 and more).')
    inputs.add_argument('--audio-map', nargs=2, action='append', metavar=('HTML', 'AUDIO'),
                        help='Match a track to one input; repeat for a batch. Unmapped inputs stay silent.')
    inputs.add_argument('--audio-dir', type=Path, metavar='DIR',
                        help='Match each local HTML to a unique same-basename track in DIR.')
    sound.add_argument('--audio-mode', choices=('none', 'trim', 'loop'),
                       help='Use once and pad/trim (default with a track), loop, or disable audio.')
    sound.add_argument('--audio-volume', type=float, default=1.0, metavar='GAIN',
                       help='Linear gain: 0=mute, 1=original, 0.5=half amplitude.')
    sound.add_argument('--audio-offset', type=float, default=0.0, metavar='SECONDS',
                       help='Signed sync: + delays audio; - skips its beginning. Video length is unchanged.')
    sound.add_argument('--audio-fade-in', type=float, default=0.0, metavar='SECONDS',
                       help='Fade from the audible start, after any leading silence.')
    sound.add_argument('--audio-fade-out', type=float, default=0.0, metavar='SECONDS',
                       help='Fade ending at the video end.')
    sound.add_argument('--audio-bitrate', type=int, choices=AUDIO_BITRATES, default=0,
                       help='AAC/Opus kbps; 0=profile default. PCM masters ignore bitrate.')
    sound.add_argument('--audio-sample-rate', type=int, choices=AUDIO_SAMPLE_RATES, default=0,
                       help='Output Hz; 0=profile default. WebM/Opus requires 48000.')
    sound.add_argument('--audio-channels', type=int, choices=AUDIO_CHANNELS, default=0,
                       help='0=profile default, 1=mono, 2=stereo.')
    sound.add_argument('--audio-normalize', action='store_true',
                       help='Optional single-pass loudness normalization to -16 LUFS before gain/fades.')
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _audio_source_key(value: str) -> str:
    import os
    return value if value.startswith(('http://', 'https://')) else os.path.normcase(str(Path(value).expanduser().resolve()))


def _configure_audio(job, source: str, args: argparse.Namespace) -> None:
    from media_pipeline import audio_source_path, find_matching_audio
    from models import AudioConfig, AudioMode

    path = args.audio
    mappings = {}
    for html, track in args.audio_map or []:
        key = _audio_source_key(html)
        if key in mappings:
            raise ValueError(f'Duplicate --audio-map for {html}.')
        mappings[key] = track
    if mappings:
        path = mappings.get(_audio_source_key(source))
    if args.audio_dir is not None:
        directory = args.audio_dir.expanduser()
        if not directory.is_dir():
            raise ValueError(f'Audio directory not found: {directory}')
        path = find_matching_audio(job.source, directory.iterdir())
        if path is None:
            raise ValueError(f'No matching audio for {job.source.display_name} in {directory}.')
    has_input_option = bool(args.audio or args.audio_map or args.audio_dir)
    has_settings = (args.audio_mode not in (None, 'none') or args.audio_volume != 1.0
                    or args.audio_offset != 0.0 or args.audio_fade_in != 0.0
                    or args.audio_fade_out != 0.0 or args.audio_bitrate != 0
                    or args.audio_sample_rate != 0 or args.audio_channels != 0
                    or args.audio_normalize)
    if not has_input_option and has_settings:
        raise ValueError('Audio settings require --audio, --audio-map, or --audio-dir.')
    job.render.audio = AudioConfig(
        path=str(audio_source_path(str(path))) if path else '',
        mode=AudioMode(args.audio_mode or 'trim') if path else AudioMode.NONE,
        volume=args.audio_volume, offset_seconds=args.audio_offset,
        fade_in_seconds=args.audio_fade_in, fade_out_seconds=args.audio_fade_out,
        bitrate_kbps=args.audio_bitrate, sample_rate_hz=args.audio_sample_rate,
        channels=args.audio_channels, normalize_loudness=args.audio_normalize,
    )
    job.render.audio.validate()


def make_job(source: str, args: argparse.Namespace):
    kind = SourceKind.URL if source.startswith(("http://", "https://")) else SourceKind.FILE
    value = source if kind == SourceKind.URL else str(Path(source).expanduser().resolve())
    job = RECIPES[args.recipe].create_job(value, kind)
    job.render.cpu_threads = args.cpu_threads
    job.render.capture_workers = args.capture_workers
    job.render.frame_buffer_mb = args.frame_buffer_mb
    job.render.fast_capture = not args.no_fast_capture
    if args.scale is not None:
        job.render.scale = args.scale
    if args.fps is not None:
        job.render.fps = args.fps
    if args.profile:
        job.render.output_profile_key = args.profile
    if args.crf is not None:
        job.render.video_crf = args.crf
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
    _configure_audio(job, source, args)
    job.render.overwrite = args.overwrite
    return job


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    known_sources = {_audio_source_key(source) for source in args.sources}
    mapped_sources = set()
    for source, _track in args.audio_map or []:
        key = _audio_source_key(source)
        if key not in known_sources or key in mapped_sources:
            print(f"Invalid --audio-map: {source} is not an input or is mapped more than once.", file=sys.stderr)
            return 2
        mapped_sources.add(key)
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
