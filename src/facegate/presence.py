"""Walk-away lock: lock Windows when you leave the camera's view.

Only the 230 KB face detector runs on each check (every couple of seconds),
plus the recogniser when ``require_owner`` is on.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from facegate.config import PresenceConfig

log = logging.getLogger("facegate.presence")


class PresenceGuard:
    def __init__(
        self,
        cfg: PresenceConfig,
        sense: Callable[[], bool | None],
        lock: Callable[[], None],
        session_locked: Callable[[], bool],
        release_camera: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
    ):
        """``sense`` returns True (someone is there), False (nobody), or
        None (no reading, e.g. the camera is busy); None never locks."""
        self.cfg = cfg
        self.sense = sense
        self.lock_screen = lock
        self.session_locked = session_locked
        self.release_camera = release_camera
        self.clock = clock
        self.last_seen = clock()
        self.paused = False

    def tick(self) -> None:
        now = self.clock()
        if self.session_locked():
            if not self.paused:
                # Free the webcam while the lock screen is up.
                self.release_camera()
                self.paused = True
            return
        if self.paused:
            self.paused = False
            self.last_seen = now  # fresh grace period after you sign back in

        present = self.sense()
        if present is None:
            return
        if present:
            self.last_seen = now
        elif now - self.last_seen >= self.cfg.absent_seconds:
            log.info("nobody in front of the camera for %.0f s, locking", now - self.last_seen)
            self.lock_screen()
            self.last_seen = now
