"""Start, pause and check the HallOumi scoring run.

    python halloumi_ctl.py start          run until finished (or paused)
    python halloumi_ctl.py start 10       run for at most 10 hours, then pause
    python halloumi_ctl.py pause          stop after the current statement
    python halloumi_ctl.py status         is it running, and how far along

Pausing loses nothing: every scored statement is appended to
Output/halloumi_checkpoint.jsonl, and `start` skips everything already in it.
Killing the process or rebooting is also safe (at most one statement is redone).

`start` launches through Task Scheduler so the run is not tied to the window or
session that started it. The double-click wrappers halloumi_start.cmd,
halloumi_pause.cmd and halloumi_status.cmd call this script.
"""
import os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
LOG = os.path.join(LOGS, "halloumi.log")
PID_FILE = os.path.join(LOGS, "halloumi.pid")
STOP_FLAG = os.path.join(LOGS, "HALLOUMI_STOP")
BAT = os.path.join(LOGS, "AIO_halloumi.bat")
TASK = "AIO_halloumi"
PY = os.path.join(HERE, ".venv", "Scripts", "python.exe")


def running_pid():
    """PID of a live HallOumi run, or None."""
    try:
        pid = int(open(PID_FILE).read().strip())
    except (OSError, ValueError):
        return None
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True).stdout
    return pid if "python" in out.lower() else None


def last_lines(n=400):
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return f.read().replace("\r", "\n").splitlines()[-n:]
    except OSError:
        return []


def status():
    pid = running_pid()
    lines = last_lines()
    prog = [l for l in lines if l.startswith("HALLOUMI checkpoint:")]
    ends = [l for l in lines if "HALLOUMI PAUSED" in l or "Halloumi scoring complete" in l
            or "FAILED" in l]
    if pid:
        state = "RUNNING (pid %d)" % pid
        if os.path.exists(STOP_FLAG):
            state += " - pause requested, stopping after the current statement"
    elif ends:
        state = "STOPPED - " + ends[-1].strip()[:200]
    else:
        state = "NOT RUNNING"
    print("HallOumi:", state)
    if prog:
        print("Progress:", prog[-1].replace("HALLOUMI checkpoint: ", ""))
    elif pid:
        print("Progress: loading model / data (first progress line after ~50 statements)")


def start(hours=None):
    if running_pid():
        print("HallOumi is already running."); status(); return
    if os.path.exists(STOP_FLAG):
        os.remove(STOP_FLAG)
    os.makedirs(LOGS, exist_ok=True)
    with open(BAT, "w") as f:
        f.write(f'cd /d "{HERE}"\n')
        f.write("set PYTHONIOENCODING=utf-8\n")
        f.write("set HF_HUB_DISABLE_IMPLICIT_TOKEN=1\n")
        f.write(f"set HALLOUMI_MAX_HOURS={hours or 0}\n")
        f.write(f'echo ==== start %DATE% %TIME% (max hours: {hours or "none"}) ==== >> "{LOG}"\n')
        f.write(f'"{PY}" run_gpu_scoring.py halloumi >> "{LOG}" 2>&1\n')
    # create + run + delete: a one-shot task that lives outside this session.
    # (Deleting the task does not stop the process it already started.)
    def sched(*args):
        r = subprocess.run(["schtasks", *args], capture_output=True, text=True)
        if r.returncode:
            print("schtasks", args[0], "failed:", (r.stderr or r.stdout).strip()[:200])
        return r.returncode == 0

    sched("/create", "/tn", TASK, "/tr", f'cmd.exe /c "{BAT}"',
          "/sc", "once", "/st", "23:58", "/f")
    started = sched("/run", "/tn", TASK)
    if not started:                      # one retry: /run occasionally races /create
        time.sleep(3)
        started = sched("/run", "/tn", TASK)
    time.sleep(5)
    sched("/delete", "/tn", TASK, "/f")
    for _ in range(45):                  # loading the 8B model takes a while
        if running_pid():
            break
        time.sleep(2)
    if not running_pid():
        print("Could not start HallOumi. Last log lines:")
        for l in last_lines(10):
            print("  ", l[:160])
        print(f"Try running this batch file directly: {BAT}")
        return
    print("Started HallOumi" + (f" (pauses on its own after {hours:g} h)" if hours else "")
          + f". Log: {LOG}")
    status()


def pause():
    pid = running_pid()
    if not pid:
        print("HallOumi is not running; nothing to pause."); return
    open(STOP_FLAG, "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))
    print("Pause requested. It stops after the current statement (usually under a minute);")
    print("progress is saved. Resume any time with: python halloumi_ctl.py start")


if __name__ == "__main__":
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if cmd == "start":
        start(float(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif cmd in ("pause", "stop"):
        pause()
    elif cmd == "status":
        status()
    else:
        print(__doc__)
