import hashlib
import io

import pytest

from facegate import models
from facegate.models import ModelFile, ModelsMissing


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_models(monkeypatch, payloads):
    files = tuple(
        ModelFile(f"m{i}.onnx", f"https://example.invalid/m{i}", hashlib.sha256(p).hexdigest(), len(p))
        for i, p in enumerate(payloads)
    )
    monkeypatch.setattr(models, "MODELS", files)
    return files


def test_download_verify_and_reuse(tmp_path, monkeypatch):
    fake_models(monkeypatch, [b"detector", b"recognizer!"])
    fetched = []

    def opener(url, timeout):
        fetched.append(url)
        return FakeResp(b"detector" if url.endswith("m0") else b"recognizer!")

    det, rec = models.ensure_models(tmp_path, opener=opener)
    assert det.read_bytes() == b"detector" and rec.read_bytes() == b"recognizer!"
    assert len(fetched) == 2
    models.ensure_models(tmp_path, opener=opener)
    assert len(fetched) == 2  # cached


def test_tampered_download_is_refused(tmp_path, monkeypatch):
    fake_models(monkeypatch, [b"good"])
    with pytest.raises(ModelsMissing, match="checksum"):
        models.ensure_models(tmp_path, opener=lambda u, timeout: FakeResp(b"evil"))
    assert list(tmp_path.iterdir()) == []


def test_damaged_file_is_replaced(tmp_path, monkeypatch):
    fake_models(monkeypatch, [b"good", b"also"])
    (tmp_path / "m0.onnx").write_bytes(b"bad!")
    models.ensure_models(tmp_path, opener=lambda u, timeout: FakeResp(b"good" if u.endswith("m0") else b"also"))
    assert (tmp_path / "m0.onnx").read_bytes() == b"good"


def test_offline_mode_reports_missing(tmp_path, monkeypatch):
    fake_models(monkeypatch, [b"good"])
    with pytest.raises(ModelsMissing, match="facegate models"):
        models.ensure_models(tmp_path, download=False)


def test_pinned_hashes_look_right():
    for m in (models.DETECTOR, models.RECOGNIZER):
        assert len(m.sha256) == 64 and m.url.startswith("https://github.com/opencv/opencv_zoo/")
