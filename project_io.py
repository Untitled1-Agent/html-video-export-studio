from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models import JobStatus, QueueJob, dataclass_to_dict, job_config_from_dict
from version import APP_NAME, APP_VERSION, PROJECT_FORMAT_VERSION


class ProjectError(RuntimeError):
    pass


@dataclass
class ProjectDocument:
    jobs: list[QueueJob]
    name: str = "Untitled Project"
    notes: str = ""


def save_project(document: ProjectDocument, path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "html-video-export-studio-project",
        "format_version": PROJECT_FORMAT_VERSION,
        "created_by": f"{APP_NAME} {APP_VERSION}",
        "name": document.name,
        "notes": document.notes,
        "jobs": [dataclass_to_dict(job.config) for job in document.jobs],
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix="project-", suffix=".json", dir=str(path.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_project(path: Path) -> ProjectDocument:
    path = path.expanduser().resolve()
    try:
        if path.stat().st_size > 10_000_000:
            raise ProjectError('Project exceeds the 10 MB safety limit.')
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProjectError(f"Could not read project: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectError("Project root must be a JSON object.")
    if payload.get("format") != "html-video-export-studio-project":
        raise ProjectError("This is not an HTML Video Export Studio project.")
    version = payload.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1 or version > PROJECT_FORMAT_VERSION:
        raise ProjectError(f"Unsupported project format version: {version}")
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list):
        raise ProjectError("Project jobs must be a list.")
    jobs: list[QueueJob] = []
    errors: list[str] = []
    for index, raw in enumerate(raw_jobs):
        try:
            if not isinstance(raw, dict):
                raise ValueError("job is not an object")
            config = job_config_from_dict(raw)
            # Resolve imported relative paths against the project, never the current working directory.
            from models import SourceKind
            if config.source.kind == SourceKind.FILE:
                value = Path(config.source.value).expanduser()
                config.source.value = str(value.resolve() if value.is_absolute() else (path.parent / value).resolve())
            for owner, attr in ((config.render,'output_directory'),(config.render.audio,'path')):
                text = getattr(owner,attr)
                if text:
                    value = Path(text).expanduser()
                    setattr(owner,attr,str(value.resolve() if value.is_absolute() else (path.parent/value).resolve()))
            config.validate(for_export=False)
            jobs.append(
                QueueJob(
                    job_id=uuid.uuid4().hex,
                    config=config,
                    status=JobStatus.QUEUED,
                )
            )
        except Exception as exc:
            errors.append(f"Job {index + 1}: {exc}")
    if errors:
        raise ProjectError("Some project jobs are invalid:\n- " + "\n- ".join(errors))
    return ProjectDocument(
        jobs=jobs,
        name=str(payload.get("name") or path.stem),
        notes=str(payload.get("notes") or ""),
    )
