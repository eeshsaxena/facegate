"""Face lock for WhatsApp Desktop.

While locked:
  * every visible WhatsApp window is kept minimised (checked on every window
    change, through a hook, plus a once-a-second backup sweep);
  * bringing WhatsApp to the front starts a face check; if it's you, the
    window comes back.

Once unlocked it stays open while you use it, including its own dialogs
(file picker, menus).  As soon as you switch to something else it locks
again (after ``relock_after`` seconds, 1 s by default) and is minimised, so
every time WhatsApp opens or comes back to the front, your face is checked.
Locking Windows locks it too.

It fails closed: the agent starts locked, so if it is restarted WhatsApp is
hidden again until your face is checked.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Protocol

from facegate.config import WhatsAppConfig

log = logging.getLogger("facegate.whatsapp")

# What a face check can come back with (see verify.VerifyResult.reason).
MATCH, NO_MATCH, CANCELLED, CAMERA = "match", "timeout", "cancelled", "camera"
# Extra outcomes of the quiet background re-check.
ABSENT, STRANGER = "absent", "stranger"


class WindowApi(Protocol):
    def foreground(self) -> int: ...
    def top_windows(self) -> list[int]: ...
    def process_name(self, hwnd: int) -> str: ...
    def title(self, hwnd: int) -> str: ...
    def is_app_window(self, hwnd: int) -> bool: ...
    def is_window(self, hwnd: int) -> bool: ...
    def is_minimized(self, hwnd: int) -> bool: ...
    def minimize(self, hwnd: int) -> None: ...
    def restore(self, hwnd: int) -> None: ...
    def session_locked(self) -> bool: ...
    def describe(self, hwnd: int) -> str: ...
    def close_app(self, process_names: list[str]) -> int: ...


class Win32Windows:
    def __init__(self, cache_seconds: float = 60.0):
        from facegate import winapi

        self._w = winapi
        self._names: dict[int, str] = {}
        self._cache_seconds = cache_seconds
        self._cache_time = time.monotonic()

    def foreground(self) -> int:
        return self._w.foreground_window()

    def top_windows(self) -> list[int]:
        return self._w.top_level_windows()

    def process_name(self, hwnd: int) -> str:
        # The sweep asks about every visible window each second; cache by
        # process id so that's a dict lookup, not an OpenProcess call.
        now = time.monotonic()
        if now - self._cache_time > self._cache_seconds:
            self._names.clear()
            self._cache_time = now
        pid = self._w.window_pid(hwnd)
        name = self._names.get(pid)
        if name is None:
            name = self._w.app_process_name(hwnd)
            if self._w.process_name(pid).lower() != "applicationframehost.exe":
                self._names[pid] = name  # UWP frames vary per window: don't cache
        return name

    def title(self, hwnd: int) -> str:
        return self._w.window_title(hwnd)

    def is_app_window(self, hwnd: int) -> bool:
        return self._w.is_app_window(hwnd)

    def is_window(self, hwnd: int) -> bool:
        return self._w.is_window(hwnd)

    def is_minimized(self, hwnd: int) -> bool:
        return self._w.is_minimized(hwnd)

    def close_app(self, process_names: list[str]) -> int:
        return sum(self._w.terminate(pid) for pid in self._w.find_processes(process_names))

    def describe(self, hwnd: int) -> str:
        w = self._w
        return (f"{hwnd} {w.window_title(hwnd)!r} visible={w.is_visible(hwnd)} "
                f"minimized={w.is_minimized(hwnd)} app={w.is_app_window(hwnd)}")

    def minimize(self, hwnd: int) -> None:
        self._w.minimize(hwnd)

    def restore(self, hwnd: int) -> None:
        self._w.restore(hwnd)

    def session_locked(self) -> bool:
        return self._w.session_locked()


class WhatsAppGuard:
    def __init__(
        self,
        cfg: WhatsAppConfig,
        api: WindowApi,
        verify: Callable[[], str],
        clock: Callable[[], float] = time.monotonic,
        notify: Callable[[str], None] = lambda _msg: None,
        sleep: Callable[[float], None] = time.sleep,
        on_unlocked: Callable[[], None] = lambda: None,
        recheck: Callable[[], str] | None = None,
    ):
        """``verify`` returns MATCH, NO_MATCH, CANCELLED or CAMERA;
        ``notify`` shows a short message (used during the cooldown);
        ``on_unlocked`` runs once WhatsApp is back (closes the camera window);
        ``recheck`` is the quiet background check: MATCH, ABSENT (nobody
        there), STRANGER (a face that isn't yours) or CAMERA."""
        self.cfg = cfg
        self.api = api
        self.verify = verify
        self.clock = clock
        self.notify = notify
        self.sleep = sleep
        self.on_unlocked = on_unlocked
        self.recheck = recheck
        self.next_recheck = 0.0
        # Windows we minimised while locked, in order; all come back on unlock.
        self.hidden: dict[int, None] = {}
        self.locked = True
        # After a check we wait for WhatsApp to leave the front before asking
        # again, so the camera never loops on its own.
        self.armed = True
        self.left_at: float | None = None  # when WhatsApp lost the focus
        self.unlocked_at = 0.0
        # Has WhatsApp actually had the focus since the last unlock?
        self.was_active = False
        self.failures = 0
        self.cooldown_until = 0.0
        self._procs = {p.lower() for p in cfg.processes}
        self._titles = [t.lower() for t in cfg.window_titles if t]

    def is_whatsapp(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        if self.api.process_name(hwnd).lower() in self._procs:
            return True
        if self._titles:
            title = self.api.title(hwnd).lower()
            return any(t in title for t in self._titles)
        return False

    def is_whatsapp_app_window(self, hwnd: int) -> bool:
        # Tray menus, notification toasts and dialogs don't count: only a real
        # WhatsApp window (the one with the chats) triggers a check.
        return bool(hwnd) and self.api.is_app_window(hwnd) and self.is_whatsapp(hwnd)

    def hide(self, hwnd: int) -> None:
        self.api.minimize(hwnd)
        self.hidden[hwnd] = None

    def sweep(self) -> list[int]:
        """Minimise every visible WhatsApp window; return the ones it hid."""
        found = []
        for hwnd in self.api.top_windows():
            if self.api.is_minimized(hwnd) or not self.is_whatsapp_app_window(hwnd):
                continue
            self.hide(hwnd)
            found.append(hwnd)
        return found

    def bring_back(self, main: int) -> None:
        """Restore every window we hid, the one you opened last (on top),
        and make sure it really came back."""
        windows = [h for h in self.hidden if h != main and self.api.is_window(h)]
        windows.append(main)
        self.hidden.clear()
        for hwnd in windows:
            for attempt in range(4):
                self.api.restore(hwnd)
                self.sleep(0.15)
                if self.api.is_app_window(hwnd) and not self.api.is_minimized(hwnd):
                    break
                log.debug("restore attempt %d did not stick: %s", attempt + 1, self.api.describe(hwnd))
            else:
                log.warning("could not bring WhatsApp back: %s", self.api.describe(hwnd))
            log.debug("restored %s", self.api.describe(hwnd))

    def close_whatsapp(self, why: str) -> None:
        """With on_fail = "close", force-quit WhatsApp.  Only the processes in
        ``processes`` are ever killed: a browser matched by title is just
        minimised."""
        if self.cfg.on_fail != "close":
            return
        killed = self.api.close_app(self.cfg.processes)
        self.hidden.clear()
        log.info("closed WhatsApp (%s): %d process(es) ended", why, killed)

    def lock(self) -> None:
        if not self.locked:
            log.info("locking WhatsApp")
        self.locked = True
        self.sweep()

    def tick(self) -> None:
        if self.api.session_locked():
            self.lock()
            self.armed = True
            return

        fg = self.api.foreground()
        now = self.clock()

        if not self.locked:
            # Any WhatsApp window counts as still using it (its dialogs and
            # menus are separate windows), unless it's minimised: Windows can
            # leave the focus on a minimised window.
            if self.is_whatsapp(fg) and not self.api.is_minimized(fg):
                self.left_at = None
                self.was_active = True
                self._maybe_recheck(fg, now)
            elif self.was_active:
                if self.left_at is None:
                    self.left_at = now
                if now - self.left_at >= self.cfg.relock_after:
                    self.lock()
            elif now - self.unlocked_at >= max(self.cfg.relock_after, self.cfg.focus_grace):
                # Restored but Windows never gave it the focus: don't leave it
                # unlocked behind other windows for ever.
                self.lock()
            return

        in_front = self.is_whatsapp_app_window(fg) and not self.api.is_minimized(fg)
        hit = fg if in_front else 0

        # WhatsApp counts as "opened" when one of its windows is in front OR
        # simply on screen: Windows doesn't always activate a window that
        # opens from the tray or the Start menu.
        if hit:
            self.hide(hit)
        shown = self.sweep()
        opened = hit or (shown[0] if shown else 0)
        if not opened:
            self.armed = True
            return
        log.debug("WhatsApp on screen while locked: %s", self.api.describe(opened))
        if not self.armed:
            return
        self.armed = False

        if now < self.cooldown_until:
            wait = int(self.cooldown_until - now) + 1
            self.close_whatsapp("still in cooldown")
            self.notify(f"Too many tries. Try again in {wait} s")
            return

        log.info("WhatsApp opened while locked, checking face")
        self._challenge(opened)

    def _maybe_recheck(self, fg: int, now: float) -> None:
        """While WhatsApp is open and in use, quietly make sure it's still you."""
        if self.recheck is None or self.cfg.recheck_interval <= 0 or now < self.next_recheck:
            return
        outcome = self.recheck()
        after = self.clock()
        if outcome == MATCH:
            self.next_recheck = after + self.cfg.recheck_interval
            log.debug("background re-check: still you")
        elif outcome in (ABSENT, CAMERA):
            # An empty seat never closes WhatsApp: just look again soon.
            self.next_recheck = after + self.cfg.recheck_absent_interval
            log.info("background re-check: %s, trying again in %.0f s", outcome, self.cfg.recheck_absent_interval)
        else:
            log.info("background re-check: someone else is in front of WhatsApp")
            main = fg if self.is_whatsapp_app_window(fg) else 0
            self.lock()
            self.armed = False
            if main:
                self._challenge(main)

    def _challenge(self, main: int) -> None:
        """Run the visible face check for a hidden WhatsApp window."""
        outcome = self.verify()
        if outcome == MATCH:
            log.info("face matched, unlocking WhatsApp")
            self.failures = 0
            self.armed = True  # ready to ask again after the next relock
            self.locked = False
            self.bring_back(main)
            self.unlocked_at = self.clock()
            self.next_recheck = self.unlocked_at + self.cfg.recheck_interval
            self.left_at = None
            self.was_active = self.is_whatsapp(self.api.foreground())
            self.on_unlocked()
            return

        if outcome in (NO_MATCH, CANCELLED):
            self.close_whatsapp(outcome)
        self.sweep()  # in case it was clicked again during the check
        if outcome == NO_MATCH:
            self.failures += 1
            log.info("face not matched (%d in a row)", self.failures)
            if self.failures >= self.cfg.max_failures:
                self.cooldown_until = self.clock() + self.cfg.cooldown
                self.failures = 0
                log.info("cooldown for %.0f s", self.cfg.cooldown)
        else:
            log.info("face check ended: %s", outcome)
