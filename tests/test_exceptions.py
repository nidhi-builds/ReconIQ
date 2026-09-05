from datetime import date
import re

import pytest

from data.generator import generate_dataset
from data.schemas import BankEntry, LedgerEntry, SettlementEntry
from matching.dedup import deduplicate_ledger
from matching.exact_match import match_exact_references
from matching.exceptions import (
    ExceptionEvidence,
    build_exception_evidence,
    categorize_exceptions,
)
from matching.fee_adjusted_match import match_fee_adjusted
from matching.llm_match import (
    GeminiMatchDecision,
    match_llm_remainder,
)
from matching.refund_match import ConfirmedReference, match_refund_reversals
from matching.scope_filter import filter_non_transactions
from matching.split_settlement import match_split_settlements
from observability.llm_tracker import LLMTracker


def evidence(label: str = "1", **updates: object) -> ExceptionEvidence:
    values = {
        "record_ids": [f"order-{label}", f"settlement-{label}", f"bank-{label}"],
        "ledger_ref_id": f"ref-{label}",
        "settlement_ref_id": f"ref-{label}",
        "currency": "INR",
        "days_since_expected": 0,
        "method_window_days": 2,
        "amount_difference": 25.0,
        "refund_candidate": False,
        "split_candidate": False,
    }
    values.update(updates)
    return ExceptionEvidence.model_validate(values)


def test_blank_bank_reference_with_valid_source_identity_is_amount_mismatch() -> None:
    source_ledger = LedgerEntry(
        order_id="order-1",
        ref_id="ref-1",
        amount=1_000,
        currency="INR",
        payment_method="upi",
        order_date=date(2026, 1, 1),
        customer_id="customer-1",
    )
    source_settlement = SettlementEntry(
        settlement_id="settlement-1",
        ref_id="ref-1",
        gross_amount=1_000,
        fee=16.95,
        gst_on_fee=3.05,
        net_amount=980,
        payment_method="upi",
        settlement_date=date(2026, 1, 2),
        tds_deducted=10,
        tds_section="194H",
        challan_number="challan-1",
    )
    blank_ref_bank = BankEntry(
        bank_txn_id="bank-1",
        ref_id=None,
        amount=990,
        value_date=date(2026, 1, 2),
        narration="RAZORPAY SETTLEMENT",
    )

    result = categorize_exceptions(
        build_exception_evidence(
            [source_ledger], [source_settlement], [blank_ref_bank]
        )
    )

    assert len(result) == 1
    assert result[0].record_ids == ["order-1", "settlement-1", "bank-1"]
    assert result[0].exception_reason == "AMOUNT_MISMATCH_UNEXPLAINED"


def test_unlinked_refund_ledger_and_bank_are_one_refund_exception() -> None:
    refund_ledger = LedgerEntry(
        order_id="refund-order",
        ref_id="ref-1-refund",
        amount=-1_000,
        currency="INR",
        payment_method="upi",
        order_date=date(2026, 1, 5),
        customer_id="customer-1",
    )
    refund_bank = BankEntry(
        bank_txn_id="refund-bank",
        ref_id="ref-1-refund",
        amount=-900,
        value_date=date(2026, 1, 6),
        narration="REFUND ref-1-refund",
    )

    evidence_rows = build_exception_evidence([refund_ledger], [], [refund_bank])
    results = categorize_exceptions(evidence_rows)

    assert len(results) == 1
    assert results[0].record_ids == ["refund-order", "refund-bank"]
    assert results[0].exception_reason == "REFUND_UNLINKED"


def test_ambiguous_split_candidates_are_one_split_exception() -> None:
    ledgers = [
        LedgerEntry(
            order_id=f"order-{label}",
            ref_id=f"ref-{label}",
            amount=amount,
            currency="INR",
            payment_method="upi",
            order_date=date(2026, 1, 1),
            customer_id=f"customer-{label}",
        )
        for label, amount in [("a", 500), ("b", 700)]
    ]
    settlements = [
        SettlementEntry(
            settlement_id=f"settlement-{label}",
            ref_id=f"ref-{label}",
            gross_amount=gross,
            fee=16.95,
            gst_on_fee=3.05,
            net_amount=net,
            payment_method="upi",
            settlement_date=date(2026, 1, 2),
            tds_deducted=10,
            tds_section="194H",
            challan_number=f"challan-{label}",
        )
        for label, gross, net in [("a", 500, 480), ("b", 700, 680)]
    ]
    banks = [
        BankEntry(
            bank_txn_id=f"bank-{label}",
            ref_id=None,
            amount=1_160,
            value_date=date(2026, 1, 2),
            narration="RAZORPAY SETTLEMENT",
        )
        for label in ["a", "b"]
    ]
    split = match_split_settlements(ledgers, settlements, banks)
    assert split.matches == []

    results = categorize_exceptions(
        build_exception_evidence(
            split.remaining_ledger_entries,
            split.remaining_settlement_entries,
            split.remaining_bank_entries,
        )
    )

    assert len(results) == 1
    assert results[0].record_ids == [
        "order-a",
        "order-b",
        "settlement-a",
        "settlement-b",
        "bank-a",
        "bank-b",
    ]
    assert results[0].exception_reason == "SPLIT_SETTLEMENT_UNRESOLVED"


def test_evidence_builder_does_not_attach_bank_with_different_reference() -> None:
    source_ledger = LedgerEntry(
        order_id="order-1",
        ref_id="ref-1",
        amount=1_000,
        currency="INR",
        payment_method="upi",
        order_date=date(2026, 1, 1),
        customer_id="customer-1",
    )
    source_settlement = SettlementEntry(
        settlement_id="settlement-1",
        ref_id="ref-1",
        gross_amount=1_000,
        fee=16.95,
        gst_on_fee=3.05,
        net_amount=980,
        payment_method="upi",
        settlement_date=date(2026, 1, 2),
        tds_deducted=10,
        tds_section="194H",
        challan_number="challan-1",
    )
    unrelated_bank = BankEntry(
        bank_txn_id="bank-other",
        ref_id="ref-other",
        amount=980,
        value_date=date(2026, 1, 2),
        narration="UNRELATED TRANSFER",
    )

    rows = build_exception_evidence(
        [source_ledger], [source_settlement], [unrelated_bank]
    )

    assert not any(
        {"settlement-1", "bank-other"}.issubset(row.record_ids) for row in rows
    )


def test_bank_only_orphan_is_amount_mismatch_not_missing_reference() -> None:
    orphan = BankEntry(
        bank_txn_id="bank-hard-negative",
        ref_id=None,
        amount=981.75,
        value_date=date(2026, 1, 2),
        narration="RZP STLMNT unrelated",
    )

    results = categorize_exceptions(build_exception_evidence([], [], [orphan]))

    assert len(results) == 1
    assert results[0].record_ids == ["bank-hard-negative"]
    assert results[0].exception_reason == "AMOUNT_MISMATCH_UNEXPLAINED"


@pytest.mark.parametrize("missing_field", ["ledger_ref_id", "settlement_ref_id"])
def test_missing_source_identity_is_missing_reference(missing_field: str) -> None:
    result = categorize_exceptions([evidence(**{missing_field: "   "})])

    assert result[0].exception_reason == "MISSING_REF_ID"


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"currency": "USD"}, "CURRENCY_MISMATCH"),
        (
            {"days_since_expected": 3, "amount_difference": 0.0},
            "TIMING_LAG_EXCEEDED",
        ),
        ({"refund_candidate": True}, "REFUND_UNLINKED"),
        ({"split_candidate": True}, "SPLIT_SETTLEMENT_UNRESOLVED"),
    ],
)
def test_exception_rules_assign_the_expected_category(updates, expected) -> None:
    result = categorize_exceptions([evidence(**updates)])

    assert result[0].exception_reason == expected
    assert result[0].reasoning


def test_every_record_is_classified_once_in_stable_order() -> None:
    inputs = [evidence("1"), evidence("2", currency="USD")]

    results = categorize_exceptions(inputs)

    assert [result.record_ids for result in results] == [
        inputs[0].record_ids,
        inputs[1].record_ids,
    ]
    assert all(result.matched is False for result in results)
    assert all(result.method == "exception_rules" for result in results)


def test_overlapping_evidence_is_rejected_instead_of_double_tagged() -> None:
    with pytest.raises(ValueError, match="more than one exception context"):
        categorize_exceptions(
            [evidence("1"), evidence("2", record_ids=["bank-1"])]
        )


def test_design_remainder_gets_complete_and_correct_exception_labels(tmp_path) -> None:
    design = generate_dataset(seed=42).design
    ledgers = deduplicate_ledger(design.ledger).retained_entries
    banks = filter_non_transactions(design.bank).remaining_entries
    exact = match_exact_references(ledgers, design.settlements, banks)
    fee_adjusted = match_fee_adjusted(
        exact.remaining_ledger_entries,
        exact.remaining_settlement_entries,
        exact.remaining_bank_entries,
    )
    split = match_split_settlements(
        fee_adjusted.remaining_ledger_entries,
        fee_adjusted.remaining_settlement_entries,
        fee_adjusted.remaining_bank_entries,
    )
    ledger_by_id = {row.order_id: row for row in ledgers}
    confirmations = [
        ConfirmedReference(
            ref_id=ledger_by_id[match.record_ids[0]].ref_id,
            original_amount=ledger_by_id[match.record_ids[0]].amount,
            match=match,
        )
        for match in exact.matches
        if ledger_by_id[match.record_ids[0]].ref_id is not None
    ]
    refund = match_refund_reversals(
        split.remaining_ledger_entries,
        split.remaining_bank_entries,
        confirmations,
    )
    def decide_from_candidate_evidence(candidate):
        target = candidate.ledger_ref_id.lower().removeprefix("ref-")
        hints = [
            hint.lower().removeprefix("ref-")
            for hint in re.findall(
                r"(?:ref-)?design-[a-z0-9]+", candidate.bank_narration, re.I
            )
        ]
        matched = "unrelated" not in candidate.bank_narration.lower() and any(
            len(hint) == len(target)
            and sum(left != right for left, right in zip(hint, target, strict=True))
            <= 1
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

    llm = match_llm_remainder(
        refund.remaining_ledger_entries,
        split.remaining_settlement_entries,
        refund.remaining_bank_entries,
        tracker=LLMTracker(tmp_path / "stage7-llm.sqlite3"),
        decide=decide_from_candidate_evidence,
    )

    evidence_rows = build_exception_evidence(
        llm.remaining_ledger_entries,
        llm.remaining_settlement_entries,
        llm.remaining_bank_entries,
    )
    results = categorize_exceptions(evidence_rows)
    truth_by_record = {
        record_id: truth
        for truth in design.ground_truth
        for record_id in [
            *truth.ledger_ids,
            *truth.settlement_ids,
            *truth.bank_txn_ids,
        ]
    }
    remaining_ids = {
        *[row.order_id for row in llm.remaining_ledger_entries],
        *[row.settlement_id for row in llm.remaining_settlement_entries],
        *[row.bank_txn_id for row in llm.remaining_bank_entries],
    }

    assert {record_id for row in results for record_id in row.record_ids} == remaining_ids
    mismatches = [
        (
            result.record_ids,
            result.exception_reason,
            [truth_by_record[record_id].case_id for record_id in result.record_ids],
            truth_by_record[result.record_ids[0]].true_exception_reason,
        )
        for result in results
        if result.exception_reason
        != truth_by_record[result.record_ids[0]].true_exception_reason
        or any(
            truth_by_record[record_id].case_id
            != truth_by_record[result.record_ids[0]].case_id
            for record_id in result.record_ids
        )
    ]
    assert mismatches == []
