# Google AI Overviews for health questions

Code and data for "Does the Citation Matter? Grounding, Quality, and Physician-Rated Accuracy of Google's AI Overviews
for Medical Information": 3,611 HealthBench questions sent to Google (September 16–18, 2026), each AI Overview compared
with the top five traditional results on source overlap, JAMA and HONcode quality, statement grounding (HallOumi), and
physician-rubric accuracy (GPT-4.1 grader).

## Folders

| Folder | Contents |
|---|---|
| `PIPELINE.md` | How each stage of the pipeline works: the implementation details the paper's appendix leaves out, with a map of the code for each stage. |
| `Supplementary_Material.pdf` | The paper's supplementary material (Supplementary Methods and Results, Figure S1, Tables S1–S12). |
| `analysis/` | `AIO_Overviews_Results.Rmd` reproduces every number, table, and figure in the main paper; `AIO_Overviews_Supplement.R` does the same for the supplement. Knitted output and figures are included. |
| `data/` | The analysis files (one row per question, page, statement, rubric criterion, or GPT-4.1 verdict). |
| `Manual Coding Checks/` | The two human-coding workbooks (JAMA/HONcode pages and HallOumi statements) that the agreement analyses read. |
| `pipeline/scrape/` | Search capture (SerpAPI), page retrieval, JAMA and HONcode coding, and HallOumi scoring (notebooks run locally by `run_pipeline.py` and `run_gpu_scoring.py`). |
| `pipeline/rubric_grading/` | Grading each overview against its HealthBench rubric with GPT-4.1 (`run_stage2.ps1`) and validating the grader on HealthBench's meta-evaluation (`run_stage1e.ps1`). |
| `pipeline/export_*` | How `data/` and the full archive were made from the pipeline outputs. |

## Reproducing the results

With R 4.4 and the packages dplyr, tidyr, tibble, readr, stringr, purrr, readxl, knitr, rmarkdown, ggplot2, ggtext,
and ragg: from `analysis/`, knit `AIO_Overviews_Results.Rmd`, then run `Rscript AIO_Overviews_Supplement.R`. For the robustness check (single-turn questions of 44 words or fewer), uncomment the `SHORT_ONLY`
line at the top of the R Markdown and knit again.

## Data notes

- **No HealthBench text.** Questions are identified by `q_index` (row in the study's question file) and, for the 3,611
  analysed questions, HealthBench's `prompt_id`, which links each one to
  [HealthBench](https://github.com/openai/simple-evals). Rubric criteria appear as their HealthBench indices.
- `questions.csv`: risk level, rubric complexity, theme, turns, length in words, and the 241 excluded questions.
- `overviews.csv`, `sources.csv`, `statements.csv`: AI Overviews, every cited page and traditional result (with JAMA and
  HONcode codes), and every overview statement (with its HallOumi verdict).
- `rubric_items.csv`, `grades_gpt41.csv`, `graded_overviews.csv`: rubric criteria, GPT-4.1's verdicts, overview length.
- `meta_eval_gpt41.csv`: GPT-4.1 and physician grades on the 3,000 meta-evaluation rows used to validate the grader.
- The full pipeline outputs (raw search results, scraped page text, every page and statement) are archived on Zenodo:
  [doi:10.5281/zenodo.23601246](https://doi.org/10.5281/zenodo.23601246).
- API keys are not included; the scripts read them from the environment (`SERPAPI_KEY`, `OPENAI_API_KEY`).
