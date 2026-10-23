"""Drive the whole scrape: run batches back to back until done or out of quota.

Each batch is a SEPARATE subprocess, so memory is fully reclaimed between them
(the local equivalent of Colab's "Restart runtime"). Stops cleanly on quota
exhaustion; any question that hit the quota is re-queued by Step 2.5, so
re-running this after adding searches continues exactly where it stopped.

Single key: SERPAPI_KEY env var, else serpapi_key.txt (see serpapi_client.py).
"""
import io, os, subprocess, sys, time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
PY   = os.path.join(HERE, ".venv", "Scripts", "python.exe")
LOGS = os.path.join(HERE, "logs")
os.makedirs(LOGS, exist_ok=True)

MAX_BATCHES = int(os.environ.get("MAX_BATCHES", "1000"))
MAX_CONSECUTIVE_CRASHES = 3

DONE, NOTHING_LEFT, QUOTA_OUT = 0, 3, 4
started = time.time()

def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)

log(f"Starting. batch_size={os.environ.get('BATCH_SIZE', '50')} "
    f"workers={os.environ.get('QUESTION_WORKERS', '4')}")

crashes = 0
for i in range(1, MAX_BATCHES + 1):
    log(f"--- launching batch attempt {i} ---")
    # Name by wall-clock, NOT by attempt number: attempt numbering restarts at 1
    # on every invocation of this script, so a resumed run was overwriting the
    # logs of batches it had already completed.
    logfile = os.path.join(LOGS, f"run_{datetime.now():%Y%m%d_%H%M%S}.log")
    t0 = time.time()
    with open(logfile, "w", encoding="utf-8") as fh:
        rc = subprocess.call([PY, os.path.join(HERE, "run_batch.py")],
                             stdout=fh, stderr=subprocess.STDOUT, cwd=HERE)
    mins = (time.time() - t0) / 60

    if rc == DONE:
        crashes = 0
        log(f"batch attempt {i} OK in {mins:.1f} min -> {logfile}")
        continue
    if rc == NOTHING_LEFT:
        log(f"All questions scraped ({mins:.1f} min). Nothing left to run.")
        break
    if rc == QUOTA_OUT:
        log(f"STOPPED: SerpAPI searches exhausted ({mins:.1f} min).")
        log("Unscraped questions stay queued; rerun when searches are available.")
        break
    crashes += 1
    log(f"batch exited {rc} after {mins:.1f} min ({crashes} in a row). See {logfile}")
    if crashes >= MAX_CONSECUTIVE_CRASHES:
        log("STOPPED: too many consecutive batch crashes.")
        break
    time.sleep(30)   # per-question resume makes a straight retry safe

log(f"Total elapsed: {(time.time() - started) / 60:.1f} min")

# Merge whatever completed, so Step 9 has a current research_output.csv.
# Same rule as notebook Step 8.5: latest SUCCESSFUL row per question wins.
try:
    import glob, re, csv
    import pandas as pd
    files = sorted(glob.glob(os.path.join(HERE, "research_output_batch*.csv")),
                   key=lambda p: int(re.search(r"batch(\d+)", p).group(1)))
    if files:
        frames = [pd.read_csv(f, encoding="utf-8-sig", low_memory=False) for f in files]
        merged = pd.concat(frames, ignore_index=True, sort=False)
        del frames
        err = merged["error"].fillna("").astype(str) if "error" in merged.columns \
            else pd.Series("", index=merged.index)
        merged["_failed"] = err.str.contains(
            r"serpapi_error|question_exception|aio_prefetch_failed",
            case=False, regex=True).astype(int)
        merged["_order"] = range(len(merged))
        merged = (merged.sort_values(["_failed", "_order"], ascending=[True, False])
                        .drop_duplicates(subset="question", keep="first")
                        .sort_values("_order"))
        n_failed = int(merged["_failed"].sum())
        merged = merged.drop(columns=["_failed", "_order"])
        out = os.path.join(HERE, "research_output.csv")
        merged.to_csv(out, index=False, encoding="utf-8-sig",
                      quoting=csv.QUOTE_ALL, lineterminator="\n")
        got = int(merged.get("aio_present", pd.Series(dtype=bool))
                  .astype(str).str.lower().eq("true").sum())
        log(f"Merged {len(files)} batch file(s) -> {len(merged)} questions "
            f"({got} with an AI Overview, {n_failed} failed rows) -> {out}")
except Exception as e:
    log(f"Merge failed ({e}); batch files are intact, run Step 8.5 manually.")
