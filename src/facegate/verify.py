"""The two core loops: enroll a face, and check a face against an enrollment.

Both take a ``read()`` callable that returns a frame (or None), so they run
the same way on a real webcam, a video file or a list of test frames.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

from facegate.config import MatchConfig
from facegate.engine import Face, match_score, normalize
from facegate.liveness import TurnChallenge

# Exit codes of `facegate verify`, read by the credential provider.
EXIT_OK = 0
EXIT_NO_MATCH = 1
EXIT_CAMERA = 2
EXIT_NOT_ENROLLED = 3
EXIT_ERROR = 4

Frame = np.ndarray
OnFrame = Callable[[Frame, list[Face], str], bool]  # return False to cancel


class Engine(Protocol):
    def detect(self, frame: Frame) -> list[Face]: ...
    def embed(self, frame: Frame, face: Face) -> np.ndarray: ...


@dataclass
class VerifyResult:
    ok: bool
    reason: str  # match | timeout | camera | cancelled
    best_score: float = -1.0
    matched_frames: int = 0
    turn_range: float = 0.0

    @property
    def exit_code(self) -> int:
        if self.ok:
            return EXIT_OK
        return EXIT_CAMERA if self.reason == "camera" else EXIT_NO_MATCH


def verify(
    read: Callable[[], Frame | None],
    engine: Engine,
    templates: np.ndarray,
    cfg: MatchConfig,
    on_frame: OnFrame | None = None,
    clock: Callable[[], float] = time.monotonic,
    max_bad_reads: int = 30,
) -> VerifyResult:
    start = clock()
    best = -1.0
    matched = 0
    bad_reads = 0
    turn = TurnChallenge(cfg.liveness_min_range)

    def result(ok: bool, reason: str) -> VerifyResult:
        return VerifyResult(ok, reason, best, matched, turn.range)

    while clock() - start < cfg.timeout:
        frame = read()
        if frame is None:
            bad_reads += 1
            if bad_reads >= max_bad_reads:
                return result(False, "camera")
            continue
        bad_reads = 0

        faces = engine.detect(frame)
        face = faces[0] if faces else None
        if face is None:
            status = "Look at the camera"
        elif face.width < cfg.min_face_px:
            status = "Come a little closer"
        else:
            score = match_score(engine.embed(frame, face), templates)
            best = max(best, score)
            if score >= cfg.threshold:
                matched += 1
                # Only frames that already match feed the turn check, so the
                # head that turns has to be the enrolled one.
                turn.feed(face.landmarks)
            if matched >= cfg.frames_required and (not cfg.liveness or turn.passed):
                return result(True, "match")
            if matched and cfg.liveness and not turn.passed:
                status = "Turn your head slightly"
            else:
                status = "Checking..."

        if on_frame is not None and on_frame(frame, faces, status) is False:
            return result(False, "cancelled")

    return result(False, "timeout")


class EnrollError(RuntimeError):
    pass


def enroll(
    read: Callable[[], Frame | None],
    engine: Engine,
    samples: int = 15,
    timeout: float = 40.0,
    min_face_px: int = 90,
    gap: float = 0.25,
    on_frame: OnFrame | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> np.ndarray:
    """Collect fingerprints from a single face moving around a little."""
    start = clock()
    last = -gap
    got: list[np.ndarray] = []
    while len(got) < samples:
        if clock() - start > timeout:
            raise EnrollError(f"only got {len(got)} of {samples} good samples; try better light")
        frame = read()
        if frame is None:
            continue
        faces = engine.detect(frame)
        if len(faces) > 1:
            status = "Only one face in view, please"
        elif not faces:
            status = "Look at the camera"
        elif faces[0].width < min_face_px:
            status = "Come a little closer"
        else:
            status = f"Slowly move your head around  {len(got)}/{samples}"
            now = clock()
            if now - last >= gap:
                got.append(engine.embed(frame, faces[0]))
                last = now
        if on_frame is not None and on_frame(frame, faces, status) is False:
            raise EnrollError("cancelled")

    emb = np.stack([normalize(e) for e in got])
    sims = emb @ emb.T
    mean_pair = (sims.sum() - len(emb)) / (len(emb) * (len(emb) - 1))
    if mean_pair < 0.5:
        raise EnrollError("the samples don't look like one person; try again alone")
    return emb.astype(np.float32)
