"""Face detection and recognition on top of OpenCV's YuNet + SFace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Face:
    box: tuple[int, int, int, int]  # x, y, w, h in frame pixels
    landmarks: np.ndarray  # 5x2: right eye, left eye, nose, mouth right, mouth left
    score: float
    raw: np.ndarray  # the YuNet row, which SFace needs for alignment

    @property
    def width(self) -> int:
        return self.box[2]


def normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def match_score(embedding: np.ndarray, templates: np.ndarray, top_k: int = 3) -> float:
    """Average cosine similarity to the closest enrolled samples.

    Averaging the best few (instead of taking the single best) keeps one
    lucky sample from carrying a stranger over the line.
    """
    if templates.size == 0:
        return -1.0
    sims = templates @ normalize(embedding)
    k = min(top_k, sims.shape[0])
    return float(np.sort(sims)[-k:].mean())


def _quiet_opencv(cv2) -> None:
    for setter in (
        lambda: cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR),
        lambda: cv2.setLogLevel(2),
    ):
        try:
            setter()
            return
        except Exception:
            pass


class FaceEngine:
    def __init__(self, detector: Path, recognizer: Path, threads: int = 2):
        import cv2

        _quiet_opencv(cv2)
        # Keep it light: we only ever look at one face at ~10 fps.
        cv2.setNumThreads(threads)
        self._cv2 = cv2
        self._size = (320, 240)
        self._det = cv2.FaceDetectorYN.create(str(detector), "", self._size, 0.85, 0.3, 5)
        self._rec = cv2.FaceRecognizerSF.create(str(recognizer), "")

    def detect(self, frame: np.ndarray) -> list[Face]:
        h, w = frame.shape[:2]
        if (w, h) != self._size:
            self._size = (w, h)
            self._det.setInputSize(self._size)
        _, rows = self._det.detect(frame)
        if rows is None:
            return []
        faces = [
            Face(
                box=(int(r[0]), int(r[1]), int(r[2]), int(r[3])),
                landmarks=np.asarray(r[4:14], dtype=np.float32).reshape(5, 2),
                score=float(r[14]),
                raw=np.asarray(r, dtype=np.float32),
            )
            for r in rows
        ]
        faces.sort(key=lambda f: f.box[2] * f.box[3], reverse=True)
        return faces

    def embed(self, frame: np.ndarray, face: Face) -> np.ndarray:
        aligned = self._rec.alignCrop(frame, face.raw)
        return normalize(self._rec.feature(aligned))
