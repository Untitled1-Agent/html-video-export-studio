"""Run under xvfb-run on Linux. Real queue and GUI-thread regression tests."""
from __future__ import annotations
import os, sys, time, threading, tempfile, unittest, uuid
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace

from models import JobStatus, QueueJob, LoadStrategy, TimelineMode
from presets import RECIPES
from renderer import ExportCancelled

@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform in {'win32','darwin'}, 'Desktop display required; run with xvfb-run')
class UIReviewTests(unittest.TestCase):
    def setUp(self):
        from app import StudioApp
        from settings_store import AppSettings
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
        self.patches=[patch('app.load_settings', return_value=AppSettings()),
                      patch('app.save_settings'),patch.object(StudioApp,'_check_environment_async'),
                      patch('app.messagebox.showinfo'),patch('app.messagebox.showerror'),
                      patch('app.messagebox.askyesno',return_value=True)]
        for p in self.patches: p.start()
        self.app=StudioApp();self.app.update_idletasks()
        self.messages=[]
        original=self.app._append_log
        self.app._append_log=lambda m:(self.messages.append(m),original(m))
    def tearDown(self):
        self.app.stop_event.set();self.app.run_event.set()
        for e in list(self.app.active_cancels.values())+list(self.app.analysis_cancels.values()):e.set()
        deadline=time.monotonic()+15
        while ((any(t.is_alive() for t in self.app.worker_threads+self.app.background_threads) or
                any(not f.done() for f in self.app.analysis_futures)) and time.monotonic()<deadline):
            self.app.update();time.sleep(.02)
        self.app.destroy()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def pump(self, predicate, timeout=20):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:
            self.app.update();time.sleep(.01)
        self.app.update()
        self.assertTrue(predicate(),self.messages)
        self.assertFalse([m for m in self.messages if m.startswith('UI action failed:')],self.messages)
    def add(self,name='input',duration=.2):
        path=self.folder/(name+'.html')
        path.write_text('<svg width="80" height="40" data-video-export><rect width="80" height="40" fill="red"/></svg>')
        c=RECIPES['exact_source_master'].create_job(str(path))
        c.source.load_strategy=LoadStrategy.EMBEDDED;c.render.scale=2;c.render.fps=10
        c.timeline.mode=TimelineMode.STATIC;c.timeline.manual_duration=duration
        j=QueueJob(job_id=uuid.uuid4().hex,config=c);self.app.jobs.append(j);self.app._refresh_tree()
        return j
    def test_ui_delivery_does_not_call_tk_from_worker(self):
        calls=[]
        t=threading.Thread(target=lambda:self.app._ui(calls.append,'delivered'))
        t.start();t.join(timeout=1)
        self.assertFalse(t.is_alive());self.assertEqual(calls,[])
        self.pump(lambda:len(calls)==1)
    def test_real_parallel_queue_two_jobs(self):
        jobs=[self.add('first'),self.add('second')]
        self.app.workers_var.set(2);self.app._start_queue()
        self.pump(lambda:not self.app.running,timeout=45)
        self.assertEqual([j.status for j in jobs],[JobStatus.DONE]*2)
        for j in jobs: self.assertTrue(Path(j.output_path).exists())
    def test_real_analysis_and_editor_validation(self):
        from app import JobEditor
        j=self.add();self.app._analyze_job_async(j)
        self.assertTrue(self.app._is_busy(j))
        self.pump(lambda:j.job_id not in self.app.analysis_cancels)
        self.assertEqual(j.status,JobStatus.READY)
        editor=JobEditor(self.app,j);editor.update_idletasks()
        editor.fps_var.set(0);editor._save();self.assertIsNone(editor.result)
        editor.fps_var.set(60);editor._save();self.assertEqual(editor.result.render.fps,60)
    def test_cancelled_analysis_not_resurrected(self):
        j=self.add();event=threading.Event();event.set()
        self.app.analysis_cancels[j.job_id]=event;j.status=JobStatus.CANCELLED
        self.app._analysis_finished(j.job_id,Mock(),'')
        self.assertEqual(j.status,JobStatus.CANCELLED)
        self.assertFalse(self.app.analysis_cancels)
    def test_cancel_pending_keeps_it_from_starting(self):
        jobs=[self.add('first'),self.add('pending')];started=threading.Event();release=threading.Event();seen=[]
        def render(c,**kw):
            seen.append(Path(c.source.value).stem);started.set();release.wait(3)
            if kw['cancel_event'].is_set():raise ExportCancelled('cancelled')
            return SimpleNamespace(output_path=self.folder/'fake.mp4',output_width=160,output_height=80,frame_count=2)
        fake=Mock();fake.render.side_effect=render
        with patch('app.HtmlVideoRenderer',return_value=fake):
            self.app.workers_var.set(1);self.app._start_queue();self.pump(started.is_set)
            self.app.tree.selection_set(jobs[1].job_id);self.app._cancel_selected();release.set()
            self.pump(lambda:not self.app.running)
        self.assertEqual(seen,['first']);self.assertEqual(jobs[1].status,JobStatus.CANCELLED)
    def test_stop_leaves_pending_and_cancels_active(self):
        jobs=[self.add('first'),self.add('pending')];started=threading.Event()
        def render(c,**kw):
            started.set();kw['cancel_event'].wait(3);raise ExportCancelled('cancelled')
        fake=Mock();fake.render.side_effect=render
        with patch('app.HtmlVideoRenderer',return_value=fake):
            self.app.workers_var.set(1);self.app._start_queue();self.pump(started.is_set)
            self.app._stop_queue();self.pump(lambda:not self.app.running)
        self.assertEqual(jobs[0].status,JobStatus.CANCELLED)
        self.assertEqual(jobs[1].status,JobStatus.QUEUED)
    def test_pending_config_is_snapshot_and_edit_is_blocked(self):
        jobs=[self.add('first'),self.add('pending')];started=threading.Event();release=threading.Event();seen=[]
        def render(c,**kw):
            seen.append(c.render.fps);started.set();release.wait(3)
            return SimpleNamespace(output_path=self.folder/'fake.mp4',output_width=160,output_height=80,frame_count=2)
        fake=Mock();fake.render.side_effect=render
        with patch('app.HtmlVideoRenderer',return_value=fake):
            self.app.workers_var.set(1);self.app._start_queue();self.pump(started.is_set)
            self.assertTrue(self.app._is_busy(jobs[1]));jobs[1].config.render.fps=99
            release.set();self.pump(lambda:not self.app.running)
        self.assertEqual(seen,[10,10])
    def test_update_does_not_install_during_export(self):
        self.app.running=True
        with patch('app.launch_update_helper') as launch,patch('updater.cleanup_staging') as cleanup:
            self.app._update_ready_to_restart(self.folder)
            launch.assert_not_called();cleanup.assert_called_once()
        self.app.running=False
    def test_update_unsaved_cancel_does_not_install(self):
        with patch.object(self.app,'_confirm_discard_changes',return_value=False),patch('app.launch_update_helper') as launch,patch('updater.cleanup_staging') as cleanup:
            self.app._update_ready_to_restart(self.folder)
            launch.assert_not_called();cleanup.assert_called_once()
    def test_analysis_pool_bounded_and_cancelled(self):
        jobs=[self.add(f'j{i}') for i in range(8)];release=threading.Event()
        fake=Mock();fake.__enter__=Mock(return_value=fake);fake.__exit__=Mock(return_value=False)
        fake.probe.side_effect=lambda *a,**kw:release.wait(3)
        with patch('app.HtmlVideoRenderer',return_value=fake):
            for j in jobs:self.app._analyze_job_async(j)
            self.assertLessEqual(len(self.app.analysis_pool._threads),2)
            for e in self.app.analysis_cancels.values():e.set()
            release.set();self.pump(lambda:not self.app.analysis_cancels)
    def test_destroyed_editor_variables_safe_on_worker_gc(self):
        from app import JobEditor
        import gc
        j=self.add();editor=JobEditor(self.app,j)
        variable=editor.duration_var
        editor.destroy()
        self.assertIsNone(variable._tk)
        del editor
        thread=threading.Thread(target=gc.collect)
        thread.start();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_minimum_layout_and_tabs(self):
        self.app.geometry('1020x680');self.app.update_idletasks();self.app.update()
        self.assertEqual((self.app.winfo_width(),self.app.winfo_height()),(1020,680))
        notebooks=[]
        def walk(w):
            for c in w.winfo_children():
                if c.winfo_class()=='TNotebook':notebooks.append(c)
                walk(c)
        walk(self.app)
        self.assertTrue(any(len(n.tabs())>=4 for n in notebooks))
        for widget in [self.app.start_button, self.app.pause_button, self.app.cancel_button,
                       self.app.filter_entry, self.app.overall_progress]:
            self.assertTrue(widget.winfo_ismapped(), str(widget))
            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),self.app.winfo_rootx()+1020)
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),self.app.winfo_rooty()+680)
        self.assertTrue(self.app.start_button.winfo_ismapped())
        self.assertLessEqual(self.app.start_button.winfo_rooty()+self.app.start_button.winfo_height(),self.app.winfo_rooty()+680)

if __name__=='__main__':unittest.main()
