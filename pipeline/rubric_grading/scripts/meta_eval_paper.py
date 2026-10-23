"""Claude vs. physicians, reported the way HealthBench reports its grader (Arora et al., 2025, Section 8.1,
Tables 5 and 6). Run after meta_eval.py make -> batch.py -> parse_results.py:

    python scripts/meta_eval_paper.py --grader sonnet

On top of meta_eval.py score (which it leaves unchanged), this adds:
  - the paper's physician baseline: each physician scored against the other physicians on the rows they graded,
    averaged with weights = that physician's number of meta-examples (Table 5), on the same rows Claude graded;
  - the same by theme (the 34 consensus criteria grouped into the paper's seven themes), with Claude's MF1 as a
    percentile of the individual physicians' MF1 scores (Table 5, last column);
  - the physician baseline on the full meta-eval file (all rows; physician grades only, no API calls);
  - bootstrap 95% CIs (rows resampled) for Claude's MF1 and for Claude minus the weighted physician average.

Metric code is the simple-evals code in meta_eval.py. Writes results/meta_<grader>_paper.json and
results/meta_<grader>_paper_by_theme.csv.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import percentileofscore

from common import config, read_jsonl
from meta_eval import compute_metrics_for_rater_by_class, load_meta

# The 34 consensus criteria ("category") grouped into the seven HealthBench themes
THEMES = {
    "emergency_referrals": "Emergency referrals",
    "communication": "Expertise-tailored communication",
    "hedging": "Responding under uncertainty",
    "complex_responses": "Response depth",
    "health_data_tasks": "Health data tasks",
    "global_health": "Global health",
    "context_seeking": "Context seeking",
}
# Arora et al. (2025): Table 5 (physicians, weighted avg physician MF1, GPT-4.1 MF1, GPT-4.1 percentile) and Table 6
PAPER_TABLE5 = {
    "Emergency referrals": (40, 0.647, 0.662, 70.0),
    "Expertise-tailored communication": (66, 0.618, 0.610, 51.5),
    "Responding under uncertainty": (60, 0.640, 0.679, 68.3),
    "Response depth": (34, 0.569, 0.572, 58.8),
    "Health data tasks": (32, 0.730, 0.683, 37.5),
    "Global health": (34, 0.648, 0.706, 73.5),
    "Context seeking": (51, 0.646, 0.706, 88.2),
}
PAPER_TABLE6 = {"GPT-4.1": 0.709, "o4-mini": 0.692, "o3": 0.681, "GPT-4.1 mini": 0.661, "GPT-4.1 nano": 0.580}


def theme_of(category: str) -> str:
    c = category.removeprefix("cluster:")
    return next(name for prefix, name in THEMES.items() if c.startswith(prefix))


def model_mf1(preds, rows):
    m = compute_metrics_for_rater_by_class(preds, [r["binary_labels"] for r in rows],
                                           [r["category"] for r in rows], "model")
    return m.get("pairwise_model_f1_balanced", {}).get("value")


def physician_mf1s(rows):
    """Each physician against the other physicians on the rows they graded: arrays of (MF1, n meta-examples)."""
    per = defaultdict(lambda: ([], [], []))
    for r in rows:
        for i, lab in enumerate(r["binary_labels"]):
            pid = r["anonymized_physician_ids"][i]
            per[pid][0].append(lab)
            per[pid][1].append(r["binary_labels"][:i] + r["binary_labels"][i + 1:])
            per[pid][2].append(r["category"])
    vals, ns = [], []
    for s, o, c in per.values():
        v = compute_metrics_for_rater_by_class(s, o, c, "physician").get(
            "pairwise_physician_f1_balanced", {}).get("value")
        if v is not None:  # needs both classes; same rule as simple-evals
            vals.append(v)
            ns.append(len(s))
    return np.array(vals), np.array(ns)


def compare(preds, rows, reps, seed):
    vals, ns = physician_mf1s(rows)
    model = model_mf1(preds, rows)
    phys_w = float(np.average(vals, weights=ns))
    out = {
        "n_rows": len(rows),
        "n_physician_grades": int(sum(len(r["binary_labels"]) for r in rows)),
        "model_mf1": model,
        "physician_mf1_weighted": phys_w,
        "physician_mf1_mean": float(vals.mean()),
        "physician_mf1_iqr": [float(np.percentile(vals, 25)), float(np.percentile(vals, 75))],
        "n_physicians": int(len(vals)),
        "model_percentile_of_physicians": float(percentileofscore(vals, model, kind="mean")),
        "model_minus_physician_weighted": model - phys_w,
    }
    if reps:
        rng = np.random.default_rng(seed)
        bm, bd = [], []
        for _ in range(reps):
            idx = rng.integers(0, len(rows), len(rows))
            rr, pp = [rows[i] for i in idx], [preds[i] for i in idx]
            m = model_mf1(pp, rr)
            v, n = physician_mf1s(rr)
            if m is None or len(v) == 0:
                continue
            bm.append(m)
            bd.append(m - np.average(v, weights=n))
        out["model_mf1_ci95"] = [float(np.percentile(bm, 2.5)), float(np.percentile(bm, 97.5))]
        out["model_minus_physician_weighted_ci95"] = [float(np.percentile(bd, 2.5)), float(np.percentile(bd, 97.5))]
        out["bootstrap_reps"] = len(bm)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grader", required=True, choices=list(config.GRADERS))
    ap.add_argument("--reps", type=int, default=config.BOOTSTRAP_REPS)
    ap.add_argument("--name", default=None, help="run name (default meta_<grader>); e.g. meta_haiku_holdout")
    args = ap.parse_args()
    name = args.name or f"meta_{args.grader}"

    side = read_jsonl(config.REQUESTS_DIR / f"{name}.rows.jsonl")
    grades = {g["custom_id"]: g["criteria_met"] for g in read_jsonl(config.RESULTS_DIR / f"grades_{name}.jsonl")}
    rows = [s for s in side if s["custom_id"] in grades]
    preds = [grades[s["custom_id"]] for s in rows]
    full = load_meta()

    overall = compare(preds, rows, args.reps, config.SEED)
    full_vals, full_ns = physician_mf1s(full)
    overall["physician_mf1_weighted_full_file"] = float(np.average(full_vals, weights=full_ns))
    overall["n_physicians_full_file"] = int(len(full_vals))

    table = []
    for th in THEMES.values():
        ix = [i for i, r in enumerate(rows) if theme_of(r["category"]) == th]
        res = compare([preds[i] for i in ix], [rows[i] for i in ix], args.reps, config.SEED)
        fv, fn = physician_mf1s([r for r in full if theme_of(r["category"]) == th])
        p = PAPER_TABLE5[th]
        table.append({
            "theme": th, "n_rows": res["n_rows"], "n_physician_grades": res["n_physician_grades"],
            "claude_mf1": res["model_mf1"], "claude_mf1_ci_lo": res["model_mf1_ci95"][0],
            "claude_mf1_ci_hi": res["model_mf1_ci95"][1],
            "physicians_same_rows": res["n_physicians"],
            "physician_mf1_weighted_same_rows": res["physician_mf1_weighted"],
            "claude_minus_physician": res["model_minus_physician_weighted"],
            "diff_ci_lo": res["model_minus_physician_weighted_ci95"][0],
            "diff_ci_hi": res["model_minus_physician_weighted_ci95"][1],
            "claude_percentile_same_rows": res["model_percentile_of_physicians"],
            "physicians_full_file": int(len(fv)),
            "physician_mf1_weighted_full_file": float(np.average(fv, weights=fn)),
            "claude_percentile_vs_full_file_physicians": float(percentileofscore(fv, res["model_mf1"], kind="mean")),
            "paper_physicians": p[0], "paper_physician_mf1_weighted": p[1],
            "paper_gpt41_mf1": p[2], "paper_gpt41_percentile": p[3],
        })
    by_theme = pd.DataFrame(table)

    out = {
        "grader": args.grader,
        "model": config.GRADERS[args.grader]["model"],
        "n_rows_requested": len(side),
        "overall": overall,
        "decision_rule": "proceed to Stage 2 if model_mf1 >= physician_mf1_weighted on the same rows",
        "proceed": overall["model_mf1"] >= overall["physician_mf1_weighted"],
        "themes_at_or_above_physician_avg": int((by_theme["claude_minus_physician"] >= 0).sum()),
        "paper_table6_mf1": PAPER_TABLE6,
        "random_baseline_mf1": 0.50,
    }
    (config.RESULTS_DIR / f"{name}_paper.json").write_text(json.dumps(out, indent=2))
    by_theme.to_csv(config.RESULTS_DIR / f"{name}_paper_by_theme.csv", index=False)
    print(json.dumps({k: out[k] for k in ("model", "proceed", "themes_at_or_above_physician_avg")}, indent=2))
    print(json.dumps(overall, indent=2))
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(by_theme[["theme", "n_rows", "claude_mf1", "physician_mf1_weighted_same_rows", "claude_minus_physician",
                        "claude_percentile_same_rows", "paper_gpt41_mf1", "paper_physician_mf1_weighted"]]
              .round(3).to_string(index=False))
    print(f"-> results/{name}_paper.json, results/{name}_paper_by_theme.csv")


if __name__ == "__main__":
    main()
