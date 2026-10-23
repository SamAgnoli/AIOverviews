"""Check a request file against the live API and estimate its Batch API cost before submitting.

    python scripts/preflight.py --requests requests/meta_sonnet.jsonl

Sends the first few requests synchronously (confirms the model id and parameters are accepted and the output
parses to a verdict), estimates input tokens (Anthropic: free token-counting endpoint on a random sample; OpenAI:
local o200k tokenizer on every request, scaled to the live calls' reported counts), and prints the estimated cost.
Exits 2 if the API rejects the request.
"""
from __future__ import annotations

import argparse
import random
import sys

from common import parse_json_to_dict, read_jsonl

# Batch API USD per million tokens (50% of standard), checked 2026-10-12; Haiku price is for prompts up to 100k
# tokens. OpenAI dated snapshots are priced as their model (pricing page lists gpt-4.1 and gpt-5.4).
BATCH_USD_PER_MTOK = {"claude-sonnet-5-5": {"input": 1.0, "output": 5.0},
                      "claude-haiku-5-5": {"input": 0.05, "output": 0.25},
                      "gpt-4.1-2025-04-14": {"input": 1.0, "output": 4.0},
                      "gpt-5.4-2026-03-05": {"input": 1.25, "output": 7.5}}


def chars(req):
    return len(req["params"]["system"]) + len(req["params"]["messages"][0]["content"])


def report(cid, model, stop, n_in, n_out, text):
    met = parse_json_to_dict(text or "").get("criteria_met")
    print(f"{cid}: model={model} stop={stop} in={n_in} out={n_out} criteria_met={met}")
    if met not in (True, False):
        print("  output did not parse to a verdict:", (text or "")[:300].replace("\n", " "))


def anthropic_estimate(reqs, live, sample_n):
    import anthropic
    client = anthropic.Anthropic()
    out_tok = []
    for r in reqs[:live]:
        try:
            msg = client.messages.create(**r["params"])
        except anthropic.APIStatusError as e:
            print(f"API rejected the request ({e.status_code}): {e.message}")
            sys.exit(2)
        text = "".join(c.text for c in msg.content if c.type == "text")
        out_tok.append(msg.usage.output_tokens)
        report(r["custom_id"], msg.model, msg.stop_reason, msg.usage.input_tokens, msg.usage.output_tokens, text)
    sample = random.Random(0).sample(reqs, min(sample_n, len(reqs)))
    counted = sum(client.messages.count_tokens(**{k: r["params"][k] for k in ("model", "system", "messages")})
                  .input_tokens for r in sample)
    est_in = sum(chars(r) for r in reqs) * counted / sum(chars(r) for r in sample)
    return reqs[0]["params"]["model"], est_in, len(reqs) * sum(out_tok) / len(out_tok)


def openai_estimate(reqs, live):
    import openai
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")

    def local_tokens(req):
        b = req["body"]
        msgs = b.get("messages") or b.get("input")
        return sum(len(enc.encode(m["content"])) for m in msgs)

    client = openai.OpenAI()
    out_tok, live_in, live_local = [], 0, 0
    for r in reqs[:live]:
        try:
            if r["url"] == "/v1/chat/completions":
                resp = client.chat.completions.create(**r["body"])
                text, n_in, n_out = resp.choices[0].message.content, resp.usage.prompt_tokens, resp.usage.completion_tokens
                stop = resp.choices[0].finish_reason
            else:
                resp = client.responses.create(**r["body"])
                text, n_in, n_out = resp.output_text, resp.usage.input_tokens, resp.usage.output_tokens
                stop = resp.status
        except openai.APIStatusError as e:
            print(f"API rejected the request ({e.status_code}): {e.message}")
            sys.exit(2)
        out_tok.append(n_out)  # includes reasoning tokens, which are billed as output
        live_in += n_in
        live_local += local_tokens(r)
        report(r["custom_id"], resp.model, stop, n_in, n_out, text)
    est_in = sum(local_tokens(r) for r in reqs) * live_in / live_local
    return reqs[0]["body"]["model"], est_in, len(reqs) * sum(out_tok) / len(out_tok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", required=True)
    ap.add_argument("--live", type=int, default=5, help="requests sent synchronously")
    ap.add_argument("--sample", type=int, default=100, help="requests token-counted (Anthropic)")
    args = ap.parse_args()

    reqs = read_jsonl(args.requests)
    if "params" in reqs[0]:
        model, est_in, est_out = anthropic_estimate(reqs, args.live, args.sample)
    else:
        model, est_in, est_out = openai_estimate(reqs, args.live)
    price = BATCH_USD_PER_MTOK[model]
    cost = est_in / 1e6 * price["input"] + est_out / 1e6 * price["output"]
    print(f"\n{len(reqs):,} requests: about {est_in / 1e6:.2f}M input + {est_out / 1e6:.2f}M output tokens "
          f"-> about ${cost:.2f} through the Batch API")


if __name__ == "__main__":
    main()
