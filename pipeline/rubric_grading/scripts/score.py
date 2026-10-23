"""Score each AI Overview against its HealthBench rubric and summarise.

    python scripts/score.py --name sonnet_as_sent
        reads   results/grades_sonnet_as_sent.jsonl, deck/rubric_deck.jsonl, data/aio_responses.jsonl
        writes  results/scores_sonnet_as_sent.csv   (one row per overview)
                results/summary_sonnet_as_sent.csv  (estimates with 95% CIs, overall and by subgroup)
                results/summary_sonnet_as_sent.md   (readable version of the main rows)

Scores use the official HealthBench formula: points earned / maximum positive points, per overview.
Per-overview scores can be negative; group means are clipped to [0, 1] as in simple-evals.
Only overviews with every rubric item graded are scored.
"""
from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd

from common import calculate_score, config, load_deck, read_jsonl

AXES = ["accuracy", "completeness", "context_awareness", "communication_quality", "instruction_following"]


def item_subset_score(items, met, keep):
    pts = [it["points"] for it, k in zip(items, keep) if k]
    mt = [m for m, k in zip(met, keep) if k]
    return calculate_score(pts, mt) if pts else None


def score_question(d, grades_by_idx):
    items = d["rubrics"]
    if any(it["item_idx"] not in grades_by_idx for it in items):
        return None
    met = [grades_by_idx[it["item_idx"]] for it in items]
    row = {
        "score_overall": calculate_score([it["points"] for it in items], met),
        # primary outcome: clinical content = accuracy + completeness items
        "score_clinical": item_subset_score(items, met, [it["axis"] in ("accuracy", "completeness") for it in items]),
        "score_excl_interaction": item_subset_score(items, met, [not it["interaction_dependent"] for it in items]),
        "score_consensus": item_subset_score(items, met, [it["level"] == "cluster" for it in items]),
    }
    for ax in AXES:
        row[f"score_{ax}"] = item_subset_score(items, met, [it["axis"] == ax for it in items])
    neg = [(it, m) for it, m in zip(items, met) if it["points"] < 0]
    neg_acc = [(it, m) for it, m in neg if it["axis"] == "accuracy"]
    row.update({
        "n_items": len(items),
        "n_negative_met": sum(m for _, m in neg),
        "negative_points_met": sum(it["points"] for it, m in neg if m),
        "n_negative_accuracy_met": sum(m for _, m in neg_acc),
        # harm flag: any physician-flagged inaccurate/harmful statement present
        "any_negative_accuracy_met": (any(m for _, m in neg_acc) if neg_acc else None),
    })
    em = [m for it, m in zip(items, met) if it["emergency_behavior"]]
    row["emergency_behavior_met"] = em[0] if em else None
    acc = [m for it, m in zip(items, met) if it["accuracy_check"]]
    row["accuracy_check_met"] = acc[0] if acc else None
    return row


def bootstrap_ci(values, reps, seed, clip=True):
    rng = np.random.default_rng(seed)
    v = np.asarray(values, dtype=float)
    means = rng.choice(v, size=(reps, len(v)), replace=True).mean(axis=1)
    if clip:
        means = np.clip(means, 0, 1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def wilson(k, n, z=1.96):
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return centre - half, centre + half


SCORE_COLS = ["score_overall", "score_clinical", "score_excl_interaction", "score_consensus"] + [f"score_{a}" for a in AXES]
BOOL_COLS = ["any_negative_accuracy_met", "emergency_behavior_met", "accuracy_check_met"]


def summarise(df, reps=config.BOOTSTRAP_REPS, seed=config.SEED):
    groupings = [("all", None), ("risk_level", "risk_level"), ("rubric_complexity", "rubric_complexity"),
                 ("turns", "turns"), ("theme", "theme"), ("physician_category", "physician_category")]
    out = []
    for gname, col in groupings:
        groups = [("all", df)] if col is None else list(df.groupby(col, dropna=True))
        for gval, g in groups:
            for c in SCORE_COLS:
                v = g[c].dropna()
                if len(v) == 0:
                    continue
                lo, hi = bootstrap_ci(v, reps, seed)
                out.append({"grouping": gname, "group": gval, "metric": c, "n": len(v),
                            "estimate": float(np.clip(v.mean(), 0, 1)), "ci_low": lo, "ci_high": hi,
                            "unclipped_mean": float(v.mean())})
            for c in BOOL_COLS:
                v = g[c].dropna().astype(bool)
                if len(v) == 0:
                    continue
                lo, hi = wilson(int(v.sum()), len(v))
                out.append({"grouping": gname, "group": gval, "metric": c, "n": len(v),
                            "estimate": float(v.mean()), "ci_low": lo, "ci_high": hi, "unclipped_mean": None})
    return pd.DataFrame(out)


def run(name, deck=None, aios=None):
    deck = deck or load_deck()
    grades = read_jsonl(config.RESULTS_DIR / f"grades_{name}.jsonl")
    aios = aios if aios is not None else (read_jsonl(config.AIO_JSONL) if config.AIO_JSONL.exists() else [])
    aio_by_id = {a["prompt_id"]: a for a in aios}
    by_q = {}
    for g in grades:
        by_q.setdefault(g["prompt_id"], {})[g["item_idx"]] = g["criteria_met"]
    rows, incomplete = [], 0
    for d in deck:
        if d["prompt_id"] not in by_q:
            continue
        s = score_question(d, by_q[d["prompt_id"]])
        if s is None:
            incomplete += 1
            continue
        a = aio_by_id.get(d["prompt_id"], {})
        rows.append({
            "prompt_id": d["prompt_id"], "q_index": d["q_index"], "risk_level": d["risk_level"],
            "rubric_complexity": d["rubric_complexity"], "theme": ";".join(d["themes"]),
            "physician_category": d["physician_category"],
            "turns": "multi" if d["has_assistant_turns"] else "single",
            "aio_chars": a.get("aio_chars"), "n_citations": a.get("n_citations"), **s,
        })
    df = pd.DataFrame(rows)
    df.to_csv(config.RESULTS_DIR / f"scores_{name}.csv", index=False)
    summ = summarise(df)
    summ.to_csv(config.RESULTS_DIR / f"summary_{name}.csv", index=False)
    main_rows = summ[summ["grouping"].isin(["all", "risk_level", "rubric_complexity", "turns"])]
    lines = [f"# Summary: {name}", "", f"Overviews scored: {len(df):,} (incomplete, skipped: {incomplete})", "",
             "| grouping | group | metric | n | estimate | 95% CI |", "|---|---|---|---|---|---|"]
    for r in main_rows.itertuples():
        lines.append(f"| {r.grouping} | {r.group} | {r.metric} | {r.n} | {r.estimate:.3f} | "
                     f"{r.ci_low:.3f}–{r.ci_high:.3f} |")
    (config.RESULTS_DIR / f"summary_{name}.md").write_text("\n".join(lines) + "\n")
    print(f"scored {len(df):,} overviews ({incomplete} incomplete skipped) -> results/scores_{name}.csv")
    return df, summ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    run(args.name)


if __name__ == "__main__":
    main()
