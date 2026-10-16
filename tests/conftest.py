import numpy as np
import pytest

from facegate.engine import Face, normalize


def make_face(width=150, nose_dx=0.0, score=0.95):
    """A Face whose nose sits nose_dx eye-distances off the eye midpoint."""
    x, y = 100, 80
    re, le = np.array([150.0, 140.0]), np.array([210.0, 140.0])
    mid = (re + le) / 2
    nose = mid + np.array([nose_dx * 60.0, 30.0])
    lm = np.stack([re, le, nose, [160.0, 200.0], [200.0, 200.0]]).astype(np.float32)
    raw = np.concatenate([[x, y, width, width], lm.reshape(-1), [score]]).astype(np.float32)
    return Face((x, y, width, width), lm, score, raw)


def unit(seed, dim=128):
    return normalize(np.random.default_rng(seed).standard_normal(dim))


def near(base, noise, seed):
    return normalize(base + noise * np.random.default_rng(seed).standard_normal(base.shape[0]))


class ScriptedEngine:
    """Plays back (faces, embedding) per frame."""

    def __init__(self, script):
        self.script = list(script)
        self.i = -1

    def detect(self, frame):
        self.i += 1
        faces, _ = self.script[min(self.i, len(self.script) - 1)]
        return faces

    def embed(self, frame, face):
        _, emb = self.script[min(self.i, len(self.script) - 1)]
        return emb


class FakeClock:
    def __init__(self, step=0.1):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


@pytest.fixture(autouse=True)
def isolated_instance(monkeypatch):
    """Never signal the real background agent from a test."""
    import uuid

    monkeypatch.setenv("FACEGATE_INSTANCE", f"test-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def frame():
    return np.zeros((480, 640, 3), np.uint8)
