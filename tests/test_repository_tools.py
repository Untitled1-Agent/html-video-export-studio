"""Repository publication/packaging safeguards; GitHub writes are always mocked here."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from tools import package_release as pack
from tools import publish_github as publish
import update_helper

ROOT = Path(__file__).resolve().parents[1]

class RepositoryToolTests(unittest.TestCase):
    def test_publish_default_private_and_public_is_explicit(self):
        args = publish.create_command('owner/new', Path('/repo'))
        self.assertIn('--private', args)
        self.assertNotIn('--public', args)
        self.assertNotIn('--force', args)
        self.assertIn('--public', publish.create_command('owner/new', Path('/repo'), True))

    def test_publish_repo_name_validation(self):
        self.assertEqual(publish.repository_name('studio', 'owner'), 'owner/studio')
        for value in ['https://github.com/owner/a', '../a', 'owner/..', 'a/b/c', '']:
            with self.subTest(value=value), self.assertRaises(publish.PublishError):
                publish.repository_name(value, 'owner')

    def test_publish_dry_run_has_no_git_or_network_calls(self):
        stdout = io.StringIO()
        with patch.object(publish, 'run') as run, patch.object(publish, 'sources', return_value=[(ROOT/'app.py',Path('app.py'))]), contextlib.redirect_stdout(stdout):
            rc = publish.main(['--repo', 'owner/studio', '--dry-run'])
        self.assertEqual(rc, 0)
        run.assert_not_called()
        obj = json.loads(stdout.getvalue())
        self.assertTrue(obj['dry_run'])
        self.assertEqual(obj['visibility'], 'private')

    def response(self, code=0, out='', err=''):
        return subprocess.CompletedProcess([], code, out, err)

    def test_existing_repository_is_never_pushed_or_replaced(self):
        with tempfile.TemporaryDirectory() as td, patch.object(publish.shutil, 'which', return_value='/bin/x'), patch.object(publish, 'run', side_effect=[self.response(out='{"login":"owner","id":123}'), self.response()]) as run:
            with self.assertRaisesRegex(publish.PublishError, 'already exists'):
                publish.publish(Path(td), 'studio')
            self.assertEqual(run.call_count, 2)
            self.assertFalse((Path(td)/'.git').exists())

    def test_authentication_failure_stops_before_git_changes(self):
        with tempfile.TemporaryDirectory() as td, patch.object(publish.shutil, 'which', return_value='/bin/x'), patch.object(publish, 'run', return_value=self.response(1)) as run:
            with self.assertRaisesRegex(publish.PublishError, 'not authenticated'):
                publish.publish(Path(td), 'studio')
            self.assertEqual(run.call_count, 1)

    def test_api_failure_is_not_treated_as_available_repo(self):
        with tempfile.TemporaryDirectory() as td, patch.object(publish.shutil, 'which', return_value='/bin/x'), patch.object(publish, 'run', side_effect=[self.response(out='{"login":"owner","id":123}'), self.response(1, err='HTTP 403 Forbidden')]) as run:
            with self.assertRaisesRegex(publish.PublishError, 'availability'):
                publish.publish(Path(td), 'studio')
            self.assertEqual(run.call_count, 2)

    def test_existing_remote_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'.git').mkdir()
            replies=[self.response(out='{"login":"owner","id":123}'), self.response(1,err='HTTP 404'), self.response(out='origin')]
            with patch.object(publish.shutil, 'which', return_value='/bin/x'), patch.object(publish, 'run', side_effect=replies) as run:
                with self.assertRaisesRegex(publish.PublishError, 'already has a remote'):
                    publish.publish(root, 'studio')
                self.assertEqual(run.call_count, 3)

    def test_unexpected_staged_files_block_publication(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'.git').mkdir()
            replies=[self.response(out='{"login":"owner","id":123}'), self.response(1,err='HTTP 404'), self.response(), self.response(out='main'), self.response(out='secrets.txt\0')]
            with patch.object(publish.shutil, 'which', return_value='/bin/x'), patch.object(publish, 'sources', return_value=[]), patch.object(publish, 'run', side_effect=replies):
                with self.assertRaisesRegex(publish.PublishError, 'Unexpected tracked'):
                    publish.publish(root, 'studio')

    def test_publisher_pins_github_host_without_shell(self):
        with patch.dict(os.environ, {'GH_HOST':'example.invalid'}), patch.object(publish.subprocess, 'run', return_value=self.response()) as run:
            publish.run(['gh','api','user'], ROOT)
        self.assertEqual(run.call_args.kwargs['env']['GH_HOST'], 'github.com')
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_packaging_allowlist_excludes_private_root_inputs_and_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for name in ['app.py','customer.html','customer.md','.env','private.key','notes.txt']:
                (root/name).write_text('# fixture')
            (root/'examples').mkdir(); (root/'examples/demo.html').write_text('<html/>')
            (root/'docs').mkdir(); (root/'docs/.env.secret').write_text('secret')
            (root/'.venv').mkdir(); (root/'.venv/bad.py').write_text('not python!')
            names={rel.as_posix() for _, rel in pack.sources(root)}
            self.assertEqual(names, {'app.py','examples/demo.html'})

    def test_packaging_skips_symlinks(self):
        # Symlink creation needs extra privileges on some Windows installations.
        # The Linux full gate always executes this assertion without skips.
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'examples').mkdir()
            actual=root/'secret.html'; actual.write_text('private')
            (root/'examples/link.html').symlink_to(actual)
            (root/'docs').symlink_to(root/'examples',target_is_directory=True)
            self.assertEqual(list(pack.sources(root)), [])

    def test_release_zips_reproducible_checksums_and_update_excludes_ci(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'source'; root.mkdir()
            for p in ROOT.glob('*.py'): shutil.copy2(p,root/p.name)
            shutil.copy2(ROOT/'requirements.txt',root/'requirements.txt')
            (root/'.github').mkdir(); (root/'.github/test.yml').write_text('name: fixture')
            with contextlib.redirect_stdout(io.StringIO()):
                first=pack.package(Path(td)/'a',root)
                second=pack.package(Path(td)/'b',root)
            self.assertEqual([p.read_bytes() for p in first],[p.read_bytes() for p in second])
            for p in first:
                if p.suffix=='.zip':
                    checksum=p.with_name(p.name+'.sha256').read_text().strip()
                    self.assertEqual(checksum,hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name)
                    with zipfile.ZipFile(p) as z:
                        has_ci=any('/.github/' in n for n in z.namelist())
                    self.assertEqual(has_ci, 'GitHub-ready' in p.name)

    def test_publish_makes_real_local_commit_with_mocked_github(self):
        # Git runs for real. GitHub discovery/creation is simulated, never remote.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'app.py').write_text('# demonstration application')
            (root/'customer.html').write_text('never stage this')
            (root/'.env').write_text('never stage this either')
            actual_run, actual_which = publish.run, shutil.which
            calls = []
            def invoke(args, directory, **kwargs):
                if args[0] != 'gh':
                    return actual_run(args, directory, **kwargs)
                calls.append(args)
                if args[1:3] == ['api', 'user']:
                    return self.response(out='{"login":"mock-owner","id":123}')
                if args[1] == 'api':
                    return self.response(1, err='HTTP 404')
                if args[1:3] == ['repo', 'create']:
                    return self.response()
                return self.response(out='https://github.com/mock-owner/studio')
            def which(name):
                return 'gh' if name == 'gh' else actual_which(name)
            with patch.object(publish, 'run', side_effect=invoke), patch.object(publish.shutil, 'which', side_effect=which), patch.dict(os.environ, {'GIT_CONFIG_GLOBAL':str(root/'empty-gitconfig'), 'GIT_CONFIG_NOSYSTEM':'1'}):
                url = publish.publish(root, 'studio')
            self.assertEqual(url, 'https://github.com/mock-owner/studio')
            tracked = actual_run(['git','ls-tree','-r','--name-only','HEAD'],root).stdout.splitlines()
            self.assertEqual(tracked, ['app.py'])
            self.assertEqual(actual_run(['git','branch','--show-current'],root).stdout.strip(),'main')
            create = next(c for c in calls if c[1:3] == ['repo','create'])
            self.assertIn('--private', create)
            self.assertNotIn('--force', create)

    def test_updater_rejects_wrong_restart_before_wait_or_apply(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with patch.object(update_helper, 'apply_update') as apply, patch.object(update_helper, 'wait_for_exit') as wait, contextlib.redirect_stderr(io.StringIO()):
                rc=update_helper.main(['--wait-pid','123','--source',str(root/'source'), '--destination',str(root), '--restart',str(root/'other.py')])
            self.assertEqual(rc,1); apply.assert_not_called(); wait.assert_not_called()
            self.assertFalse((root/'.hves-update.lock').exists())

if __name__=='__main__': unittest.main()
