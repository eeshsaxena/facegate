"""The background agent: WhatsApp lock + walk-away lock in one small process."""

from __future__ import annotations

import logging
import time

from facegate import store
from facegate.camera import CameraError, SharedCamera
from facegate.config import Config, user_dir
from facegate.engine import FaceEngine, match_score
from facegate.models import ensure_models
from facegate.presence import PresenceGuard
from facegate.verify import verify
from facegate.whatsapp import WhatsAppGuard, Win32Windows

log = logging.getLogger("facegate.agent")

STOP_EVENT = "Local\\FaceGateAgentStop"
MUTEX = "Local\\FaceGateAgent"


class NotEnrolled(RuntimeError):
    pass


def run(cfg: Config, show_preview: bool = True) -> int:
    from facegate import winapi

    mutex = winapi.single_instance(MUTEX)
    if mutex is None:
        log.error("the agent is already running")
        return 1
    stop = winapi.NamedEvent(STOP_EVENT)

    sid = winapi.current_user_sid()
    enrollment = store.load(user_dir(), sid)
    if enrollment is None:
        raise NotEnrolled("no face enrolled yet; run `facegate enroll` first")
    templates = enrollment.embeddings

    detector, recognizer = ensure_models(user_dir() / "models", download=False)
    engine = FaceEngine(detector, recognizer)
    camera = SharedCamera(cfg.camera_index)

    def read():
        try:
            return camera.read()
        except CameraError as e:
            log.warning("%s", e)
            return None

    def check_face() -> bool:
        preview = None
        if show_preview:
            from facegate.ui import Preview

            preview = Preview("FaceGate: WhatsApp is locked")
        try:
            res = verify(read, engine, templates, cfg.match, on_frame=preview)
            log.info("verify: %s (best %.3f, turn %.2f)", res.reason, res.best_score, res.turn_range)
            return res.ok
        finally:
            if preview is not None:
                preview.close()
            if not cfg.presence.enabled:
                camera.release()

    def sense() -> bool | None:
        frame = read()
        if frame is None:
            return None
        faces = [f for f in engine.detect(frame) if f.width >= cfg.match.min_face_px // 2]
        if not cfg.presence.require_owner:
            return bool(faces)
        return any(
            match_score(engine.embed(frame, f), templates) >= cfg.match.threshold for f in faces
        )

    guards = []
    if cfg.whatsapp.enabled:
        guards.append((WhatsAppGuard(cfg.whatsapp, Win32Windows(), check_face), cfg.whatsapp.poll_interval))
    if cfg.presence.enabled:
        guards.append(
            (
                PresenceGuard(cfg.presence, sense, winapi.lock_workstation, winapi.session_locked, camera.release),
                cfg.presence.interval,
            )
        )
    if not guards:
        log.error("both the WhatsApp lock and the walk-away lock are turned off in config.toml")
        return 1

    log.info("agent running (%s)", ", ".join(type(g).__name__ for g, _ in guards))
    due = [0.0] * len(guards)
    step = min(interval for _, interval in guards)
    try:
        while not stop.wait(step):
            now = time.monotonic()
            for i, (guard, interval) in enumerate(guards):
                if now >= due[i]:
                    try:
                        guard.tick()
                    except Exception:
                        log.exception("%s failed", type(guard).__name__)
                    due[i] = time.monotonic() + interval
    finally:
        camera.release()
        stop.close()
    log.info("agent stopped")
    return 0


def request_stop() -> bool:
    from facegate import winapi

    try:
        ev = winapi.NamedEvent(STOP_EVENT, create=False)
    except OSError:
        return False
    ev.set()
    ev.close()
    return True
