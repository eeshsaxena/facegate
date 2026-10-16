import sys

from facegate import agent


class Runs:
    def __init__(self, codes):
        self.codes = list(codes)
        self.calls = 0

    def __call__(self, cmd):
        self.calls += 1
        return self.codes.pop(0)


def test_clean_exit_is_not_restarted():
    runs = Runs([0])
    assert agent.supervise(["x"], runner=runs, sleep=lambda s: None) == 0
    assert runs.calls == 1


def test_crash_is_restarted_until_a_clean_stop():
    runs, naps = Runs([1, 3221225477, 0]), []
    assert agent.supervise(["x"], runner=runs, sleep=naps.append) == 0
    assert runs.calls == 3 and naps == [3.0, 3.0]


def test_setup_problems_are_final():
    for code in (agent.EXIT_NOT_ENROLLED, agent.EXIT_SETUP, agent.EXIT_ALREADY_RUNNING):
        runs = Runs([code])
        assert agent.supervise(["x"], runner=runs, sleep=lambda s: None) == code
        assert runs.calls == 1


def test_gives_up_on_a_crash_loop():
    t = [0.0]

    def clock():
        t[0] += 1
        return t[0]

    runs = Runs([1] * 100)
    assert agent.supervise(["x"], runner=runs, sleep=lambda s: None, clock=clock, max_restarts=5) == 1
    assert runs.calls == 6


def test_old_crashes_are_forgotten():
    t = [0.0]

    def clock():
        t[0] += 400  # each crash far apart
        return t[0]

    runs = Runs([1] * 20 + [0])
    assert agent.supervise(["x"], runner=runs, sleep=lambda s: None, clock=clock, max_restarts=5) == 0


def test_child_command_puts_verbose_before_the_subcommand():
    cmd = agent.child_command(["--presence"], verbose=True)
    assert cmd[:4] == [sys.executable, "-m", "facegate", "-v"]
    assert cmd[4:] == ["agent", "run", "--no-supervise", "--presence"]


class FakeProc:
    def __init__(self, polls_before_exit, code=0, interrupt_at=None):
        self.left = polls_before_exit
        self.code = code
        self.interrupt_at = interrupt_at
        self.returncode = None
        self.waited = self.killed = False
        self.polls = 0

    def poll(self):
        self.polls += 1
        if self.interrupt_at is not None and self.polls == self.interrupt_at:
            raise KeyboardInterrupt
        if self.left <= 0:
            self.returncode = self.code
            return self.code
        self.left -= 1
        return None

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def kill(self):
        self.killed = True


def test_run_child_waits_for_exit_code():
    proc = FakeProc(3, code=7)
    assert agent.run_child(["x"], popen=lambda *a, **k: proc, sleep=lambda s: None) == 7
    assert proc.polls == 4


def test_ctrl_c_stops_the_agent_cleanly(monkeypatch):
    stops = []
    monkeypatch.setattr(agent, "request_stop", lambda: stops.append(1) or True)
    proc = FakeProc(100, interrupt_at=2)
    assert agent.run_child(["x"], popen=lambda *a, **k: proc, sleep=lambda s: None) == agent.EXIT_OK
    assert stops == [1] and proc.waited and not proc.killed


def test_child_gets_no_console_handles():
    seen = {}

    def popen(cmd, **kw):
        seen.update(kw)
        return FakeProc(0)

    agent.run_child(["x"], popen=popen, sleep=lambda s: None)
    import subprocess

    assert seen["stdin"] == seen["stdout"] == seen["stderr"] == subprocess.DEVNULL


class FakeCamera:
    def __init__(self):
        self.is_open = True
        self.closed = 0

    def close(self):
        self.is_open = False
        self.closed += 1

    def read(self):
        return None


def checker(**whatsapp):
    from facegate.config import Config

    cfg = Config()
    for k, v in whatsapp.items():
        setattr(cfg.whatsapp, k, v)
    c = agent.FaceChecker(cfg, engine=None, templates=None, show_preview=False)
    c.camera = FakeCamera()
    return c


def test_camera_stays_warm_then_turns_off():
    c = checker(camera_keep_open=15)
    c.last_used = 100.0
    c.release_if_idle(110.0)
    assert c.camera.is_open
    c.release_if_idle(115.5)
    assert not c.camera.is_open and c.camera.closed == 1


def test_camera_is_left_alone_for_the_walk_away_lock():
    c = checker(camera_keep_open=1)
    c.cfg.presence.enabled = True
    c.release_if_idle(1e9)
    assert c.camera.is_open


def test_recheck_maps_results(monkeypatch, tmp_path):
    import numpy as np

    from facegate import intruders
    from facegate.verify import VerifyResult
    from facegate.whatsapp import ABSENT, CAMERA, MATCH, STRANGER

    saved = []
    monkeypatch.setattr(intruders, "save", lambda frame, **kw: saved.append(kw["reason"]) or tmp_path / "x.jpg")
    c = checker()
    c.camera.flush = lambda: None
    shot = np.zeros((2, 2, 3), np.uint8)
    cases = [
        (VerifyResult(True, "match", 0.8, 2), MATCH),
        (VerifyResult(False, "timeout", -1.0, faces_seen=0, evidence=shot), ABSENT),
        (VerifyResult(False, "timeout", 0.1, faces_seen=12, evidence=shot), STRANGER),
        (VerifyResult(False, "camera"), CAMERA),
    ]
    seen_cfg = []
    for result, expected in cases:
        monkeypatch.setattr(agent, "verify", lambda read, eng, tpl, cfg, r=result: seen_cfg.append(cfg) or r)
        assert c.recheck() == expected
    assert saved == ["stranger-recheck"]  # only the stranger is photographed
    assert all(not cfg.liveness and cfg.timeout == 6.0 for cfg in seen_cfg)
