"""Build grader requests: one per (AI Overview, rubric item), using the official HealthBench grader prompt.

Examples:
    # pilot: 50 random overviews, as-sent framing
    python scripts/make_requests.py --grader sonnet --limit-aios 50 --name pilot_sonnet

    # full run
    python scripts/make_requests.py --grader sonnet --framing as_sent

    # robustness: multi-turn questions graded with the original HealthBench conversation
    python scripts/make_requests.py --grader sonnet --framing original --subset multi_turn

    # retries for items whose output could not be parsed
    python scripts/make_requests.py --grader sonnet --only-cids results/failed_sonnet_as_sent.txt \
        --out requests/sonnet_as_sent__retry1.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from common import (build_grader_prompt, config, conversation_messages, load_deck, load_template,
                    make_cid, provider_request, read_jsonl, rubric_item_str)


def build_requests(grader, framing, deck, aios, subset="all", limit_aios=None, only_cids=None, seed=config.SEED):
    template = load_template()
    deck_by_id = {d["prompt_id"]: d for d in deck}
    aios = [a for a in aios if a["prompt_id"] in deck_by_id]
    if subset == "single_turn":
        aios = [a for a in aios if not deck_by_id[a["prompt_id"]]["has_assistant_turns"]]
    elif subset == "multi_turn":
        aios = [a for a in aios if deck_by_id[a["prompt_id"]]["has_assistant_turns"]]
    if limit_aios and limit_aios < len(aios):
        aios = random.Random(seed).sample(aios, limit_aios)
    reqs, n_chars = [], 0
    for a in aios:
        d = deck_by_id[a["prompt_id"]]
        msgs = conversation_messages(d, framing)
        for item in d["rubrics"]:
            cid = make_cid(d["prompt_id"], item["item_idx"], framing)
            if only_cids is not None and cid not in only_cids:
                continue
            prompt = build_grader_prompt(template, msgs, a["aio_text"], rubric_item_str(item))
            n_chars += len(prompt)
            reqs.append(provider_request(grader, cid, prompt))
    return reqs, len(aios), n_chars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grader", default="sonnet", choices=list(config.GRADERS))
    ap.add_argument("--framing", default="as_sent", choices=["as_sent", "original"])
    ap.add_argument("--subset", default="all", choices=["all", "single_turn", "multi_turn"])
    ap.add_argument("--limit-aios", type=int, default=None)
    ap.add_argument("--only-cids", default=None, help="text file of custom_ids to (re)build")
    ap.add_argument("--aio", default=str(config.AIO_JSONL))
    ap.add_argument("--name", default=None, help="run name (default: <grader>_<framing>)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    name = args.name or f"{args.grader}_{args.framing}" + ("" if args.subset == "all" else f"_{args.subset}")
    out = Path(args.out) if args.out else config.REQUESTS_DIR / f"{name}.jsonl"
    only = None
    if args.only_cids:
        only = {line.strip() for line in open(args.only_cids) if line.strip()}
    reqs, n_aios, n_chars = build_requests(args.grader, args.framing, load_deck(), read_jsonl(args.aio),
                                           args.subset, args.limit_aios, only)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in reqs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    est_in = n_chars / 4
    print(f"{len(reqs):,} requests for {n_aios:,} overviews -> {out}")
    print(f"~{est_in/1e6:.1f}M input tokens (chars/4 estimate); plus ~100 output tokens per request")


if __name__ == "__main__":
    main()
