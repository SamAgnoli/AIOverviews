"""Run ONE batch of the AIO scrape pipeline headlessly, then exit.

Executes the ported notebook's cells in a single namespace. Deliberately runs
one batch per PROCESS: process exit is the only way to guarantee every last
byte (Chromium subprocesses, parser arenas, cached page bodies) is returned to
the OS. run_all.py loops this.

Cell order is NOT notebook order. Steps 3-7 are executed BEFORE Step 2 so that
their built-in live "preview" blocks -- which are guarded on QUESTIONS /
RAW_RESULT existing -- skip themselves. That matters: Step 3's preview fires a
real SerpAPI search, which would burn one search of quota per batch for nothing.

One SerpAPI key (SERPAPI_KEY env var, else serpapi_key.txt). Resume is
per question (Step 2.5), so the batch can be shrunk to exactly what the
remaining quota affords -- no batch grid to corrupt.

Exit codes:  0 = batch done   3 = nothing left to do   4 = quota exhausted
"""
import io, json, os, re, sys, traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
NB = os.path.join(HERE, "AIO_WebsiteScrape_local.ipynb")

import serpapi_client

BATCH_SIZE       = int(os.environ.get("BATCH_SIZE", "50"))
QUESTION_WORKERS = int(os.environ.get("QUESTION_WORKERS", "4"))

if not serpapi_client.get_key():
    print("FATAL: no SerpAPI key (set SERPAPI_KEY or create serpapi_key.txt).")
    sys.exit(2)

# ── Pre-flight quota check ───────────────────────────────────────────────────
# Questions cost ~2.2 searches each (the AIO page_token prefetch is a second
# call). Instead of refusing a batch the key cannot fully afford, shrink it to
# what it can: per-question resume makes a short batch harmless.
SEARCHES_PER_QUESTION = 2.3
RESERVE_SEARCHES = 5
if os.environ.get("SKIP_QUOTA_PRECHECK", "").strip() != "1":
    try:
        acct = serpapi_client.account()
        if acct.get("error"):
            print(f"FATAL: SerpAPI account check failed: {acct['error']}")
            sys.exit(4)
        left = int(acct.get("total_searches_left", 0))
        affordable = int(max(0, left - RESERVE_SEARCHES) / SEARCHES_PER_QUESTION)
        print(f"Quota pre-flight: {left} searches left "
              f"(~{affordable} questions affordable); "
              f"{acct.get('this_hour_searches', '?')} used this hour "
              f"of {acct.get('account_rate_limit_per_hour', '?')}.")
        if affordable < 1:
            print("\n⚠️  NOT STARTING: SerpAPI searches exhausted for this plan period.")
            sys.exit(4)
        if affordable < BATCH_SIZE:
            print(f"  Shrinking this batch {BATCH_SIZE} -> {affordable} to fit the remaining quota.")
            BATCH_SIZE = affordable
    except SystemExit:
        raise
    except Exception as e:
        print(f"Quota pre-flight skipped ({e}) — proceeding.")

nb = json.load(open(NB, encoding="utf-8"))
cells = ["".join(c["source"]) for c in nb["cells"]]
titles = [c.split("\n")[0] for c in cells]

def cell_by(fragment):
    hits = [i for i, t in enumerate(titles) if fragment in t]
    assert len(hits) == 1, f"{fragment!r} matched {len(hits)} cells"
    return hits[0]

STEP1   = cell_by("STEP 1 —")
STEP2   = cell_by("STEP 2 —")
STEP2_5 = cell_by("STEP 2.5")
STEP3   = cell_by("STEP 3 —")
STEP4   = cell_by("STEP 4 —")
STEP5   = cell_by("STEP 5 —")
STEP6   = cell_by("STEP 6 —")
STEP7   = cell_by("STEP 7 —")
STEP8   = cell_by("STEP 8 —")


# Point the whole pipeline somewhere else (used by the smoke test) without
# editing the notebook.
PROJECT_DIR_OVERRIDE = os.environ.get("PROJECT_DIR_OVERRIDE", "").strip()
QUESTIONS_FILE_OVERRIDE = os.environ.get("QUESTIONS_FILE_OVERRIDE", "").strip()


def neutralise_previews(src):
    """Blank the live-test URLs so importing Steps 5/7 doesn't scrape samples."""
    src = re.sub(r'^TEST_URL = "[^"]*"', 'TEST_URL = ""', src, flags=re.M)
    src = re.sub(r'^TEST_FORUM_URL = "[^"]*"', 'TEST_FORUM_URL = ""', src, flags=re.M)
    if PROJECT_DIR_OVERRIDE:
        src = re.sub(r'^PROJECT_DIR\s*=\s*r?"[^"]*"',
                     f'PROJECT_DIR = r"{PROJECT_DIR_OVERRIDE}"', src, flags=re.M)
    if QUESTIONS_FILE_OVERRIDE:
        src = re.sub(r'^QUESTIONS_FILE\s*=\s*"[^"]*"',
                     f'QUESTIONS_FILE  = r"{QUESTIONS_FILE_OVERRIDE}"', src, flags=re.M)
    # Colab's 12.7GB forced QUESTION_WORKERS=2; this box has 32GB and the
    # unbounded-fetch bug is fixed, so parallelism is safe to raise here.
    src = re.sub(r'^    QUESTION_WORKERS = \d+',
                 f'    QUESTION_WORKERS = {QUESTION_WORKERS}', src, flags=re.M)
    return src


def run(idx, ns, label):
    print(f"\n{'=' * 70}\n>>> {label}: {titles[idx][:60]}\n{'=' * 70}", flush=True)
    exec(compile(neutralise_previews(cells[idx]), f"<cell{idx}>", "exec"), ns, ns)


ns = {"__name__": "__main__"}
if PROJECT_DIR_OVERRIDE:
    ns["PROJECT_DIR"] = PROJECT_DIR_OVERRIDE

try:
    run(STEP1, ns, "Step 1")
    # Steps 3-7 first: defines every scraper function while QUESTIONS is still
    # undefined, so each cell's preview block short-circuits (see module docstring).
    for i, lbl in ((STEP3, "Step 3"), (STEP4, "Step 4"), (STEP5, "Step 5"),
                   (STEP6, "Step 6"), (STEP7, "Step 7")):
        run(i, ns, lbl)

    run(STEP2, ns, "Step 2")
    if not ns.get("QUESTIONS"):
        print("FATAL: no questions loaded.")
        sys.exit(2)

    ns["BATCH_SIZE"] = BATCH_SIZE
    src2_5 = re.sub(r"^BATCH_SIZE\s*=\s*\d+", f"BATCH_SIZE = {BATCH_SIZE}",
                    neutralise_previews(cells[STEP2_5]), flags=re.M)
    print(f"\n{'=' * 70}\n>>> Step 2.5: batch plan (BATCH_SIZE={BATCH_SIZE})\n{'=' * 70}")
    exec(compile(src2_5, "<step2.5>", "exec"), ns, ns)

    if ns.get("CURRENT_BATCH_NUM") is None:
        print("\nNothing left to run — all questions scraped.")
        sys.exit(3)

    run(STEP8, ns, "Step 8")

except SystemExit:
    raise
except Exception:
    traceback.print_exc()
    sys.exit(1)

# ── quota check: did SerpAPI start refusing us? ──────────────────────────────
rows = ns.get("all_rows") or []
quota_hits = sum(1 for r in rows
                 if re.search(r"serpapi quota", str(r.get("error", "")), re.I))
if serpapi_client.quota_exhausted() or (rows and quota_hits >= max(3, len(rows) // 2)):
    print(f"\n⚠️  QUOTA EXHAUSTED — {quota_hits}/{len(rows)} rows hit the quota "
          f"(they are re-queued automatically for when searches are available).")
    sys.exit(4)

print(f"\nBatch {ns.get('CURRENT_BATCH_NUM')} finished: {len(rows)} rows.")
sys.exit(0)
