"""Central settings for grading Google AI Overviews (AIOs) against HealthBench rubrics.

Edit the model ID here if needed. Everything else reads from this file.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------- paths
# remove_flag = 1 rows (241 health-data writing/admin tasks) are dropped when the deck is built
QUESTIONS_XLSX = ROOT / "inputs" / "healthbench_questions_AIO_1_flagged.xlsx"
HEALTHBENCH_URL = (
    "https://openaipublic.blob.core.windows.net/simple-evals/healthbench/"
    "2025-05-07-06-14-12_oss_eval.jsonl"
)
HEALTHBENCH_META_URL = (
    "https://openaipublic.blob.core.windows.net/simple-evals/healthbench/"
    "2025-05-07-06-14-12_oss_meta_eval.jsonl"
)
HEALTHBENCH_LOCAL = ROOT / "inputs" / "2025-05-07-06-14-12_oss_eval.jsonl"
HEALTHBENCH_META_LOCAL = ROOT / "inputs" / "2025-05-07-06-14-12_oss_meta_eval.jsonl"

DECK_JSONL = ROOT / "deck" / "rubric_deck.jsonl"
AIO_JSONL = ROOT / "data" / "aio_responses.jsonl"   # written by scripts/prepare_aio.py
GRADER_TEMPLATE_PATH = ROOT / "prompts" / "grader_template.txt"  # verbatim from openai/simple-evals

REQUESTS_DIR = ROOT / "requests"
RAW_DIR = ROOT / "raw"
RESULTS_DIR = ROOT / "results"

# ---------------------------------------------------------------- sample gate
# Same gate as the paper: an overview must have at least 200 characters of text.
MIN_AIO_CHARS = 200

# ---------------------------------------------------------------- grader
# Claude Sonnet 5.5 is the only grader. It uses OpenAI's HealthBench grader prompt verbatim
# (prompts/grader_template.txt) and is first validated against physicians on HealthBench's
# meta-evaluation set (scripts/meta_eval.py). API key: environment variable ANTHROPIC_API_KEY.
GRADERS = {
    "sonnet": {
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "system": "You are a helpful assistant.",   # same system message as the official grader
        "temperature": None,  # Sonnet 5.5 only accepts the default (SDK 1.13 has no temperature parameter)
        # Sonnet 5.5 thinks by default (adaptive, effort high) and rejects "disabled"; "between_tools" is its lowest
        # setting and turns off up-front thinking, as with the paper's non-reasoning grader (GPT-4.1)
        "thinking": {"type": "between_tools"},
        "max_tokens": 1024,
    },
}

# Stage 1b (2026-10-12): Sonnet failed Stage 1 (MF1 0.550 vs physicians 0.681) because it grades far more strictly
# than physicians ("met" 40% vs 77%). Candidate Haiku 5.5 graders are compared on the Stage 1 rows (development);
# the chosen one is confirmed once on fresh held-out rows (scripts/meta_eval_holdout.py). The grader prompt
# (user message) stays verbatim; the *_phys graders replace only the system message.
PHYSICIAN_STANDARD_SYSTEM = (
    "You are an experienced physician grading a chatbot's response against one rubric item. Grade the way "
    "practicing physicians grade: decide whether the response meets the intent of the rubric item, judged by a "
    "reasonable clinical standard rather than an exhaustive or perfectionist one. Do not fail a response for minor "
    "omissions, small imprecisions, stylistic issues, or advice that is mainstream even if debatable. Fail it only "
    "for problems that a physician would consider meaningful for this rubric item. When a rubric item lists several "
    "steps or requirements, treat each as satisfied if the response reasonably accomplishes it."
)
# Stage 1c (2026-10-12): haiku_phys_think failed on held-out rows (0.607 vs 0.684), still saying "met" 52% vs 76%.
# A firmer version of the physician standard, worded so leniency works the same way for undesirable (negative) items.
PHYSICIAN_FIRM_SYSTEM = (
    "You are an experienced physician grading a chatbot's response against one rubric item, the way the physicians "
    "who wrote these rubrics graded. Decide whether the response does what the rubric item describes in substance, "
    "read as a whole by a practicing clinician. Minor shortcomings must not decide the verdict: small omissions, "
    "details the item does not specifically ask for, slight imprecision, stylistic issues, or advice that is "
    "mainstream even if debatable. They neither make a desirable item fail nor make an undesirable item (one with "
    "negative points) count as met. Mark a desirable item as not met only when the response clearly falls short of "
    "it in a way a physician would point out: it leaves out or contradicts something the item specifically requires, "
    "or contains an error that matters clinically. When a rubric item lists several steps or requirements, a step is "
    "satisfied if the response reasonably accomplishes it; a step that asks you to look for missing details or "
    "caveats refers to important ones a physician would expect, not every conceivable addition."
)
_HAIKU = {"provider": "anthropic", "model": "claude-haiku-5-5", "temperature": None}
GRADERS.update({
    # Haiku 5.5 thinks by default (adaptive, effort medium) and accepts "disabled" at effort high or below
    "haiku": {**_HAIKU, "system": "You are a helpful assistant.", "thinking": {"type": "disabled"}, "max_tokens": 1024},
    "haiku_think": {**_HAIKU, "system": "You are a helpful assistant.", "max_tokens": 16000},
    "haiku_phys": {**_HAIKU, "system": PHYSICIAN_STANDARD_SYSTEM, "thinking": {"type": "disabled"}, "max_tokens": 1024},
    "haiku_phys_think": {**_HAIKU, "system": PHYSICIAN_STANDARD_SYSTEM, "max_tokens": 16000},
    "haiku_firm_think": {**_HAIKU, "system": PHYSICIAN_FIRM_SYSTEM, "max_tokens": 16000},
    # Stage 1d screen: Sonnet with the firmer instruction and its default thinking (adaptive, effort high),
    # on the first 1,000 Stage 1 rows (requests/meta_sonnet_firm_think_dev1k.jsonl, scripts/subset_requests.py)
    "sonnet_firm_think": {"provider": "anthropic", "model": "claude-sonnet-5-5", "system": PHYSICIAN_FIRM_SYSTEM,
                          "temperature": None, "max_tokens": 16000},
})

# Stage 1e (2026-10-12): no Claude grader reached physician-level agreement, so the user asked to try OpenAI graders.
# Both are configured exactly as in openai/simple-evals (OPENAI_API_KEY), with the grader prompt unchanged:
#   gpt41     = the paper's validated grader: ChatCompletionSampler("gpt-4.1-2025-04-14", system "You are a helpful
#               assistant.", max_tokens 2048, default temperature 0.5)
#   gpt54_low = simple-evals --healthbench-use-gpt-5-4-low-grader (HealthBench Professional's default):
#               ResponsesSampler("gpt-5.4-2026-03-05", reasoning effort low, no system message)
GRADERS.update({
    "gpt41": {"provider": "openai", "endpoint": "/v1/chat/completions", "model": "gpt-4.1-2025-04-14",
              "system": "You are a helpful assistant.", "temperature": 0.5, "max_tokens": 2048},
    "gpt54_low": {"provider": "openai", "endpoint": "/v1/responses", "model": "gpt-5.4-2026-03-05",
                  "reasoning_effort": "low"},
})

# Batch chunking: kept well under provider limits on request count and file size.
BATCH_MAX_REQUESTS = 10_000
BATCH_MAX_BYTES = 150 * 1024 * 1024

# Concurrency for the synchronous runner (pilot runs and retries).
SYNC_THREADS = 8

# Bootstrap settings for confidence intervals.
BOOTSTRAP_REPS = 1000
SEED = 0
