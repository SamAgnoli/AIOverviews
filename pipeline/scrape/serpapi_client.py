"""Single-key SerpAPI plumbing shared by the notebook, run_batch.py and progress tools.

Replaces the old multi-account setup (a different SERPAPI_KEY exported per
run_all.py invocation, FORCE_BATCH_NUM to spend a key's leftovers, separate
run_all_<name>.log files). There is now ONE key and three jobs:

1. get_key()      -- SERPAPI_KEY env var, else serpapi_key.txt next to this file.
                     The key never lives in the notebook.
2. acquire()      -- account-wide hourly rate limit (Developer plan: 1000/hour).
                     A rolling-window limiter that survives across batch
                     PROCESSES by persisting call timestamps to
                     logs/serpapi_calls.log. A rolling window below the cap is
                     safe whether SerpAPI counts by rolling or clock hour.
3. classify_error() -- SerpAPI returns failures as HTTP 200/4xx JSON bodies with
                     an "error" key; the serpapi client library does NOT raise
                     on them. Unclassified, a quota or rate-limit response
                     parses as "no AI Overview" and is recorded as a finished
                     question. Everything that calls SerpAPI must route the
                     "error" field through here.
"""
import os
import threading
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, "serpapi_key.txt")
CALL_LOG = os.path.join(HERE, "logs", "serpapi_calls.log")

HOURLY_LIMIT = int(os.environ.get("SERPAPI_HOURLY_LIMIT", "1000"))
# Headroom for calls that race past the check from parallel question workers
# (each worker can hold one reserved follow-up slot) and for anything else
# using the key.
HOURLY_MARGIN = int(os.environ.get("SERPAPI_HOURLY_MARGIN", "40"))
WINDOW = 3600.0


class SerpApiQuotaError(RuntimeError):
    """Out of searches / invalid key. Retrying cannot help until the account changes."""


def get_key():
    key = os.environ.get("SERPAPI_KEY", "").strip()
    if key:
        return key
    try:
        with open(KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# ── error classification ─────────────────────────────────────────────────────
import re

_NO_RESULTS_RE = re.compile(r"hasn'?t returned any results|no results found", re.I)
_RATE_RE = re.compile(r"hourly|per hour|throughput|too many requests|rate.?limit", re.I)
_QUOTA_RE = re.compile(
    r"run out of searches|searches for the month|monthly|upgrade|no more searches|"
    r"plan.*(exhaust|limit)|credits?|invalid api key|api key.*(invalid|missing)|"
    r"account.*(disabled|suspended|deleted)", re.I)


def classify_error(message):
    """-> 'no_results' | 'rate_limited' | 'quota' | 'other'."""
    msg = str(message or "")
    if _NO_RESULTS_RE.search(msg):
        return "no_results"
    if _RATE_RE.search(msg):
        return "rate_limited"
    if _QUOTA_RE.search(msg):
        return "quota"
    return "other"


# Once any thread sees a quota error, the rest of the batch stops calling.
_quota_hit = threading.Event()


def mark_quota_exhausted():
    _quota_hit.set()


def quota_exhausted():
    return _quota_hit.is_set()


# ── rolling-window limiter ───────────────────────────────────────────────────
_lock = threading.Lock()
_calls = deque()


def _load_history():
    now = time.time()
    try:
        with open(CALL_LOG, encoding="utf-8") as fh:
            for line in fh:
                try:
                    t = float(line.strip())
                except ValueError:
                    continue
                if now - t < WINDOW:
                    _calls.append(t)
    except OSError:
        pass


_load_history()


def _prune(now):
    while _calls and now - _calls[0] >= WINDOW:
        _calls.popleft()


def _record(now):
    _calls.append(now)
    try:
        os.makedirs(os.path.dirname(CALL_LOG), exist_ok=True)
        with open(CALL_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{now:.3f}\n")
    except OSError:
        pass


def budget():
    return max(1, HOURLY_LIMIT - HOURLY_MARGIN)


def calls_last_hour():
    with _lock:
        _prune(time.time())
        return len(_calls)


def acquire(reserve=1):
    """Block until `reserve` calls fit in the rolling hour, then record ONE call.

    A Google search reserves 2 (itself + the AI Overview page_token follow-up,
    which must be fetched within about a minute and so must never wait here);
    the follow-up then calls record_reserved().
    """
    announced = False
    while True:
        with _lock:
            now = time.time()
            _prune(now)
            if len(_calls) + reserve <= budget():
                _record(now)
                return
            wait = WINDOW - (now - _calls[0]) + 0.5
        if not announced:
            print(f"  ⏳ SerpAPI hourly budget reached ({budget()}/h) — "
                  f"pausing ~{wait / 60:.1f} min", flush=True)
            announced = True
        time.sleep(min(max(wait, 1.0), 30.0))


def record_reserved():
    """Record a call whose slot was already reserved by acquire(reserve=2)."""
    with _lock:
        _record(time.time())


def wait_after_rate_limit(attempt):
    """SerpAPI itself said we are over the hourly limit: back off hard."""
    delay = min(60 * (2 ** max(0, attempt - 1)), 600)
    print(f"  ⏳ SerpAPI reported the hourly limit — sleeping {delay // 60} min", flush=True)
    time.sleep(delay)


def account(key=None, timeout=25):
    """The /account endpoint (free; does not consume a search)."""
    import requests
    return requests.get("https://serpapi.com/account",
                        params={"api_key": key or get_key()}, timeout=timeout).json()
