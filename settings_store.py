from __future__ import annotations

import json
import os
import platform
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from version import APP_NAME, SETTINGS_FORMAT_VERSION


@dataclass
class AppSettings:
    format_version: int = SETTINGS_FORMAT_VERSION
    workers: int = 2
    default_recipe_key: str = "motion_graphics_master"
    default_output_directory: str = ""
    save_next_to_source: bool = True
    overwrite: bool = False
    auto_analyze_on_add: bool = False
    update_repo: str = ""
    check_updates_at_startup: bool = False
    recent_projects: list[str] = field(default_factory=list)
    recent_sources: list[str] = field(default_factory=list)
    window_geometry: str = "1180x800"


def app_data_directory() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "HTMLVideoExportStudio"


def settings_path() -> Path:
    return app_data_directory() / "settings.json"


def _coerce_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _coerce_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, Path))][:20]


def load_settings(path: Path | None = None) -> AppSettings:
    path = path or settings_path()
    defaults = AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return defaults
    except Exception:
        return defaults

    # Each field migrates independently. One malformed value should not discard
    # otherwise valid preferences.
    try:
        workers = max(1, min(8, int(data.get("workers", defaults.workers))))
    except Exception:
        workers = defaults.workers

    recipe = data.get("default_recipe_key", defaults.default_recipe_key)
    if not isinstance(recipe, str):
        recipe = defaults.default_recipe_key

    output = data.get("default_output_directory", defaults.default_output_directory)
    if not isinstance(output, str):
        output = defaults.default_output_directory

    repo = data.get("update_repo", defaults.update_repo)
    if not isinstance(repo, str):
        repo = defaults.update_repo

    geometry = data.get("window_geometry", defaults.window_geometry)
    if not isinstance(geometry, str) or "x" not in geometry:
        geometry = defaults.window_geometry

    return AppSettings(
        format_version=SETTINGS_FORMAT_VERSION,
        workers=workers,
        default_recipe_key=recipe,
        default_output_directory=output,
        save_next_to_source=_coerce_bool(
            data.get("save_next_to_source"), defaults.save_next_to_source
        ),
        overwrite=_coerce_bool(data.get("overwrite"), defaults.overwrite),
        auto_analyze_on_add=_coerce_bool(
            data.get("auto_analyze_on_add"), defaults.auto_analyze_on_add
        ),
        update_repo=repo,
        check_updates_at_startup=_coerce_bool(
            data.get("check_updates_at_startup"), defaults.check_updates_at_startup
        ),
        recent_projects=_coerce_list(data.get("recent_projects")),
        recent_sources=_coerce_list(data.get("recent_sources")),
        window_geometry=geometry,
    )


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": SETTINGS_FORMAT_VERSION,
        "workers": settings.workers,
        "default_recipe_key": settings.default_recipe_key,
        "default_output_directory": settings.default_output_directory,
        "save_next_to_source": settings.save_next_to_source,
        "overwrite": settings.overwrite,
        "auto_analyze_on_add": settings.auto_analyze_on_add,
        "update_repo": settings.update_repo,
        "check_updates_at_startup": settings.check_updates_at_startup,
        "recent_projects": settings.recent_projects[:20],
        "recent_sources": settings.recent_sources[:20],
        "window_geometry": settings.window_geometry,
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix="settings-", suffix=".json", dir=str(path.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def add_recent(items: list[str], value: str, limit: int = 10) -> list[str]:
    normalized = str(value)
    return [normalized] + [item for item in items if item != normalized][: limit - 1]
