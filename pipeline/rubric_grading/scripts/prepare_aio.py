"""Turn your AI Overview data into data/aio_responses.jsonl, joined to the rubric deck.

This is the one script that depends on how your AIO data is stored. Two ways in:

1) A table (CSV, XLSX or JSONL) with one row per question:
       python scripts/prepare_aio.py --input path/to/aios.csv --question-col question --aio-col aio_text
   Optional: --citations-col n_citations (kept for later analyses).

2) The scraper's research_output.csv (this study's file; one row per question):
       python scripts/prepare_aio.py --research-output <AIO_Workspace>/LocalRun/research_output.csv

3) Raw SerpAPI responses: adapt `load_serpapi_dir` below (Claude Code should check it against your files).
   `flatten_ai_overview()` turns SerpAPI's ai_overview.text_blocks into plain text.

Rows are matched to the deck by the exact question text (whitespace-normalised), which is how the
study questions were built. Overviews shorter than config.MIN_AIO_CHARS are excluded, as in the paper.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

from common import config, load_deck, norm_text, write_jsonl


# ------------------------------------------------------------------ text helpers
def flatten_ai_overview(ai_overview: dict) -> str:
    """Flatten a SerpAPI `ai_overview` object (text_blocks of paragraphs, headings, lists, tables).

    Keeps the visible text only (no reference links), lists as "- item" lines.
    """
    lines: list[str] = []

    def walk(blocks, depth=0):
        for b in blocks or []:
            t = b.get("type")
            title = (b.get("title") or "").strip()
            snippet = (b.get("snippet") or "").strip()
            if t == "list" or "list" in b:
                for li in b.get("list", []):
                    li_title = (li.get("title") or "").strip()
                    li_snip = (li.get("snippet") or "").strip()
                    txt = " ".join(x for x in [li_title, li_snip] if x)
                    if txt:
                        lines.append("  " * depth + "- " + txt)
                    if li.get("list"):
                        walk([{"type": "list", "list": li["list"]}], depth + 1)
                    if li.get("text_blocks"):
                        walk(li["text_blocks"], depth + 1)
            elif t == "table" and b.get("table"):
                for row in b["table"]:
                    lines.append(" | ".join(str(c) for c in row))
            else:
                txt = " ".join(x for x in [title, snippet] if x)
                if txt:
                    lines.append(txt)
            if b.get("text_blocks"):
                walk(b["text_blocks"], depth + 1)

    walk(ai_overview.get("text_blocks"))
    return clean_aio_text("\n".join(lines))


def clean_aio_text(s: str) -> str:
    """Remove URLs and bracketed citation markers; keep the answer text."""
    s = re.sub(r"https?://\S+", "", str(s))
    s = re.sub(r"\[\d+(?:[,–-]\s*\d+)*\]", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ------------------------------------------------------------------ loaders
def load_table(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p)
    if p.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(p)
    if p.suffix.lower() in (".jsonl", ".json"):
        return pd.read_json(p, lines=p.suffix.lower() == ".jsonl")
    raise ValueError(f"unsupported file type {p.suffix}")


def pipeline_norm(s) -> str:
    """The scraping pipeline stored each question NFKC-normalised with double quotes replaced by single quotes."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s)).replace('"', "'")).strip()


def load_research_output(path: str, deck=None) -> pd.DataFrame:
    """research_output.csv from the AIO scraper: question, aio_present, aio_text (full overview text).

    Questions are mapped back to the deck's exact text (undoing the pipeline's quote substitution). Overviews Google
    did not return (aio_present False) get empty text, so build() records them as excluded.
    """
    deck = deck or load_deck()
    by_norm = {pipeline_norm(d["question"]): d["question"] for d in deck}
    df = pd.read_csv(path, usecols=["question", "aio_present", "aio_text"], dtype=str, keep_default_na=False)
    df["question"] = df["question"].map(lambda q: by_norm.get(pipeline_norm(q), q))
    df.loc[df["aio_present"] != "True", "aio_text"] = ""
    return df


def load_serpapi_dir(directory: str) -> pd.DataFrame:
    """TEMPLATE: one SerpAPI JSON per question. Adjust field names to your files."""
    rows = []
    for f in sorted(Path(directory).glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        question = d.get("search_parameters", {}).get("q")  # the query string you sent
        aio = d.get("ai_overview") or {}
        rows.append({
            "question": question,
            "aio_text": flatten_ai_overview(aio) if aio else "",
            "n_citations": len(aio.get("references", []) or []),
            "source_file": f.name,
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ main
def build(df: pd.DataFrame, question_col: str, aio_col: str, citations_col: str | None,
          out_path=config.AIO_JSONL, min_chars=config.MIN_AIO_CHARS, deck=None):
    deck = deck or load_deck()
    by_q = {norm_text(d["question"]): d for d in deck}
    out, excluded, seen = [], [], set()
    for i, row in df.iterrows():
        q = row.get(question_col)
        d = by_q.get(norm_text(q)) if isinstance(q, str) else None
        if d is None:
            excluded.append({"row": i, "reason": "question not in deck", "question": str(q)[:200]})
            continue
        if d["prompt_id"] in seen:
            excluded.append({"row": i, "reason": "duplicate question", "question": str(q)[:200]})
            continue
        text = row.get(aio_col)
        text = clean_aio_text(text) if isinstance(text, str) else ""
        if len(text) < min_chars:
            excluded.append({"row": i, "reason": f"overview text < {min_chars} chars",
                             "question": str(q)[:200], "prompt_id": d["prompt_id"]})
            continue
        seen.add(d["prompt_id"])
        rec = {"prompt_id": d["prompt_id"], "q_index": d["q_index"], "question": d["question"],
               "aio_text": text, "aio_chars": len(text), "source_row": int(i)}
        if citations_col and citations_col in row and pd.notna(row[citations_col]):
            rec["n_citations"] = row[citations_col]
        out.append(rec)
    missing = [d for d in deck if d["prompt_id"] not in seen
               and not any(e.get("prompt_id") == d["prompt_id"] for e in excluded)]
    for d in missing:
        excluded.append({"row": None, "reason": "no row for this question", "question": d["question"][:200],
                         "prompt_id": d["prompt_id"]})
    write_jsonl(out_path, out)
    ex_path = Path(out_path).with_name("aio_exclusions.csv")
    pd.DataFrame(excluded).to_csv(ex_path, index=False)
    print(f"{len(out)} overviews written to {out_path}; {len(excluded)} exclusions in {ex_path}")
    if excluded:
        print(pd.DataFrame(excluded)["reason"].value_counts().to_string())
    return out, excluded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="CSV/XLSX/JSONL with one row per question")
    ap.add_argument("--research-output", help="the scraper's research_output.csv")
    ap.add_argument("--serpapi-dir", help="directory of raw SerpAPI JSON files (template loader)")
    ap.add_argument("--question-col", default="question")
    ap.add_argument("--aio-col", default="aio_text")
    ap.add_argument("--citations-col", default=None)
    ap.add_argument("--out", default=str(config.AIO_JSONL))
    args = ap.parse_args()
    if args.research_output:
        df = load_research_output(args.research_output)
        args.question_col, args.aio_col = "question", "aio_text"
    elif args.input:
        df = load_table(args.input)
    elif args.serpapi_dir:
        df = load_serpapi_dir(args.serpapi_dir)
        args.question_col, args.aio_col = "question", "aio_text"
        args.citations_col = args.citations_col or "n_citations"
    else:
        ap.error("give --research-output, --input or --serpapi-dir")
    build(df, args.question_col, args.aio_col, args.citations_col, out_path=args.out)


if __name__ == "__main__":
    main()
