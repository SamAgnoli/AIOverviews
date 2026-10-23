"""Writes the full pipeline outputs for an external archive (OSF or Zenodo), gzip-compressed, with every question's
HealthBench text replaced by its q_index (its row in the study's question file, as in data/questions.csv).
Scraped page text and AI Overview text are kept. Needs the private inputs below.
Run from pipeline/:  python export_full_data.py
"""
import csv
import gzip
import json
import shutil
import sys
import unicodedata
from pathlib import Path

import openpyxl

RUN = Path("C:/Users/SAgno/Documents/AIO_Workspace/LocalRun")                     # research_output.csv, Output/
QFILE = Path("C:/Users/SAgno/Documents/AIO_Workspace/R_Analysis/healthbench_questions_AIO_1_flagged.xlsx")
HB = Path("C:/Users/SAgno/Documents/AIO_HealthBench_Claude/aio_healthbench")      # GPT-4.1 grading
OUT = Path("C:/Users/SAgno/Documents/AIOverviews_OSF")
csv.field_size_limit(2**31 - 1)


def norm(s):
    """Same key as norm_q() in R: NFKC, double quotes as single quotes (as the pipeline wrote them), squished spaces."""
    return " ".join(unicodedata.normalize("NFKC", s).replace('"', "'").split())


rows = openpyxl.load_workbook(QFILE, read_only=True).active.iter_rows(values_only=True)
next(rows)
Q_INDEX = {norm(str(r[0])): i for i, r in enumerate(rows, start=1)}
assert len(Q_INDEX) == 3852


def q_index(text):
    return Q_INDEX[norm(text)]


def export_csv(src, name):
    dst = OUT / (name + ".gz")
    with open(src, encoding="utf-8-sig", newline="") as f, gzip.open(dst, "wt", encoding="utf-8", newline="") as g:
        r, w = csv.reader(f), csv.writer(g)
        head = next(r)
        qi = head.index("question")
        keep = [i for i, h in enumerate(head) if "gispy" not in h]          # gist scores: not used in the paper
        w.writerow(["q_index" if i == qi else head[i] for i in keep])
        n = 0
        for row in r:
            row[qi] = q_index(row[qi])
            w.writerow([row[i] for i in keep])
            n += 1
    print(f"{name:32s} {n:7d} rows  {dst.stat().st_size / 1e6:8.1f} MB", flush=True)


def export_jsonl(src, name, drop=("question",)):
    dst = OUT / (name + ".gz")
    n = 0
    with open(src, encoding="utf-8") as f, gzip.open(dst, "wt", encoding="utf-8") as g:
        for line in f:
            d = json.loads(line)
            if "question" in d:
                d = {"q_index": q_index(d["question"]), **{k: v for k, v in d.items() if k not in drop}}
            g.write(json.dumps(d, ensure_ascii=False) + "\n")
            n += 1
    print(f"{name:32s} {n:7d} rows  {dst.stat().st_size / 1e6:8.1f} MB", flush=True)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    out = RUN / "Output"
    for f in ["comparison_questions.csv", "comparison_sources.csv", "statement_scores.csv", "statements_raw.csv",
              "organic_dataset.csv", "aio_dataset.csv"]:
        export_csv(out / f, f)
    export_csv(RUN / "research_output.csv", "research_output.csv")
    shutil.copy(out / "jama_hon_validation.csv", OUT / "jama_hon_validation.csv")
    export_jsonl(HB / "data" / "aio_responses.jsonl", "aio_responses.jsonl")
    # GPT-4.1's verdicts are in data/grades_gpt41.csv. Its explanations are left out: about a third of them quote
    # their HealthBench criterion.
    sys.exit(0)
