"""Explicit, opt-in source regression; no private animation is shipped."""
from pathlib import Path
import argparse, json, sys, tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from presets import RECIPES
from renderer import HtmlVideoRenderer, png_dimensions
from models import LoadStrategy, dataclass_to_dict

def main():
    p=argparse.ArgumentParser()
    p.add_argument("source",type=Path)
    p.add_argument("--output",type=Path,help="Also render the entire 2x 60fps lossless master.")
    p.add_argument("--load", choices=[m.value for m in LoadStrategy],default="auto")
    args=p.parse_args()
    job=RECIPES["exact_source_master"].create_job(str(args.source.resolve()))
    job.source.load_strategy=LoadStrategy(args.load)
    job.render.scale=2; job.render.fps=60
    with tempfile.TemporaryDirectory() as td, HtmlVideoRenderer(log_callback=print) as r:
        probe=r.probe(job)
        assert not probe.has_errors, probe.diagnostics
        assert probe.duration_seconds and probe.duration_seconds > 0
        print(json.dumps(dataclass_to_dict(probe), indent=2))
        for fraction in [0,.2,.5,.8,.95]:
            path,pb=r.capture_test_frame(job,Path(td)/"frame.png",at_seconds=probe.duration_seconds*fraction)
            assert png_dimensions(path.read_bytes()) == (probe.output_width,probe.output_height)
            print(f"Frame {fraction:.0%}: {probe.output_width}x{probe.output_height} PASS")
        if args.output:
            result=r.render(job,args.output,progress_callback=lambda q: print(f"{q['frame']}/{q['total_frames']}",flush=True) if q['frame']%120==0 else None)
            print(json.dumps(dataclass_to_dict(result),indent=2,default=str))
    print("REAL SOURCE PASSED")
if __name__=="__main__": main()
