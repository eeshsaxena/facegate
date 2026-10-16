"""Face lock for WhatsApp Desktop.

While locked, any time a WhatsApp window comes to the front it is minimised
and the camera checks your face.  If it's you, the window comes back.  Once
unlocked it stays unlocked until WhatsApp has been in the background for
``relock_after`` seconds, or Windows itself gets locked; then it is
minimised and locked again.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Protocol

from facegate.config import WhatsAppConfig

log = logging.getLogger("facegate.whatsapp")


class WindowApi(Protocol):
    def foreground(self) -> int: ...
    def process_name(self, hwnd: int) -> str: ...
    def title(self, hwnd: int) -> str: ...
    def is_window(self, hwnd: int) -> bool: ...
    def minimize(self, hwnd: int) -> None: ...
    def restore(self, hwnd: int) -> None: ...
    def session_locked(self) -> bool: ...


class Win32Windows:
    def __init__(self):
        from facegate import winapi

        self._w = winapi

    def foreground(self) -> int:
        return self._w.foreground_window()

    def process_name(self, hwnd: int) -> str:
        return self._w.app_process_name(hwnd)

    def title(self, hwnd: int) -> str:
        return self._w.window_title(hwnd)

    def is_window(self, hwnd: int) -> bool:
        return self._w.is_window(hwnd)

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
        verify: Callable[[], bool],
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg = cfg
        self.api = api
        self.verify = verify
        self.clock = clock
        self.locked = True
        # After a failed check we wait for WhatsApp to leave the front before
        # asking again, so a stranger can't keep the camera spinning.
        self.armed = True
        self.last_active = 0.0
        self.known: set[int] = set()
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

    def lock(self) -> None:
        if not self.locked:
            log.info("locking WhatsApp")
            for hwnd in self.known:
                if self.api.is_window(hwnd):
                    self.api.minimize(hwnd)
        self.locked = True
        self.known = {h for h in self.known if self.api.is_window(h)}

    def tick(self) -> None:
        if self.api.session_locked():
            self.lock()
            self.armed = True
            return

        fg = self.api.foreground()
        hit = fg if self.is_whatsapp(fg) else 0
        if hit:
            self.known.add(hit)
        now = self.clock()

        if not self.locked:
            if hit:
                self.last_active = now
            elif now - self.last_active >= self.cfg.relock_after:
                self.lock()
            return

        if not hit:
            self.armed = True
            return
        if not self.armed:
            return

        self.api.minimize(hit)
        log.info("WhatsApp opened while locked, checking face")
        if self.verify():
            log.info("face matched, unlocking WhatsApp")
            self.locked = False
            self.last_active = self.clock()
            self.api.restore(hit)
        else:
            log.info("face not matched, WhatsApp stays locked")
            self.armed = False
