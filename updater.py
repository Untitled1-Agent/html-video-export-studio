"""GitHub release updates. HTTPS + exact checksums + bounded, validated payloads.

A checksum proves transport integrity, not publisher identity. Configure only a
repository you trust: an update is executable code. No network code executes at import.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from version import APP_NAME, APP_VERSION


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    api_url: str = ''


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    page_url: str
    notes: str
    assets: tuple[ReleaseAsset, ...]


def parse_version(value: str) -> tuple[int, int, int, tuple[str, ...]]:
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
                         r'(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?', value.strip(), re.I)
    if not match:
        raise ValueError(f'Invalid semantic version: {value}')
    prerelease = tuple(match[4].split('.')) if match[4] else ()
    identifiers = prerelease + (tuple(match[5].split('.')) if match[5] else ())
    if any(not x for x in identifiers) or any(x.isdigit() and len(x)>1 and x.startswith('0') for x in prerelease):
        raise ValueError(f'Invalid semantic version: {value}')
    return int(match[1]), int(match[2]), int(match[3]), prerelease


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    c, o = parse_version(candidate), parse_version(current)
    if c[:3] != o[:3]: return c[:3] > o[:3]
    if not c[3] or not o[3]: return not c[3] and bool(o[3])
    for left, right in zip(c[3], o[3]):
        if left == right: continue
        if left.isdigit() and right.isdigit(): return int(left)>int(right)
        if left.isdigit() != right.isdigit(): return not left.isdigit()
        return left > right
    return len(c[3]) > len(o[3])


def normalize_repo(value: str) -> str:
    value = value.strip().rstrip('/')
    if value.startswith('git@github.com:'):
        value = value[len('git@github.com:'):]
    elif '://' in value:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {'https','ssh'} or parsed.hostname != 'github.com' or parsed.query or parsed.fragment:
            raise ValueError('Use owner/repository or an exact GitHub repository URL.')
        if parsed.scheme == 'https' and (parsed.username or parsed.password):
            raise ValueError('Credential-bearing URLs are not supported.')
        value = parsed.path.strip('/')
    if value.endswith('.git'): value = value[:-4]
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value) or any(p in {'.','..'} for p in value.split('/')):
        raise ValueError('GitHub repository must be owner/repository.')
    return value


def detect_git_origin(directory: Path) -> str:
    try:
        proc = subprocess.run(['git','-C',str(directory),'remote','get-url','origin'],
                              capture_output=True,text=True,timeout=5)
        if proc.returncode == 0: return normalize_repo(proc.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired): pass
    return ''


_ALLOWED_HOSTS = {'api.github.com','github.com','release-assets.githubusercontent.com',
                  'objects.githubusercontent.com'}


def _validate_download_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in _ALLOWED_HOSTS or
        parsed.username or parsed.password or parsed.port not in (None,443)):
        raise UpdateError('Update downloads must use HTTPS on an approved GitHub host.')


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        _validate_download_url(newurl)
        redirected = super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected is not None:
            redirected.remove_header('Authorization')
        return redirected


def download_bytes(url: str, token: str = '', *, limit: int = 100_000_000, accept: str = 'application/octet-stream') -> bytes:
    _validate_download_url(url)
    request = urllib.request.Request(url, headers={
        'User-Agent': f'{APP_NAME.replace(" ","-")}/{APP_VERSION}',
        'Accept': accept, 'X-GitHub-Api-Version': '2022-11-28'})
    # Never forward credentials to redirects or public download hosts.
    if token and urllib.parse.urlsplit(url).hostname == 'api.github.com':
        request.add_unredirected_header('Authorization',f'Bearer {token}')
    try:
        with urllib.request.build_opener(_SafeRedirect()).open(request,timeout=30) as response:
            length = response.headers.get('Content-Length')
            if length and int(length)>limit: raise UpdateError('Update download exceeds size limit.')
            data=response.read(limit+1)
            if len(data)>limit: raise UpdateError('Update download exceeds size limit.')
            return data
    except UpdateError: raise
    except Exception as exc:
        # Do not echo request headers/tokens/signed redirect URLs.
        raise UpdateError(f'GitHub download failed ({type(exc).__name__}). Check repository access and connectivity.') from exc


def _request_json(url: str, token: str = '') -> Any:
    try:
        return json.loads(download_bytes(url,token,limit=2_000_000,accept='application/vnd.github+json'))
    except (ValueError, UnicodeError) as exc:
        raise UpdateError('GitHub returned invalid JSON.') from exc


def fetch_latest_release(repo: str, token: str = '') -> ReleaseInfo:
    repo=normalize_repo(repo)
    payload=_request_json(f'https://api.github.com/repos/{repo}/releases/latest',token)
    if not isinstance(payload,dict) or payload.get('draft') or payload.get('prerelease'):
        raise UpdateError('No stable published release was returned.')
    tag=str(payload.get('tag_name') or '')
    version=tag.lstrip('vV'); parse_version(version)
    assets=[]
    for raw in payload.get('assets') or []:
        if isinstance(raw,dict) and raw.get('name') and raw.get('browser_download_url'):
            assets.append(ReleaseAsset(str(raw['name']),str(raw['browser_download_url']),
                                       int(raw.get('size') or 0),str(raw.get('url') or '')))
    return ReleaseInfo(version,tag,str(payload.get('html_url') or ''),str(payload.get('body') or ''),tuple(assets))


def choose_update_assets(release: ReleaseInfo) -> tuple[ReleaseAsset, ReleaseAsset]:
    by_name={asset.name:asset for asset in release.assets}
    names=[f'html-video-export-studio-v{release.version}.zip',
           f'html_to_mp4_exporter_v{release.version}.zip',
           f'html_video_export_studio_v{release.version}.zip']
    for name in names:
        if name in by_name:
            for suffix in ('.sha256','.sha256.txt'):
                if name+suffix in by_name: return by_name[name],by_name[name+suffix]
            raise UpdateError(f'Release is missing the exact checksum asset: {name}.sha256')
    raise UpdateError('Release lacks a recognized versioned update ZIP; the GitHub-ready source ZIP is not an update asset.')


def parse_checksum(text: str, archive_name: str) -> str:
    text=text.strip()
    if re.fullmatch(r'[0-9a-fA-F]{64}',text): return text.lower()
    matches=[]
    for line in text.splitlines():
        match=re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?(.+)',line.strip())
        if match and match[2]==archive_name: matches.append(match[1].lower())
    if len(matches)!=1: raise UpdateError('Checksum must uniquely name the selected archive.')
    return matches[0]


def verify_sha256(data: bytes, expected: str) -> None:
    if not re.fullmatch(r'[0-9a-fA-F]{64}',expected) or hashlib.sha256(data).hexdigest()!=expected.lower():
        raise UpdateError('Update SHA-256 checksum mismatch.')


_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$',re.I)
_PROTECTED = {'.git','.venv','venv','__pycache__','.env','settings.json'}


def validate_zip_members(archive: zipfile.ZipFile) -> None:
    seen=set(); total=0
    infos=archive.infolist()
    if len(infos)>2000: raise UpdateError('Update archive contains too many members.')
    for info in infos:
        name=info.filename.rstrip('/')
        parts=name.split('/')
        if (not name or '\\' in name or ':' in name or name.startswith('/') or
            any(p in {'','.','..'} or p.rstrip(' .')!=p or _RESERVED.fullmatch(p) or
                p.casefold() in _PROTECTED or any(ord(c)<32 for c in p) for p in parts)):
            raise UpdateError(f'Unsafe path in update archive: {info.filename}')
        key=name.casefold()
        if key in seen: raise UpdateError('Duplicate/case-colliding archive member.')
        seen.add(key)
        mode=(info.external_attr>>16)&0o170000
        if mode not in (0,0o100000,0o040000): raise UpdateError('Symlinks/special files are forbidden in updates.')
        if info.flag_bits&1: raise UpdateError('Encrypted update archives are not supported.')
        total+=info.file_size
        if info.file_size>25_000_000 or total>100_000_000:
            raise UpdateError('Update archive exceeds unpacked size limits.')
    for name in seen:
        path=PurePosixPath(name)
        for parent in path.parents:
            if str(parent)=='.': continue
            if str(parent) in seen:
                # Explicit directory records are valid; file/dir collisions aren't.
                parent_info=next(i for i in infos if i.filename.rstrip('/').casefold()==str(parent))
                if not parent_info.is_dir(): raise UpdateError('Archive file/directory collision.')


def stage_update(archive_bytes: bytes, destination: Path) -> Path:
    destination=destination.resolve()
    destination.mkdir(parents=True,exist_ok=True)
    if any(destination.iterdir()): raise UpdateError('Update staging directory must be empty.')
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        validate_zip_members(archive)
        # Validate every member BEFORE writing any bytes.
        for info in archive.infolist():
            target=destination/PurePosixPath(info.filename)
            if info.is_dir(): target.mkdir(parents=True,exist_ok=True); continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(info) as src, target.open('xb') as dst: shutil.copyfileobj(src,dst)
            if target.suffix=='.sh': target.chmod(0o755)
    entries=list(destination.iterdir())
    return entries[0] if len(entries)==1 and entries[0].is_dir() else destination


def validate_application(source: Path, expected_version: str = '') -> str:
    required={'app.py','cli.py','renderer.py','models.py','presets.py','processing.py','media_pipeline.py',
              'project_io.py','settings_store.py','updater.py','update_helper.py','version.py','requirements.txt'}
    if any(not (source/name).is_file() for name in required):
        raise UpdateError('Update is missing required application files.')
    excluded = {'.git', '.venv', 'venv', '__pycache__', 'build', 'dist',
                '.pytest_cache', '.mypy_cache', '.ruff_cache'}
    for file in source.rglob('*.py'):
        if any(part in excluded or part.endswith('.egg-info')
               for part in file.relative_to(source).parts):
            continue
        if file.is_symlink():
            raise UpdateError('Application Python files must not be symlinks.')
        try: compile(file.read_bytes(),str(file),'exec')
        except (SyntaxError, UnicodeError) as exc: raise UpdateError(f'Invalid Python file in update: {file.name}') from exc
    tree=ast.parse((source/'version.py').read_text(encoding='utf-8'))
    version=''
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='APP_VERSION' for t in node.targets):
            version=ast.literal_eval(node.value)
    parse_version(version)
    if expected_version and version!=expected_version:
        raise UpdateError('Version inside ZIP does not match the selected release.')
    return version


def cleanup_staging(source: Path) -> None:
    """Delete only a staging folder created and marked by this application."""
    for candidate in (source.resolve(), source.resolve().parent):
        marker=candidate/'.hves-stage.json'
        if candidate.name.startswith('hves-update-') and marker.is_file():
            try:
                data=json.loads(marker.read_text(encoding='utf-8'))
                if data.get('root')==str(source.resolve()):
                    shutil.rmtree(candidate)
                    return
            except (OSError,ValueError): return


def prepare_release_update(repo: str, release: ReleaseInfo, token: str = '') -> Path:
    normalize_repo(repo)
    archive,checksum=choose_update_assets(release)
    if archive.size>100_000_000: raise UpdateError('Release ZIP exceeds size limit.')
    data=download_bytes(archive.api_url or archive.url,token)
    text=download_bytes(checksum.api_url or checksum.url,token,limit=64_000).decode('utf-8')
    verify_sha256(data,parse_checksum(text,archive.name))
    staging=Path(tempfile.mkdtemp(prefix='hves-update-'))
    try:
        source=stage_update(data,staging)
        validate_application(source,release.version)
        (staging/'.hves-stage.json').write_text(json.dumps({'root':str(source.resolve())}),encoding='utf-8')
        return source
    except Exception:
        shutil.rmtree(staging,ignore_errors=True)
        raise


def launch_update_helper(source_root: Path, install_root: Path) -> None:
    if any(part in {'site-packages', 'dist-packages'} for part in install_root.resolve().parts):
        raise UpdateError('Use pip to update a pip-installed copy; self-update is only for the standalone app folder.')
    validate_application(source_root)
    # Updating dependencies in a running environment cannot be transactional.
    # Refuse automatic installation when requirements change, rather than install
    # an app that might no longer launch. Manual installer is the safe path.
    if (source_root/'requirements.txt').read_bytes() != (install_root/'requirements.txt').read_bytes():
        raise UpdateError('This release changes dependencies. Extract it into a fresh folder and run its installer instead.')
    helper=install_root/'update_helper.py'
    if not helper.is_file(): raise UpdateError('Installed update helper is missing.')
    subprocess.Popen([sys.executable,str(helper),'--wait-pid',str(os.getpid()),
                      '--source',str(source_root),'--destination',str(install_root),
                      '--restart',str(install_root/'app.py')],close_fds=True)
