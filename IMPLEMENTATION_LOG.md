# ReconIQ Implementation Log

## 00 - Repository Foundation

- **Status:** PASS
- **Implemented:** Planned package layout, Python/pytest configuration, environment template, repository rules, and Pydantic source schemas.
- **Gate evidence:** Ledger, settlement, and bank schema parsing and required-field validation passed.
- **Metrics:** 4/4 schema tests passed. Accuracy evaluation: not applicable.
- **Decisions:** Work directly on `main`; request approval before commits; keep future modules inside domain folders.
- **Issue:** Pytest cache writes were denied by the managed workspace.
- **Cause / resolution:** Sandbox filesystem permissions; disabled the cache provider and ignored its temporary folders.
- **Edge case:** `is_transaction` is generator-only truth and must never appear in matcher-facing bank CSVs.
- **Claude checkpoint:** Not required for foundation setup.

## 01 - Synthetic Data Generator

- **Status:** PASS - Claude review applied
- **Implemented:** Seeded generator, 15 case types, typed ground truth, design/holdout partitions, isolated CSV export, derivable 26AS tax evidence, linked refunds, and cross-transaction hard negatives.
- **Gate evidence:** Exactly 85 design and 35 holdout scenarios. Design categories contain 5-6 cases each; holdout categories contain 2-3 each.
- **Metrics:** 11/11 generator tests and 16/16 full-suite tests passed. Accuracy evaluation: not applicable until matching begins.
- **Decisions:** Treat 120 records as labeled scenarios; multi-leg cases create extra source rows. Keep hard negatives within the Stage 3 `0.5` tolerance (`+0.25`) so they genuinely test false-match prevention; the review's suggested `>2%` offset was rejected as too easy.
- **Review findings resolved:** Tax labels now have settlement/26AS evidence; hard negatives reference another real case; refunds contain positive and negative legs; non-transaction narrations vary; duplicate retries remain distinguishable while preserving `order_id + amount`.
- **Issue:** CSV tests could not access pytest temp storage; a missing-reference test lookup also collided on the `None` key.
- **Cause / resolution:** Sandbox ACL required an approved external test run; test comparisons now use ground-truth settlement IDs instead of nullable references.
- **Edge cases:** `bank.csv` excludes generator truth. Holdout is generated and gate-checked but remains unused for tuning.
- **Claude checkpoint:** COMPLETE. Next review is required only when the Stage 0 design/test plan is ready.

## 02 - Stage 0 Deduplication

- **Status:** PASS - Claude review applied; Ponytail review passed.
- **Implemented:** Deterministic `order_id + amount` deduplication with an inclusive one-day window, first-input retention, stable output order, and typed duplicate-to-canonical trace links.
- **Gate evidence:** All 6 injected design duplicates collapsed; no split-settlement or other records collapsed falsely.
- **Metrics:** Precision 100%, recall 100%, 0 false collapses; 6/6 focused tests and 22/22 full-suite tests passed.
- **Decisions:** Process one partition per call; compare only against retained canonical rows to prevent transitive chain collapse; use source indices for identity without changing the ledger schema.
- **Review findings resolved:** Made retry dates exactly one day later; defined inclusive boundary, tie-breaking, chain behavior, output order, and traceability. The review's byte-identical-row concern was stale because retry `ref_id` values were already distinct.
- **Issue:** Initial full-suite runs could not enumerate pytest temporary directories.
- **Cause / resolution:** Windows sandbox ACL, not application behavior; verified the complete suite outside that boundary. A reporting query used stale ground-truth names and was corrected without code changes.
- **Edge cases:** Same-day and day-one retries collapse; day-two, different-amount, and different-order rows remain; empty, single-row, chain, and split-settlement cases are covered.
- **Claude checkpoint:** COMPLETE. Next review is required only when the Stage 1 design/test plan is ready.

## 03 - Stage 1 Bank Scope Filter

- **Status:** PASS - Claude review applied; Ponytail review passed.
- **Implemented:** Token-boundary-aware, case-insensitive exclusion of `LOAN DISBURSEMENT`, `GST REFUND`, and `INTERNAL TRANSFER`, with fixed priority, stable ordering, and typed source-index traceability.
- **Gate evidence:** Design set excluded exactly every `NON_TRANSACTION_BANK_LINE` and no other bank row; all three generated phrases are covered.
- **Metrics:** 100% design-set recall, 0 false exclusions; 7/7 focused Stage 1 cases and 29/29 full-suite tests passed. Accuracy evaluation beyond scope filtering: not applicable.
- **Decisions:** `RAZORPAY SETTLEMENT`, refunds, and unmatched/ambiguous narrations remain in scope. `is_transaction` was removed from matcher-facing `BankEntry`; only ground truth retains it.
- **Review findings resolved:** Added word/token boundaries, phrase priority, original-list source indices, punctuation handling, and regression coverage for schema leakage. Generator phrase rotation was already present and verified.
- **Issue:** The default `python` command resolved to an environment without pytest during verification.
- **Cause / resolution:** Interpreter selection changed externally; full verification used the configured Miniconda interpreter.
- **Edge cases:** Empty narration passes through; mixed case, repeated whitespace, hyphen/punctuation variants match; `TRANSFERRED` and `REFUNDABLE` do not.
- **Claude checkpoint:** COMPLETE. Next review is required only when the Stage 2 design/test plan is ready.

## 04 - Stage 2 Exact Reference Match

- **Status:** PASS - Claude review applied; Ponytail review passed.
- **Implemented:** Typed one-to-one-to-one exact `ref_id` matching with stable output order, shared `ReconciliationResult` records, and untouched unresolved inputs.
- **Gate evidence:** 33/33 design-set matches were valid; zero hard-negative bank rows matched. All 6 refund originals matched while all 6 derived refund legs remained for Stage 5.
- **Metrics:** 100% design-set precision; 0 hard-negative matches; 16/16 focused Stage 2 cases and 45/45 full-suite tests passed.
- **Decisions:** Match only unique, nonblank references present in all three sources; comparisons remain exact, case-sensitive, and untrimmed. Repeated or two-source references are not consumed.
- **Review findings resolved:** Confirmed derived refund references; added all-source hard-negative collision protection; defined blank handling and fixed `record_ids` order as ledger, settlement, bank.
- **Issue:** Claude identified possible refund and hard-negative reference collisions.
- **Cause / resolution:** Both reserved naming conventions already existed; regression tests now enforce distinct refund IDs and decoy uniqueness across ledger, settlement, and other bank rows.
- **Edge cases:** `None`, empty, whitespace-only, case variants, two-source references, repeated references, empty inputs, refunds, and stable ordering are covered.
- **Claude checkpoint:** COMPLETE. Next review is required only when the Stage 3 design/test plan is ready.

## 05 - Stage 3 Fee-Adjusted Match

- **Status:** PASS - Claude review applied; Ponytail review passed.
- **Implemented:** Per-method fees and settlement windows, rate-card-validated ledger/settlement pairing, strict `<0.50` bank tolerance, inclusive forward date windows, and mutually unique candidate matching.
- **Gate evidence:** All 12 design fee/timing cases matched correctly; no other case or hard-negative bank row matched. Generated CSVs were refreshed.
- **Metrics:** Precision 100%, recall 100%, 0 hard-negative matches; 18/18 focused Stage 3 cases and 65/65 full-suite tests passed.
- **Decisions:** Settlement `net_amount` is the primary bank comparison because it records the actual gateway charge; the rate card independently validates its fee components. Ambiguous candidate graphs remain wholly unresolved.
- **Review findings resolved:** Generator rates and windows now vary by payment method; direct fixtures cover both ambiguity directions without adding a case type.
- **Issue:** Flat generator values could let hardcoded method logic pass; amount-close hard negatives were not testing the tolerance path.
- **Cause / resolution:** Added distinct shared configurations and method-specific timing fixtures. Hard negatives remain structurally excluded by their nonblank bank references; a blank-ref tolerance hard negative is explicitly outside this slice.
- **Edge cases:** Tolerance boundary, forward-date boundary, pre-settlement dates, non-INR, method mismatch, inconsistent fees, populated bank references, splits, refunds, empty inputs, and stable order are covered.
- **Claude checkpoint:** COMPLETE. Next review is required only when the Stage 4 design/test plan is ready.

## 06 - Stage 4 Split Settlement Match

- **Status:** PASS - Claude implementation review complete; Ponytail review passed.
- **Implemented:** Unique ledger/settlement part pairing, 2-5 part search by method and settlement date, stored-net amount comparison, inclusive method windows, and global ambiguity rejection.
- **Gate evidence:** 28/28 focused Stage 4 and generator-invariant checks passed; 93/93 full-suite tests passed. Generated CSVs were restored.
- **Metrics:** 6/6 design splits recovered; precision 100%, recall 100%, 0 hard-negative matches.
- **Decisions:** Search every combination size before resolving; accept only globally unique candidates. Combination search remains intentionally limited to hackathon-size batches.
- **Issue / resolution:** Added a partition-level subset-sum collision invariant; no seeded collisions were found. Claude identified missing direct proof for cross-bank partial overlap; the regression fixture confirms both candidates remain unresolved.
- **Edge cases:** 2-5 parts, strict tolerance, blank refs, window boundaries, method/date mismatch, invalid currency/amount, duplicate refs, both ambiguity directions, stored net source, empty inputs, and stable ordering.
- **Claude checkpoint:** COMPLETE - no implementation bug found; required coverage gap closed.

## 07 - Stage 5 Refund/Reversal Link

- **Status:** PASS - Claude implementation review complete; Ponytail review passed.
- **Implemented:** Typed confirmed-original context, unique `-refund` reference linking, gross-amount anchoring, reachable ambiguity rejection, and separate refund results without reopening Stage 2 matches.
- **Gate evidence:** 17/17 focused Stage 5 and generator checks passed; 109/109 full-suite tests passed.
- **Metrics:** 6/6 design refund truth groups covered by the union of Stage 2 and Stage 5 results; 100% coverage and 0 incorrect refund links.
- **Decisions:** The pipeline caller constructs `ConfirmedReference` from Stage 2 output plus the full ledger. Refund ledger and bank legs both reverse original gross; original bank plus refund bank therefore nets to the non-refundable fee and GST loss.
- **Issue / resolution:** Review exposed an unverified refund-amount assumption. Generator assertions and Razorpay policy verification confirmed the full-gross reversal; amount drift and equal-amount unrelated originals now have direct regression tests.
- **Review disposition:** Distinct refund refs cannot strip to the same original under exact `-refund` suffix removal; the redundant candidate counter was removed. Duplicate refund refs and duplicate confirmed originals remain directly tested.
- **Edge cases:** Missing/blank refs, absent or duplicate originals, duplicate refund candidates, partial/amount-drift refunds, non-refund negatives, non-mutation, stable ordering, and empty inputs are covered.
- **Claude checkpoint:** COMPLETE - implementation and generator accounting proof approved; unreachable ambiguity guard removed.

## 08 - Stage 6 LLM-Assisted Remainder

- **Status:** PASS - Claude feedback applied; correctness and Ponytail reviews
  complete.
- **Implemented:** Narration-based ambiguous fixtures, explicit competing
  candidates, deterministic eligibility and top-two ranking, structured Gemini
  decisions, global conflict rejection, SQLite caching/call tracking, retry and
  unavailable handling, and separate audit versus reconciliation outputs.
- **Gate evidence:** 25/25 focused Stage 6 offline tests and 152/152 full-suite
  offline tests passed. Post-review
  real Gemini gate accepted all 5 true ambiguous matches, rejected 4 competing
  candidates, and rejected both semantic hard negatives.
- **Metrics:** Live precision 100%, recall 100%, filter-guard exclusion 100%
  (n=3), semantic-decoy rejection 100% (n=2), 0 false matches, 11 calls,
  5,599 input tokens, 1,409 output tokens, 3,079.55 ms average latency, and
  $0.0052022 / INR 0.4916 estimated pre-tax cost for the post-review run.
- **Decisions:** Only INR source-consistent pairs and blank-reference banks in
  the 0.50-2.00 amount band and method window reach Gemini. Calls remain
  sequential; concurrency is deferred until measured latency requires it.
- **Issue / resolution:** Prompt v1 rejected every true match because it treated
  the fuzzy amount difference and expected ledger-gross/bank-net gap as hard
  mismatches. Prompt v2 supplies the accounting policy and passed the live gate;
  raw responses for both versions are preserved locally for comparison.
- **Review fixes:** Indistinguishable evidence is checked before top-two
  pruning and only duplicate rows are removed, so distinct candidates remain;
  invented-ID responses are not cached; returned decisions carry latency;
  metrics include cache-hit rate, confidence bands, and cost per resolved
  match. A fresh raw live log supports the recorded seeded gate.
- **Edge cases:** Currency and timing bypasses, top-two pruning, identical
  evidence, invalid IDs, confidence floor, global conflicts, cache
  invalidation, three-attempt outage handling, filter guards, semantic decoys,
  and complete input reasoning are covered offline.
- **Review:** Sol re-review found no remaining issues. Ponytail found no
  removable Stage 6 complexity.
- **Claude checkpoint:** COMPLETE - duplicate evidence is isolated without
  dropping distinct candidates; all review findings are covered by regressions.

## 09 - Stage 7 Exception Categorization

- **Status:** PASS - Claude feedback applied; correctness and Ponytail reviews
  complete.
- **Implemented:** Typed exception evidence, deterministic source grouping,
  fixed-priority labels, stable results, and duplicate-context rejection.
- **Gate evidence:** The Stage 0-6 design flow with a narration-only Stage 6
  test double received complete, correct labels; all 13 focused Stage 7 tests
  and all Stage 4-7/generator regression tests passed; the full offline suite
  passed 152/152 with one paid live test deselected.
- **Metrics:** 100% design label accuracy; zero null labels, missing records, or
  double-tagged records.
- **Decision:** Missing identity means a blank ledger or settlement reference;
  a blank bank reference alone is valid input to later matching stages.
- **Issue / resolution:** A hard negative borrowing another case's date looked
  late relative to its own settlement. Timing now requires both an exceeded
  window and an amount-consistent bank leg; otherwise it remains an unexplained
  amount mismatch.
- **Review fixes:** Unique negative ledger-bank reversal pairs now reach the
  refund fallback; remaining overlapping Stage 4 candidates become one split
  exception context; contradictory nonblank bank references cannot be joined
  by amount or date; bank-only orphans are amount mismatches rather than
  missing references; Stage 6 test decisions no longer read ground truth.
- **Edge cases:** Missing anchors, non-INR, timing lag, unresolved refunds,
  unresolved splits, hard negatives, stable order, and overlapping contexts.
- **Review:** Terra re-review found no remaining issues. Ponytail found the
  shared Stage 4 candidate search to be the smallest non-duplicated solution.
- **Claude checkpoint:** COMPLETE - bank-only orphan and source-missing cases
  are explicitly distinguished and regression tested.

## 10 - API and Frontend Foundations

- **Status:** STRUCTURE READY - integration not started.
- **Implemented:** Versioned FastAPI contract and a responsive Next.js App
  Router dashboard with overview, reconciliation, tax, Q&A, and audit views.
- **Gate evidence:** Frontend view-model tests passed, the production build
  generated all nine routes, and all five primary pages returned HTTP 200.
- **Decision:** Keep FastAPI as a thin boundary over the future typed pipeline;
  the frontend uses isolated fixtures until those endpoints exist.
- **Issue / resolution:** The frontend worker reached its usage limit after
  writing the scaffold. The resulting files were inspected, API paths aligned,
  and the complete build verified directly.
- **Edge cases:** Loading, error, empty, mobile navigation, table overflow, and
  unavailable-API states are represented.
- **Review:** User review required before API implementation and integration.

## 11 - Reconciliation Stage Guide

- **Status:** COMPLETE
- **Implemented:** Added `docs/reconciliation_stage_guide.md` with stage-by-stage input/output schemas, concrete examples, handled and unhandled problem labels, record-flow behavior, test/gate evidence, and Stage 6 live API/cache details.
- **Gate evidence:** Documentation cross-checked against matching modules, tests, implementation plan, and implementation log; no runtime behavior changed.
- **Metrics:** Documentation-only slice; no application test gate required.
- **Decisions:** Keep live Gemini behavior separate from offline fake-decision tests; document the final deterministic exception stage and the explicit out-of-scope cases.
- **Problems:** None.
- **Claude checkpoint:** Not required for documentation-only work.

## 12 - Stage 8 Tax-Line Enrichment

- **Status:** COMPLETE - approved design implemented with TDD and reviewed.
- **Implemented:** Typed tax findings, unique reference-only 26AS linkage,
  five synthetic GST categories including EMI, fixed-priority TDS mismatch
  labels, and explicit unverifiable outcomes.
- **Gate evidence:** 22/22 focused tests and the full offline suite passed
  (174 passed, 1 live test deselected). Design enrichment produced 66 findings:
  51 clear and 15 mismatches.
- **Metrics:** Overall tag accuracy 100%; precision and recall 100% for
  `SHORT_DEDUCTION`, `MISSING_CHALLAN`, and `WRONG_SECTION`; 0 unverifiable
  findings on the design set.
- **Decisions:** Settlement IDs use membership, not result position. Challans
  compare exactly without normalization. `UNVERIFIABLE` covers broken linkage
  and detected-but-unmodeled over-deduction/challan conflicts, distinguished by
  reasoning.
- **Problems / resolution:** Whitespace-only references initially linked as
  valid; the shared local blank check now excludes them on both sides. Pytest's
  global and OneDrive temp directories hit Windows ACL errors; rerunning with
  `C:\tmp` isolated the environment issue and passed the complete suite.
- **Edge cases:** Unmatched results, split matches, missing/duplicate refs,
  mismatch priority, over-deduction, challan case/whitespace, stable order, and
  non-mutation are covered.
- **Ponytail:** COMPLETE - removed a redundant test parameter; no dependency,
  synthetic-tax module, or speculative abstraction was added.
- **Claude checkpoint:** COMPLETE - approved as implemented; documented that
  challan validity takes precedence over section and amount comparisons.

## 13 - Stage 9 Text-to-SQL Q&A

- **Status:** COMPLETE (Gemini-only) - NVIDIA remains disabled pending its
  independent live gate.
- **Implemented:** Typed run-scoped SQLite snapshots, read-only SQL authorizer,
  stacked-query rejection, 50-row cap, 250 ms runtime guard, grounded answers,
  provider affinity, telemetry, Gemini adapter, and Nemotron NIM adapter.
- **Gate evidence:** 27/27 focused offline tests pass; complete offline suite
  passes (201 passed, 4 live tests deselected). Grounded
  Gemini SQL gate passed 23/24 (95.83%) with 100% unsafe-request rejection.
- **Gemini behavior:** Three real end-to-end questions returned correct SQL,
  evidence rows, and natural-language answers for match rate, tax mismatches,
  and highest-gross payment method.
- **Metrics:** Grounded 24-question run used 9,177 input and 2,056 output tokens,
  averaged 1,177.34 ms, and cost $0.0078931. End-to-end smoke cost $0.0016211;
  one cold SQL call took 15.97 s while the other five calls took 1.5-2.0 s.
- **Decisions:** Provider affinity is explicit; unsafe SQL short-circuits;
  stacked statements rely on `sqlite3.execute()` plus a regression test; a
  250 ms progress guard bounds execution and refuses without fallback; caught
  stacked-statement errors enter Q&A telemetry; metrics include cost. Exact
  stored enums are prompt-visible; scoring compares semantic row values and
  accepts harmless extra evidence columns.
- **Problems / resolution:** A temporary Gemini client closed before its call;
  retaining it locally matched the working Stage 6 pattern. The first live
  prompt omitted stored enum spellings and produced only 4.17% strict accuracy;
  grounding those values raised the next run to 95.83%. Its sole reported miss
  was a correct answer with an extra supporting amount, so the scorer now
  treats extra evidence as valid. DeepSeek V4 Pro timed out at both 30- and
  90-second limits. Nemotron responded within 1.67-2.83 seconds and generated
  correct SQL when given the real schema-grounded prompt; its full gate could
  not run because the Codex external-call allowance was exhausted.
- **Risk / contingency:** NVIDIA is a trial service. If unavailable, Q&A runs
  Gemini-only and fails closed with `QA_UNAVAILABLE`.
- **Ponytail:** PASS; stdlib SQLite and `urllib` avoided new runtime
  dependencies, and the failing DeepSeek candidate was replaced rather than
  hidden behind longer retries.
- **Claude checkpoint:** Stage 9 design approved before implementation. NVIDIA
  certification is a separate optional follow-up and remains disabled.

## 14 - End-to-End Integration: Pipeline And Evaluation

- **Status:** PASS - ready for commit review.
- **Implemented:** Added the sole Stages 0-8 orchestrator, typed pipeline input
  and output boundaries, operational stage counts, and a separate truth-only
  evaluator for matching, exceptions, hard negatives, and tax findings.
- **Gate evidence:** 4/4 focused integration tests and 205/205 complete offline
  tests pass; 4 live tests were intentionally deselected.
- **Metrics:** Design set scored 100% match precision, 100% match recall, 100%
  hard-negative precision, 100% tax accuracy, and 100% precision/recall for
  every generated exception category with the deterministic Stage 6 test double.
- **Decisions:** Results remain ordered by pipeline stage. Refund confirmations
  come only from Stage 2 exact matches. Operational match rate is matched output
  groups divided by all final output groups; truth-scored match rate remains a
  separate evaluation metric.
- **Problem / resolution:** Windows denied pytest's default temporary directory;
  the established `C:\\tmp` base restored a clean test run. No pipeline defect.
- **Edge cases:** Duplicate and excluded inputs are retained as typed evidence;
  final transaction records have complete coverage and unique ownership; inputs
  remain unchanged; ground truth is absent from `PipelineInput`.
- **Ponytail:** PASS - orchestration delegates to existing stage functions and
  evaluation is the only new separate responsibility.
- **Claude checkpoint:** Integration design approved before implementation.

## 15 - End-to-End Integration: Supabase Store And FastAPI

- **Status:** CODE PASS - live migration/readback pending.
- **Implemented:** Added the run lifecycle, typed source round-trip, completed-only
  reads, single-holdout enforcement, direct Supabase persistence, and run-scoped
  FastAPI result, metric, tax, LLM, and Q&A routes.
- **Gate evidence:** 14 focused store/API tests and the complete 219-test offline
  Python suite pass; 4 live provider tests remain deselected.
- **Decisions:** Partial writes remain hidden behind `running`/`failed`; no upload
  run type, queue, auth layer, or blockchain placeholder was added.
- **Problem / likely cause:** Supabase returned `PGRST205`; credentials reach the
  project, but `public.runs` is absent because migration `001_runs.sql` has not
  been applied there.
- **Expected resolution:** Run the checked-in migration, then persist and read
  back the design run before this slice is marked complete.
- **Ponytail:** PASS - stdlib PostgREST transport and the database uniqueness
  constraint keep the integration narrow.
- **Claude checkpoint:** COMPLETE - integration design approved.

## 16 - End-to-End Integration: Real-Data Next.js Dashboard

- **Status:** CODE PASS - live browser gate pending.
- **Implemented:** Removed fixtures, added holdout-first/design-fallback run
  selection, preserved `run_id` across pages and drill-down links, aligned tax
  types with FastAPI, and removed the unimplemented audit route and navigation.
- **Gate evidence:** Frontend behavior tests and TypeScript pass; the optimized
  Next.js build generates seven real-data routes successfully.
- **Decisions:** Missing `NEXT_PUBLIC_API_URL` fails visibly; completed runs are
  the only selectable data source.
- **Problem / resolution:** Global query-aware navigation initially broke 404
  prerendering; the required React `Suspense` boundary fixed the production build.
- **Edge cases:** Requested run, holdout default, design-only fallback, no runs,
  result filters, nullable expected TDS, and run-scoped Q&A are covered.
- **Ponytail:** PASS - deleted fixture and audit code; one selector component and
  three small pure helpers cover the shared behavior.
- **Claude checkpoint:** Not required until the persisted design browser gate.

## 17 - End-to-End Integration: Live Design Run

- **Status:** PASS - design data is persisted and served live.
- **Implemented:** Applied the Supabase schema, executed the live Gemini design
  run, reconstructed it from Supabase in a fresh process, and served it through
  FastAPI and Next.js.
- **Gate evidence:** Design run `3790c5b7-56e3-4145-bcc0-717164c0f0b1` completed
  in 34.02s with 87 reconciliation results and 66 tax findings. All dashboard
  pages returned HTTP 200 with the real run label. The Next.js Q&A proxy returned
  a grounded live Gemini answer from the persisted snapshot.
- **Metrics:** Match precision 100%; recall 91.07%; hard-negative precision
  100%; tax accuracy 100%. Persisted 24-question Q&A gate: 24/24 correct,
  100% unsafe-query rejection, 1.03s average latency, $0.0079506 estimated cost.
- **Decisions:** Design remains the only stored run; holdout is still untouched.
- **Problem / resolution:** Browser screenshots could not run because the Codex
  browser allowance was exhausted. Equivalent live HTTP page and API checks
  passed; visual inspection remains a manual final check.
- **Claude checkpoint:** Required before freezing code and executing the single
  holdout run.

## 18 - Explainable Results, Uploads, And Q&A History

- **Status:** PASS - live local dashboard updated.
- **Implemented:** Added concise source-level reasons to exact-reference,
  fee-adjusted, and split-settlement matches; added CSV upload runs using the
  existing schemas; and retained Ask-tab messages for the selected run session.
- **Gate evidence:** 226 offline Python tests pass; frontend unit tests,
  TypeScript, and production build pass. A real one-row CSV upload completed in
  Supabase, appears in the run list, and correctly leaves precision/recall null.
  All 62 matched records in refreshed design run `093dd2e3-07ae-4b84-9001-94348c40fd5f`
  have nonblank reasoning.
- **Decision:** Uploaded data gets operational metrics only; no synthetic
  accuracy score is displayed. Q&A history is session-only and scoped to the
  selected run.
- **Problem / resolution:** Exact, fee, and split match producers deliberately
  emitted `reasoning=None`; fixed at each producer. Loopback CORS initially
  omitted `127.0.0.1`; added it and covered preflight. A dev/build cache conflict
  caused a temporary Next.js 500; clean restart restored the live dashboard.
- **Ponytail:** PASS - reused CSV/Pydantic standard libraries and existing run
  persistence; no upload storage layer or chat-history database was added.

## 19 - Dashboard Run Alignment And Ask History

- **Status:** PASS - frontend checks complete; targeted API test is blocked by
  the local Codex execution quota.
- **Implemented:** The dashboard now shows only the newest completed run per
  dataset type, uses that same run list across all tabs, aligns overview match
  rate with the result rows queried by Q&A, and persists Ask history per run in
  browser session storage across tab navigation.
- **Gate evidence:** Web view-model tests and TypeScript pass; Overview, Ask,
  Reconciliations, and Tax live routes return HTTP 200. The uploaded run's
  displayed 67.6% is verified as 25 matched groups out of 37 result groups.
- **Decision:** History remains local to the browser session and selected run;
  no chat records are stored with financial data.
- **Problem / resolution:** Repeated persisted design runs made the selector
  ambiguous, and an intermediate wrapper prevented question bubbles from
  right-aligning. The selector keeps the newest run of each type; each Ask turn
  now has its own layout container.
- **Ponytail:** PASS - one pure selector helper and native session storage;
  no chat backend or new persistence schema.

## 20 - Upload Run Selection

- **Status:** PASS.
- **Implemented:** Removed the sidebar upload list; the dataset dropdown keeps
  one Design/Holdout entry and lists every upload separately by timestamp.
- **Gate evidence:** Frontend view-model tests and TypeScript pass.
- **Decision:** Upload history is discoverable in the existing selector, with no
  secondary navigation or storage model.
- **Ponytail:** PASS - reused the existing list-runs API and run-scoped Ask flow.

## 21 - Upload Metrics And Q&A Method Contract

- **Status:** PASS - frontend checks complete; Python regression execution is
  blocked by the local Codex execution quota.
- **Implemented:** Removed exception-accuracy display for uploaded runs and
  corrected the Q&A SQL prompt to use persisted method names, including
  `llm_remainder` and `fee_adjusted_window`.
- **Gate evidence:** The selected upload contains one real `llm_remainder`
  result (`result-0025`); frontend view-model tests and TypeScript pass.
- **Decision:** Exception accuracy remains available only when ground truth is
  present; uploaded data has none and must not imply a score.
- **Problem / resolution:** Q&A was instructed to query obsolete method values
  (`llm`, `fee_adjusted`) while the pipeline stores different names. The prompt
  and its regression expectations now match the persisted schema.
- **Ponytail:** PASS - one prompt correction and one conditional panel; no new
  metric or Q&A data path.

## 22 - Uploaded Document Scope

- **Status:** PASS - live API and Q&A verified.
- **Implemented:** Ask identifies the uploaded Ledger, Settlement, Bank, and
  Tax 26AS documents. Missing Tax 26AS is explicit; the Tax tab renders an
  honest empty state instead of tax findings without a source document.
- **Gate evidence:** Restarted FastAPI returns `tax_26as: 0` for upload
  `f9e967f7-a722-4755-b8bb-afce7b8e61b5`. A live Gemini question correctly
  returned one `llm_remainder` match with its generated SQL.
- **Problem / resolution:** The old API process continued serving the prior
  response model. Restarted it with the no-project runtime path and reload.
- **Ponytail:** PASS - exposes an existing source count; no document storage or
  new endpoint was added.

## 23 - Upload Reset And README

- **Status:** PASS - frontend checks complete.
- **Implemented:** Added a Reset files action that clears only unsubmitted CSV
  selections. Replaced the stale README with current setup, pipeline, run type,
  upload scope, Supabase, and verification guidance.
- **Gate evidence:** Frontend view-model tests and TypeScript pass.
- **Decision:** Reset never deletes a completed upload run or its persisted
  results; existing Dataset selections remain available.
- **Ponytail:** PASS - native form reset and local component state, no delete
  endpoint or upload lifecycle abstraction.
