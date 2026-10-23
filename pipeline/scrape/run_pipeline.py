"""Unattended end-to-end run, everything EXCEPT Halloumi.

    .\.venv\Scripts\python.exe run_pipeline.py                 # all stages
    .\.venv\Scripts\python.exe run_pipeline.py --from gispy    # resume at a stage

Stages (each a separate subprocess, logged to logs/<stage>.log):
    scrape    run_all.py            SerpAPI + page scraping, batch by batch, merge
    datasets  run_analysis.py       Steps 9-13: datasets, JAMA, HON, comparison CSVs
    gispy     run_gpu_scoring.py    GisPy on the GPU, rebuilds CSVs + workbook
    r         R_Analysis/_r_full.R  paired tests, figures, tables (Halloumi skipped)

Halloumi is deliberately not a stage: run `run_gpu_scoring.py halloumi` later;
it only adds its own columns and rebuilds the workbook, so order is free.

Progress: logs/pipeline_status.json (read by progress.py).
"""
import argparse, io, json, os, subprocess, sys, time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, ".venv", "Scripts", "python.exe")
LOGS = os.path.join(HERE, "logs")
RDIR = os.path.normpath(os.path.join(HERE, "..", "R_Analysis"))
RSCRIPT = r"C:\Program Files\R\R-4.4.1\bin\Rscript.exe"
STATUS = os.path.join(LOGS, "pipeline_status.json")
os.makedirs(LOGS, exist_ok=True)

STAGES = [
    ("scrape",   [PY, os.path.join(HERE, "run_all.py")], HERE),
    ("datasets", [PY, os.path.join(HERE, "run_analysis.py")], HERE),
    ("gispy",    [PY, os.path.join(HERE, "run_gpu_scoring.py"), "gispy"], HERE),
    ("r",        [RSCRIPT, "_r_full.R"], RDIR),
]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_status():
    try:
        return json.load(open(STATUS, encoding="utf-8"))
    except Exception:
        return {"history": []}


def save_status(st):
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    os.replace(tmp, STATUS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="scrape",
                    choices=[s[0] for s in STAGES])
    a = ap.parse_args()
    names = [s[0] for s in STAGES]
    todo = STAGES[names.index(a.start):]

    env = dict(os.environ)
    env.setdefault("BATCH_SIZE", "50")
    env.setdefault("QUESTION_WORKERS", "4")
    env["PYTHONIOENCODING"] = "utf-8"
    # A stale OAuth token in ~/.cache/huggingface breaks even public model
    # downloads with a misleading RepositoryNotFoundError (seen 2026-08-26).
    env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

    st = load_status()
    st["started"] = st.get("started") or now()
    for name, cmd, cwd in todo:
        entry = {"stage": name, "status": "running", "started": now(), "ended": None}
        st["history"] = [h for h in st.get("history", []) if h["stage"] != name] + [entry]
        st["stage"], st["detail"] = name, "running"
        save_status(st)
        print(f"[{now()}] >>> stage {name}: {' '.join(cmd)}", flush=True)
        t0 = time.time()
        with open(os.path.join(LOGS, f"{name}.log"), "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {now()} =====\n")
            fh.flush()
            rc = subprocess.call(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, env=env)
        mins = round((time.time() - t0) / 60, 1)
        entry.update(status="ok" if rc == 0 else f"exit {rc}", ended=now(), minutes=f"{mins} min")
        save_status(st)
        print(f"[{now()}] <<< stage {name}: rc={rc} after {mins} min", flush=True)
        if rc != 0:
            st["stage"], st["detail"] = name, f"FAILED (exit {rc}) — see logs/{name}.log"
            save_status(st)
            sys.exit(rc)

    st["stage"], st["detail"] = "done", f"finished {now()} (Halloumi not run)"
    save_status(st)
    print(f"[{now()}] pipeline complete (Halloumi intentionally skipped)")


if __name__ == "__main__":
    main()
