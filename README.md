# FaceGate

Face unlock for Windows laptops that have a normal webcam but no IR camera
or fingerprint reader.

- **WhatsApp lock:** every time WhatsApp opens or comes back to the front,
  the webcam checks your face first. While you're using it, including its
  own dialogs like the file picker, it stays open. Switch to something else
  and it locks and minimizes within a second. Locking Windows locks it too.
- **Walk-away lock:** Windows locks itself when nobody has been in front of
  the camera for 30 seconds. This is optional and off by default.

It's lightweight: two small OpenCV models run on the CPU (no GPU, no
dlib build, no cloud), it uses a couple of threads at most, and the
camera is only on while it is actually checking.

## How the WhatsApp lock behaves

- **Instant.** A Windows event hook reports every window change, so
  WhatsApp is minimized the moment it comes to the front. There's also a
  once-a-second backup sweep. Nothing is injected into WhatsApp.
- **Catches every way of opening it.** Tray icon, Start menu, taskbar or
  Alt+Tab. Even when Windows shows WhatsApp without making it the active
  window, it still gets checked.
- **Comes back in front.** After a match, every hidden WhatsApp window is
  restored and focused. FaceGate checks that it really reappeared and
  retries if it didn't.
- **Fast re-checks, no replays.** The webcam stays on for 15 s after a check
  (`camera_keep_open`), so switching back is quick. Frames buffered from an
  earlier check are thrown away first, so they can never count again.
- **Every window.** Popped-out chat windows are hidden too, not just the
  main one. Dialogs follow their main window.
- **Fails closed.** The agent starts locked and hides WhatsApp if it's
  already on screen. Clicking WhatsApp again during a check just hides it again.
- **Restarts itself.** `facegate start` runs a small watchdog over the lock process
  and restarts it within 3 seconds if it crashes or is killed. Stop it
  properly with `facegate stop`.
- **No camera loops.** After 3 faces that don't match in a row, it stops
  asking for 30 seconds. Pressing Esc or a busy camera (Teams, Meet)
  doesn't count as a wrong face.
- **Logs** go to `%LOCALAPPDATA%\FaceGate\facegate.log` (rotated at 1 MB).
  Faces and frames are never logged.

## How it works

| Step | What runs | Size |
| --- | --- | --- |
| Find the face + 5 landmarks | [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) | 230 KB |
| Turn the face into a 128-number fingerprint | [SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) | 37 MB |
| Compare with your enrolled fingerprints | cosine similarity, average of the best 3 | none |
| "Is it a real head?" | head-turn check (below) | none |

Both models come from the OpenCV Model Zoo and are pinned by SHA-256.
FaceGate refuses a download that doesn't match. Only the fingerprints are
saved (`%LOCALAPPDATA%\FaceGate\faces\<your SID>.npz`), never a photo.

**Head-turn check.** While it is matching, FaceGate asks you to turn your
head slightly. It measures how far the nose moves sideways compared with
the mouth. On a real head the nose sticks out, so it moves more. On a flat
photo, however you tilt it, everything moves together and the number stays
the same, so the check doesn't pass. The tests check this with a simulated
3D head against a simulated printed photo.

## Install

Needs Windows 10/11, Python 3.11+ and a webcam.

```bash
pip install git+https://github.com/eeshsaxena/facegate
```

```bash
facegate models
```

```bash
facegate enroll
```

```bash
facegate test
```

`enroll` opens a small camera window. Slowly move your head around until
it has 15 samples. `test` shows your score against the threshold, so you
can see how reliable it is in your lighting.

## Use

Start it in the background. It keeps running after you close the terminal:

```bash
facegate start
```

Start it automatically every time you sign in to Windows:

```bash
facegate autostart on
```

Check on it (running, autostart, enrollment, last log lines):

```bash
facegate status
```

Stop it:

```bash
facegate stop
```

Add `--presence` to `facegate start` for the walk-away lock. `facegate agent`
runs it in the current terminal instead (Ctrl+C stops it).

Other commands: `facegate doctor` checks the models, your enrollment and the
camera. `facegate config` writes a settings file you can edit.
`facegate forget` deletes your enrolled face.

## Settings

`facegate config` creates `%LOCALAPPDATA%\FaceGate\config.toml`:

```toml
camera_index = 0

[match]
threshold = 0.42          # higher = stricter
frames_required = 3
timeout = 8.0
liveness = true           # head-turn check
liveness_min_range = 0.10

[whatsapp]
enabled = true
processes = ["WhatsApp.exe", "WhatsApp.Root.exe"]
window_titles = []        # ["WhatsApp"] also guards web.whatsapp.com
relock_after = 1.0        # seconds out of focus before it locks again
focus_grace = 10.0        # lock if it never gets focus after an unlock
camera_keep_open = 15.0   # keep the webcam warm for quick re-checks (0 = off)
max_failures = 3          # wrong faces in a row before a cooldown
cooldown = 30.0

[presence]
enabled = false
absent_seconds = 30.0
interval = 2.0
require_owner = false     # true = also lock when someone else sits down
```

A typo in a setting name is an error, not something silently ignored.

## What it can and can't protect against

Be clear-eyed about this. It is a convenience lock, not Windows Hello.

- It **stops** someone else's face, and a printed photo or a still picture
  of you on a phone.
- It **does not stop** a video of you turning your head, or a good 3D mask.
  Windows Hello needs an IR camera for exactly this reason.
- The WhatsApp lock minimizes the window. Notification previews and the
  taskbar thumbnail can still show message text. For stronger protection,
  also turn on WhatsApp's own **Settings > Privacy > App lock**.
- Anything already running as your Windows user can read your fingerprints
  file or stop the agent. That's the same level of trust as the WhatsApp
  window itself.
- A normal webcam struggles in the dark. Raise the screen brightness or
  turn on a light.

For the Windows sign-in screen, set up a **Windows Hello PIN** (Settings >
Accounts > Sign-in options). It needs no special hardware.

## Development

```bash
pip install -e .[dev]
```

```bash
pytest
```

The tests use scripted faces, fake windows and a fake clock, so they need
no camera. `tests/test_models_real.py` loads the real models when they are
present (set `FACEGATE_DOWNLOAD_MODELS=1` to fetch them). CI runs everything
on `windows-latest` with Python 3.11 to 3.13.

```
src/facegate/
  engine.py     YuNet + SFace wrapper, match score
  liveness.py   head-turn check
  verify.py     enroll and verify loops (camera-agnostic)
  whatsapp.py   WhatsApp lock state machine + window sweep
  presence.py   walk-away lock
  agent.py      background agent, camera owner, crash supervisor
  winapi.py     the Win32 calls, via ctypes
  models.py     pinned model download
  store.py      enrolled fingerprints
  config.py     config.toml
  cli.py        `facegate` command
```

## License

MIT
