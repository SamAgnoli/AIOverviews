## Supplementary Material: every number, table, and figure in the supplement that the main paper does not report.
## Runs the code of AIO_Overviews_Results.Rmd first (full sample) to build the shared objects, then prints each
## supplement result under a heading naming its table or section. Figure S1 is saved to figures/.
## Run from analysis/:  Rscript AIO_Overviews_Supplement.R
suppressPackageStartupMessages({ library(knitr) })
main <- purl("AIO_Overviews_Results.Rmd", output = tempfile(fileext = ".R"), quiet = TRUE, documentation = 0)
invisible(capture.output(suppressMessages(suppressWarnings(source(main, encoding = "UTF-8")))))
if (SHORT_ONLY) stop("Comment out the robustness toggle in AIO_Overviews_Results.Rmd before running the supplement")
options(width = 140, pillar.sigfig = 4)
head_ <- function(s) cat("\n\n==== ", s, " ", strrep("=", max(0, 100 - nchar(s))), "\n", sep = "")
show  <- function(d) print(as.data.frame(d), row.names = FALSE)

# ------------------------------------------------------------------ Figure S1
head_("Figure S1: question length")
BIN <- 10; X_MAX <- 300
w <- QS$q_words
c(n = length(w), M = r1(mean(w)), Mdn = median(w), max = max(w), longer_than_shown = sum(w >= X_MAX))
h <- tibble(w = w[w < X_MAX])
y_top <- 200 * ceiling(max(table(cut(h$w, seq(0, X_MAX, BIN), right = FALSE))) / 200)
lines_ <- tibble(x = c(median(w), mean(w)), lab = c(sprintf("*Mdn* = %.0f words", median(w)), sprintf("*M* = %.1f words", mean(w)))) %>%
  mutate(lab = factor(lab, levels = lab))
fs1 <- ggplot(h, aes(w)) +
  geom_histogram(binwidth = BIN, boundary = 0, closed = "left", fill = PAL[["AIO"]], colour = "white", linewidth = .25) +
  geom_vline(data = lines_, aes(xintercept = x, linetype = lab), linewidth = .5, key_glyph = "path") +
  scale_linetype_manual(values = c("dashed", "dotted"), name = NULL) +
  scale_x_continuous(breaks = seq(0, X_MAX, 50), limits = c(0, X_MAX), expand = expansion(mult = c(0, .01))) +
  scale_y_continuous(labels = f_int, breaks = function(l) seq(0, l[2], 200), limits = c(0, y_top), expand = expansion(mult = c(0, .02))) +
  labs(x = "Question length (words)", y = "Number of questions") +
  theme_classic(base_size = 11, base_family = FONT) +
  theme(axis.line = element_line(colour = "black", linewidth = .4), axis.ticks = element_line(colour = "black", linewidth = .4),
        axis.text = element_text(colour = "black"), axis.title = element_text(colour = "black"),
        legend.position = "inside", legend.position.inside = c(.97, .95), legend.justification = c(1, 1),
        legend.text = ggtext::element_markdown(colour = "black"), legend.key.width = unit(26, "pt"), plot.margin = margin(10, 16, 8, 8))
invisible(save_fig(fs1, "FigureS1_question_length", 6.5, 3.6))

# ------------------------------------------------------------------ Supplementary Methods: retrieval and readability
head_("Search capture and organic results")
QA <- Q %>% filter(aio_present)
c(searches_failed = sum(failed), questions_with_aio = nrow(QA), fewer_than_five_organic = sum(QA$n_organic < 5),
  no_organic = sum(QA$n_organic == 0))

# Forum sites as in is_forum_url() (scrape notebook, Step 8), except that patient.info counts only for its forums:
# its other pages are health articles.
FORUM <- c("reddit.com", "quora.com", "patient.info/forums", "healthunlocked.com", "medhelp.org", "inspire.com", "drugs.com/comments",
           "webmd.com/answers", "forums.webmd.com", "community.patient.co.uk", "connect.mayoclinic.org")
head_("Forum threads read as ordinary pages, paired sample")
R %>% filter(q_index %in% pq, map_lgl(url, ~ any(str_detect(.x, fixed(FORUM))))) %>%
  count(source_type) %>% bind_rows(tibble(source_type = "Total", n = sum(.$n))) %>% show()

head_("Table S1: retrieval method of the readable pages")
G1 <- S %>% filter(q_index %in% good)
METHOD <- c(requests = "Direct HTTP request", playwright = "Headless browser (Playwright)", jina = "Jina Reader proxy",
            europepmc = "Europe PMC full text", trafilatura = "trafilatura fetcher", wayback = "Wayback Machine archive",
            europepmc_abstract = "Europe PMC abstract", amp = "AMP version")
rdbl <- G1 %>% filter(text_quality == "ok", text_len > 0)
rdbl %>% count(method = METHOD[fetch_method], source_type) %>% pivot_wider(names_from = source_type, values_from = n, values_fill = 0) %>%
  mutate(pct = r1(100 * (AIO + Organic) / sum(AIO + Organic))) %>% arrange(desc(pct)) %>%
  bind_rows(summarise(., method = "Total", AIO = sum(AIO), Organic = sum(Organic), pct = 100)) %>% show()
c(pdf_pct_of_readable = r1(100 * mean(grepl("\\.pdf($|\\?)", rdbl$url, ignore.case = TRUE)), 1))

head_("Table S2: readability labels (gate-1 questions)")
LABEL_S2 <- c(snippet_only = "Snippet only", empty = "Empty", garbage_binary = "Binary", garbage_interstitial = "Interstitial",
              too_short = "Too short", "forum record" = "Forum record", ok = "Readable")
G1 %>% mutate(label = LABEL_S2[ifelse(text_quality == "", "forum record", text_quality)]) %>% count(label, source_type) %>%
  pivot_wider(names_from = source_type, values_from = n, values_fill = 0) %>% arrange(match(label, LABEL_S2)) %>%
  bind_rows(summarise(., label = "Total", AIO = sum(AIO), Organic = sum(Organic))) %>% show()
G1 %>% group_by(source_type) %>% summarise(pages = n(), unreadable_pct = r1(100 * mean(text_quality != "ok"))) %>% show()

head_("Analytic sample and Table S3: questions and pages at each filter stage")
cap <- SE$q_index[!failed]
c(submitted = nrow(QS), captured = length(cap), aio_returned = nrow(QA), no_aio_captured = sum(!cap %in% QA$q_index),
  no_overview = sum(err[!failed] == "no_aio"), empty_placeholder = sum(err[!failed] == "aio_token_empty"),
  removed_by_gate1 = nrow(QA) - length(good),
  no_cited_page_and_no_organic = sum((QA$n_aio_sources == 0 | is.na(QA$n_aio_sources)) & QA$n_organic == 0))
tibble(stage = c("AIO returned", "Gate 1: overview text of at least 200 characters", "Gate 2: readable pages only",
                 "At least one readable AIO-cited page", "At least one readable organic result", "Paired analytic sample"),
       questions = c(nrow(QA), length(good), NA, n_distinct(R$q_index[R$source_type == "AIO"]),
                     n_distinct(R$q_index[R$source_type == "Organic"]), length(pq)),
       aio_pages = c(sum(S$source_type == "AIO"), sum(G1$source_type == "AIO"), sum(R$source_type == "AIO"), NA, NA,
                     sum(R$source_type == "AIO" & R$q_index %in% pq)),
       organic = c(sum(S$source_type == "Organic"), sum(G1$source_type == "Organic"), sum(R$source_type == "Organic"), NA, NA,
                   sum(R$source_type == "Organic" & R$q_index %in% pq))) %>% show()

head_("JAMA Authorship: trusted health domains (MedlinePlus list)")
c(trusted_domains = nrow(read_csv("../pipeline/scrape/trusted_health_domains_medlineplus.csv", show_col_types = FALSE)))

head_("Table S7: automated JAMA and HONcode coding against human coders")
bind_rows(hum, hum %>% mutate(criterion = "All criteria")) %>% group_by(criterion) %>%
  summarise(judgments = n(), pages = n_distinct(url), agreement = r1(100 * mean(human == auto)), kappa = round(kappa(human, auto), 2)) %>%
  arrange(match(criterion, c(CRIT, "All criteria"))) %>% show()

head_("HallOumi coding: agreement with each coder")
rbind(all = kci(hv$human, hv$halloumi), t(sapply(split(hv, hv$Coder), function(x) kci(x$human, x$halloumi))))[, c("n", "agree", "kappa", "lo", "hi", "human_supported", "halloumi_supported", "h1_m0", "h0_m1")]

head_("HallOumi coding: misclassification correction (computed in the main R Markdown)")
c(sensitivity = r1(100 * a[["se"]]), sens = sprintf("%d of %d", sum(hv$halloumi[hv$human == 1]), sum(hv$human == 1)),
  specificity = r1(100 * a[["sp"]]), spec = sprintf("%d of %d", sum(hv$halloumi[hv$human == 0] == 0), sum(hv$human == 0)),
  validation_overviews = nv, scorable_statements = sum(scq$n),
  observed = sprintf("%.1f [%.1f, %.1f]", 100 * obs[["est"]], 100 * obs[["lo"]], 100 * obs[["hi"]]),
  corrected = sprintf("%.1f [%.1f, %.1f]", 100 * clip(rogan(obs[["est"]], a)), 100 * quantile(cb, .025, na.rm = TRUE),
                      100 * quantile(cb, .975, na.rm = TRUE)))

# ------------------------------------------------------------------ Physician rubric grading
head_("Rubrics")
c(overviews = n_distinct(g$prompt_id), criteria = nrow(g), specific = sum(g$level == "example"), consensus = sum(g$level == "cluster"),
  to_show = sum(g$points > 0), to_avoid = sum(g$points < 0), median_per_overview = median(n_per$n), min = min(n_per$n), max = max(n_per$n))
count(g, axis, sort = TRUE) %>% show()

head_("Grader validation")
tot <- rd("meta_eval_totals.csv")
c(meta_eval_responses = tot$meta_rows, consensus_criteria = tot$consensus_criteria, physician_grades = tot$physician_grades,
  sampled_rows = nrow(me), sampled_grades = length(unlist(lab)), physicians_mf1 = round(weighted.mean(pm$mf1, pm$n), 3),
  gpt41_mf1 = round(mf1(pairs_model$self, pairs_model$other), 3))

head_("Grading procedure, scoring and analysis")
c(multi_turn_conversations = sum(A$multi), verdicts = nrow(g), no_clinical_criteria = sum(is.na(A$clinical)),
  cited_with_all_three_measures = nrow(cd), excluded_without_referral = sum(!is.na(A2$clinical) & is.na(A2$noref)))

# ------------------------------------------------------------------ Known limitations
head_("Known limitations: text-only retrieval routes (paired sample), forum pages, unreadable pages")
R %>% filter(q_index %in% pq) %>% group_by(source_type) %>%
  summarise(text_only_pct = r1(100 * mean(fetch_method %in% c("jina", "europepmc", "europepmc_abstract") |
                                            grepl("\\.pdf($|\\?)", url, ignore.case = TRUE)))) %>% show()

# ------------------------------------------------------------------ Supplementary Results
head_("Table S9: most frequent domains")
dom <- R %>% count(domain, source_type) %>% pivot_wider(names_from = source_type, values_from = n, values_fill = 0) %>%
  mutate(total = AIO + Organic) %>% arrange(desc(total)) %>% slice_head(n = 12)
tq <- R %>% filter(q_index %in% pq) %>% count(q_index, source_type, name = "n_all")
t9 <- map_dfr(dom$domain, function(dm) {
  x <- R %>% filter(q_index %in% pq, domain == dm) %>% count(q_index, source_type) %>%
    right_join(tq, by = c("q_index", "source_type")) %>% mutate(share = coalesce(n, 0L) / n_all) %>%
    select(q_index, source_type, share) %>% pivot_wider(names_from = source_type, values_from = share)
  as_tibble_row(paired(x$AIO, x$Organic)) %>% mutate(domain = dm) }) %>% mutate(p_BH = p.adjust(p, "BH"))
dom %>% mutate(pct_aio = r1(100 * AIO / sum(R$source_type == "AIO")), pct_org = r1(100 * Organic / sum(R$source_type == "Organic"))) %>%
  left_join(t9 %>% select(domain, n, t, p_BH, dz), by = "domain") %>%
  transmute(domain, AIO, pct_aio, Organic, pct_org, n, t = round(t, 2), p_BH = signif(p_BH, 3), dz = round(dz, 2)) %>% show()

head_("Table S10: JAMA and HONcode scores of the two arms")
qt %>% transmute(measure, M_aio = round(M_aio, 2), SD_aio = round(SD_aio, 2), M_org = round(M_org, 2), SD_org = round(SD_org, 2),
                 df = df, t = round(t, 2), p_BH = signif(p_BH, 3), dz = round(dz, 2)) %>% show()

head_("Quality within risk level and rubric complexity (JAMA and HONcode totals; unadjusted)")
ql %>% group_by(measure, variable) %>%
  summarise(dz_from = round(min(dz), 2), dz_to = round(max(dz), 2), max_p = signif(max(p), 3), all_favor_organic = all(dz < 0), .groups = "drop") %>% show()

head_("Table S11: clinical scores and inaccurate or harmful content by HealthBench theme")
THEME <- c(health_data_tasks = "Health data tasks", context_seeking = "Context seeking", global_health = "Global health",
           hedging = "Responding under uncertainty", communication = "Expertise-tailored communication",
           emergency_referrals = "Emergency referrals", complex_responses = "Response depth")
A %>% group_by(risk = factor(risk, LV), theme = THEME[theme]) %>%
  summarise(n = n(), clinical = r1(100 * clip(mean(clinical, na.rm = TRUE))), harm_pct = r1(100 * mean(harm, na.rm = TRUE)),
            harm_n = sum(!is.na(harm)), .groups = "drop") %>% arrange(risk, match(theme, THEME)) %>% show()

# ------------------------------------------------------------------ Robustness to question length (Table S12)
head_("Robustness to question length: the subset")
SUB <- QS %>% filter(n_user_turns == 1, !has_assistant_turns, q_words <= 44)
single <- QS %>% filter(n_user_turns == 1, !has_assistant_turns)
c(questions = nrow(QS), single_turn = nrow(single), single_turn_M = r1(mean(single$q_words)), single_turn_Mdn = median(single$q_words),
  subset = nrow(SUB), full_sample_mean_words = r1(mean(QS$q_words)))
addmargins(table(Risk = factor(SUB$risk_level, LV), Complexity = factor(SUB$rubric_complexity, LV)))

# Every headline result for one set of questions, with the same formulas as the main analyses.
# 95% CIs: Wilson for proportions of AIOs; question-level bootstrap for pooled rates over pages or statements (overlap,
# grounding); bootstrap for mean scores; paired-t interval / SD of the differences for dz; Fisher z (Bonett-Wright SE)
# for Spearman's rho; Welch interval for the uncited-minus-cited difference.
Q0 <- Q; S0 <- S; ST0 <- ST; A0 <- A; A20 <- A2; D0 <- D; CAPTURED <- cap
ORD <- QS %>% select(q_index, boot_order)
bo  <- function(d) d %>% left_join(ORD, by = "q_index") %>% arrange(boot_order, .by_group = FALSE)
fisher <- function(r, n) { se <- sqrt((1 + r^2 / 2) / (n - 3)); tanh(atanh(r) + c(-1, 1) * 1.96 * se) }
results <- function(keys) {
  rows <- list()
  add <- function(section, measure, est, lo = NA, hi = NA, n = NA, p = NA, test = NA, scale = 100)
    rows[[length(rows) + 1]] <<- tibble(section, measure, n, est = scale * est, lo = scale * lo, hi = scale * hi, p, test)
  k  <- function(d) d[d$q_index %in% keys, ]
  Q <- k(Q0); S <- k(S0); ST <- k(ST0)
  good <- Q$q_index[Q$aio_present & !is.na(Q$aio_text_len) & Q$aio_text_len >= 200]
  R  <- S %>% filter(q_index %in% good, text_quality == "ok", !is.na(text_len), text_len > 0)
  pq <- R %>% distinct(q_index, source_type) %>% count(q_index) %>% filter(n == 2) %>% pull(q_index)
  lv <- QS %>% select(q_index, risk_level, rubric_complexity)
  LBL <- c(rubric_complexity = "complexity", risk_level = "risk", cx = "complexity", risk = "risk")
  # sample
  add("Sample", "Questions", length(keys), scale = 1)
  w <- wilson(sum(Q$aio_present), sum(keys %in% CAPTURED))
  add("Sample", "AI Overview returned (% of captured searches)", w[["est"]], w[["lo"]], w[["hi"]], w[["n"]])
  add("Sample", "AI Overviews with usable text", length(good), scale = 1)
  add("Sample", "Paired questions (readable pages in both arms)", length(pq), scale = 1)
  # uncited overviews
  QA <- Q %>% filter(aio_present) %>% mutate(uncited = n_aio_sources == 0 | is.na(n_aio_sources)) %>% left_join(QS %>% select(q_index, q_words), by = "q_index")
  w <- wilson(sum(QA$uncited), nrow(QA)); add("Uncited overviews", "Uncited (%)", w[["est"]], w[["lo"]], w[["hi"]], nrow(QA))
  u <- QA$q_words[QA$uncited]; c_ <- QA$q_words[!QA$uncited]; wt <- t.test(u, c_)
  sp <- sqrt(((length(u) - 1) * var(u) + (length(c_) - 1) * var(c_)) / (length(u) + length(c_) - 2))
  add("Uncited overviews", "Question length, uncited vs. cited (words)", mean(u) - mean(c_), wt$conf.int[1], wt$conf.int[2],
      nrow(QA), wt$p.value, sprintf("%.1f vs. %.1f, d = %.2f", mean(u), mean(c_), (mean(u) - mean(c_)) / sp), 1)
  for (v in c("rubric_complexity", "risk_level")) {
    by <- QA %>% filter(.data[[v]] %in% LV) %>% group_by(l = factor(.data[[v]], LV)) %>% summarise(n = n(), k = sum(uncited), .groups = "drop")
    ca <- prop.trend.test(by$k, by$n)
    for (i in seq_len(nrow(by))) { w <- wilson(by$k[i], by$n[i]); last <- i == nrow(by)
      add("Uncited overviews", paste0("Uncited (%), ", LBL[[v]], " ", by$l[i]), w[["est"]], w[["lo"]], w[["hi"]], by$n[i],
          if (last) ca$p.value else NA, if (last) sprintf("\u03c7\u00b2(1) = %.2f", ca$statistic) else NA) }
  }
  # overlap: share of organic top-five results also cited by the overview
  ov <- R %>% distinct(q_index, source_type, url) %>% group_by(q_index, url) %>%
    summarise(both = n_distinct(source_type) == 2, org = any(source_type == "Organic"), .groups = "drop") %>%
    filter(org) %>% group_by(q_index) %>% summarise(both = sum(both), org = n()) %>% bo()
  b <- boot_ratio(ov$both, ov$org)
  add("Overlap", "Organic top-five results also cited by the overview (%)", b[["est"]], b[["lo"]], b[["hi"]], sum(ov$org))
  # source quality: paired t tests on per-question means, BH across the six criteria
  qm <- R %>% filter(q_index %in% pq) %>% group_by(q_index, source_type) %>%
    summarise(across(c(all_of(CRIT), JAMA_Total, HON_Total), ~ mean(.x, na.rm = TRUE)), .groups = "drop")
  arm <- function(v, d = qm) d %>% select(q_index, source_type, all_of(v)) %>% pivot_wider(names_from = source_type, values_from = all_of(v))
  pt <- map_dfr(CRIT, function(m) { x <- arm(m); d <- x$AIO - x$Organic; tt <- t.test(x$AIO, x$Organic, paired = TRUE)
    tibble(m, n = length(d), dz = mean(d) / sd(d), lo = tt$conf.int[1] / sd(d), hi = tt$conf.int[2] / sd(d), p = tt$p.value) }) %>%
    mutate(p_BH = p.adjust(p, "BH"))
  for (i in seq_len(nrow(pt))) add("Source quality", sub("_", " ", pt$m[i]), pt$dz[i], pt$lo[i], pt$hi[i], pt$n[i], pt$p_BH[i], scale = 1)
  lvq <- map_dfr(c("risk_level", "rubric_complexity"), function(v) map_dfr(c("JAMA_Total", "HON_Total"), function(m)
    arm(m) %>% left_join(lv, by = "q_index") %>% group_by(level = .data[[v]]) %>%
      summarise(n = n(), dz = mean(AIO - Organic) / sd(AIO - Organic), p = t.test(AIO, Organic, paired = TRUE)$p.value, .groups = "drop")))
  add("Source quality", "Within-level comparisons favoring organic (of 12)", sum(lvq$dz < 0), n = min(lvq$n),
      test = sprintf("%d of 12 (%d with p < .05)", sum(lvq$dz < 0), sum(lvq$dz < 0 & lvq$p < .05)), scale = 1)
  # statement grounding
  st <- ST %>% filter(q_index %in% good)
  add("Grounding", "Uncited factual statements (% of all statements)", mean(st$status == "uncited" & st$uncited_type %in% "uncited_factual"), n = nrow(st))
  sc <- st %>% filter(status %in% c("supported", "unsupported")) %>% group_by(q_index) %>% summarise(sup = sum(status == "supported"), n = n()) %>% bo()
  b <- boot_ratio(sc$sup, sc$n)
  add("Grounding", "Scorable statements supported (%)", b[["est"]], b[["lo"]], b[["hi"]], sum(sc$n))
  for (v in c("rubric_complexity", "risk_level")) {
    x <- st %>% filter(status %in% c("supported", "unsupported")) %>% left_join(lv, by = "q_index") %>%
      group_by(q_index, l = factor(.data[[v]], LV)) %>% summarise(sup = sum(status == "supported"), n = n(), .groups = "drop") %>% bo()
    tot <- x %>% group_by(l) %>% summarise(sup = sum(sup), n = sum(n))
    ch <- chisq.test(cbind(tot$sup, tot$n - tot$sup))
    for (i in seq_len(nrow(tot))) { b <- boot_ratio(x$sup[x$l == tot$l[i]], x$n[x$l == tot$l[i]]); last <- i == nrow(tot)
      add("Grounding", paste0("Supported (%), ", LBL[[v]], " ", tot$l[i]), b[["est"]], b[["lo"]], b[["hi"]], tot$n[i],
          if (last) ch$p.value else NA, if (last) sprintf("\u03c7\u00b2(%d) = %.2f", ch$parameter, ch$statistic) else NA) }
  }
  # physician rubrics
  A <- k(A0)
  SCN <- c(overall = "All criteria", clinical = "Clinical (accuracy and completeness)", accuracy = "Accuracy",
           completeness = "Completeness", communication_quality = "Communication quality")
  for (m in names(SCN)) { b <- boot(A[[m]]); add("Rubric scores", SCN[[m]], clip(b[["est"]]), clip(b[["lo"]]), clip(b[["hi"]]), b[["n"]]) }
  for (v in c("cx", "risk")) for (l in LV) { b <- boot(A$clinical[A[[v]] == l])
    add("Rubric scores", paste0("Clinical, ", LBL[[v]], " ", l), clip(b[["est"]]), clip(b[["lo"]]), clip(b[["hi"]]), b[["n"]]) }
  A2 <- k(A20) %>% filter(!is.na(clinical))
  for (l in LV) { b <- boot(A2$noref[A2$risk == l])
    add("Rubric scores", paste0("Clinical without referral criteria, risk ", l), clip(b[["est"]]), clip(b[["lo"]]), clip(b[["hi"]]), b[["n"]]) }
  RTN <- c(harm = "Contained inaccurate or potentially harmful content", acc_check = "No factually incorrect information",
           emergency = "Referred appropriately (emergency referrals)")
  for (m in names(RTN)) { w <- wilson(sum(A[[m]], na.rm = TRUE), sum(!is.na(A[[m]]))); add("Rubric rates", RTN[[m]], w[["est"]], w[["lo"]], w[["hi"]], w[["n"]]) }
  # accuracy against source quality and grounding
  D  <- k(D0)
  cd <- D %>% filter(!uncited, !is.na(support_rate), !is.na(jama), !is.na(hon), !is.na(clinical))
  CL <- c(jama = "JAMA total", hon = "HONcode total", support_rate = "Grounding (support rate)")
  for (v in names(CL)) { t <- cor.test(cd[[v]], cd$clinical, method = "spearman", exact = FALSE); r <- unname(t$estimate); ci <- fisher(r, nrow(cd))
    add("Correlates of clinical score", CL[[v]], r, ci[1], ci[2], nrow(cd), t$p.value, scale = 1) }
  x <- D$clinical_pts[D$uncited & !is.na(D$clinical)]; y <- D$clinical_pts[!D$uncited & !is.na(D$clinical)]; tt <- t.test(x, y)
  add("Correlates of clinical score", "Clinical score, uncited minus cited (points)", mean(x) - mean(y), tt$conf.int[1], tt$conf.int[2],
      length(x) + length(y), tt$p.value, sprintf("%.1f (n = %d) vs. %.1f (n = %d); Welch t(%.1f) = %.2f", mean(x), length(x),
                                                  mean(y), length(y), tt$parameter, tt$statistic), 1)
  bind_rows(rows)
}
full  <- results(QS$q_index)
short <- results(SUB$q_index)

# The rows of Table S12 as printed (APA style: minus signs, no leading zero for rho, stars for p)
mn   <- function(x) gsub("-", "\u2212", x, fixed = TRUE)
z0   <- function(x) ifelse(abs(x) < .005, 0, x)
ci   <- function(r, d = 1) mn(sprintf(paste0("%.", d, "f [%.", d, "f, %.", d, "f]"), z0(r$est), z0(r$lo), z0(r$hi)))
nlz  <- function(s) gsub("(^|[ \\[\u2212])0\\.", "\\1.", s)
star <- function(p) ifelse(is.na(p), "", stars(p))
cell <- function(r, m) {
  x <- r[r$measure == m, ]; stopifnot(nrow(x) == 1)
  if (x$section == "Source quality" && m != "Within-level comparisons favoring organic (of 12)") return(paste0(ci(x, 2), star(x$p)))
  if (x$section == "Correlates of clinical score" && !grepl("uncited", m)) return(paste0(nlz(ci(x, 2)), star(x$p)))
  if (m %in% c("Question length, uncited vs. cited (words)", "Within-level comparisons favoring organic (of 12)")) return(mn(paste0(x$test, star(x$p))))
  if (is.na(x$lo)) return(if (x$est == round(x$est) && x$est > 100) f_int(x$est) else sprintf("%.1f", x$est))
  ci(x) }
trip <- function(r, prefix) {
  x <- r[startsWith(r$measure, prefix), ]; stopifnot(nrow(x) == 3)
  s <- paste(sprintf("%.1f", x$est), collapse = " / "); t <- x[!is.na(x$test), ]
  if (nrow(t)) paste0(s, "; ", t$test, star(t$p)) else s }
SPEC <- list(
  list("Sample"), list("Questions", "Questions"), list("AI Overview returned (% of captured searches)", "AI Overview returned (% of captured searches)"),
  list("AI Overviews with usable text (graded)", "AI Overviews with usable text"),
  list("Paired questions (readable pages in both arms)", "Paired questions (readable pages in both arms)"),
  list("AI Overviews citing no sources (%)"), list("All overviews", "Uncited (%)"),
  list("Low / medium / high complexity", "Uncited (%), complexity ", "trip"), list("Low / medium / high risk", "Uncited (%), risk ", "trip"),
  list("Question length, uncited vs. cited (words)", "Question length, uncited vs. cited (words)"),
  list("Source overlap (%)"),
  list("Organic top-five results also cited by the overview", "Organic top-five results also cited by the overview (%)"),
  list("Source quality, dz (AI Overview \u2212 organic)"),
  list("JAMA Authorship", "JAMA Authorship"), list("JAMA Attribution", "JAMA Attribution"), list("JAMA Disclosure", "JAMA Disclosure"),
  list("JAMA Currency", "JAMA Currency"), list("HON Authority", "HON Authority"), list("HON Complementarity", "HON Complementarity"),
  list("Risk and complexity levels favoring organic (JAMA and HON totals)", "Within-level comparisons favoring organic (of 12)"),
  list("Statement grounding (%)"), list("Scorable statements supported", "Scorable statements supported (%)"),
  list("Uncited factual statements (% of all statements)", "Uncited factual statements (% of all statements)"),
  list("Supported, low / medium / high complexity", "Supported (%), complexity ", "trip"),
  list("Supported, low / medium / high risk", "Supported (%), risk ", "trip"),
  list("Physician rubric scores (% of available points)"),
  list("All criteria", "All criteria"), list("Clinical (accuracy and completeness)", "Clinical (accuracy and completeness)"),
  list("Accuracy", "Accuracy"), list("Completeness", "Completeness"), list("Communication quality", "Communication quality"),
  list("Clinical, low / medium / high complexity", "Clinical, complexity ", "trip"), list("Clinical, low / medium / high risk", "Clinical, risk ", "trip"),
  list("Clinical without referral criteria, low / medium / high risk", "Clinical without referral criteria, risk ", "trip"),
  list("Physician rubric rates (% of AI Overviews)"),
  list("Inaccurate or potentially harmful content", "Contained inaccurate or potentially harmful content"),
  list("No factually incorrect information", "No factually incorrect information"),
  list("Referred appropriately (emergency referrals)", "Referred appropriately (emergency referrals)"),
  list("Clinical score and citations"),
  list("Spearman \u03c1 with JAMA total", "JAMA total"), list("Spearman \u03c1 with HONcode total", "HONcode total"),
  list("Spearman \u03c1 with grounding (support rate)", "Grounding (support rate)"),
  list("Uncited minus cited AI Overviews (points)", "Clinical score, uncited minus cited (points)"))
head_("Table S12: main results in the full sample and in single-turn questions of 44 words or fewer")
map_dfr(SPEC, function(s) {
  if (length(s) == 1) return(tibble(Measure = s[[1]], `Full sample` = "", `Single-turn, <= 44 words` = ""))
  f <- if (length(s) == 3) function(r) trip(r, s[[2]]) else function(r) cell(r, s[[2]])
  tibble(Measure = s[[1]], `Full sample` = f(full), `Single-turn, <= 44 words` = f(short)) }) %>% show()
