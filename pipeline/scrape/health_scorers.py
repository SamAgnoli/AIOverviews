"""
JAMA + HONcode source-quality scorers — page-signal edition.

Each analyzer scores a single source. Rules were re-derived from the human
coding worksheet (Sam / Lily / Brigid, Sept 2026). The humans judged the
RENDERED PAGE — bylines, "Medically reviewed by" lines, "Last updated"
labels, footer disclaimers and reference headings — none of which survive
trafilatura's body-text extraction. So the preferred inputs are the `sig_*`
page-signal columns that Step 5's `extract_quality_signals()` captures from
the full HTML. The structured scrape columns (`authors`, `pub_date`,
`updated_date`, `disclosures`, `url`) and the body `text` remain as
fallbacks so datasets scraped before the signals existed still score.

Variables (HON_Attribution and HON_Justifiability were dropped — the former
duplicated JAMA_Attribution in the human codes, the latter is not codeable):

  JAMA_Authorship      credentialed author or reviewer (MD, PA-C, PhD, RN...),
                       or a page on a trusted health domain (MedlinePlus list)
  JAMA_Attribution     reference section, numbered [n] cites, or >=2 external
                       source links in the prose (internal links don't count)
  JAMA_Disclosure      a conflict/advertising/funding STATEMENT, or a named
                       "Advertising Policy" / "Affiliate Disclosure" link
  JAMA_Currency        any visible date (labeled or bare), never the
                       '<date>_current' access-date placeholder
  HON_Authority        credentialed byline/reviewer OR an institutional domain
  HON_Complementarity  "see/consult your doctor", "not a substitute", ...

Usage:
    from health_scorers import JAMAAnalyzer, HONcodeAnalyzer, SIG_COLS
    jama = JAMAAnalyzer(); hon = HONcodeAnalyzer()
    jama.score(text, authors=..., pub_date=..., updated_date=...,
               disclosures=..., url=..., external_citation=True,
               sig_credentialed=1, sig_ref_section=0, ...)
    hon.score(...)  # same keyword arguments
"""

import csv
import os
import re
from urllib.parse import urlsplit

# Page-signal columns emitted by Step 5 / carried by Steps 8-9. Single source
# of truth: the notebook imports this list so the column set stays in sync.
SIG_COLS = [
    "sig_byline",
    "sig_reviewer",
    "sig_credentialed",
    "sig_date_label",
    "sig_date_visible",
    "sig_disclaimer",
    "sig_disclosure",
    "sig_ref_section",
    "sig_n_numbered_cites",
    "sig_n_external_links",
]


def _field(val):
    """Normalize any cell value to a clean string. NaN/None/'nan'/'none'/'nat'
    and whitespace-only values all collapse to ''. This is the NaN-robust guard
    used on every column read."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("", "nan", "none", "nat", "null"):
        return ""
    return s


def _lower(val):
    return _field(val).lower()


def _int(val):
    """Int from a cell that may be '', NaN, '3', '3.0', True."""
    s = _field(val)
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 1 if s.lower() in ("true", "yes") else 0


# ── Credentials ───────────────────────────────────────────────────────────────
# Medical credentials that make a byline/reviewer "qualified" in the coders'
# eyes. The abbreviations are case-sensitive and letter-bounded (a lowercase
# "do" or "np" in prose must not match); the job titles are case-insensitive.
_CRED_ABBREV_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    r"MD|M\.D\.|DO|D\.O\.|MBBS|MBChB|PhD|Ph\.D\.|PharmD|Pharm\.D\.|"
    r"RN|BSN|MSN|DNP|APRN|CRNA|CNM|NP|FNP|PNP|PA-C|MPH|MHA|"
    r"FACP|FACS|FACG|FAAP|FACC|FACEP|FACOG|FAAFP|DDS|DMD|DPM|"
    r"RDN|LDN|LCSW|PsyD|DPT|RPh|CDCES"
    r")(?![A-Za-z])(?!\s*\d{5})"          # 'Baltimore, MD 21201' is an address
)
_CRED_TITLE_RE = re.compile(
    r"\b(?:physician assistant|nurse practitioner|registered nurse|"
    r"registered dietitian|board[- ]certified|medical doctor|"
    r"gastroenterologist|cardiologist|pediatrician|dermatologist|"
    r"neurologist|oncologist|psychiatrist|family physician|"
    r"emergency physician|internist|surgeon)\b",
    re.I,
)


def has_credential(s):
    s = _field(s)
    if not s:
        return False
    return bool(_CRED_ABBREV_RE.search(s) or _CRED_TITLE_RE.search(s)
                or re.search(r"\bDr\.?\s+[A-Z]", s))


# An author is "inferred" (not a real named byline) when it is empty, tagged
# '(inferred)', or is the scraper's editorial-team fallback.
_INFERRED_AUTHOR_RE = re.compile(r"(\(inferred\)|editorial team)", re.I)


def _is_real_author(val):
    s = _field(val)
    return bool(s) and not _INFERRED_AUTHOR_RE.search(s)


# ── Dates ─────────────────────────────────────────────────────────────────────
# Only the scraper's access-date fallback ('<today>_current') is a placeholder.
# '(inferred)' dates ARE visible on the page and the coders counted them.
_DATE_PLACEHOLDER_RE = re.compile(r"_current\b", re.I)


def _is_real_date(val):
    s = _field(val)
    if not s or _DATE_PLACEHOLDER_RE.search(s):
        return False
    return bool(re.search(r"\b(19|20)\d{2}\b", s))


_MON = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?)")
DATE_ALTS = (
    _MON + r"\.?\s+\d{1,2},?\s+\d{4}"
    + r"|\d{1,2}\s+" + _MON + r"\.?,?\s+\d{4}"
    + r"|" + _MON + r"\.?,?\s+\d{4}"
    + r"|\d{1,2}/\d{1,2}/\d{2,4}"
    + r"|\d{4}-\d{2}-\d{2}"
)
# "Last updated on 05/05/2022", "Updated on July 06, 2026", "Posted on ...",
# "Published on: 5/21/2026", "Last reviewed: March 2026", "Reviewed: ..."
DATE_LABEL_RE = re.compile(
    r"\b(?:last\s+(?:updated|reviewed|revised|modified|medically\s+reviewed)|"
    r"medically\s+reviewed|updated|reviewed|revised|published|posted|"
    r"date\s+(?:published|updated|reviewed)|written)\b"
    r"[\s:]*(?:on|:)?[\s:]{0,3}"
    r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+)?"   # 'Posted on Wednesday, June 3, 2026'
    r"(" + DATE_ALTS + r")",
    re.I,
)
# Bare date (no label) — used on the TOP of the article text only.
BARE_DATE_RE = re.compile(r"(?<![\w/.-])(?:" + DATE_ALTS + r")(?![\w/])", re.I)

# ── Complementarity cues (HONcode: "supports, does not replace") ─────────────
DISCLAIMER_RE = re.compile(
    r"(not\s+(?:a\s+|intended\s+(?:as|to\s+be)\s+a\s+|meant\s+(?:as|to\s+be)\s+a\s+)?substitute\s+for|"
    r"for\s+(?:general\s+)?(?:informational|educational|information)\s+purposes\s+only|"
    r"does\s+not\s+(?:constitute|provide|offer)\s+medical\s+advice|"
    r"not\s+(?:intended\s+)?(?:to\s+be\s+|as\s+)?(?:medical|professional|health)\s+advice|"
    r"should\s+not\s+be\s+(?:used|relied\s+on)\s+(?:as|for)\s+(?:a\s+)?(?:diagnos|medical|substitute)|"
    r"(?:always\s+)?(?:consult|see|talk\s+(?:to|with)|speak\s+(?:to|with)|contact|call|visit|check\s+with|ask)\s+"
    r"(?:with\s+)?(?:your|a|an)\s+(?:own\s+)?(?:doctor|physician|healthcare|health\s+care|medical|"
    r"provider|pediatrician|gp|gastroenterologist|pharmacist|specialist|clinician|urgent\s+care)|"
    r"seek\s+(?:the\s+advice|medical|professional|immediate|emergency|urgent|prompt|care)|"
    r"(?:see|consult)\s+a\s+(?:doctor|physician|healthcare|medical|health)|"
    r"(?:doctor|physician|provider)\s+(?:can|will)\s+(?:help|determine|diagnose|recommend|pinpoint)|"
    r"time\s+to\s+see\s+a\s+doctor|"
    r"go\s+to\s+(?:the\s+)?(?:emergency\s+room|er\b|urgent\s+care|a\s+hospital)|"
    r"call\s+911)",
    re.I,
)

# ── Disclosure statement cues (tight: statements, not nav links) ─────────────
DISCLOSURE_RE = re.compile(
    r"(conflicts?\s+of\s+interest|competing\s+interests?|"
    r"advertising\s+(?:on\s+(?:our|this)\s+(?:site|website)|helps\s+support|revenue|supports?\s+our)|"
    r"we\s+do\s+not\s+endorse|does\s+not\s+endorse|"
    r"sponsored\s+by|funded\s+by|receives?\s+funding|funding\s+(?:from|source|was\s+provided)|"
    r"affiliate\s+(?:links?|commission|program)|earn\s+(?:a\s+)?commission|"
    r"financial\s+(?:support|disclosure|relationship|interest|ties)|"
    r"editorial\s+independence|paid\s+(?:partnership|sponsor|advertis)|"
    r"disclosure\s+statement|no\s+(?:financial\s+)?(?:conflicts?|relationships?)\s+to\s+disclose|"
    r"non-?profit\s+(?:academic\s+)?medical\s+cent(?:er|re))",
    re.I,
)


# A named policy page counts as disclosure even as a bare footer link
# (user decision 2026-09-16): the site is pointing the reader at how it is
# funded / what it advertises.
DISCLOSURE_POLICY_RE = re.compile(
    r"\b(advertising|advertiser|advertisement|sponsorship|affiliate|"
    r"funding|financial|disclosure)\s+(policy|disclosure|statement|guidelines?)\b|"
    r"\bpolicy\s+on\s+advertis",
    re.I,
)


def disclosure_statement(text, min_words=6):
    """First disclosure cue on the page: a sentence of >= min_words words
    carrying a statement cue, or a named policy link ('Advertising Policy').
    Other bare nav phrases ('Advertising') do not count."""
    t = _field(text)
    if not t:
        return ""
    for sent in re.split(r"(?<=[.!?])\s+|\s*\|\|\s*", t):
        s = sent.strip()
        if len(s.split()) >= min_words and DISCLOSURE_RE.search(s):
            return s[:300]
    m = DISCLOSURE_POLICY_RE.search(t)
    if m:
        return t[max(0, m.start() - 20):m.end() + 20].strip()[:120]
    return ""


# ── Authoritative-source domains for HONcode Authority ───────────────────────
AUTHORITY_DOMAIN_RE = re.compile(
    r"(\.gov(?:\.[a-z]{2})?(?:/|$)|\.edu(?:/|$)|\.nhs\.uk|"
    r"cdc\.gov|nih\.gov|nlm\.nih\.gov|medlineplus|who\.int|fda\.gov|"
    r"mayoclinic|clevelandclinic|hopkinsmedicine|massgeneralbrigham|"
    r"massgeneral\.org|mountsinai|stanfordhealthcare|uclahealth|ucsfhealth|"
    r"upmc\.com|nyulangone|cedars-sinai|pennmedicine|michiganmedicine|"
    r"uchicagomedicine|dukehealth|yalemedicine|nm\.org|kidshealth\.org|"
    r"cancer\.org|heart\.org|lung\.org|diabetes\.org|kidney\.org|"
    r"aafp\.org|acog\.org|aap\.org|"
    r"healthdirect\.gov\.au|nhsinform|healthychildren\.org)",
    re.I,
)


# ── Trusted health domains for JAMA Authorship ──────────────────────────────
# Sites MedlinePlus links to (trusted_health_domains_medlineplus.csv, next to
# this file). Their editorial authorship is known, so a page on one of these
# domains, or on any subdomain of one, passes JAMA Authorship
# (user decision 2026-10-02).
TRUSTED_DOMAINS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "trusted_health_domains_medlineplus.csv")
with open(TRUSTED_DOMAINS_CSV, encoding="utf-8-sig", newline="") as _f:
    TRUSTED_DOMAINS = frozenset(r["domain"].strip().lower()
                                for r in csv.DictReader(_f) if r["domain"].strip())


def is_trusted_domain(url):
    """True when the URL's host is a listed domain or a subdomain of one."""
    try:
        host = (urlsplit(_field(url)).hostname or "").rstrip(".")
    except ValueError:
        return False
    parts = host.split(".")
    return any(".".join(parts[i:]) in TRUSTED_DOMAINS
               for i in range(len(parts) - 1))


def _signals_present(sig_credentialed):
    """True when Step 5's signal extractor ran on this source. The int-valued
    signals are '' only on rows scraped before the sig_* columns existed (or
    PDFs); then the structured-column fallbacks below take over."""
    return _field(sig_credentialed) != ""


def _authority_signal(authors, sig_credentialed, sig_byline, sig_reviewer):
    """Shared by JAMA_Authorship and HON_Authority: a credentialed human."""
    if _int(sig_credentialed):
        return True
    if has_credential(sig_byline) or has_credential(sig_reviewer):
        return True
    # Fallback for rows scraped before the sig_* columns existed.
    return _is_real_author(authors) and has_credential(authors)


class JAMAAnalyzer:
    """JAMA benchmark: Authorship, Attribution, Disclosure, Currency."""

    def score(self, text="", *, authors="", pub_date="", updated_date="",
              disclosures="", url="", has_citations=False,
              external_citation=False,
              sig_byline="", sig_reviewer="", sig_credentialed="",
              sig_date_label="", sig_date_visible="", sig_disclaimer="",
              sig_disclosure="", sig_ref_section=0, sig_n_numbered_cites=0,
              sig_n_external_links=0):
        t = _field(text)
        sigs = _signals_present(sig_credentialed)

        # Authorship: a credentialed author/reviewer, or a page on a trusted
        # health domain. Elsewhere, bare names and editorial-team fallbacks
        # score 0 (matches all three coders).
        authorship = 1 if (
            _authority_signal(authors, sig_credentialed, sig_byline, sig_reviewer)
            or is_trusted_domain(url)
        ) else 0

        # Attribution: reference section, numbered cites, or >=2 external
        # source links in the prose. ONLY when no signals were scraped: a
        # scraped sub-citation pointing off the source's own domain (the
        # inline-link fallback surfaces booking/portal links, so this is a
        # weaker signal and must not override the page-level ones).
        attribution = 1 if (
            _int(sig_ref_section)
            or _int(sig_n_numbered_cites) >= 1
            or _int(sig_n_external_links) >= 2
            or (not sigs and bool(external_citation))
        ) else 0

        # Disclosure: an actual statement, or a named "Advertising Policy" /
        # "Affiliate Disclosure" link. A bare "Advertising" nav word is not enough.
        disclosure = 1 if (
            _field(sig_disclosure)
            or disclosure_statement(disclosures)
            or (not sigs and DISCLOSURE_POLICY_RE.search(t))
        ) else 0

        # Currency: a date the READER can see — a labeled date, a bare date at
        # the top of the article, or a '(inferred)' date (by construction
        # visible text). Dates that exist only in JSON-LD / meta tags scored 0
        # with the coders, so the structured columns are used only when the
        # page signals were never scraped. '<date>_current' never counts.
        if sigs:
            currency = 1 if (
                _field(sig_date_label)
                or _field(sig_date_visible)
                or ("(inferred)" in _field(pub_date) and _is_real_date(pub_date))
            ) else 0
        else:
            currency = 1 if (
                _is_real_date(pub_date)
                or _is_real_date(updated_date)
                or DATE_LABEL_RE.search(t[:3000])
            ) else 0

        total = authorship + attribution + disclosure + currency
        return {
            "JAMA_Authorship": authorship,
            "JAMA_Attribution": attribution,
            "JAMA_Disclosure": disclosure,
            "JAMA_Currency": currency,
            "JAMA_Total": total,
        }


class HONcodeAnalyzer:
    """HONcode subset: Authority, Complementarity (0-2)."""

    def score(self, text="", *, authors="", pub_date="", updated_date="",
              disclosures="", url="", has_citations=False,
              external_citation=False,
              sig_byline="", sig_reviewer="", sig_credentialed="",
              sig_date_label="", sig_date_visible="", sig_disclaimer="",
              sig_disclosure="", sig_ref_section=0, sig_n_numbered_cites=0,
              sig_n_external_links=0):
        t = _field(text)

        # Authority: credentialed byline/reviewer OR institutional domain.
        authority = 1 if (
            _authority_signal(authors, sig_credentialed, sig_byline, sig_reviewer)
            or AUTHORITY_DOMAIN_RE.search(_field(url))
        ) else 0

        # Complementarity: "supports, not replaces" — captured from the full
        # page (footer included) by Step 5; body-text cue as fallback.
        complementarity = 1 if (
            _field(sig_disclaimer)
            or DISCLAIMER_RE.search(t)
        ) else 0

        return {
            "HON_Authority": authority,
            "HON_Complementarity": complementarity,
            "HON_Total": authority + complementarity,
        }
