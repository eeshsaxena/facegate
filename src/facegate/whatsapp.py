"""Face lock for WhatsApp Desktop.

While locked:
  * every visible WhatsApp window is kept minimised (checked on every window
    change, through a hook, plus a once-a-second backup sweep);
  * bringing WhatsApp to the front starts a face check; if it's you, the
    window comes back.

Once unlocked it stays open while you use it.  It locks again (and is
minimised) after ``relock_after`` seconds in the background, or as soon as
Windows itself is locked.

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


class WindowApi(Protocol):
    def foreground(self) -> int: ...
    def top_windows(self) -> list[int]: ...
    def process_name(self, hwnd: int) -> str: ...
    def title(self, hwnd: int) -> str: ...
    def is_visible(self, hwnd: int) -> bool: ...
    def is_minimized(self, hwnd: int) -> bool: ...
    def owner(self, hwnd: int) -> int: ...
    def minimize(self, hwnd: int) -> None: ...
    def restore(self, hwnd: int) -> None: ...
    def session_locked(self) -> bool: ...


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

    def is_visible(self, hwnd: int) -> bool:
        return self._w.is_visible(hwnd)

    def is_minimized(self, hwnd: int) -> bool:
        return self._w.is_minimized(hwnd)

    def owner(self, hwnd: int) -> int:
        return self._w.owner(hwnd)

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
    ):
        """``verify`` returns MATCH, NO_MATCH, CANCELLED or CAMERA;
        ``notify`` shows a short message (used during the cooldown)."""
        self.cfg = cfg
        self.api = api
        self.verify = verify
        self.clock = clock
        self.notify = notify
        self.locked = True
        # After a check we wait for WhatsApp to leave the front before asking
        # again, so the camera never loops on its own.
        self.armed = True
        self.last_active = 0.0
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

    def sweep(self) -> int:
        """Minimise every visible WhatsApp window; return how many."""
        hidden = 0
        for hwnd in self.api.top_windows():
            if not self.api.is_visible(hwnd) or self.api.is_minimized(hwnd):
                continue
            # Owned windows (dialogs, pop-ups) follow their owner.
            if self.api.owner(hwnd) or not self.is_whatsapp(hwnd):
                continue
            self.api.minimize(hwnd)
            hidden += 1
        return hidden

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
        hit = fg if self.is_whatsapp(fg) else 0
        now = self.clock()

        if not self.locked:
            if hit:
                self.last_active = now
            elif now - self.last_active >= self.cfg.relock_after:
                self.lock()
            return

        if hit:
            self.api.minimize(hit)
        self.sweep()
        if not hit:
            self.armed = True
            return
        if not self.armed:
            return
        self.armed = False

        if now < self.cooldown_until:
            wait = int(self.cooldown_until - now) + 1
            self.notify(f"Too many tries. Try again in {wait} s")
            return

        log.info("WhatsApp opened while locked, checking face")
        outcome = self.verify()
        if outcome == MATCH:
            log.info("face matched, unlocking WhatsApp")
            self.failures = 0
            self.armed = True  # ready to ask again after the next relock
            self.locked = False
            self.last_active = self.clock()
            self.api.restore(hit)
            return

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
