r"""Validate the JAMA / HONcode rules against the human coding worksheet.

    .\.venv\Scripts\python.exe validate_jama_hon.py
    .\.venv\Scripts\python.exe validate_jama_hon.py --worksheet path\to\other.xlsx

Live-scrapes every URL in the worksheet's '_Human' sheet through the
notebook's own scrape_page (Step 5, with the page-quality signal extractor),
scores each page with health_scorers.py, and prints agreement with each coder
per variable plus a row-level table (mismatches starred). Writes
Output/jama_hon_validation.csv with the scraped signals and both score sets.

No SerpAPI calls. ~30 pages, a minute or two. Run this after changing the
scorer or the signal extractor, before a full local run.
"""
import argparse, io, json, os, re, sys, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DEFAULT_WS = r"C:\Users\SAgno\Documents\JAMA_HON_CodingWorksheet_LilyBrigidSam.xlsx"
VARS = ["JAMA_Authorship", "JAMA_Attribution", "JAMA_Disclosure",
        "JAMA_Currency", "HON_Authority", "HON_Complementarity"]
ABBR = {"JAMA_Authorship": "Auth", "JAMA_Attribution": "Attr",
        "JAMA_Disclosure": "Disc", "JAMA_Currency": "Curr",
        "HON_Authority": "HAut", "HON_Complementarity": "Comp"}
SKIP_URL_RE = re.compile(r"google\.com/searchviewer|facebook\.com", re.I)


def load_notebook_scraper():
    """Exec Steps 1, 3, 5 of the notebook into one namespace (same pattern as
    run_batch.py) so scrape_page and extract_quality_signals are the real ones."""
    nb = json.load(open(os.path.join(HERE, "AIO_WebsiteScrape_local.ipynb"),
                        encoding="utf-8"))
    cells = ["".join(c["source"]) for c in nb["cells"]]
    titles = [c.split("\n")[0] for c in cells]

    def cell_by(frag):
        hits = [i for i, t in enumerate(titles) if frag in t]
        assert len(hits) == 1, frag
        return hits[0]

    ns = {"__name__": "__main__"}
    for frag in ("STEP 1 —", "STEP 3 —", "STEP 5 —"):
        src = cells[cell_by(frag)]
        src = re.sub(r'^TEST_URL = "[^"]*"', 'TEST_URL = ""', src, flags=re.M)
        src = re.sub(r'^PROJECT_DIR\s*=\s*r?"[^"]*"',
                     lambda _m: f'PROJECT_DIR = r"{HERE}"', src, flags=re.M)
        exec(compile(src, f"<{frag}>", "exec"), ns, ns)
    return ns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worksheet", default=DEFAULT_WS)
    ap.add_argument("--limit", type=int, default=0, help="only the first N URLs")
    args = ap.parse_args()

    import pandas as pd
    pd.set_option("display.width", 250)

    from health_scorers import JAMAAnalyzer, HONcodeAnalyzer, SIG_COLS
    from comparison_builder import registered_domain
    jama, hon = JAMAAnalyzer(), HONcodeAnalyzer()

    human = pd.read_excel(args.worksheet, sheet_name="Coding Worksheet _Human")
    human = human[human["url"].notna()].copy()
    human["url"] = human["url"].astype(str).str.strip()
    human = human[~human["url"].str.contains(SKIP_URL_RE)]
    for v in VARS:
        human[v] = pd.to_numeric(human[v], errors="coerce")
    urls = list(dict.fromkeys(human["url"]))
    if args.limit:
        urls = urls[:args.limit]
    print(f"{len(urls)} unique URLs from {len(human)} human-coded rows "
          f"({', '.join(f'{c}={n}' for c, n in human['Coder'].value_counts().items())})")

    print("Loading notebook scraper (Steps 1, 3, 5)...")
    ns = load_notebook_scraper()
    scrape_page = ns["scrape_page"]

    # ── scrape + score ──────────────────────────────────────────────────
    scored = {}
    t0 = time.time()
    for i, u in enumerate(urls, 1):
        print(f"[{i:2d}/{len(urls)}] {u[:80]}")
        try:
            r = scrape_page(u)
        except Exception as e:
            print(f"      ✗ {type(e).__name__}: {str(e)[:80]}")
            r = {"url": u, "error": f"{type(e).__name__}: {e}"}
        cites = r.get("references") or []
        src_dom = registered_domain(u)
        ext_cite = any(
            c.get("url") and registered_domain(c["url"]) != src_dom
            for c in cites[:5] if isinstance(c, dict))
        kw = {
            "authors": r.get("authors", ""), "pub_date": r.get("pub_date", ""),
            "updated_date": r.get("updated_date", ""),
            "disclosures": r.get("disclosures", ""), "url": u,
            "external_citation": ext_cite,
            **{k: r.get(k, "") for k in SIG_COLS},
        }
        sc = {**jama.score(r.get("text", ""), **kw), **hon.score(r.get("text", ""), **kw)}
        scored[u] = {"fetch_method": r.get("fetch_method", ""),
                     "text_quality": r.get("text_quality", ""),
                     "text_len": len(r.get("text", "") or ""),
                     "authors": r.get("authors", ""),
                     "pub_date": r.get("pub_date", ""),
                     "updated_date": r.get("updated_date", ""),
                     "error": r.get("error", ""),
                     **{k: r.get(k, "") for k in SIG_COLS}, **sc}
        print(f"      {r.get('fetch_method', '')}/{r.get('text_quality', '')} "
              + " ".join(f"{ABBR[v]}={sc[v]}" for v in VARS))
    print(f"Scraped {len(urls)} pages in {time.time() - t0:.0f}s\n")
    try:
        ns["shutdown_playwright"]()
    except Exception:
        pass

    # ── agreement ───────────────────────────────────────────────────────
    rows = []
    for _, h in human.iterrows():
        s = scored.get(h["url"])
        if s is None:
            continue
        rows.append({"Coder": h["Coder"], "url": h["url"],
                     **{f"{v}_h": h[v] for v in VARS},
                     **{f"{v}_c": s[v] for v in VARS}})
    m = pd.DataFrame(rows)

    def agreement(sub, label):
        print(f"--- {label} ({len(sub)} rows)")
        tot_a = tot_n = 0
        for v in VARS:
            a, c = sub[f"{v}_h"], sub[f"{v}_c"]
            ok = a.notna()
            agree = int((a[ok] == c[ok]).sum()); n = int(ok.sum())
            fn = int(((a == 1) & (c == 0)).sum()); fp = int(((a == 0) & (c == 1)).sum())
            tot_a += agree; tot_n += n
            print(f"  {v:22s} agree={agree:2d}/{n:<2d}  human1={int((a[ok] == 1).sum()):2d} "
                  f"code1={int((c[ok] == 1).sum()):2d}  FN(h1,c0)={fn:2d} FP(h0,c1)={fp:2d}")
        print(f"  {'OVERALL':22s} {tot_a}/{tot_n} = {100 * tot_a / max(1, tot_n):.0f}%")

    agreement(m, "ALL CODERS")
    for coder in ("Sam", "Lily", "Brigid"):
        sub = m[m["Coder"] == coder]
        if len(sub):
            agreement(sub, coder)

    # ── row table ───────────────────────────────────────────────────────
    def cell(a, b):
        if pd.isna(a):
            return "  -  "
        return f"{int(a)}/{int(b)}" + ("*" if int(a) != int(b) else " ")
    tbl = pd.DataFrame([
        [str(r["Coder"])[:6],
         r["url"].replace("https://", "").replace("http://", "").replace("www.", "")[:58]]
        + [cell(r[f"{v}_h"], r[f"{v}_c"]) for v in VARS]
        for _, r in m.iterrows()],
        columns=["coder", "url  (human/code, * = mismatch)"] + [ABBR[v] for v in VARS])
    print("\n" + tbl.to_string(index=False))

    # ── save ────────────────────────────────────────────────────────────
    out_dir = os.path.join(HERE, "Output")
    os.makedirs(out_dir, exist_ok=True)
    out = pd.DataFrame([{"url": u, **s} for u, s in scored.items()])
    hm = (human.groupby("url")[VARS].mean(numeric_only=True)
          .rename(columns={v: f"{v}_human_mean" for v in VARS}).reset_index())
    out = out.merge(hm, on="url", how="left")
    p = os.path.join(out_dir, "jama_hon_validation.csv")
    out.to_csv(p, index=False, encoding="utf-8-sig")
    print(f"\n💾 {len(out)} rows → {p}")


if __name__ == "__main__":
    main()
