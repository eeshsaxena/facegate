import re
import uuid

from facegate import winapi


def test_current_user_sid_format():
    assert re.match(r"^S-1-\d+(-\d+)+$", winapi.current_user_sid())


def test_sam_name_has_a_backslash():
    assert "\\" in winapi.current_user_sam_name()


def test_named_event_across_handles():
    name = f"Local\\FaceGateTest-{uuid.uuid4()}"
    owner = winapi.NamedEvent(name)
    assert not owner.wait(0)
    other = winapi.NamedEvent(name, create=False)
    other.set()
    assert owner.wait(0)
    other.close()
    owner.close()


def test_single_instance():
    name = f"Local\\FaceGateTest-{uuid.uuid4()}"
    first = winapi.single_instance(name)
    assert first is not None
    assert winapi.single_instance(name) is None
    winapi.kernel32.CloseHandle(first)


def test_window_helpers_dont_crash_on_bad_handles():
    assert winapi.is_window(0) is False
    assert winapi.window_title(0) == ""
    assert winapi.process_name(0) == ""
    assert isinstance(winapi.session_locked(), bool)
