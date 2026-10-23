"""Send requests one by one (threaded) instead of through the Batch API.

Use for the pilot, the meta-evaluation sample, and retries. Output lines use the same format as batch
results, so parse_results.py treats them identically.

    python scripts/run_sync.py --requests requests/pilot_sonnet.jsonl --name pilot_sonnet
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from common import config, read_jsonl


def _client(provider):
    if provider == "openai":
        from openai import OpenAI
        return OpenAI()
    from anthropic import Anthropic
    return Anthropic()


def _dump(obj):
    return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj


def call_one(client, req, max_tries=6):
    delay = 2.0
    for attempt in range(max_tries):
        try:
            if "params" in req:  # anthropic
                msg = client.messages.create(**req["params"])
                return {"custom_id": req["custom_id"], "result": {"type": "succeeded", "message": _dump(msg)}}
            if req["url"] == "/v1/chat/completions":
                resp = client.chat.completions.create(**req["body"])
            else:
                resp = client.responses.create(**req["body"])
            return {"custom_id": req["custom_id"], "response": {"status_code": 200, "body": _dump(resp)},
                    "error": None}
        except Exception as e:  # rate limits, timeouts, transient server errors
            if attempt == max_tries - 1:
                err = {"message": repr(e)[:500]}
                if "params" in req:
                    return {"custom_id": req["custom_id"], "result": {"type": "errored", "error": err}}
                return {"custom_id": req["custom_id"], "response": None, "error": err}
            time.sleep(delay)
            delay = min(delay * 2, 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", required=True)
    ap.add_argument("--name", required=True, help="run name; output goes to raw/<name>/")
    ap.add_argument("--threads", type=int, default=config.SYNC_THREADS)
    args = ap.parse_args()

    reqs = read_jsonl(args.requests)
    if not reqs:
        print("no requests")
        return
    provider = "anthropic" if "params" in reqs[0] else "openai"
    client = _client(provider)
    out_dir = config.RAW_DIR / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"sync_{Path(args.requests).stem}_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    lock = threading.Lock()
    done = 0
    with open(out, "w", encoding="utf-8") as f, ThreadPoolExecutor(args.threads) as pool:
        futures = [pool.submit(call_one, client, r) for r in reqs]
        for fut in as_completed(futures):
            line = json.dumps(fut.result(), ensure_ascii=False)
            with lock:
                f.write(line + "\n")
                done += 1
                if done % 200 == 0 or done == len(reqs):
                    print(f"{done}/{len(reqs)}", flush=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
