"""Run the post-scrape steps (9 -> 13) on whatever research_output.csv holds.

Local-only processing: no SerpAPI, no network scraping. Halloumi and GisPy live
in their own GPU notebooks and are NOT run here, so the workbook comes out
without them -- the R analysis skips those sections by design.

Step 9R is deliberately skipped: it re-scrapes pages and would hit the network.
"""
import io, json, os, re, sys, traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
nb = json.load(open(os.path.join(HERE, "AIO_WebsiteScrape_local.ipynb"),
                    encoding="utf-8"))
cells = ["".join(c["source"]) for c in nb["cells"]]
titles = [c.split("\n")[0] for c in cells]

WANT = ["STEP 9 —", "STEP 10 —", "STEP 11 —", "STEP 12 —", "STEP 13 —"]
ns = {"__name__": "__main__"}
PROJECT_DIR_OVERRIDE = os.environ.get("PROJECT_DIR_OVERRIDE", "").strip()

for frag in WANT:
    idx = next(i for i, t in enumerate(titles) if frag in t)
    print(f"\n{'=' * 72}\n>>> {titles[idx][:66]}\n{'=' * 72}", flush=True)
    src = cells[idx]
    if PROJECT_DIR_OVERRIDE:
        src = re.sub(r'^PROJECT_DIR\s*=\s*r?"[^"]*"',
                     f'PROJECT_DIR = r"{PROJECT_DIR_OVERRIDE}"', src, flags=re.M)
    try:
        exec(compile(src, f"<cell{idx}>", "exec"), ns, ns)
    except Exception:
        traceback.print_exc()
        print(f"\n!! {frag} FAILED — stopping.")
        sys.exit(1)

print("\nAll post-scrape steps completed.")
