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

## Next

- **Stage 3 - Fee-adjusted matching:** READY for design/test-plan review before implementation.
