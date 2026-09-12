"""End-to-end browser render benchmark; reports timings, never asserts speed.

No external assets. All outputs are fully decoded; PNG generation, unlike an
encoding-only benchmark, is inside the measured render. Use at least two trials.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from media_pipeline import available_cpu_count
from models import LoadStrategy, TimelineMode
from presets import RECIPES
from renderer import HtmlVideoRenderer


def source_html(width, height, duration):
    return f'''<!doctype html><style>html,body{{margin:0}}#stage{{width:{width}px;height:{height}px;background:#142844}}</style>
<div id="stage" data-video-export data-video-duration="{duration}" data-video-export-parallel-safe="true">
<svg width="{width}" height="{height}" viewBox="0 0 1280 720">
<rect width="1280" height="720" fill="#142844"/>
<rect width="256" height="128" fill="#ff00ff"/><rect x="256" width="256" height="128" fill="#00ff00"/>
<g id="shapes"></g><text x="48" y="670" fill="white" font-size="40">Deterministic browser render</text></svg></div>
<script>
const root=document.getElementById('shapes');
for(let i=0;i<180;i++){{const n=document.createElementNS('http://www.w3.org/2000/svg','rect');
n.setAttribute('width',20);n.setAttribute('height',20);n.setAttribute('fill',`hsl(${{i*17%360}},65%,60%)`);root.appendChild(n);}}
window.seekTo=t=>{{[...root.children].forEach((n,i)=>{{n.setAttribute('x',20+(i*47+t*93)%1200);
n.setAttribute('y',160+(i*23)%420);n.setAttribute('rx',i%5);}});}};
</script>'''


def run(args):
    variants = [('legacy_serial', 1, False), ('fast_serial', 1, True)]
    variants += [(f'parallel_{n}', n, True) for n in args.workers]
    rows=[]
    with tempfile.TemporaryDirectory(prefix='hves-capture-benchmark-') as directory:
        root=Path(directory);source=root/'benchmark.html'
        source.write_text(source_html(args.width,args.height,args.frames/60),encoding='utf-8')
        for trial in range(args.trials):
            # Rotate order to avoid attributing all warm-up effects to baseline.
            order=variants[trial%len(variants):]+variants[:trial%len(variants)]
            for label,workers,fast in order:
                job=RECIPES['social_delivery'].create_job(str(source))
                job.source.load_strategy=LoadStrategy.EMBEDDED
                job.timeline.mode=TimelineMode.JAVASCRIPT_FUNCTION
                job.render.cpu_threads=args.encoder_threads
                job.render.capture_workers=workers;job.render.fast_capture=fast
                output=root/f'{trial}-{label}.mp4'
                started=time.perf_counter()
                with HtmlVideoRenderer(page_timeout_ms=30000) as renderer:
                    result=renderer.render(job,output)
                    stats=renderer.last_render_stats;exe=renderer._ffmpeg_exe
                elapsed=time.perf_counter()-started
                # Framemd5 decodes the complete movie without accumulating raw 4K frames.
                check=subprocess.run([exe,'-v','error','-i',str(output),'-map','0:v:0',
                    '-f','framemd5','-threads','1','pipe:1'],capture_output=True,text=True,timeout=120)
                if check.returncode:raise RuntimeError(check.stderr)
                frames=[line for line in check.stdout.splitlines() if line and not line.startswith('#')]
                if len(frames)!=args.frames:raise AssertionError(f'{label}: {len(frames)} decoded frames')
                rows.append(dict(variant=label,trial=trial,seconds=elapsed,fps=result.frame_count/elapsed,
                                 decoded_frames=len(frames),stages=stats))
                print(f'{label}: {elapsed:.3f}s, {result.frame_count/elapsed:.2f} fps; full decode passed', file=sys.stderr, flush=True)
                output.unlink()
    summary={label:dict(median_seconds=statistics.median(r['seconds'] for r in rows if r['variant']==label),
                        median_fps=statistics.median(r['fps'] for r in rows if r['variant']==label))
             for label,_,_ in variants}
    environment = dict(python=sys.version.split()[0], platform=platform.platform(),
                       available_logical_cpus=available_cpu_count(),
                       ffmpeg=subprocess.check_output([exe, '-version'], text=True).splitlines()[0])
    return dict(environment=environment,width=args.width,height=args.height,frames=args.frames,encoder_threads=args.encoder_threads,
                trials=args.trials,summary=summary,runs=rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--width',type=int,default=1280)
    parser.add_argument('--height',type=int,default=720)
    parser.add_argument('--frames',type=int,default=120)
    parser.add_argument('--trials',type=int,default=2)
    parser.add_argument('--workers',type=int,nargs='+',default=[2,4])
    parser.add_argument('--encoder-threads',type=int,default=4)
    args=parser.parse_args()
    if (min(args.width,args.height,args.frames,args.trials,args.encoder_threads)<1 or
        args.width%2 or args.height%2 or not all(2<=n<=16 for n in args.workers)):
        parser.error('Use positive dimensions/frame counts/trials/threads, even dimensions and 2..16 workers.')
    print(json.dumps(run(args),indent=2))


if __name__=='__main__':main()
