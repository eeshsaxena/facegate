"""The background agent: WhatsApp lock (+ optional walk-away lock).

``run`` is the agent itself.  ``supervise`` keeps it alive: if the agent
crashes or is killed it is restarted a few seconds later, so WhatsApp
doesn't stay unlocked just because a process died.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
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



def _name(base: str) -> str:
    # FACEGATE_INSTANCE gives tests their own names, so running the test
    # suite never stops (or blocks) the real background agent.
    suffix = os.environ.get("FACEGATE_INSTANCE", "")
    return "Local\\" + base + (f"-{suffix}" if suffix else "")


def stop_event_name() -> str:
    return _name("FaceGateAgentStop")


def mutex_name() -> str:
    return _name("FaceGateAgent")


def supervisor_mutex_name() -> str:
    return _name("FaceGateSupervisor")


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
        self._kept_preview = None

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
                if res.ok and preview is not None:
                    # Keep our window (and with it the right to change the
                    # foreground) until WhatsApp is back in front.
                    self._kept_preview, preview = preview, None
                return res.reason
            finally:
                if preview is not None:
                    preview.close()
                if not self.cfg.presence.enabled:
                    self.camera.close()

    def close_preview(self) -> None:
        if self._kept_preview is not None:
            self._kept_preview.close()
            self._kept_preview = None

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

    mutex = winapi.single_instance(mutex_name())
    if mutex is None:
        log.error("the agent is already running")
        return EXIT_ALREADY_RUNNING
    stop = winapi.NamedEvent(stop_event_name())

    enrollment = store.load(user_dir(), winapi.current_user_sid())
    if enrollment is None:
        raise NotEnrolled("no face enrolled yet; run `facegate enroll` first")

    detector, recognizer = ensure_models(user_dir() / "models", download=False)
    checker = FaceChecker(cfg, FaceEngine(detector, recognizer), enrollment.embeddings, show_preview)

    wake = threading.Event()
    guards: list[tuple[object, float, bool]] = []  # (guard, interval, wakes on window events)
    if cfg.whatsapp.enabled:
        wa = WhatsAppGuard(cfg.whatsapp, Win32Windows(), lambda: "", notify=checker.notify,
                           on_unlocked=checker.close_preview)
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


def is_running() -> bool:
    from facegate import winapi

    return winapi.mutex_exists(supervisor_mutex_name()) or winapi.mutex_exists(mutex_name())


def start_detached(extra: list[str] | None = None, verbose: bool = False) -> int:
    """Launch the supervised agent in the background, with no window, and
    return its process id.  It keeps running after this terminal closes."""
    exe = Path(sys.executable)
    windowed = exe.with_name("pythonw.exe")
    if windowed.exists():
        exe = windowed
    top = ["-v"] if verbose else []
    cmd = [str(exe), "-m", "facegate", *top, "agent", "run", *(extra or [])]
    flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    try:
        # Also leave the terminal's job object, if it lets us, so closing
        # the terminal (or the app that hosts it) doesn't take FaceGate down.
        proc = subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True)
    except OSError:
        proc = subprocess.Popen(cmd, creationflags=flags, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return proc.pid


def request_stop() -> bool:
    from facegate import winapi

    try:
        ev = winapi.NamedEvent(stop_event_name(), create=False)
    except OSError:
        return False
    ev.set()
    ev.close()
    return True


def run_child(command: list[str], popen=subprocess.Popen, sleep: Callable[[float], None] = time.sleep) -> int:
    """Run the agent process and wait for it, staying responsive to Ctrl+C.

    A plain ``wait()`` can't be interrupted on Windows, so we poll.  Ctrl+C
    asks the agent to stop cleanly (it has no console of its own to get the
    signal) and is treated as a normal exit.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = popen(command, creationflags=flags, stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while proc.poll() is None:
            sleep(0.5)
        return proc.returncode
    except KeyboardInterrupt:
        log.info("Ctrl+C, stopping the agent")
        request_stop()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return EXIT_OK


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
    runner = runner or run_child
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
