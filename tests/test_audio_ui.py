"""Real Tk editor/pairing actions and parallel queue coverage; use xvfb-run."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from models import AudioMode, JobStatus, LoadStrategy, QueueJob, TimelineMode
from presets import RECIPES
from settings_store import AppSettings
from test_audio_media import write_wave


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform in {'win32', 'darwin'},
                     'Desktop display required; run with xvfb-run')
class AudioUITests(unittest.TestCase):
    def setUp(self):
        from app import StudioApp
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch('app.load_settings', return_value=AppSettings()),
                        patch('app.save_settings'), patch.object(StudioApp, '_check_environment_async'),
                        patch('app.messagebox.showinfo'), patch('app.messagebox.showerror'),
                        patch('app.messagebox.askyesno', return_value=True)]
        for item in self.patches:
            item.start()
        self.app = StudioApp()
        self.app.update_idletasks()

    def tearDown(self):
        self.app.stop_event.set()
        self.app.run_event.set()
        for event in list(self.app.active_cancels.values()) + list(self.app.analysis_cancels.values()):
            event.set()
        end = time.monotonic() + 15
        while any(t.is_alive() for t in self.app.worker_threads + self.app.background_threads) and time.monotonic() < end:
            self.app.update()
            time.sleep(0.02)
        self.app.destroy()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def add(self, name='example'):
        html = self.root / (name + '.html')
        html.write_text('<svg width="64" height="96" data-video-export><rect width="64" height="96" fill="red"/></svg>', encoding='utf-8')
        config = RECIPES['social_delivery'].create_job(str(html))
        config.source.load_strategy = LoadStrategy.EMBEDDED
        config.render.scale = 1
        config.render.fps = 30
        config.render.processing.preset_key = 'no_processing'
        config.timeline.mode = TimelineMode.STATIC
        config.timeline.manual_duration = 0.2
        job = QueueJob(uuid.uuid4().hex, config)
        self.app.jobs.append(job)
        self.app._refresh_tree()
        self.app.tree.selection_set(job.job_id)
        return job

    def test_attach_enables_audio_and_clears_stale_output(self):
        job = self.add()
        path = self.root / 'arbitrary soundtrack.wav'
        write_wave(path)
        job.status = JobStatus.DONE
        job.output_path = 'old.mp4'
        job.error = 'old error'
        job.config.render.audio.offset_seconds = -0.2
        with patch('app.filedialog.askopenfilename', return_value=str(path)):
            self.app._attach_audio()
        self.assertEqual(job.config.render.audio.path, str(path))
        self.assertEqual(job.config.render.audio.mode, AudioMode.TRIM)
        self.assertEqual(job.config.render.audio.offset_seconds, -0.2)
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(job.output_path, '')
        self.assertEqual(job.error, '')
        self.assertTrue(self.app.project_dirty)
        self.assertIn(path.name, self.app.tree.set(job.job_id, 'audio'))

    def test_filename_matching_preserves_existing_and_reports_ambiguity(self):
        jobs = [self.add(name) for name in ('one', 'two', 'three')]
        tracks = []
        for name in ('one.wav', 'TWO.mp3', 'three.wav', 'three.m4a'):
            path = self.root / name
            path.touch()
            tracks.append(str(path))
        jobs[1].config.render.audio.path = 'manually-matched.wav'
        self.app.tree.selection_set([job.job_id for job in jobs])
        with patch('app.filedialog.askopenfilenames', return_value=tracks), patch('app.messagebox.showinfo') as info:
            self.app._match_audio()
        self.assertEqual(jobs[0].config.render.audio.path, tracks[0])
        self.assertEqual(jobs[1].config.render.audio.path, 'manually-matched.wav')
        self.assertEqual(jobs[2].config.render.audio.path, '')
        self.assertIn('Matched 1', info.call_args.args[1])
        self.assertIn('Ambiguous', info.call_args.args[1])

    def test_busy_jobs_cannot_be_rematched_or_removed(self):
        job = self.add()
        job.config.render.audio.path = 'retained.wav'
        self.app.run_job_ids.add(job.job_id)
        self.app.running = True
        with patch('app.filedialog.askopenfilename') as attach, patch('app.filedialog.askopenfilenames') as match:
            self.app._attach_audio()
            self.app._match_audio()
            self.app._remove_audio()
            attach.assert_not_called()
            match.assert_not_called()
        self.assertEqual(job.config.render.audio.path, 'retained.wav')
        self.app.run_job_ids.clear()
        self.app.running = False

    def test_cancelled_picker_and_remove_audio(self):
        job = self.add()
        with patch('app.filedialog.askopenfilename', return_value=''):
            self.app._attach_audio()
        self.assertEqual(job.config.render.audio.mode, AudioMode.NONE)
        job.config.render.audio.path = 'selected.wav'
        job.config.render.audio.mode = AudioMode.LOOP
        self.app._remove_audio()
        self.assertEqual(job.config.render.audio.path, '')
        self.assertEqual(job.config.render.audio.mode, AudioMode.NONE)

    def test_editor_saves_all_options_and_releases_tk_variables(self):
        from app import JobEditor
        job = self.add()
        track = self.root / 'chosen.wav'
        write_wave(track)
        editor = JobEditor(self.app, job)
        editor.audio_path_var.set(str(track))
        editor.audio_mode_var.set('Loop to video length')
        editor.audio_offset_var.set(-0.15)
        editor.audio_volume_var.set(0.7)
        editor.audio_fade_in_var.set(0.1)
        editor.audio_fade_out_var.set(0.2)
        editor.audio_bitrate_var.set(192)
        editor.audio_sample_rate_var.set(44100)
        editor.audio_channels_var.set(1)
        editor.audio_normalize_var.set(True)
        variable = editor.audio_normalize_var
        editor._save()
        self.assertIsNotNone(editor.result)
        audio = editor.result.render.audio
        self.assertEqual((audio.path, audio.mode, audio.offset_seconds), (str(track), AudioMode.LOOP, -0.15))
        self.assertEqual((audio.bitrate_kbps, audio.sample_rate_hz, audio.channels), (192, 44100, 1))
        self.assertTrue(audio.normalize_loudness)
        self.assertEqual((audio.volume, audio.fade_in_seconds, audio.fade_out_seconds), (0.7, 0.1, 0.2))
        self.assertIsNone(variable._tk)
        self.assertEqual(job.config.render.audio.mode, AudioMode.NONE)

    def test_editor_rejects_invalid_numbers_without_mutating_job(self):
        from app import JobEditor
        job = self.add()
        editor = JobEditor(self.app, job)
        editor.audio_offset_var.set(float('inf'))
        editor._save()
        self.assertIsNone(editor.result)
        self.assertEqual(job.config.render.audio.offset_seconds, 0.0)
        editor.destroy()

    def test_real_parallel_queue_keeps_matched_tracks(self):
        jobs = [self.add('first'), self.add('second')]
        for index, job in enumerate(jobs):
            path = self.root / (Path(job.config.source.value).stem + '.wav')
            write_wave(path, frequency=440 * (index + 1))
            self.app._set_job_audio(job, path)
            job.config.render.audio.offset_seconds = -0.02
        self.app.workers_var.set(2)
        self.app._start_queue()
        end = time.monotonic() + 45
        while self.app.running and time.monotonic() < end:
            self.app.update()
            time.sleep(0.01)
        self.app.update()
        self.assertFalse(self.app.running)
        self.assertEqual([job.status for job in jobs], [JobStatus.DONE, JobStatus.DONE], [job.error for job in jobs])
        for job in jobs:
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', job.output_path],
                                    capture_output=True, text=True, check=True, timeout=15)
            streams = json.loads(result.stdout)['streams']
            self.assertEqual([s['codec_type'] for s in streams], ['video', 'audio'])
            self.assertEqual(streams[1]['codec_name'], 'aac')


if __name__ == '__main__':
    unittest.main()
