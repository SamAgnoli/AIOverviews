# How the pipeline works

The paper's appendix describes the methods in brief. This file holds the implementation details it leaves out, stage by stage, and points to the code for each stage.

## Contents

- [Code map](#code-map)
- A. Supplementary Methods
  1. [Search Capture](#1-search-capture)
  2. [Page Retrieval and Text Extraction](#2-page-retrieval-and-text-extraction)
  3. [Readability Screening](#3-readability-screening)
  4. [Analytic Sample](#4-analytic-sample)
  5. [Averaging Page Scores](#5-averaging-page-scores)
  6. [Page Signals Used for Coding](#6-page-signals-used-for-coding)
  7. [JAMA Benchmark Coding](#7-jama-benchmark-coding)
  8. [HONcode Coding](#8-honcode-coding)
  9. [Agreement With Human Coders](#9-agreement-with-human-coders)
  10. [HallOumi Coding](#10-halloumi-coding)
  11. [Physician Rubric Grading](#11-physician-rubric-grading)
  12. [Scoring and Analysis](#12-scoring-and-analysis)
  13. [Known Limitations of the Automated Coding](#13-known-limitations-of-the-automated-coding)
  14. [Software](#14-software)
- B. Supplementary Results
  15. [Analytic Sample](#15-analytic-sample)
  16. [Source Quality](#16-source-quality)
  17. [Physician Rubric Grading](#17-physician-rubric-grading)
  18. [Robustness to Question Length](#18-robustness-to-question-length)
- [References](#references)

## Code map

| Stage | Code | What it does |
|---|---|---|
| Search capture and page scraping | `pipeline/scrape/AIO_WebsiteScrape_local.ipynb` (Steps 1–8.5), `serpapi_client.py`, `run_batch.py`, `run_all.py` | Sends each question to Google through SerpAPI, parses the AI Overview and the traditional results, scrapes every page (Step 5) and forum thread (Step 7), and merges the batches into one results file. `run_all.py` runs the batches back to back. |
| Page datasets, JAMA and HONcode | Notebook Steps 9–13, `run_analysis.py`, `health_scorers.py`, `comparison_builder.py`, `trusted_health_domains_medlineplus.csv` | Reshapes the scrape into one row per page, applies the JAMA and HONcode rules (`health_scorers.py`), and builds the AI Overview vs. traditional-results comparison files. |
| Checking the coding rules | `validate_jama_hon.py`, `README_JAMA_HON_scoring.md` | Compares the automated codes with the human coding worksheet in `Manual Coding Checks/`. |
| Statement grounding | `Halloumi_Scoring_local.ipynb`, `halloumi_scorer.py`, `run_gpu_scoring.py`, `halloumi_ctl.py` and the `.cmd` shortcuts | Runs HallOumi on every cited statement against the pages cited for it. |
| Whole-run drivers | `run_pipeline.py`, `progress.py`, `status.py` | Runs the scrape stages unattended and reports progress. |
| Rubric grading | `pipeline/rubric_grading/`: `scripts/build_deck.py`, `prepare_aio.py`, `make_requests.py`, `batch_openai_seq.py`, `parse_results.py`, `score.py`, run by `run_stage2.ps1`; settings in `config.py` | Builds one GPT-4.1 request per AI Overview and rubric criterion with HealthBench's grader prompt, sends them through the Batch API, parses the verdicts, and scores each overview. |
| Grader validation | `scripts/meta_eval.py`, `scripts/meta_eval_paper.py`, run by `run_stage1e.ps1` | Scores the grader against physicians on HealthBench's meta-evaluation set. |
| Public data | `pipeline/export_public_data.R`, `pipeline/export_full_data.py` | Write `data/` and the full archive without HealthBench text. |
| Analysis | `analysis/AIO_Overviews_Results.Rmd`, `analysis/AIO_Overviews_Supplement.R` | Reproduce every number, table, and figure in the paper, its appendix, and this file. |

API keys are read from the environment (`SERPAPI_KEY`, `OPENAI_API_KEY`) and are not included.

## A. Supplementary Methods

**Source Retrieval, Readability Screening, and Automated JAMA and HONcode Coding**

These Supplementary Materials walk through the coding decisions built into the automated pipeline. First we document how we scraped the pages cited by AIOs and the traditional search results and how we decided which pages were readable enough to score. Then we present the coding decisions on both the JAMA HONcode criteria.

### 1. Search Capture

When Google returned the AIO as a page token, a follow-up request retrieved its full text and citations. A placeholder that returned no content was recorded as an empty overview rather than as a missing one. If a search failed, it was reattempted once more (only one question failed both attempts).

*AIO citations.* Every page the overview cited was captured. For a subset of citations, Google used click-tracking redirects (google.com/url or google.com/goto) whose destinations cannot be decoded offline. The code resolved each to its publisher URL with a streamed HTTP request before the result was parsed to ensure that domains all refer to their real page. URL fragments (#…) were removed, and a URL cited more than once by the same overview was kept once.

*Traditional results.* Google’s separate “Discussions and forums” panel was recorded but not scored, because it is a distinct page feature rather than a ranked result; forum threads that ranked within the top five were kept. For 159 of 3,481 questions the first results page held fewer than five eligible traditional results (64 held none).

### 2. Page Retrieval and Text Extraction

PubMed and PubMed Central links were sent first to the Europe PMC API, which serves the same articles’ full text without bot checks.

A fetch counted as successful only if its content was a real page rather than a bot-check, CAPTCHA, or error page served with a normal status code (e.g., “Checking your browser,” “Just a moment…,” “Access denied”). Requests to the same host were spaced at least 1 second apart. Downloads were capped at 25 MB for HTML and 15 MB for PDFs, only the first 60 pages of a PDF were extracted (for computational reasons), and the client advertised only the compression formats it could decode. Table S1 shows which method supplied the readable pages.

Main text was extracted with trafilatura in its recall-oriented mode (tables included, reader comments excluded). If that yielded fewer than 500 characters, trafilatura’s default settings were tried, followed by the visible text of the page’s main or article element after removing navigation, headers, footers, sidebars, forms, and advertising containers. The longest result across attempts was kept. PDF text was extracted with pdfplumber. When every method failed, the search-result snippet was stored so that the record was not blank, but the page was labeled snippet-only and never scored.

*Forum threads.* The first forum thread per question that ranked in the traditional top five was retrieved with a forum handler code instead of the page scraper. Every other forum page (one cited by an AIO, or a second forum thread in the traditional top five) went through the ordinary page scraper and was coded like any other page on its page text (e.g., Mayo Clinic Connect threads; 47 AIO-cited and 34 traditional pages in the paired sample).

**Table S1.** Retrieval Method of the Readable Pages

| Method | AIO citations | Traditional results | % of readable pages |
|---|---|---|---|
| Direct HTTP request | 17,492 | 10,384 | 73.2 |
| Headless browser (Playwright) | 2,149 | 1,331 | 9.1 |
| Jina Reader proxy | 1,710 | 1,414 | 8.2 |
| Europe PMC full text | 1,511 | 766 | 6.0 |
| trafilatura fetcher | 621 | 441 | 2.8 |
| Wayback Machine archive | 127 | 77 | 0.5 |
| Europe PMC abstract | 27 | 17 | 0.1 |
| AMP version | 20 | 13 | 0.1 |
| Total | 23,657 | 14,443 | 100.0 |

*Note.* Readable pages for the 3,474 questions that passed the overview-text check. PDFs (about 1.8% of readable pages) were fetched mostly by direct request.

### 3. Readability Screening

Every fetched page received exactly one readability label, assigned from its extracted text when the page was captured (Table S2). Only pages labeled readable were coded and used on subsequent analyses (i.e., JAMA, HONcode, and HallOumi analyses). The binary check guards against undecoded responses and wrongly encoded text, and the interstitial check applies only to short texts. See Table S2 for all checks.

**Table S2.** Readability Labels and Their Frequencies

| Label | Rule (checked in this order) | AIO citations | Traditional results |
|---|---|---|---|
| Snippet only | Every retrieval method failed; only the search-result snippet was stored | 2,512 | 1,563 |
| Empty | Every retrieval method failed and no snippet was available | 19 | 0 |
| Binary | In the first 4,000 characters, more than 2% are Unicode replacement characters (U+FFFD) or more than 2% are control characters | 48 | 11 |
| Interstitial | Fewer than 2,500 characters, and the first 1,500 contain a bot-check, CAPTCHA, or error phrase | 0 | 1 |
| Too short | Fewer than 300 characters of text | 544 | 328 |
| Forum record | Thread captured by the forum handler (no label assigned; see text) | 0 | 457 |
| Readable | None of the above | 23,657 | 14,443 |
| Total |  | 26,780 | 16,803 |

*Note.* Counts are pages for the 3,474 questions that passed the overview-text check. Interstitials are rare because a fetch that returned one was rejected and the next retrieval method was tried.

### 4. Analytic Sample

*Note to the sample-flow table in the appendix.* Thirty-two overviews had neither a cited page nor a traditional result, which counted as uncited overviews but contributed no pages to analyses.

### 5. Averaging Page Scores

The two-arm means were compared within question (paired t test). References listed on the cited pages themselves (second-level citations) were captured and scored but were not used in any analysis as they were beyond the scope of the current study.

### 6. Page Signals Used for Coding

Every readable page in this run had its signals extracted, so the pipeline’s fallback rules for pages without signals (based on scraped metadata) were never used.

**Table S4.** Page Signals Extracted From Each Page

| Signal | How it was detected |
|---|---|
| Byline | A byline cue (“By,” “Written by,” “Authored by,” “Author:,” “Contributor”) followed by a capitalized personal name of at least two words. Text beginning with an organizational word (e.g., “the,” “our,” “staff,” “editorial,” “team”) was skipped. |
| Reviewer | A review cue (“Reviewed by,” “Medically/Clinically/Expert reviewed by,” “Fact-checked by,” “Verified by”) followed by a personal name. |
| Credentialed person | A professional credential (Table S5 note) within 90 characters after a byline, reviewer, or contributor cue, or a structured-data (JSON-LD) author or reviewer whose name, job title, or honorific carries a credential. |
| Labeled date | The first date anywhere on the page introduced by a label such as “Last updated,” “Last reviewed,” “Medically reviewed,” “Updated,” “Reviewed,” “Revised,” “Published,” “Posted,” or “Written.” |
| Visible date | The first unlabeled date within the first 1,200 characters of the article or main region, excluding dates preceded by ©, “Copyright,” or “(c).” |
| Disclaimer | The first “supports, does not replace” cue anywhere on the page, footer included (cues in Table S6). |
| Disclosure | The first sentence of at least six words containing a disclosure cue, or a named policy link (cues in Table S5 note). |
| Reference section | A short element (at most four words and 40 characters: heading, button, summary, bold text, or link) reading “References,” “Sources,” “Citations,” “Bibliography,” “Works cited,” “Footnotes,” “View sources,” “12 sources,” “Article sources,” or a similar variant. “Resources” was excluded because it is usually a navigation label. |
| Numbered citations | Links whose text is a number of one to three digits, with or without brackets (“[3]”); links inside superscripts; and in-page anchors to #ref, #cite, #fn, #foot, #source, #note, or #bib. |
| External links in prose | Distinct links inside paragraphs, list items, table cells, and block quotes of the article or main region that point to a different registered domain than the page after removing navigation, header, footer, sidebar, form, share, related-content, promotional, and comment blocks. Links to patient portals, booking or payment pages, app stores, link shorteners, social media, and video sites were ignored. |

*Note.* Dates were recognized in the forms “March 5, 2024,” “5 March 2024,” “March 2024,” “3/5/2024,” and “2024-03-05.”

### 7. JAMA Benchmark Coding

Each JAMA criterion was scored on a binary 0 or 1 per page, and the JAMA total is their sum (0–4). A page qualifies when its host is a listed domain or any subdomain of one (e.g., www.cdc.gov, pmc.ncbi.nlm.nih.gov, health.harvard.edu).

**Table S5.** JAMA Benchmark Coding Rules

| Criterion | Scored 0 (failed examples) |
|---|---|
| Authorship | Bare names without credentials, organizational bylines (“By HealthPartners”), and the scraper’s “[organization] editorial team” placeholder, unless the domain is on the trusted list. |
| Attribution | Links to the site’s own pages, navigation, or footer links. |
| Disclosure | A bare “Advertising” navigation item. |
| Currency | Dates present only in page metadata or structured data, and the access date the scraper records when a page shows no date. |

*Note.* Credentials recognized (case-sensitive, as standalone tokens): MD, DO, MBBS, MBChB, PhD, PharmD, RN, BSN, MSN, DNP, APRN, CRNA, CNM, NP, FNP, PNP, PA-C, MPH, MHA, DDS, DMD, DPM, RDN, LDN, LCSW, PsyD, DPT, RPh, CDCES, and fellowship suffixes (FACP, FACS, FACG, FAAP, FACC, FACEP, FACOG, FAAFP). Titles recognized (any case): physician assistant, nurse practitioner, registered nurse, registered dietitian, board-certified, medical doctor, family physician, emergency physician, internist, surgeon, cardiologist, dermatologist, gastroenterologist, neurologist, oncologist, pediatrician, and psychiatrist, as well as “Dr.” before a capitalized name. “MD” followed by a five-digit ZIP code was read as a state abbreviation. Disclosure cues: conflict of interest, competing interests, sponsored by, funded by, funding source, receives funding, affiliate links or commissions, financial support, disclosure, relationships, or ties, editorial independence, paid partnership or sponsorship, “advertising on our site helps support …,” “we do not endorse,” “no conflicts to disclose,” and nonprofit-medical-center advertising statements. Named policy links: advertising, advertiser, sponsorship, affiliate, funding, financial, or disclosure followed by policy, disclosure, statement, or guidelines (e.g., “Advertising Policy,” “Affiliate Disclosure”).

### 8. HONcode Coding

HONcode was operationalized as two binary criteria, for a total of 0–2 (Table S6). HONcode Attribution was dropped because the human coders’ judgments duplicated JAMA Attribution, and Justifiability was dropped because it could not be coded reliably from page text.

**Table S6.** HONcode Coding Rules

| Criterion | Scored 1 when |
|---|---|
| Authority | A credentialed person is named (the same signal as JAMA Authorship), and/or the page is on an institutional domain: any .gov (including national government domains such as .gov.au), .edu, or NHS domain (nhs.uk, NHS inform); CDC, NIH, NLM/MedlinePlus, WHO, or FDA; the academic health systems Mayo Clinic, Cleveland Clinic, Johns Hopkins Medicine, Mass General Brigham, Mount Sinai, Stanford Health Care, UCLA Health, UCSF Health, UPMC, NYU Langone, Cedars-Sinai, Penn Medicine, Michigan Medicine, UChicago Medicine, Duke Health, Yale Medicine, and Northwestern Medicine; or KidsHealth, HealthyChildren.org, healthdirect Australia, the American Cancer Society, American Heart Association, American Lung Association, American Diabetes Association, National Kidney Foundation, AAFP, ACOG, or AAP. |
| Complementarity | The page positions itself as supporting rather than replacing care, with a cue anywhere on the page including the footer: e.g., “consult / see / talk to your doctor” (or physician, provider, pharmacist, specialist, or clinician), “seek medical / emergency care,” “not a substitute for professional medical advice,” “for informational purposes only,” “does not constitute medical advice,” “go to the emergency room,” or “call 911.” |

### 9. Agreement With Human Coders

*Note to the agreement table in the appendix.* κ = Cohen’s kappa between the automated code and the human judgment, with one coder per page.

### 10. HallOumi Coding

Built by Oumi AI, HallOumi is a publicly available model designed to test AI-generated text against source documents, returning a per-sentence supported/unsupported verdict with a confidence score [2].

This gate may be liberal, and was inferred after failing to receive a response from the Oumi team about the details of their own protocol. Two refinements were applied to reduce known false negatives: bullet labels (e.g., "Healthy fats: olive oil, avocados…") were stripped before scoring, since the label is not itself a claim; and where a statement's subject was an enumeration of three or more items sharing one predicate, the claim was decomposed into per-item sub-claims and the majority rule applied, so that a source omitting one listed item did not fail the entire statement. Scanning of a page stopped once a window reached the support threshold to lower the computational burden of the analysis. This affects only the reported probability, which becomes the first window at or above threshold rather than the page maximum, meaning that the final supported/unsupported verdicts are unchanged.

This procedure yielded four verdicts. Supported: cited and backed by a cited page. Unsupported: cited, all cited pages were readable, and none supported the statement. Indeterminate (reported as skipped): the readable cited pages did not support the statement, and at least one cited page could not be recovered, so support could not be ruled out. Uncited: The AIO attached no citation, and the statement was therefore not scorable. Uncited statements were further classified as section headers, conversational turns directed at the user (a frequent characteristic of Gemini's AIOs), or uncited factual claims.

Across all overviews, 24,060 statements were enumerated, of which 12,175 were scorable against a cited source. We report grounding as the proportion of scorable statements that are supported. Uncited factual statements are reported separately as a count and are not folded into the denominator, because a claim Google left uncited is a different phenomenon from a claim its own cited source does not support.

HallOumi agreed with the coders on 81% of statements (Cohen’s κ = .61, 95%). In 16 of the 19 disagreements the coder judged the statement supported and HallOumi did not: coders judged 67% of the statements supported and HallOumi 54%. Agreement was similar for the two coders (κ = .61 and .58).

The interval comes from a bootstrap (5,000 resamples) that resampled the validation statements by overview (29 overviews) and the 12,175 scorable statements by question. It therefore carries the uncertainty of both samples and is so wide because our validation was only 100 statements.

### 11. Physician Rubric Grading

*Rubrics.* Each study question was matched to its HealthBench conversation by its exact text. By HealthBench axis, the criteria concerned completeness (16,762), accuracy (13,901), context awareness (6,193), communication quality (3,191), and instruction following (1,133).

*Grader Validation.* As the benchmark, each physician was scored the same way against the other physicians on the same rows, and physicians’ scores were averaged with weights equal to their numbers of grades, as in HealthBench (physician average: .681).

*Grading Procedure.* The grader returned a brief explanation and a judgment of whether the criterion was met; all 41,180 outputs parsed to a judgment.

### 12. Scoring and Analysis

Table S8 defines the scores and rates reported in the main text, as defined by Arora et al. [1]. AIO scores could fall below zero when undesirable criteria are met.

**Table S8.** Definitions of the Rubric Scores and Rates Reported for AI Overviews

| Measure | Definition |
|---|---|
| *Score (% of available points)* | |
| All criteria | Every criterion in the AIO’s rubric. |
| Accuracy | Whether the response contains only factually correct information aligned with current medical consensus, including acknowledging uncertainty where the evidence is weak or still evolving. |
| Completeness | Whether the response includes the information needed to be safe and helpful, such as key steps, warning signs, and caveats. |
| Context awareness | Whether the response responds to contextual cues, such as the user’s role, location, or available resources, and seeks clarification when needed. |
| Communication quality | Whether the response is clear, well structured, and concise, with technical depth and vocabulary suited to the user. |
| Instruction following | Whether the response follows the user’s explicit instructions, such as a requested format or type of clinical note, prioritizing safety. |
| Consensus criteria only | The 34 predefined criteria (from at least two physicians’ agreement) that HealthBench attaches to a conversation in a theme-specific category. All other criteria were written by physicians for the individual conversation. |
| *Rate (% of AI Overviews)* | |
| Contained content flagged as inaccurate or potentially harmful | Met at least one negative-point accuracy criterion. These criteria were written by physicians for the individual question, and each describes a specific error, overstatement, or omission to explicit avoid for the potential harm they might cause. |
| Met “no factually incorrect information” | Consensus criterion for responding-under-uncertainty and response-depth conversations, met when the response contains no factually incorrect information. For response-depth conversations, it also requires every claim to be unambiguously correct, and no correct claim to be hedged unnecessarily. |
| Referred appropriately (emergency-referral questions) | Consensus criterion for emergency-referral conversations, met when the response recommends in-person care with urgency suited to the situation as physicians classified it. A clear referral within the first few sentences for emergent conversations, the circumstances that warrant immediate care for conditionally emergent ones, and care on an appropriate timeline, without alarm, for non-emergent ones. |

### 13. Known Limitations of the Automated Coding

*Text-only retrieval routes.* The other criteria rely on text cues alone, so were not affected.

*Forum content.* Forum threads captured by the forum handler (mostly Reddit) and the Reddit threads cited by AIOs were not coded on JAMA or HONcode. The forum pages that were coded (81 in the paired sample) were scored on their page text like ordinary pages, not on a defined set of comments.

Trusted-domain dependence. JAMA Authorship depends on which organizations appear on the MedlinePlus list. For example, Mayo Clinic is listed, whereas Cleveland Clinic and the NHS are not.

*Pattern-based detection.* Signals are found with English-language text patterns. Credentials shown only in images, unusual page layouts, and non-English pages can be missed, and the coding cannot verify that a stated credential is genuine.

### 14. Software

Retrieval and coding ran in Python 3.13.2 with requests 2.34.2, trafilatura 2.2.0, Beautiful Soup 4.15.0 (lxml 6.1.2), Playwright 1.62.0 (Chromium), pdfplumber 0.11.10, pandas 3.0.5, and the SerpAPI client google-search-results 2.4.2. Statistical analyses used R 4.4.1. The coding rules are implemented in health_scorers.py and the page signals in the page-scraper step of the pipeline notebook; both, together with the trusted-domain list, are available on GitHub.

## B. Supplementary Results

### 15. Analytic Sample

Generally, academic health systems and government health services were more frequently cited by traditional results than were AIOs (Cleveland Clinic, 8.0% vs. 4.7%, t(3102) = −15.09, p < .001, d<sub>z</sub> = −0.27; Mayo Clinic, 5.8% vs. 3.9%, t(3102) = −7.63, p < .001, d<sub>z</sub> = −0.14; NHS, 3.1% vs. 1.5%, t(3102) = −10.72, p < .001, d<sub>z</sub> = −0.19; Healthdirect Australia, 1.3% vs .7%, t(3102) = -4.83, p < .001, d<sub>z</sub> = −0.09). In contrast, commercial health publishers made up a larger share of AIO citations than of traditional results (WebMD, 2.0% vs. 1.4%, t(3102) = 5.48, p < .001, d<sub>z</sub> = 0.10; Healthline, 1.8% vs. 1.3%, t(3102) = 4.35, p < .001, d<sub>z</sub> = 0.08; Ubie Health, 1.4% vs. 0.9%, t(3102) = 5.12, p < .001, d<sub>z</sub> = 0.09). This provides initial evidence that AIOs draw on less rigorous sources than traditional ranked search results.

*Note to the domain table in the appendix.* Percentages are within each set: of all 23,657 readable AI Overview citations and of all 14,443 readable traditional results, so the two columns are comparable despite the different set sizes. t, p, and d<sub>z</sub> compare, across n = 3,103 paired questions, the share of each question's AI Overview citations and the share of its traditional results that came from the domain (paired t tests; p values Benjamini–Hochberg adjusted across the 12 domains).

### 16. Source Quality

*Note to the quality table in the appendix.* Criteria are scored 0/1.

### 17. Physician Rubric Grading

*Note to the theme table in the appendix.* Theme names follow HealthBench [1].

### 18. Robustness to Question Length

By risk level, the subset held 17, 70, and 573 low-risk questions; 108, 103, and 510 medium-risk questions; and 109, 9, and 215 high-risk questions of low, medium, and high complexity, respectively.

**Table S12.** Main Results in the Full Sample and in Single-Turn Questions of 44 Words or Fewer

| Measure | Full sample | Single-turn, ≤ 44 words |
|---|---|---|
| *Sample* | | |
| Questions | 3,611 | 1,714 |
| AI Overview returned (% of captured searches) | 96.4 [95.8, 97.0] | 96.2 [95.2, 97.0] |
| AI Overviews with usable text (graded) | 3,474 | 1,648 |
| Paired questions (readable pages in both arms) | 3,103 | 1,531 |
| *AI Overviews citing no sources (%)* | | |
| Question length, uncited vs. cited (words) | 73.5 vs. 40.5, d = 0.75\*\*\* | 23.2 vs. 18.8, d = 0.40\*\*\* |
| *Source quality, d<sub>z</sub> (AI Overview − traditional)* | | |
| JAMA Authorship | 0.00 [−0.04, 0.03] | −0.02 [−0.07, 0.03] |
| JAMA Currency | −0.09 [−0.12, −0.05]\*\*\* | −0.06 [−0.11, −0.01]\* |
| HON Complementarity | −0.02 [−0.06, 0.01] | 0.03 [−0.02, 0.08] |
| Risk and complexity levels favoring traditional (JAMA and HON totals) | 12 of 12 (12 with p < .05) | 12 of 12 (7 with p < .05) |
| *Statement grounding (%)* | | |
| Uncited factual statements (% of all statements) | 20.8 | 16.5 |
| Supported, low / medium / high complexity | 52.2 / 47.4 / 46.3; χ²(2) = 16.57\*\*\* | 55.5 / 50.5 / 48.9; χ²(2) = 12.31\*\* |
| Supported, low / medium / high risk | 45.0 / 48.9 / 46.4; χ²(2) = 14.86\*\*\* | 48.0 / 51.6 / 49.8; χ²(2) = 6.26\* |
| *Physician rubric scores (% of available points)* | | |
| All criteria | 38.2 [37.0, 39.3] | 39.3 [37.6, 40.9] |
| Accuracy | 54.2 [52.6, 55.8] | 52.5 [50.2, 54.8] |
| Completeness | 21.6 [19.4, 23.7] | 21.5 [18.5, 24.6] |
| Communication quality | 64.7 [61.7, 67.5] | 70.8 [66.5, 75.0] |
| Clinical, low / medium / high complexity | 76.7 / 28.8 / 35.7 | 82.7 / 27.7 / 33.3 |
| Clinical, low / medium / high risk | 26.7 / 43.4 / 52.4 | 25.4 / 43.5 / 58.6 |
| Clinical without referral criteria, low / medium / high risk | 22.5 / 41.3 / 35.9 | 21.3 / 41.2 / 32.9 |
| *Physician rubric rates (% of AI Overviews)* | | |
| Inaccurate or potentially harmful content | 22.2 [20.5, 24.1] | 21.9 [19.4, 24.6] |
| No factually incorrect information | 93.4 [91.5, 94.9] | 95.8 [93.1, 97.4] |
| Referred appropriately (emergency referrals) | 94.9 [92.4, 96.7] | 93.6 [89.7, 96.1] |
| *Clinical score and citations* | | |
| Uncited minus cited AI Overviews (points) | 1.1 [−3.8, 6.1] | 1.9 [−6.3, 10.0] |

*Note.* Full sample = all 3,611 questions; subset = single-turn questions of 44 words or fewer. We used question-level bootstrap intervals for source overlap and statement support because pages and statements are nested within questions. We used bootstrap intervals for rubric scores, the paired t interval divided by the SD of the differences for d<sub>z</sub>, Fisher z intervals for ρ, and a Welch interval for the uncited-minus-cited difference.

## References

[1] Rahul K. Arora, Jason Wei, Rebecca Soskin Hicks, Preston Bowman, Joaquin Quiñonero-Candela, Foivos Tsimpourlas, Michael Sharman, Meghan Shah, Andrea Vallone, Alex Beutel, Johannes Heidecke, and Karan Singhal. 2025. HealthBench: Evaluating Large Language Models Towards Improved Human Health. arXiv:2505.08775 [cs.CL] doi:10.48550/arXiv.2505.08775

[2] Oumi AI. 2025. HallOumi-8B. Hugging Face. https://huggingface.co/oumi-ai/HallOumi-8B

[3] Walter J. Rogan and Beth Gladen. 1978. Estimating Prevalence from the Results of a Screening Test. American Journal of Epidemiology 107, 1 (1978), 71–76. doi:10.1093/oxfordjournals.aje.a112510
