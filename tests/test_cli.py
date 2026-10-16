import pytest

from facegate import cli
from facegate.config import Config, load_config


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path / "FaceGate"


def test_config_writes_defaults_then_validates(home, capsys):
    assert cli.main(["config"]) == 0
    assert (home / "config.toml").exists()
    assert load_config(home / "config.toml") == Config()
    assert cli.main(["config"]) == 0
    assert "valid" in capsys.readouterr().out


def test_bad_config_exits_cleanly(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text("[match]\nthreshold = 'x'\n")
    assert cli.main(["config"]) == 4


def test_agent_stop_when_not_running(home):
    assert cli.main(["agent", "stop"]) == 1


def test_autostart_command_uses_windowless_python():
    cmd = cli._agent_command()
    assert cmd.endswith("-m facegate agent") and cmd.startswith('"')


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_stop_when_not_running(home, capsys):
    assert cli.main(["stop"]) == 0
    assert "not running" in capsys.readouterr().out


def test_start_refuses_without_enrollment(home, capsys, monkeypatch):
    from facegate import agent

    monkeypatch.setattr(agent, "is_running", lambda: False)
    launched = []
    monkeypatch.setattr(agent, "start_detached", lambda *a, **k: launched.append(a))
    assert cli.main(["start"]) == agent.EXIT_NOT_ENROLLED
    assert launched == [] and "enroll" in capsys.readouterr().out


def test_status_runs(home, capsys):
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "running:" in out and "enrolled:   no" in out


def test_intruders_command(home, capsys):
    import numpy as np

    from facegate import intruders

    assert cli.main(["intruders"]) == 0
    assert "no failed attempts" in capsys.readouterr().out
    intruders.save(np.zeros((10, 10, 3), np.uint8))
    assert cli.main(["intruders"]) == 0
    assert "1 photo(s)" in capsys.readouterr().out
    assert cli.main(["intruders", "--clear"]) == 0
    assert intruders.photos() == []
