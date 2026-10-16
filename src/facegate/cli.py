"""`facegate` command line."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from facegate import __version__, store
from facegate.config import DEFAULT_CONFIG_TOML, load_config, user_dir

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "FaceGate"


def _engine():
    from facegate.engine import FaceEngine
    from facegate.models import ensure_models

    det, rec = ensure_models(user_dir() / "models", download=True, log=print)
    return FaceEngine(det, rec)


def cmd_models(args) -> int:
    from facegate.models import ensure_models

    det, rec = ensure_models(user_dir() / "models", download=True, log=print)
    print(f"models ready:\n  {det}\n  {rec}")
    return 0


def cmd_config(args) -> int:
    path = user_dir() / "config.toml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        print(f"wrote default settings to {path}")
    else:
        load_config(path)  # validates
        print(f"settings: {path} (valid)")
    return 0


def cmd_enroll(args) -> int:
    from facegate import winapi
    from facegate.camera import Camera
    from facegate.ui import Preview
    from facegate.verify import EnrollError, enroll

    cfg = load_config()
    engine = _engine()
    sid = winapi.current_user_sid()
    preview = Preview("FaceGate: enroll (Esc to cancel)")
    print("Look at the camera and slowly move your head around (left, right, up, down).")
    try:
        with Camera(cfg.camera_index) as cam:
            emb = enroll(cam.read, engine, samples=args.samples, on_frame=preview)
    except EnrollError as e:
        print(f"enrollment failed: {e}")
        return 1
    finally:
        preview.close()
    path = store.save(user_dir(), store.Enrollment(sid, winapi.current_user_sam_name(), emb))
    print(f"saved {len(emb)} face samples to {path}")
    print("Next: `facegate test`, then `facegate start` (and `facegate autostart on` to start it at sign-in).")
    return 0


def cmd_test(args) -> int:
    from facegate import winapi
    from facegate.camera import Camera
    from facegate.ui import Preview
    from facegate.verify import verify

    cfg = load_config()
    enr = store.load(user_dir(), winapi.current_user_sid())
    if enr is None:
        print("no face enrolled yet; run `facegate enroll` first")
        return 3
    engine = _engine()
    preview = Preview("FaceGate: test (Esc to cancel)")
    try:
        with Camera(cfg.camera_index) as cam:
            res = verify(cam.read, engine, enr.embeddings, cfg.match, on_frame=preview)
    finally:
        preview.close()
    verdict = "MATCH" if res.ok else f"no match ({res.reason})"
    print(
        f"{verdict}: best score {res.best_score:.3f} (threshold {cfg.match.threshold}), "
        f"matching frames {res.matched_frames}, head turn {res.turn_range:.2f} "
        f"(needs {cfg.match.liveness_min_range if cfg.match.liveness else 0})"
    )
    return res.exit_code


def cmd_forget(args) -> int:
    from facegate import winapi

    removed = store.delete(user_dir(), winapi.current_user_sid())
    print("enrolled face deleted" if removed else "nothing was enrolled")
    return 0


def cmd_agent(args) -> int:
    from facegate import agent, winapi
    from facegate.models import ModelsMissing

    if args.action == "stop":
        ok = agent.request_stop()
        print("stop signal sent" if ok else "the agent is not running")
        return 0 if ok else 1

    if not args.no_supervise:
        _setup_logging(args.verbose, "supervisor.log")
        mutex = winapi.single_instance(agent.supervisor_mutex_name())
        if mutex is None:
            logging.error("the agent is already running")
            return agent.EXIT_ALREADY_RUNNING
        extra = [f for f, on in (("--presence", args.presence), ("--no-whatsapp", args.no_whatsapp),
                                 ("--no-preview", args.no_preview)) if on]
        try:
            return agent.supervise(agent.child_command(extra, args.verbose))
        finally:
            winapi.kernel32.CloseHandle(mutex)

    cfg = load_config()
    if args.presence:
        cfg.presence.enabled = True
    if args.no_whatsapp:
        cfg.whatsapp.enabled = False
    try:
        return agent.run(cfg, show_preview=not args.no_preview)
    except agent.NotEnrolled as e:
        logging.error("%s", e)
        return agent.EXIT_NOT_ENROLLED
    except ModelsMissing as e:
        logging.error("%s", e)
        return agent.EXIT_SETUP


def cmd_start(args) -> int:
    import time

    from facegate import agent, winapi

    if agent.is_running():
        print("FaceGate is already running")
        return 0
    if store.load(user_dir(), winapi.current_user_sid()) is None:
        print("no face enrolled yet; run `facegate enroll` first")
        return agent.EXIT_NOT_ENROLLED
    extra = ["--presence"] if args.presence else []
    agent.start_detached(extra, args.verbose)
    for _ in range(40):  # the agent loads its models in about a second
        time.sleep(0.25)
        if winapi.mutex_exists(agent.mutex_name()):
            print("FaceGate is running in the background. Open WhatsApp to try it.")
            print("It keeps running after you close this window; stop it with `facegate stop`.")
            return 0
    print(f"FaceGate did not start; see {user_dir() / 'facegate.log'}")
    return 1


def cmd_stop(args) -> int:
    import time

    from facegate import agent

    if not agent.request_stop():
        print("FaceGate is not running")
        return 0
    for _ in range(40):
        time.sleep(0.25)
        if not agent.is_running():
            print("FaceGate stopped")
            return 0
    print("stop signal sent, but it is still shutting down")
    return 1


def cmd_status(args) -> int:
    import winreg

    from facegate import agent, winapi

    print("running:    " + ("yes" if agent.is_running() else "no  (start it with `facegate start`)"))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        auto = "on"
    except FileNotFoundError:
        auto = "off (turn on with `facegate autostart on`)"
    print("autostart:  " + auto)
    enr = store.load(user_dir(), winapi.current_user_sid())
    print("enrolled:   " + (f"yes, {len(enr.embeddings)} samples" if enr else "no  (`facegate enroll`)"))
    from facegate import intruders

    shots = intruders.photos()
    print(f"intruders: {len(shots)} photo(s)" + (f", latest {shots[-1].name}" if shots else ""))
    log_path = user_dir() / "facegate.log"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
        print(f"last log lines ({log_path}):")
        for line in lines:
            print("  " + line)
    return 0


def cmd_intruders(args) -> int:
    import os

    from facegate import intruders

    if args.clear:
        print(f"deleted {intruders.clear()} photo(s)")
        return 0
    shots = intruders.photos()
    if args.open:
        intruders.folder().mkdir(parents=True, exist_ok=True)
        os.startfile(intruders.folder())  # opens it in File Explorer
        return 0
    if not shots:
        print("no failed attempts recorded")
        return 0
    print(f"{len(shots)} photo(s) of failed attempts in {intruders.folder()}:")
    for p in shots[-10:]:
        print("  " + p.name)
    print("open the folder with `facegate intruders --open`")
    return 0


def _agent_command() -> str:
    exe = Path(sys.executable)
    windowed = exe.with_name("pythonw.exe")
    if windowed.exists():
        exe = windowed
    return f'"{exe}" -m facegate agent'


def cmd_autostart(args) -> int:
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
        if args.action == "on":
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _agent_command())
            print("the agent will start when you sign in")
        elif args.action == "off":
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
            print("autostart removed")
        else:
            try:
                value, _ = winreg.QueryValueEx(key, RUN_VALUE)
                print(f"on: {value}")
            except FileNotFoundError:
                print("off")
    return 0


def cmd_doctor(args) -> int:
    from facegate import winapi
    from facegate.camera import Camera, CameraError
    from facegate.models import ModelsMissing, ensure_models

    ok = True
    print(f"facegate {__version__}, Python {sys.version.split()[0]}")
    try:
        cfg = load_config()
        print(f"[ok] settings ({user_dir() / 'config.toml'})")
    except ValueError as e:
        print(f"[!!] settings: {e}")
        return 1
    try:
        det, rec = ensure_models(user_dir() / "models", download=False)
        print("[ok] models present and checksums match")
    except ModelsMissing as e:
        print(f"[!!] {e}")
        return 1
    enr = store.load(user_dir(), winapi.current_user_sid())
    print(f"[ok] {len(enr.embeddings)} face samples enrolled" if enr else "[--] no face enrolled (`facegate enroll`)")
    ok &= enr is not None

    from facegate.engine import FaceEngine

    engine = FaceEngine(det, rec)
    try:
        with Camera(cfg.camera_index) as cam:
            frame = cam.read()
            faces = engine.detect(frame) if frame is not None else []
            if frame is None:
                print("[!!] camera opened but returned no picture")
                ok = False
            else:
                h, w = frame.shape[:2]
                print(f"[ok] camera {cfg.camera_index}: {w}x{h}, faces in view: {len(faces)}")
    except CameraError as e:
        print(f"[!!] {e}")
        ok = False
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="facegate", description="Webcam face lock for WhatsApp Desktop and walk-away locking.")
    p.add_argument("--version", action="version", version=f"facegate {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("models", help="download the face models (38 MB, once)").set_defaults(func=cmd_models)
    sub.add_parser("config", help="create or check config.toml").set_defaults(func=cmd_config)
    e = sub.add_parser("enroll", help="teach FaceGate your face")
    e.add_argument("--samples", type=int, default=15)
    e.set_defaults(func=cmd_enroll)
    sub.add_parser("test", help="check your face against the enrollment").set_defaults(func=cmd_test)
    sub.add_parser("forget", help="delete your enrolled face").set_defaults(func=cmd_forget)
    a = sub.add_parser("agent", help="run (or stop) the background locks")
    a.add_argument("action", nargs="?", choices=["run", "stop"], default="run")
    a.add_argument("--presence", action="store_true", help="also turn on the walk-away lock")
    a.add_argument("--no-whatsapp", action="store_true", help="turn off the WhatsApp lock")
    a.add_argument("--no-preview", action="store_true", help="don't show the camera window")
    a.add_argument("--no-supervise", action="store_true", help="don't restart the agent if it crashes")
    a.set_defaults(func=cmd_agent)
    s = sub.add_parser("autostart", help="start the agent when you sign in")
    s.add_argument("action", choices=["on", "off", "status"])
    s.set_defaults(func=cmd_autostart)
    st = sub.add_parser("start", help="run FaceGate in the background (survives closing the terminal)")
    st.add_argument("--presence", action="store_true", help="also turn on the walk-away lock")
    st.set_defaults(func=cmd_start)
    sub.add_parser("stop", help="stop the background FaceGate").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="is it running, autostart, enrollment, recent log").set_defaults(func=cmd_status)
    i = sub.add_parser("intruders", help="photos of people who failed the face check")
    i.add_argument("--open", action="store_true", help="open the folder")
    i.add_argument("--clear", action="store_true", help="delete them all")
    i.set_defaults(func=cmd_intruders)
    sub.add_parser("doctor", help="check models, enrollment and camera").set_defaults(func=cmd_doctor)
    return p


def _setup_logging(verbose: bool, filename: str = "facegate.log") -> None:
    from logging.handlers import RotatingFileHandler

    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # None under pythonw
        handlers.append(logging.StreamHandler())
    try:
        user_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(user_dir() / filename, maxBytes=1_000_000, backupCount=2,
                                            encoding="utf-8", delay=True))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(process)d %(name)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    supervisor = args.command == "agent" and args.action == "run" and not args.no_supervise
    if not supervisor:
        _setup_logging(args.verbose)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except ValueError as e:  # bad config.toml and friends
        print(f"error: {e}", file=sys.stderr if sys.stderr else None)
        return 4
