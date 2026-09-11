"""Deterministic, allowlisted source/update archives (never a recursive user-folder dump)."""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import APP_VERSION
from updater import validate_application

ROOT_FILES = frozenset({
    'app.py', 'cli.py', 'media_pipeline.py', 'models.py', 'presets.py',
    'processing.py', 'project_io.py', 'renderer.py', 'settings_store.py',
    'update_helper.py', 'updater.py', 'version.py', 'pyproject.toml',
    'requirements.txt', 'requirements-dev.txt', 'MANIFEST.in', '.gitignore',
    '.gitattributes', 'README.md', 'CHANGELOG.md', 'CONTRIBUTING.md',
    'SECURITY.md', 'NOTICE.md', 'GITHUB_SETUP.md', 'TEST_REPORT.md',
    'install_windows.bat', 'run_windows.bat', 'install_macos_linux.sh',
    'run_macos_linux.sh', 'RELEASE_NOTES_1.5.1.md', 'RELEASE_NOTES_1.5.2.md',
})
SOURCE_TREES = frozenset({'docs', 'examples', 'tests', 'tools', '.github', 'validation'})
EXCLUDE = frozenset({'.git', '.venv', 'venv', '__pycache__', 'build', 'dist',
                     '.pytest_cache', '.mypy_cache', '.ruff_cache', '.env',
                     'node_modules', 'exports', 'user_inputs'})
EXTENSIONS = frozenset({'.py', '.md', '.txt', '.toml', '.yml', '.yaml', '.html',
                        '.bat', '.sh', '.json'})


def sources(root: Path = ROOT):
    """Yield (absolute file, relative file) for the distributable project only.

    Customer HTML/media saved at the app root are intentionally not included.
    Everything under docs/examples/tests/tools/.github/validation is repository
    material: do not put private inputs there. Symlinked files/directories are ignored.
    """
    root = root.resolve()
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if any(part in EXCLUDE or part.endswith('.egg-info') or part.startswith('.env')
               for part in rel.parts):
            continue
        if any(parent.is_symlink() for parent in (p, *p.parents) if parent != root and root in parent.parents):
            continue
        if not p.is_file():
            continue
        allowed = (len(rel.parts) == 1 and rel.name in ROOT_FILES) or (
            len(rel.parts) > 1 and rel.parts[0] in SOURCE_TREES and p.suffix in EXTENSIONS)
        if not allowed:
            continue
        if p.suffix == '.py':
            compile(p.read_bytes(), str(rel), 'exec')
        yield p, rel


def package(destination: Path, root: Path = ROOT) -> list[Path]:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    validate_application(root, APP_VERSION)
    entries = list(sources(root))
    outputs = []
    for name, update in [
        (f'HTML-Video-Export-Studio-v{APP_VERSION}-GitHub-ready.zip', False),
        (f'html-video-export-studio-v{APP_VERSION}.zip', True),
    ]:
        output = destination/name
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for p, rel in entries:
                if update and rel.parts[0] == '.github':
                    continue
                info = zipfile.ZipInfo('HTML-Video-Export-Studio/'+rel.as_posix(), (2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100755 if p.suffix == '.sh' else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, p.read_bytes())
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        checksum = output.with_name(output.name+'.sha256')
        checksum.write_text(digest+'  '+output.name+'\n', encoding='utf-8')
        outputs.extend([output, checksum])
        print(f'{output.name}  SHA256 {digest}')
    return outputs


def main() -> None:
    package(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT/'dist')


if __name__ == '__main__':
    main()
