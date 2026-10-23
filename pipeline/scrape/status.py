r"""One-screen status for every stage of the pipeline.

    .\.venv\Scripts\python.exe status.py
    .\.venv\Scripts\python.exe status.py --watch        # refresh every 30s
    .\.venv\Scripts\python.exe status.py --watch --every 120

Read-only: it inspects on-disk artefacts and log files, so it is always safe to
run while anything else is going, including overnight.

Stages covered: scrape -> datasets/scoring -> GisPy -> Halloumi -> workbook -> R.
"""
import argparse, glob, io, os, re, sys, time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

HERE  = os.path.dirname(os.path.abspath(__file__))
OUT   = os.path.join(HERE, "Output")
RDIR  = r"C:\Users\SAgno\Documents\AIO_Workspace\R_Analysis"
W     = 34


def bar(done, total, width=W):
    if not total:
        return "·" * width
    done = max(0, min(done, total))
    f = int(width * done / total)
    return "█" * f + "░" * (width - f)


def human(sec):
    sec = int(max(0, sec))
    if sec >= 3600:
        return f"{sec // 3600}h {(sec % 3600) // 60}m"
    return f"{sec // 60}m {sec % 60}s" if sec >= 60 else f"{sec}s"


def age(path):
    if not os.path.exists(path):
        return None
    return time.time() - os.path.getmtime(path)


def fresh(path, window=180):
    """Has this file been written to recently (i.e. is its job alive)?"""
    a = age(path)
    return a is not None and a < window


def tqdm_progress(log_path):
    """Pull (done, total, rate_str, eta_str) out of a tqdm stderr log."""
    if not os.path.exists(log_path):
        return None
    try:
        txt = open(log_path, encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    hits = re.findall(r"(\d+)/(\d+) \[([0-9:]+)<([0-9:?]+),\s*([0-9.]+)([a-z/]+)\]",
                      txt.replace("\r", "\n"))
    if not hits:
        return None
    d, t, elapsed, eta, rate, unit = hits[-1]
    return int(d), int(t), f"{rate}{unit}", eta, elapsed


def line(label, text):
    print(f"  {label:<12} {text}")


def section(title):
    print(f"\n\033[1m{title}\033[0m" if sys.stdout.isatty() else f"\n{title}")


def report(show_quota=True):
    import pandas as pd
    print("\n" + "=" * 68)
    print(f"  AIO PIPELINE STATUS                {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 68)

    # ── 1. Scrape ───────────────────────────────────────────────────────────
    section("1. SCRAPE")
    try:
        total_q = len(pd.read_excel(os.path.join(HERE, "questions.xlsx")))
    except Exception:
        total_q = 0
    files = sorted(glob.glob(os.path.join(HERE, "research_output_batch*.csv")),
                   key=lambda p: int(re.search(r"batch(\d+)", p).group(1)))
    done_q = aio_yes = 0
    for f in files:
        try:
            d = pd.read_csv(f, encoding="utf-8-sig", low_memory=False)
        except Exception:
            continue
        if "error" in d.columns:
            bad = d["error"].astype(str).str.contains(
                r"serpapi_error|run out of searches|429", case=False, na=False)
            d = d[~bad]
        done_q += len(d)
        if "aio_present" in d.columns:
            aio_yes += int(d["aio_present"].fillna(False).astype(bool).sum())
    line("questions", f"{bar(done_q, total_q)}  {done_q}/{total_q}"
                      + (f"  ({100*done_q/total_q:.0f}%)" if total_q else ""))
    if done_q:
        line("with AIO", f"{aio_yes}/{done_q} ({100*aio_yes/done_q:.0f}%)")
    line("batches", f"{len(files)} file(s) on disk")
    running = any(fresh(p) for p in glob.glob(os.path.join(HERE, "logs", "*.log")))
    line("state", "RUNNING" if running else "idle")

    # ── 2. Datasets + rule-based scoring ────────────────────────────────────
    section("2. DATASETS & JAMA/HON")
    for name in ("aio_dataset.csv", "organic_dataset.csv", "statements_raw.csv"):
        p = os.path.join(OUT, name)
        if os.path.exists(p):
            try:
                n = len(pd.read_csv(p, encoding="utf-8-sig", low_memory=False))
            except Exception:
                n = "?"
            line(name.split("_")[0][:11], f"{n} rows   ({human(age(p))} ago)")
        else:
            line(name.split("_")[0][:11], "not built")

    # ── 3. GisPy ────────────────────────────────────────────────────────────
    section("3. GISPY")
    gp = os.path.join(OUT, "gispy_scores.csv")
    prog = tqdm_progress(os.path.join(HERE, "gispy.progress.log"))
    if os.path.exists(gp):
        try:
            n = len(pd.read_csv(gp, low_memory=False))
        except Exception:
            n = "?"
        line("scores", f"{n} documents   ({human(age(gp))} ago)")
        line("state", "DONE")
    elif prog:
        d, t, rate, eta, el = prog
        line("progress", f"{bar(d, t)}  {d}/{t}")
        line("rate", f"{rate}   elapsed {el}   eta {eta}")
        line("state", "RUNNING")
    else:
        line("state", "not started")

    # ── 4. Halloumi ─────────────────────────────────────────────────────────
    section("4. HALLOUMI")
    hp = os.path.join(OUT, "statement_scores.csv")
    prog = tqdm_progress(os.path.join(HERE, "halloumi.progress.log"))
    alive = fresh(os.path.join(HERE, "halloumi.progress.log"))
    if prog:
        d, t, rate, eta, el = prog
        line("progress", f"{bar(d, t)}  {d}/{t}")
        line("rate", f"{rate}   elapsed {el}   eta {eta}")
        line("state", "RUNNING" if alive and d < t else
                      ("DONE" if d >= t else "stalled/finished"))
    elif os.path.exists(hp):
        line("state", "DONE")
    else:
        line("state", "not started")
    if os.path.exists(hp):
        try:
            sd = pd.read_csv(hp, low_memory=False)
            line("statements", f"{len(sd)} scored")
            if "status" in sd.columns:
                vc = sd["status"].value_counts().to_dict()
                line("verdicts", ", ".join(f"{k}={v}" for k, v in vc.items()))
        except Exception:
            pass

    # ── 5. Workbook ─────────────────────────────────────────────────────────
    section("5. WORKBOOK")
    wb = os.path.join(OUT, "analysis_workbook.xlsx")
    if os.path.exists(wb):
        try:
            xl = pd.ExcelFile(wb)
            line("sheets", ", ".join(xl.sheet_names))
            have = {"GisPy Scores": "GisPy", "Statement Scores": "Halloumi"}
            missing = [v for k, v in have.items() if k not in xl.sheet_names]
            line("missing", ", ".join(missing) if missing else "nothing — all scores present")
        except Exception as e:
            line("sheets", f"unreadable: {e}")
        line("updated", f"{human(age(wb))} ago")
    else:
        line("state", "not built")

    # ── 6. R analysis ───────────────────────────────────────────────────────
    section("6. R ANALYSIS")
    # Was hardcoded to figures_101q/results_101q, so it kept reporting a stale
    # run's outputs after the analysis moved on to a new dataset. Glob every
    # figures_*/results_* directory and report the most recently written pair.
    fig_dirs = sorted(glob.glob(os.path.join(RDIR, "figures_*")))
    res_dirs = sorted(glob.glob(os.path.join(RDIR, "results_*")))
    figs = [p for d in fig_dirs for p in glob.glob(os.path.join(d, "*.png"))]
    res  = [p for d in res_dirs for p in glob.glob(os.path.join(d, "*.csv"))]
    if figs or res:
        newest_dir = min(
            (d for d in fig_dirs if glob.glob(os.path.join(d, "*.png"))),
            key=lambda d: min(age(p) for p in glob.glob(os.path.join(d, "*.png"))),
            default=None)
        if newest_dir:
            line("latest", os.path.basename(newest_dir))
    if figs or res:
        newest = max([age(p) for p in figs + res] or [0])
        line("outputs", f"{len(figs)} figures, {len(res)} tables")
        line("updated", f"{human(min([age(p) for p in figs + res]))} ago")
    else:
        line("state", "not run for this dataset")

    # ── 7. Quota ────────────────────────────────────────────────────────────
    if show_quota and os.environ.get("SERPAPI_KEY", "").strip():
        section("7. SERPAPI")
        try:
            import requests
            a = requests.get("https://serpapi.com/account",
                             params={"api_key": os.environ["SERPAPI_KEY"]},
                             timeout=15).json()
            left = a.get("total_searches_left", 0)
            line("quota", f"{left} of {a.get('searches_per_month')} left "
                          f"({a.get('plan_name')})")
            line("~questions", f"{int(left / 2.2)} more affordable (~2.2 searches each)")
        except Exception as e:
            line("quota", f"check failed: {e}")

    print("\n" + "=" * 68 + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--every", type=int, default=30)
    a = ap.parse_args()
    while True:
        try:
            report()
        except Exception as e:
            print(f"status error: {e}")
        if not a.watch:
            break
        time.sleep(a.every)
