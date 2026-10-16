import numpy as np

from conftest import near, unit
from facegate.engine import match_score, normalize


def test_normalize_unit_length_and_zero_safe():
    assert np.isclose(np.linalg.norm(normalize(np.arange(1, 129))), 1.0)
    assert not np.any(np.isnan(normalize(np.zeros(128))))


def test_same_person_scores_higher_than_stranger():
    me = unit(1)
    templates = np.stack([near(me, 0.03, s) for s in range(10)])
    assert match_score(near(me, 0.03, 99), templates) > 0.85
    assert abs(match_score(unit(2), templates)) < 0.3


def test_one_lucky_template_is_not_enough():
    stranger = unit(5)
    templates = np.stack([stranger] + [unit(10 + s) for s in range(9)])
    # top-1 would be 1.0; the top-3 average is far below any threshold
    assert match_score(stranger, templates) < 0.42


def test_empty_templates():
    assert match_score(unit(1), np.zeros((0, 128), np.float32)) == -1.0
