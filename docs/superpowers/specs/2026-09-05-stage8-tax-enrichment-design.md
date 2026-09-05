# Stage 8 Tax Enrichment Design

## Scope

Stage 8 deterministically enriches accepted reconciliation matches with
synthetic GST categories and compares their settlement TDS fields against the
supplied 26AS reference. It never uses an LLM, ground truth, amount-based
joining, row position, or synthetic `case_id` for linkage.

## Contract

`enrich_tax_lines(matches, settlements, tax_26as) -> list[TaxFinding]`
defensively ignores results where `matched=False`. Settlement IDs are found by
membership in the supplied settlement collection, never by position inside
`record_ids`. Each matched settlement produces one finding; split matches can
therefore produce several, while matches without a settlement produce none.

`TaxFinding` contains `record_ids`, `settlement_id`, `ref_id`,
`gst_category`, actual `tds_section`, `expected_tds`, `actual_tds`,
`status`, `mismatch_reason`, and `reasoning`.

## Linking

A settlement reference must be nonblank and unique among the settlements being
enriched. The same reference must occur exactly once across all supplied 26AS
rows. Missing, duplicate, or unmatched references produce `UNVERIFIABLE`;
there is no fallback join.

## Classification

GST categories are synthetic labels derived only from payment method:

- `upi`: `PAYMENT_GATEWAY_SERVICE`
- `card`: `CARD_PROCESSING_SERVICE`
- `wallet`: `DIGITAL_WALLET_SERVICE`
- `netbanking`: `BANKING_PAYMENT_SERVICE`
- `emi`: `EMI_PROCESSING_SERVICE`

After a unique link, classification is fixed-order, first-match-wins:
`MISSING_CHALLAN`, `WRONG_SECTION`, `SHORT_DEDUCTION`, then `CLEAR`.
TDS amounts are rounded to two decimals before comparison.

Challan values use exact, case-sensitive string equality with no trimming.
A missing settlement challan when 26AS has one is `MISSING_CHALLAN`. Conflicting
nonblank challans, a missing 26AS challan, and over-deduction are
`UNVERIFIABLE`, never `CLEAR`.
Challan-validity checks precede section and amount checks because an unreliable
26AS challan makes those comparisons unverifiable.

`UNVERIFIABLE` deliberately covers both broken linkage and
detected-but-unmodeled discrepancies. The reasoning field states which occurred.

## Gate

Tests cover clear findings, all three modeled mismatches, EMI, priority,
over-deduction, challan exactness, missing/duplicate/unmatched references,
matched-only filtering, split matches, settlement membership lookup, stable
ordering, and non-mutation. Design-set tag accuracy must be at least 90%, with
per-category precision and recall and unverifiable counts reported separately.
