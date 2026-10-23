"""Run the Halloumi and/or GisPy scoring notebooks locally.

    python run_gpu_scoring.py halloumi
    python run_gpu_scoring.py gispy
    python run_gpu_scoring.py both

Each notebook is a 3-cell standalone: markdown, a pinned-deps install cell we
skip (the venv already has them), and one big scoring cell that we execute.

VRAM note: the notebooks were tuned for a Colab T4 (16GB). This box has an
RTX 2080 Ti (11GB, ~9.6GB free after the desktop), so CHUNK_TOKENS is lowered
by default. HalloumiScorer already retries on OutOfMemoryError with a smaller
window, so overshooting degrades rather than crashes -- but starting smaller
avoids burning time on doomed forward passes.
"""
import io, json, os, re, sys, time, traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CHUNK_TOKENS = int(os.environ.get("CHUNK_TOKENS", "1600"))

NOTEBOOKS = {
    "halloumi": "Halloumi_Scoring_local.ipynb",
    "gispy":    "GisPy_Scoring_local.ipynb",
}


def run_notebook(key):
    path = os.path.join(HERE, NOTEBOOKS[key])
    nb = json.load(open(path, encoding="utf-8"))
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    # cell 0 of the code cells is the pinned-deps installer -- skip it.
    scoring = code_cells[-1]
    src = "".join(scoring["source"])

    _pd = os.environ.get("PROJECT_DIR_OVERRIDE", "").strip()
    if _pd:
        src = re.sub(r'^PROJECT_DIR\s*=\s*r?"[^"]*"', f'PROJECT_DIR    = r"{_pd}"', src, flags=re.M)

    if key == "halloumi":
        src, n = re.subn(r"^CHUNK_TOKENS\s*=\s*\d+",
                         f"CHUNK_TOKENS   = {CHUNK_TOKENS}", src, flags=re.M)
        print(f"  CHUNK_TOKENS -> {CHUNK_TOKENS} ({n} substitution)")

    print(f"\n{'=' * 72}\n>>> {key.upper()} scoring\n{'=' * 72}", flush=True)
    t0 = time.time()
    ns = {"__name__": "__main__"}
    exec(compile(src, f"<{key}>", "exec"), ns, ns)
    print(f"\n{key} finished in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    which = (sys.argv[1] if len(sys.argv) > 1 else "both").lower()
    targets = list(NOTEBOOKS) if which == "both" else [which]
    for t in targets:
        if t not in NOTEBOOKS:
            print(f"unknown target {t!r}; pick from {list(NOTEBOOKS)} or 'both'")
            sys.exit(2)
    for t in targets:
        try:
            run_notebook(t)
        except Exception:
            traceback.print_exc()
            print(f"\n!! {t} FAILED")
            sys.exit(1)
    print("\nAll requested scoring completed.")
