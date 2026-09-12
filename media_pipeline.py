"""Encoder selection, explicit color conversion and non-destructive output commits."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import imageio_ffmpeg

if TYPE_CHECKING:
    from models import JobConfig

_cache: dict[str, dict[str, set[str]]] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class FFmpegThreadPlan:
    """Separate FFmpeg pools, not a hard process-wide CPU/thread limit."""

    encoder: int
    filters: int
    decoder: int


def available_cpu_count() -> int:
    """Respect affinity where supported, including Python 3.10-3.12.

    Do not cache: an embedding application can change affinity between exports.
    CPU-time quotas and the load from unrelated applications are not measured.
    """
    counts: list[int] = []
    process_count = getattr(os, 'process_cpu_count', None)
    if process_count is not None:
        try:
            count = process_count()
            if count and count > 0:
                counts.append(count)
        except (OSError, NotImplementedError):
            pass
    affinity = getattr(os, 'sched_getaffinity', None)
    if affinity is not None:
        try:
            count = len(affinity(0))
            if count:
                counts.append(count)
        except (OSError, NotImplementedError):
            pass
    if counts:
        return min(counts)
    return max(1, os.cpu_count() or 1)


def resolve_cpu_threads(requested: int = 0, concurrent_jobs: int = 1) -> int:
    """Resolve 0=auto into a bounded encoder pool per simultaneous export.

    Reserve one logical CPU as headroom for capture/UI, divide by the queue's
    effective concurrency, and cap automatic pools at 32 to bound frame buffers.
    Explicit 1..256 overrides are per job and are never silently divided.
    """
    from models import MAX_CPU_THREADS, strict_int

    requested = strict_int(requested)
    concurrent_jobs = strict_int(concurrent_jobs)
    if not 0 <= requested <= MAX_CPU_THREADS:
        raise ValueError(f'CPU threads must be between 0 (automatic) and {MAX_CPU_THREADS}.')
    if concurrent_jobs < 1:
        raise ValueError('Concurrent jobs must be a positive integer.')
    if requested:
        return requested
    return min(32, max(1, (available_cpu_count() - 1) // concurrent_jobs))


def ffmpeg_thread_plan(requested: int = 0) -> FFmpegThreadPlan:
    """Do not give each pipeline stage its own unrestricted all-core pool."""
    encoder = resolve_cpu_threads(requested)
    return FFmpegThreadPlan(encoder=encoder, filters=min(8, encoder), decoder=min(4, encoder))


def snapshot_for_export(job: JobConfig, concurrent_jobs: int = 1) -> JobConfig:
    """Resolve queue CPU allocation only in a deep copy, never the saved job."""
    import copy

    snapshot = copy.deepcopy(job)
    snapshot.render.cpu_threads = resolve_cpu_threads(job.render.cpu_threads, concurrent_jobs)
    return snapshot


def ffmpeg_candidates() -> list[str]:
    explicit = os.environ.get('HTML_VIDEO_FFMPEG') or os.environ.get('FFMPEG_BINARY')
    if explicit:
        value = shutil.which(explicit) or os.path.expanduser(os.path.expandvars(explicit))
        if not Path(value).is_file():
            raise RuntimeError(f'Configured FFmpeg does not exist: {value}')
        return [str(Path(value).resolve())]
    candidates = [shutil.which('ffmpeg')]
    try:
        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    return list(dict.fromkeys(str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()))


def capabilities(executable: str, force: bool = False) -> dict[str, set[str]]:
    with _lock:
        if executable in _cache and not force:
            return _cache[executable]
        result: dict[str, set[str]] = {}
        for kind, pattern in [('encoders', r'^\s*[VAS.][A-Z.]{5}\s+(\S+)'),
                              ('filters', r'^\s*[TSC.]{3}\s+(\S+)')]:
            proc = subprocess.run([executable, '-hide_banner', '-'+kind],
                                  capture_output=True, text=True, timeout=15)
            if proc.returncode:
                raise RuntimeError(f'FFmpeg {kind} check failed: {proc.stderr[-1000:]}')
            result[kind] = set(re.findall(pattern, proc.stdout, re.MULTILINE))
        _cache[executable] = result
        return result


def filter_names(chain: str) -> set[str]:
    return {part.split('=', 1)[0] for part in chain.split(',') if part}


def select_ffmpeg(encoders: set[str], filters: set[str]) -> str:
    errors = []
    for executable in ffmpeg_candidates():
        try:
            available = capabilities(executable)
            missing_e = encoders - available['encoders']
            missing_f = filters - available['filters']
            if not missing_e and not missing_f:
                return executable
            errors.append(f'{executable}: missing encoders {sorted(missing_e)} / filters {sorted(missing_f)}')
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError('No usable FFmpeg build for this job.\n'+'\n'.join(errors))


def color_pipeline(chain: str, rgb_output: bool) -> str:
    """Never resize. Explicit matrices avoid RGB->BT.601 tagged as BT.709.

    RGB + no processing really has no -vf. CAS operates in planar RGB. Luma
    controls use full-chroma YUV; any subsequent conversion back is explicit.
    YUV delivery requires a colorspace conversion, not an enhancement filter.
    """
    if not chain:
        return '' if rgb_output else 'scale=in_range=pc:out_range=tv:out_color_matrix=bt709'
    result = []
    yuv = False
    for part in chain.split(','):
        name = part.split('=', 1)[0]
        wants_yuv = name in {'unsharp', 'eq', 'deband'}
        if wants_yuv and not yuv:
            result.extend(['scale=in_range=pc:out_range=tv:out_color_matrix=bt709', 'format=yuv444p'])
            yuv = True
        elif name == 'cas' and not yuv:
            result.append('format=gbrp')
        result.append(part)
    if rgb_output and yuv:
        result.extend(['scale=in_range=tv:out_range=pc:in_color_matrix=bt709', 'format=gbrp'])
    elif not rgb_output and not yuv:
        result.append('scale=in_range=pc:out_range=tv:out_color_matrix=bt709')
    return ','.join(result)


def commit_output(temporary: Path, destination: Path, *, overwrite: bool) -> Path:
    """Publish only complete files. link() is an atomic no-clobber operation.

    Same-directory temporary output ensures links stay on the same filesystem.
    Unlike exists()+replace(), two workers cannot overwrite each other's output.
    An explicit CLI filename always stays in its requested directory.
    """
    if overwrite:
        os.replace(temporary, destination)
        return destination
    for number in range(1, 10000):
        candidate = destination if number == 1 else destination.with_name(
            f'{destination.stem}_{number}{destination.suffix}')
        try:
            if os.name == 'nt':
                # Windows rename is atomic and refuses an existing destination.
                # It also works on FAT/exFAT, where hard links are unavailable.
                os.rename(temporary, candidate)
            else:
                os.link(temporary, candidate)
        except FileExistsError:
            continue
        except OSError as exc:
            raise OSError('Cannot publish without overwriting on this filesystem. '
                          'Use a local output folder supporting hard links, or explicitly '
                          'enable overwrite for a unique destination.') from exc
        temporary.unlink(missing_ok=True)
        return candidate
    raise OSError('Too many filename collisions in the output directory.')
