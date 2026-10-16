"""The background agent: WhatsApp lock (+ optional walk-away lock).

``run`` is the agent itself.  ``supervise`` keeps it alive: if the agent
crashes or is killed it is restarted a few seconds later, so WhatsApp
doesn't stay unlocked just because a process died.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Callable

from facegate import store
from facegate.camera import Camera, CameraError
from facegate.config import Config, user_dir
from facegate.engine import FaceEngine, match_score
from facegate.models import ensure_models
from facegate.presence import PresenceGuard
from facegate.verify import verify
from facegate.whatsapp import CAMERA, WhatsAppGuard, Win32Windows

log = logging.getLogger("facegate.agent")

STOP_EVENT = "Local\\FaceGateAgentStop"
MUTEX = "Local\\FaceGateAgent"
SUPERVISOR_MUTEX = "Local\\FaceGateSupervisor"

# Exit codes.  The supervisor restarts on anything not listed as final.
EXIT_OK = 0
EXIT_NOT_ENROLLED = 3
EXIT_SETUP = 4  # models missing, bad settings, nothing enabled
EXIT_ALREADY_RUNNING = 10
FINAL_EXITS = {EXIT_OK, EXIT_NOT_ENROLLED, EXIT_SETUP, EXIT_ALREADY_RUNNING}


class NotEnrolled(RuntimeError):
    pass


class FaceChecker:
    """Owns the camera for the agent and runs one face check at a time."""

    def __init__(self, cfg: Config, engine, templates, show_preview: bool = True):
        self.cfg = cfg
        self.engine = engine
        self.templates = templates
        self.show_preview = show_preview
        self.camera = Camera(cfg.camera_index)
        self.lock = threading.RLock()

    def _preview(self, title: str):
        if not self.show_preview:
            return None
        from facegate.ui import Preview

        return Preview(title)

    def open_camera(self) -> bool:
        if self.camera.is_open:
            return True
        try:
            self.camera.open()
            return True
        except CameraError as e:
            log.warning("%s", e)
            return False

    def release(self) -> None:
        with self.lock:
            self.camera.close()

    def notify(self, text: str) -> None:
        preview = self._preview("FaceGate")
        if preview is not None:
            preview.message(text)
            preview.close()

    def check(self, keep_hidden: Callable[[], object] = lambda: None) -> str:
        with self.lock:
            preview = self._preview("FaceGate: WhatsApp is locked")
            try:
                if not self.open_camera():
                    if preview is not None:
                        preview.message("Camera is busy or missing. Try again in a moment.", 3.0)
                    return CAMERA
                last_sweep = 0.0

                def on_frame(frame, faces, status):
                    nonlocal last_sweep
                    now = time.monotonic()
                    if now - last_sweep > 0.3:
                        keep_hidden()  # WhatsApp may be clicked again meanwhile
                        last_sweep = now
                    return True if preview is None else preview(frame, faces, status)

                res = verify(self.camera.read, self.engine, self.templates, self.cfg.match, on_frame=on_frame)
                log.info("verify: %s (best %.3f, turn %.2f)", res.reason, res.best_score, res.turn_range)
                if not res.ok and res.reason == "timeout" and preview is not None:
                    preview.message("Face not recognised", 1.2)
                return res.reason
            finally:
                if preview is not None:
                    preview.close()
                if not self.cfg.presence.enabled:
                    self.camera.close()

    def someone_there(self) -> bool | None:
        with self.lock:
            if not self.open_camera():
                return None
            frame = self.camera.read()
        if frame is None:
            return None
        faces = [f for f in self.engine.detect(frame) if f.width >= self.cfg.match.min_face_px // 2]
        if not self.cfg.presence.require_owner:
            return bool(faces)
        return any(
            match_score(self.engine.embed(frame, f), self.templates) >= self.cfg.match.threshold for f in faces
        )


def run(cfg: Config, show_preview: bool = True) -> int:
    from facegate import winapi

    mutex = winapi.single_instance(MUTEX)
    if mutex is None:
        log.error("the agent is already running")
        return EXIT_ALREADY_RUNNING
    stop = winapi.NamedEvent(STOP_EVENT)

    enrollment = store.load(user_dir(), winapi.current_user_sid())
    if enrollment is None:
        raise NotEnrolled("no face enrolled yet; run `facegate enroll` first")

    detector, recognizer = ensure_models(user_dir() / "models", download=False)
    checker = FaceChecker(cfg, FaceEngine(detector, recognizer), enrollment.embeddings, show_preview)

    wake = threading.Event()
    guards: list[tuple[object, float, bool]] = []  # (guard, interval, wakes on window events)
    if cfg.whatsapp.enabled:
        wa = WhatsAppGuard(cfg.whatsapp, Win32Windows(), lambda: "", notify=checker.notify)
        wa.verify = lambda: checker.check(keep_hidden=wa.sweep)
        guards.append((wa, cfg.whatsapp.poll_interval, True))
    if cfg.presence.enabled:
        pg = PresenceGuard(cfg.presence, checker.someone_there, winapi.lock_workstation,
                           winapi.session_locked, checker.release)
        guards.append((pg, cfg.presence.interval, False))
    if not guards:
        log.error("both the WhatsApp lock and the walk-away lock are turned off in config.toml")
        return EXIT_SETUP

    watcher = winapi.ForegroundWatcher(wake.set).start() if cfg.whatsapp.enabled else None
    if watcher is not None and not watcher.ok:
        log.warning("window hook unavailable, falling back to polling")

    log.info("agent running (%s)", ", ".join(type(g).__name__ for g, _, _ in guards))
    due = [0.0] * len(guards)
    step = min(interval for _, interval, _ in guards)
    try:
        while not stop.wait(0):
            woke = wake.wait(step)
            wake.clear()
            now = time.monotonic()
            for i, (guard, interval, on_wake) in enumerate(guards):
                if now >= due[i] or (woke and on_wake):
                    try:
                        guard.tick()
                    except Exception:
                        log.exception("%s failed", type(guard).__name__)
                    due[i] = time.monotonic() + interval
    finally:
        if watcher is not None:
            watcher.stop()
        checker.release()
        stop.close()
    log.info("agent stopped")
    return EXIT_OK


def request_stop() -> bool:
    from facegate import winapi

    try:
        ev = winapi.NamedEvent(STOP_EVENT, create=False)
    except OSError:
        return False
    ev.set()
    ev.close()
    return True


def child_command(extra: list[str], verbose: bool = False) -> list[str]:
    top = ["-v"] if verbose else []
    return [sys.executable, "-m", "facegate", *top, "agent", "run", "--no-supervise", *extra]


def supervise(
    command: list[str],
    runner: Callable[[list[str]], int] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_restarts: int = 5,
    window: float = 300.0,
    delay: float = 3.0,
) -> int:
    """Run the agent and restart it whenever it dies unexpectedly.

    Gives up if it crashes more than ``max_restarts`` times in ``window``
    seconds (something is really wrong; see facegate.log).
    """
    if runner is None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        runner = lambda cmd: subprocess.call(cmd, creationflags=flags)  # noqa: E731
    crashes: deque[float] = deque()
    while True:
        code = runner(command)
        if code in FINAL_EXITS:
            return code
        now = clock()
        crashes.append(now)
        while crashes and now - crashes[0] > window:
            crashes.popleft()
        if len(crashes) > max_restarts:
            log.error("agent keeps crashing (exit %s); giving up, see facegate.log", code)
            return code
        log.warning("agent exited with %s, restarting in %.0f s", code, delay)
        sleep(delay)
