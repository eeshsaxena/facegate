from facegate.config import WhatsAppConfig
from facegate.whatsapp import WhatsAppGuard

WA, CHROME, CODE = 101, 202, 303


class FakeWindows:
    def __init__(self):
        self.fg = CODE
        self.locked_session = False
        self.names = {WA: "WhatsApp.Root.exe", CHROME: "chrome.exe", CODE: "Code.exe"}
        self.titles = {WA: "WhatsApp", CHROME: "WhatsApp - Google Chrome", CODE: "x"}
        self.alive = {WA, CHROME, CODE}
        self.calls = []

    def foreground(self):
        return self.fg

    def process_name(self, h):
        return self.names.get(h, "")

    def title(self, h):
        return self.titles.get(h, "")

    def is_window(self, h):
        return h in self.alive

    def session_locked(self):
        return self.locked_session

    def minimize(self, h):
        self.calls.append(("min", h))
        if self.fg == h:
            self.fg = 0

    def restore(self, h):
        self.calls.append(("restore", h))
        self.fg = h


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def guard(results, **cfg):
    api, clock, answers = FakeWindows(), Clock(), list(results)
    asked = []

    def verify():
        asked.append(clock.t)
        return answers.pop(0)

    return WhatsAppGuard(WhatsAppConfig(**cfg), api, verify, clock), api, clock, asked


def test_opening_whatsapp_asks_for_face_and_unlocks():
    g, api, clock, asked = guard([True])
    g.tick()
    assert asked == []
    api.fg = WA
    g.tick()
    assert asked == [0.0]
    assert api.calls == [("min", WA), ("restore", WA)]
    assert not g.locked


def test_stays_unlocked_while_in_use_and_relocks_after_idle():
    g, api, clock, asked = guard([True, True], relock_after=60)
    api.fg = WA
    g.tick()
    clock.t, api.fg = 30, CODE
    g.tick()
    clock.t, api.fg = 80, WA  # back within the window: no prompt
    g.tick()
    assert len(asked) == 1 and not g.locked
    clock.t, api.fg = 100, CODE
    g.tick()
    clock.t = 139  # 59 s since WhatsApp was last in front (t=80)
    g.tick()
    assert not g.locked
    clock.t = 141
    g.tick()
    assert g.locked and api.calls[-1] == ("min", WA)  # hidden on relock
    api.fg = WA
    g.tick()
    assert len(asked) == 2


def test_failed_face_keeps_it_locked_and_waits_for_reopen():
    g, api, clock, asked = guard([False, True])
    api.fg = WA
    g.tick()
    assert g.locked and api.calls == [("min", WA)]
    api.fg = WA  # something brings it straight back: no new prompt yet
    g.tick()
    assert len(asked) == 1
    api.fg = CODE
    g.tick()
    api.fg = WA
    g.tick()
    assert len(asked) == 2 and not g.locked


def test_windows_lock_relocks_immediately():
    g, api, clock, asked = guard([True])
    api.fg = WA
    g.tick()
    api.locked_session = True
    g.tick()
    assert g.locked and api.calls[-1] == ("min", WA)


def test_browser_tab_only_when_configured():
    g, api, _, asked = guard([True])
    api.fg = CHROME
    g.tick()
    assert asked == []
    g, api, _, asked = guard([True], window_titles=["WhatsApp"])
    api.fg = CHROME
    g.tick()
    assert asked == [0.0]


def test_closed_windows_are_forgotten():
    g, api, clock, _ = guard([True])
    api.fg = WA
    g.tick()
    api.alive.discard(WA)
    api.fg, clock.t = CODE, 1000
    g.tick()
    assert g.locked and g.known == set()
    assert ("min", WA) not in api.calls[2:]


def test_process_names_are_case_insensitive():
    g, api, _, _ = guard([True], processes=["whatsapp.root.EXE"])
    assert g.is_whatsapp(WA) and not g.is_whatsapp(CODE) and not g.is_whatsapp(0)
