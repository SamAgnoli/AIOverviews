"""Run OpenAI batch jobs in chunks small enough for the organisation's enqueued-token limit.

    python scripts/batch_openai_seq.py --names meta_gpt41 meta_gpt54_low --max-tokens 1000000

New OpenAI organisations can queue only a limited number of input tokens per model (1,350,000 for gpt-4.1 on this
account), so each request file is split into chunks of at most --max-tokens estimated input tokens (o200k
tokenizer). A run submits its next chunk only after the previous one has finished; different runs (models) proceed
in parallel. Results are saved to raw/<name>/ exactly as batch.py fetch saves them, so parse_results.py works
unchanged. State is kept in results/seqbatches_<name>.json, so the script can be stopped and re-run. A whole-file
batch already submitted with batch.py is reused if OpenAI accepted it (still running or finished).

If OpenAI stops a chunk (out of credit, rejected, expired or partly failed), the requests without a result are
queued again as a new chunk and the script exits with a message; add credit if needed and re-run the same
command. Finished chunks are never resent.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import tiktoken
import openai
from openai import OpenAI

from common import config, read_jsonl

ENC = tiktoken.get_encoding("o200k_base")
MAX_QUEUE_RETRIES = 20
MAX_RESEND_DEPTH = 2          # resends of requests that failed for reasons other than credit


def est_tokens(req):
    b = req["body"]
    return sum(len(ENC.encode(m["content"])) for m in (b.get("messages") or b.get("input"))) + 20


def state_path(name):
    return config.RESULTS_DIR / f"seqbatches_{name}.json"


def save(st):
    state_path(st["name"]).write_text(json.dumps(st, indent=2))


def plan(client, name, max_tokens):
    """Chunk the request file, or reuse an accepted whole-file batch from batch.py."""
    reqs = read_jsonl(config.REQUESTS_DIR / f"{name}.jsonl")
    st = {"name": name, "endpoint": reqs[0]["url"], "chunks": []}
    old = config.RESULTS_DIR / f"batches_{name}.json"
    if old.exists():
        for ob in json.loads(old.read_text())["batches"]:
            status = client.batches.retrieve(ob["batch_id"]).status
            if status not in ("failed", "cancelling", "cancelled"):
                print(f"{name}: reusing earlier batch {ob['batch_id']} ({status})")
                st["chunks"].append({"path": ob["chunk"], "n": ob["n"], "batch_id": ob["batch_id"],
                                     "fetched": False, "queue_retries": 0})
                return st
    chunk_dir = config.REQUESTS_DIR / f"{name}_seqchunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    cur, tok, k = [], 0, 0
    for r in reqs + [None]:
        t = est_tokens(r) if r else 0
        if cur and (r is None or tok + t > max_tokens):
            p = chunk_dir / f"chunk_{k:03d}.jsonl"
            p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in cur), encoding="utf-8")
            st["chunks"].append({"path": str(p), "n": len(cur), "est_tokens": tok, "batch_id": None,
                                 "fetched": False, "queue_retries": 0})
            cur, tok, k = [], 0, k + 1
        if r:
            cur.append(r)
            tok += t
    print(f"{name}: {len(reqs):,} requests in {len(st['chunks'])} chunks of at most {max_tokens:,} input tokens")
    return st


def download(client, b, out_dir):
    text = ""
    for fid in (b.output_file_id, b.error_file_id):
        if fid:
            text += client.files.content(fid).text
            if not text.endswith("\n"):
                text += "\n"
    (out_dir / f"batch_{b.id}.jsonl").write_text(text, encoding="utf-8")
    return text


def out_of_credit(text: str) -> bool:
    """Only ever pass OpenAI error text here: graded explanations can mention "billing" (medical-coding questions)."""
    t = text.lower()
    return "insufficient_quota" in t or "billing" in t or "credit balance" in t


def failed_lines(text: str) -> str:
    """The output lines that are not successful results (their error objects only)."""
    errs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        x = json.loads(line)
        r = x.get("response") or {}
        if x.get("error") or r.get("status_code") != 200:
            errs.append(json.dumps(x.get("error")) + json.dumps((r.get("body") or {}).get("error")))
    return " ".join(errs)


def requeue_missing(st, c, text, name):
    """Add a chunk with the requests of chunk c that have no successful result in text. Returns how many."""
    done = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        x = json.loads(line)
        r = x.get("response") or {}
        if not x.get("error") and r.get("status_code") == 200:
            done.add(x.get("custom_id"))
    lines = [l for l in Path(c["path"]).read_text(encoding="utf-8").splitlines(keepends=True)
             if l.strip() and json.loads(l)["custom_id"] not in done]
    if not lines:
        return 0
    k = len(st["chunks"])
    p = config.REQUESTS_DIR / f"{name}_seqchunks" / f"resend_{k:03d}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(lines), encoding="utf-8")
    st["chunks"].append({"path": str(p), "n": len(lines), "batch_id": None, "fetched": False,
                         "queue_retries": 0, "depth": c.get("depth", 0) + 1})
    return len(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", nargs="+", required=True)
    ap.add_argument("--max-tokens", type=int, default=1_000_000)
    ap.add_argument("--poll", type=int, default=60, help="seconds between status checks")
    args = ap.parse_args()

    client = OpenAI()
    runs = {}
    for name in args.names:
        sp = state_path(name)
        runs[name] = json.loads(sp.read_text()) if sp.exists() else plan(client, name, args.max_tokens)
        save(runs[name])

    while True:
        active = False
        for name, st in runs.items():
            pending = [c for c in st["chunks"] if not c["fetched"]]
            if not pending:
                continue
            active = True
            c, k = pending[0], st["chunks"].index(pending[0])
            label = f"{time.strftime('%H:%M')} {name} chunk {k + 1}/{len(st['chunks'])}"
            if c["batch_id"] is None:
                try:
                    with open(c["path"], "rb") as f:
                        up = client.files.create(file=f, purpose="batch")
                    b = client.batches.create(input_file_id=up.id, endpoint=st["endpoint"], completion_window="24h")
                except openai.APIStatusError as e:
                    why = "out of credit" if out_of_credit(str(e.message) + str(e.body)) else e.message
                    raise SystemExit(f"{label}: OpenAI refused the chunk ({why}). Progress is saved; "
                                     f"add credit if needed and re-run the same command.")
                c["batch_id"] = b.id
                save(st)
                print(f"{label}: submitted {c['n']:,} requests -> {b.id}", flush=True)
                continue
            b = client.batches.retrieve(c["batch_id"])
            rc = b.request_counts
            if b.status in ("completed", "expired"):
                out_dir = config.RAW_DIR / name
                out_dir.mkdir(parents=True, exist_ok=True)
                text = download(client, b, out_dir)
                c["fetched"] = True
                credit = out_of_credit(failed_lines(text))
                resend = requeue_missing(st, c, text, name) if (credit or c.get("depth", 0) < MAX_RESEND_DEPTH) else 0
                save(st)
                print(f"{label}: {b.status}, {rc.completed:,} done / {rc.failed:,} failed -> downloaded"
                      + (f"; {resend:,} queued to resend" if resend else ""), flush=True)
                if credit:
                    raise SystemExit(f"{label}: OpenAI ran out of credit partway through. Progress is saved; "
                                     f"add credit and re-run the same command ({resend:,} requests will be resent).")
            elif b.status in ("failed", "cancelled"):
                codes = [e.code for e in (b.errors.data if b.errors else [])]
                if "token_limit_exceeded" in codes and c["queue_retries"] < MAX_QUEUE_RETRIES:
                    c["batch_id"] = None
                    c["queue_retries"] += 1
                    save(st)
                    print(f"{label}: queue still full, will resubmit (try {c['queue_retries']})", flush=True)
                else:
                    msgs = [e.message for e in (b.errors.data if b.errors else [])]
                    c["batch_id"] = None              # resent on the next run
                    save(st)
                    why = "out of credit" if out_of_credit(" ".join(msgs) + " ".join(codes)) else msgs
                    raise SystemExit(f"{label}: OpenAI {b.status} the chunk ({why}). Progress is saved; "
                                     f"fix that (e.g. add credit) and re-run the same command.")
            else:
                print(f"{label}: {b.status} ({rc.completed:,}/{rc.total:,})", flush=True)
        if not active:
            break
        time.sleep(args.poll)
    print("all chunks downloaded")


if __name__ == "__main__":
    main()
