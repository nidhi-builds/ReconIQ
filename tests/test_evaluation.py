from datetime import date

from data.schemas import BankEntry, GroundTruthEntry, ReconciliationResult
from matching.evaluation import evaluate_run
from matching.pipeline import PipelineMetrics, PipelineRunResult, SourceCounts
from matching.scope_filter import ScopeExclusion
from tax.gst_tds_enrichment import TaxFinding


def truth(
    case_id: str,
    *,
    matched: bool = False,
    exception: str | None = None,
    case_type: str = "MISSING_REF_ID",
    tax: str | None = None,
) -> GroundTruthEntry:
    return GroundTruthEntry(
        case_id=case_id,
        split="design",
        case_type=case_type,
        ledger_ids=[f"order-{case_id}"],
        settlement_ids=[f"settlement-{case_id}"],
        bank_txn_ids=[f"bank-{case_id}"],
        is_transaction=True,
        true_match_group=case_id if matched else None,
        true_exception_reason=exception,
        true_tax_mismatch=tax,
    )


def result(case_id: str, *, matched: bool, exception: str | None = None):
    return ReconciliationResult(
        record_ids=[
            f"order-{case_id}",
            f"settlement-{case_id}",
            f"bank-{case_id}",
        ],
        matched=matched,
        confidence=1,
        method="exact_ref" if matched else "exception_rules",
        exception_reason=exception,
        reasoning=None,
    )


def test_evaluator_reports_independent_quality_metrics() -> None:
    truths = [
        truth("positive-1", matched=True),
        truth("positive-2", matched=True, tax="SHORT_DEDUCTION"),
        truth(
            "hard-negative",
            exception="AMOUNT_MISMATCH_UNEXPLAINED",
            case_type="HARD_NEGATIVE",
        ),
        truth("missing", exception="MISSING_REF_ID"),
        truth("timing", exception="TIMING_LAG_EXCEEDED"),
    ]
    run = PipelineRunResult(
        results=[
            result("positive-1", matched=True),
            result("positive-2", matched=True),
            result("hard-negative", matched=True),
            result("missing", matched=False, exception="MISSING_REF_ID"),
            result("timing", matched=False, exception="MISSING_REF_ID"),
        ],
        duplicates=[],
        exclusions=[],
        llm_decisions=[],
        tax_findings=[
            TaxFinding(
                record_ids=result("positive-1", matched=True).record_ids,
                settlement_id="settlement-positive-1",
                ref_id="ref-1",
                gst_category="PAYMENT_GATEWAY_SERVICE",
                tds_section="194H",
                expected_tds=1,
                actual_tds=1,
                status="CLEAR",
                mismatch_reason=None,
                reasoning="Clear.",
            ),
            TaxFinding(
                record_ids=result("positive-2", matched=True).record_ids,
                settlement_id="settlement-positive-2",
                ref_id="ref-2",
                gst_category="PAYMENT_GATEWAY_SERVICE",
                tds_section="194H",
                expected_tds=2,
                actual_tds=2,
                status="CLEAR",
                mismatch_reason=None,
                reasoning="Incorrectly clear.",
            ),
        ],
        metrics=PipelineMetrics(
            source_counts=SourceCounts(ledger=5, settlements=5, bank=5, tax_26as=2),
            matched_groups=3,
            exception_groups=2,
            match_rate=0.6,
            stage_counts={"exact_ref": 3, "exception_rules": 2},
        ),
    )

    metrics = evaluate_run(run, truths)

    assert metrics.match_precision == 2 / 3
    assert metrics.match_recall == 1
    assert metrics.match_rate == 2 / 5
    assert metrics.hard_negative_precision == 0
    assert metrics.tax_accuracy == 0.5
    assert metrics.exception_metrics["MISSING_REF_ID"].precision == 0.5
    assert metrics.exception_metrics["MISSING_REF_ID"].recall == 1
    assert metrics.exception_metrics["TIMING_LAG_EXCEEDED"].precision == 0
    assert metrics.exception_metrics["TIMING_LAG_EXCEEDED"].recall == 0


def test_evaluator_includes_scope_exclusions() -> None:
    bank = BankEntry(
        bank_txn_id="bank-non-transaction",
        ref_id=None,
        amount=10,
        value_date=date(2026, 1, 1),
        narration="INTERNAL TRANSFER",
    )
    expected = GroundTruthEntry(
        case_id="non-transaction",
        split="design",
        case_type="NON_TRANSACTION_BANK_LINE",
        ledger_ids=[],
        settlement_ids=[],
        bank_txn_ids=[bank.bank_txn_id],
        is_transaction=False,
        true_match_group=None,
        true_exception_reason="NON_TRANSACTION_EXCLUDED",
    )
    run = PipelineRunResult(
        results=[],
        duplicates=[],
        exclusions=[
            ScopeExclusion(
                source_index=0,
                entry=bank,
                matched_phrase="INTERNAL TRANSFER",
            )
        ],
        llm_decisions=[],
        tax_findings=[],
        metrics=PipelineMetrics(
            source_counts=SourceCounts(ledger=0, settlements=0, bank=1, tax_26as=0),
            matched_groups=0,
            exception_groups=0,
            match_rate=0,
            stage_counts={},
        ),
    )

    metrics = evaluate_run(run, [expected])

    assert metrics.exception_metrics["NON_TRANSACTION_EXCLUDED"].precision == 1
    assert metrics.exception_metrics["NON_TRANSACTION_EXCLUDED"].recall == 1
