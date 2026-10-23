r"""Progress of the whole run at a glance. Read-only; safe any time.

    .\.venv\Scripts\python.exe progress.py
    .\.venv\Scripts\python.exe progress.py --watch            # refresh every 60s
    .\.venv\Scripts\python.exe progress.py --no-quota         # skip the SerpAPI call

Sections: pipeline stage -> scrape (per question) -> SerpAPI searches -> GisPy.
Also writes the same text to logs/progress.txt.
"""
import argparse, glob, io, json, os, re, sys, time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
sys.path.insert(0, HERE)
W = 36

RETRY_RE = r"serpapi_error|question_exception|aio_prefetch_failed"


def bar(done, total, width=W):
    if not total:
        return "·" * width
    f = int(width * max(0, min(done, total)) / total)
    return "█" * f + "░" * (width - f)


def human(sec):
    sec = int(max(0, sec))
    if sec >= 3600:
        return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"
    return f"{sec // 60}m {sec % 60:02d}s"


def report(show_quota=True):
    import pandas as pd
    out = []
    p = out.append
    p("=" * 70)
    p(f"  AIO RUN PROGRESS                         {datetime.now():%Y-%m-%d %H:%M:%S}")
    p("=" * 70)

    # ── pipeline stage ──────────────────────────────────────────────────────
    st_path = os.path.join(LOGS, "pipeline_status.json")
    if os.path.exists(st_path):
        try:
            st = json.load(open(st_path, encoding="utf-8"))
            p(f"\n  Stage: {st.get('stage', '?')}   ({st.get('detail', '')})")
            for s in st.get("history", []):
                p(f"    {s['stage']:<22} {s.get('status', ''):<8} "
                  f"{s.get('started', '')[11:16]}–{(s.get('ended') or '')[11:16]}  "
                  f"{s.get('minutes', '')}")
        except Exception:
            pass

    # ── scrape ──────────────────────────────────────────────────────────────
    plan = {}
    try:
        plan = json.load(open(os.path.join(LOGS, "scrape_plan.json"), encoding="utf-8"))
    except Exception:
        pass
    total = plan.get("total") or 0

    files = sorted(glob.glob(os.path.join(HERE, "research_output_batch*.csv")),
                   key=lambda f: int(re.search(r"batch(\d+)", f).group(1)))
    ok_q, fail_q, aio_q, noaio_q = set(), set(), set(), set()
    first_t = last_t = None
    for f in files:
        try:
            d = pd.read_csv(f, encoding="utf-8-sig", low_memory=False,
                            usecols=lambda c: c in ("question", "error", "aio_present"))
        except Exception:
            continue
        mt = os.path.getmtime(f)
        first_t = min(first_t or mt, os.path.getctime(f))
        last_t = max(last_t or mt, mt)
        err = d["error"].fillna("").astype(str) if "error" in d else pd.Series("", index=d.index)
        bad = err.str.contains(RETRY_RE, case=False, regex=True)
        for q, b, e, a in zip(d["question"], bad, err,
                              d.get("aio_present", pd.Series(False, index=d.index))):
            if b:
                fail_q.add(q)
            else:
                ok_q.add(q)
                if str(a).lower() == "true":
                    aio_q.add(q)
                elif e == "no_aio":
                    noaio_q.add(q)
    fail_q -= ok_q
    done = len(ok_q)

    # searches affordable -> the realistic finish line for this plan
    target = total
    acct = None
    if show_quota:
        try:
            import serpapi_client
            acct = serpapi_client.account(timeout=15)
        except Exception:
            acct = None
    used = None
    per_q = 2.2
    if acct and not acct.get("error"):
        used = acct.get("this_month_usage")
        if done and used:
            per_q = max(1.5, used / max(1, done + len(fail_q)))
        left = acct.get("total_searches_left", 0)
        target = min(total, done + int(left / per_q)) if total else done

    p("\n  SCRAPE")
    if total:
        p(f"    all questions   {bar(done, total)} {done:>5}/{total} ({100*done/total:.1f}%)")
    if target and target != total:
        p(f"    this plan's cap {bar(done, target)} {done:>5}/{target} "
          f"(what the remaining searches can reach)")
    p(f"    AI Overview     {len(aio_q)} yes / {len(noaio_q)} none"
      + (f"  ({100*len(aio_q)/max(1,len(aio_q)+len(noaio_q)):.0f}% present)" if done else ""))
    p(f"    failed, queued  {len(fail_q)}   gave up: {plan.get('gave_up', 0)}   "
      f"batch files: {len(files)}")

    live = sorted(glob.glob(os.path.join(LOGS, "run_*.log")), key=os.path.getmtime)
    if live:
        txt = open(live[-1], encoding="utf-8", errors="replace").read()
        m = re.findall(r"Progress saved \((\d+)/(\d+)", txt)
        alive = time.time() - os.path.getmtime(live[-1]) < 300
        if m and alive:
            p(f"    current batch   {bar(int(m[-1][0]), int(m[-1][1]))} {m[-1][0]}/{m[-1][1]}")
        if "hourly budget reached" in txt[-4000:] and alive:
            p("    ⏳ paused for the SerpAPI hourly limit")

    calls = []
    try:
        now = time.time()
        calls = [float(x) for x in open(os.path.join(LOGS, "serpapi_calls.log"))
                 if x.strip()]
        last_hour = sum(1 for t in calls if now - t < 3600)
    except Exception:
        last_hour = None
    if first_t and last_t and done:
        el = time.time() - first_t
        rate = done / max(el, 1) * 3600
        remaining = max(0, target - done)
        p(f"    throughput      {rate:.0f} questions/hour   elapsed {human(el)}   "
          f"ETA to cap {human(remaining / max(rate, 1e-9) * 3600) if rate else '?'}")

    p("\n  SERPAPI")
    if acct and not acct.get("error"):
        p(f"    searches        {acct.get('this_month_usage')} used, "
          f"{acct.get('total_searches_left')} left of {acct.get('searches_per_month')} "
          f"({acct.get('plan_name')})")
        p(f"    per question    ~{per_q:.2f} searches   "
          f"~{int(acct.get('total_searches_left', 0) / per_q)} more questions affordable")
        p(f"    this hour       {acct.get('this_hour_searches')} of "
          f"{acct.get('account_rate_limit_per_hour')}"
          + (f"   (local log, rolling hour: {last_hour})" if last_hour is not None else ""))
    elif show_quota:
        p("    account check failed")

    # ── GisPy ───────────────────────────────────────────────────────────────
    ck = os.path.join(HERE, "Output", "gispy_checkpoint.csv")
    gp = os.path.join(LOGS, "gispy.log")
    if os.path.exists(ck) or os.path.exists(gp):
        p("\n  GISPY")
        try:
            txt = open(gp, encoding="utf-8", errors="replace").read().replace("\r", "\n")
            hits = re.findall(r"GIS checkpoint: (\d+)/(\d+) docs", txt)
            if hits:
                d_, t_ = map(int, hits[-1])
                p(f"    documents       {bar(d_, t_)} {d_}/{t_}")
        except Exception:
            pass

    p("=" * 70)
    text = "\n".join(out)
    print(text)
    try:
        with open(os.path.join(LOGS, "progress.txt"), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--every", type=int, default=60)
    ap.add_argument("--no-quota", action="store_true")
    a = ap.parse_args()
    while True:
        try:
            report(show_quota=not a.no_quota)
        except Exception as e:
            print(f"progress error: {e}")
        if not a.watch:
            break
        time.sleep(a.every)
