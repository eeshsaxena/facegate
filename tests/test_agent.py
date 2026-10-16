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
