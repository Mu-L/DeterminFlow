"""Scripts and process metrics used by the Executor pool load harness."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any


CPU_BUSY_SCRIPT = """\
import fcntl
import json
import os
import time
from pathlib import Path

task_id = os.environ["TASK_ID"]
busy = float(os.environ.get("DETERMINFLOW_CPU_BUSY_SECONDS", "0.15"))
log_path = os.environ.get("DETERMINFLOW_EXEC_LOG")
root = Path(os.environ["DETERMINFLOW_MARKER_DIR"])
root.mkdir(parents=True, exist_ok=True)


def _log(event):
    if not log_path:
        return
    record = json.dumps({
        "event": event,
        "task_id": task_id,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "ts": time.time(),
    }, ensure_ascii=False) + "\\n"
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, record.encode("utf-8"))
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


_log("start")
started_at = time.time()
cpu_started_at = time.process_time()
value = 0
while time.process_time() - cpu_started_at < busy:
    value = (value * 33 + 17) % 1000003
(root / task_id).write_text(json.dumps({
    "pid": os.getpid(),
    "ppid": os.getppid(),
    "started_at": started_at,
    "completed_at": time.time(),
    "value": value,
}), encoding="utf-8")
_log("complete")
print("<script_out>ok</script_out>")
"""

FAULT_HOLD_SCRIPT = """\
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

task_id = os.environ["TASK_ID"]
log_path = os.environ.get("DETERMINFLOW_EXEC_LOG")
root = Path(os.environ["DETERMINFLOW_HOLD_DIR"]) / task_id
root.mkdir(parents=True, exist_ok=True)


def _log(event):
    if not log_path:
        return
    record = json.dumps({
        "event": event,
        "task_id": task_id,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "ts": time.time(),
    }, ensure_ascii=False) + "\\n"
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, record.encode("utf-8"))
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


_log("start")
(root / "script.pid").write_text(str(os.getpid()), encoding="utf-8")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(180)"])
(root / "child.pid").write_text(str(child.pid), encoding="utf-8")
(root / "ready").write_text("1", encoding="utf-8")
try:
    release = root / "release"
    while not release.exists():
        time.sleep(0.05)
finally:
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=1)
_log("complete")
print("<script_out>released</script_out>")
"""


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _parse_ps_time(text: str) -> float:
    text = text.strip()
    days = 0.0
    if "-" in text:
        day_text, text = text.split("-", 1)
        days = float(day_text)
    parts = text.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return days * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if len(parts) == 2:
        minutes, seconds = parts
        return days * 86400 + int(minutes) * 60 + float(seconds)
    return days * 86400 + float(text)


def parse_pss_kb(smaps_text: str) -> int | None:
    total = 0
    found = False
    for line in smaps_text.splitlines():
        if line.startswith("Pss:"):
            parts = line.split()
            if len(parts) >= 2:
                total += int(parts[1])
                found = True
    return total if found else None


def _read_linux_pss_kb(pid: int) -> int | None:
    rollup = Path(f"/proc/{pid}/smaps_rollup")
    if rollup.is_file():
        try:
            parsed = parse_pss_kb(rollup.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            parsed = None
        if parsed is not None:
            return parsed
    smaps = Path(f"/proc/{pid}/smaps")
    if not smaps.is_file():
        return None
    try:
        return parse_pss_kb(smaps.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _read_linux_rss_cpu(pid: int) -> tuple[int | None, float | None]:
    rss_kb = None
    cpu_seconds = None
    status_path = Path(f"/proc/{pid}/status")
    if status_path.is_file():
        try:
            for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break
        except (OSError, ValueError):
            rss_kb = None
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.is_file():
        try:
            text = stat_path.read_text(encoding="utf-8", errors="replace")
            fields = text[text.rfind(")") + 2:].split()
            ticks = int(fields[11]) + int(fields[12])
            cpu_seconds = ticks / float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
        except (OSError, ValueError, IndexError, KeyError):
            cpu_seconds = None
    return rss_kb, cpu_seconds


def _read_ps_rss_cpu(pid: int) -> tuple[int | None, float | None]:
    try:
        listing = subprocess.run(
            ["ps", "-o", "pid=,rss=,time=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        ).stdout
    except OSError:
        return None, None
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            if int(parts[0]) != pid:
                continue
            return int(parts[1]), _parse_ps_time(parts[2])
        except (TypeError, ValueError):
            continue
    return None, None


def read_process_sample(pid: int) -> dict[str, Any]:
    if pid <= 0 or not _process_alive(pid):
        return {
            "pid": pid,
            "alive": False,
            "rss_kb": None,
            "pss_kb": None,
            "cpu_seconds": None,
        }
    rss_kb = None
    cpu_seconds = None
    pss_kb = None
    if sys.platform.startswith("linux"):
        pss_kb = _read_linux_pss_kb(pid)
        rss_kb, cpu_seconds = _read_linux_rss_cpu(pid)
    if rss_kb is None or cpu_seconds is None:
        ps_rss, ps_cpu = _read_ps_rss_cpu(pid)
        if rss_kb is None:
            rss_kb = ps_rss
        if cpu_seconds is None:
            cpu_seconds = ps_cpu
    return {
        "pid": pid,
        "alive": True,
        "rss_kb": rss_kb,
        "pss_kb": pss_kb,
        "cpu_seconds": None if cpu_seconds is None else round(cpu_seconds, 6),
    }


def linux_pss_available() -> bool:
    return sys.platform.startswith("linux") and Path("/proc/self/smaps_rollup").is_file()
