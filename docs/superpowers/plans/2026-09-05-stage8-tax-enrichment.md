# Stage 8 Tax Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task.

**Goal:** Add deterministic tax findings for matched settlement records.

**Architecture:** A single tax module owns typed output, unique-reference
linkage, GST mapping, and the fixed classification tree. Existing generator
26AS rows are reused; ground truth is scoring-only.

**Tech Stack:** Python 3.13, Pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-stage8-tax-enrichment-design.md`

## Global Constraints

- Use only accepted matches; defensively filter `matched=False`.
- Join only by unique, nonblank `ref_id` on both sides.
- Use two-decimal comparisons and exact case-sensitive challan equality.
- Do not add dependencies or `tax/synthetic_26as.py`.
- Do not commit without explicit user approval.

### Task 1: Core Tax Findings

**Files:**
- Create: `tax/gst_tds_enrichment.py`
- Create: `tests/test_tax_enrichment.py`

**Interfaces:**
- Consumes: `list[ReconciliationResult]`, `list[SettlementEntry]`,
  `list[Tax26ASEntry]`
- Produces: `enrich_tax_lines(...) -> list[TaxFinding]`

- [ ] Write failing tests for clear rows, five GST mappings, three mismatch
  labels, priority, and matched-only filtering.
- [ ] Run `python -m pytest tests/test_tax_enrichment.py -q` and confirm RED.
- [ ] Implement the Pydantic finding model, membership-based settlement
  extraction, mapping, and fixed decision tree.
- [ ] Run the focused suite and confirm GREEN.

### Task 2: Conservative Linkage

**Files:**
- Modify: `tax/gst_tds_enrichment.py`
- Modify: `tests/test_tax_enrichment.py`

- [ ] Write failing tests for blank, duplicate, and unmatched references,
  over-deduction, exact challan comparison, split matches, stable ordering, and
  non-mutation.
- [ ] Run the focused suite and confirm RED.
- [ ] Implement `UNVERIFIABLE` outcomes without fallback matching.
- [ ] Run the focused suite and confirm GREEN.

### Task 3: Design Gate and Traceability

**Files:**
- Modify: `tests/test_tax_enrichment.py`
- Modify: `docs/superpowers/specs/2026-09-05-fastapi-design.md`
- Modify: `IMPLEMENTATION_LOG.md`

- [ ] Write the design-set scoring test using ground truth only after
  enrichment; assert at least 90% accuracy and report per-category metrics.
- [ ] Run the focused test, full offline suite, and `git diff --check`.
- [ ] Align the FastAPI `TaxFinding` contract and record status, metrics,
  decisions, edge cases, and Claude checkpoint in the implementation log.
- [ ] Run Ponytail review; request explicit approval before any commit.
