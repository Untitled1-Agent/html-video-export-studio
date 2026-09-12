"""Compare legacy and CPU-aware FFmpeg export pools on identical PNG frames.

This is an encoding microbenchmark, not a browser/whole-export speed claim.
No timing assertion belongs in CI. Outputs live only in a temporary directory.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw
from media_pipeline import available_cpu_count, ffmpeg_thread_plan, select_ffmpeg
from models import MAX_CPU_THREADS
from presets import RECIPES
from renderer import HtmlVideoRenderer


def child_cpu_seconds() -> float | None:
    try:
        import resource
    except ImportError:
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def make_frames(width: int, height: int, count: int) -> bytes:
    frames = []
    for index in range(count):
        image = Image.new('RGB', (width, height), (24, 29, 41))
        draw = ImageDraw.Draw(image)
        # Many edges, small type, and moving shapes exercise CAS and the encoder.
        for x in range(0, width, 24):
            draw.line((x, 0, (x + index * 3) % width, height), fill=(110, 138, 173), width=2)
        for y in range(20, height, 48):
            draw.text((40, y), f'Frame {index:03d} / CPU export benchmark 0123456789', fill=(230, 235, 241))
        left = int((width - 160) * (0.5 + 0.5 * math.sin(index / 13)))
        draw.rectangle((left, height // 3, left + 120, height // 3 + 100), fill=(232, 62, 103))
        if (index // 15) % 2:
            image = image.convert('RGBA')
        stream = io.BytesIO()
        image.save(stream, 'PNG')
        frames.append(stream.getvalue())
    return b''.join(frames)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--threads', nargs='+', type=int, default=[0, 4, 8])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--frames', type=int, default=90)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    args = parser.parse_args(argv)
    if any(not 0 <= n <= MAX_CPU_THREADS for n in args.threads):
        parser.error(f'--threads must be 0..{MAX_CPU_THREADS}')
    if not 1 <= args.repeats <= 20 or not 1 <= args.frames <= 600:
        parser.error('--repeats must be 1..20 and --frames 1..600')
    if any(n < 32 or n > 3840 or n % 2 for n in (args.width, args.height)):
        parser.error('Dimensions must be even and between 32 and 3840')
    ffmpeg = select_ffmpeg({'libx264'}, {'cas', 'format', 'scale'})
    ffprobe = shutil.which('ffprobe')
    if ffprobe is None:
        parser.error('ffprobe is required to validate benchmark outputs')
    version = subprocess.run([ffmpeg, '-version'], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    payload = make_frames(args.width, args.height, args.frames)  # Outside timed region.
    renderer = HtmlVideoRenderer()
    renderer._ffmpeg_exe = ffmpeg
    settings = [('legacy-2-1-2', None)] + [(f'cpu-threads-{n}', n) for n in dict.fromkeys(args.threads)]
    samples = {label: [] for label, _ in settings}
    with tempfile.TemporaryDirectory(prefix='hves-cpu-benchmark-') as directory:
        for repeat in range(args.repeats):
            # Rotate order to reduce first-run/thermal ordering bias.
            shift = repeat % len(settings)
            for label, requested in settings[shift:] + settings[:shift]:
                job = RECIPES['social_delivery'].create_job('benchmark.html')
                job.render.cpu_threads = 2 if requested is None else requested
                path = Path(directory) / f'{label}-{repeat}.mp4'
                command = renderer._build_ffmpeg_command(job, path, args.frames / 60, 'cas=strength=0.2800')
                plan = ffmpeg_thread_plan(job.render.cpu_threads)
                if requested is None:
                    command[command.index('-filter_threads') + 1] = '1'
                    pools = {'encoder': 2, 'filters': 1, 'decoder': 2}
                else:
                    pools = dict(encoder=plan.encoder, filters=plan.filters, decoder=plan.decoder)
                before_cpu = child_cpu_seconds()
                start = time.perf_counter()
                result = subprocess.run(command, input=payload, capture_output=True, timeout=600)
                elapsed = time.perf_counter() - start
                after_cpu = child_cpu_seconds()
                if result.returncode:
                    raise RuntimeError(result.stderr.decode(errors='replace'))
                # Decode every frame and check count, outside the timed region.
                decoded = subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-err_detect', 'explode',
                                          '-i', str(path), '-f', 'null', '-'], capture_output=True, timeout=120)
                if decoded.returncode:
                    raise RuntimeError(decoded.stderr.decode(errors='replace'))
                probe = subprocess.run([ffprobe, '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                                        '-show_entries', 'stream=nb_read_frames,pix_fmt,profile', '-of', 'json', str(path)],
                                       capture_output=True, text=True, check=True, timeout=120)
                video = json.loads(probe.stdout)['streams'][0]
                if (int(video['nb_read_frames']), video['pix_fmt'], video['profile']) != (args.frames, 'yuv420p', 'Main'):
                    raise RuntimeError(f'Invalid benchmark output: {video}')
                cpu = after_cpu - before_cpu if before_cpu is not None and after_cpu is not None else None
                samples[label].append(dict(seconds=elapsed, fps=args.frames / elapsed,
                                           average_cpu_cores=cpu / elapsed if cpu is not None else None,
                                           pools=pools, bytes=path.stat().st_size))
    report = dict(scope='FFmpeg encoding only; PNG generation and validation excluded',
                  platform=platform.platform(), python=platform.python_version(), ffmpeg=version,
                  available_logical_cpus=available_cpu_count(), dimensions=[args.width, args.height],
                  frames=args.frames, samples=samples,
                  median_fps={label: statistics.median(row['fps'] for row in rows) for label, rows in samples.items()})
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
