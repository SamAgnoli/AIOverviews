"""Print a random sample of grader verdicts with their rubric item and explanation, for a by-hand check.

    python scripts/show_verdicts.py --name pilot_gpt41 --n 10
Question and overview text are shortened. Keep the output local (HealthBench text must not be published).
"""
from __future__ import annotations

import argparse
import random
import textwrap

from common import config, load_deck, read_jsonl


def short(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + " ..."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    deck = {d["prompt_id"]: d for d in load_deck()}
    aio = {a["prompt_id"]: a for a in read_jsonl(config.AIO_JSONL)}
    grades = read_jsonl(config.RESULTS_DIR / f"grades_{args.name}.jsonl")
    met = sum(g["criteria_met"] for g in grades)
    print(f"{len(grades):,} verdicts; 'met' {met / len(grades):.0%}")
    for g in random.Random(config.SEED).sample(grades, min(args.n, len(grades))):
        d = deck[g["prompt_id"]]
        item = next(i for i in d["rubrics"] if i["item_idx"] == g["item_idx"])
        print("\n" + "=" * 100)
        print(f"QUESTION: {short(d['question'], 200)}")
        print(f"OVERVIEW: {short(aio[g['prompt_id']]['aio_text'], 300)}")
        print(f"RUBRIC [{item['points']:+g}, {item['axis']}]: {short(item['criterion'], 300)}")
        print(f"VERDICT: criteria_met = {g['criteria_met']}")
        print(textwrap.fill("WHY: " + short(g["explanation"], 600), 100))


if __name__ == "__main__":
    main()
