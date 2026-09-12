"""Exercise real queue dispatch without starting browsers or calling Tk widgets."""
from __future__ import annotations

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import StudioApp
from models import JobConfig, QueueJob, SourceSpec


class CPUQueueTests(unittest.TestCase):
    def app(self, jobs=1, workers=8):
        return SimpleNamespace(
            running=False, update_in_progress=False,
            jobs=[QueueJob(str(i), JobConfig(SourceSpec(f'{i}.html'))) for i in range(jobs)],
            workers_var=Mock(get=Mock(return_value=workers)),
            stop_event=threading.Event(), run_event=threading.Event(), active_cancels={},
            work_queue=queue.Queue(), start_button=Mock(), pause_button=Mock(),
            cancel_button=Mock(), stop_button=Mock(), _worker_loop=Mock(),
            _watch_workers=Mock(), _append_log=Mock(),
        )

    def start(self, app):
        with patch('media_pipeline.available_cpu_count', return_value=16), \
             patch('app.threading.Thread'):
            StudioApp._start_queue(app)

    def test_one_job_gets_single_job_budget_not_configured_pool_size(self):
        app = self.app(jobs=1, workers=8)
        self.start(app)
        self.assertEqual(len(app.worker_threads), 1)
        _, snapshot, _ = app.work_queue.get_nowait()
        self.assertEqual(snapshot.render.cpu_threads, 15)
        self.assertEqual(app.jobs[0].config.render.cpu_threads, 0)
        self.assertIsNone(app.work_queue.get_nowait())
        self.assertTrue(app.work_queue.empty())

    def test_two_jobs_share_budget_without_mutating_saved_jobs(self):
        app = self.app(jobs=2, workers=8)
        self.start(app)
        self.assertEqual(len(app.worker_threads), 2)
        for _ in range(2):
            _, snapshot, _ = app.work_queue.get_nowait()
            self.assertEqual(snapshot.render.cpu_threads, 7)
        self.assertEqual([job.config.render.cpu_threads for job in app.jobs], [0, 0])

    def test_manual_override_is_not_divided(self):
        app = self.app(jobs=2, workers=2)
        app.jobs[0].config.render.cpu_threads = 12
        self.start(app)
        _, first, _ = app.work_queue.get_nowait()
        _, second, _ = app.work_queue.get_nowait()
        self.assertEqual((first.render.cpu_threads, second.render.cpu_threads), (12, 7))

    def test_bad_thread_setting_does_not_leave_queue_running(self):
        app = self.app()
        app.jobs[0].config.render.cpu_threads = -1
        original_queue = app.work_queue
        with patch('app.messagebox.showerror') as showerror:
            self.start(app)
        self.assertFalse(app.running)
        self.assertIs(app.work_queue, original_queue)
        self.assertFalse(app.active_cancels)
        showerror.assert_called_once()


if __name__ == '__main__':
    unittest.main()
