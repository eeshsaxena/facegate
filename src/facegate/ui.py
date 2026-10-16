"""A small always-on-top camera preview with a status line.

If OpenCV has no GUI support (e.g. opencv-python-headless is installed),
the preview quietly turns itself off instead of breaking the face check.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from facegate.engine import Face

log = logging.getLogger("facegate.ui")


class Preview:
    def __init__(self, title: str = "FaceGate"):
        import cv2

        self._cv2 = cv2
        self.title = title
        self._shown = False
        self._broken = False

    def _draw_status(self, view: np.ndarray, status: str) -> None:
        cv2 = self._cv2
        cv2.rectangle(view, (0, 0), (view.shape[1], 34), (30, 30, 30), -1)
        cv2.putText(view, status, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)

    def _show(self, view: np.ndarray) -> bool:
        """Show a frame; False if the user cancelled (Esc or closed window)."""
        if self._broken:
            return True
        cv2 = self._cv2
        try:
            if not self._shown:
                cv2.namedWindow(self.title, cv2.WINDOW_AUTOSIZE)
                try:
                    cv2.setWindowProperty(self.title, cv2.WND_PROP_TOPMOST, 1)
                except cv2.error:
                    pass
                self._shown = True
            cv2.imshow(self.title, view)
            if cv2.waitKey(1) & 0xFF == 27:  # Esc
                return False
            return cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error as e:
            log.warning("camera preview unavailable: %s", e)
            self._broken = True
            return True

    def __call__(self, frame: np.ndarray, faces: list[Face], status: str) -> bool:
        cv2 = self._cv2
        view = frame.copy()
        for i, f in enumerate(faces):
            x, y, w, h = f.box
            color = (80, 200, 80) if i == 0 else (60, 60, 200)
            cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
        self._draw_status(view, status)
        return self._show(view)

    def message(self, text: str, seconds: float = 2.0) -> None:
        """Show a text-only card for a moment (no camera needed)."""
        view = np.zeros((120, 520, 3), np.uint8)
        self._draw_status(view, "FaceGate")
        self._cv2.putText(view, text, (14, 80), self._cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 200, 255), 1,
                          self._cv2.LINE_AA)
        end = time.monotonic() + seconds
        while time.monotonic() < end and self._show(view):
            time.sleep(0.03)

    def close(self) -> None:
        if self._shown:
            try:
                self._cv2.destroyWindow(self.title)
                self._cv2.waitKey(1)
            except self._cv2.error:
                pass
            self._shown = False
