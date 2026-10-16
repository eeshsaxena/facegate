from datetime import datetime, timedelta

import numpy as np

from facegate import intruders


def shot():
    return np.full((60, 80, 3), 128, np.uint8)


def test_save_names_by_time_and_reason(tmp_path):
    p = intruders.save(shot(), tmp_path, now=datetime(2026, 10, 16, 13, 5, 9), reason="timeout")
    assert p.name == "2026-10-16_13-05-09_timeout.jpg"
    assert p.read_bytes()[:2] == b"\xff\xd8"  # a JPEG


def test_same_second_does_not_overwrite(tmp_path):
    now = datetime(2026, 10, 16, 13, 5, 9)
    a = intruders.save(shot(), tmp_path, now=now)
    b = intruders.save(shot(), tmp_path, now=now)
    assert a != b and a.exists() and b.exists()


def test_only_the_newest_are_kept(tmp_path):
    start = datetime(2026, 10, 16, 12, 0, 0)
    for i in range(7):
        intruders.save(shot(), tmp_path, keep=5, now=start + timedelta(seconds=i))
    names = [p.name for p in intruders.photos(tmp_path)]
    assert len(names) == 5 and names[0].startswith("2026-10-16_12-00-02")


def test_clear(tmp_path):
    intruders.save(shot(), tmp_path)
    assert intruders.clear(tmp_path) == 1 and intruders.photos(tmp_path) == []
    assert intruders.photos(tmp_path / "missing") == []
