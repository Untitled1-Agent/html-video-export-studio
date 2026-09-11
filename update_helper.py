"""Out-of-process updater: never terminate the running app and never delete user trees."""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _windows_pid_exists(pid: int) -> bool:
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.OpenProcess(0x00100000,False,pid) # SYNCHRONIZE, never PROCESS_TERMINATE
    if not handle:
        return ctypes.get_last_error()!=87 # access-denied: assume alive
    try: return kernel.WaitForSingleObject(handle,0)!=0
    finally: kernel.CloseHandle(handle)


def pid_exists(pid: int) -> bool:
    if pid<=0: return False
    if sys.platform=='win32': return _windows_pid_exists(pid)
    try: os.kill(pid,0)
    except ProcessLookupError: return False
    except PermissionError: return True
    return True


def wait_for_exit(pid: int, timeout: float = 60) -> None:
    deadline=time.monotonic()+timeout
    while pid_exists(pid):
        if time.monotonic()>=deadline:
            raise RuntimeError('App is still running. Update aborted without modifying files.')
        time.sleep(0.1)


def _safe_target(root: Path, relative: Path) -> Path:
    target=root/relative
    current=target
    while current!=root:
        if current.is_symlink(): raise RuntimeError(f'Symlink in installation path: {current}')
        current=current.parent
    if target.exists() and not target.is_file():
        raise RuntimeError(f'Cannot replace directory with application file: {target}')
    return target


def _replace_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd, name=tempfile.mkstemp(prefix='.hves-replace-',dir=str(destination.parent))
    os.close(fd)
    temporary=Path(name)
    try:
        shutil.copy2(source,temporary)
        os.replace(temporary,destination)
    finally: temporary.unlink(missing_ok=True)


def apply_update(source: Path, destination: Path) -> Path:
    """Transactional for handled errors (not power-loss atomic across the entire app).

    Back up only paths shipped by the update, never .git/.venv/exports/preferences.
    New files are removed on rollback. Backups are retained for manual recovery.
    """
    source,destination=source.resolve(),destination.resolve()
    if source==destination or destination in source.parents or source in destination.parents:
        raise RuntimeError('Source and installation must be separate trees.')
    files=sorted(p for p in source.rglob('*') if p.is_file())
    forbidden={'.git','.venv','venv','__pycache__','.env','settings.json','.hves-stage.json'}
    files=[p for p in files if not any(part in forbidden for part in p.relative_to(source).parts)]
    relative=[p.relative_to(source) for p in files]
    for src, rel in zip(files,relative):
        if src.is_symlink(): raise RuntimeError('Symlinks are not permitted in an update.')
        _safe_target(destination,rel)
    backup=Path(tempfile.mkdtemp(prefix='hves-backup-'))
    existed=set()
    touched=[]
    try:
        # Complete all backups before changing any installation file.
        for rel in relative:
            target=destination/rel
            if target.exists():
                b=backup/rel; b.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(target,b); existed.add(rel)
        for src,rel in zip(files,relative):
            touched.append(rel)
            _replace_file(src,destination/rel)
    except Exception as original:
        rollback_errors=[]
        for rel in reversed(touched):
            try:
                if rel in existed: _replace_file(backup/rel,destination/rel)
                else: (destination/rel).unlink(missing_ok=True)
            except Exception as exc: rollback_errors.append(str(exc))
        message=f'Update failed: {original}. Backup retained at {backup}.'
        if rollback_errors: message+=' Rollback incomplete: '+'; '.join(rollback_errors)
        raise RuntimeError(message) from original
    return backup


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--wait-pid',type=int,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--restart',type=Path,required=True)
    args=parser.parse_args(argv)
    destination=args.destination.resolve()
    lock=destination/'.hves-update.lock'
    lock_owned=False
    try:
        restart = args.restart.resolve()
        if restart != destination / 'app.py':
            raise RuntimeError('Unexpected restart path.')
        wait_for_exit(args.wait_pid)
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(fd); lock_owned=True
        from updater import validate_application, cleanup_staging
        validate_application(args.source)
        backup=apply_update(args.source,destination)
        (destination/'update.log').write_text(f'Update installed. Backup: {backup}\n',encoding='utf-8')
        cleanup_staging(args.source)
        subprocess.Popen([sys.executable,str(restart)],cwd=str(destination))
        return 0
    except Exception as exc:
        try: (destination/'update.log').write_text(str(exc)+'\n',encoding='utf-8')
        except OSError: pass
        print(f'Update aborted: {exc}',file=sys.stderr)
        return 1
    finally:
        if lock_owned: lock.unlink(missing_ok=True)


if __name__=='__main__':
    raise SystemExit(main())
