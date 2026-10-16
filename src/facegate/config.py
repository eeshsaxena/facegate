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
    # Lock again once WhatsApp has been out of focus this long (seconds).
    # 1 s means: every time it comes back to the front, check the face.
    relock_after: float = 1.0
    # If WhatsApp never gets the focus after an unlock, lock after this long.
    focus_grace: float = 10.0
    # Keep the webcam open this long after a check so the next one is instant
    # (0 = turn it off right away; the camera light stays on meanwhile).
    camera_keep_open: float = 15.0
    # What to do when the face doesn't match (or the check is cancelled):
    # "close" force-quits WhatsApp, "minimize" just keeps it hidden.
    on_fail: str = "close"
    # While WhatsApp is open, quietly re-check the face this often (seconds,
    # 0 = off).  No camera window, no head turn.
    recheck_interval: float = 60.0
    # If nobody is in front of the camera, WhatsApp stays open and the
    # re-check repeats this often until someone is.
    recheck_absent_interval: float = 10.0
    # How long one background re-check may look.
    recheck_timeout: float = 6.0
    # Save a photo of whoever fails the check (kept on this laptop only).
    save_intruder_photos: bool = True
    intruder_photos_keep: int = 100
    # After this many faces that didn't match in a row, stop asking for a while.
    max_failures: int = 3
    cooldown: float = 30.0
    # Backup check interval; window changes are caught instantly by a hook.
    poll_interval: float = 1.0


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
        elif isinstance(current, str):
            if not isinstance(value, str):
                raise ValueError(f"{where}{key} must be text")
        elif isinstance(current, list):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{where}{key} must be a list of strings")
        setattr(obj, key, value)


def parse_config(text: str) -> Config:
    cfg = Config()
    _merge(cfg, tomllib.loads(text))
    if cfg.whatsapp.on_fail not in ("close", "minimize"):
        raise ValueError('whatsapp.on_fail must be "close" or "minimize"')
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
relock_after = 1.0        # seconds out of focus before it locks again
focus_grace = 10.0
camera_keep_open = 15.0   # keep the webcam warm for quick re-checks (0 = off)
on_fail = "close"         # wrong face: "close" quits WhatsApp, "minimize" hides it
recheck_interval = 60.0   # quiet face re-check while WhatsApp is open (0 = off)
recheck_absent_interval = 10.0   # nobody there: stay open, re-check this often
recheck_timeout = 6.0
save_intruder_photos = true   # photo of whoever fails the check (local only)
intruder_photos_keep = 100
max_failures = 3          # wrong faces in a row before a cooldown
cooldown = 30.0

[presence]
enabled = false           # lock Windows when you walk away
absent_seconds = 30.0
interval = 2.0
require_owner = false
"""
