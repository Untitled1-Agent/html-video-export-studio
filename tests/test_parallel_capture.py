"""Real ordered multicore capture and lifecycle regressions (no required skips)."""
from __future__ import annotations

import copy
import io
import json
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from media_pipeline import OrderedCapturePool, CapturePoolError, capture_plan, snapshot_for_export
from models import JobConfig, SourceSpec, TimelineMode, LoadStrategy, dataclass_to_dict, job_config_from_dict
from presets import RECIPES, PROCESSING_PRESETS, apply_recipe
from renderer import HtmlVideoRenderer, ExportCancelled

HTML = '''<!doctype html><style>
html,body{margin:0;background:#184460}#stage{width:192px;height:128px;background:#142844}
</style><div id="stage" data-video-export data-video-duration="1" data-video-export-parallel-safe="true">
<svg width="192" height="128"><rect width="64" height="64" fill="#ff00ff"/>
<rect x="64" width="64" height="64" fill="#00ff00"/><rect x="128" width="64" height="64" fill="#00ffff"/>
<rect id="moving" y="72" width="16" height="32" fill="white"/></svg></div>
<script>window.seekTo=t=>{document.querySelector('#moving').setAttribute('x',Math.round(t*140));};</script>'''


def fixture_job(path):
    job = RECIPES['exact_source_master'].create_job(str(path))
    job.source.load_strategy = LoadStrategy.EMBEDDED
    job.render.scale = 1
    job.render.fps = 12
    job.render.cpu_threads = 2
    job.timeline.mode = TimelineMode.JAVASCRIPT_FUNCTION
    return job


def decoded(exe, path, pix_fmt='rgb24'):
    proc = subprocess.run([exe, '-v', 'error', '-i', str(path), '-f', 'rawvideo',
                           '-pix_fmt', pix_fmt, '-threads', '1', 'pipe:1'],
                          capture_output=True, timeout=60)
    if proc.returncode:
        raise AssertionError(proc.stderr.decode(errors='replace'))
    return proc.stdout


class ParallelConfigurationTests(unittest.TestCase):
    def test_round_trip_and_recipe_keep_capture_options(self):
        job = JobConfig(SourceSpec('source.html'))
        job.render.capture_workers = 4
        job.render.frame_buffer_mb = 128
        job.render.fast_capture = False
        restored = job_config_from_dict(dataclass_to_dict(job))
        fresh = apply_recipe(restored, 'exact_source_master')
        self.assertEqual((fresh.render.capture_workers, fresh.render.frame_buffer_mb,
                          fresh.render.fast_capture), (4,128,False))

    def test_cli_capture_settings_and_rejection(self):
        from cli import build_parser, make_job
        parser=build_parser()
        args=parser.parse_args(['source.html','--capture-workers','4',
                               '--frame-buffer-mb','128','--no-fast-capture'])
        job=make_job('source.html',args)
        self.assertEqual((job.render.capture_workers,job.render.frame_buffer_mb,job.render.fast_capture),(4,128,False))
        for flag,value in [('--capture-workers','17'),('--capture-workers','1.5'),
                           ('--frame-buffer-mb','15'),('--frame-buffer-mb','text')]:
            from contextlib import redirect_stderr
            with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                parser.parse_args(['source.html',flag,value])

    def test_old_project_gets_safe_auto(self):
        job = job_config_from_dict({'source':{'value':'source.html'}})
        self.assertEqual(job.render.capture_workers,0)
        with patch('media_pipeline.available_cpu_count',return_value=24):
            self.assertEqual(capture_plan(job,TimelineMode.OM_EVENT,1920,1080,600,False).workers,1)
            self.assertEqual(capture_plan(job,TimelineMode.OM_EVENT,1920,1080,600,True).workers,7)

    def test_memory_limits_frame_payload_and_auto_accounts_for_queue(self):
        job = JobConfig(SourceSpec('source.html'))
        with patch('media_pipeline.available_cpu_count',return_value=24):
            first = capture_plan(job,TimelineMode.OM_EVENT,1920,1080,600,True)
            snapshot = snapshot_for_export(job,2)
            second = capture_plan(snapshot,TimelineMode.OM_EVENT,1920,1080,600,True)
            self.assertLess(second.workers,first.workers)
            job.render.frame_buffer_mb = 16
            self.assertEqual(capture_plan(job,TimelineMode.OM_EVENT,3840,2160,600,True).workers,1)
            self.assertNotIn('_concurrent_exports',dataclass_to_dict(snapshot.render))

    def test_rejects_progressive_parallel_and_invalid_settings(self):
        job = JobConfig(SourceSpec('source.html'))
        job.render.capture_workers = 4
        for mode in (TimelineMode.REALTIME,TimelineMode.BROWSER_CLOCK):
            with self.assertRaisesRegex(ValueError,'independently seekable'):
                capture_plan(job,mode,192,128,12,True)
        self.assertEqual(capture_plan(job,TimelineMode.STATIC,192,128,12,True).workers,1)
        for field,values in [('capture_workers',[-1,17,True,1.5]),
                             ('frame_buffer_mb',[0,15,4097,True,32.5]),
                             ('fast_capture',[1,'true',None])]:
            for value in values:
                invalid=copy.deepcopy(job)
                setattr(invalid.render,field,value)
                with self.subTest(field=field,value=value),self.assertRaises((TypeError,ValueError)):
                    invalid.validate(False)


class OrderedPoolTests(unittest.TestCase):
    def test_order_parallel_overlap_and_owners(self):
        owners,closed=[],[]
        gate=threading.Barrier(3,timeout=5)
        @contextmanager
        def factory(lane,stop):
            ident=threading.get_ident();owners.append(ident)
            try:
                def capture(index):
                    self.assertEqual(ident,threading.get_ident())
                    if index<3:gate.wait()
                    time.sleep(0.004*(3-lane))
                    return bytes([index]),0.1,0.2
                yield capture
            finally:closed.append(threading.get_ident())
        with OrderedCapturePool(factory,3,12,256,lambda:None) as pool:
            self.assertEqual([pool.frame(i) for i in range(12)],[bytes([i]) for i in range(12)])
        self.assertEqual(len(set(owners)),3)
        self.assertCountEqual(owners,closed)
        self.assertFalse(any(t.is_alive() for t in pool.threads))

    def test_failed_lane_wakes_consumer_and_closes_all_owners(self):
        @contextmanager
        def factory(lane,stop):
            def capture(index):
                if lane==1:raise RuntimeError('fixture failure')
                stop.wait(3)
                return b'x',0.,0.
            yield capture
        with self.assertRaisesRegex(CapturePoolError,'fixture failure'):
            with OrderedCapturePool(factory,2,6,256,lambda:None) as pool:
                pool.frame(0)
        self.assertFalse(any(t.is_alive() for t in pool.threads))

    def test_bounded_production_and_cancel_while_paused(self):
        count=[]
        cancel=threading.Event();running=threading.Event()
        @contextmanager
        def factory(lane,stop):
            def capture(index):
                count.append(index);return b'x',0.,0.
            yield capture
        def check():
            if cancel.is_set():raise ExportCancelled('cancel')
        with self.assertRaises(ExportCancelled):
            with OrderedCapturePool(factory,3,600,256,check,running) as pool:
                time.sleep(.1);self.assertEqual(count,[])
                running.set();time.sleep(.1)
                self.assertEqual(len(count),3)
                running.clear();cancel.set();pool.frame(0)
        self.assertFalse(any(t.is_alive() for t in pool.threads))

    def test_oversized_png_is_rejected(self):
        @contextmanager
        def factory(lane,stop):
            yield lambda index:(b'x'*17,0.,0.)
        with self.assertRaisesRegex(CapturePoolError,'buffer size'):
            with OrderedCapturePool(factory,2,3,16,lambda:None) as pool:pool.frame(0)


class ParallelBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source.html';self.source.write_text(HTML)

    def render_job(self,job,name):
        with HtmlVideoRenderer(page_timeout_ms=15000) as renderer:
            result=renderer.render(job,self.root/name)
            stats=renderer.last_render_stats
            exe=renderer._ffmpeg_exe
        return result,stats,exe

    def test_fast_and_parallel_renders_are_pixel_exact_and_hold_ordered(self):
        job=fixture_job(self.source)
        job.timeline.trim_start=.1;job.timeline.trim_end=.85
        job.timeline.hold_start=.2;job.timeline.hold_end=.3
        raws=[]
        for workers,fast in [(1,False),(1,True),(3,True)]:
            job.render.capture_workers=workers;job.render.fast_capture=fast
            result,stats,exe=self.render_job(job,f'{workers}-{fast}.mp4')
            raws.append(decoded(exe,result.output_path))
            self.assertEqual(len(raws[-1]),result.frame_count*192*128*3)
            self.assertEqual(stats['capture_workers'],workers)
            self.assertGreater(stats['end_to_end_fps'],0)
        self.assertEqual(raws[0],raws[1]);self.assertEqual(raws[0],raws[2])
        self.assertFalse(any(t.name.startswith('hves-capture-') for t in threading.enumerate()))

    def test_default_auto_requires_declaration_and_uses_it(self):
        job=fixture_job(self.source)
        job.render.fps = 72
        with patch('media_pipeline.available_cpu_count',return_value=10):
            result,stats,exe=self.render_job(job,'auto.mp4')
        self.assertEqual(stats['capture_workers'],3)
        self.source.write_text(HTML.replace('data-video-export-parallel-safe="true"',''))
        with patch('media_pipeline.available_cpu_count',return_value=10):
            result,stats,exe=self.render_job(job,'serial.mp4')
        self.assertEqual(stats['capture_workers'],1)

    def test_parallel_delivery_fully_decodes_colors(self):
        job=fixture_job(self.source);job.render.capture_workers=3
        job.render.output_profile_key='h264_420_mp4'
        job.render.processing=PROCESSING_PRESETS['social_compensation'].to_config()
        result,stats,exe=self.render_job(job,'delivery.mp4')
        raw=decoded(exe,result.output_path);stride=192*128*3
        self.assertEqual(len(raw),result.frame_count*stride)
        for i in range(result.frame_count):
            for x,expected in [(32,(255,0,255)),(96,(0,255,0)),(160,(0,255,255))]:
                offset=i*stride+(32*192+x)*3
                self.assertLessEqual(max(abs(a-b) for a,b in zip(raw[offset:offset+3],expected)),5)

    def test_fast_alpha_and_iframe_pixels_match_legacy(self):
        # A scaled, translucent ancestor in an iframe exercises the whole guard chain.
        inner=HTML.replace('background:#142844','background:transparent').replace('background:#184460','background:transparent')
        import html
        self.source.write_text('<body style="margin:0;background:transparent"><div style="opacity:.5;transform:scale(.7)"><iframe srcdoc="'+html.escape(inner,quote=True)+'"></iframe></div></body>')
        job=fixture_job(self.source);job.render.capture_workers=1
        job.capture.transparent_background=True;job.render.output_profile_key='prores_4444_mov'
        captures=[]
        for fast in (False,True):
            job.render.fast_capture=fast
            with HtmlVideoRenderer(page_timeout_ms=15000) as renderer:
                _,_=renderer.capture_test_frame(job,self.root/f'alpha-{fast}.png',at_seconds=.5,apply_processing=False)
            with Image.open(self.root/f'alpha-{fast}.png') as im:captures.append(im.convert('RGBA').tobytes())
        self.assertEqual(captures[0],captures[1])

    def test_worker_render_failure_closes_threads_and_preserves_output(self):
        self.source.write_text(HTML.replace("window.seekTo=t=>{", "window.seekTo=t=>{if(t>.25)throw new Error('intentional seek failure');"))
        job=fixture_job(self.source);job.render.capture_workers=3;job.render.overwrite=True
        dest=self.root/'existing.mp4';dest.write_bytes(b'keep original')
        with HtmlVideoRenderer(page_timeout_ms=15000) as renderer:
            with self.assertRaisesRegex(CapturePoolError,'intentional seek failure'):
                renderer.render(job,dest)
        self.assertEqual(dest.read_bytes(),b'keep original')
        self.assertEqual(list(self.root.glob('.hves-partial-*')),[])
        self.assertFalse(any(t.name.startswith('hves-capture-') for t in threading.enumerate()))

    def test_cancel_parallel_export_preserves_existing_file(self):
        job=fixture_job(self.source);job.render.capture_workers=3;job.render.overwrite=True
        dest=self.root/'existing.mp4';dest.write_bytes(b'keep original')
        event=threading.Event()
        with HtmlVideoRenderer(page_timeout_ms=15000) as renderer:
            def progress(payload):
                if payload['frame']==2:event.set()
            with self.assertRaises(ExportCancelled):renderer.render(job,dest,progress,event)
        self.assertEqual(dest.read_bytes(),b'keep original')
        self.assertEqual(list(self.root.glob('.hves-partial-*')),[])
        self.assertFalse(any(t.name.startswith('hves-capture-') for t in threading.enumerate()))


if __name__=='__main__':unittest.main()
