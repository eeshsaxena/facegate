from dataclasses import dataclass

from facegate.config import WhatsAppConfig
from facegate.whatsapp import CAMERA, CANCELLED, MATCH, NO_MATCH, WhatsAppGuard

WA, WA2, WA_POPUP, WA_HIDDEN, CHROME, CODE = 101, 102, 103, 104, 202, 303


@dataclass
class Win:
    name: str
    title: str = ""
    visible: bool = True
    minimized: bool = False
    owner: int = 0


class FakeWindows:
    def __init__(self):
        self.fg = CODE
        self.locked_session = False
        self.wins = {
            WA: Win("WhatsApp.Root.exe", "WhatsApp"),
            CHROME: Win("chrome.exe", "WhatsApp - Google Chrome"),
            CODE: Win("Code.exe", "editor"),
        }
        self.calls = []

    def foreground(self):
        return self.fg

    def top_windows(self):
        return list(self.wins)

    def process_name(self, h):
        return self.wins[h].name if h in self.wins else ""

    def title(self, h):
        return self.wins[h].title if h in self.wins else ""

    def is_visible(self, h):
        return self.wins[h].visible

    def is_minimized(self, h):
        return self.wins[h].minimized

    def owner(self, h):
        return self.wins[h].owner

    def session_locked(self):
        return self.locked_session

    def minimize(self, h):
        self.calls.append(("min", h))
        self.wins[h].minimized = True
        if self.fg == h:
            self.fg = 0

    def restore(self, h):
        self.calls.append(("restore", h))
        self.wins[h].minimized = False
        self.fg = h

    def bring_up(self, h):
        """What clicking the taskbar button does."""
        self.wins[h].minimized = False
        self.fg = h


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def guard(results, **cfg):
    api, clock, answers = FakeWindows(), Clock(), list(results)
    asked, notes = [], []

    def verify():
        asked.append(clock.t)
        return answers.pop(0)

    g = WhatsAppGuard(WhatsAppConfig(**cfg), api, verify, clock, notify=notes.append)
    return g, api, clock, asked, notes


def visible(api, h):
    return api.wins[h].visible and not api.wins[h].minimized


def test_default_relock_is_30_seconds():
    assert WhatsAppConfig().relock_after == 30.0


def test_starts_locked_and_hides_whatsapp_already_on_screen():
    g, api, _, asked, _ = guard([])
    g.tick()  # WhatsApp is open behind the editor
    assert not visible(api, WA) and visible(api, CODE) and asked == []


def test_opening_whatsapp_asks_for_face_and_unlocks():
    g, api, clock, asked, _ = guard([MATCH])
    g.tick()
    api.bring_up(WA)
    g.tick()
    assert asked == [0.0]
    assert api.calls[-1] == ("restore", WA) and visible(api, WA)
    assert not g.locked


def test_stays_unlocked_while_in_use_and_relocks_after_30s_in_background():
    g, api, clock, asked, _ = guard([MATCH, MATCH])
    api.bring_up(WA)
    g.tick()
    clock.t, api.fg = 10, CODE
    g.tick()
    clock.t, api.fg = 25, WA  # back within 30 s: no prompt
    g.tick()
    assert len(asked) == 1 and not g.locked
    clock.t, api.fg = 26, CODE
    g.tick()
    clock.t = 54  # 29 s since WhatsApp was last in front
    g.tick()
    assert not g.locked and visible(api, WA)
    clock.t = 56
    g.tick()
    assert g.locked and not visible(api, WA)  # hidden on relock
    api.bring_up(WA)
    g.tick()
    assert len(asked) == 2


def test_failed_face_keeps_it_hidden_and_waits_for_reopen():
    g, api, clock, asked, _ = guard([NO_MATCH, MATCH])
    api.bring_up(WA)
    g.tick()
    assert g.locked and not visible(api, WA)
    api.bring_up(WA)  # something brings it straight back: hidden, no new prompt
    g.tick()
    assert len(asked) == 1 and not visible(api, WA)
    g.tick()  # WhatsApp no longer in front: re-armed
    api.bring_up(WA)
    g.tick()
    assert len(asked) == 2 and not g.locked


def test_cooldown_after_three_wrong_faces():
    g, api, clock, asked, notes = guard([NO_MATCH] * 3 + [MATCH], max_failures=3, cooldown=30)
    for i in range(3):
        clock.t = i
        api.bring_up(WA)
        g.tick()
        g.tick()
    assert len(asked) == 3
    clock.t = 10
    api.bring_up(WA)
    g.tick()
    assert len(asked) == 3 and not visible(api, WA)
    assert notes == ["Too many tries. Try again in 23 s"]
    g.tick()
    clock.t = 33
    api.bring_up(WA)
    g.tick()
    assert len(asked) == 4 and not g.locked


def test_cancel_and_camera_trouble_do_not_count_toward_cooldown():
    g, api, clock, asked, notes = guard([CANCELLED, CAMERA, CANCELLED, MATCH], max_failures=2)
    for _ in range(4):
        api.bring_up(WA)
        g.tick()
        g.tick()
    assert len(asked) == 4 and notes == [] and not g.locked


def test_clicking_whatsapp_during_the_check_is_undone():
    g, api, clock, asked, _ = guard([])

    def verify():
        asked.append(1)
        api.bring_up(WA)  # user clicks it again while the camera runs
        return NO_MATCH

    g.verify = verify
    api.bring_up(WA)
    g.tick()
    assert asked == [1] and not visible(api, WA)


def test_windows_lock_relocks_immediately():
    g, api, clock, asked, _ = guard([MATCH])
    api.bring_up(WA)
    g.tick()
    api.locked_session = True
    g.tick()
    assert g.locked and not visible(api, WA)


def test_every_whatsapp_window_is_hidden_but_not_popups_or_hidden_ones():
    g, api, _, _, _ = guard([])
    api.wins[WA2] = Win("WhatsApp.Root.exe", "Chat - Mom")  # popped-out chat window
    api.wins[WA_POPUP] = Win("WhatsApp.Root.exe", "Save as", owner=WA)
    api.wins[WA_HIDDEN] = Win("WhatsApp.Root.exe", "", visible=False)
    assert g.sweep() == 2
    minimized = {h for op, h in api.calls if op == "min"}
    assert minimized == {WA, WA2}


def test_browser_tab_only_when_configured():
    g, api, _, asked, _ = guard([MATCH])
    api.bring_up(CHROME)
    g.tick()
    assert asked == [] and visible(api, CHROME)
    g, api, _, asked, _ = guard([MATCH], window_titles=["WhatsApp"])
    api.bring_up(CHROME)
    g.tick()
    assert asked == [0.0]


def test_closed_whatsapp_is_fine():
    g, api, clock, _, _ = guard([MATCH])
    api.bring_up(WA)
    g.tick()
    del api.wins[WA]
    api.fg, clock.t = CODE, 1000
    g.tick()
    assert g.locked


def test_process_names_are_case_insensitive():
    g, api, _, _, _ = guard([], processes=["whatsapp.root.EXE"])
    assert g.is_whatsapp(WA) and not g.is_whatsapp(CODE) and not g.is_whatsapp(0)
