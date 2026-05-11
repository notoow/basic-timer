import ctypes
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from tkinter import messagebox

from timer_widget import APP_TITLE, main


ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000
MUTEX_NAME = "Local\\CodexTimerWidget"
WINDOW_TITLES = (APP_TITLE, "Timer Complete", "Timer Settings")

SW_RESTORE = 9
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
MONITOR_DEFAULTTONEAREST = 2
ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def claim_single_instance():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not handle:
        return None
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if activate_existing_window():
            kernel32.CloseHandle(handle)
            return None
        if not messagebox.askyesno(
            "Timer Widget",
            "타이머 위젯이 이미 실행 중이지만 창을 찾지 못했습니다.\n\n기존 인스턴스를 종료하고 새로 시작할까요?",
        ):
            kernel32.CloseHandle(handle)
            return None
        close_existing_instances()
        time.sleep(0.7)
        kernel32.CloseHandle(handle)
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            messagebox.showerror("Timer Widget", "기존 타이머를 종료하지 못했습니다.")
            return None
    return handle


def activate_existing_window():
    hwnd = find_timer_window()
    if not hwnd:
        return False

    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, SW_RESTORE)
    keep_window_in_work_area(hwnd)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_SHOWWINDOW | SWP_NOMOVE | SWP_NOSIZE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True


def find_timer_window():
    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [ENUM_WINDOWS_PROC, ctypes.c_void_p]
    user32.EnumWindows.restype = ctypes.c_bool
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    matches = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title_length = user32.GetWindowTextLengthW(hwnd)
        if title_length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, buffer, title_length + 1)
        title = buffer.value.strip()
        if any(title == candidate or title.startswith(f"{candidate} -") for candidate in WINDOW_TITLES):
            matches.append(hwnd)
            return False
        return True

    enum_proc = ENUM_WINDOWS_PROC(callback)
    user32.EnumWindows(enum_proc, None)
    return matches[0] if matches else None


def keep_window_in_work_area(hwnd):
    user32 = ctypes.windll.user32
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = ctypes.c_bool
    user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = ctypes.c_bool
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = ctypes.c_bool

    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return

    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    monitor_info = MONITORINFO()
    monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
        return

    width = max(1, rect.right - rect.left)
    height = max(1, rect.bottom - rect.top)
    work = monitor_info.rcWork
    max_left = max(work.left, work.right - width)
    max_top = max(work.top, work.bottom - height)
    left = min(max(rect.left, work.left), max_left)
    top = min(max(rect.top, work.top), max_top)

    if left != rect.left or top != rect.top:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, left, top, width, height, SWP_SHOWWINDOW)


def close_existing_instances():
    current_pid = os.getpid()
    script_dir = str(Path(__file__).resolve().parent).replace("'", "''")
    command = f"""
$currentPid = {current_pid}
$scriptDir = '{script_dir}'
Get-CimInstance Win32_Process |
    Where-Object {{
        $_.ProcessId -ne $currentPid -and
        $_.Name -like 'python*.exe' -and
        $_.CommandLine -like "*$scriptDir*timer_widget*"
    }} |
    ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


if __name__ == "__main__":
    mutex_handle = claim_single_instance()
    if mutex_handle is None:
        sys.exit(0)

    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        log_path = Path(__file__).resolve().with_name("timer_widget_error.log")
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        messagebox.showerror("Timer Widget", f"실행 중 오류가 났습니다.\n\n{exc}\n\n{log_path}")
