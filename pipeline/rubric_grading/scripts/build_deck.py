"""Build the rubric deck: every HealthBench rubric item for every question in the study set.

Usage:
    python scripts/build_deck.py [--healthbench PATH_OR_URL] [--questions XLSX]

The deck in deck/ was already built from the official file; re-run only to reproduce it.
Matching rule: each study question equals the HealthBench conversation's user turns joined with " | "
(whitespace-normalised). All 3,852 questions match exactly one conversation.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import urllib.request
from pathlib import Path

import openpyxl
from openpyxl.styles import Font
import pandas as pd

from common import config, norm_text, write_jsonl

INTERACTION_CLUSTER_PAT = re.compile(r"(seeks_context|context_seeking)")
# Example-level criteria that reward or penalise asking the user for more information.
INTERACTION_TEXT_PAT = re.compile(
    r"(?<!user )(?<!not )(?<!is )(?<!was )(?<!they )"
    r"\b(asks? (the user|the patient|the parent|the caregiver|about|for|if|whether|what|how|when|which|them|you)"
    r"|asking (the user|the patient|about|for|if|whether)"
    r"|inquir(e|es|ing) (about|if|whether|into|regarding)"
    r"|clarif(y|ies|ying) (with|whether|if|what|the user)"
    r"|follow[- ]?up questions?"
    r"|(fails to|does not|doesn't|should not|avoids?) (ask|seek|inquire|clarify)\w*"
    r"|seeks? (the |more |additional |further |relevant |important |key |any )?"
    r"(context|information|details|clarification|history)"
    r"|(obtain|gather)s? (more |additional |further )?(context|information|details))\b",
    re.I,
)
ACCURACY_CHECK_PAT = re.compile(r"(hedging_.*_accurate$|complex_responses_.*_accuracy_hedging$)")


def load_healthbench(src: str) -> list[dict]:
    p = Path(src)
    if p.exists():
        data = p.read_bytes()
    else:
        print(f"Downloading {src} ...")
        with urllib.request.urlopen(src) as r:
            data = r.read()
        config.HEALTHBENCH_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        config.HEALTHBENCH_LOCAL.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    return rows, sha


def tag_value(tags, prefix):
    vals = [t.split(":", 1)[1] for t in tags if t.startswith(prefix + ":")]
    return vals[0] if vals else None


def main():
    ap = argparse.ArgumentParser()
    default_hb = str(config.HEALTHBENCH_LOCAL) if config.HEALTHBENCH_LOCAL.exists() else config.HEALTHBENCH_URL
    ap.add_argument("--healthbench", default=default_hb)
    ap.add_argument("--questions", default=str(config.QUESTIONS_XLSX))
    ap.add_argument("--out", default=str(config.ROOT / "deck"))
    args = ap.parse_args()

    hb, sha = load_healthbench(args.healthbench)
    by_text = collections.defaultdict(list)
    for ex in hb:
        joined = " | ".join(m["content"] for m in ex["prompt"] if m["role"] == "user")
        by_text[norm_text(joined)].append(ex)

    ws = openpyxl.load_workbook(args.questions, read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() for h in rows[0]]
    assert header[:3] == ["question", "risk_level", "rubric_complexity"], header

    flag_col = header.index("remove_flag") if "remove_flag" in header else None
    deck, flat, unmatched, n_removed = [], [], [], 0
    for q_index, row in enumerate(rows[1:], start=1):
        question, risk, complexity = row[:3]
        if flag_col is not None and row[flag_col] in (1, "1", True):
            n_removed += 1   # excluded from the study (health-data writing/admin tasks)
            continue
        matches = by_text.get(norm_text(question), [])
        if len(matches) != 1:
            unmatched.append((q_index, len(matches), str(question)[:120]))
            continue
        ex = matches[0]
        themes = [t.split(":", 1)[1] for t in ex["example_tags"] if t.startswith("theme:")]
        category = tag_value(ex["example_tags"], "physician_agreed_category")
        has_asst = any(m["role"] == "assistant" for m in ex["prompt"])
        items = []
        for i, r in enumerate(ex["rubrics"]):
            tags = r["tags"]
            axis = tag_value(tags, "axis")
            level = tag_value(tags, "level")
            cluster = tag_value(tags, "cluster")
            interaction = bool(
                (cluster and INTERACTION_CLUSTER_PAT.search(cluster))
                or (level == "example" and INTERACTION_TEXT_PAT.search(r["criterion"]))
            )
            item = {
                "item_idx": i,
                "criterion": r["criterion"],
                "points": r["points"],
                "tags": tags,
                "axis": axis,
                "level": level,
                "cluster": cluster,
                "polarity": "positive" if r["points"] > 0 else "negative",
                "interaction_dependent": interaction,
                "instruction_dependent": axis == "instruction_following",
                "emergency_behavior": bool(cluster and cluster.endswith("_emergency_behavior")),
                "accuracy_check": bool(cluster and ACCURACY_CHECK_PAT.search(cluster)),
            }
            items.append(item)
            flat.append({
                "q_index": q_index,
                "prompt_id": ex["prompt_id"],
                "item_id": f"{ex['prompt_id']}:{i:02d}",
                "risk_level": risk,
                "rubric_complexity": complexity,
                "theme": ";".join(themes),
                "physician_category": category,
                **{k: v for k, v in item.items() if k != "tags"},
                "tags": ";".join(tags),
            })
        deck.append({
            "q_index": q_index,
            "prompt_id": ex["prompt_id"],
            "question": question,                 # exactly as sent to Google
            "risk_level": risk,
            "rubric_complexity": complexity,
            "themes": themes,
            "physician_category": category,
            "example_tags": ex["example_tags"],
            "n_user_turns": sum(m["role"] == "user" for m in ex["prompt"]),
            "has_assistant_turns": has_asst,
            "prompt": ex["prompt"],               # original HealthBench conversation
            "rubrics": items,
        })

    if unmatched:
        print(f"WARNING: {len(unmatched)} questions did not match exactly one conversation:")
        for u in unmatched[:20]:
            print("  ", u)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "rubric_deck.jsonl", deck)
    flat_df = pd.DataFrame(flat)
    flat_df.to_csv(out / "rubric_items.csv", index=False)

    # ---------------- summary
    def cnt(series):
        return {str(k): int(v) for k, v in series.value_counts(dropna=False).items()}

    q_df = pd.DataFrame([{
        "q_index": d["q_index"], "prompt_id": d["prompt_id"], "question": d["question"],
        "risk_level": d["risk_level"], "rubric_complexity": d["rubric_complexity"],
        "theme": ";".join(d["themes"]), "physician_category": d["physician_category"],
        "n_user_turns": d["n_user_turns"], "has_assistant_turns": d["has_assistant_turns"],
        "n_items": len(d["rubrics"]),
        "n_positive": sum(i["points"] > 0 for i in d["rubrics"]),
        "n_negative": sum(i["points"] < 0 for i in d["rubrics"]),
        "max_points": sum(i["points"] for i in d["rubrics"] if i["points"] > 0),
        "n_accuracy": sum(i["axis"] == "accuracy" for i in d["rubrics"]),
        "n_completeness": sum(i["axis"] == "completeness" for i in d["rubrics"]),
        "n_interaction_dependent": sum(i["interaction_dependent"] for i in d["rubrics"]),
    } for d in deck])

    summary = {
        "healthbench_file_sha256": sha,
        "n_questions_in_xlsx": len(rows) - 1,
        "n_removed_by_flag": n_removed,
        "n_matched": len(deck),
        "n_unmatched": len(unmatched),
        "n_rubric_items": len(flat_df),
        "items_by_level": cnt(flat_df["level"]),
        "items_by_axis": cnt(flat_df["axis"]),
        "items_by_polarity": cnt(flat_df["polarity"]),
        "items_interaction_dependent": int(flat_df["interaction_dependent"].sum()),
        "items_instruction_dependent": int(flat_df["instruction_dependent"].sum()),
        "questions_with_assistant_turns": int(q_df["has_assistant_turns"].sum()),
        "questions_by_theme": cnt(q_df["theme"]),
        "questions_by_physician_category": cnt(q_df["physician_category"]),
        "questions_by_risk": cnt(q_df["risk_level"]),
        "questions_by_complexity": cnt(q_df["rubric_complexity"]),
        "items_per_question": {
            "min": int(q_df["n_items"].min()), "median": float(q_df["n_items"].median()),
            "max": int(q_df["n_items"].max()),
        },
    }
    (out / "deck_summary.json").write_text(json.dumps(summary, indent=2))

    # ---------------- readable workbook
    readme = pd.DataFrame({"About this workbook": [
        "Rubric deck: every physician-written HealthBench rubric item for each of the study's questions.",
        "Questions sheet: one row per question (q_index = row number in healthbench_questions_AIO_1.xlsx).",
        "Rubric_items sheet: one row per rubric item; item_id = prompt_id:item_idx.",
        "points > 0 = behaviour a good answer should show; points < 0 = behaviour to avoid.",
        "level 'example' = written for this conversation; level 'cluster' = shared consensus criterion.",
        "interaction_dependent (heuristic) = item rewards or penalises asking the user follow-up questions,"
        " which a search summary cannot do.",
        "emergency_behavior = consensus item judging whether in-person/emergency care was suggested appropriately.",
        "accuracy_check = consensus item requiring no factually incorrect information.",
        f"Source: HealthBench oss_eval.jsonl, sha256 {sha}.",
        "Machine-readable version used by the grading scripts: rubric_deck.jsonl.",
    ]})
    summary_rows = []
    for key in ["items_by_axis", "items_by_level", "items_by_polarity", "questions_by_theme",
                "questions_by_physician_category", "questions_by_risk", "questions_by_complexity"]:
        for k, v in summary[key].items():
            summary_rows.append({"breakdown": key, "category": k, "count": v})
    for key in ["n_matched", "n_rubric_items", "items_interaction_dependent", "items_instruction_dependent",
                "questions_with_assistant_turns"]:
        summary_rows.append({"breakdown": "totals", "category": key, "count": summary[key]})
    sheet_items = flat_df[["item_id", "q_index", "prompt_id", "risk_level", "rubric_complexity", "theme",
                           "physician_category", "points", "polarity", "axis", "level", "cluster",
                           "interaction_dependent", "instruction_dependent", "emergency_behavior",
                           "accuracy_check", "criterion"]]
    xlsx = out / "rubric_deck.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        readme.to_excel(xw, sheet_name="README", index=False)
        pd.DataFrame(summary_rows).to_excel(xw, sheet_name="Summary", index=False)
        q_df.to_excel(xw, sheet_name="Questions", index=False)
        sheet_items.to_excel(xw, sheet_name="Rubric_items", index=False)
        widths = {"README": {"A": 120}, "Summary": {"A": 34, "B": 44, "C": 10},
                  "Questions": {"C": 80, "F": 22, "G": 30},
                  "Rubric_items": {"A": 42, "F": 20, "G": 30, "L": 40, "Q": 100}}
        for name, ws_ in xw.sheets.items():
            ws_.freeze_panes = "A2"
            for col, w in widths.get(name, {}).items():
                ws_.column_dimensions[col].width = w
            for cell in ws_[1]:
                cell.font = Font(bold=True)
    print(json.dumps({k: summary[k] for k in ["n_matched", "n_unmatched", "n_rubric_items"]}))
    print(f"Wrote {out/'rubric_deck.jsonl'}, {out/'rubric_items.csv'}, {xlsx}, {out/'deck_summary.json'}")


if __name__ == "__main__":
    main()
