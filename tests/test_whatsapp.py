from dataclasses import dataclass

from facegate.config import WhatsAppConfig
from facegate.whatsapp import CAMERA, CANCELLED, MATCH, NO_MATCH, WhatsAppGuard

WA, WA2, WA_POPUP, WA_HIDDEN, WA_TRAY, CHROME, CODE = 101, 102, 103, 104, 105, 202, 303


@dataclass
class Win:
    name: str
    title: str = ""
    visible: bool = True
    minimized: bool = False
    owner: int = 0
    tool: bool = False


class FakeWindows:
    def __init__(self):
        self.fg = CODE
        self.locked_session = False
        self.wins = {
            WA: Win("WhatsApp.Root.exe", "WhatsApp", visible=False),  # in the tray
            CHROME: Win("chrome.exe", "WhatsApp - Google Chrome"),
            CODE: Win("Code.exe", "editor"),
        }
        self.calls = []
        self.stubborn = 0  # how many restores of a window get ignored

    def foreground(self):
        return self.fg

    def top_windows(self):
        return list(self.wins)

    def process_name(self, h):
        return self.wins[h].name if h in self.wins else ""

    def title(self, h):
        return self.wins[h].title if h in self.wins else ""

    def is_app_window(self, h):
        w = self.wins.get(h)
        return bool(w) and w.visible and not w.owner and not w.tool

    def is_window(self, h):
        return h in self.wins

    def is_minimized(self, h):
        return self.wins[h].minimized

    def describe(self, h):
        return str(self.wins.get(h))

    def session_locked(self):
        return self.locked_session

    def minimize(self, h):
        # Like real Windows, the minimised window can keep the focus.
        self.calls.append(("min", h))
        self.wins[h].minimized = True

    def restore(self, h):
        self.calls.append(("restore", h))
        if self.stubborn:
            self.stubborn -= 1
            return
        self.wins[h].minimized = False
        self.wins[h].visible = True
        self.fg = h

    def bring_up(self, h):
        """What clicking the taskbar button or tray icon does."""
        self.wins[h].minimized = False
        self.wins[h].visible = True
        self.fg = h

    def show_behind(self, h):
        """Opens without becoming the active window."""
        self.wins[h].minimized = False
        self.wins[h].visible = True


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

    g = WhatsAppGuard(WhatsAppConfig(**cfg), api, verify, clock, notify=notes.append, sleep=lambda s: None)
    return g, api, clock, asked, notes


def visible(api, h):
    return api.wins[h].visible and not api.wins[h].minimized


def test_tray_menu_in_front_does_not_trigger_a_check():
    g, api, _, asked, _ = guard([MATCH])
    api.wins[WA_TRAY] = Win("WhatsApp.Root.exe", "H.NotifyIcon", tool=True)
    api.fg = WA_TRAY
    g.tick()
    assert asked == [] and ("min", WA_TRAY) not in api.calls


def test_unlock_brings_back_every_window_it_hid_with_the_opened_one_on_top():
    g, api, _, asked, _ = guard([MATCH])
    api.wins[WA2] = Win("WhatsApp.Root.exe", "Chat - Mom")
    api.bring_up(WA)
    g.tick()  # both hidden, face checked, both back
    assert asked == [0.0]
    assert visible(api, WA) and visible(api, WA2)
    assert api.calls[-1] == ("restore", WA) and api.fg == WA


def test_restore_is_retried_until_it_sticks():
    g, api, _, _, _ = guard([MATCH])
    api.bring_up(WA)
    api.stubborn = 2  # WhatsApp ignores the first two restores
    g.tick()
    assert visible(api, WA) and api.calls.count(("restore", WA)) == 3


def test_window_hidden_to_tray_comes_back():
    g, api, _, _, _ = guard([MATCH])
    api.bring_up(WA)

    def hide_to_tray(h):  # some apps hide instead of minimising
        api.calls.append(("min", h))
        api.wins[h].visible = False
        api.fg = 0

    api.minimize = hide_to_tray
    g.tick()
    assert visible(api, WA) and not g.locked


def test_camera_window_closes_only_after_whatsapp_is_back():
    events = []
    g, api, _, _, _ = guard([MATCH])
    real_restore = api.restore
    api.restore = lambda h: (events.append("restore"), real_restore(h))
    g.on_unlocked = lambda: events.append("close camera window")
    api.bring_up(WA)
    g.tick()
    assert events == ["restore", "close camera window"]


def test_default_asks_every_time_it_comes_back_to_the_front():
    assert WhatsAppConfig().relock_after == 1.0


def test_switching_away_and_back_asks_for_face_again():
    g, api, clock, asked, _ = guard([MATCH, MATCH])
    api.bring_up(WA)
    g.tick()
    assert not g.locked
    clock.t, api.fg = 5, CODE  # you click your editor
    g.tick()
    clock.t = 6.1
    g.tick()
    assert g.locked and not visible(api, WA)
    clock.t = 8
    api.bring_up(WA)  # and come back to WhatsApp
    g.tick()
    assert asked == [0.0, 8] and visible(api, WA)


def test_a_quick_focus_flicker_does_not_lock():
    g, api, clock, asked, _ = guard([MATCH])
    api.bring_up(WA)
    g.tick()
    clock.t, api.fg = 3, CODE
    g.tick()
    clock.t, api.fg = 3.5, WA  # back within a second
    g.tick()
    clock.t = 10
    g.tick()
    assert not g.locked and asked == [0.0]


def test_whatsapp_dialogs_keep_it_unlocked():
    g, api, clock, asked, _ = guard([MATCH])
    api.wins[WA_POPUP] = Win("WhatsApp.Root.exe", "Open", owner=WA)  # file picker
    api.bring_up(WA)
    g.tick()
    clock.t, api.fg = 2, WA_POPUP
    g.tick()
    clock.t = 30
    g.tick()
    assert not g.locked and visible(api, WA)


def test_unfocused_restore_gets_a_grace_period_then_locks():
    g, api, clock, asked, _ = guard([MATCH], focus_grace=10)
    real_restore = api.restore

    def restore_without_focus(h):
        real_restore(h)
        api.fg = CODE  # Windows refused to hand over the focus

    api.restore = restore_without_focus
    api.bring_up(WA)
    g.tick()
    clock.t = 5
    g.tick()
    assert not g.locked and visible(api, WA)
    clock.t = 10.5
    g.tick()
    assert g.locked and not visible(api, WA)


def test_whatsapp_already_on_screen_at_start_is_hidden_and_checked():
    g, api, _, asked, _ = guard([NO_MATCH])
    api.show_behind(WA)  # open behind the editor when the agent starts
    g.tick()
    assert asked == [0.0] and not visible(api, WA) and visible(api, CODE)


def test_whatsapp_opening_without_focus_still_asks_for_face():
    g, api, _, asked, _ = guard([MATCH])
    g.tick()
    api.show_behind(WA)  # Windows didn't activate it
    g.tick()
    assert asked == [0.0] and visible(api, WA) and not g.locked


def test_opening_whatsapp_asks_for_face_and_unlocks():
    g, api, clock, asked, _ = guard([MATCH])
    g.tick()
    api.bring_up(WA)
    g.tick()
    assert asked == [0.0]
    assert api.calls[-1] == ("restore", WA) and visible(api, WA)
    assert not g.locked


def test_relock_after_can_be_made_longer():
    g, api, clock, asked, _ = guard([MATCH, MATCH], relock_after=30)
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
    api.show_behind(WA)
    assert len(g.sweep()) == 2
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
    clock.t = 1002
    g.tick()
    assert g.locked


def test_process_names_are_case_insensitive():
    g, api, _, _, _ = guard([], processes=["whatsapp.root.EXE"])
    assert g.is_whatsapp(WA) and not g.is_whatsapp(CODE) and not g.is_whatsapp(0)


def test_after_a_failed_check_clicking_whatsapp_asks_again_even_if_it_kept_focus():
    # Regression: Windows left the focus on the minimised window, which looked
    # like "still in front", so FaceGate never re-armed and WhatsApp stayed hidden.
    g, api, clock, asked, _ = guard([NO_MATCH, MATCH])
    api.bring_up(WA)
    g.tick()
    assert api.fg == WA and api.wins[WA].minimized  # minimised but focused
    for t in (1, 2, 3):
        clock.t = t
        g.tick()
    assert len(asked) == 1
    api.bring_up(WA)  # click it in the taskbar
    g.tick()
    assert len(asked) == 2 and visible(api, WA) and not g.locked


def test_minimising_whatsapp_yourself_locks_it():
    g, api, clock, asked, _ = guard([MATCH, MATCH])
    api.bring_up(WA)
    g.tick()
    api.wins[WA].minimized = True  # you hit "_"; the focus stays on it
    clock.t = 5
    g.tick()
    clock.t = 6.5
    g.tick()
    assert g.locked
    api.bring_up(WA)
    g.tick()
    assert len(asked) == 2
