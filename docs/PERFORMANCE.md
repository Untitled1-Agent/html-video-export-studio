# CPU parallelism

[Back to README](../README.md) · [Configuration reference](CONFIGURATION.md)

## Controls

Video export no longer fixes FFmpeg to two encoder threads and one filter thread.
In **Job Settings → Output → CPU threads per export**, choose **0** (automatic)
or an explicit integer from **1 to 256**. The setting is saved in project jobs.
The CLI equivalent is:

```bash
python cli.py source.html --cpu-threads 0 --output exports
python cli.py source.html --cpu-threads 16 --output exports
```

With the Python API, set `job.render.cpu_threads = 16`. Existing projects without
this field use 0. Choosing a visual recipe preserves an explicit CPU setting.
No machine-specific resolved allocation is written back into the saved project.

## Automatic allocation

The encoder budget is `min(32, max(1, (available_logical_cpus - 1) // concurrent_jobs))`.
The one-CPU subtraction leaves scheduling headroom for capture and the interface;
it does not pin or exclusively reserve a core. Detection uses process CPU count
and/or affinity where supported, then the system CPU count, with a minimum of one.
The 32-thread automatic ceiling limits frame-buffer memory growth on large hosts.
An explicit override can exceed it.

Desktop queue concurrency is the smaller of the selected worker count and the
number of queued jobs. A single queued video therefore receives a single-job
budget even when the queue is configured for eight workers. On 16 available
logical CPUs, one video gets 15 encoder threads; two concurrent videos get seven
each. The allocation is snapshotted before dispatch and stays fixed for that run.
It is not dynamically increased when another job finishes.

Video decoding uses at most four threads and filters use at most eight, each no
larger than the resolved encoder budget. Explicit values are **per export** and
are not divided by the queue worker count. Use care combining a large override
with several queue workers. One selects a one-thread pool for each stage, not a
single-thread process: FFmpeg and Chromium have other internal threads.

The CLI processes files sequentially. Independent CLI processes or independent
Python renderer instances do not coordinate their budgets automatically. CPU-time
quotas, other applications' load, and available RAM are not measured. Set a manual
budget for constrained containers or when running several independent instances.

## What is and is not accelerated

This changes FFmpeg video decoder, filter, and encoder parallelism. Browser page
seeking and screenshots remain ordered on their owning Playwright thread. Timer,
media, and stateful JavaScript timelines are not divided among shared browser
threads. PNG previews keep their existing conservative processing path.

More threads can improve encoding-heavy work; capture-bound, small, or static
jobs may see less benefit or even overhead. Start with automatic mode; for one
large video use one queue worker before increasing simultaneous jobs. The log
reports the effective decoder/filter/encoder budgets. These are requested pool
sizes, not guarantees that a particular codec can keep every CPU busy.

Codec quality, resolution, frame rate, audio settings, and the explicit color
pipeline are unchanged. In particular, `-noautoscale`, H.264 Main/4:2:0 delivery,
CAS strength, and the unfiltered lossless-RGB path are retained. Threading can
change compressed bytes in lossy encoders; file byte identity is not promised.

## Reproducible measurement

```bash
python tools/benchmark_cpu.py --threads 0 4 8 16 --repeats 3 > cpu-benchmark.json
```

The benchmark compares identical generated PNGs, including RGB/RGBA transitions,
using the previous 2-encoder/1-filter/2-decoder allocation and the requested new
budgets. It preserves the same delivery profile and CAS strength. Frame generation
and validation are outside the timed region. Every output is fully decoded and
its frame count/profile checked. Files are temporary. JSON contains each sample,
median encoding fps, requested pools, and average child CPU-core use on systems
providing `resource.getrusage`.

This is an **encoding microbenchmark**, not an end-to-end browser-rendering result.
Measure a representative source export on the target machine too. Do not put
speed thresholds in CI: runner contention makes them unreliable.

## References

- [FFmpeg stream options and filter threads](https://ffmpeg.org/ffmpeg.html)
- [Python process CPU count and affinity](https://docs.python.org/3.13/library/os.html#os.process_cpu_count)
- [Playwright Python threading guidance](https://playwright.dev/python/docs/library#threading)
