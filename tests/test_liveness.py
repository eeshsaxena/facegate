import numpy as np

from facegate.liveness import TurnChallenge, nose_offset

# A rough 3D face (x right, y down, z toward camera), in cm.
HEAD = np.array(
    [
        [-3.2, 0.0, 0.0],  # right eye
        [3.2, 0.0, 0.0],  # left eye
        [0.0, 3.5, 2.5],  # nose tip sticks out
        [-2.5, 6.5, 0.5],
        [2.5, 6.5, 0.5],
    ]
)


def project(points, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0, flat=False):
    pts = points.copy()
    if flat:
        pts[:, 2] = 0.0  # a printed photo of the face
    a, b, c = np.radians([yaw_deg, pitch_deg, roll_deg])
    ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
    rz = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    p = pts @ (rz @ rx @ ry).T
    return p[:, :2] * 20 + 300  # orthographic camera


def test_frontal_face_is_centred():
    assert abs(nose_offset(project(HEAD))) < 1e-9


def test_real_head_turn_moves_the_nose():
    ch = TurnChallenge(0.12)
    for yaw in (-15, -5, 0, 5, 15):
        ch.feed(project(HEAD, yaw))
    assert ch.passed


def test_tilting_a_photo_does_not_pass():
    ch = TurnChallenge(0.12)
    for yaw in range(-50, 51, 10):
        for pitch in range(-40, 41, 10):
            for roll in (-20, 0, 20):
                ch.feed(project(HEAD, yaw, pitch, roll, flat=True))
    assert not ch.passed
    assert ch.range < 1e-6  # exactly invariant for a flat face


def test_roll_alone_does_not_count_as_a_turn():
    ch = TurnChallenge(0.12)
    for roll in (-30, 0, 30):
        ch.feed(project(HEAD, roll_deg=roll))
    assert not ch.passed


def test_degenerate_landmarks():
    assert nose_offset(np.zeros((5, 2))) == 0.0
    assert TurnChallenge().range == 0.0
