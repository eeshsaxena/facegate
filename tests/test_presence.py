from facegate.config import PresenceConfig
from facegate.presence import PresenceGuard


class World:
    def __init__(self):
        self.t = 0.0
        self.present = True
        self.locked = False
        self.locks = 0
        self.releases = 0

    def clock(self):
        return self.t

    def sense(self):
        return self.present

    def lock(self):
        self.locks += 1
        self.locked = True

    def session_locked(self):
        return self.locked

    def release(self):
        self.releases += 1


def make(absent=30):
    w = World()
    cfg = PresenceConfig(enabled=True, absent_seconds=absent)
    return PresenceGuard(cfg, w.sense, w.lock, w.session_locked, w.release, w.clock), w


def at(g, w, t):
    w.t = t
    g.tick()


def test_locks_after_absence():
    g, w = make()
    w.present = False
    for t in range(0, 30, 2):
        at(g, w, t)
    assert w.locks == 0
    at(g, w, 31)
    assert w.locks == 1


def test_coming_back_resets_the_timer():
    g, w = make()
    w.present = False
    at(g, w, 25)
    w.present = True
    at(g, w, 26)
    w.present = False
    at(g, w, 50)
    assert w.locks == 0


def test_camera_glitch_never_locks():
    g, w = make()
    w.present = None
    at(g, w, 999)
    assert w.locks == 0


def test_pauses_and_frees_camera_while_locked_then_gives_grace():
    g, w = make()
    w.present = False
    at(g, w, 40)
    assert w.locks == 1 and w.releases == 0
    at(g, w, 42)
    at(g, w, 100)
    assert w.releases == 1 and w.locks == 1
    w.locked = False
    at(g, w, 500)  # just signed back in, nobody detected yet
    assert w.locks == 1
    at(g, w, 531)
    assert w.locks == 2
