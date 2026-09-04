from datetime import date

import pytest

from data.generator import generate_dataset
from data.schemas import (
    BankEntry,
    GroundTruthEntry,
    LedgerEntry,
    ReconciliationResult,
)
from matching.dedup import deduplicate_ledger
from matching.exact_match import match_exact_references
from matching.fee_adjusted_match import match_fee_adjusted
from matching.refund_match import (
    ConfirmedReference,
    match_refund_reversals,
)
from matching.scope_filter import filter_non_transactions
from matching.split_settlement import match_split_settlements


def ledger(
    ref_id: str | None,
    amount: float = -1_000.0,
    order_id: str = "refund-order-1",
) -> LedgerEntry:
    return LedgerEntry(
        order_id=order_id,
        ref_id=ref_id,
        amount=amount,
        currency="INR",
        payment_method="upi",
        order_date=date(2026, 1, 3),
        customer_id="customer-1",
    )


def bank(
    ref_id: str | None,
    amount: float = -1_000.0,
    bank_txn_id: str = "refund-bank-1",
) -> BankEntry:
    return BankEntry(
        bank_txn_id=bank_txn_id,
        ref_id=ref_id,
        amount=amount,
        value_date=date(2026, 1, 3),
        narration="REFUND original-ref",
    )


def exact_match(label: str = "original") -> ReconciliationResult:
    return ReconciliationResult(
        record_ids=[f"order-{label}", f"settlement-{label}", f"bank-{label}"],
        matched=True,
        confidence=1.0,
        method="exact_ref",
        exception_reason=None,
        reasoning=None,
    )


def confirmed(
    ref_id: str = "original-ref",
    original_amount: float = 1_000.0,
    label: str = "original",
) -> ConfirmedReference:
    return ConfirmedReference(
        ref_id=ref_id,
        original_amount=original_amount,
        match=exact_match(label),
    )


def truth_group_covered(
    truth: GroundTruthEntry,
    all_matches: list[ReconciliationResult],
) -> bool:
    claimed_ids = {
        record_id for match in all_matches for record_id in match.record_ids
    }
    truth_ids = {
        *truth.ledger_ids,
        *truth.settlement_ids,
        *truth.bank_txn_ids,
    }
    return truth_ids.issubset(claimed_ids)


def test_refund_match_links_valid_refund_to_confirmed_original() -> None:
    refund_ref = "original-ref-refund"

    result = match_refund_reversals(
        [ledger(refund_ref)],
        [bank(refund_ref)],
        [confirmed()],
    )

    assert len(result.matches) == 1
    assert result.matches[0].record_ids == [
        "refund-order-1",
        "refund-bank-1",
    ]
    assert result.matches[0].matched is True
    assert result.matches[0].confidence == 0.95
    assert result.matches[0].method == "refund_reversal"
    assert result.matches[0].reasoning == "Refund of original-ref"
    assert result.remaining_ledger_entries == []
    assert result.remaining_bank_entries == []


def test_refund_match_leaves_unlinked_refund_unresolved() -> None:
    refund_ledger = ledger("missing-ref-refund")
    refund_bank = bank("missing-ref-refund")

    result = match_refund_reversals(
        [refund_ledger], [refund_bank], [confirmed()]
    )

    assert result.matches == []
    assert result.remaining_ledger_entries == [refund_ledger]
    assert result.remaining_bank_entries == [refund_bank]


@pytest.mark.parametrize(
    ("ledger_amount", "bank_amount"),
    [(-900.0, -1_000.0), (-1_000.0, -900.0), (-900.0, -900.0)],
)
def test_refund_match_rejects_amount_drift_from_confirmed_original(
    ledger_amount: float,
    bank_amount: float,
) -> None:
    refund_ref = "original-ref-refund"

    result = match_refund_reversals(
        [ledger(refund_ref, ledger_amount)],
        [bank(refund_ref, bank_amount)],
        [confirmed(original_amount=1_000.0)],
    )

    assert result.matches == []


def test_refund_match_uses_reference_before_coincidental_amount() -> None:
    refund_ref = "original-a-refund"
    confirmations = [
        confirmed("original-a", 1_000.0, "a"),
        confirmed("original-b", 1_000.0, "b"),
    ]

    result = match_refund_reversals(
        [ledger(refund_ref)], [bank(refund_ref)], confirmations
    )

    assert len(result.matches) == 1
    assert result.matches[0].reasoning == "Refund of original-a"


def test_refund_match_rejects_duplicate_confirmed_reference() -> None:
    refund_ref = "original-ref-refund"
    confirmations = [confirmed(label="one"), confirmed(label="two")]

    result = match_refund_reversals(
        [ledger(refund_ref)], [bank(refund_ref)], confirmations
    )

    assert result.matches == []


def test_refund_match_rejects_multiple_refund_candidates_for_one_original() -> None:
    refund_ref = "original-ref-refund"
    ledgers = [
        ledger(refund_ref, order_id="refund-order-1"),
        ledger(refund_ref, order_id="refund-order-2"),
    ]
    banks = [
        bank(refund_ref, bank_txn_id="refund-bank-1"),
        bank(refund_ref, bank_txn_id="refund-bank-2"),
    ]

    result = match_refund_reversals(ledgers, banks, [confirmed()])

    assert result.matches == []
    assert result.remaining_ledger_entries == ledgers
    assert result.remaining_bank_entries == banks


def test_refund_match_does_not_mutate_original_match() -> None:
    confirmation = confirmed()
    before = confirmation.match.model_dump_json()

    match_refund_reversals(
        [ledger("original-ref-refund")],
        [bank("original-ref-refund")],
        [confirmation],
    )

    assert confirmation.match.model_dump_json() == before


@pytest.mark.parametrize("ref_id", [None, "", "   ", "adjustment-1"])
def test_refund_match_rejects_negative_rows_without_refund_reference(
    ref_id: str | None,
) -> None:
    refund_ledger = ledger(ref_id)
    refund_bank = bank(ref_id)

    result = match_refund_reversals(
        [refund_ledger], [refund_bank], [confirmed()]
    )

    assert result.matches == []


def test_refund_match_preserves_match_and_remaining_order() -> None:
    ledgers = [
        ledger("original-b-refund", order_id="refund-order-b"),
        ledger("unlinked-refund", order_id="refund-order-unlinked"),
        ledger("original-a-refund", order_id="refund-order-a"),
    ]
    banks = [
        bank("original-a-refund", bank_txn_id="refund-bank-a"),
        bank("unlinked-refund", bank_txn_id="refund-bank-unlinked"),
        bank("original-b-refund", bank_txn_id="refund-bank-b"),
    ]
    confirmations = [
        confirmed("original-a", label="a"),
        confirmed("original-b", label="b"),
    ]

    result = match_refund_reversals(ledgers, banks, confirmations)

    assert [match.record_ids for match in result.matches] == [
        ["refund-order-b", "refund-bank-b"],
        ["refund-order-a", "refund-bank-a"],
    ]
    assert result.remaining_ledger_entries == [ledgers[1]]
    assert result.remaining_bank_entries == [banks[1]]


def test_refund_match_handles_empty_inputs() -> None:
    result = match_refund_reversals([], [], [])

    assert result.matches == []
    assert result.remaining_ledger_entries == []
    assert result.remaining_bank_entries == []


def test_refund_match_covers_every_design_refund_truth_group() -> None:
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
    all_matches = [
        *exact.matches,
        *fee_adjusted.matches,
        *split.matches,
        *refund.matches,
    ]
    refund_truth = [
        truth
        for truth in design.ground_truth
        if truth.case_type == "REFUND_REVERSAL"
    ]
    covered = sum(
        truth_group_covered(truth, all_matches) for truth in refund_truth
    )

    assert covered == len(refund_truth)
    assert len(refund.matches) == len(refund_truth)
