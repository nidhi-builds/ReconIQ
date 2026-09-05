import re

from data.generator import generate_dataset
from matching.evaluation import evaluate_run
from matching.llm_match import GeminiMatchDecision
from matching.pipeline import PipelineInput, run_reconciliation
from observability.llm_tracker import LLMTracker


def decide_from_narration(candidate):
    target = candidate.ledger_ref_id.lower().removeprefix("ref-")
    hints = [
        hint.lower().removeprefix("ref-")
        for hint in re.findall(
            r"(?:ref-)?design-[a-z0-9]+", candidate.bank_narration, re.I
        )
    ]
    matched = "unrelated" not in candidate.bank_narration.lower() and any(
        len(hint) == len(target)
        and sum(left != right for left, right in zip(hint, target, strict=True)) <= 1
        for hint in hints
    )
    return (
        GeminiMatchDecision(
            matched=matched,
            matched_ids=candidate.record_ids if matched else None,
            confidence=0.9 if matched else 0.1,
            reason_category=None if matched else "UNRELATED_REFERENCE",
            reasoning="Decision based only on submitted narration evidence.",
        ),
        0,
        0,
    )


def test_pipeline_runs_every_stage_and_conserves_design_records(tmp_path) -> None:
    design = generate_dataset(seed=42).design
    pipeline_input = PipelineInput(
        ledger=design.ledger,
        settlements=design.settlements,
        bank=design.bank,
        tax_26as=design.tax_26as,
    )

    run = run_reconciliation(
        pipeline_input,
        tracker=LLMTracker(tmp_path / "pipeline.sqlite3"),
        decide=decide_from_narration,
    )

    assert "ground_truth" not in PipelineInput.model_fields
    assert len(run.duplicates) == 6
    assert len(run.exclusions) == 6
    assert sum(result.method == "refund_reversal" for result in run.results) == 6

    final_ids = [record_id for result in run.results for record_id in result.record_ids]
    expected_ids = {
        *{row.order_id for row in design.ledger},
        *{row.settlement_id for row in design.settlements},
        *{
            row.bank_txn_id
            for row in design.bank
            if row.bank_txn_id
            not in {exclusion.entry.bank_txn_id for exclusion in run.exclusions}
        },
    }
    assert set(final_ids) == expected_ids
    assert len(final_ids) == len(set(final_ids))

    method_order = {
        "exact_ref": 0,
        "fee_adjusted_window": 1,
        "split_settlement": 2,
        "refund_reversal": 3,
        "llm_remainder": 4,
        "exception_rules": 5,
    }
    assert [method_order[row.method] for row in run.results] == sorted(
        method_order[row.method] for row in run.results
    )
    assert run.metrics.matched_groups == sum(row.matched for row in run.results)
    assert run.metrics.exception_groups == sum(not row.matched for row in run.results)
    assert run.metrics.match_rate == run.metrics.matched_groups / len(run.results)
    assert run.tax_findings

    evaluation = evaluate_run(run, design.ground_truth)
    assert evaluation.match_precision == 1
    assert evaluation.match_recall == 1
    assert evaluation.hard_negative_precision == 1
    assert evaluation.tax_accuracy == 1
    assert all(
        metric.precision == metric.recall == 1
        for metric in evaluation.exception_metrics.values()
    )


def test_pipeline_does_not_mutate_inputs(tmp_path) -> None:
    design = generate_dataset(seed=42).design
    pipeline_input = PipelineInput(
        ledger=design.ledger,
        settlements=design.settlements,
        bank=design.bank,
        tax_26as=design.tax_26as,
    )
    before = pipeline_input.model_dump_json()

    run_reconciliation(
        pipeline_input,
        tracker=LLMTracker(tmp_path / "pipeline.sqlite3"),
        decide=decide_from_narration,
    )

    assert pipeline_input.model_dump_json() == before
