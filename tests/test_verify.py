import numpy as np
import pytest

from conftest import FakeClock, ScriptedEngine, make_face, near, unit
from facegate.config import MatchConfig
from facegate.verify import EXIT_CAMERA, EXIT_NO_MATCH, EXIT_OK, EnrollError, enroll, verify

ME = unit(1)
TEMPLATES = np.stack([near(ME, 0.05, s) for s in range(8)])
BLANK = np.zeros((480, 640, 3), np.uint8)


def mine(i, nose=0.0, width=150):
    return ([make_face(width, nose)], near(ME, 0.05, 100 + i))


def stranger(i, nose=0.0):
    return ([make_face(150, nose)], unit(500 + i))


def run(script, cfg=None, **kw):
    return verify(lambda: BLANK, ScriptedEngine(script), TEMPLATES, cfg or MatchConfig(), clock=FakeClock(), **kw)


def test_match_with_head_turn():
    res = run([mine(0, -0.1), mine(1, 0.0), mine(2, 0.1)])
    assert res.ok and res.reason == "match" and res.exit_code == EXIT_OK
    assert res.turn_range == pytest.approx(0.2, abs=1e-3)


def test_still_face_needs_a_turn_when_liveness_is_on():
    res = run([mine(i) for i in range(200)])
    assert not res.ok and res.reason == "timeout" and res.exit_code == EXIT_NO_MATCH
    assert res.matched_frames > 3


def test_still_face_is_enough_with_liveness_off():
    res = run([mine(i) for i in range(5)], MatchConfig(liveness=False))
    assert res.ok and res.matched_frames == 3


def test_stranger_never_matches():
    res = run([stranger(i, (-1) ** i * 0.2) for i in range(200)])
    assert not res.ok and res.matched_frames == 0 and res.best_score < 0.42


def test_turn_by_a_different_face_does_not_count():
    # A photo of me held still while a stranger turns their head.
    script = []
    for i in range(100):
        script.append(mine(i, 0.0))
        script.append(stranger(i, 0.3 if i % 2 else -0.3))
    res = run(script)
    assert not res.ok and res.turn_range == 0.0


def test_far_away_face_is_ignored():
    res = run([mine(i, (-1) ** i * 0.2, width=40) for i in range(200)])
    assert not res.ok and res.matched_frames == 0


def test_dead_camera():
    res = verify(lambda: None, ScriptedEngine([mine(0)]), TEMPLATES, MatchConfig(), clock=FakeClock(0.001))
    assert res.reason == "camera" and res.exit_code == EXIT_CAMERA


def test_cancel_from_preview():
    seen = []

    def on_frame(frame, faces, status):
        seen.append(status)
        return len(seen) < 2

    res = run([([], None)] * 50, on_frame=on_frame)
    assert res.reason == "cancelled" and seen == ["Look at the camera"] * 2


def test_status_messages():
    statuses = []
    run(
        [([], None), mine(0, width=30), mine(1), mine(2)] + [mine(3)] * 200,
        on_frame=lambda f, faces, s: statuses.append(s) or True,
    )
    assert statuses[:3] == ["Look at the camera", "Come a little closer", "Turn your head slightly"]


def test_enroll_collects_samples():
    eng = ScriptedEngine([mine(i, 0.05 * (i % 3)) for i in range(50)])
    emb = enroll(lambda: BLANK, eng, samples=6, gap=0.0, clock=FakeClock())
    assert emb.shape == (6, 128) and emb.dtype == np.float32
    assert np.allclose(np.linalg.norm(emb, axis=1), 1.0, atol=1e-5)


def test_enroll_refuses_two_faces_in_view():
    two = ([make_face(), make_face()], ME)
    with pytest.raises(EnrollError):
        enroll(lambda: BLANK, ScriptedEngine([two] * 1000), samples=3, timeout=5, gap=0.0, clock=FakeClock())


def test_enroll_refuses_mixed_people():
    with pytest.raises(EnrollError, match="one person"):
        enroll(lambda: BLANK, ScriptedEngine([stranger(i) for i in range(20)]), samples=6, gap=0.0, clock=FakeClock())
