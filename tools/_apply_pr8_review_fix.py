from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "renderer.py",
    '''            if loaded.console_errors or loaded.page_errors:
                messages = (loaded.page_errors + loaded.console_errors)[-5:]
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "browser_errors",
                        "The source logged browser errors: " + " | ".join(messages),
                    )
                )
''',
    '''            if loaded.page_errors:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "page_runtime_error",
                        "The source raised uncaught JavaScript errors: "
                        + " | ".join(loaded.page_errors[-5:])
                        + ". Export stopped to avoid capturing a broken or error-overlay state.",
                    )
                )
            if loaded.console_errors:
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "browser_errors",
                        "The source logged browser console errors: "
                        + " | ".join(loaded.console_errors[-5:]),
                    )
                )
''',
)

replace_once(
    "renderer.py",
    '''    def _capture_frame(self, prepared: PreparedSource, job: JobConfig) -> bytes:
        target = prepared.target
        page = prepared.loaded.page
''',
    '''    def _raise_page_errors(self, loaded: LoadedSource) -> None:
        if loaded.page_errors:
            raise ExportError(
                "Source JavaScript runtime error: "
                + " | ".join(loaded.page_errors[-5:])
                + ". Fix the source error before exporting."
            )

    def _capture_frame(self, prepared: PreparedSource, job: JobConfig) -> bytes:
        self._raise_page_errors(prepared.loaded)
        target = prepared.target
        page = prepared.loaded.page
''',
)

replace_once(
    "renderer.py",
    '''                data = screenshot(
                    type="png",
                    scale="device",
                    caret="hide",
                    omit_background=job.capture.transparent_background,
                    timeout=self.page_timeout_ms,
                )
                last_actual = png_dimensions(data)
''',
    '''                data = screenshot(
                    type="png",
                    scale="device",
                    caret="hide",
                    omit_background=job.capture.transparent_background,
                    timeout=self.page_timeout_ms,
                )
                self._raise_page_errors(prepared.loaded)
                last_actual = png_dimensions(data)
''',
)

replace_once(
    "renderer.py",
    '''        actual = png_dimensions(data)
        expected = (prepared.output_width, prepared.output_height)
''',
    '''        self._raise_page_errors(prepared.loaded)
        actual = png_dimensions(data)
        expected = (prepared.output_width, prepared.output_height)
''',
)

test_insert = """    def test_uncaught_page_error_blocks_export(self):
        j=self.job('''<svg width="80" height="40" data-video-export data-duration=".1"><rect width="80" height="40" fill="red"/></svg><script>throw new Error("creative exploded")</script>''')
        probe=self.renderer.probe(j)
        self.assertTrue(any(d.code=='page_runtime_error' and d.severity=='error' and 'creative exploded' in d.message for d in probe.diagnostics))
        with self.assertRaisesRegex(ExportError,'creative exploded'):
            self.renderer.render(j,self.root/'page-error.mp4')
        self.assertFalse((self.root/'page-error.mp4').exists())

    def test_runtime_page_error_during_seek_blocks_export(self):
        j=self.job('''<svg width="80" height="40" data-video-export data-duration=".2"><rect width="80" height="40" fill="red"/></svg><script>window.seekTo=t=>{if(t>0) Promise.resolve().then(()=>{throw new Error("seek runtime exploded")})}</script>''',TimelineMode.JAVASCRIPT_FUNCTION)
        j.render.fps=10
        with self.assertRaisesRegex(ExportError,'seek runtime exploded'):
            self.renderer.render(j,self.root/'seek-page-error.mp4')
        self.assertFalse((self.root/'seek-page-error.mp4').exists())

    def test_console_error_remains_warning_not_export_blocker(self):
        j=self.job('''<svg width="80" height="40" data-video-export data-duration=".1"><rect width="80" height="40" fill="red"/></svg><script>console.error("nonfatal telemetry")</script>''')
        probe=self.renderer.probe(j)
        self.assertTrue(any(d.code=='browser_errors' and d.severity=='warning' and 'nonfatal telemetry' in d.message for d in probe.diagnostics))
        self.assertFalse(any(d.severity=='error' for d in probe.diagnostics))
        out=self.root/'console-error-warning.mp4'
        self.renderer.render(j,out)
        self.assertTrue(out.is_file() and out.stat().st_size>0)

"""
replace_once(
    "tests/test_review.py",
    "    def test_iframe_font_readiness_is_awaited(self):\n",
    test_insert + "    def test_iframe_font_readiness_is_awaited(self):\n",
)

replace_once(
    "docs/USER_GUIDE.md",
    "Errors block export. Warnings deserve inspection: low raster density means assets contain too few pixels for the requested size; very large frames warn of memory use; real-time timing warns about missed states; browser console warnings can reveal missing network assets. The app is not a complete asset-health checker for every loading pattern. A visually wrong preview must not be treated as a passing export merely because no exception occurred.",
    "Errors block export. Uncaught JavaScript page/runtime errors are treated as errors and stop export, including errors that occur during seek/capture, so a source-generated error overlay is not silently encoded. Plain browser `console.error` messages remain warnings because some pages log non-fatal telemetry there. Other warnings deserve inspection: low raster density means assets contain too few pixels for the requested size; very large frames warn of memory use; real-time timing warns about missed states; browser console warnings can reveal missing network assets. The app is not a complete asset-health checker for every loading pattern. A visually wrong preview must not be treated as a passing export merely because no exception occurred.",
)

replace_once(
    "CHANGELOG.md",
    "- Add an opt-in AV1 Main 8-bit 4:2:0 MP4 profile using portable `libaom-av1`, CRF 18, `av01`, fast-start MP4, explicit BT.709/sRGB metadata, and AAC-LC audio.\n",
    "- Add an opt-in AV1 Main 8-bit 4:2:0 MP4 profile using portable `libaom-av1`, CRF 18, `av01`, fast-start MP4, explicit BT.709/sRGB metadata, and AAC-LC audio.\n- Treat uncaught JavaScript page/runtime errors as export-blocking source validation failures, including errors raised during seek/capture; keep plain `console.error` output as a non-blocking warning.\n",
)

print("PR8 review finding patch applied.")
