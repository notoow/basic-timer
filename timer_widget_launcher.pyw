import ctypes
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from tkinter import messagebox

from timer_widget import main


ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000


def claim_single_instance():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, True, "Local\\CodexTimerWidget")
    if not handle:
        return None
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if not messagebox.askyesno(
            "Timer Widget",
            "타이머 위젯이 이미 실행 중입니다.\n\n기존 인스턴스를 종료하고 새로 시작할까요?",
        ):
            kernel32.CloseHandle(handle)
            return None
        close_existing_instances()
        time.sleep(0.7)
        kernel32.CloseHandle(handle)
        handle = kernel32.CreateMutexW(None, True, "Local\\CodexTimerWidget")
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            messagebox.showerror("Timer Widget", "기존 타이머를 종료하지 못했습니다.")
            return None
    return handle


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
