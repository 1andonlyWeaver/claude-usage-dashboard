"""
Launcher for the Claude usage dashboard server.
Intended to be run via pythonw.exe from Windows Task Scheduler at login.
Checks if port 8080 is already in use before starting, logs to logs/dashboard.log.

The server is bound to a Windows Job Object (KILL_ON_JOB_CLOSE) so that whenever
this launcher exits — including when Task Scheduler stops the task — the OS tears
the server down with it. Without this, Stop-ScheduledTask kills only the launcher
and orphans the server holding port 8080, so the next Start sees the port in use
and silently no-ops.
"""
import ctypes
import socket
import subprocess
import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
LOG_FILE = REPO_DIR / "logs" / "dashboard.log"
PORT = 8080

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9


def log(msg: str):
    LOG_FILE.parent.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} {msg}\n")


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def bind_to_kill_on_close_job(proc) -> "wintypes.HANDLE | None":
    """Place ``proc`` in a Job Object that kills its members when the job's last
    handle closes. The launcher keeps the only handle, so when the launcher process
    dies the job closes and the server is terminated with it.

    Returns the job handle (which must stay alive — do not close it), or None if
    the binding could not be set up (in which case we fall back to the previous
    orphan-prone behaviour rather than failing to start the server).
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        if not kernel32.AssignProcessToJobObject(job, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        return job
    except OSError as e:
        log(f"WARNING: could not bind server to job object ({e}); "
            f"it may be orphaned if the task is stopped.")
        return None


if port_in_use(PORT):
    log(f"Port {PORT} already in use — skipping startup.")
    sys.exit(0)

log("Starting dashboard server...")
# Run app.py as a child of this pythonw.exe process. The launcher stays alive via
# proc.wait() so Task Scheduler can properly track lifecycle, enforce RestartCount,
# and honour IgnoreNew. No special creation flags needed — child inherits the
# windowless state from pythonw.exe.
proc = subprocess.Popen(
    [sys.executable, "app.py", "--port", str(PORT)],
    cwd=str(REPO_DIR),
    stdout=open(LOG_FILE, "a"),
    stderr=subprocess.STDOUT,
)
# Bind the child to a kill-on-close job so stopping this launcher (e.g. via
# Stop-ScheduledTask) reliably terminates the server instead of orphaning it.
# `_job` must remain referenced for the launcher's lifetime or the job closes early.
_job = bind_to_kill_on_close_job(proc)
log(f"Dashboard process launched (PID {proc.pid}; job={'yes' if _job else 'no'}).")
proc.wait()
log(f"Dashboard process exited (code {proc.returncode}).")
