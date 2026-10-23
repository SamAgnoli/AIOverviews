"""Shared helpers. Grading logic mirrors openai/simple-evals healthbench_eval.py (MIT licence,
see third_party/SIMPLE_EVALS_LICENSE.txt) so scores are comparable with published HealthBench numbers.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

FRAMING_CODES = {"as_sent": "S", "original": "O"}
CODE_FRAMINGS = {v: k for k, v in FRAMING_CODES.items()}


# ------------------------------------------------------------------ io
def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def norm_text(s: str) -> str:
    """Whitespace-normalised text, used to join questions across files."""
    return re.sub(r"\s+", " ", str(s)).strip()


def load_deck(path=None):
    return read_jsonl(path or config.DECK_JSONL)


def load_template() -> str:
    return Path(config.GRADER_TEMPLATE_PATH).read_text(encoding="utf-8")


# ------------------------------------------------------------------ prompts
def conversation_messages(deck_q: dict, framing: str) -> list[dict]:
    """The conversation the grader sees before the AIO's answer.

    as_sent  : exactly what Google received (user turns joined with " | ").
    original : the HealthBench conversation, including earlier assistant turns.
    Single-turn questions are identical under both framings.
    """
    if framing == "original" or not deck_q["has_assistant_turns"]:
        return deck_q["prompt"]
    if framing == "as_sent":
        return [{"role": "user", "content": deck_q["question"]}]
    raise ValueError(f"unknown framing {framing}")


def rubric_item_str(item: dict) -> str:
    # Same as RubricItem.__str__ in simple-evals: "[points] criterion" (points kept as in the source JSON)
    return f"[{item['points']}] {item['criterion']}"


def build_grader_prompt(template: str, messages: list[dict], response_text: str, item_str: str) -> str:
    convo = messages + [{"role": "assistant", "content": response_text}]
    convo_str = "\n\n".join(f"{m['role']}: {m['content']}" for m in convo)
    return template.replace("<<conversation>>", convo_str).replace("<<rubric_item>>", item_str)


def provider_request(grader_key: str, custom_id: str, prompt: str) -> dict:
    g = config.GRADERS[grader_key]
    if g["provider"] == "openai" and g["endpoint"] == "/v1/chat/completions":
        return {
            "custom_id": custom_id,
            "method": "POST",
            "url": g["endpoint"],
            "body": {
                "model": g["model"],
                "messages": [
                    {"role": "system", "content": g["system"]},
                    {"role": "user", "content": prompt},
                ],
                "temperature": g["temperature"],
                "max_tokens": g["max_tokens"],
            },
        }
    if g["provider"] == "openai" and g["endpoint"] == "/v1/responses":
        return {
            "custom_id": custom_id,
            "method": "POST",
            "url": g["endpoint"],
            "body": {
                "model": g["model"],
                "input": [{"role": "user", "content": prompt}],
                "reasoning": {"effort": g["reasoning_effort"]},
            },
        }
    if g["provider"] == "anthropic":
        params = {
            "model": g["model"],
            "max_tokens": g["max_tokens"],
            "system": g["system"],
            "messages": [{"role": "user", "content": prompt}],
        }
        if g.get("temperature") is not None:
            params["temperature"] = g["temperature"]
        if g.get("thinking"):
            params["thinking"] = g["thinking"]
        return {"custom_id": custom_id, "params": params}
    raise ValueError(f"unsupported grader config {grader_key}")


# ------------------------------------------------------------------ ids
def make_cid(prompt_id: str, item_idx: int, framing: str) -> str:
    """Batch custom_id. Anthropic allows [A-Za-z0-9_-]{1,64}; this is 37 characters."""
    return f"{prompt_id.replace('-', '')}_{item_idx:02d}_{FRAMING_CODES[framing]}"


def parse_cid(cid: str) -> dict:
    hexid, idx, code = cid.split("_")
    pid = f"{hexid[:8]}-{hexid[8:12]}-{hexid[12:16]}-{hexid[16:20]}-{hexid[20:]}"
    return {"prompt_id": pid, "item_idx": int(idx), "framing": CODE_FRAMINGS[code]}


# ------------------------------------------------------------------ parsing
def parse_json_to_dict(json_string: str) -> dict:
    """Identical to simple-evals: strip ```json fences and parse."""
    json_cleaned = re.sub(r"^```json\s*|\s*```$", "", json_string.strip())
    try:
        return json.loads(json_cleaned)
    except json.JSONDecodeError:
        return {}


def extract_text(raw: dict) -> tuple[str | None, str | None]:
    """Return (text, error) from one raw output line (OpenAI batch, Anthropic batch, or sync runner)."""
    # OpenAI batch / sync format
    if "response" in raw or ("error" in raw and "result" not in raw):
        if raw.get("error"):
            return None, json.dumps(raw["error"])[:500]
        resp = raw.get("response") or {}
        if resp.get("status_code") not in (200, None):
            return None, f"status {resp.get('status_code')}"
        body = resp.get("body") or {}
        if "choices" in body:  # chat completions
            try:
                return body["choices"][0]["message"]["content"], None
            except (KeyError, IndexError, TypeError):
                return None, "no choices"
        if "output" in body:  # responses API
            texts = []
            for out in body.get("output") or []:
                if out.get("type") == "message":
                    for c in out.get("content") or []:
                        if c.get("type") in ("output_text", "text"):
                            texts.append(c.get("text", ""))
            return ("".join(texts) or None), (None if texts else "no output_text")
        return None, "unrecognised openai body"
    # Anthropic batch / sync format
    if "result" in raw:
        res = raw["result"] or {}
        if res.get("type") != "succeeded":
            return None, f"anthropic result {res.get('type')}: {json.dumps(res.get('error'))[:300]}"
        msg = res.get("message") or {}
        texts = [c.get("text", "") for c in msg.get("content") or [] if c.get("type") == "text"]
        return ("".join(texts) or None), (None if texts else "no text block")
    return None, "unrecognised raw format"


# ------------------------------------------------------------------ scoring
def calculate_score(points: list[float], met: list[bool]) -> float | None:
    """Official HealthBench score: points achieved / total positive points (can be < 0 per example)."""
    total_possible = sum(p for p in points if p > 0)
    if total_possible == 0:
        return None
    achieved = sum(p for p, m in zip(points, met, strict=True) if m)
    return achieved / total_possible
