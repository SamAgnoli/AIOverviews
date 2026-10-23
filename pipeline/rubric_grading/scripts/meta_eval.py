"""Validate Claude against physicians using HealthBench's meta-evaluation set, the same way OpenAI
validated its grader (Arora et al., 2025, Section 8.1: GPT-4.1 macro-F1 = 0.709).

Step 1, build requests for a random sample of meta-eval rows (seeded exactly as simple-evals does):
    python scripts/meta_eval.py make --grader sonnet --n 3000
Step 2, run them (Batch API, half price; or run_sync.py for a quick look):
    python scripts/batch.py submit --requests requests/meta_sonnet.jsonl --name meta_sonnet
    python scripts/batch.py fetch --name meta_sonnet
    python scripts/parse_results.py --name meta_sonnet
Step 3, score agreement with physicians:
    python scripts/meta_eval.py score --grader sonnet

The metric functions below are copied from openai/simple-evals healthbench_meta_eval.py
(MIT licence, third_party/SIMPLE_EVALS_LICENSE.txt). The headline number is
pairwise_model_f1_balanced; the physician baseline is the mean of each physician's
pairwise_physician_f1_balanced against the other physicians on the same rows.
"""
from __future__ import annotations

import argparse
import json
import random
import urllib.request
from collections import defaultdict

import numpy as np

from common import build_grader_prompt, config, load_template, provider_request, read_jsonl, write_jsonl

INDEX_STR_TEMPLATE = "pairwise_{model_or_physician}_{metric}_{pred_str}"
CLUSTER_STR_TEMPLATE = "{cluster}: {index_str}"


# ------------------------------------------------------------------ copied from simple-evals
def compute_metrics_for_rater_by_class(self_pred_list, other_preds_list, cluster_list, model_or_physician):
    metric_lists = defaultdict(list)
    for self_pred, other_preds, cluster in zip(self_pred_list, other_preds_list, cluster_list, strict=True):
        self_pred_str = "pos" if self_pred else "neg"
        for other_pred in other_preds:
            precision_index_str = INDEX_STR_TEMPLATE.format(
                model_or_physician=model_or_physician, metric="precision", pred_str=self_pred_str)
            metric_lists[precision_index_str].append(self_pred == other_pred)
            metric_lists[CLUSTER_STR_TEMPLATE.format(cluster=cluster, index_str=precision_index_str)].append(
                self_pred == other_pred)
            other_pred_str = "pos" if other_pred else "neg"
            recall_index_str = INDEX_STR_TEMPLATE.format(
                model_or_physician=model_or_physician, metric="recall", pred_str=other_pred_str)
            metric_lists[recall_index_str].append(self_pred == other_pred)
            metric_lists[CLUSTER_STR_TEMPLATE.format(cluster=cluster, index_str=recall_index_str)].append(
                self_pred == other_pred)
    metrics = {}
    for index_str, metric_list in metric_lists.items():
        n = len(metric_list)
        metrics[index_str] = {"n": n, "value": sum(metric_list) / n if n > 0 else None}
    metrics.update(get_f1_metrics(metrics))
    metrics.update(get_balanced_metrics(metrics))
    return metrics


def get_f1_metrics(metrics):
    f1 = {}
    for pk in metrics:
        if "precision" in pk:
            rk = pk.replace("precision", "recall")
            if rk not in metrics:
                continue
            f1[pk.replace("precision", "f1")] = compute_f1_metric(metrics[pk], metrics[rk])
    return f1


def compute_f1_metric(precision, recall):
    pn, rn = precision["n"], recall["n"]
    pm, rm = precision["value"], recall["value"]
    if pm is None or rm is None:
        return {"n": pn + rn, "value": None}
    if pm == 0 and rm == 0:
        tp = pm * pn
        return {"n": pn + rn - tp, "value": 0.0}
    tp = pm * pn
    return {"n": pn + rn - tp, "value": 2 * (pm * rm) / (pm + rm)}


def get_balanced_metrics(metrics):
    bal = {}
    for pk in metrics:
        if "pos" in pk:
            nk = pk.replace("pos", "neg")
            if nk not in metrics:
                continue
            bal[pk.replace("pos", "balanced")] = compute_balanced_metric(metrics[pk], metrics[nk])
    return bal


def compute_balanced_metric(metric_pos, metric_neg):
    pv, nv = metric_pos["value"], metric_neg["value"]
    return {"n": metric_pos["n"] + metric_neg["n"],
            "value": None if pv is None or nv is None else (pv + nv) / 2}


# ------------------------------------------------------------------ steps
def load_meta():
    p = config.HEALTHBENCH_META_LOCAL
    if not p.exists():
        print(f"Downloading {config.HEALTHBENCH_META_URL} ...")
        p.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(config.HEALTHBENCH_META_URL) as r:
            p.write_bytes(r.read())
    return read_jsonl(p)


def make(args):
    rows = load_meta()
    idx = list(range(len(rows)))
    if args.n and args.n < len(rows):
        idx = random.Random(0).sample(idx, args.n)  # simple-evals: rng = random.Random(0); rng.sample(...)
    template = load_template()
    reqs, side = [], []
    for i in idx:
        r = rows[i]
        prompt = build_grader_prompt(template, r["prompt"], r["completion"], r["rubric"])
        cid = f"meta{i:06d}"
        reqs.append(provider_request(args.grader, cid, prompt))
        side.append({"custom_id": cid, "binary_labels": r["binary_labels"],
                     "anonymized_physician_ids": r["anonymized_physician_ids"], "category": r["category"]})
    name = f"meta_{args.grader}"
    write_jsonl(config.REQUESTS_DIR / f"{name}.jsonl", reqs)
    write_jsonl(config.REQUESTS_DIR / f"{name}.rows.jsonl", side)
    print(f"{len(reqs):,} meta-eval requests -> requests/{name}.jsonl")


def score(args):
    name = f"meta_{args.grader}"
    side = {s["custom_id"]: s for s in read_jsonl(config.REQUESTS_DIR / f"{name}.rows.jsonl")}
    grades = {g["custom_id"]: g["criteria_met"] for g in read_jsonl(config.RESULTS_DIR / f"grades_{name}.jsonl")}
    ex = [side[c] for c in side if c in grades]
    preds = [grades[e["custom_id"]] for e in ex]
    model = compute_metrics_for_rater_by_class(preds, [e["binary_labels"] for e in ex],
                                               [e["category"] for e in ex], "model")
    phys = defaultdict(lambda: ([], [], []))
    for e in ex:
        for i, lab in enumerate(e["binary_labels"]):
            pid = e["anonymized_physician_ids"][i]
            phys[pid][0].append(lab)
            phys[pid][1].append(e["binary_labels"][:i] + e["binary_labels"][i + 1:])
            phys[pid][2].append(e["category"])
    phys_f1 = []
    for pid, (s, o, c) in phys.items():
        m = compute_metrics_for_rater_by_class(s, o, c, "physician")
        v = m.get("pairwise_physician_f1_balanced", {}).get("value")
        if v is not None:
            phys_f1.append(v)
    by_cat = {k.split(": ")[0]: v["value"] for k, v in model.items()
              if k.endswith(": pairwise_model_f1_balanced")}
    out = {
        "grader": args.grader,
        "n_rows_scored": len(ex),
        "n_rows_requested": len(side),
        "model_f1_balanced": model["pairwise_model_f1_balanced"]["value"],
        "physician_f1_balanced_mean": float(np.mean(phys_f1)) if phys_f1 else None,
        "physician_f1_balanced_iqr": [float(np.percentile(phys_f1, 25)), float(np.percentile(phys_f1, 75))]
        if phys_f1 else None,
        "n_physicians": len(phys_f1),
        "model_f1_balanced_by_category": by_cat,
    }
    p = config.RESULTS_DIR / f"{name}.json"
    p.write_text(json.dumps(out, indent=2))
    print(json.dumps({k: out[k] for k in ["grader", "n_rows_scored", "model_f1_balanced",
                                           "physician_f1_balanced_mean"]}, indent=2))
    print(f"full results -> {p}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--grader", required=True, choices=list(config.GRADERS))
    m.add_argument("--n", type=int, default=3000)
    s = sub.add_parser("score")
    s.add_argument("--grader", required=True, choices=list(config.GRADERS))
    args = ap.parse_args()
    {"make": make, "score": score}[args.cmd](args)


if __name__ == "__main__":
    main()
