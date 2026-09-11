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
    1: ("a21bbf50457b625f463c32b2dd994dc0e91794a9c454a4d847f02db00346ff17", 12696),
    2: ("ac8f5cd362b00ce6dbf480c6aaa22291d842b689c286f9b1f1900fccdd62ea89", 12696),
    3: ("b404bbb6d8392af5650b7369bf5ccb049843d8140c8511c00a644ccda819be07", 12696),
    4: ("57069d355384e9994fec3a518b5da051a9752b34fae78cf82a06a125606350da", 12696),
    5: ("5cb61fdd183e8bcb28470ccc33e9d1b89f1e4eb9e411218324250ff93596cb30", 12696),
    6: ("68efcf6dc8d7c9067919cdddc69c3a064d2ea2072e7c6bab7b0e49871fb56927", 12696),
    7: ("87a96c85fd6236a0545a5268f6832c42c1d255fb92f640c592b0720079c3a0ba", 12696),
    8: ("675548f05d4212bcc6738e3acd3bee4de3a762126b230baca3a6112d0c38ef24", 12696),
    9: ("f715beeaedd46a7f1a98bab747424ce914713c122c34ffd87c491b3e59f8c902", 12696),
    10: ("53a3c48edf5b6267d90e6eea2681c9e8dccc5309ce92063741acc5027df01caf", 12696),
    11: ("85bacda01d1a40ba825f27c18d88cc2258546ff49c723905eed2d07e3b4d493a", 12696),
    12: ("9130e9f0dd21d061841e1a8f6858cd4f8fcd8969de47db40dfff9c7cb1e782a4", 12688),
}
EXPECTED_COMPRESSED_SHA = "61a5531fa5efafca6d8520ae97be9b247a554965b601f5f9f4e671ce0b2c6f96"
EXPECTED_RAW_SHA = "a36a10b567b93d48c430eab2ffcedf09f456c65819c6dd0c014f18e7693ab207"
MARKER = re.compile(r"^<!-- HVES_SOURCE_PAYLOAD (\d{1,2})/12 sha256=([0-9a-f]{64}) -->\n([A-Za-z0-9+/=]+)$")

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
compressed = base64.b64decode("".join(parts[index] for index in sorted(parts)), validate=True)
if hashlib.sha256(compressed).hexdigest() != EXPECTED_COMPRESSED_SHA:
    raise SystemExit("Compressed payload checksum mismatch")
raw = lzma.decompress(compressed, format=lzma.FORMAT_XZ, memlimit=128 * 1024 * 1024)
if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != EXPECTED_RAW_SHA:
    raise SystemExit("Reviewed source payload failed raw checksum/size verification")
files = json.loads(raw.decode("utf-8"))
if not isinstance(files, dict) or len(files) != 87:
    raise SystemExit("Unexpected reviewed source inventory")
required = {"app.py", "renderer.py", "cli.py", "README.md", "version.py", "requirements.txt", "requirements-dev.txt"}
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
