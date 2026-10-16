"""A cheap "is this a real head?" check that works with a plain webcam.

The idea: measure where the nose sits sideways along the line between the
eyes, in units of eye distance.  When a real head turns, the nose sticks out
of the face much further than the mouth does, so it swings past the eye
midpoint by more than the mouth.  A flat photo, however it is tilted or
turned, only gets stretched and sheared as a whole (an affine map), so the
mouth slides by exactly the amount its height predicts.  Subtracting the
mouth's share leaves a number that stays put for any photo and moves for a
real head.

This stops a printed photo or a still image on a phone.  It does NOT stop a
video of you turning your head, or a good 3D mask.  Only IR / depth cameras
really solve that, which is why Windows Hello needs one.
"""

from __future__ import annotations

import numpy as np


def nose_offset(landmarks: np.ndarray) -> float:
    """Sideways nose shift not explained by a flat (planar) face.

    For a point p, ``along(p)`` is its position along the eye axis and
    ``down(p)`` its distance below it, both in eye-distance units.  A planar
    face under any affine map gives ``along(p) = a_p + c * down(p)`` with one
    shared shear ``c``, so ``along(nose) - along(mouth) * down(nose) / down(mouth)``
    cancels ``c`` and is the same for every view of a photo.
    """
    lm = np.asarray(landmarks, dtype=np.float64).reshape(5, 2)
    right_eye, left_eye, nose = lm[0], lm[1], lm[2]
    mouth = (lm[3] + lm[4]) / 2.0
    axis = left_eye - right_eye
    dist2 = float(axis @ axis)
    if dist2 < 1e-6:
        return 0.0
    normal = np.array([-axis[1], axis[0]])
    mid = (right_eye + left_eye) / 2.0

    def along(p):
        return float((p - mid) @ axis / dist2)

    def down(p):
        return float((p - mid) @ normal / dist2)

    d_mouth = down(mouth)
    if abs(d_mouth) < 1e-3:
        return 0.0
    return along(nose) - along(mouth) * down(nose) / d_mouth


class TurnChallenge:
    def __init__(self, min_range: float = 0.12):
        self.min_range = min_range
        self.low = float("inf")
        self.high = float("-inf")

    def feed(self, landmarks: np.ndarray) -> None:
        x = nose_offset(landmarks)
        self.low = min(self.low, x)
        self.high = max(self.high, x)

    @property
    def range(self) -> float:
        return 0.0 if self.high < self.low else self.high - self.low

    @property
    def passed(self) -> bool:
        return self.range >= self.min_range
