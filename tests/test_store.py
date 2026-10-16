import numpy as np
import pytest

from conftest import unit
from facegate import store

SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"


def test_roundtrip(tmp_path):
    emb = np.stack([unit(i) for i in range(5)]).astype(np.float32)
    path = store.save(tmp_path, store.Enrollment(SID, "LAPTOP\\eesh", emb))
    assert path.name == f"{SID}.npz"
    got = store.load(tmp_path, SID)
    assert got.name == "LAPTOP\\eesh"
    assert np.allclose(got.embeddings, emb)
    assert got.model == "sface_2021dec"


def test_missing_is_none_and_delete(tmp_path):
    assert store.load(tmp_path, SID) is None
    assert store.delete(tmp_path, SID) is False
    store.save(tmp_path, store.Enrollment(SID, "x", np.stack([unit(1)])))
    assert store.delete(tmp_path, SID) is True
    assert store.load(tmp_path, SID) is None


@pytest.mark.parametrize("bad", ["", "..\\evil", "S-1-5-21-1/../../x", "eesh"])
def test_bad_sid_cannot_escape_the_folder(tmp_path, bad):
    with pytest.raises(ValueError):
        store.path_for(tmp_path, bad)


def test_file_for_another_account_is_rejected(tmp_path):
    other = "S-1-5-21-1-2-3-1002"
    store.save(tmp_path, store.Enrollment(other, "x", np.stack([unit(1)])))
    (tmp_path / "faces" / f"{other}.npz").rename(tmp_path / "faces" / f"{SID}.npz")
    with pytest.raises(ValueError):
        store.load(tmp_path, SID)


def test_bad_shape_is_rejected(tmp_path):
    store.save(tmp_path, store.Enrollment(SID, "x", np.zeros((3, 64), np.float32)))
    with pytest.raises(ValueError):
        store.load(tmp_path, SID)
