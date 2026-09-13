"""Desktop CRF override controls; use xvfb-run on Linux."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from models import QueueJob
from presets import OUTPUT_PROFILES, RECIPES
from settings_store import AppSettings


@unittest.skipUnless(os.environ.get("DISPLAY") or sys.platform in {"win32", "darwin"},
                     "Desktop display required; run with xvfb-run")
class QualityUITests(unittest.TestCase):
    def setUp(self):
        from app import StudioApp
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.html = self.root / "quality.html"
        self.html.write_text('<svg width="64" height="96" data-video-export></svg>', encoding="utf-8")
        self.patches = [patch("app.load_settings", return_value=AppSettings()),
                        patch("app.save_settings"),
                        patch.object(StudioApp, "_check_environment_async"),
                        patch("app.messagebox.showerror")]
        for item in self.patches:
            item.start()
        self.app = StudioApp()
        self.app.update_idletasks()
        self.job = QueueJob(uuid.uuid4().hex, RECIPES["social_delivery"].create_job(str(self.html)))

    def tearDown(self):
        self.app.destroy()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_editor_saves_manual_crf_and_recipe_resets_it(self):
        from app import JobEditor
        editor = JobEditor(self.app, self.job)
        self.assertEqual(editor.crf_var.get(), "")
        editor.crf_var.set("6.5")
        editor._save()
        self.assertIsNotNone(editor.result)
        self.assertEqual(editor.result.render.video_crf, 6.5)
        self.assertIsNone(self.job.config.render.video_crf)

        self.job.config.render.video_crf = 6.5
        editor = JobEditor(self.app, self.job)
        editor._apply_recipe()
        self.assertEqual(editor.crf_var.get(), "")
        editor.destroy()

    def test_editor_disables_override_for_non_h26x_profile(self):
        from app import JobEditor
        editor = JobEditor(self.app, self.job)
        editor.crf_var.set("7")
        editor.profile_var.set(OUTPUT_PROFILES["prores_hq_mov"].label)
        editor._update_profile_description()
        self.assertEqual(editor.crf_var.get(), "")
        self.assertEqual(str(editor.crf_widget.cget("state")), "disabled")
        editor.destroy()

    def test_editor_rejects_out_of_range_crf(self):
        from app import JobEditor
        editor = JobEditor(self.app, self.job)
        editor.crf_var.set("52")
        editor._save()
        self.assertIsNone(editor.result)
        editor.destroy()


if __name__ == "__main__":
    unittest.main()
