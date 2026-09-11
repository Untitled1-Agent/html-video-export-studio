"""Create a NEW GitHub repository using the user's locally authenticated GitHub CLI.

Defaults to private; never overwrites an existing remote or force-pushes.
ChatGPT connector authorization is not reused or exported to this script.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.package_release import sources


class PublishError(RuntimeError):
    pass


def repository_name(value: str, login: str) -> str:
    value = value.strip()
    if '/' not in value:
        value = f'{login}/{value}'
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+', value):
        raise PublishError('Use a repository name or owner/name, not a URL.')
    if value.split('/')[1] in {'.', '..'}:
        raise PublishError('Invalid repository name.')
    return value


def create_command(repo: str, root: Path, public: bool = False) -> list[str]:
    return ['gh', 'repo', 'create', repo, '--public' if public else '--private',
            '--source', str(root), '--remote', 'origin', '--push', '--disable-wiki',
            '--description', 'Python desktop and CLI studio for high-fidelity HTML-to-video export']


def run(args: list[str], root: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    # No shell expansion, token arguments, or credential-file reads.
    env = os.environ.copy()
    if args[0] == "gh":
        env["GH_HOST"] = "github.com"
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=180, env=env)
    if check and result.returncode:
        raise PublishError(result.stderr.strip() or result.stdout.strip() or f'{args[0]} failed')
    return result


def publish(root: Path, requested_repo: str, public: bool = False) -> str:
    root = root.resolve()
    for command in ('git', 'gh'):
        if not shutil.which(command):
            raise PublishError(f'{command} is not installed. See GITHUB_SETUP.md.')
    user_result = run(['gh', 'api', 'user'], root, check=False)
    if user_result.returncode:
        raise PublishError('GitHub CLI is not authenticated. Run: gh auth login --hostname github.com --web')
    try:
        user = json.loads(user_result.stdout)
        login, user_id = user['login'], int(user['id'])
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*', login) or user_id <= 0:
            raise ValueError('invalid identity')
    except (ValueError, TypeError, KeyError) as exc:
        raise PublishError('Could not read the authenticated GitHub identity.') from exc
    repo = repository_name(requested_repo, login)
    existing = run(['gh', 'api', f'repos/{repo}'], root, check=False)
    if existing.returncode == 0:
        raise PublishError(f'{repo} already exists. Nothing was pushed; choose a NEW repository name.')
    if 'HTTP 404' not in existing.stderr:
        raise PublishError('Could not verify repository availability. Check GitHub access/connectivity; nothing was pushed.')
    git_dir = root/'.git'
    if git_dir.is_symlink():
        raise PublishError('Refusing a symlinked .git directory.')
    if git_dir.exists():
        remotes = run(['git', 'remote'], root).stdout.strip()
        if remotes:
            raise PublishError('This checkout already has a remote. Nothing was pushed or replaced.')
        branch = run(['git', 'symbolic-ref', '--short', 'HEAD'], root).stdout.strip()
        if branch != 'main':
            raise PublishError('Use a clean main branch for initial publication; no branch was renamed.')
    else:
        run(['git', 'init', '-b', 'main'], root)
    paths = [str(rel.as_posix()) for _, rel in sources(root)]
    tracked = set(run(['git', 'ls-files', '-z'], root).stdout.split('\0')) - {''}
    extra = tracked - set(paths)
    if extra:
        raise PublishError('Unexpected tracked/staged files; review them before publishing: '+', '.join(sorted(extra)))
    # For new archives, set only missing LOCAL commit identity using GitHub's no-reply form.
    for key, default in [('user.name', login), ('user.email', f'{user_id}+{login}@users.noreply.github.com')]:
        if not run(['git', 'config', '--get', key], root, check=False).stdout.strip():
            run(['git', 'config', '--local', key, default], root)
    run(['git', 'add', '--', *paths], root)
    changed = run(['git', 'diff', '--cached', '--quiet'], root, check=False)
    if changed.returncode not in (0, 1):
        raise PublishError('Could not inspect the staged changes.')
    if changed.returncode == 1:
        run(['git', 'commit', '-m', 'Prepare HTML Video Export Studio 1.5.2'], root)
    run(['git', 'rev-parse', '--verify', 'HEAD'], root)
    # If create succeeds but push fails, leave the remote intact for manual recovery.
    # Never delete a repo or repeat a push destructively on an ambiguous failure.
    run(create_command(repo, root, public), root)
    result = run(['gh', 'repo', 'view', repo, '--json', 'url', '--jq', '.url'], root)
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='html-video-export-studio', help='New repository name or owner/name.')
    parser.add_argument('--public', action='store_true', help='Explicitly make the NEW repository public (default: private).')
    parser.add_argument('--dry-run', action='store_true', help='Print the plan without git changes, authentication, or network requests.')
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            repo = repository_name(args.repo, 'AUTHENTICATED-USER')
            paths = [rel.as_posix() for _, rel in sources(ROOT)]
            print(json.dumps({'dry_run': True, 'repository': repo,
                              'visibility': 'public' if args.public else 'private',
                              'files': paths, 'create_command': create_command(repo, ROOT, args.public)}, indent=2))
        else:
            url = publish(ROOT, args.repo, args.public)
            print('Created and pushed: '+url)
        return 0
    except (PublishError, OSError, subprocess.TimeoutExpired) as exc:
        print('Publication stopped: '+str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
