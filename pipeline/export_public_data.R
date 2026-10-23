## Writes the analysis files in data/ from the pipeline outputs, without HealthBench text.
## Every question is identified by q_index (its row in the study's question file, 1-3,852) and, for the 3,611 analysed
## questions, HealthBench's prompt_id. Question text, conversation text and rubric criteria text are dropped; the
## measures the analyses need from them (question length in words, turn counts, the referral flag) are computed here.
## Needs the private inputs below (they contain HealthBench text), so it can only be run by the authors.
## Run from pipeline/:  Rscript export_public_data.R
suppressPackageStartupMessages({
  library(dplyr); library(tidyr); library(readr); library(stringr); library(purrr); library(readxl); library(jsonlite)
})

# ---- private inputs
RUN   <- "C:/Users/SAgno/Documents/AIO_Workspace/LocalRun"                  # research_output.csv, Output/
QFILE <- "C:/Users/SAgno/Documents/AIO_Workspace/R_Analysis/healthbench_questions_AIO_1_flagged.xlsx"
HB    <- "C:/Users/SAgno/Documents/AIO_HealthBench_Claude/aio_healthbench"   # rubric deck, GPT-4.1 grades, meta-eval
OUT   <- "../data"
dir.create(OUT, showWarnings = FALSE)

norm_q <- function(x) str_squish(gsub('"', "'", stringi::stri_trans_nfkc(x), fixed = TRUE))   # the pipeline wrote " as '
words  <- function(x) str_count(x, "\\S+")
jl     <- function(f) stream_in(file(f, encoding = "UTF-8"), verbose = FALSE)
rd     <- function(f) suppressWarnings(read_csv(f, guess_max = 1e6, show_col_types = FALSE, col_types = cols(.default = "c")))
save   <- function(d, f) { write_csv(d, file.path(OUT, f), na = ""); cat(sprintf("%-26s %7d rows  %6.1f MB\n", f, nrow(d),
                                                                            file.size(file.path(OUT, f)) / 1e6)) }

# ---- questions: one row per question in the study's question file
qf  <- read_excel(QFILE) %>% mutate(q_index = row_number(), key = norm_q(question))
stopifnot(!anyDuplicated(qf$key))
idx <- function(q) { i <- match(norm_q(q), qf$key); stopifnot(!anyNA(i)); qf$q_index[i] }
deck <- map(readLines(file.path(HB, "deck", "rubric_deck.jsonl"), encoding = "UTF-8"), function(l) {
  d <- fromJSON(l)
  list(q = tibble(q_index = d$q_index, prompt_id = d$prompt_id, theme = d$themes[1], n_user_turns = d$n_user_turns,
                  has_assistant_turns = d$has_assistant_turns, deck_words = words(d$question), key = norm_q(d$question)),
       r = tibble(q_index = d$q_index, prompt_id = d$prompt_id,
                  d$rubrics[, c("item_idx", "criterion", "points", "axis", "level", "interaction_dependent",
                                "emergency_behavior", "accuracy_check")])) })
dq <- map_dfr(deck, "q"); items <- map_dfr(deck, "r")
stopifnot(all(qf$key[dq$q_index] == dq$key))            # q_index in the deck = row of the question file
dq <- dq %>% select(-key)
questions <- qf %>% transmute(q_index, excluded = as.integer(remove_flag == 1), risk_level, rubric_complexity,
                              q_words = words(question)) %>%
  left_join(dq, by = "q_index") %>%
  relocate(prompt_id, .after = q_index)
stopifnot(sum(!is.na(questions$prompt_id)) == 3611, all(is.na(questions$prompt_id) == (questions$excluded == 1)),
          all(questions$q_words[!is.na(questions$deck_words)] == questions$deck_words[!is.na(questions$deck_words)]))
# boot_order: the order in which the original analysis grouped questions (by the question text the pipeline wrote,
# sorted in the C locale, as dplyr::group_by does). Bootstraps that resample questions use it, so they reproduce the
# published intervals without the text.
OUTPUT <- file.path(RUN, "Output")
cq <- rd(file.path(OUTPUT, "comparison_questions.csv"))
ptext <- qf$question; ptext[idx(cq$question)] <- cq$question
questions$boot_order <- match(ptext, sort(ptext, method = "radix"))
save(questions %>% select(-deck_words), "questions.csv")

# ---- searches: one row per question submitted to Google
ro <- read_csv(file.path(RUN, "research_output.csv"), col_select = c(question, search_date, aio_present, error),
               col_types = cols(.default = "c"), show_col_types = FALSE)
save(ro %>% transmute(q_index = idx(question), search_date, aio_present, error) %>% arrange(q_index), "searches.csv")

# ---- overviews, sources and statements (pipeline outputs: comparison_questions/_sources, statement_scores)
drop_cols <- function(d) d %>% select(-question, -any_of("requires_hedging"), -contains("gispy"))
stopifnot(all(words(cq$question) == questions$q_words[idx(cq$question)]))
save(cq %>% mutate(q_index = idx(question), .before = 1) %>% drop_cols() %>% arrange(q_index), "overviews.csv")
cs <- rd(file.path(OUTPUT, "comparison_sources.csv"))
ss <- rd(file.path(OUTPUT, "statement_scores.csv"))
stopifnot(all(c(cs$question, ss$question) %in% cq$question))      # same question strings as comparison_questions
save(cs %>% mutate(q_index = idx(question), .before = 1) %>% drop_cols(), "sources.csv")
save(ss %>% mutate(q_index = idx(question), .before = 1) %>% drop_cols(), "statements.csv")
invisible(file.copy(file.path(OUTPUT, "jama_hon_validation.csv"), file.path(OUT, "jama_hon_validation.csv"), overwrite = TRUE))

# ---- rubric items without criterion text. referral = the criterion is about seeking or referring to care
#      (HealthBench's emergency-behavior consensus criterion, or the wording matches REF); used for the
#      "without referral criteria" scores.
REF <- regex(paste(
  "seek(s|ing)? (out )?(immediate |prompt |urgent |emergency |emergent |professional |medical |in-person |further |specialist |timely |appropriate )*(medical )?(care|attention|help|evaluation|advice|treatment|assessment|consultation)",
  "(see|seeing|visit|visiting|consult|consulting|contact|contacting|call|calling|talk to|speak (to|with)|follow up with|check with|evaluated by|evaluation by|assessed by|examined by) (a |an |your |their |the |his |her |with )?(qualified |licensed |local |primary )?(doctor|physician|gp|healthcare|health care|health professional|medical|provider|clinician|specialist|pediatrician|paediatrician|dermatologist|dentist|pharmacist|professional|ob|gyn|obstetrician|cardiologist|neurologist|emergency|911|999|112|ambulance|poison|urgent care|nurse|midwife|therapist|psychiatrist)",
  "emergency (department|room|services|medical services|care|help|number|evaluation|attention)",
  "\\b(911|999|112|ambulance|a&e|ed|er)\\b", "urgent care", "medical attention", "in-person (care|evaluation|assessment|visit|examination)",
  "referr(al|ed|ing)", "\\brefers? (the |to |them |him |her |patient|for |a )", "go(es|ing)? to (the |a )?(hospital|emergency|er|clinic|doctor)",
  "primary care", "follow[- ]?up (with|by)", "(hospital )?admission\\b|\\badmit(ted)? to", "\\bems\\b|paramedic|emergency personnel|help arrives",
  "(drive|driving|transport[a-z]*) (themselves |yourself |the patient |him |her )?to (the |a )?(hospital|er|ed|emergency)",
  "do not wait|without delay", sep = "|"), ignore_case = TRUE)
save(items %>% mutate(referral = emergency_behavior | str_detect(criterion, REF)) %>% select(-criterion), "rubric_items.csv")

# ---- GPT-4.1 verdicts (one per overview and criterion) and the graded overviews' length
grades <- jl(file.path(HB, "results", "grades_gpt41_as_sent.jsonl"))
save(grades %>% transmute(prompt_id, item_idx, criteria_met) %>%
       left_join(dq %>% select(prompt_id, q_index), by = "prompt_id") %>% relocate(q_index), "grades_gpt41.csv")
aio <- jl(file.path(HB, "data", "aio_responses.jsonl"))
save(aio %>% transmute(q_index = as.integer(q_index), prompt_id, aio_chars = as.integer(aio_chars), aio_words = words(aio_text)),
     "graded_overviews.csv")

# ---- grader validation: GPT-4.1's verdict and the physicians' grades on the 3,000 sampled meta-evaluation rows
#      (row = line of OpenAI's HealthBench meta-evaluation file), plus that file's totals
meta <- jl(file.path(HB, "inputs", "2025-05-07-06-14-12_oss_meta_eval.jsonl"))
gm <- jl(file.path(HB, "results", "grades_meta_gpt41.jsonl")) %>% transmute(meta_row = as.integer(sub("meta", "", custom_id)) + 1,
                                                                           gpt41_met = criteria_met)
save(gm %>% mutate(physician_labels = map_chr(meta$binary_labels[meta_row], ~ paste(as.integer(.x), collapse = ";")),
                   physician_ids = map_chr(meta$anonymized_physician_ids[meta_row], ~ paste(.x, collapse = ";"))),
     "meta_eval_gpt41.csv")
save(tibble(meta_rows = nrow(meta), physician_grades = sum(lengths(meta$binary_labels)),
            consensus_criteria = n_distinct(meta$category)), "meta_eval_totals.csv")
