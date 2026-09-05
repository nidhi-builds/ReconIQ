# Stage 6 Gemini Evaluation Log

Ground truth is used only after inference for scoring. Raw local call logs are
stored as ignored SQLite files under `observability/evaluations/`; they contain
synthetic candidates and model responses, never the API key or ground truth.

## Prompt v1 - 2026-09-05

- **Prompt behavior:** Asked Gemini to compare each triplet, use narration, and
  abstain when uncertain. It did not explain gross versus net accounting or why
  candidates were intentionally outside the deterministic tolerance.
- **Prompt instruction:** "Decide whether these ledger, gateway settlement, and
  bank records are the same transaction. Use the bank narration as semantic
  evidence, but abstain when evidence is insufficient or ambiguous. When
  matched, return exactly the three supplied IDs; otherwise return null IDs.
  Never invent an identifier."
- **Result:** FAIL. Accepted 0/5 true matches; false matches 0; recall 0%.
  Precision is undefined with no accepted predictions and was reported as 0 by
  the test convention.
- **Usage:** 7 calls; 2,621 input tokens; 878 output tokens; 2,561.79 ms average
  latency; paid usage $0.0029813, approximately INR 0.2817 before tax.
- **Observed cause:** Gemini treated every 1.37 amount difference as a hard
  mismatch and sometimes compared ledger gross directly with bank net. Even the
  explicit `ADJ +1.37` narration was rejected.
- **Raw log:** `observability/evaluations/stage6_prompt_v1.sqlite3`.

## Prompt v2 - 2026-09-05

- **Prompt change:** Explained that ledger amount is gross, settlement net is
  post-fee, and the 0.50-2.00 bank difference is the reason for semantic review.
  Defined partial references, one-character corruption, adjustments, and
  reference mismatches without exposing expected answers.
- **Prompt instruction:** "Ledger amount is gross and settlement net is
  post-fee, so do not compare ledger gross directly with bank net. The candidate
  is in the 0.50-2.00 semantic-review band. Use narration to recognize exact,
  partial, abbreviated, or one-character-corrupted references; reject a
  different reference and abstain when evidence is insufficient. `PARTIAL REF`
  means a truncated identifier, not a partial refund."
- **Result:** PASS. Precision 100%; recall 100%; 5/5 true matches accepted; both
  competing decoys rejected; false matches 0; abstentions 0.
- **Usage:** 7 calls; 3,566 input tokens; 1,146 output tokens; 2,692.08 ms average
  latency; paid usage $0.0039348, approximately INR 0.3718 before tax.
- **Trade-off:** Compared with v1, input tokens increased by 945, output tokens
  by 268, and average latency by about 130 ms. The added domain context changed
  behavior from blanket rejection to correct reference-based discrimination.
- **Raw log:** `observability/evaluations/stage6_prompt_v2.sqlite3`.

### Candidate Outcomes

| Ledger case | Bank case | Expected | Decision | Confidence | Basis |
|---|---|---:|---:|---:|---|
| design-028 | design-028 | Match | Match | 0.90 | Partial reference |
| design-028 | design-054 | Reject | Reject | 1.00 | Different reference |
| design-054 | design-028 | Reject | Reject | 1.00 | Different reference |
| design-054 | design-054 | Match | Match | 0.90 | One-character typo |
| design-056 | design-056 | Match | Match | 1.00 | Abbreviated exact reference |
| design-060 | design-060 | Match | Match | 1.00 | Explicit adjustment |
| design-067 | design-067 | Match | Match | 0.90 | Partial reference |

## Current Conclusion

Prompt v2 passes the design-set gate and correctly uses narration to separate
the two intentionally competing candidates. This is design-set evidence only;
the holdout remains unscored until the planned final evaluation.

Across both live runs, 6,187 input tokens and 2,024 output tokens cost
$0.0069161, approximately INR 0.6535 before tax at INR 94.4914 per USD. With
18% GST, if applicable to the billing account, the estimate is INR 0.7711.

## Prompt v2 - Revised Hard-Negative Gate - 2026-09-05

- **Fixture change:** Four ambiguous cases form two reciprocal competitor
  pairs; the fifth remains solo. Hard negatives are split into three filter
  guards and two semantic decoys that genuinely reach Gemini.
- **Result:** PASS. Precision 100%; recall 100%; 5/5 true matches accepted;
  four ambiguity competitors and both semantic decoys rejected; false matches
  0; filter-guard exclusion 100% (n=3); semantic-decoy rejection 100% (n=2).
- **Usage:** 11 calls; 5,599 input tokens; 1,406 output tokens; 2,570.71 ms
  average latency; paid usage $0.0051947, approximately INR 0.4909 before tax.
- **Cumulative paid estimate:** $0.0121108, approximately INR 1.1444 before tax
  or INR 1.3504 with 18% GST if applicable.
- **Holdout:** Structurally generated but still unscored and never sent to
  Gemini.

## Prompt v2 - Post-Review Verification - 2026-09-05

- **Code change:** Indistinguishable evidence is isolated before top-two
  pruning while distinct candidates remain eligible; invalid model IDs are
  logged but never cached; returned decisions now carry measured latency.
- **Result:** PASS. Precision 100%; recall 100%; 5/5 true matches accepted;
  0 false matches; filter-guard exclusion 100% (n=3); semantic-decoy rejection
  100% (n=2).
- **Usage:** 11 calls; 5,599 input tokens; 1,409 output tokens; 3,079.55 ms
  average latency; paid usage $0.0052022, approximately INR 0.4916 before tax;
  $0.00104044 per resolved match.
- **Telemetry:** Cache-hit rate 0%; all 11 responses were in the 0.8-1.0
  confidence band.
- **Raw log:** `observability/evaluations/stage6_v2_1788616305616684500.sqlite3`.
- **Cumulative paid estimate:** $0.017313, approximately INR 1.6359 before tax
  or INR 1.9304 with 18% GST if applicable.
- **Holdout:** Still unscored and never sent to Gemini.

## Stage 9 Q&A - Schema-Only Prompt - 2026-09-05

- **Result:** FAIL at 4.17% strict row accuracy. The model often produced
  semantically correct aggregates with different aliases, but also guessed
  title-cased values instead of stored lowercase/uppercase enums.
- **Usage:** 4,197 input tokens; 2,033 output tokens; 1,184.24 ms average
  latency; $0.0063416 estimated paid cost.
- **Decision:** Keep schema names but explicitly disclose every stored enum and
  score semantic row values rather than SQL aliases.

## Stage 9 Q&A - Grounded Prompt - 2026-09-05

- **Prompt change:** Added exact reconciliation methods, exception reasons,
  payment methods, tax statuses, mismatch reasons, boolean representation, and
  amount-column meanings. Added deterministic write-intent rejection before
  any model call.
- **Result:** PASS at 95.83% strict accuracy with 100% unsafe-request rejection.
  The only reported miss returned the correct `card` answer plus a useful
  supporting gross amount; the scorer was corrected to allow extra evidence.
- **Usage:** 9,177 input tokens; 2,056 output tokens; 1,177.34 ms average SQL
  latency; $0.0078931 estimated paid cost.
- **End-to-end smoke:** Three questions used Gemini for both phases and returned
  correct grounded answers. Six calls cost $0.0016211. One cold SQL generation
  took 15.97 s; the other calls took 1.5-2.0 s.
- **Additional diagnostic:** One client-lifetime verification call cost about
  $0.0001549. Stage 9 Gemini spend so far is approximately $0.0160107.
- **NVIDIA probes:** DeepSeek V4 Pro timed out twice at 30 seconds and once at
  90 seconds, so it was rejected as demo-unsafe. Nemotron 3.5 Lightning
  responded in 1.67-2.83 seconds; it failed an underspecified `SELECT 1` probe
  but generated the correct query from the real ReconIQ schema prompt.
- **Next gate:** Nemotron remains disabled until it completes the same
  24-question, 90%-accuracy gate. The run was blocked by the Codex external-call
  allowance, not by the NVIDIA key or endpoint.
