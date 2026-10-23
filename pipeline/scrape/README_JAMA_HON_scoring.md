# JAMA / HONcode scoring — what changed on 2026-09-16 and how to use it

Read this before touching `health_scorers.py`, Step 5 / 11 / 12 of
`AIO_WebsiteScrape_local.ipynb`, or before a full local run. It records the
decisions made while calibrating the automated JAMA / HONcode scores against
the human coding worksheet
(`C:\Users\SAgno\Documents\JAMA_HON_CodingWorksheet_LilyBrigidSam.xlsx`,
sheets `Coding Worksheet _Human` and `Coding Worksheet _Code`).

## 1. Why the old scores disagreed with the humans

Two root causes, both confirmed by re-fetching every worksheet URL:

1. **The scorer only saw the trafilatura body text.** The things the humans
   used to code a page — `By Barbara Bolen, PhD`, `Medically reviewed by
   Sonal Kumar, MD`, `Last updated on 05/05/2022`, the footer "not a
   substitute for professional medical advice", the `References` / `View
   Sources` heading — all sit outside the extracted article text, so the
   code could not see them. HON_Complementarity agreed on 11 of 63 rows.
2. **The 2026-08-26 datasets hold binary garbage for ~30% of sources**
   (the Brotli `Accept-Encoding` bug, fixed in Step 5 on 08-27). Cleveland
   Clinic, Mass General, eer24, gohealthuc and others held 1-90 bytes of
   junk flagged `text_quality == ok`, so every text-based rule scored 0.
   Those datasets were **not** re-fetched; the next full local run
   supersedes them.

Old agreement with the worksheet: ~57% overall. New: 86% overall,
Sam 23/24 (96%), Brigid 86%, Lily 83%.

## 2. Variables now produced

| column | rule (1 = met) |
|---|---|
| `JAMA_Authorship` | a **credentialed** author or reviewer is shown (MD, M.D., DO, PhD, RN, NP, PA-C, MPH, PharmD, "Physician Assistant", "Nurse Practitioner", "board-certified", ...). Bare names ("Michelle Schmanski", "Brent"), org bylines ("by HealthPartners"), and the scraper's "X editorial team (inferred)" fallback are 0. Exception (2026-10-02): any page on a domain in `trusted_health_domains_medlineplus.csv`, or a subdomain of one, is 1. |
| `JAMA_Attribution` | a References / Sources / Citations / Bibliography / View Sources heading, **or** numbered `[n]` / superscript citation links, **or** >= 2 links to *other* domains inside the article prose. Internal site links never count. |
| `JAMA_Disclosure` | a real >= 6-word disclosure sentence ("Advertising on our site helps support our mission", "conflict of interest", "sponsored by", "funded by", "we do not endorse", ...) **or** a named policy link ("Advertising Policy", "Affiliate Disclosure", "Sponsorship Policy"). A bare "Advertising" nav word is 0. |
| `JAMA_Currency` | a date the reader can **see**: a labeled date ("Last updated on ...", "Published on: ...", "Posted on ...", "Last reviewed: March 2026"), a bare date in the top ~1200 characters of the article, or a date the scraper tagged "(inferred)" (which by construction came from visible text). Dates that exist only in JSON-LD / meta tags scored 0 with the coders and are ignored. The `<date>_current` access-date placeholder never counts. |
| `JAMA_Total` | 0-4 |
| `HON_Authority` | the credentialed-byline signal above **or** an institutional domain (`.gov`, `.edu`, NHS, CDC/NIH/WHO/FDA, Mayo, Cleveland Clinic, Hopkins, Mass General Brigham, Mount Sinai, Stanford, UCLA, UCSF, UPMC, NYU Langone, Cedars-Sinai, Penn, Michigan, Chicago, Duke, Yale, Northwestern, KidsHealth, AHA/ACS/ALA/ADA/NKF, AAFP/ACOG/AAP, healthdirect, NHS inform, HealthyChildren). |
| `HON_Complementarity` | any "supports, does not replace" cue anywhere on the page, footer included: "consult / see / talk to your doctor / provider / physician", "seek medical care / attention / advice", "not a substitute for", "for informational purposes only", "does not constitute medical advice", "call 911", "go to the emergency room". |
| `HON_Total` | 0-2 |

**Dropped:** `HON_Attribution` (duplicated `JAMA_Attribution` in the human
codes) and `HON_Justifiability` (not codeable). Step 12 deletes stale copies
of both from the datasets, including the `cite{j}_` versions.
`comparison_builder._SCORE_COLS` and the Data Dictionary were updated.
**The R analysis (`R_Analysis/*.Rmd`) still references both columns and needs
those lines removed.**

Sub-citations (`cite1_` ... `cite5_`) are scored with the same rules and
carry the same column names with the `cite{j}_` prefix (AIO dataset only).

## 3. The page-signal columns (`sig_*`)

Step 5's `scrape_page` now calls `extract_quality_signals(soup, url)`, which
reads the **full** HTML (scripts/styles removed, nothing else stripped) and
emits ten columns. `SIG_COLS` in `health_scorers.py` is the single source of
truth for the list; Steps 5, 8, 9, 9R, 11 and 12 all import it.

| column | content |
|---|---|
| `sig_byline` | first visible byline / contributor line (`By Barbara Bolen, PhD ...`, `Contributor Ronak Shah, MD`) |
| `sig_reviewer` | first `(Medically) reviewed by ...` line |
| `sig_credentialed` | 1 if a credential token sits within ~90 chars after a byline / reviewer / contributor cue, or JSON-LD `author` / `reviewedBy` carries one |
| `sig_date_label` | first labeled date on the page |
| `sig_date_visible` | first bare date in the top ~1200 chars of the article (copyright years excluded) |
| `sig_disclaimer` | first complementarity cue, with context |
| `sig_disclosure` | first disclosure sentence or policy link |
| `sig_ref_section` | 1 if a short heading / button reads References, Sources, Citations, Bibliography, View Sources, "20 Sources" ... ("Resources" deliberately excluded — it is nav) |
| `sig_n_numbered_cites` | count of `[n]` anchors, `<sup><a>` links, and `#ref` / `#cite` / `#fn` anchors |
| `sig_n_external_links` | distinct links to other registered domains inside `<p>` / `<li>` of the article after stripping nav / header / footer / aside; portals, booking, billing and social hosts excluded |

They flow: Step 5 result dict -> Step 8 `aio_src{N}_sig_*`, `org{N}_sig_*`,
`{prefix}_cite{i}_sig_*` -> Step 9 `sig_*`, `cite{j}_sig_*` on both datasets
-> Steps 11/12 pass them to `JAMAAnalyzer.score` / `HONcodeAnalyzer.score`.
Step 9R overwrites them when it repairs a row. PDFs get empty signals.

**Fallback behaviour.** When `sig_credentialed` is `''` (rows scraped before
2026-09-16, or PDFs) the scorer uses the structured columns instead:
credential regex on `authors`, an off-domain `cite{j}_url` for attribution,
the tight disclosure regex on `disclosures`, and any real date in
`pub_date` / `updated_date`. When signals **are** present those fallbacks are
switched off, because the inline-link citation fallback surfaces booking /
portal links and metadata-only dates were rejected by the coders.

## 4. Validating after any rule change

```bash
.\.venv\Scripts\python.exe validate_jama_hon.py
```

Live-scrapes every URL in the worksheet (~30 pages, ~25 s, no SerpAPI)
through the notebook's own Step 5, scores with `health_scorers.py`, prints
per-coder agreement per variable and a row table with mismatches starred,
and writes `Output/jama_hon_validation.csv` (all signals + scores + human
means). `--worksheet path.xlsx` points it at a different coding sheet;
`--limit N` scrapes only the first N URLs.

Targets: Sam is the gold standard (aim for >= 23/24); Lily and Brigid are
secondary. Known irreducible disagreements: Lily counts urgent-care clinic
pages as authored/authoritative; Brigid gives Cleveland Clinic Authority 0
(the user chose to keep institutional domains); Lily scored Healthline's
"Advertising Policy" 0 while the user chose to count policy links.

## 5. Before the next full local run

1. Nothing extra to do — `run_all.py` / `run_batch.py` execute the patched
   Step 5 and Step 8, so the `sig_*` columns are populated at scrape time.
2. After Step 8.5, run `run_analysis.py` (Steps 9 -> 13) as before.
3. If you ever need to repair an already-scraped dataset without SerpAPI,
   Step 9R now refreshes the `sig_*` columns too.
4. Keep `health_scorers.py` and `comparison_builder.py` in `LocalRun/` as the
   canonical copies; the ones in `AIO_Workspace/` root and the Colab
   notebook (`AIO_WebsiteScrape_8_25.ipynb`) were **not** updated.

## 6. Files touched on 2026-09-16

- `health_scorers.py` — rewritten (rules, `SIG_COLS`, shared regexes).
- `AIO_WebsiteScrape_local.ipynb` — Step 5 (`extract_quality_signals`,
  `scrape_page`, live-test printout), Step 8 (`SIG_COLS`, `flatten_citations`,
  `process_question`), Step 9 (`_sig_val`, pass-through), Step 9R, Steps 11
  and 12 (`_row_signals`, `_external_citation`, stale-column drop, headers).
  Backup of the pre-change notebook: `AIO_WebsiteScrape_local.ipynb.bak_2026-09-16`.
- `comparison_builder.py` — `_SCORE_COLS`, Data Dictionary entries.
- `validate_jama_hon.py` — new.
- `Output/jama_hon_validation.csv` — output of the last validation run.
