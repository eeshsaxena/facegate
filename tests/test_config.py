import pytest

from facegate.config import DEFAULT_CONFIG_TOML, Config, load_config, parse_config


def test_defaults_match_the_template():
    assert parse_config(DEFAULT_CONFIG_TOML) == Config()


def test_partial_override_keeps_other_defaults():
    cfg = parse_config("camera_index = 1\n[match]\nthreshold = 0.5\n")
    assert cfg.camera_index == 1
    assert cfg.match.threshold == 0.5
    assert cfg.match.frames_required == Config().match.frames_required


def test_int_is_accepted_for_float():
    cfg = parse_config("[whatsapp]\nrelock_after = 60\n")
    assert cfg.whatsapp.relock_after == 60.0 and isinstance(cfg.whatsapp.relock_after, float)


@pytest.mark.parametrize(
    "text",
    [
        "nonsense = 1",
        "[match]\nthreshhold = 0.5",
        "[match]\nliveness = 1",
        "[match]\nthreshold = true",
        "[match]\nthreshold = 'high'",
        "[whatsapp]\nprocesses = 'WhatsApp.exe'",
        "match = 3",
    ],
)
def test_bad_settings_are_rejected(text):
    with pytest.raises(ValueError):
        parse_config(text)


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()
