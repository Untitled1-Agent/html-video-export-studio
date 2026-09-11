"""Regression cases added in the 1.5.1 review. Tests assert behavior/pixels, not log slogans."""
from __future__ import annotations
import base64
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from models import *
from presets import RECIPES, OUTPUT_PROFILES, PROCESSING_PRESETS
from processing import analyze_frames
from renderer import HtmlVideoRenderer, ExportCancelled, ExportError, get_ffmpeg_executable, png_dimensions
from media_pipeline import color_pipeline, commit_output
from project_io import ProjectDocument, save_project, load_project, ProjectError
from updater import (UpdateError, ReleaseInfo, ReleaseAsset, parse_checksum, is_newer_version,
                     normalize_repo, stage_update, validate_zip_members, choose_update_assets,
                     cleanup_staging, _SafeRedirect, _validate_download_url)
import update_helper


def png(color, size=(64,48)):
    b=io.BytesIO();Image.new('RGB',size,color).save(b,'PNG');return b.getvalue()


def decode(path, fmt='rgb24'):
    p=subprocess.run([get_ffmpeg_executable(),'-v','error','-i',str(path),'-f','rawvideo',
                      '-pix_fmt',fmt,'pipe:1'],capture_output=True,timeout=20)
    if p.returncode: raise AssertionError(p.stderr.decode(errors='replace'))
    return p.stdout


def zip_data(members):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        for name,data in members: z.writestr(name,data)
    return b.getvalue()


class ReviewCoreTests(unittest.TestCase):
    def test_project_boolean_numeric_fields_rejected(self):
        from models import job_config_from_dict
        for section,field in [('timeline','manual_duration'),('render','scale')]:
            with self.assertRaises(ValueError):
                job_config_from_dict({'source':{'value':'a.html'},section:{field:True}})

    def test_no_processing_rgb_chain_is_empty(self):
        self.assertEqual(color_pipeline('',True),'')

    def test_yuv_matrix_is_conversion_not_just_metadata(self):
        chain=color_pipeline('',False)
        self.assertIn('out_color_matrix=bt709',chain)
        self.assertNotIn('w=',chain)
        self.assertNotIn('h=',chain)

    def test_sharpen_pipeline_full_chroma(self):
        self.assertTrue(color_pipeline('cas=strength=0.18',True).startswith('format=gbrp,'))
        chain=color_pipeline('eq=contrast=1.1',True)
        self.assertIn('format=yuv444p',chain)
        self.assertIn('in_color_matrix=bt709',chain)

    def test_uniform_bright_and_dark_frames_not_sharpened(self):
        for color in ('white','black','red','#808080'):
            with self.subTest(color=color):
                a=analyze_frames([png(color)])
                self.assertEqual(a.recommended_preset_key,'no_processing')
                self.assertEqual(a.classification,'near_uniform')

    def test_fps_fraction_and_booleans_rejected(self):
        for fps in (0,240.5,60.5,float('nan'),True):
            j=RECIPES['exact_source_master'].create_job('a.html');j.render.fps=fps
            with self.subTest(fps=fps),self.assertRaises(ValueError):j.validate()

    def test_unknown_profiles_and_presets_rejected(self):
        j=RECIPES['exact_source_master'].create_job('a.html')
        j.render.output_profile_key='not-a-codec'
        with self.assertRaises(ValueError):j.validate()
        j.render.output_profile_key='lossless_rgb_mp4';j.render.processing.preset_key='typo'
        with self.assertRaises(ValueError):j.validate()

    def test_project_string_false_is_not_truthy(self):
        d=dataclass_to_dict(RECIPES['exact_source_master'].create_job('a.html'))
        d['render']['overwrite']='false'
        with self.assertRaises(ValueError): job_config_from_dict(d)

    def test_unknown_timeline_rejected_not_auto(self):
        d=dataclass_to_dict(RECIPES['exact_source_master'].create_job('a.html'))
        d['timeline']['mode']='misspelled'
        with self.assertRaises(ValueError): job_config_from_dict(d)

    def test_unsafe_filename_templates(self):
        for value in ('../{stem}.{ext}', r'a\{stem}.{ext}', '{stem.__class__}.{ext}', '{stem!r}.{ext}', '{stem}.mp4:{ext}'):
            j=RECIPES['exact_source_master'].create_job('a.html');j.render.filename_template=value
            with self.subTest(value=value),self.assertRaises(ValueError): j.validate()

    def test_url_validation(self):
        for value in ('file:///etc/passwd','https://','https://user:pass@github.com/'):
            with self.subTest(value=value),self.assertRaises(ValueError):SourceSpec(value,SourceKind.URL).validate()
        SourceSpec('https://example.com/a',SourceKind.URL).validate()

    def test_relative_project_paths_use_project_directory(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);j=RECIPES['exact_source_master'].create_job('assets/a.html')
            j.render.audio.path='audio/tone.wav';j.render.output_directory='renders'
            path=root/'work.hves.json';save_project(ProjectDocument([QueueJob('one',j)]),path)
            loaded=load_project(path).jobs[0].config
            self.assertEqual(loaded.source.value,str(root/'assets/a.html'))
            self.assertEqual(loaded.render.audio.path,str(root/'audio/tone.wav'))

    def test_atomic_commit_same_name_race(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'out.mp4').write_bytes(b'original')
            barrier=threading.Barrier(2); outputs=[];errors=[]
            def worker(i):
                try:
                    src=root/f'.temp{i}';src.write_bytes(bytes([i])*10);barrier.wait()
                    outputs.append(commit_output(src,root/'out.mp4',overwrite=False))
                except Exception as e:errors.append(e)
            ts=[threading.Thread(target=worker,args=(i,)) for i in (1,2)]
            for thread in ts:thread.start()
            for thread in ts:thread.join(5)
            self.assertFalse(errors);self.assertEqual(len(set(outputs)),2)
            self.assertEqual((root/'out.mp4').read_bytes(),b'original')
            self.assertEqual({p.read_bytes() for p in outputs},{bytes([1])*10,bytes([2])*10})

    def test_subframe_duration_never_seeks_negative(self):
        r=HtmlVideoRenderer()
        self.assertEqual(r._frame_source_time(0,60,0,.001,0,0),0)
        self.assertGreaterEqual(r._frame_source_time(1,60,0,.001,0,.1),0)

    def test_end_hold_repeats_last_sample(self):
        r=HtmlVideoRenderer()
        self.assertAlmostEqual(r._frame_source_time(2,10,0,.2,0,.2),.1)
        self.assertAlmostEqual(r._frame_source_time(3,10,0,.2,0,.2),.1)

    def test_semver_numeric_prerelease_and_build(self):
        self.assertTrue(is_newer_version('1.0.0-beta.10','1.0.0-beta.2'))
        self.assertFalse(is_newer_version('1.0.0+build.2','1.0.0+build.1'))
        self.assertFalse(is_newer_version('1.0.0-1','1.0.0-alpha'))

    def test_checksum_must_name_exact_asset(self):
        h='0'*64
        with self.assertRaises(UpdateError):parse_checksum(h+'  other.zip','wanted.zip')
        with self.assertRaises(UpdateError):parse_checksum(h+' wanted.zip\n'+h+' wanted.zip','wanted.zip')
        self.assertEqual(parse_checksum(h+' *wanted.zip','wanted.zip'),h)

    def test_release_selects_exact_zip_and_checksum(self):
        release=ReleaseInfo('1.5.1','v1.5.1','','',(
            ReleaseAsset('GitHub-ready.zip','https://github.com/source',10),
            ReleaseAsset('html-video-export-studio-v1.5.1.zip','https://github.com/update',10),
            ReleaseAsset('html-video-export-studio-v1.5.1.zip.sha256','https://github.com/sum',10)))
        a,c=choose_update_assets(release)
        self.assertEqual(c.name,a.name+'.sha256')
        with self.assertRaises(UpdateError):choose_update_assets(ReleaseInfo('1.5.1','','','',release.assets[:2]))

    def test_fake_github_hosts_and_repo_paths_rejected(self):
        for text in ('https://evil.test/github.com/a/b','https://github.com.evil/a/b','../repo'):
            with self.subTest(text=text),self.assertRaises(ValueError):normalize_repo(text)
        for url in ('http://github.com/a','file:///tmp/a','https://evil.test/a','https://github.com:444/a'):
            with self.subTest(url=url),self.assertRaises(UpdateError):_validate_download_url(url)

    def test_redirect_drops_authorization(self):
        req=urllib.request.Request('https://api.github.com/a',headers={'Authorization':'Bearer dummy'})
        result=_SafeRedirect().redirect_request(req,None,302,'',{},'https://release-assets.githubusercontent.com/a')
        self.assertFalse(result.has_header('Authorization'))

    def test_cross_platform_zip_traversal_rejected(self):
        for name in ('../a','/tmp/a',r'..\outside.py',r'C:\a','dir/../a','dir/NUL.txt','file.py:stream','dir./a','a//b'):
            with self.subTest(name=name):
                with zipfile.ZipFile(io.BytesIO(zip_data([(name,'bad')]))) as z:
                    with self.assertRaises(UpdateError):validate_zip_members(z)

    def test_zip_duplicate_casefold_and_protected_paths(self):
        for members in ([('a.py','a'),('A.py','b')],[('.venv/python','bad')],[('app','bad'),('app/a.py','bad')]):
            with self.subTest(members=members),zipfile.ZipFile(io.BytesIO(zip_data(members))) as z:
                with self.assertRaises(UpdateError):validate_zip_members(z)

    def test_zip_symlink_rejected(self):
        b=io.BytesIO();info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=0o120777<<16
        with zipfile.ZipFile(b,'w') as z:z.writestr(info,'outside')
        with zipfile.ZipFile(io.BytesIO(b.getvalue())) as z:
            with self.assertRaises(UpdateError):validate_zip_members(z)

    def test_staging_validates_all_before_writing(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(UpdateError):stage_update(zip_data([('app/good.py','ok'),('../bad','bad')]),Path(t))
            self.assertEqual(list(Path(t).iterdir()),[])

    def test_cleanup_never_removes_arbitrary_parent(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);src=root/'stage';src.mkdir();(root/'user-data').write_text('keep')
            cleanup_staging(src)
            self.assertTrue((root/'user-data').exists());self.assertTrue(src.exists())

    def test_update_wait_timeout_leaves_running_app_alone(self):
        with patch.object(update_helper,'pid_exists',return_value=True):
            with self.assertRaises(RuntimeError):update_helper.wait_for_exit(999,timeout=0)

    def test_windows_pid_probe_does_not_send_kill(self):
        with patch.object(update_helper.sys,'platform','win32'), patch.object(update_helper,'_windows_pid_exists',return_value=True), patch.object(update_helper.os,'kill',side_effect=AssertionError('must not kill')):
            self.assertTrue(update_helper.pid_exists(123))

    def test_update_rollback_removes_added_files_and_preserves_user_data(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);src=root/'source';dest=root/'installed';src.mkdir();dest.mkdir()
            (src/'a.py').write_text('new');(src/'b.py').write_text('added');(src/'c.py').write_text('new c')
            (dest/'a.py').write_text('old');(dest/'c.py').write_text('old c');(dest/'user.mp4').write_bytes(b'keep')
            original=update_helper._replace_file
            def broken(source,target):
                if source==src/'c.py':raise OSError('injected disk failure')
                return original(source,target)
            with patch.object(update_helper,'_replace_file',side_effect=broken):
                with self.assertRaises(RuntimeError):update_helper.apply_update(src,dest)
            self.assertEqual((dest/'a.py').read_text(),'old')
            self.assertEqual((dest/'c.py').read_text(),'old c')
            self.assertFalse((dest/'b.py').exists())
            self.assertEqual((dest/'user.mp4').read_bytes(),b'keep')

    def test_update_success_does_not_touch_venv_or_exports(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);src=root/'s';dst=root/'d';src.mkdir();dst.mkdir()
            (src/'app.py').write_text('new');(dst/'app.py').write_text('old')
            (dst/'.venv').mkdir();(dst/'.venv'/'keep').write_text('yes')
            backup=update_helper.apply_update(src,dst)
            self.assertEqual((dst/'app.py').read_text(),'new')
            self.assertEqual((backup/'app.py').read_text(),'old')
            self.assertEqual((dst/'.venv'/'keep').read_text(),'yes')


class ReviewBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.renderer=HtmlVideoRenderer(page_timeout_ms=4000)
        # Missing browser is a genuine test setup error, not a false passing skip.
        cls.renderer.start()

    @classmethod
    def tearDownClass(cls):
        cls.renderer.close();cls.temp.cleanup()

    def job(self, text, mode=TimelineMode.STATIC):
        source=self.root/(self._testMethodName+'.html');source.write_text(text)
        j=RECIPES['exact_source_master'].create_job(str(source))
        j.source.load_strategy=LoadStrategy.EMBEDDED;j.timeline.mode=mode
        j.render.scale=1;j.render.fps=10
        return j

    def test_clock_keeps_current_epoch(self):
        import time
        j=self.job('<svg width="80" height="40" data-video-export data-duration=".2"></svg>',TimelineMode.BROWSER_CLOCK)
        prepared=self.renderer._prepare(j)
        try:
            self.assertLess(abs(prepared.loaded.page.evaluate('Date.now()')/1000-time.time()),5)
        finally: prepared.loaded.close()

    def test_delayed_duration_with_virtual_clock(self):
        j=self.job("""<svg width="80" height="40" data-om-exportable-video-with-duration-secs=""></svg>
        <script>setTimeout(()=>document.querySelector('svg').setAttribute('data-om-exportable-video-with-duration-secs','.2'),100)</script>""",TimelineMode.BROWSER_CLOCK)
        probe=self.renderer.probe(j)
        self.assertAlmostEqual(probe.duration_seconds,.2)

    def test_h264_headers_carry_primaries_and_transfer(self):
        # FFmpeg trace_headers reads the SPS itself; no external ffprobe dependency.
        j=self.job('<svg width="80" height="40" data-video-export data-duration=".1"><rect width="80" height="40" fill="red"/></svg>')
        for profile in ['lossless_rgb_mp4','h264_444_mp4','h264_420_mp4']:
            j.render.output_profile_key=profile
            path=self.root/(profile+'_metadata.mp4')
            self.renderer.render(j,path)
            trace=subprocess.run([get_ffmpeg_executable(),'-v','info','-i',str(path),'-c:v','copy','-bsf:v','trace_headers','-f','null','-'],capture_output=True,timeout=20)
            text=trace.stderr.decode()
            import re
            self.assertRegex(text,r'colour_primaries[^\n]*= 1\b')
            self.assertRegex(text,r'transfer_characteristics[^\n]*= 13\b')
            matrix=0 if profile=='lossless_rgb_mp4' else 1
            self.assertRegex(text,rf'matrix_coefficients[^\n]*= {matrix}\b')

    def test_blocked_local_assets_do_not_silently_export(self):
        (self.root/'relative_script.js').write_text("document.querySelector('rect').setAttribute('fill','blue');")
        j=self.job('<svg width="80" height="40" data-video-export data-duration=".1"><rect width="80" height="40" fill="red"/></svg><script src="relative_script.js"></script>')
        probe=self.renderer.probe(j)
        self.assertTrue(any(d.code=='local_assets_blocked' and d.severity=='error' for d in probe.diagnostics))
        with self.assertRaisesRegex(ExportError,'local assets'):
            self.renderer.render(j,self.root/'missing_assets.mp4')

    def test_iframe_font_readiness_is_awaited(self):
        import html
        inner = '''<svg width="80" height="40" data-video-export data-duration=".2"><rect width="80" height="40" fill="red"/></svg>
        <script>Object.defineProperty(document.fonts,'ready',{value:new Promise(resolve=>setTimeout(()=>{document.querySelector('rect').setAttribute('fill','blue');resolve()},500))})</script>'''
        j=self.job('<iframe srcdoc="'+html.escape(inner,quote=True)+'"></iframe>')
        out=self.root/'iframe_assets.png'
        self.renderer.capture_test_frame(j,out)
        with Image.open(out) as im:self.assertEqual(im.convert('RGB').getpixel((20,20)),(0,0,255))

    def test_fractional_scale_half_up(self):
        j=self.job('<canvas width=101 height=51 data-video-export data-duration=.1></canvas>')
        j.render.scale=1.5
        out=self.root/'fractional.png';_,p=self.renderer.capture_test_frame(j,out)
        self.assertEqual(png_dimensions(out.read_bytes()),(152,77))
        self.assertEqual((p.output_width,p.output_height),(152,77))

    def test_static_active_timers_encoded_frames_identical(self):
        j=self.job("<canvas width=64 height=48 data-video-export data-duration=.4></canvas><script>let c=document.querySelector('canvas').getContext('2d'),n=0;setInterval(()=>{c.fillStyle='rgb('+(++n%255)+',0,0)';c.fillRect(0,0,64,48)},1)</script>")
        out=self.root/'static.mp4';result=self.renderer.render(j,out)
        raw=decode(out);stride=64*48*3
        self.assertEqual(result.frame_count,4)
        self.assertEqual(len(raw),stride*4)
        self.assertEqual(len({raw[i:i+stride] for i in range(0,len(raw),stride)}),1)

    def test_clock_stops_wall_time_and_uses_absolute_frame_times(self):
        j=self.job("<canvas width=64 height=48 data-video-export data-duration=1></canvas><script>window.n=0;setInterval(()=>n++,1)</script>",TimelineMode.BROWSER_CLOCK)
        p=self.renderer._prepare(j)
        try:
            initial=p.target.frame.evaluate('n');time.sleep(.06)
            self.assertEqual(p.target.frame.evaluate('n'),initial)
            for i in range(61):self.renderer._seek(p,j,i/60,None,None)
            self.assertEqual(p.clock_elapsed_ms,1000)
            self.assertEqual(p.target.frame.evaluate('n')-initial,1000)
        finally:p.loaded.close()

    def test_clock_render_does_not_hang_on_frozen_raf(self):
        j=self.job("<canvas width=64 height=48 data-video-export data-duration=.2></canvas><script>let c=document.querySelector('canvas').getContext('2d');setInterval(()=>{c.fillStyle='red';c.fillRect(0,0,64,48)},10)</script>",TimelineMode.BROWSER_CLOCK)
        out=self.root/'clock.mp4';self.renderer.render(j,out)
        self.assertEqual(len(decode(out)),64*48*3*2)

    def test_default_javascript_object_receiver_preserved(self):
        j=self.job("<canvas width=64 height=48 data-video-export data-duration=.1></canvas><script>window.HTML_VIDEO_EXPORT={color:'rgb(17,99,201)',seek:function(t){let c=document.querySelector('canvas').getContext('2d');c.fillStyle=this.color;c.fillRect(0,0,64,48)}};</script>",TimelineMode.AUTO)
        out=self.root/'object.png';self.renderer.capture_test_frame(j,out)
        self.assertEqual(Image.open(out).convert('RGB').getpixel((20,20)),(17,99,201))

    def test_delayed_explicit_marker(self):
        j=self.job("<script>setTimeout(()=>document.body.innerHTML='<canvas width=64 height=48 data-video-export data-duration=.1></canvas>',180)</script>")
        j.capture.mode=CaptureMode.MARKER
        p=self.renderer.probe(j);self.assertEqual((p.source_width,p.source_height),(64,48))

    def test_delayed_om_duration(self):
        j=self.job("<svg width=64 height=48 data-om-exportable-video-with-duration-secs=''></svg><script>setTimeout(()=>document.querySelector('svg').setAttribute('data-om-exportable-video-with-duration-secs','.125'),120)</script>")
        p=self.renderer.probe(j);self.assertAlmostEqual(p.duration_seconds,.125)

    def test_overlay_hidden_but_composition_hidden_elements_preserved(self):
        j=self.job("<style>body{margin:0;background:black}</style><div style='transform:scale(.8);overflow:hidden;width:50px'><svg data-video-export data-duration=.1 width=64 height=48><rect width=64 height=48 fill='red'/><rect width=64 height=48 fill='blue' style='visibility:hidden'/></svg></div><div style='position:fixed;inset:0;background:blue;z-index:2147483647'>toolbar</div>")
        j.render.scale=2
        out=self.root/'overlay.png';self.renderer.capture_test_frame(j,out)
        self.assertEqual(png_dimensions(out.read_bytes()),(128,96))
        self.assertEqual(Image.open(out).convert('RGB').getpixel((30,30)),(255,0,0))

    def test_ancestor_opacity_is_preserved(self):
        j=self.job("<style>body{margin:0;background:black}</style><div style='opacity:.5'><svg data-video-export data-duration=.1 width=64 height=48><rect width=64 height=48 fill='red'/></svg></div>")
        out=self.root/'opacity.png';self.renderer.capture_test_frame(j,out)
        pixel=Image.open(out).convert('RGB').getpixel((20,20))
        self.assertIn(pixel[0],(127,128));self.assertEqual(pixel[1:],(0,0))

    def test_same_origin_iframe_scaled_parent(self):
        j=self.job('''<style>body{margin:0}</style><div style="transform:scale(.7)"><iframe srcdoc="<svg width='64' height='48' data-video-export data-duration='.1'><rect width='64' height='48' fill='red'/></svg>"></iframe></div><div style="position:fixed;inset:0;background:blue;z-index:2147483647"></div>''')
        j.render.scale=2
        out=self.root/'iframe.png';self.renderer.capture_test_frame(j,out)
        self.assertEqual(png_dimensions(out.read_bytes()),(128,96))
        self.assertEqual(Image.open(out).convert('RGB').getpixel((30,30)),(255,0,0))

    def test_subframe_export_one_frame_no_negative_seek(self):
        j=self.job("<canvas width=64 height=48 data-video-export data-duration=.001></canvas><script>window.seekTo=t=>{if(t<0)throw Error('negative seek')};</script>",TimelineMode.JAVASCRIPT_FUNCTION)
        j.render.fps=60
        result=self.renderer.render(j,self.root/'subframe.mp4')
        self.assertEqual(result.frame_count,1)

    def test_explicit_trim_end_is_a_bound_not_hidden_duration(self):
        j=self.job('<canvas width=64 height=48 data-video-export></canvas>')
        j.timeline.trim_end=.2
        p=self.renderer.probe(j)
        self.assertEqual(p.duration_seconds,.2);self.assertIn('trim end',p.duration_source)

    def test_precancel_does_not_load_source(self):
        event=threading.Event();event.set()
        j=self.job('<canvas width=64 height=48 data-video-export data-duration=.1></canvas>')
        with patch.object(self.renderer,'_prepare',side_effect=AssertionError('must not load')):
            with self.assertRaises(ExportCancelled):self.renderer.render(j,self.root/'cancel.mp4',cancel_event=event)

    def test_cancel_at_final_frame_preserves_existing_file(self):
        event=threading.Event();j=self.job('<canvas width=64 height=48 data-video-export data-duration=.2></canvas>')
        out=self.root/'existing.mp4';out.write_bytes(b'original');j.render.overwrite=True
        def progress(p):
            if p['frame']==p['total_frames']:event.set()
        with self.assertRaises(ExportCancelled):self.renderer.render(j,out,cancel_event=event,progress_callback=progress)
        self.assertEqual(out.read_bytes(),b'original')
        self.assertFalse(list(self.root.glob('*.partial-*')))

    def test_cancel_encoder_finalization(self):
        event=threading.Event();j=self.job('<canvas width=64 height=48 data-video-export data-duration=.1></canvas>')
        timer=None
        def progress(p):
            nonlocal timer
            timer=threading.Timer(.15,event.set);timer.start()
        cmd=[sys.executable,'-c','import sys,time;sys.stdin.buffer.read();time.sleep(30)']
        with patch.object(self.renderer,'_build_ffmpeg_command',return_value=cmd):
            with self.assertRaises(ExportCancelled):self.renderer.render(j,self.root/'encoder-cancel.mp4',cancel_event=event,progress_callback=progress)
        if timer:timer.join(1)

    def test_alpha_profile_rejection(self):
        j=self.job('<svg width=64 height=48 data-video-export data-duration=.1></svg>')
        j.capture.transparent_background=True
        p=self.renderer.probe(j);self.assertTrue(any(d.code=='alpha_unsupported' for d in p.diagnostics))

    def test_transparent_prores_preserves_alpha(self):
        j=self.job("<style>html,body{background:transparent;margin:0}</style><svg width=64 height=48 data-video-export data-duration=.1><rect x=20 y=10 width=24 height=24 fill='red'/></svg>")
        j.capture.transparent_background=True;j.render.output_profile_key='prores_4444_mov'
        out=self.root/'alpha.mov';self.renderer.render(j,out)
        raw=decode(out,'rgba');im=Image.frombytes('RGBA',(64,48),raw)
        self.assertEqual(im.getpixel((0,0))[3],0)
        self.assertEqual(im.getpixel((30,20))[3],255)

    def test_lossless_rgb_browser_encode_decode_exact(self):
        j=self.job("<style>body{margin:0}</style><svg data-video-export data-duration=.1 width=64 height=48><rect width=64 height=48 fill='#1163c9'/><rect x=30 width=34 height=48 fill='#f51291'/></svg>")
        out=self.root/'rgb.mp4';ref=self.root/'rgb.png'
        self.renderer.capture_test_frame(j,ref)
        self.renderer.render(j,out)
        with Image.open(ref) as image:self.assertEqual(decode(out),image.convert('RGB').tobytes())

    def test_yuv_color_swatch_conversion(self):
        j=self.job("<style>body{margin:0}</style><svg data-video-export data-duration=.1 width=64 height=48><rect width=64 height=48 fill='#1163c9'/></svg>")
        for key in ('h264_444_mp4','h264_420_mp4'):
            j.render.output_profile_key=key;out=self.root/(key+'.mp4');self.renderer.render(j,out)
            im=Image.frombytes('RGB',(64,48),decode(out));actual=im.getpixel((30,20))
            with self.subTest(profile=key):self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,(17,99,201))),4,actual)

    def test_media_adapter_seeks_actual_video(self):
        video=self.root/'media.webm'
        pixels=bytes([255,0,0])*64*48*2+bytes([0,0,255])*64*48*2
        p=subprocess.run([get_ffmpeg_executable(),'-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','64x48','-framerate','10','-i','pipe:0','-c:v','libvpx-vp9','-pix_fmt','yuv420p',str(video)],input=pixels,capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr)
        uri='data:video/webm;base64,'+base64.b64encode(video.read_bytes()).decode()
        j=self.job(f'<video width=64 height=48 preload=auto src="{uri}" data-video-export></video>',TimelineMode.MEDIA)
        ref=self.root/'video.png';_,pr=self.renderer.capture_test_frame(j,ref,at_seconds=.25)
        self.assertAlmostEqual(pr.duration_seconds,.4,places=2)
        pixel=Image.open(ref).convert('RGB').getpixel((20,20));self.assertGreater(pixel[2],240);self.assertLess(pixel[0],15)

    def test_never_settling_seek_rejects_with_timeout(self):
        j=self.job('<canvas width=64 height=48 data-video-export data-duration=.1></canvas><script>window.seekTo=()=>new Promise(()=>{})</script>',TimelineMode.JAVASCRIPT_FUNCTION)
        old=self.renderer.page_timeout_ms;self.renderer.page_timeout_ms=300
        try:
            with self.assertRaises(Exception) as raised:self.renderer.capture_test_frame(j,self.root/'timeout.png')
            self.assertIn('timed out',str(raised.exception).lower())
        finally:self.renderer.page_timeout_ms=old


if __name__=='__main__':unittest.main()
