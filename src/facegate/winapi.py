"""The handful of Win32 calls FaceGate needs, through ctypes (no pywin32)."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("facegate.winapi only works on Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
secur32 = ctypes.WinDLL("secur32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

HWND = wintypes.HWND
DWORD = wintypes.DWORD
BOOL = wintypes.BOOL
HANDLE = wintypes.HANDLE

user32.GetForegroundWindow.restype = HWND
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
user32.GetWindowThreadProcessId.restype = DWORD
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindow.argtypes = [HWND]
user32.IsIconic.argtypes = [HWND]
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [HWND]
user32.LockWorkStation.restype = BOOL
user32.OpenInputDesktop.argtypes = [DWORD, BOOL, DWORD]
user32.OpenInputDesktop.restype = HANDLE
user32.CloseDesktop.argtypes = [HANDLE]
user32.GetUserObjectInformationW.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]
EnumProc = ctypes.WINFUNCTYPE(BOOL, HWND, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = [HWND, EnumProc, wintypes.LPARAM]

kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
kernel32.OpenProcess.restype = HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, DWORD, wintypes.LPWSTR, ctypes.POINTER(DWORD)]
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, BOOL, BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = HANDLE
kernel32.OpenEventW.argtypes = [DWORD, BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = HANDLE
kernel32.SetEvent.argtypes = [HANDLE]
kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
kernel32.WaitForSingleObject.restype = DWORD
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = HANDLE
kernel32.GetCurrentProcess.restype = HANDLE

advapi32.OpenProcessToken.argtypes = [HANDLE, DWORD, ctypes.POINTER(HANDLE)]
advapi32.GetTokenInformation.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]
advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
secur32.GetUserNameExW.argtypes = [ctypes.c_int, wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG)]

SW_HIDE, SW_MINIMIZE, SW_RESTORE, SW_SHOW = 0, 6, 9, 5
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DESKTOP_SWITCHDESKTOP = 0x0100
UOI_NAME = 2
WAIT_OBJECT_0 = 0
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
ERROR_ALREADY_EXISTS = 183


# --- windows -----------------------------------------------------------------

def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def window_pid(hwnd: int) -> int:
    pid = DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_path(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def process_name(pid: int) -> str:
    return os.path.basename(process_path(pid))


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def child_windows(hwnd: int) -> list[int]:
    found: list[int] = []

    @EnumProc
    def cb(child, _):
        found.append(child)
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return found


def app_process_name(hwnd: int) -> str:
    """Process name behind a window, looking through ApplicationFrameHost.

    Store (UWP) apps draw inside a frame owned by ApplicationFrameHost.exe;
    the real app lives in a child window from another process.
    """
    pid = window_pid(hwnd)
    name = process_name(pid)
    if name.lower() == "applicationframehost.exe":
        for child in child_windows(hwnd):
            cpid = window_pid(child)
            if cpid != pid:
                return process_name(cpid)
    return name


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def minimize(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_MINIMIZE)


def restore(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


# --- session -----------------------------------------------------------------

def lock_workstation() -> bool:
    return bool(user32.LockWorkStation())


def session_locked() -> bool:
    """True while the lock screen (or any secure desktop) is up."""
    desk = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not desk:
        return True
    try:
        buf = ctypes.create_unicode_buffer(256)
        needed = DWORD()
        if not user32.GetUserObjectInformationW(desk, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
            return False
        return buf.value.lower() != "default"
    finally:
        user32.CloseDesktop(desk)


# --- identity ----------------------------------------------------------------

def current_user_sid() -> str:
    TOKEN_QUERY, TokenUser = 0x0008, 1
    token = HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = DWORD()
        advapi32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TokenUser, buf, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


def current_user_sam_name() -> str:
    """DOMAIN\\user (for a local account, MACHINE\\user)."""
    NameSamCompatible = 2
    size = wintypes.ULONG(256)
    buf = ctypes.create_unicode_buffer(size.value)
    if not secur32.GetUserNameExW(NameSamCompatible, buf, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return buf.value


def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


# --- process signalling ------------------------------------------------------

class NamedEvent:
    """A manual-reset event other processes can set (used for `agent stop`)."""

    def __init__(self, name: str, create: bool = True):
        if create:
            self.handle = kernel32.CreateEventW(None, True, False, name)
        else:
            self.handle = kernel32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def set(self) -> None:
        kernel32.SetEvent(self.handle)

    def wait(self, seconds: float) -> bool:
        return kernel32.WaitForSingleObject(self.handle, int(seconds * 1000)) == WAIT_OBJECT_0

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


def single_instance(name: str) -> HANDLE | None:
    """Return a mutex handle, or None if another process already holds it."""
    ctypes.set_last_error(0)
    h = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(h)
        return None
    return h
