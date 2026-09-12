"""Real Tk editor validation for the capture-worker controls; run under Xvfb."""
from __future__ import annotations
import unittest
from unittest.mock import patch

from models import JobConfig, QueueJob, SourceSpec


class CaptureEditorTests(unittest.TestCase):
    def setUp(self):
        from app import StudioApp
        from settings_store import AppSettings
        self.patches=[patch('app.load_settings',return_value=AppSettings()),
                      patch('app.save_settings'),patch.object(StudioApp,'_check_environment_async'),
                      patch('app.messagebox.showinfo'),patch('app.messagebox.showerror')]
        for item in self.patches:
            item.start();self.addCleanup(item.stop)
        self.app=StudioApp();self.addCleanup(self.app.destroy)
        self.job=QueueJob('fixture',JobConfig(SourceSpec('source.html')))

    def test_editor_saves_options_without_changing_source_job(self):
        from app import JobEditor
        editor=JobEditor(self.app,self.job)
        try:
            editor.capture_workers_var.set('4')
            editor.frame_buffer_var.set('128')
            editor.fast_capture_var.set(False)
            variable=editor.capture_workers_var
            editor._save()
            self.assertEqual(editor.result.render.capture_workers,4)
            self.assertEqual(editor.result.render.frame_buffer_mb,128)
            self.assertFalse(editor.result.render.fast_capture)
            self.assertEqual(self.job.config.render.capture_workers,0)
            self.assertIsNone(variable._tk)
        finally:
            if editor.result is None:editor.destroy()

    def test_editor_rejects_invalid_counts_and_recipe_preserves_settings(self):
        from app import JobEditor
        editor=JobEditor(self.app,self.job)
        try:
            editor.capture_workers_var.set('17');editor._save()
            self.assertIsNone(editor.result)
            editor.capture_workers_var.set('4.5');editor._save()
            self.assertIsNone(editor.result)
            editor.capture_workers_var.set('4');editor.frame_buffer_var.set('15');editor._save()
            self.assertIsNone(editor.result)
            editor.frame_buffer_var.set('128');editor._apply_recipe()
            self.assertEqual(editor.capture_workers_var.get(),'4')
            self.assertEqual(editor.frame_buffer_var.get(),'128')
        finally:editor.destroy()


if __name__=='__main__':unittest.main()
