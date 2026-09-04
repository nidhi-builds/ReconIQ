# Multi-Source Reconciliation Engine — Implementation Plan (v2)
**Razorpay Buildathon — Track: AI Finance Controller**

This document is the full spec for implementation. Follow it stage by stage. Every stage has a **non-negotiable gate** — do not move to the next stage until the current one's gate passes on the design set.

---

## 1. Project Overview

Build an agent that reconciles transactions across three sources (gateway settlement report, bank statement, internal ledger), enriches matched transactions with GST/TDS tax-line assignment, exposes a Q&A layer over the reconciled result, and anchors an immutable audit trail on-chain.

**Core scoring artifact:** a reason-categorized exceptions table + per-category precision/recall on a held-out synthetic batch, computed once and reported honestly.

---

## 2. Tech Stack

| Layer | Choice |
|---|---|
| Matching engine | Python + pandas |
| API | FastAPI |
| Database | Postgres via Supabase (free hosted tier) |
| Fuzzy/reasoning layer | LLM API, structured JSON output only, temperature 0 |
| Q&A layer | Text-to-SQL agent (NOT vector RAG — data is structured/tabular) |
| Frontend | **Next.js** |
| Blockchain | web3.py + Base Sepolia or Polygon Amoy testnet, Solidity contract, Merkle batching |
| Backend hosting | **Render** (auto-deploy from GitHub) |
| Frontend hosting | **Vercel** (native for Next.js — zero-config deploys, free tier). If you'd rather keep one platform, Render also serves Next.js; Vercel is simply the better default for this stack. |

**Deploy a bare-bones skeleton on Day 1** (empty FastAPI endpoint on Render + empty Next.js page on Vercel + live empty Supabase table, wired end-to-end). Every subsequent feature is built against the live deployment, not localhost.

Secrets (LLM API key, testnet wallet private key) go into Render/Vercel environment variables from Day 1. Never commit them to the repo.

---

## 3. Repo Structure

```
/recon-engine
  /data
    generator.py
    schemas.py
  /matching
    pipeline.py
    dedup.py                 # stage 0
    scope_filter.py          # stage 1
    exact_match.py            # stage 2
    fee_adjusted_match.py      # stage 3
    split_settlement.py         # stage 4
    refund_match.py               # stage 5
    llm_match.py                   # stage 6
    exceptions.py                    # stage 7
    rate_card.py
    settlement_windows.py
  /tax
    gst_tds_enrichment.py
    synthetic_26as.py
  /qa
    text_to_sql_agent.py
  /audit
    merkle.py
    chain_client.py
    contract/AuditLog.sol
  /observability
    llm_tracker.py              # cost, latency, cache hit rate, call log
  /api
    main.py
  /web                          # Next.js app
    app/
    components/
  /tests
    test_dedup.py
    test_scope_filter.py
    test_exact_match.py
    test_fee_adjusted_match.py
    test_split_settlement.py
    test_refund_match.py
    test_exceptions.py
    test_tax_enrichment.py
    test_llm_tracker.py
  AGENTS.md                  # MANDATORY — see Section 14
  .env.example
  README.md
```

---

## 4. Data Schemas

Define these as Pydantic models in `data/schemas.py`.

```python
class LedgerEntry(BaseModel):
    order_id: str
    ref_id: str | None
    amount: float
    currency: str
    payment_method: str          # upi | card | netbanking | wallet | emi
    order_date: date
    customer_id: str

class SettlementEntry(BaseModel):
    settlement_id: str
    ref_id: str | None
    gross_amount: float
    fee: float
    gst_on_fee: float
    net_amount: float
    payment_method: str
    settlement_date: date

class BankEntry(BaseModel):
    bank_txn_id: str
    ref_id: str | None
    amount: float
    value_date: date
    narration: str
    is_transaction: bool           # ground truth only, hidden from the matcher
```

Ground truth fields (`is_transaction`, `true_match_group`, `true_exception_reason`) exist ONLY in the generator's internal record — stripped before the matcher ever sees the data. Keep in a separate `ground_truth.csv` used only for scoring.

---

## 5. Synthetic Data Generator (`data/generator.py`)

**Model assignment: design the case list with Sol, have Claude review the list for gaps, implement with Terra/Codex.**

**Volume: 120 records total** (50 is too thin for a national-level bar — several case types would have only 1-2 instances, which makes per-category precision/recall meaningless — a single wrong call swings the category's score by 50-100%).

- **Design set: ~85 records** — used while building and debugging.
- **Holdout set: ~35 records** — untouched until Day 6, same case-type proportions as the design set so the final metrics generalize instead of testing on a differently-shaped sample.

**Gate — non-negotiable before moving to Section 6:** every case type below has **at least 4 instances** in the design set and **at least 2** in the holdout set. Do not proceed to pipeline implementation until this is true — a pipeline built against under-represented case types will look like it works and then fail publicly on the holdout set.

| Case type | What to inject |
|---|---|
| Clean exact match | Same ref_id across all 3 sources |
| Fee-adjusted match | Settlement = ledger amount minus fee minus GST-on-fee |
| Timing lag (within window) | Bank value_date within method-specific window |
| Timing lag EXCEEDED | Gap beyond window — should become an exception |
| Split settlement | 2-4 ledger entries summing to one bank deposit |
| Duplicate ledger entry | Same order_id twice (simulated retry) |
| Non-transaction bank line | Loan disbursement, GST refund, internal transfer |
| Missing ref_id | ref_id null on one or more sources |
| Currency mismatch | Non-INR currency |
| Refund/reversal | Negative-amount entry referencing an original transaction |
| Ambiguous remainder | Doesn't cleanly fit rules 2-5 — requires the LLM pass |
| Tax mismatch: short deduction | TDS deducted below required rate |
| Tax mismatch: missing challan | No challan for a deduction that should have one |
| Tax mismatch: wrong section | TDS filed under wrong section code |
| **Hard negative (new)** | **A record with close amount + close date to another, but genuinely unrelated — must NOT match. This is what actually proves matcher precision to a judge, not just recall.** |

---

## 6. Matching Pipeline (`matching/pipeline.py`)

Each stage only processes records unresolved by the previous stage. **Write the TDD test for a stage before implementing it.** A stage is not "done" until it clears BOTH its numeric gate AND a Ponytail review pass — see Section 15.

### Stage 0 — Dedup (`dedup.py`)
Collapse ledger entries with same `order_id` + `amount` within a short time window.
**Gate:** 100% of injected duplicate cases collapsed; 0 false-collapses of genuinely distinct records.

### Stage 1 — Non-transaction filter (`scope_filter.py`)
Keyword rules on narration first; ambiguous cases route to stage 6, not guessed here. Excluded records logged separately, not counted as exceptions.
**Gate:** 100% recall on the design set's non-transaction lines (a leaked one silently corrupts your headline match-rate).

### Stage 2 — Exact reference match (`exact_match.py`)
```python
exact = ledger.merge(settlement, on="ref_id").merge(bank, on="ref_id")
exact["confidence"] = 1.0
exact["method"] = "exact_ref"
```
**Gate:** 100% precision — this stage claims certainty, so it must never be wrong on the design set.

### Stage 3 — Fee-adjusted amount + method-aware window (`fee_adjusted_match.py`)
```python
def expected_settlement(amount, method, rate_card):
    fee = amount * rate_card[method]["fee_pct"]
    gst_on_fee = fee * 0.18
    return amount - fee - gst_on_fee
```
Match on `abs(bank_amount - expected) < tolerance` AND date within `settlement_windows[method]`. Both required.
**Gate:** ≥95% precision, ≥90% recall on this stage's injected case type, and 0 false matches against the injected hard-negative records.

### Stage 4 — Split settlement, tightened (`split_settlement.py`)
```python
from itertools import combinations
def find_split_match(bank_row, candidate_ledger_rows, tolerance=0.5):
    for r in range(2, 6):
        for combo in combinations(candidate_ledger_rows, r):
            if abs(sum(c.expected for c in combo) - bank_row.amount) < tolerance:
                return combo
    return None
```
**Gate:** ≥90% precision (tight tolerance is what earns this — a loose split-match is the single easiest way to accidentally group unrelated transactions).

### Stage 5 — Refund/reversal (`refund_match.py`)
Detect negative-amount entries referencing an original transaction; net against it.
**Decision point, end of Day 3:** if behind schedule, skip implementation — route these to stage 7 as `REFUND_UNLINKED` instead. Either way:
**Gate:** every refund-type record in the design set is either correctly netted or correctly tagged — none fall into a generic mismatch bucket.

### Stage 6 — LLM-assisted remainder (`llm_match.py`)
```python
class MatchDecision(BaseModel):
    matched: bool
    matched_ids: list[str] | None
    confidence: float
    reason_category: str | None
    reasoning: str
```
- Temperature 0, structured output only.
- Only the true remainder reaches this stage — **do not run stage 6 on records already resolved by stages 0-5.** Reasons: (1) cost and latency for zero benefit — a rule-based match is already certain; (2) it reintroduces non-determinism into a decision that was previously deterministic, which breaks the auditability the whole rules-first design exists to protect; (3) an LLM overriding a correct rule-based match with a wrong probabilistic one is a worse failure mode than a slow pipeline. If you want extra confidence on rule-based matches, do it as a **separate, offline QA sample** — pull a random 10% of rule-matched records and have the LLM sanity-check them outside the pipeline, purely for your own validation, never as a live decision path.
- Cache decisions by record-pair (idempotency).
- Confidence < 0.5 → do not auto-match, route to stage 7.
**Gate:** every record reaching this stage gets a decision + non-empty reasoning string; 0 auto-matches below the 0.5 floor.

### Stage 7 — Exception categorization (`exceptions.py`)
Fixed-order decision tree, first match wins:
```python
def categorize_exception(record):
    if not record.ref_id: return "MISSING_REF_ID"
    if record.currency != "INR": return "CURRENCY_MISMATCH"
    if record.days_since_expected > record.method_window: return "TIMING_LAG_EXCEEDED"
    if record.is_refund_candidate and not record.refund_resolved: return "REFUND_UNLINKED"
    if record.could_be_split and not record.split_resolved: return "SPLIT_SETTLEMENT_UNRESOLVED"
    return "AMOUNT_MISMATCH_UNEXPLAINED"
```
**Gate:** 100% of unresolved records get exactly one category — zero nulls, zero double-tags.

**Output schema (core deliverable table):**
```python
class ReconciliationResult(BaseModel):
    record_ids: list[str]
    matched: bool
    confidence: float
    method: str
    exception_reason: str | None
    reasoning: str | None
```

---

## 7. LLM Operations — Rate Limiting, Cost, Tracking, Observability

This is a real gap if left unaddressed, and a genuine differentiator if handled — most hackathon teams don't think about it.

- **Rate limiting:** stage 6 only sees the true remainder (roughly 10-20% of 120 records ≈ 12-24 calls), so provider limits are unlikely to bite. Still implement exponential backoff + retry (max 3 attempts) and cap concurrency with a semaphore (e.g., 5 concurrent calls) — costs nothing to add, and its absence is an obvious gap to a judge who asks "what happens at scale."
- **Cost tracking:** log estimated cost per call (`observability/llm_tracker.py`) and report **cost per resolved exception** as a metric — this doubles as proof that your rules-first architecture keeps LLM usage (and cost) small, which is itself a design strength worth stating explicitly.
- **Call tracking:** every LLM call logged to a dedicated table — input payload, output decision, confidence, cache hit/miss, latency, timestamp. This is your audit trail for the *reasoning* layer, separate from the blockchain audit trail for the *decision* layer.
- **Observability dashboard panel:** total calls, cache hit rate, avg latency, running cost estimate, confidence distribution. A small panel, but it's a concrete "we thought about production readiness" signal in the demo.

---

## 8. Tax-Line Enrichment (`tax/gst_tds_enrichment.py`, `tax/synthetic_26as.py`)

Runs only on matched records — adds columns to the reconciliation result, not a separate pipeline.
- `gst_category`: simplified synthetic schema (3-4 categories — do not model real GST law).
- `tds_section`: small fixed set (e.g., 194C, 194J, 194H).

Build `26as_reference.csv` with deliberate mismatches; flag `SHORT_DEDUCTION`, `MISSING_CHALLAN`, `WRONG_SECTION`.
**Gate:** ≥90% tag accuracy vs synthetic ground truth on the design set.

---

## 9. Q&A Layer (`qa/text_to_sql_agent.py`)

Build only after Sections 6-8 are stable (Day 5).
- Text-to-SQL: question → LLM generates SQL against `reconciliation_results` + `tax_enrichment` → execute → answer with underlying row(s) shown.
- Not vector RAG — data is structured. State this as a deliberate design choice in the write-up.
**Gate:** ≥90% correct answers on a prepared 20-30 question test set.

---

## 10. Blockchain Audit Trail (`audit/merkle.py`, `audit/chain_client.py`, `audit/contract/AuditLog.sol`)

Scope in after Sections 6-9 are solid (Day 6, time-permitting).
1. SHA-256 hash of every finalized `ReconciliationResult`.
2. Batch a run's hashes into a Merkle tree.
3. Minimal Solidity contract on Base Sepolia / Polygon Amoy anchoring just the root:
```solidity
event RunAnchored(bytes32 merkleRoot, uint256 timestamp, string runId);
function anchorRun(bytes32 merkleRoot, string calldata runId) external {
    emit RunAnchored(merkleRoot, block.timestamp, runId);
}
```
4. Verification view: pick a record, recompute hash, verify via Merkle proof against the on-chain root.
**Gate:** any record's proof independently verifies against the on-chain root — if it doesn't, this stage is not demo-ready and should be cut, not shown half-working.

This is a trust layer, not required for core correctness — state that trade-off explicitly.

---

## 11. Validation & Metrics

All metrics computed against `ground_truth.csv` — never eyeball the output.

**Primary metrics, computed once on the untouched 35-record holdout set on Day 6:**
- Overall match rate
- Match precision and recall (a wrong match is worse than a missed one)
- **Per-exception-category precision/recall** — the primary demo asset
- Precision on hard-negative records specifically (should approach 100% — this is your strongest complexity/rigor signal)
- Tax-tag accuracy + per-mismatch-type detection rate
- Q&A accuracy on the test set
- LLM cost per resolved exception, cache hit rate

**Process rule:** tune only against the design set. Run once, unmodified, against holdout on Day 6. Report those numbers as final.

**Explicitly out-of-scope in the write-up:**
- Many-to-many splits across multiple days
- Chargebacks/disputes arriving weeks later
- Incremental re-matching against a partially-reconciled prior state
- Linking a refund to an original split-settlement group
- Reconciling multi-month EMI installments

---

## 12. Build Workflow (Model & Tool Assignment)

- **Sol:** architecture decisions, dataset case-list design, matching-logic design.
- **Claude:** independent review of Sol's plans before implementation starts on each stage.
- **Terra/Codex:** bulk implementation.
- **Superpowers (TDD):** write the test against ground truth before the implementation, per stage.
- **Ponytail:** run after each stage's tests pass — see Section 15 for exactly what to check.
- **Self-review:** diff output against `ground_truth.csv` for a targeted sample per stage, never eyeball.

---

## 13. Day-by-Day Schedule

| Day | Deliverable |
|---|---|
| 1 | Skeleton deployed end-to-end on Render/Vercel/Supabase. 120-record generator built and gate-checked (≥4 design-set instances per case type). |
| 2 | Stages 0-2, TDD-tested, gates passed, deployed. |
| 3 | Stages 3-4 tested and gated. Go/no-go on stage 5. |
| 4 | Stages 5-7 + tax enrichment, tested and gated. LLM observability wired in. |
| 5 | Q&A layer + metrics dashboard, gated against the question test set. |
| 6 | One unmodified run against the 35-record holdout — final metrics locked. Blockchain layer if time allows. |
| 7 | Demo video (leads with per-category + hard-negative metrics), write-up with stated limitations, rehearsal. |

---

## 14. AGENTS.md — Mandatory, Maintain From Day 1

Create this at the repo root before any implementation starts. Superpowers and Ponytail both read it — without it, they apply generic defaults instead of this project's actual discipline. Update it whenever a convention changes; treat a stale AGENTS.md as a bug.

Must state:
- **Test framework:** pytest, one test file per pipeline module, tests written before implementation (TDD).
- **Folder structure:** as in Section 3 — new modules go in their matching function's folder, not a shared "utils" dump.
- **Rules-first-then-LLM philosophy:** stages 0-5 are deterministic and must stay that way; stage 6 (LLM) only ever sees what deterministic stages couldn't resolve — never used to re-decide an already-resolved record.
- **Per-stage gates:** the exact numeric gates from Section 6 — an agent implementing a stage should be able to read AGENTS.md and know what "done" means without re-deriving it.
- **Schema discipline:** typed Pydantic models everywhere, no bare dicts passed between modules.
- **Determinism requirement:** temperature 0 for all LLM calls, all decisions cached by record-pair.

---

## 15. Using Superpowers and Ponytail Against This Plan

A stage is complete only when **both** of these pass — not just the numeric gate alone.

| Step | Superpowers (before/during implementation) | Ponytail (after tests pass) |
|---|---|---|
| Each pipeline stage (0-7) | Write failing test(s) encoding that stage's exact gate from Section 6, using ground truth. Red → green → refactor. | Check the implementation didn't reach for unnecessary abstraction to pass the test — matching logic should stay readable enough that a judge glancing at the code understands the decision. |
| Tax enrichment | Test against `26as_reference.csv` mismatches before writing the enrichment logic. | Confirm tax logic didn't quietly encode real GST law beyond the deliberately simplified synthetic schema — scope creep here wastes time. |
| LLM stage + observability | Test the cache/idempotency behavior and the confidence-floor routing explicitly — these are the easiest correctness bugs to miss. | Check that cost/latency tracking isn't bolted on as an afterthought hack — it should be a clean wrapper around the LLM client, not scattered print statements. |
| Q&A layer | Test against the 20-30 question set before wiring the UI to it. | Check the generated SQL is actually constrained (no arbitrary write queries) — a judge poking at this live is a real risk otherwise. |
| Blockchain layer | Test hash computation and Merkle proof verification locally before touching testnet. | Confirm this stayed a thin add-on and didn't creep into being load-bearing for core correctness. |

Run Ponytail as a gate, not a suggestion — a stage doesn't merge until both checks pass.

---

## 16. Definition of Done

- All 8 pipeline stages (0-7) pass their TDD tests and Ponytail review on the design set.
- Every case type has its required minimum instance count in both design and holdout sets.
- Final metrics computed once, on the untouched holdout set, and are the numbers reported.
- Hard-negative precision is reported explicitly, not folded into the general precision number.
- Every unresolved record has exactly one exception category.
- LLM cost, cache hit rate, and call volume are visible in the dashboard.
- Q&A layer passes its test set.
- System is live at a public URL (Render + Vercel), not localhost.
- Write-up states scoped-out limitations and the RAG-vs-SQL, LLM-only-on-remainder, and blockchain-optional design decisions explicitly.

---

## 17. Final Check Against the Buildathon Problem Statement

Track 4 (AI Finance Controller) asks for a system that closes one finance-ops loop and reports match rate plus honest, uncategorized-free exceptions on a meaningful batch — not a cherry-picked demo. This plan satisfies that directly: 120 records (not 50) makes the metrics statistically real instead of anecdotal; the 8-stage rules-first pipeline with hard-negative testing demonstrates precision rigor beyond a single-pass matcher; tax-line matching and Q&A extend the same reconciled table rather than becoming separate half-built features; and the stated out-of-scope items are the same honesty the track explicitly rewards, applied to the system's own boundaries.

**Confirmed good to proceed.**
