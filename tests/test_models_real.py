"""Loads the real ONNX models (downloaded once into the user folder)."""

import os

import numpy as np
import pytest

from conftest import make_face
from facegate.config import user_dir
from facegate.engine import FaceEngine
from facegate.models import ModelsMissing, ensure_models

pytestmark = pytest.mark.models


@pytest.fixture(scope="module")
def engine():
    download = os.environ.get("FACEGATE_DOWNLOAD_MODELS") == "1"
    try:
        det, rec = ensure_models(user_dir() / "models", download=download)
    except ModelsMissing:
        pytest.skip("models not downloaded (run `facegate models` or set FACEGATE_DOWNLOAD_MODELS=1)")
    return FaceEngine(det, rec)


def test_blank_frame_has_no_faces(engine):
    assert engine.detect(np.zeros((480, 640, 3), np.uint8)) == []


def test_embedding_is_deterministic_unit_vector(engine):
    frame = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    face = make_face()
    a, b = engine.embed(frame, face), engine.embed(frame, face)
    assert a.shape == (128,) and np.allclose(a, b)
    assert np.isclose(np.linalg.norm(a), 1.0, atol=1e-5)
