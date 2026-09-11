"""Integration test through the actual browser renderer, every codec/filter."""
import sys, tempfile, subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from presets import OUTPUT_PROFILES, PROCESSING_PRESETS, RECIPES
from models import LoadStrategy, TimelineMode
from renderer import HtmlVideoRenderer, get_ffmpeg_executable, png_dimensions

def main():
    with tempfile.TemporaryDirectory() as td, HtmlVideoRenderer() as r:
        d=Path(td); html=d/'input.html'
        html.write_text('<svg width="96" height="64" data-video-export><rect width="96" height="64" fill="#112233"/><text y="36" fill="#ff4433">Test</text></svg>')
        for key,profile in OUTPUT_PROFILES.items():
            for processing in ['no_processing','ui_subtle','custom']:
                job=RECIPES['exact_source_master'].create_job(str(html))
                job.source.load_strategy=LoadStrategy.EMBEDDED
                job.timeline.mode=TimelineMode.STATIC;job.timeline.manual_duration=.2
                job.render.fps=10;job.render.scale=1;job.render.output_profile_key=key
                if processing=='custom':
                    job.render.processing.sharpen_method='unsharp'
                    job.render.processing.preset_key='custom'
                    job.render.processing.sharpen_strength=.15
                    job.render.processing.contrast=1.02
                else: job.render.processing=PROCESSING_PRESETS[processing].to_config()
                output=d/(key+'_'+processing+'.'+profile.extension)
                result=r.render(job,output)
                data=subprocess.run([get_ffmpeg_executable(),'-v','error','-i',str(output),'-frames:v','1','-f','image2pipe','-c:v','png','-'],capture_output=True,check=True,timeout=30).stdout
                assert png_dimensions(data)==(96,64)
                assert result.frame_count==2
                print(key,processing,'PASS',flush=True)
    print('PROFILE MATRIX PASSED')
if __name__=='__main__': main()
