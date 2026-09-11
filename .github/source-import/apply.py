"""One-time, checksum-verified transfer of the reviewed source tree."""
from pathlib import Path, PurePosixPath
import base64
import hashlib
import json
import lzma
import shutil

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / ".github" / "source-import"
MANIFEST = json.loads((STAGING / "manifest.json").read_text(encoding="utf-8"))
EXPECTED_COMPRESSED_SHA = "4b5bab2f95840ea560487e554a726e9b5161c2329aa0ef8bfc0b3e41631ac8b0"
EXPECTED_RAW_SHA = "a36a10b567b93d48c430eab2ffcedf09f456c65819c6dd0c014f18e7693ab207"

if MANIFEST.get("encoding") != "base64+xz+json":
    raise SystemExit("Unexpected source encoding")
if MANIFEST.get("compressed_sha256") != EXPECTED_COMPRESSED_SHA:
    raise SystemExit("Unexpected compressed payload checksum")
if MANIFEST.get("uncompressed_sha256") != EXPECTED_RAW_SHA:
    raise SystemExit("Unexpected raw payload checksum")

encoded_parts = []
for item in MANIFEST["parts"]:
    name = item["name"]
    if not name.startswith("part-") or not name[5:].isdigit():
        raise SystemExit("Invalid source part name")
    text = (STAGING / name).read_text(encoding="ascii")
    data = text.encode("ascii")
    if len(text) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise SystemExit(f"Source part checksum mismatch: {name}")
    encoded_parts.append(text)

compressed = base64.b64decode("".join(encoded_parts), validate=True)
if hashlib.sha256(compressed).hexdigest() != EXPECTED_COMPRESSED_SHA:
    raise SystemExit("Compressed payload checksum mismatch")
raw = lzma.decompress(compressed, format=lzma.FORMAT_XZ, memlimit=128 * 1024 * 1024)
if len(raw) > 2 * 1024 * 1024:
    raise SystemExit("Source payload is unexpectedly large")
if hashlib.sha256(raw).hexdigest() != EXPECTED_RAW_SHA:
    raise SystemExit("Raw payload checksum mismatch")
files = json.loads(raw.decode("utf-8"))
if not isinstance(files, dict) or len(files) != MANIFEST["file_count"]:
    raise SystemExit("Invalid source file inventory")

required = {"app.py", "renderer.py", "cli.py", "README.md", "version.py", "requirements.txt"}
if not required.issubset(files):
    raise SystemExit("Reviewed payload is missing required application components")

for name, content in files.items():
    path = PurePosixPath(name)
    if not isinstance(content, str) or path.is_absolute() or "\\" in name or ".." in path.parts:
        raise SystemExit(f"Invalid source entry: {name!r}")
    if ".git" in path.parts or name.startswith(".github/workflows/") or name.startswith(".github/source-import/"):
        raise SystemExit(f"Protected path in payload: {name}")
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
workflow = ROOT / ".github" / "workflows" / "import-source.yml"
workflow.unlink(missing_ok=True)
print(f"Verified and imported {len(files)} reviewed files for version {MANIFEST['version']}")
