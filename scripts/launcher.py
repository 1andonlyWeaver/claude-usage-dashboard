"""
Launcher for the Claude usage dashboard server.
Intended to be run via pythonw.exe from Windows Task Scheduler at login.
Checks if port 8080 is already in use before starting, logs to logs/dashboard.log.
"""
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
LOG_FILE = REPO_DIR / "logs" / "dashboard.log"
PORT = 8080


def log(msg: str):
    LOG_FILE.parent.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} {msg}\n")


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


if port_in_use(PORT):
    log(f"Port {PORT} already in use — skipping startup.")
    sys.exit(0)

log("Starting dashboard server...")
# Use sys.executable (pythonw.exe when run via Task Scheduler) so the child process
# also has no console window. CREATE_NO_WINDOW suppresses any accidental console.
# On Windows 8+ nested jobs are supported, so CREATE_BREAKAWAY_FROM_JOB is not needed
# and can raise Access Denied when Task Scheduler's job disallows it.
try:
    proc = subprocess.Popen(
        [sys.executable, "app.py", "--port", str(PORT)],
        cwd=str(REPO_DIR),
        stdout=open(LOG_FILE, "a"),
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log(f"Dashboard process launched (PID {proc.pid}).")
except Exception as e:
    log(f"ERROR launching dashboard: {e}")
