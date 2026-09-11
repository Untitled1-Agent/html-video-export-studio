from __future__ import annotations
import contextlib, io, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace
import cli
from models import ProbeResult, SourceSpec, TimelineMode, GeometryMode, Diagnostic

class CLIReviewTests(unittest.TestCase):
    def run_cli(self,args):
        stdout=io.StringIO();stderr=io.StringIO()
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            code=cli.main(args)
        return code,stdout.getvalue(),stderr.getvalue()
    def probe(self,errors=False):
        return ProbeResult(SourceSpec('a.html'),'embedded','test','svg','','',TimelineMode.STATIC,.1,'manual',80,40,160,80,GeometryMode.LOCK_INTRINSIC,diagnostics=[Diagnostic('error','no_duration','No source duration')] if errors else [])
    def test_batch_probe_one_json_document(self):
        with patch('cli.HtmlVideoRenderer') as cls:
            cls.return_value.__enter__.return_value.probe.return_value=self.probe()
            code,out,_=self.run_cli(['a.html','b.html','--probe'])
            self.assertEqual(code,0);self.assertEqual(len(json.loads(out)),2)
    def test_probe_diagnostic_errors_nonzero(self):
        with patch('cli.HtmlVideoRenderer') as cls:
            cls.return_value.__enter__.return_value.probe.return_value=self.probe(True)
            code,out,_=self.run_cli(['a.html','--probe'])
            self.assertEqual(code,1);self.assertTrue(json.loads(out)['diagnostics'])
    def test_probe_partial_failure_keeps_batch_json(self):
        with patch('cli.HtmlVideoRenderer') as cls:
            cls.return_value.__enter__.return_value.probe.side_effect=[ValueError('bad'),self.probe()]
            code,out,_=self.run_cli(['bad.html','good.html','--probe'])
            self.assertEqual(code,1);self.assertEqual(len(json.loads(out)),2)
    def test_startup_error_no_traceback(self):
        with patch('cli.HtmlVideoRenderer',side_effect=RuntimeError('missing browser')):
            code,_,err=self.run_cli(['a.html','--probe'])
            self.assertEqual(code,1);self.assertIn('missing browser',err);self.assertNotIn('Traceback',err)
    def test_dotted_folder_batch_is_directory(self):
        with tempfile.TemporaryDirectory() as td,patch('cli.HtmlVideoRenderer') as cls:
            r=cls.return_value.__enter__.return_value
            r.render.return_value=SimpleNamespace(output_path=Path(td)/'x.mp4')
            folder=Path(td)/'exports.v2'
            code,_,err=self.run_cli(['a.html','b.html','--output',str(folder)])
            self.assertEqual(code,0,err)
            self.assertEqual(r.render.call_args.kwargs['output_path'].parent,folder)
    def test_known_output_file_passed_to_renderer(self):
        with tempfile.TemporaryDirectory() as td,patch('cli.HtmlVideoRenderer') as cls:
            r=cls.return_value.__enter__.return_value;r.render.return_value=SimpleNamespace(output_path=Path(td)/'out.mp4')
            target=Path(td)/'out.mp4';code,_,err=self.run_cli(['a.html','--output',str(target)])
            self.assertEqual(code,0,err);self.assertEqual(r.render.call_args.kwargs['output_path'],target)
    def test_custom_seek_options(self):
        args=cli.build_parser().parse_args(['a.html','--timeline','javascript_function','--seek-function','window.myTimeline.seek','--load','embedded'])
        job=cli.make_job('a.html',args)
        self.assertEqual(job.timeline.javascript_function,'window.myTimeline.seek')
        self.assertEqual(job.source.load_strategy.value,'embedded')
    def test_url_can_be_probed_without_output_directory(self):
        args=cli.build_parser().parse_args(['https://example.com','--probe'])
        cli.make_job('https://example.com',args).validate(for_export=False)

if __name__=='__main__': unittest.main()
