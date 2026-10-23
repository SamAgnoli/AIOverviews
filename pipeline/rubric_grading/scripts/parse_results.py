"""Parse raw grader outputs into one verdict per request, and list the ones that need a retry.

    python scripts/parse_results.py --name sonnet_as_sent
        reads   raw/sonnet_as_sent/*.jsonl  and  requests/sonnet_as_sent.jsonl (expected ids)
        writes  results/grades_sonnet_as_sent.jsonl  and  results/failed_sonnet_as_sent.txt

A verdict is valid only if the output parses to JSON with a boolean "criteria_met" (same rule as
simple-evals, which retries until it gets one). Re-run after retries; earlier valid verdicts are kept.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from common import config, extract_text, parse_cid, parse_json_to_dict, read_jsonl, write_jsonl


def parse_run(name, requests_path=None):
    requests_path = Path(requests_path or config.REQUESTS_DIR / f"{name}.jsonl")
    expected = [r["custom_id"] for r in read_jsonl(requests_path)]
    raw_files = sorted((config.RAW_DIR / name).glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    grades, errors = {}, {}
    for rf in raw_files:
        for raw in read_jsonl(rf):
            cid = raw.get("custom_id")
            if cid is None or cid in grades:
                continue
            text, err = extract_text(raw)
            if text is None:
                errors[cid] = err
                continue
            d = parse_json_to_dict(text)
            met = d.get("criteria_met")
            if met is True or met is False:
                rec = {"custom_id": cid, "criteria_met": met, "explanation": d.get("explanation", "")}
                if not cid.startswith("meta"):
                    rec.update(parse_cid(cid))
                grades[cid] = rec
                errors.pop(cid, None)
            else:
                errors[cid] = "unparseable output: " + text[:200].replace("\n", " ")
    failed = [c for c in expected if c not in grades]
    out = config.RESULTS_DIR / f"grades_{name}.jsonl"
    write_jsonl(out, [grades[c] for c in expected if c in grades])
    fail_path = config.RESULTS_DIR / f"failed_{name}.txt"
    fail_path.write_text("\n".join(failed) + ("\n" if failed else ""))
    print(f"{len(grades):,}/{len(expected):,} valid verdicts -> {out}")
    print(f"{len(failed):,} need a retry -> {fail_path}")
    if failed:
        reasons = {}
        for c in failed:
            r = (errors.get(c) or "no output yet").split(":")[0]
            reasons[r] = reasons.get(r, 0) + 1
        print("reasons:", reasons)
    return grades, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--requests", default=None)
    args = ap.parse_args()
    parse_run(args.name, args.requests)


if __name__ == "__main__":
    main()
