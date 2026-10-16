"""Webcam access.  The camera is only open while something is looking."""

from __future__ import annotations

import sys
import time

import numpy as np


class CameraError(RuntimeError):
    pass


class Camera:
    def __init__(self, index: int = 0, width: int = 640, height: int = 480, warmup: float = 0.4):
        self.index = index
        self.width = width
        self.height = height
        self.warmup = warmup
        self._cap = None

    def open(self) -> "Camera":
        import cv2

        # DirectShow opens in ~0.3 s; the default Media Foundation backend
        # can take several seconds on some laptops.
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            raise CameraError(f"could not open camera {self.index}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cap = cap
        # Let auto-exposure settle; the first frames are often black.
        end = time.monotonic() + self.warmup
        while time.monotonic() < end:
            cap.read()
        return self

    def flush(self, min_seconds: float = 0.3, max_grabs: int = 90, clock=time.monotonic) -> int:
        """Drop frames buffered while nobody was reading; return how many.

        A warm camera can hand back several old frames instantly, e.g. ones
        from the previous face check, which must never count again.  Keep
        grabbing until ``min_seconds`` have passed AND a grab had to wait
        for a new frame, i.e. we are reading live.
        """
        if self._cap is None:
            return 0
        start = clock()
        for n in range(1, max_grabs + 1):
            t = clock()
            self._cap.grab()
            waited = clock() - t
            if clock() - start >= min_seconds and waited >= 0.015:
                return n
        return max_grabs

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    def __enter__(self) -> "Camera":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

