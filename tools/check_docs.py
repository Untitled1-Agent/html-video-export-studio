"""Check local Markdown links and declared version consistency (no network)."""
from __future__ import annotations
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import APP_VERSION
from tools.generate_reference import main as check_reference


def local_link_errors(root: Path) -> list[str]:
    errors = []
    for file in [*root.glob('*.md'), *(root/'docs').rglob('*.md')]:
        text = re.sub(r'```.*?```', '', file.read_text(encoding='utf-8'), flags=re.S)
        for raw in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', text):
            path = raw.split(' "')[0].strip('<>')
            parsed = urlsplit(path)
            if not path or path.startswith('#') or parsed.scheme or parsed.netloc:
                continue
            target = (file.parent/unquote(parsed.path)).resolve()
            if not target.is_file() and not target.is_dir():
                errors.append(f'{file.relative_to(root)} -> {path}')
    return errors


def main() -> int:
    errors = local_link_errors(ROOT)
    changelog = (ROOT/'CHANGELOG.md').read_text(encoding='utf-8')
    if not changelog.startswith('# Changelog\n'):
        errors.append('CHANGELOG.md must start with the # Changelog heading')
    text = (ROOT/'pyproject.toml').read_text(encoding='utf-8')
    if not re.search(r'^version\s*=\s*"'+re.escape(APP_VERSION)+r'"\s*$', text, re.M):
        errors.append('pyproject.toml and version.py versions differ')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    if check_reference(['--check']): return 1
    print('Local documentation links, changelog structure, and package version pass.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())