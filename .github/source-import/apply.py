"""One-time PR-comment importer for the reviewed v1.5.2 source tree."""
from pathlib import Path, PurePosixPath
import base64
import hashlib
import json
import lzma
import os
import re
import urllib.request
import shutil

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / ".github" / "source-import"
EXPECTED_PARTS = {
    1: ("bc3eb26a8c3caf3389b361285ba29efff0c55c7250f997cc78b39d3cdea22dc9", 50782),
    2: ("5450136d24a8579b84a309d7e87a3f6ce9e274b819f70d784b9b48324b17eba5", 50782),
    3: ("e3177b0a1183a64312b38d752dab8833fed5fdc83db0fd3541439d44e8ade118", 50780),
}
EXPECTED_COMPRESSED_SHA = "4b5bab2f95840ea560487e554a726e9b5161c2329aa0ef8bfc0b3e41631ac8b0"
EXPECTED_RAW_SHA = "a36a10b567b93d48c430eab2ffcedf09f456c65819c6dd0c014f18e7693ab207"
MARKER = re.compile(r"^<!-- HVES_SOURCE_PAYLOAD (\d)/3 sha256=([0-9a-f]{64}) -->\n([A-Za-z0-9+/=]+)$")

repo = os.environ["GITHUB_REPOSITORY"]
token = os.environ["GITHUB_TOKEN"]
pr_number = (STAGING / "trigger.txt").read_text(encoding="ascii").strip()
if not pr_number.isdigit():
    raise SystemExit("Invalid PR trigger")

url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments?per_page=100"
request = urllib.request.Request(url, headers={
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "html-video-export-studio-source-import",
})
with urllib.request.urlopen(request, timeout=30) as response:
    comments = json.load(response)

parts = {}
for comment in comments:
    match = MARKER.fullmatch(comment.get("body", ""))
    if not match:
        continue
    index = int(match.group(1))
    declared_sha = match.group(2)
    payload = match.group(3)
    expected_sha, expected_size = EXPECTED_PARTS.get(index, (None, None))
    actual_sha = hashlib.sha256(payload.encode("ascii")).hexdigest()
    if declared_sha != expected_sha or actual_sha != expected_sha or len(payload) != expected_size:
        raise SystemExit(f"Payload comment {index} failed checksum/size verification")
    if index in parts and parts[index] != payload:
        raise SystemExit(f"Conflicting payload comment {index}")
    parts[index] = payload

if set(parts) != set(EXPECTED_PARTS):
    raise SystemExit(f"Expected payload comments {sorted(EXPECTED_PARTS)}, found {sorted(parts)}")
encoded = "".join(parts[index] for index in sorted(parts))
compressed = base64.b64decode(encoded, validate=True)
if hashlib.sha256(compressed).hexdigest() != EXPECTED_COMPRESSED_SHA:
    raise SystemExit("Compressed payload checksum mismatch")
raw = lzma.decompress(compressed, format=lzma.FORMAT_XZ, memlimit=128 * 1024 * 1024)
if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != EXPECTED_RAW_SHA:
    raise SystemExit("Reviewed source payload failed raw checksum/size verification")
files = json.loads(raw.decode("utf-8"))
if not isinstance(files, dict) or len(files) != 87:
    raise SystemExit("Unexpected reviewed source inventory")
required = {"app.py", "renderer.py", "cli.py", "README.md", "version.py", "requirements.txt"}
if not required.issubset(files):
    raise SystemExit("Reviewed source is missing required application files")

for name, content in files.items():
    path = PurePosixPath(name)
    if not isinstance(content, str) or path.is_absolute() or "\\" in name or ".." in path.parts:
        raise SystemExit(f"Unsafe source path: {name!r}")
    if ".git" in path.parts or name.startswith(".github/workflows/") or name.startswith(".github/source-import/"):
        raise SystemExit(f"Protected path in reviewed payload: {name}")
    destination = ROOT.joinpath(*path.parts)
    if not destination.resolve().is_relative_to(ROOT):
        raise SystemExit(f"Escaping source path: {name}")
    if destination.suffix == ".py":
        compile(content, name, "exec")

for name, content in files.items():
    destination = ROOT / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8", newline="")
    destination.chmod(0o755 if destination.suffix == ".sh" else 0o644)

shutil.rmtree(STAGING)
(ROOT / ".github" / "workflows" / "import-source.yml").unlink(missing_ok=True)
print(f"Verified and imported {len(files)} reviewed v1.5.2 files")
