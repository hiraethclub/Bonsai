"""Local config on disk. Credentials live here and nowhere else."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import APP_NAME

CONFIG_FILENAME = "config.json"
SETTINGS_FILENAME = "settings.json"


class ConfigError(Exception):
    pass


def config_dir() -> Path:
    """Per-user config folder: ~/.config/bonsai on Linux, %APPDATA%\\bonsai on Windows.

    BONSAI_CONFIG_DIR overrides it (used by the tests).
    """
    override = os.environ.get("BONSAI_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


@dataclass
class Config:
    server: str = ""
    username: str = ""
    password: str = ""
    user_agent: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.server and self.username and self.password)


def _write_private(path: Path, text: str) -> None:
    """Write a file readable only by the current user (0600 on POSIX)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    if os.name == "posix":
        os.chmod(path, 0o600)


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} should contain a JSON object")
    known = Config.__dataclass_fields__
    return Config(**{k: str(v) for k, v in data.items() if k in known and v is not None})


def save_config(config: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    _write_private(path, json.dumps(asdict(config), indent=2) + "\n")
    return path


def ensure_config_template(path: Path | None = None) -> tuple[Path, bool]:
    """Create an empty config for the user to fill in. Returns (path, created)."""
    path = path or config_path()
    if path.exists():
        return path, False
    save_config(Config(server="http://example.com:8080"), path)
    return path, True


@dataclass
class Settings:
    """Non-secret app preferences (kept apart from the credentials)."""

    last_export_path: str = ""
    stream_format: str = "ts"
    include_epg: bool = False
    hidden_tiers: list[str] = field(default_factory=lambda: ["UHD", "FHD"])


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILENAME


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    defaults = Settings()
    values = {}
    for name in Settings.__dataclass_fields__:
        value = data.get(name)
        if isinstance(value, type(getattr(defaults, name))):
            values[name] = value
    return Settings(**values)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    path = path or settings_path()
    _write_private(path, json.dumps(asdict(settings), indent=2) + "\n")
    return path
