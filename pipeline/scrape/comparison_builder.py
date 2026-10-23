"""
comparison_builder — builds the analysis-ready AIO-vs-organic comparison package.

Called from BOTH notebooks so every output stays in sync:
  * main pipeline Step 13 (after Steps 9-12) — comparison CSVs only
  * Halloumi_Scoring.ipynb (after scoring)  — CSVs + the Excel workbook, so the
    AIO sheet always carries the halloumi columns and statement scores

ALL datasets live in <project_dir>/<output_subdir> (default "Output") — this
module reads them there and writes there. Nothing is written to the project
root and nothing is copied.

Files in Output/:
  * aio_dataset.csv / organic_dataset.csv   (written by Step 9, enriched in place)
  * statement_scores.csv                    (written by the Halloumi notebook)
  * comparison_sources.csv                  (one row per source, overlap flags)
  * comparison_questions.csv                (one row per question, counts only)
  * analysis_workbook.xlsx                  (6 sheets; built only when
                                             include_workbook=True)

No google.colab imports here — Drive mounting is the notebooks' job.

Usage:
    from comparison_builder import build_comparison_outputs
    build_comparison_outputs(PROJECT_DIR)                          # CSVs only
    build_comparison_outputs(PROJECT_DIR, include_workbook=True)   # + workbook
"""

import os
import re
import csv

import pandas as pd

# ── Small helpers ─────────────────────────────────────────────────────────────

def _field(val):
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("", "nan", "none", "nat", "null") else s


def normalize_url(url):
    """Comparable URL key: lowercase host, drop www., drop fragment & trailing /."""
    u = _field(url)
    if not u:
        return ""
    u = u.split("#", 1)[0].rstrip("/")
    m = re.match(r"^(https?://)([^/]+)(.*)$", u, re.I)
    if not m:
        return u.lower()
    host = m.group(2).lower()
    if host.startswith("www."):
        host = host[4:]
    return host + m.group(3)


_TWO_LEVEL_TLDS = {"co.uk", "org.uk", "ac.uk", "com.au", "org.au", "co.nz",
                   "co.jp", "com.br", "co.in", "gov.uk", "gov.au"}


def registered_domain(url):
    """e.g. https://www.mayoclinic.org/a/b -> mayoclinic.org"""
    u = _field(url)
    m = re.match(r"^https?://([^/]+)", u, re.I)
    host = (m.group(1) if m else u).lower().split(":")[0]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _build_lookup(df):
    """Per-question lookup tables: normalized url -> rank, domain -> best rank."""
    url_rank, dom_rank = {}, {}
    for q, grp in df.groupby("question"):
        u_map, d_map = {}, {}
        for _, r in grp.iterrows():
            u, rk = normalize_url(r.get("url")), r.get("rank")
            if u and u not in u_map:
                u_map[u] = rk
            d = registered_domain(r.get("url"))
            if d and (d not in d_map or rk < d_map[d]):
                d_map[d] = rk
        url_rank[q], dom_rank[q] = u_map, d_map
    return url_rank, dom_rank


# HON_Attribution (duplicated JAMA_Attribution in the human codes) and
# HON_Justifiability (not codeable) were dropped in Sept 2026.
_SCORE_COLS = ["JAMA_Authorship", "JAMA_Attribution", "JAMA_Disclosure",
               "JAMA_Currency", "JAMA_Total", "HON_Authority",
               "HON_Complementarity", "HON_Total", "gispy_gis"]

# Per-question metadata from questions.xlsx, stamped on every row by Steps 2/8/9.
# Blank on datasets built before those columns existed — all reads are guarded.
QUESTION_META_COLS = ["risk_level", "rubric_complexity", "requires_hedging"]


def _meta_from(*frames):
    """First non-blank metadata values found across the given row groups."""
    out = {c: "" for c in QUESTION_META_COLS}
    for frame in frames:
        if frame is None or not len(frame):
            continue
        for c in QUESTION_META_COLS:
            if not out[c] and c in frame.columns:
                vals = frame[c].dropna().astype(str)
                vals = vals[vals.str.strip() != ""]
                if len(vals):
                    out[c] = vals.iloc[0]
    return out


def _source_row(r, source_type, other_urls, other_doms):
    q = r.get("question")
    u_norm = normalize_url(r.get("url"))
    dom = registered_domain(r.get("url"))
    o_urls = other_urls.get(q, {})
    o_doms = other_doms.get(q, {})
    row = {
        "question":              q,
        **{c: _field(r.get(c)) for c in QUESTION_META_COLS},
        "search_date":           _field(r.get("search_date")),
        "source_type":           source_type,
        "rank":                  r.get("rank", ""),
        "url":                   _field(r.get("url")),
        "domain":                dom,
        "title":                 _field(r.get("title")),
        "authors":               _field(r.get("authors")),
        "pub_date":              _field(r.get("pub_date")),
        "updated_date":          _field(r.get("updated_date")),
        "text_len":              len(_field(r.get("text"))),
        "text_quality":          _field(r.get("text_quality")),
        "fetch_method":          _field(r.get("fetch_method")),
        "error":                 _field(r.get("error")),
        "in_other_set":          u_norm in o_urls,
        "other_set_rank":        o_urls.get(u_norm, ""),
        "domain_in_other_set":   dom in o_doms,
        "domain_other_set_rank": o_doms.get(dom, ""),
    }
    for c in _SCORE_COLS:
        if c in r.index:
            row[c] = r.get(c)
    return row


# ── Per-question Halloumi totals ──────────────────────────────────────────────
# Preferred: read the aio_halloumi_n_statements_* columns the Halloumi notebook
# stamps on every row. Fallback: recompute from statement_scores.csv rows.

_TOTAL_KEYS = ("total", "supported", "scored", "skipped", "uncited")

def _blank_totals():
    out = {f"n_statements_{k}": "" for k in _TOTAL_KEYS}
    out["n_statements_uncited_factual"] = ""
    out["halloumi_support_score"] = ""
    out["halloumi_support_rate"] = ""
    out["halloumi_support_rate_strict"] = ""
    return out

def _totals_from_counts(total, sup, scd, skp, unc, unc_fact=0):
    strict_den = scd + unc_fact
    return {
        "n_statements_total":     total,
        "n_statements_supported": sup,
        "n_statements_scored":    scd,
        "n_statements_skipped":   skp,
        "n_statements_uncited":   unc,
        "n_statements_uncited_factual": unc_fact,
        "halloumi_support_score": f"{sup}/{scd}" if scd else "",
        "halloumi_support_rate":  round(sup / scd, 3) if scd else "",
        # Strict = Oumi-comparable accounting: uncited FACTUAL claims count
        # against the AIO (headers / conversational text still excluded).
        "halloumi_support_rate_strict": round(sup / strict_den, 3) if strict_den else "",
    }

def _question_halloumi_totals(a_grp, stmt_scores_q=None):
    if not len(a_grp):
        return _blank_totals()

    # 1. Stamped totals (constant per question)
    stamped = ("aio_halloumi_n_statements_supported" in a_grp.columns and
               a_grp["aio_halloumi_n_statements_supported"].notna().any())
    if stamped:
        r = a_grp[a_grp["aio_halloumi_n_statements_supported"].notna()].iloc[0]
        def g(k):
            v = r.get(f"aio_halloumi_n_statements_{k}")
            return int(float(v)) if _field(v) else 0
        return _totals_from_counts(g("total"), g("supported"), g("scored"),
                                   g("skipped"), g("uncited"),
                                   g("uncited_factual"))

    # 2. Recompute from statement_scores rows for this question
    if stmt_scores_q is not None and len(stmt_scores_q):
        status = stmt_scores_q["status"].astype(str)
        sup = int((status == "supported").sum())
        uns = int((status == "unsupported").sum())
        skp = int((status == "skipped").sum())
        unc = int((status == "uncited").sum())
        unc_fact = 0
        if "uncited_type" in stmt_scores_q.columns:
            unc_fact = int(((status == "uncited") &
                            (stmt_scores_q["uncited_type"].astype(str)
                             == "uncited_factual")).sum())
        return _totals_from_counts(len(stmt_scores_q), sup, sup + uns, skp, unc,
                                   unc_fact)

    return _blank_totals()


# ── Per-question GisPy (Gist Inference Score) columns ────────────────────────
# Stamped on the datasets by GisPy_Scoring.ipynb; blank when that notebook
# hasn't run (all reads are guarded so pre-GisPy datasets work unchanged).

def _mean_or_blank(grp, col):
    if col not in grp.columns or not len(grp):
        return ""
    vals = pd.to_numeric(grp[col], errors="coerce").dropna()
    return round(float(vals.mean()), 3) if len(vals) else ""


def _question_gispy_cols(a_grp, o_grp):
    aio_gis = ""
    if "aio_gispy_gis" in a_grp.columns and len(a_grp):
        vals = pd.to_numeric(a_grp["aio_gispy_gis"], errors="coerce").dropna()
        if len(vals):
            aio_gis = round(float(vals.iloc[0]), 3)
    return {
        "aio_gispy_gis":             aio_gis,
        "gispy_gis_aio_sources_mean": _mean_or_blank(a_grp, "gispy_gis"),
        "gispy_gis_organic_mean":     _mean_or_blank(o_grp, "gispy_gis"),
    }


# ── Data Dictionary ───────────────────────────────────────────────────────────

_DD_EXACT = {
    "question": "The search question sent to Google",
    "risk_level": "questions.xlsx: clinical risk of the question (High | Medium | Low)",
    "rubric_complexity": "questions.xlsx: how complex a correct answer's rubric is (High | Medium | Low)",
    "requires_hedging": "questions.xlsx: TRUE when a correct answer must hedge or defer to a clinician",
    "search_date": "Date the SerpAPI search ran",
    "type": "Row type marker (AIO or Organic dataset origin)",
    "source_type": "AIO = cited in the AI Overview; Organic = top-5 ranked result",
    "rank": "Position within its result set (AIO citation order / organic rank)",
    "source_index": "Stable AIO source index (aio_src{N}); used by Halloumi to map statements to sources",
    "aio_text": "Full text of the AI Overview answer (citation chips removed)",
    "title": "Page title from SerpAPI",
    "url": "Page URL (fragment-stripped)",
    "domain": "Registered domain of the URL",
    "authors": "Extracted byline; '... editorial team (inferred)' = no named author found",
    "pub_date": "Published date; '(inferred)' = scraped from visible text, not metadata",
    "updated_date": "Last-updated date; '<date>_current' = access date fallback (no date on page)",
    "text": "Full scraped body text of the page",
    "text_len": "Character count of scraped body text",
    "text_quality": "Content validity of the scraped text: ok | garbage_interstitial (bot-check/CAPTCHA page) | too_short | empty | snippet_only | product_stub (Google Shopping listing stub). Only 'ok' sources are used for Halloumi scoring",
    "disclosures": "Conflict-of-interest / funding / sponsorship statements found on the page",
    "error": "Scrape errors for this source (empty = clean)",
    "fetch_method": "How the page was fetched: europepmc | requests | trafilatura | playwright | amp | jina | wayback | europepmc_abstract | snippet_fallback",
    "in_other_set": "TRUE if this exact URL also appears in the other result set for this question",
    "other_set_rank": "Rank of this URL in the other result set (blank if absent)",
    "domain_in_other_set": "TRUE if this domain appears anywhere in the other result set",
    "domain_other_set_rank": "Best rank of this domain in the other result set",
    "aio_present": "TRUE when Google returned an AI Overview for this question (independent of whether it cited any sources; see n_aio_sources for that)",
    "aio_text_len": "Character count of the AI Overview text",
    "aio_text_words": "Word count of the AI Overview text (whitespace-split)",
    "n_aio_sources": "Number of AIO citations captured (all of them, uncapped)",
    "n_organic": "Number of organic results captured (top 5)",
    "n_url_overlap": "Distinct URLs appearing in BOTH the AIO citations and organic top-5",
    "n_domain_overlap": "Distinct registered domains appearing in both sets",
    "organic_ranks_cited_by_aio": "Which organic ranks (1-5) are also cited by the AIO",
    "n_aio_sources_not_in_top5": "AIO citations that do NOT appear in the organic top-5",
    "aio_statements_source": "How statement→source attribution was derived: serpapi (inline refs) | containment (token match) | none",
    "n_statements_attributed": "Distinct AIO statements attributed to at least one source",
    "n_sources_ok": "AIO sources with clean scraped text (text_quality = ok)",
    "n_sources_bad": "AIO sources with unusable text (bot-check/blank/snippet) — excluded from Halloumi",
    "n_statements_total": "EVERY statement in the AI Overview text (cited or not) — the full denominator",
    "n_statements_supported": "Statements supported by at least one readable cited source (prob >= threshold)",
    "n_statements_scored": "Statements with a definitive verdict (supported + unsupported)",
    "n_statements_skipped": "Statements with an INDETERMINATE verdict — readable cited sources didn't support them AND >=1 cited source was unreadable, so 'unsupported' can't be proven",
    "n_statements_uncited": "Statements Google attached no citations to — enumerated but not scored",
    "n_statements_uncited_factual": "Uncited statements that are REAL factual claims (headers and conversational text excluded)",
    "halloumi_support_score": "Supported / scored, e.g. '6/8' — the AI Overview's support tally for this question",
    "halloumi_support_rate": "n_statements_supported / n_statements_scored as a fraction (blank if nothing scored)",
    "halloumi_support_rate_strict": "supported / (scored + uncited_factual) — strict, Oumi-comparable accounting where uncited factual claims count against the AIO",
    "statement_index": "Position of this statement within the AI Overview text (1-based)",
    "statement_text": "The statement as segmented from the AI Overview text",
    "status": "supported (majority of factual sentences grounded) | unsupported (all sources readable, majority not met) | skipped (indeterminate — unreadable source, or no factual claims) | uncited (no Google citations)",
    "cited_source_indexes": "source_index values Google attached to this statement (blank for uncited)",
    "unreadable_source_indexes": "Cited sources whose text couldn't be recovered",
    "n_sentences": "Sentences the statement splits into",
    "n_factual": "Factual sentences left after dropping lead-ins/directives/disclaimers",
    "n_supported_sentences": "Factual sentences that ANY cited source supports (prob >= threshold)",
    "support_fraction": "n_supported_sentences / n_factual — statement is 'supported' when this is >= 0.5",
    "supported_prob": "Best single factual-sentence support probability found across cited sources (reference)",
    "best_source": "source_index whose text best supports this statement",
    "mean_prob": "Mean of the per-sentence best-source support probabilities",
    "skip_reason": "Why a statement got no supported/unsupported verdict (e.g. 'indeterminate: unreadable_source(s): 3', 'no_factual_claims')",
    "uncited_type": "For uncited statements only: header (section label) | conversational (Gemini talking to the user) | uncited_factual (a real claim Google left uncited)",
    "supported_by_any_source": "Second opinion on UNSUPPORTED statements: TRUE = supported by some readable source for the question (wrong citation), FALSE = grounded nowhere we can see, blank = not applicable",
    "any_source_best": "source_index that best supports an unsupported statement when scored against ALL the question's sources",
    "threshold": "SUPPORT_THRESHOLD used for the sentence-level verdicts on this run",
    "cited_by_statement_indexes": "Which AIO statement numbers cite this source",
    "is_forum": "TRUE if this organic result is a forum (Reddit/Quora/health forums)",
    "forum_serpapi_retry": "Forum scrape failed and is flagged for a manual retry",
    "JAMA_Authorship": "JAMA: 1 = a named author or reviewer with medical credentials (MD, PA-C, PhD, RN...) is shown; bare names / editorial-team fallbacks = 0",
    "JAMA_Attribution": "JAMA: 1 = reference/sources section, numbered [n] citation links, or >=2 external source links in the article prose (internal site links do not count)",
    "JAMA_Disclosure": "JAMA: 1 = a conflict-of-interest / advertising / funding / sponsorship STATEMENT on the page, or a named 'Advertising Policy' / 'Affiliate Disclosure' link (a bare 'Advertising' nav word = 0)",
    "JAMA_Currency": "JAMA: 1 = any visible publish/update/review date (labeled or bare); the '<date>_current' access-date placeholder never counts",
    "JAMA_Total": "JAMA benchmark total (0-4): Authorship + Attribution + Disclosure + Currency",
    "HON_Authority": "HONcode: 1 = credentialed author/reviewer OR an institutional domain (.gov, .edu, major medical centers, professional societies)",
    "HON_Complementarity": "HONcode: 1 = page says it supports rather than replaces medical care ('consult your doctor', 'not a substitute', 'informational purposes only', ...) — body or footer",
    "HON_Total": "HONcode subset total (0-2): Authority + Complementarity (Attribution and Justifiability were dropped)",
    "sig_byline": "Page signal (Step 5, full HTML): first visible byline / contributor line, e.g. 'By Barbara Bolen, PhD'",
    "sig_reviewer": "Page signal: first '(Medically) reviewed by ...' line",
    "sig_credentialed": "Page signal: 1 if a byline/reviewer/contributor line or JSON-LD author carries medical credentials",
    "sig_date_label": "Page signal: first visible labeled date ('Last updated on 05/05/2022', 'Published on: ...', 'Last reviewed: March 2026')",
    "sig_date_visible": "Page signal: first bare date in the top ~1200 characters of the article text (copyright years excluded) — a date the reader can see",
    "sig_disclaimer": "Page signal: first complementarity cue found anywhere on the page (body or footer)",
    "sig_disclosure": "Page signal: first >=6-word sentence containing a disclosure cue (conflict of interest, advertising on our site, sponsored by, funded by, ...) or a named policy link ('Advertising Policy')",
    "sig_ref_section": "Page signal: 1 if a heading/button reads References / Sources / Citations / Bibliography / View Sources",
    "sig_n_numbered_cites": "Page signal: count of numbered citation links ([1], superscript links, #ref anchors)",
    "sig_n_external_links": "Page signal: distinct links to OTHER domains inside the article prose (nav/footer/aside stripped; portals and social excluded)",
    "aio_halloumi_n_statements_total": "This question's total statement count — every statement in the AIO text (stamped on every row)",
    "aio_halloumi_n_statements_supported": "This question's supported-statement count (stamped on every row)",
    "aio_halloumi_n_statements_scored": "This question's definitively-tested statement count (stamped on every row)",
    "aio_halloumi_n_statements_skipped": "This question's indeterminate statement count (stamped on every row)",
    "aio_halloumi_n_statements_uncited": "This question's uncited statement count (stamped on every row)",
    "aio_halloumi_n_statements_uncited_factual": "This question's count of uncited statements that are real factual claims (stamped on every row)",
    "aio_halloumi_support_score": "This question's support tally, e.g. '6/8' (stamped on every row)",
    "aio_halloumi_support_rate": "supported/scored as a fraction (stamped on every row)",
    "aio_halloumi_support_rate_strict": "supported / (scored + uncited_factual) — strict rate where uncited factual claims count against the AIO (stamped on every row)",
    "gispy_gis": "GisPy Gist Inference Score of THIS document's full text (z-score composite, pooled across all of the run's documents; higher = more gist-friendly). Blank = not scored (bad text quality)",
    "aio_gispy_gis": "GisPy GIS of the question's AI Overview text itself (stamped on every row of the question)",
    "gispy_gis_aio_sources_mean": "Mean gispy_gis over this question's scored AIO-cited sources",
    "gispy_gis_organic_mean": "Mean gispy_gis over this question's scored organic results",
    "doc_type": "GisPy document type: aio (the AI Overview text) | aio_source (first-tier cited page) | organic (ranked page) | organic_forum (forum post + comments)",
    "text_source": "Where the GisPy document text came from: statements_raw / statement_columns / aio_text_flat (AIO) | text (pages) | forum_post_and_comments (forums)",
    "skip_reason": "Why a document was NOT GisPy-scored (e.g. text_quality:snippet_only, forum_empty); blank = scored",
    "gispy_error": "Index-computation failure for this document (blank = clean)",
    "gis": "The document's Gist Inference Score: weighted sum of z-scored GisPy indices (default: zPCREF_ap + zPCDC + zSMCAUSe_1p − zSMCAUSwn_a_binary − zPCCNC_megahr − zWRDIMGc_megahr − zWRDHYPnv)",
    "DESPC": "GisPy: number of paragraphs in the document",
    "DESSC": "GisPy: number of sentences in the document",
    "PCDC": "GisPy deep cohesion: causal connectives per sentence",
    "WRDHYPnv": "GisPy hypernymy of nouns & verbs: mean WordNet hypernym-path length (higher = more specific words = less gist-friendly)",
}

_DD_PATTERNS = [
    (r"^cite(\d+)_url$",          "URL of sub-citation {0} scraped from this source's reference list"),
    (r"^cite(\d+)_ref_text$",     "Reference-list text for sub-citation {0}"),
    (r"^cite(\d+)_text$",         "Full scraped body text of sub-citation {0}"),
    (r"^cite(\d+)_text_quality$", "Content validity of sub-citation {0}'s text"),
    (r"^cite(\d+)_authors$",      "Authors of sub-citation {0}"),
    (r"^cite(\d+)_pub_date$",     "Published date of sub-citation {0}"),
    (r"^cite(\d+)_updated_date$", "Updated date of sub-citation {0}"),
    (r"^cite(\d+)_disclosures$",  "Disclosure statements on sub-citation {0}"),
    (r"^cite(\d+)_error$",        "Scrape errors for sub-citation {0}"),
    (r"^cite(\d+)_fetch_method$", "Fetch method for sub-citation {0}"),
    (r"^cite(\d+)_JAMA_",         "JAMA sub-score for sub-citation {0}"),
    (r"^cite(\d+)_HON_",          "HONcode sub-score for sub-citation {0}"),
    (r"^cite(\d+)_sig_",          "Page-quality signal for sub-citation {0} (see the sig_* rows)"),
    (r"^sig_",                    "Page-quality signal captured from the full HTML by Step 5 (input to JAMA/HON scoring)"),
    # Per-AIO statement columns (identical on every row of a question):
    (r"^aio_statement(\d+)_text$",      "AIO statement #{0} (the {0}th block unit of the overview) — same on every row of the question"),
    (r"^aio_statement(\d+)_citations$", "Source indexes Google cited for statement #{0}"),
    (r"^aio_statement(\d+)_status$",    "Verdict for statement #{0}: supported | unsupported | skipped (indeterminate) | uncited"),
    (r"^aio_statement(\d+)_supported$", "TRUE if statement #{0} is supported (majority of its factual sentences grounded), FALSE if unsupported, blank if skipped/uncited"),
    (r"^aio_statement(\d+)_support_fraction$", "Fraction of statement #{0}'s factual sentences that a cited source supports"),
    (r"^aio_statement(\d+)_best_source$", "source_index that best supports statement #{0}"),
    (r"^JAMA_",  "JAMA benchmark sub-score (1 = criterion met)"),
    (r"^HON_",   "HONcode sub-score (1 = criterion met)"),
    (r"^forum_", "Forum post/comment field (only populated for forum rows)"),
    (r"^aio_halloumi_", "Halloumi score of the whole AIO text vs all sources (fallback path for questions without statements)"),
    (r"^z(PCREF|PCDC|SMCAUSe|SMCAUSwn|PCCNC|WRDIMGc|WRDHYPnv|DESPC|DESSC|CoREF)", "GisPy index z-scored across this run's pooled documents"),
    (r"^(PCREF|SMCAUSe|SMCAUSwn|PCCNC|WRDIMGc)_", "GisPy raw index (see gispy_scorer.py: referential cohesion / verb overlap / concreteness / imageability variants)"),
    (r"^cite1_is_pdf_skipped$", "TRUE if a PDF citation was skipped when filling citation slots"),
]


def _describe(col):
    if col in _DD_EXACT:
        return _DD_EXACT[col]
    for pat, desc in _DD_PATTERNS:
        m = re.match(pat, col)
        if m:
            return desc.format(*m.groups()) if m.groups() else desc
    return ""


# ── Excel writing ─────────────────────────────────────────────────────────────

XLSX_CELL_CAP = 30000  # Excel hard limit is 32,767 chars/cell
_TRUNC_MARK = " …[truncated — full text in CSV]"
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xlsx_safe(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if isinstance(val, str):
        val = _CTRL_RE.sub("", val)
        if len(val) > XLSX_CELL_CAP:
            return val[:XLSX_CELL_CAP] + _TRUNC_MARK
    return val


def _write_sheet(wb, title, df):
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    ws = wb.create_sheet(title=title)
    # Write-only workbooks (used at 3,800-question scale) must set layout
    # before the first row and style cells via WriteOnlyCell.
    ws.freeze_panes = "A2"
    for i, col in enumerate(df.columns, 1):
        c = str(col).lower()
        width = 45 if ("text" in c or c in ("question", "url", "title", "disclosures")) else 16
        ws.column_dimensions[get_column_letter(i)].width = width
    if getattr(wb, "write_only", False):
        from openpyxl.cell import WriteOnlyCell
        header = []
        for c in df.columns:
            cell = WriteOnlyCell(ws, value=str(c))
            cell.font = Font(bold=True)
            header.append(cell)
        ws.append(header)
    else:
        ws.append([str(c) for c in df.columns])
        for cell in ws[1]:
            cell.font = Font(bold=True)
    for row in df.itertuples(index=False, name=None):
        ws.append([_xlsx_safe(v) for v in row])
    return ws


# ── Main entry point ──────────────────────────────────────────────────────────

def build_comparison_outputs(project_dir, output_subdir="Output",
                             aio_file="aio_dataset.csv",
                             organic_file="organic_dataset.csv",
                             stmt_file="statement_scores.csv",
                             src_out="comparison_sources.csv",
                             q_out="comparison_questions.csv",
                             xlsx_out="analysis_workbook.xlsx",
                             include_workbook=False,
                             verbose=True):
    """Build the comparison package. Datasets are read from AND written to
    <project_dir>/<output_subdir> — the single home for all analysis files.
    The Excel workbook is only produced when include_workbook=True (the
    Halloumi notebook's final step, so it always carries the scores)."""
    def log(msg):
        if verbose:
            print(msg)

    out_dir = os.path.join(project_dir, output_subdir)
    os.makedirs(out_dir, exist_ok=True)

    aio_path = os.path.join(out_dir, aio_file)
    org_path = os.path.join(out_dir, organic_file)
    aio_df = pd.read_csv(aio_path, encoding="utf-8-sig", low_memory=False)
    org_df = pd.read_csv(org_path, encoding="utf-8-sig", low_memory=False)
    log(f"Loaded {len(aio_df)} AIO rows, {len(org_df)} organic rows.")

    stmt_path = os.path.join(out_dir, stmt_file)
    stmt_df = None
    if os.path.exists(stmt_path):
        stmt_df = pd.read_csv(stmt_path, encoding="utf-8-sig", low_memory=False)
        log(f"Loaded {len(stmt_df)} statement scores.")

    # statements_raw.csv is the only place the AI Overview TEXT survives for a
    # question whose overview cited nothing: such questions produce no
    # aio_dataset rows at all, so `aio_text` (read off an AIO source row below)
    # comes back empty and makes a real, uncited overview look like a scrape
    # failure. Keep a question -> overview-text map to set aio_present and
    # aio_text_len truthfully.
    raw_stmt_path = os.path.join(out_dir, "statements_raw.csv")
    aio_text_by_q = {}
    if os.path.exists(raw_stmt_path):
        _rs = pd.read_csv(raw_stmt_path, encoding="utf-8-sig", low_memory=False)
        if {"question", "statement_text"}.issubset(_rs.columns):
            _rs = _rs.sort_values(["question", "statement_index"]) \
                if "statement_index" in _rs.columns else _rs
            aio_text_by_q = (_rs.groupby("question")["statement_text"]
                             .apply(lambda v: " ".join(str(x) for x in v
                                                       if isinstance(x, str)))
                             .to_dict())
            log(f"Loaded overview text for {len(aio_text_by_q)} questions "
                f"from statements_raw.csv.")

    # An overview that cited nothing AND had no organic results has no dataset
    # rows at all, so it used to vanish from comparison_questions (38 on the
    # 2026-09-16 run). It is listed from statements_raw below; its question
    # metadata and search date come from the raw search file in the project root.
    raw_meta_by_q = {}
    raw_path = os.path.join(project_dir, "research_output.csv")
    if aio_text_by_q and os.path.exists(raw_path):
        _want = ["question", "search_date"] + QUESTION_META_COLS
        _rm = pd.read_csv(raw_path, encoding="utf-8-sig", low_memory=False,
                          usecols=lambda c: c in _want)
        raw_meta_by_q = {q: g for q, g in _rm.groupby("question", sort=False)}

    gispy_path = os.path.join(out_dir, "gispy_scores.csv")
    gispy_df = None
    if os.path.exists(gispy_path):
        gispy_df = pd.read_csv(gispy_path, encoding="utf-8-sig", low_memory=False)
        log(f"Loaded {len(gispy_df)} GisPy document scores.")

    aio_urls_by_q, aio_doms_by_q = _build_lookup(aio_df)
    org_urls_by_q, org_doms_by_q = _build_lookup(org_df)

    # ── comparison_sources ──
    src_rows = []
    for _, r in aio_df.iterrows():
        src_rows.append(_source_row(r, "AIO", org_urls_by_q, org_doms_by_q))
    for _, r in org_df.iterrows():
        src_rows.append(_source_row(r, "Organic", aio_urls_by_q, aio_doms_by_q))
    src_cmp_df = pd.DataFrame(src_rows)
    log(f"comparison_sources: {len(src_cmp_df)} rows")

    # ── comparison_questions ──
    # Group once. Boolean-mask filtering per question was O(questions x rows)
    # and crawled at the 3,800-question scale.
    q_rows = []
    all_questions = sorted(set(aio_df["question"]) | set(org_df["question"])
                           | set(aio_text_by_q))
    aio_by_q = {q: g for q, g in aio_df.groupby("question", sort=False)}
    org_by_q = {q: g for q, g in org_df.groupby("question", sort=False)}
    stmt_by_q = ({q: g for q, g in stmt_df.groupby("question", sort=False)}
                 if stmt_df is not None and "question" in stmt_df.columns else None)
    _empty_aio, _empty_org = aio_df.iloc[0:0], org_df.iloc[0:0]
    for q in all_questions:
        a_grp = aio_by_q.get(q, _empty_aio)
        o_grp = org_by_q.get(q, _empty_org)
        a_urls = aio_urls_by_q.get(q, {})
        o_urls = org_urls_by_q.get(q, {})
        a_doms = aio_doms_by_q.get(q, {})
        o_doms = org_doms_by_q.get(q, {})

        url_overlap = set(a_urls) & set(o_urls)
        dom_overlap = set(a_doms) & set(o_doms)
        organic_ranks_cited = sorted({int(o_urls[u]) for u in url_overlap
                                      if _field(o_urls.get(u))})

        stmt_idx = set()
        if "cited_by_statement_indexes" in a_grp.columns:
            for v in a_grp["cited_by_statement_indexes"]:
                for tok in re.split(r"[,\s]+", _field(v)):
                    if tok.isdigit():
                        stmt_idx.add(int(tok))

        ok = bad = 0
        for _, rr in a_grp.iterrows():
            tq = _field(rr.get("text_quality"))
            if tq == "ok":
                ok += 1
            elif tq:
                bad += 1

        raw_grp = raw_meta_by_q.get(q)   # fallback for overviews with no dataset rows
        search_date = ""
        for frame in (a_grp, o_grp, raw_grp):
            if frame is not None and len(frame):
                search_date = _field(frame.iloc[0].get("search_date"))
                break
        aio_text = _field(a_grp.iloc[0].get("aio_text")) if len(a_grp) else ""
        # Fall back to the statements text so a cited-nothing overview is not
        # mistaken for a failed scrape (see aio_text_by_q above).
        if not aio_text:
            aio_text = _field(aio_text_by_q.get(q, ""))

        row = {
            "question":                   q,
            **_meta_from(a_grp, o_grp, raw_grp),
            "search_date":                search_date,
            "aio_present":                bool(aio_text),   # Google returned an overview (NOT "has sources")
            "aio_text_len":               len(aio_text),
            "aio_text_words":             len(aio_text.split()),
            "n_aio_sources":              len(a_grp),
            "n_organic":                  len(o_grp),
            "n_url_overlap":              len(url_overlap),
            "n_domain_overlap":           len(dom_overlap),
            "organic_ranks_cited_by_aio": ",".join(str(x) for x in organic_ranks_cited),
            "n_aio_sources_not_in_top5":  sum(1 for u in a_urls if u not in o_urls),
            "aio_statements_source":      (str(a_grp.iloc[0].get("aio_statements_source"))
                                           if len(a_grp) and pd.notna(a_grp.iloc[0].get("aio_statements_source"))
                                           else ""),
            "n_statements_attributed":    len(stmt_idx),
            "n_sources_ok":               ok,
            "n_sources_bad":              bad,
        }
        stmt_q = (stmt_by_q.get(q, stmt_df.iloc[0:0]) if stmt_by_q is not None else None)
        row.update(_question_halloumi_totals(a_grp, stmt_q))
        row.update(_question_gispy_cols(a_grp, o_grp))
        q_rows.append(row)

    q_cmp_df = pd.DataFrame(q_rows)
    log(f"comparison_questions: {len(q_cmp_df)} rows")

    # ── Write CSVs to Output ──
    def save_csv(df, name):
        p = os.path.join(out_dir, name)
        df.to_csv(p, index=False, encoding="utf-8-sig",
                  quoting=csv.QUOTE_ALL, lineterminator="\n")
        log(f"  💾 {len(df)} rows × {len(df.columns)} cols → {output_subdir}/{name}")

    save_csv(src_cmp_df, src_out)
    save_csv(q_cmp_df, q_out)
    files_written = [src_out, q_out]

    # ── Workbook (only at the end of the Halloumi run) ──
    if include_workbook:
        from openpyxl import Workbook
        # write_only streams rows to disk instead of holding every cell object
        # in RAM -- the full-scale AIO sheet alone is ~10M cells.
        wb = Workbook(write_only=True)
        sheets = [
            ("AIO Sources",            aio_df),
            ("Organic Results",        org_df),
        ]
        if stmt_df is not None:
            sheets.append(("Statement Scores", stmt_df))
        if gispy_df is not None:
            sheets.append(("GisPy Scores", gispy_df))
        sheets += [
            ("Comparison (Sources)",   src_cmp_df),
            ("Comparison (Questions)", q_cmp_df),
        ]
        for title, df in sheets:
            _write_sheet(wb, title, df)
            log(f"  📄 Sheet '{title}': {len(df)} rows × {len(df.columns)} cols")
        dd_rows = [{"sheet": title, "column": str(col), "meaning": _describe(str(col))}
                   for title, df in sheets for col in df.columns]
        _write_sheet(wb, "Data Dictionary", pd.DataFrame(dd_rows))
        log(f"  📄 Sheet 'Data Dictionary': {len(dd_rows)} rows")

        xlsx_path = os.path.join(out_dir, xlsx_out)
        wb.save(xlsx_path)
        files_written.append(xlsx_out)
        log(f"\n💾 Workbook saved → {xlsx_path}")

    return {
        "output_dir": out_dir,
        "comparison_sources_rows": len(src_cmp_df),
        "comparison_questions_rows": len(q_cmp_df),
        "files": files_written,
    }
