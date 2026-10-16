"""Webcam access.  The camera is only open while something is looking."""

from __future__ import annotations

import sys
import threading
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


class SharedCamera:
    """One camera for the background agent, opened on demand.

    The WhatsApp lock needs it for a few seconds at a time; the walk-away lock
    keeps it busy.  Either way it is released while Windows is locked so the
    lock-screen verifier can use it.
    """

    def __init__(self, index: int = 0):
        self._cam = Camera(index)
        self._lock = threading.RLock()

    def read(self) -> np.ndarray | None:
        with self._lock:
            if not self._cam.is_open:
                self._cam.open()
            return self._cam.read()

    def release(self) -> None:
        with self._lock:
            self._cam.close()
