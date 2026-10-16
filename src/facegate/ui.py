"""A small always-on-top camera preview with a status line."""

from __future__ import annotations

import numpy as np

from facegate.engine import Face


class Preview:
    def __init__(self, title: str = "FaceGate"):
        import cv2

        self._cv2 = cv2
        self.title = title
        self._shown = False

    def __call__(self, frame: np.ndarray, faces: list[Face], status: str) -> bool:
        cv2 = self._cv2
        view = frame.copy()
        for i, f in enumerate(faces):
            x, y, w, h = f.box
            color = (80, 200, 80) if i == 0 else (60, 60, 200)
            cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
        cv2.rectangle(view, (0, 0), (view.shape[1], 34), (30, 30, 30), -1)
        cv2.putText(view, status, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)
        if not self._shown:
            cv2.namedWindow(self.title, cv2.WINDOW_AUTOSIZE)
            try:
                cv2.setWindowProperty(self.title, cv2.WND_PROP_TOPMOST, 1)
            except Exception:
                pass
            self._shown = True
        cv2.imshow(self.title, view)
        key = cv2.waitKey(1) & 0xFF
        if key == 27:  # Esc cancels
            return False
        try:
            if cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) < 1:
                return False  # window closed with the X
        except Exception:
            pass
        return True

    def close(self) -> None:
        if self._shown:
            self._cv2.destroyWindow(self.title)
            self._cv2.waitKey(1)
            self._shown = False
