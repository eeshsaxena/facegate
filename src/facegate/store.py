"""Enrolled face fingerprints, one ``<SID>.npz`` per Windows account.

Only the 128-number fingerprints are kept, never a photo.
"""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from facegate.models import EMBEDDING_MODEL_ID

_SID = re.compile(r"^S-1-\d+(-\d+)+$")
FORMAT_VERSION = 1


def check_sid(sid: str) -> str:
    if not _SID.match(sid):
        raise ValueError(f"not a Windows SID: {sid!r}")
    return sid


@dataclass
class Enrollment:
    sid: str
    name: str
    embeddings: np.ndarray  # (N, 128) float32, unit length
    model: str = EMBEDDING_MODEL_ID
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


def path_for(directory: Path, sid: str) -> Path:
    return directory / "faces" / f"{check_sid(sid)}.npz"


def save(directory: Path, enrollment: Enrollment) -> Path:
    path = path_for(directory, enrollment.sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = json.dumps(
        {
            "version": FORMAT_VERSION,
            "sid": enrollment.sid,
            "name": enrollment.name,
            "model": enrollment.model,
            "created": enrollment.created,
        }
    )
    buf = io.BytesIO()
    np.savez(buf, embeddings=np.asarray(enrollment.embeddings, dtype=np.float32), meta=np.array(meta))
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(buf.getvalue())
    os.replace(tmp, path)
    return path


def load(directory: Path, sid: str) -> Enrollment | None:
    path = path_for(directory, sid)
    if not path.exists():
        return None
    with np.load(path, allow_pickle=False) as data:
        meta = json.loads(str(data["meta"]))
        emb = np.asarray(data["embeddings"], dtype=np.float32)
    if meta.get("version") != FORMAT_VERSION or meta.get("sid") != sid:
        raise ValueError(f"{path} is not a FaceGate enrollment for {sid}")
    if emb.ndim != 2 or emb.shape[1] != 128 or emb.shape[0] == 0:
        raise ValueError(f"{path} has a bad fingerprint array {emb.shape}")
    return Enrollment(sid=sid, name=meta["name"], embeddings=emb, model=meta["model"], created=meta["created"])


def delete(directory: Path, sid: str) -> bool:
    path = path_for(directory, sid)
    if path.exists():
        path.unlink()
        return True
    return False
