"""The two ONNX models from the OpenCV Model Zoo, pinned by SHA-256.

* YuNet (230 KB) finds faces and five landmarks.
* SFace (37 MB) turns an aligned face into a 128-number fingerprint.

Both run on the CPU through OpenCV's DNN module, no GPU or extra runtime.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ModelFile:
    filename: str
    url: str
    sha256: str
    size: int


_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"

DETECTOR = ModelFile(
    "face_detection_yunet_2023mar.onnx",
    f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    232_589,
)
RECOGNIZER = ModelFile(
    "face_recognition_sface_2021dec.onnx",
    f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    38_696_353,
)
MODELS = (DETECTOR, RECOGNIZER)

# Stored next to every enrollment so a model swap can't silently compare
# fingerprints from two different networks.
EMBEDDING_MODEL_ID = "sface_2021dec"


class ModelsMissing(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _good(path: Path, model: ModelFile) -> bool:
    return path.is_file() and path.stat().st_size == model.size and sha256_of(path) == model.sha256


def _download(model: ModelFile, dest: Path, opener=urllib.request.urlopen) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    with opener(model.url, timeout=60) as resp, open(tmp, "wb") as out:
        while chunk := resp.read(1 << 16):
            h.update(chunk)
            out.write(chunk)
    if h.hexdigest() != model.sha256:
        tmp.unlink(missing_ok=True)
        raise ModelsMissing(f"{model.filename}: checksum mismatch, refusing to use it")
    os.replace(tmp, dest)


def ensure_models(
    directory: Path,
    download: bool = True,
    log: Callable[[str], None] = lambda _msg: None,
    opener=urllib.request.urlopen,
) -> tuple[Path, Path]:
    """Return (detector, recognizer) paths, fetching them if allowed."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for model in MODELS:
        path = directory / model.filename
        if not _good(path, model):
            if not download:
                raise ModelsMissing(
                    f"{path} is missing or damaged; run `facegate models` first"
                )
            log(f"downloading {model.filename} ({model.size / 1e6:.1f} MB)")
            _download(model, path, opener)
        paths.append(path)
    return paths[0], paths[1]
