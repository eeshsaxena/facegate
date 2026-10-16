"""Photos of whoever failed a face check ("intruder selfies").

Saved only on this laptop, in ``%LOCALAPPDATA%\\FaceGate\\intruders``, as
JPEGs named by date and time.  Only the newest ``keep`` are kept.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from facegate.config import user_dir


def folder() -> Path:
    return user_dir() / "intruders"


def photos(directory: Path | None = None) -> list[Path]:
    """Saved photos, oldest first."""
    directory = directory or folder()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.jpg"))


def save(frame: np.ndarray, directory: Path | None = None, keep: int = 100,
         now: datetime | None = None, reason: str = "") -> Path:
    import cv2

    directory = directory or folder()
    directory.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    stem = now.strftime("%Y-%m-%d_%H-%M-%S")
    if reason:
        stem += f"_{reason}"
    path = directory / f"{stem}.jpg"
    n = 1
    while path.exists():
        n += 1
        path = directory / f"{stem}_{n}.jpg"
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise OSError("could not encode the photo")
    path.write_bytes(data.tobytes())  # imwrite chokes on non-ASCII paths
    for old in photos(directory)[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return path


def clear(directory: Path | None = None) -> int:
    gone = photos(directory)
    for p in gone:
        p.unlink(missing_ok=True)
    return len(gone)
