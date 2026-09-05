from pydantic import BaseModel

from data.schemas import GroundTruthEntry
from matching.pipeline import PipelineRunResult


class PrecisionRecall(BaseModel):
    precision: float
    recall: float
    support: int


class EvaluationMetrics(BaseModel):
    match_precision: float
    match_recall: float
    match_rate: float
    hard_negative_precision: float
    tax_accuracy: float
    exception_metrics: dict[str, PrecisionRecall]


def evaluate_run(
    run: PipelineRunResult,
    ground_truth: list[GroundTruthEntry],
) -> EvaluationMetrics:
    truth_by_record = {
        record_id: truth
        for truth in ground_truth
        for record_id in {
            *truth.ledger_ids,
            *truth.settlement_ids,
            *truth.bank_txn_ids,
        }
    }
    matched_results = [row for row in run.results if row.matched]
    correctly_matched = [
        row
        for row in matched_results
        if _single_truth(row.record_ids, truth_by_record) is not None
        and _single_truth(row.record_ids, truth_by_record).true_match_group is not None
    ]
    matched_ids = {
        record_id for row in matched_results for record_id in row.record_ids
    }
    positive_truth = [row for row in ground_truth if row.true_match_group is not None]
    covered_positive = [
        truth
        for truth in positive_truth
        if _truth_ids(truth) <= matched_ids
    ]
    transaction_truth = [row for row in ground_truth if row.is_transaction is not False]
    hard_negatives = [row for row in ground_truth if row.case_type == "HARD_NEGATIVE"]

    predicted_exceptions: list[tuple[str, str]] = []
    for row in run.results:
        if row.matched or row.exception_reason is None:
            continue
        truth = _single_truth(row.record_ids, truth_by_record)
        if truth is not None:
            predicted_exceptions.append((truth.case_id, row.exception_reason))
    predicted_exceptions.extend(
        (truth_by_record[row.entry.bank_txn_id].case_id, row.reason)
        for row in run.exclusions
        if row.entry.bank_txn_id in truth_by_record
    )
    expected_exceptions = {
        row.case_id: row.true_exception_reason
        for row in ground_truth
        if row.true_exception_reason is not None
    }
    categories = {
        *expected_exceptions.values(),
        *[category for _, category in predicted_exceptions],
    }
    exception_metrics = {}
    for category in sorted(categories):
        true_positives = sum(
            expected_exceptions.get(case_id) == predicted == category
            for case_id, predicted in predicted_exceptions
        )
        predictions = sum(predicted == category for _, predicted in predicted_exceptions)
        expected = sum(value == category for value in expected_exceptions.values())
        exception_metrics[category] = PrecisionRecall(
            precision=true_positives / predictions if predictions else 0,
            recall=true_positives / expected if expected else 0,
            support=expected,
        )

    tax_pairs = [
        (
            finding.mismatch_reason,
            truth_by_record[finding.settlement_id].true_tax_mismatch,
        )
        for finding in run.tax_findings
        if finding.settlement_id in truth_by_record
    ]
    return EvaluationMetrics(
        match_precision=(
            len(correctly_matched) / len(matched_results) if matched_results else 0
        ),
        match_recall=(
            len(covered_positive) / len(positive_truth) if positive_truth else 0
        ),
        match_rate=(
            len(covered_positive) / len(transaction_truth) if transaction_truth else 0
        ),
        hard_negative_precision=(
            sum(not (_truth_ids(row) & matched_ids) for row in hard_negatives)
            / len(hard_negatives)
            if hard_negatives
            else 1
        ),
        tax_accuracy=(
            sum(actual == expected for actual, expected in tax_pairs) / len(tax_pairs)
            if tax_pairs
            else 1
        ),
        exception_metrics=exception_metrics,
    )


def _truth_ids(truth: GroundTruthEntry) -> set[str]:
    return {*truth.ledger_ids, *truth.settlement_ids, *truth.bank_txn_ids}


def _single_truth(
    record_ids: list[str],
    truth_by_record: dict[str, GroundTruthEntry],
) -> GroundTruthEntry | None:
    truths = [truth_by_record.get(record_id) for record_id in record_ids]
    if not truths or any(row is None for row in truths):
        return None
    first = truths[0]
    return first if all(row.case_id == first.case_id for row in truths) else None
