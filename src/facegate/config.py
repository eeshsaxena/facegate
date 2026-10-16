"""Settings and on-disk locations.

Everything lives in ``%LOCALAPPDATA%\\FaceGate``: config.toml, the models
and your enrolled face fingerprints.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path


def user_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FaceGate"


@dataclass
class MatchConfig:
    # SFace cosine similarity.  OpenCV calls 0.363 "same person"; we sit a
    # little above that because a false accept here means someone else gets in.
    threshold: float = 0.42
    # Matching frames needed before we say yes.
    frames_required: int = 3
    # Give up after this many seconds.
    timeout: float = 8.0
    # Ask for a small head turn so a printed photo is not enough.
    liveness: bool = True
    # How far the nose has to swing across the eye line (in eye-distance units).
    liveness_min_range: float = 0.10
    # Ignore faces narrower than this (too far away to trust).
    min_face_px: int = 70


@dataclass
class WhatsAppConfig:
    enabled: bool = True
    processes: list[str] = field(
        default_factory=lambda: ["WhatsApp.exe", "WhatsApp.Root.exe"]
    )
    # Substrings of window titles to guard too, e.g. ["WhatsApp"] for
    # web.whatsapp.com in a browser.  Off by default: it minimises the browser.
    window_titles: list[str] = field(default_factory=list)
    # Lock again after WhatsApp has been out of focus this long (seconds).
    relock_after: float = 120.0
    poll_interval: float = 0.4


@dataclass
class PresenceConfig:
    enabled: bool = False
    # Lock Windows after nobody has been in front of the camera this long.
    absent_seconds: float = 30.0
    # Seconds between checks.  Only the tiny detector runs here.
    interval: float = 2.0
    # Also lock when someone who is not you is sitting there.
    require_owner: bool = False


@dataclass
class Config:
    camera_index: int = 0
    match: MatchConfig = field(default_factory=MatchConfig)
    whatsapp: WhatsAppConfig = field(default_factory=WhatsAppConfig)
    presence: PresenceConfig = field(default_factory=PresenceConfig)


def _merge(obj, data: dict, where: str = "") -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"unknown setting {where}{key}")
        current = getattr(obj, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"{where}{key} must be a table")
            _merge(current, value, f"{where}{key}.")
            continue
        if isinstance(current, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{where}{key} must be true or false")
        elif isinstance(current, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{where}{key} must be a number")
            value = type(current)(value)
        elif isinstance(current, list):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{where}{key} must be a list of strings")
        setattr(obj, key, value)


def parse_config(text: str) -> Config:
    cfg = Config()
    _merge(cfg, tomllib.loads(text))
    return cfg


def load_config(path: Path | None = None) -> Config:
    path = path or user_dir() / "config.toml"
    if not path.exists():
        return Config()
    return parse_config(path.read_text(encoding="utf-8"))


DEFAULT_CONFIG_TOML = """\
# FaceGate settings.  Delete a line to go back to the default.
camera_index = 0

[match]
threshold = 0.42          # higher = stricter, more "not recognised"
frames_required = 3
timeout = 8.0
liveness = true           # turn your head a little while it looks at you
liveness_min_range = 0.10

[whatsapp]
enabled = true
processes = ["WhatsApp.exe", "WhatsApp.Root.exe"]
window_titles = []        # ["WhatsApp"] also guards web.whatsapp.com
relock_after = 120.0

[presence]
enabled = false           # lock Windows when you walk away
absent_seconds = 30.0
interval = 2.0
require_owner = false
"""
