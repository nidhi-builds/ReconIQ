# ReconIQ Reconciliation Stage Guide

This guide explains what each reconciliation stage receives, what it can resolve, what it deliberately leaves unresolved, and how records flow to the next stage.

## Overall flow

```text
raw ledger, settlement, bank rows
        |
        v
Stage 0  deduplicate ledger rows
        |
        v
Stage 1  remove obvious non-transaction bank lines
        |
        v
Stage 2  exact three-source reference match
        |
        v
Stage 3  fee-adjusted one-to-one match
        |
        v
Stage 4  split settlement match
        |
        v
Stage 5  refund/reversal link
        |
        v
Stage 6  LLM review of the narrow unresolved remainder
        |
        v
Stage 7  deterministic exception categorization
```

Every stage consumes some records and returns the rest. A later stage must not reopen a record already accepted by an earlier stage.

The shared match output is `ReconciliationResult`:

```python
ReconciliationResult(
    record_ids=[...],
    matched=True or False,
    confidence=0.0_to_1.0,
    method="...",
    exception_reason=None or "...",
    reasoning=None or "...",
)
```

## Stage 0 — Ledger deduplication

Module: `matching/dedup.py`

Input:

```python
list[LedgerEntry]
```

Output:

```python
DeduplicationResult(
    retained_entries=list[LedgerEntry],
    duplicates=list[DuplicateLink],
)
```

It treats rows as duplicate when `order_id` and `amount` are equal and their dates are within the inclusive one-day window. The first input row is retained as canonical.

Example:

```text
Input:
  order-7, ref-7, ₹1000, Jan 8
  order-7, ref-7-retry, ₹1000, Jan 9

Output:
  retained:  order-7 / Jan 8
  duplicate: order-7 / Jan 9 -> retained index 0
```

Handles: retry duplicates, same-day duplicates, one-day duplicates, empty input.

Does not handle: different amounts, different order IDs, split settlements, refunds, or duplicate records more than one day apart.

Problem labels it does not assign: all exception labels. It only produces duplicate trace links.

Focused gate: 6/6 tests; design precision 100%, recall 100%, zero false collapses.

## Stage 1 — Bank scope filtering

Module: `matching/scope_filter.py`

Input:

```python
list[BankEntry]
```

Output:

```python
ScopeFilterResult(
    remaining_entries=list[BankEntry],
    exclusions=list[ScopeExclusion],
)
```

It removes bank narrations containing complete normalized phrases:

```text
LOAN DISBURSEMENT
GST REFUND
INTERNAL TRANSFER
```

Example:

```text
Input narration: "prefix INTERNAL-TRANSFER: suffix"
Output: excluded
Reason: NON_TRANSACTION_EXCLUDED
Matched phrase: INTERNAL TRANSFER
```

Handles: case changes, repeated spaces, punctuation, hyphens, and stable exclusion order.

Does not handle: actual payment matching, refunds, amount differences, timing, or ambiguous narrations. `RAZORPAY SETTLEMENT`, `REFUND ...`, and `PAYMENT RECEIVED` remain in scope.

Problem label: `NON_TRANSACTION_EXCLUDED`.

Focused gate: 7/7 tests; design recall 100%, zero false exclusions.

## Stage 2 — Exact reference matching

Module: `matching/exact_match.py`

Input:

```python
ledger_entries: list[LedgerEntry]
settlement_entries: list[SettlementEntry]
bank_entries: list[BankEntry]
```

Output:

```python
ExactMatchResult(
    matches=list[ReconciliationResult],
    remaining_ledger_entries=list[LedgerEntry],
    remaining_settlement_entries=list[SettlementEntry],
    remaining_bank_entries=list[BankEntry],
)
```

It matches only when the same nonblank `ref_id` appears exactly once in all three sources.

Example:

```text
Ledger:      order-1 / ref-1
Settlement:  settlement-1 / ref-1
Bank:        bank-1 / ref-1

Output:
  record_ids = [order-1, settlement-1, bank-1]
  matched = True
  confidence = 1.0
  method = exact_ref
```

Handles: clean three-source matches and exact references.

Does not handle: missing references, repeated references, two-source matches, case differences, whitespace differences, fee-only differences, timing-only matches, splits, or refunds without a settlement row.

Problem labels it does not assign. Unmatched rows continue downstream.

Focused gate: 16/16 tests; 100% design precision, zero hard-negative matches. Six original refund legs match here; derived refund legs remain.

## Stage 3 — Fee-adjusted matching

Module: `matching/fee_adjusted_match.py`

Input: unresolved ledger, settlement, and bank rows after Stage 2.

Output: `FeeAdjustedMatchResult` with matches and remaining rows.

It first validates the ledger-settlement pair using `ref_id`, currency, payment method, gross amount, fee, GST, and the rate card. It then matches a blank-reference bank row when:

```text
abs(bank.amount - settlement.net_amount) < 0.50
```

and the bank date is inside the payment-method settlement window.

Example for a ₹1,000 UPI sale:

```text
Ledger amount:          1000.00
Gateway fee (1%):         10.00
GST on fee:                1.80
Settlement net:          988.20
Bank amount:              988.20

Output:
  method = fee_adjusted_window
  confidence = 0.9
```

Handles: missing bank references, gateway fee/GST deductions, small bank rounding differences, and method-specific timing.

Does not handle: split sums, refunds, ambiguous bank candidates, non-INR rows, inconsistent source pairs, or populated conflicting bank references.

Problem labels it does not assign. Unresolved records continue to Stage 4.

Focused gate: 18/18 tests; precision 100%, recall 100%, zero hard-negative matches.

## Stage 4 — Split settlement matching

Module: `matching/split_settlement.py`

Input: unresolved rows after Stage 3.

Output: `SplitSettlementMatchResult`.

It pairs unique ledger and settlement parts by reference, groups them by payment method and settlement date, and searches combinations of 2–5 parts. The sum of stored settlement `net_amount` values must be within the strict `< 0.50` tolerance of one blank-reference bank amount.

Example:

```text
Settlement nets:  ₹100.00 + ₹200.00 = ₹300.00
Bank amount:      ₹300.00

Output:
  record_ids = [order-1, order-2,
                settlement-1, settlement-2,
                bank-1]
  method = split_settlement
  confidence = 0.85
```

Handles: unique 2–5 part splits, same-method/same-date batches, and blank bank references.

Does not handle: many-to-many splits over multiple dates, ambiguous subset sums, one combination matching two banks, one bank matching multiple combinations, refunds, or nonpositive/non-INR parts.

Problem labels it does not assign. Ambiguous cases continue to Stage 5–7.

Focused and generator-invariant gate: 28/28 checks; 6/6 design splits recovered, precision 100%, recall 100%.

## Stage 5 — Refund/reversal linking

Module: `matching/refund_match.py`

Input:

```python
remaining_ledger_entries
remaining_bank_entries
confirmed_references: list[ConfirmedReference]
```

A `ConfirmedReference` contains the already-confirmed original `ref_id`, original gross amount, and original match result.

Output: `RefundMatchResult` with two-record refund matches and remaining rows.

It accepts a refund only when:

```text
refund ref ends with "-refund"
refund ledger amount < 0
refund bank amount < 0
original reference was confirmed by exact_ref
refund ledger amount == refund bank amount == -original gross amount
```

Example:

```text
Confirmed original: ref-1, +1000.00
Refund ledger:      ref-1-refund, -1000.00
Refund bank:        ref-1-refund, -1000.00

Output:
  record_ids = [refund-order-1, refund-bank-1]
  method = refund_reversal
  confidence = 0.95
  reasoning = Refund of ref-1
```

Handles: full-gross refund reversals linked to a confirmed original.

Does not handle: partial/amount-drift refunds, unlinked negative rows, duplicate refund candidates, chargebacks, or refunds linked to split groups.

Problem label left for Stage 7: `REFUND_UNLINKED`.

Focused and generator gate: 17/17 checks; 6/6 design refund groups covered, zero incorrect links.

## Stage 6 — LLM-assisted remainder

Module: `matching/llm_match.py`

Input: only the unresolved remainder after deterministic Stages 0–5.

Candidate input to Gemini is a typed `LLMCandidate` containing ledger, settlement, and bank IDs, references, amounts, dates, narration, amount difference, and date difference. It deliberately excludes ground truth and unrelated customer data.

The deterministic builder sends only candidates that have:

```text
INR and source-consistent ledger/settlement pair
blank bank reference
amount difference from 0.50 through 2.00 inclusive
bank date inside the method window
```

It ranks candidates and sends at most the top two per pair. Filtered rows receive local decisions such as `CURRENCY_MISMATCH`, `TIMING_WINDOW_EXCEEDED`, or `NO_CANDIDATE` without an API call.

### Live API example

Input:

```text
Ledger:      order-1 / ref-1 / gross ₹1000
Settlement:  settlement-1 / net ₹980
Bank:        bank-1 / amount ₹981.37 / narration "PARTIAL REF ref-1"
Difference:  ₹1.37
```

The candidate passes deterministic filters and is sent to Gemini with:

```text
temperature = 0
response format = structured JSON
prompt version = v2
```

Possible model output:

```json
{
  "matched": true,
  "matched_ids": ["order-1", "settlement-1", "bank-1"],
  "confidence": 0.90,
  "reason_category": null,
  "reasoning": "The narration contains an unambiguous shortened reference."
}
```

The finalizer accepts it because the IDs are exact and confidence is at least `0.5`:

```python
ReconciliationResult(
    record_ids=["order-1", "settlement-1", "bank-1"],
    matched=True,
    confidence=0.90,
    method="llm_remainder",
    reasoning="The narration contains an unambiguous shortened reference.",
)
```

### Cache and retry example

On the first identical candidate:

```text
cache miss → live Gemini call → store decision → log call
```

On the second identical candidate with the same model and prompt version:

```text
cache hit → no Gemini call → reuse stored decision → log cache hit
```

If Gemini fails:

```text
attempt 1 → retry
attempt 2 → retry
attempt 3 → local LLM_UNAVAILABLE decision
```

An outage is not cached as a successful decision.

Handles: narration-based ambiguity, shortened references, one-character reference errors, and semantic decoys.

Does not handle: deterministic cases filtered before the API, ground-truth lookup, low-confidence automatic matches, invented IDs, conflicting claims, or unresolved uncertainty.

LLM tests: offline tests use injected fake decision functions for deterministic behavior. Only `test_live_gemini_passes_design_truth_gate` makes real API calls and requires `GEMINI_API_KEY`.

Live gate result recorded in the implementation log: 5/5 true ambiguous matches accepted, 4 competing candidates rejected, 2/2 semantic hard negatives rejected, 100% precision, 100% recall, 11 calls, 5,599 input tokens, 1,406 output tokens, 2,570.71 ms average latency, and estimated cost `$0.0051947`.

## Stage 7 — Exception categorization

Module: `matching/exceptions.py`

Input:

```python
list[ExceptionEvidence]
```

The evidence builder can derive these rows from unresolved ledger, settlement, and bank records. It tries source identity first, then amount/date fallbacks, and records the evidence needed for a deterministic label.

Output:

```python
list[ReconciliationResult]
```

Every output is unmatched and receives exactly one label using fixed priority:

```text
MISSING_REF_ID
CURRENCY_MISMATCH
TIMING_LAG_EXCEEDED
REFUND_UNLINKED
SPLIT_SETTLEMENT_UNRESOLVED
AMOUNT_MISMATCH_UNEXPLAINED
```

Example input:

```python
ExceptionEvidence(
    record_ids=["order-1", "settlement-1", "bank-1"],
    ledger_ref_id="ref-1",
    settlement_ref_id="ref-1",
    currency="INR",
    days_since_expected=3,
    method_window_days=2,
    amount_difference=0.0,
)
```

Output:

```python
ReconciliationResult(
    record_ids=["order-1", "settlement-1", "bank-1"],
    matched=False,
    confidence=1.0,
    method="exception_rules",
    exception_reason="TIMING_LAG_EXCEEDED",
    reasoning="The bank entry exceeded its settlement window.",
)
```

Another example:

```text
ledger_ref_id = "ref-1"
settlement_ref_id = "ref-1"
currency = "INR"
amount_difference = 25.00
```

Output label:

```text
AMOUNT_MISMATCH_UNEXPLAINED
```

Handles: final deterministic labels for unresolved records and stable audit reasoning.

Does not handle: matching records, changing earlier match decisions, live LLM calls, or assigning multiple labels to one record.

Focused gate: 10 executed Stage 7 cases; full design remainder achieved 100% label accuracy, zero null labels, missing records, or double tags.

## LLM tracker behavior

Module: `observability/llm_tracker.py`

The tracker is not a matching stage. It records LLM operations in SQLite.

`decision_cache` stores the structured decision using a SHA-256 key derived from:

```text
model + prompt version + complete candidate JSON
```

`call_log` stores:

```text
timestamp, model, prompt version, candidate, response,
cache hit, latency, token counts, estimated cost, error
```

Example metrics:

```python
LLMMetrics(
    total_calls=2,
    cache_hits=1,
    failed_calls=0,
    input_tokens=50,
    output_tokens=20,
    average_latency_ms=120.0,
    estimated_cost_usd=0.000065,
)
```

Tracker tests are offline and use temporary SQLite databases. They verify persistence, cache-key invalidation, call/error metrics, and free-versus-paid cost calculation. They do not call Gemini.

## Record-flow example from input to final label

```text
Input rows:
  duplicate ledger retry
  non-transaction bank line
  exact reference transaction
  fee-adjusted transaction
  split settlement
  refund reversal
  ambiguous remainder
  hard negative
```

Stage results:

```text
Stage 0:
  duplicate retry removed

Stage 1:
  LOAN DISBURSEMENT removed

Stage 2:
  exact reference transaction matched at confidence 1.0

Stage 3:
  fee-adjusted transaction matched at confidence 0.9

Stage 4:
  split parts matched to one bank deposit at confidence 0.85

Stage 5:
  negative refund ledger/bank legs linked at confidence 0.95

Stage 6:
  eligible ambiguous remainder sent to Gemini or served from cache

Stage 7:
  everything still unresolved receives exactly one exception label
```

The current implementation intentionally does not cover many-to-many splits over multiple dates, chargebacks/disputes arriving weeks later, re-matching prior reconciliations, or linking a refund to an original split-settlement group.
